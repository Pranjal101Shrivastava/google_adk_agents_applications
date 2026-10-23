"""Deterministic diagnostic tools.

Two jobs that models do badly and code does well:

* **Parsing a stack trace.** A model asked to "find the root cause frame" will
  often pick a plausible-looking library frame. A parser that knows which
  frames are third-party picks the right one every time.
* **Assigning a priority.** "This feels like a P1" is not a defensible triage.
  A rubric over observed signals is.

Both are pure functions with no I/O, so they are fully unit-tested and their
output is identical on every run.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from google.adk.tools import ToolContext

from ..constants import StateKeys

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Stack trace parsing
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Frame:
    """One parsed stack frame."""

    file: str
    line: int | None
    function: str | None
    language: str
    is_third_party: bool


# Python:  File "/app/svc/handler.py", line 42, in process
_PY_FRAME = re.compile(
    r'File "(?P<file>[^"]+)", line (?P<line>\d+)(?:, in (?P<func>[\w.<>]+))?'
)
# Java/Kotlin:  at com.example.Service.process(Service.java:42)
_JVM_FRAME = re.compile(
    r"at\s+(?P<func>[\w$.]+)\((?P<file>[\w$.]+\.(?:java|kt))(?::(?P<line>\d+))?\)"
)
# JS/TS:  at process (/app/src/handler.js:42:15)  |  at /app/src/handler.ts:42:15
_JS_FRAME = re.compile(
    r"at\s+(?:(?P<func>[\w$.<>\[\] ]+?)\s+\()?"
    r"(?P<file>[^\s()]+\.(?:js|jsx|ts|tsx|mjs|cjs))"
    r":(?P<line>\d+):\d+\)?"
)
# Go:  /app/svc/handler.go:42 +0x1f
_GO_FRAME = re.compile(r"(?P<file>[^\s:]+\.go):(?P<line>\d+)")

# Path fragments that mean "not our code". Any match is enough.
_THIRD_PARTY_PATH_MARKERS = (
    "site-packages",
    "dist-packages",
    "node_modules",
    "/usr/lib/python",
    "/usr/local/lib/python",
    "<frozen ",
    "/go/pkg/mod/",
    "/gradle/caches/",
    "/.m2/repository/",
    "/vendor/",
)

# JVM traces carry a bare class filename ("Service.java") with no path, so a
# path check cannot see that a frame is library code. The package in the
# fully-qualified method name is what distinguishes them.
_THIRD_PARTY_PACKAGE_PREFIXES = (
    "java.",
    "javax.",
    "jakarta.",
    "jdk.",
    "sun.",
    "com.sun.",
    "kotlin.",
    "kotlinx.",
    "scala.",
    "org.springframework.",
    "org.apache.",
    "org.eclipse.",
    "org.hibernate.",
    "org.junit.",
    "org.mockito.",
    "org.slf4j.",
    "org.jetbrains.",
    "io.netty.",
    "io.micrometer.",
    "com.fasterxml.",
    "com.google.common.",
    "ch.qos.logback.",
    "reactor.core.",
)

# Exception-line shapes:  "ValueError: bad input", "java.io.IOException: boom"
_EXCEPTION_LINE = re.compile(
    r"^(?P<type>(?:[\w.]+\.)?[A-Z]\w*(?:Error|Exception|Exit|Fault|Warning|Panic|Failure))"
    r"(?::\s*(?P<message>.*))?$"
)
# Go panics do not follow the Error/Exception naming convention.
_GO_PANIC = re.compile(r"^panic:\s*(?P<message>.*)$")


def _is_third_party(path: str, function: str | None = None) -> bool:
    """Decide whether a frame is library code rather than ours.

    Two signals, because no single one covers every language: the file path
    (Python, JS, Go) and the package prefix of the qualified method name (JVM,
    where the frame carries only a bare class filename).
    """
    lowered = path.lower()
    if any(marker.lower() in lowered for marker in _THIRD_PARTY_PATH_MARKERS):
        return True
    if function:
        qualified = function.lower()
        return any(
            qualified.startswith(prefix) for prefix in _THIRD_PARTY_PACKAGE_PREFIXES
        )
    return False


def _parse_frames(text: str) -> list[Frame]:
    """Extract frames from a trace, preserving source order.

    Each line is matched against the language patterns in turn and the *first*
    match wins, so a Python path that happens to contain ``.go`` is not
    misread as a Go frame.
    """
    frames: list[Frame] = []
    patterns = (
        (_PY_FRAME, "python"),
        (_JVM_FRAME, "jvm"),
        (_JS_FRAME, "javascript"),
        (_GO_FRAME, "go"),
    )

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        for pattern, language in patterns:
            match = pattern.search(line)
            if not match:
                continue
            groups = match.groupdict()
            path = groups["file"]
            line_number = groups.get("line")
            frames.append(
                Frame(
                    file=path,
                    line=int(line_number) if line_number else None,
                    function=groups.get("func"),
                    language=language,
                    is_third_party=_is_third_party(path, groups.get("func")),
                )
            )
            break

    return frames


def _innermost_first(frames: list[Frame]) -> bool:
    """Whether this trace lists the innermost frame first.

    Python tracebacks read outermost-first and put the throwing frame last
    ("most recent call last"). JVM, JavaScript and Go stack traces are the
    other way round. Getting this backwards reports the HTTP entry point as
    the root cause of every bug, which looks plausible and is always wrong.
    """
    return not any(frame.language == "python" for frame in frames)


def _parse_exception(text: str) -> tuple[str | None, str | None]:
    """Find the exception type and message.

    Scans from the bottom, because in Python and the JVM the final line of a
    trace is the exception that actually propagated. Chained traces ("During
    handling of the above exception...") put the *original* error higher up,
    and the one that reached the caller is the one worth reporting.
    """
    for raw_line in reversed(text.splitlines()):
        line = raw_line.strip()
        if not line:
            continue
        if panic := _GO_PANIC.match(line):
            return "panic", panic.group("message").strip() or None
        if match := _EXCEPTION_LINE.match(line):
            message = (match.group("message") or "").strip()
            return match.group("type"), message or None
    return None, None


async def parse_stack_trace(
    stack_trace: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Parse a stack trace into its exception, its frames and the likely culprit.

    Use this whenever a user pastes a traceback, **before** searching for
    similar tickets: the exception type and the first first-party frame make a
    far better search query than the raw trace, which is mostly noise.

    Supports Python, Java/Kotlin, JavaScript/TypeScript and Go.

    Args:
        stack_trace: The raw trace text, exactly as the user pasted it.
        tool_context: ADK tool context (supplied by the runtime).

    Returns:
        A dict with the ``exception_type``, ``exception_message``, the parsed
        ``frames``, the ``root_cause_frame`` (the deepest frame in
        first-party code), and a ``suggested_search_query``.
    """
    text = (stack_trace or "").strip()
    if not text:
        return {"status": "error", "message": "No stack trace text was provided."}

    frames = _parse_frames(text)
    exception_type, exception_message = _parse_exception(text)

    if not frames and not exception_type:
        return {
            "status": "error",
            "message": (
                "No stack frames or exception line could be parsed. This may not be "
                "a stack trace, or it may be in a language this tool does not cover "
                "(it handles Python, Java/Kotlin, JavaScript/TypeScript and Go). "
                "Treat the text as a plain bug description instead."
            ),
        }

    # The culprit is the *innermost* first-party frame: the last line of our own
    # code before control passed into a library. Which end of the list that is
    # depends on the language - see _innermost_first.
    languages = sorted({f.language for f in frames})
    innermost_first = _innermost_first(frames)

    ordered = frames if innermost_first else list(reversed(frames))
    first_party = [f for f in ordered if not f.is_third_party]
    # `ordered` runs innermost -> outermost, so the first entry is the culprit.
    root_cause = first_party[0] if first_party else (ordered[0] if ordered else None)

    query_parts: list[str] = []
    if exception_type:
        query_parts.append(exception_type)
    if exception_message:
        # Strip the volatile bits - IDs, hex addresses, paths - that would stop
        # an otherwise-matching ticket or Stack Overflow post from being found.
        cleaned = re.sub(r"0x[0-9a-fA-F]+", "", exception_message)
        cleaned = re.sub(r"\b\d{3,}\b", "", cleaned)
        cleaned = re.sub(r"(/[\w.-]+){2,}", "", cleaned)
        cleaned = " ".join(cleaned.split())[:120]
        if cleaned:
            query_parts.append(cleaned)
    if root_cause and root_cause.function:
        query_parts.append(root_cause.function)

    result = {
        "status": "success",
        "exception_type": exception_type,
        "exception_message": exception_message,
        "languages": languages,
        "frame_count": len(frames),
        "first_party_frame_count": len(first_party),
        "frames": [
            {
                "file": f.file,
                "line": f.line,
                "function": f.function,
                "language": f.language,
                "is_third_party": f.is_third_party,
            }
            for f in frames
        ],
        "root_cause_frame": (
            {
                "file": root_cause.file,
                "line": root_cause.line,
                "function": root_cause.function,
                "is_third_party": root_cause.is_third_party,
            }
            if root_cause
            else None
        ),
        "suggested_search_query": " ".join(query_parts) or None,
    }

    tool_context.state[StateKeys.LAST_STACK_TRACE] = result
    logger.info(
        "Parsed %d frame(s), %d first-party, exception=%s",
        len(frames),
        len(first_party),
        exception_type,
    )
    return result


# --------------------------------------------------------------------------
# Priority triage
# --------------------------------------------------------------------------
# The rubric. Each signal contributes points; the total maps to a priority.
# Making this a table rather than a prompt is the whole point: a reviewer can
# argue with the weights, and the same inputs always give the same output.
TRIAGE_SIGNALS: dict[str, tuple[int, str]] = {
    "data_loss": (50, "Data is lost or corrupted"),
    "security_impact": (50, "Security or privacy exposure"),
    "production_outage": (50, "Production is down or unusable"),
    "no_workaround": (20, "No workaround exists"),
    "affects_all_users": (20, "All users are affected"),
    "affects_many_users": (10, "A large subset of users is affected"),
    "revenue_impact": (15, "Revenue or billing is affected"),
    "regression": (10, "This worked in a previous release"),
    "blocks_release": (10, "Blocks an imminent release"),
    "reproducible": (5, "Reliably reproducible"),
}

# Score thresholds, highest first.
_PRIORITY_THRESHOLDS: tuple[tuple[int, str], ...] = (
    (70, "P0 - Critical"),
    (40, "P1 - High"),
    (15, "P2 - Medium"),
    (0, "P3 - Low"),
)


async def triage_priority(
    signals: list[str],
    tool_context: ToolContext,
    summary: str = "",
) -> dict[str, Any]:
    """Assign a ticket priority from observed impact signals, using a fixed rubric.

    Call this instead of judging a priority yourself. Pass only the signals you
    have actual evidence for from the user's report — guessing at signals
    produces a confidently wrong priority, which is worse than no priority.

    Valid signals: ``data_loss``, ``security_impact``, ``production_outage``,
    ``no_workaround``, ``affects_all_users``, ``affects_many_users``,
    ``revenue_impact``, ``regression``, ``blocks_release``, ``reproducible``.

    Args:
        signals: The signal names evidenced by the report.
        tool_context: ADK tool context (supplied by the runtime).
        summary: Optional one-line summary of the issue, echoed back.

    Returns:
        The computed ``priority``, the ``score``, which signals were
        ``matched``, which were ``unrecognised``, and a ``rationale``.
    """
    provided = [str(s).strip().lower().replace(" ", "_").replace("-", "_") for s in (signals or [])]
    matched = [s for s in dict.fromkeys(provided) if s in TRIAGE_SIGNALS]
    unrecognised = [s for s in dict.fromkeys(provided) if s not in TRIAGE_SIGNALS]

    score = sum(TRIAGE_SIGNALS[s][0] for s in matched)

    priority = "P3 - Low"
    for threshold, candidate in _PRIORITY_THRESHOLDS:
        if score >= threshold:
            priority = candidate
            break

    if matched:
        reasons = "; ".join(TRIAGE_SIGNALS[s][1] for s in matched)
        rationale = f"{priority} at {score} points. Signals: {reasons}."
    else:
        rationale = (
            "P3 - Low by default: no impact signals were evidenced. If the report "
            "does describe impact, re-run with the matching signals."
        )

    if unrecognised:
        rationale += f" Ignored unrecognised signal(s): {', '.join(unrecognised)}."

    result = {
        "status": "success",
        "priority": priority,
        "score": score,
        "matched_signals": matched,
        "unrecognised_signals": unrecognised,
        "rationale": rationale,
        "summary": summary or None,
        "rubric": {name: weight for name, (weight, _) in TRIAGE_SIGNALS.items()},
    }

    tool_context.state[StateKeys.LAST_TRIAGE] = result
    logger.info("Triaged to %s (score %d)", priority, score)
    return result


# --------------------------------------------------------------------------
# Small utility
# --------------------------------------------------------------------------
def get_current_date() -> dict[str, str]:
    """Return today's date in UTC.

    Call this before writing any date into a ticket, or when reasoning about
    how old something is. Do not guess at the current date.

    Returns:
        A dict with ``current_date`` (``YYYY-MM-DD``) and the full
        ``current_datetime_utc`` in ISO-8601.
    """
    now = datetime.now(timezone.utc)
    return {
        "current_date": now.strftime("%Y-%m-%d"),
        "current_datetime_utc": now.isoformat(),
    }
