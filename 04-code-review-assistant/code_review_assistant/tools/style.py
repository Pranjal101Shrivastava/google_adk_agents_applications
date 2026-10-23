"""PEP 8 style checking via pycodestyle.

Style is the one review dimension with a reference implementation, so there is
no reason to ask a model about it. ``pycodestyle`` is the tool everyone's CI
already runs; using it means the review agrees with the build.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import tempfile
from collections import Counter
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

import pycodestyle
from google.adk.tools import ToolContext

from ..config import config
from ..constants import StateKeys

logger = logging.getLogger(__name__)

# Codes ignored by default. E501 because line length is configurable and
# checked separately; W503/W504 because they contradict each other and black's
# formatting trips one of them whichever you pick.
DEFAULT_IGNORE = ("E501", "W503", "W504")

# pycodestyle's report line: path:row:col: CODE message
_REPORT_LINE = re.compile(r"^(?P<path>.+?):(?P<row>\d+):(?P<col>\d+):\s+(?P<code>\w+)\s+(?P<message>.*)$")

# How much each class of violation costs, per occurrence.
_SEVERITY_COST = {"E9": 10, "E1": 2, "E2": 1, "E3": 1, "E7": 2, "W6": 3}
_DEFAULT_COST = 1


def _code_cost(code: str) -> int:
    """Points deducted for one violation of *code*."""
    return _SEVERITY_COST.get(code[:2], _DEFAULT_COST)


def _run_pycodestyle(code: str, max_line_length: int) -> list[dict[str, Any]]:
    """Run pycodestyle over *code* and return structured violations.

    pycodestyle only reads files and only reports to stdout, so the source goes
    to a temp file and stdout is captured. Runs in a worker thread because both
    of those are blocking.
    """
    with tempfile.TemporaryDirectory(prefix="style-check-") as directory:
        path = Path(directory) / "submission.py"
        path.write_text(code, encoding="utf-8")

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            guide = pycodestyle.StyleGuide(
                quiet=False,
                max_line_length=max_line_length,
                ignore=list(DEFAULT_IGNORE),
            )
            guide.check_files([str(path)])

        violations: list[dict[str, Any]] = []
        for line in buffer.getvalue().splitlines():
            match = _REPORT_LINE.match(line.strip())
            if not match:
                continue
            violations.append(
                {
                    "line": int(match.group("row")),
                    "column": int(match.group("col")),
                    "code": match.group("code"),
                    "message": match.group("message"),
                }
            )
        return violations


def _long_lines(code: str, limit: int) -> list[dict[str, Any]]:
    """Lines over the configured length, reported separately from E501."""
    return [
        {
            "line": number,
            "column": limit + 1,
            "code": "E501",
            "message": f"line too long ({len(text)} > {limit} characters)",
        }
        for number, text in enumerate(code.splitlines(), start=1)
        if len(text) > limit
    ]


async def check_code_style(
    code: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Check PEP 8 compliance with pycodestyle and return a 0-100 style score.

    Pass an empty string to reuse the code stored by
    ``analyze_code_structure``.

    Args:
        code: The Python source to check, or ``""`` to read it from state.
        tool_context: ADK tool context (supplied by the runtime).

    Returns:
        The ``score`` out of 100, the ``violations`` with line numbers, and a
        ``by_code`` count so the feedback can lead with the most frequent
        problem rather than listing 40 individually.
    """
    source = code or tool_context.state.get(StateKeys.CODE_TO_REVIEW, "")
    if not source:
        return {
            "status": "error",
            "message": (
                "No code to check. Run analyze_code_structure first, or pass the "
                "code directly."
            ),
        }

    try:
        violations = await asyncio.to_thread(
            _run_pycodestyle, source, config.max_line_length
        )
        violations += _long_lines(source, config.max_line_length)
    except Exception as exc:  # noqa: BLE001 - a style check must not fail the review
        logger.exception("Style check failed")
        report = {
            "status": "error",
            "message": f"Style check could not run: {exc}",
            "score": None,
        }
        tool_context.state[StateKeys.STYLE_REPORT] = report
        return report

    violations.sort(key=lambda v: (v["line"], v["column"]))
    by_code = Counter(v["code"] for v in violations)

    penalty = sum(_code_cost(v["code"]) for v in violations)
    score = max(0, 100 - penalty)

    report = {
        "status": "success",
        "score": score,
        "violation_count": len(violations),
        "violations": violations[:50],
        "truncated": len(violations) > 50,
        "by_code": dict(by_code.most_common()),
        "most_common": (
            [{"code": code, "count": count} for code, count in by_code.most_common(3)]
        ),
        "max_line_length": config.max_line_length,
        "ignored_codes": list(DEFAULT_IGNORE),
    }

    tool_context.state[StateKeys.STYLE_REPORT] = report

    logger.info("Style: score %d, %d violation(s)", score, len(violations))
    return report
