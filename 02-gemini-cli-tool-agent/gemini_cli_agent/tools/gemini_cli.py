"""The Gemini CLI, wrapped as an ADK function tool.

This is the interesting part of the agent: a full agentic CLI used as a *single
tool call* inside an ADK pipeline. The CLI brings capabilities the in-process
agent does not have — it walks a real filesystem, reads whole directory trees,
builds its own context from the repository and runs its own internal tool loop
— so the ADK agent delegates "understand this codebase" to it rather than
trying to stream a repository through its own context window.

Invocation rules enforced here:

* argv list, never a shell, so prompts and paths cannot become commands;
* a scrubbed child environment, so the CLI inherits credentials and nothing
  else;
* ``--yolo`` is only ever passed when the operator opted into writes;
* wall-clock timeout, and stdout truncated before it re-enters a model prompt.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from typing import Any

from google.adk.tools import ToolContext

from ..config import config
from ..constants import StateKeys
from ..workspace import WorkspaceError, get_workspace, resolve_within

logger = logging.getLogger(__name__)

# Environment variables the CLI legitimately needs. Everything else in the
# parent environment is dropped, so a stray secret cannot leak into a
# subprocess that then talks to a model.
_ENV_PASSTHROUGH = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "GOOGLE_GENAI_USE_VERTEXAI",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "NODE_EXTRA_CA_CERTS",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
)


def gemini_cli_path() -> str | None:
    """Return the resolved path to the Gemini CLI, or ``None`` if absent."""
    binary = config.gemini_cli_binary
    if os.path.isabs(binary):
        return binary if os.access(binary, os.X_OK) else None
    return shutil.which(binary)


def build_child_env() -> dict[str, str]:
    """Build the scrubbed environment for the CLI subprocess."""
    env = {
        key: os.environ[key] for key in _ENV_PASSTHROUGH if key in os.environ
    }
    # The CLI is non-interactive here; make that explicit so it never blocks
    # the agent waiting on a TTY prompt.
    env["CI"] = "true"
    env["TERM"] = "dumb"
    return env


def build_cli_argv(
    prompt: str,
    *,
    model: str | None = None,
    allow_write: bool | None = None,
    extra_directories: list[str] | None = None,
) -> list[str]:
    """Build the argv list for one CLI invocation.

    Kept as a pure function so the exact command line is unit-testable — the
    thing you most want pinned when a tool shells out.

    Args:
        prompt: The task text passed via ``-p``.
        model: Override for the CLI's own model.
        allow_write: Whether to permit file modification. Defaults to the
            configured value.
        extra_directories: Additional directories to add to the CLI's context.

    Returns:
        The argv list, starting with the CLI binary name.
    """
    binary = config.gemini_cli_binary
    argv = [binary, "-m", model or config.gemini_cli_model]

    writes_allowed = (
        config.gemini_cli_allow_write if allow_write is None else allow_write
    )
    if writes_allowed:
        # --yolo auto-approves the CLI's own tool calls, including edits. Only
        # reachable when the operator explicitly enabled writes.
        argv.append("--yolo")

    for directory in extra_directories or []:
        argv += ["--include-directories", directory]

    # `-p` last, and the prompt as its own argv entry: no quoting, no escaping,
    # nothing for a shell to reinterpret, because there is no shell.
    argv += ["-p", prompt]
    return argv


def truncate_output(text: str, limit: int | None = None) -> tuple[str, bool]:
    """Truncate CLI output to a prompt-safe length.

    Returns:
        ``(text, was_truncated)``. When truncating, the head and the tail are
        both kept — the head carries the CLI's summary, the tail its
        conclusion, and the middle is the part you can afford to lose.
    """
    cap = limit or config.gemini_cli_max_output_chars
    if len(text) <= cap:
        return text, False

    head = cap * 2 // 3
    tail = cap - head
    marker = (
        f"\n\n... [{len(text) - cap} characters elided by the agent; "
        f"raise GEMINI_CLI_MAX_OUTPUT_CHARS to see more] ...\n\n"
    )
    return text[:head] + marker + text[-tail:], True


async def run_gemini_cli(
    task: str,
    workspace_id: str,
    tool_context: ToolContext,
    subdirectory: str = "",
    allow_write: bool = False,
) -> dict[str, Any]:
    """Run the Gemini CLI against a prepared workspace.

    Use this for anything that needs to read the repository itself: explaining
    an architecture, tracing a call path, drafting a test plan, finding where a
    behaviour is implemented. The CLI builds its own context from the files on
    disk, so you do not need to read them first.

    Args:
        task: What the CLI should do, as a complete instruction. Be specific
            and name the files or directories to focus on when you know them.
        workspace_id: ID returned by ``prepare_workspace``.
        tool_context: ADK tool context (supplied by the runtime).
        subdirectory: Optional path inside the repository to run in, for
            focusing the CLI on one package. Defaults to the repository root.
        allow_write: Request permission for the CLI to modify files. Ignored
            unless the deployment enabled writes.

    Returns:
        A dict with ``status`` and, on success, the CLI's ``output``, the
        ``duration_seconds`` and whether the output was ``truncated``.
    """
    if not (task or "").strip():
        return {"status": "error", "message": "The `task` argument was empty."}

    binary = gemini_cli_path()
    if binary is None:
        return {
            "status": "error",
            "message": (
                f"The Gemini CLI ({config.gemini_cli_binary!r}) is not on PATH. "
                "Install it with `npm install -g @google/gemini-cli`, or set "
                "GEMINI_CLI_BINARY to its absolute path. Tell the user this is "
                "an environment problem rather than retrying."
            ),
        }

    try:
        workspace = get_workspace(workspace_id)
        cwd = resolve_within(workspace, subdirectory)
    except WorkspaceError as exc:
        return {"status": "error", "message": str(exc)}

    if not cwd.is_dir():
        return {
            "status": "error",
            "message": f"Subdirectory {subdirectory!r} is not a directory in this repo.",
        }

    writes = bool(allow_write) and config.gemini_cli_allow_write
    if allow_write and not config.gemini_cli_allow_write:
        logger.warning(
            "Write access requested but GEMINI_CLI_ALLOW_WRITE is false; "
            "running read-only."
        )

    argv = build_cli_argv(task, allow_write=writes)
    started = time.monotonic()

    try:
        result = subprocess.run(  # noqa: S603 - argv list, shell=False
            argv,
            cwd=str(cwd),
            env=build_child_env(),
            capture_output=True,
            text=True,
            timeout=config.gemini_cli_timeout_seconds,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        duration = time.monotonic() - started
        logger.warning("Gemini CLI timed out after %.0fs", duration)
        return {
            "status": "error",
            "error_type": "timeout",
            "message": (
                f"The CLI exceeded its {config.gemini_cli_timeout_seconds}s budget. "
                "Narrow the task - name a specific directory or file - and try again."
            ),
            "duration_seconds": round(duration, 1),
        }
    except OSError as exc:
        return {
            "status": "error",
            "error_type": "exec",
            "message": f"Could not execute the Gemini CLI: {exc}",
        }

    duration = time.monotonic() - started

    if result.returncode != 0:
        stderr, _ = truncate_output(result.stderr or "", 4_000)
        logger.warning("Gemini CLI exited %d after %.1fs", result.returncode, duration)
        return {
            "status": "error",
            "error_type": "cli_failure",
            "exit_code": result.returncode,
            "message": stderr.strip() or f"The CLI exited with code {result.returncode}.",
            "duration_seconds": round(duration, 1),
        }

    output, truncated = truncate_output(result.stdout or "")
    if not output.strip():
        return {
            "status": "error",
            "error_type": "empty_output",
            "message": (
                "The CLI succeeded but produced no output. The task was probably "
                "too vague; restate it as a concrete question about specific files."
            ),
            "duration_seconds": round(duration, 1),
        }

    # Record the run so the synthesis step can cite which invocations produced
    # which findings.
    history = list(tool_context.state.get(StateKeys.CLI_RUNS) or [])
    history.append(
        {
            "task": task,
            "workspace_id": workspace_id,
            "subdirectory": subdirectory or ".",
            "duration_seconds": round(duration, 1),
            "output_chars": len(result.stdout or ""),
            "truncated": truncated,
            "write_enabled": writes,
        }
    )
    tool_context.state[StateKeys.CLI_RUNS] = history
    tool_context.state[StateKeys.LAST_CLI_OUTPUT] = output

    logger.info(
        "Gemini CLI completed in %.1fs (%d chars%s)",
        duration,
        len(result.stdout or ""),
        ", truncated" if truncated else "",
    )

    return {
        "status": "success",
        "output": output,
        "truncated": truncated,
        "duration_seconds": round(duration, 1),
        "workspace_id": workspace_id,
        "subdirectory": subdirectory or ".",
        "write_enabled": writes,
    }


def check_gemini_cli() -> dict[str, Any]:
    """Report whether the Gemini CLI is installed and which version.

    Call this when a CLI invocation fails for an unclear reason, to tell an
    environment problem apart from a bad task.

    Returns:
        A dict with ``available``, and ``path``/``version`` when it is.
    """
    binary = gemini_cli_path()
    if binary is None:
        return {
            "available": False,
            "configured_binary": config.gemini_cli_binary,
            "message": (
                "Not found on PATH. Install with `npm install -g @google/gemini-cli`."
            ),
        }

    try:
        result = subprocess.run(  # noqa: S603 - argv list, shell=False
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=build_child_env(),
            shell=False,
        )
        version = (result.stdout or result.stderr or "").strip()
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {
            "available": False,
            "path": binary,
            "message": f"Found at {binary} but could not run it: {exc}",
        }

    return {
        "available": True,
        "path": binary,
        "version": version or "unknown",
        "cli_model": config.gemini_cli_model,
        "write_enabled": config.gemini_cli_allow_write,
        "timeout_seconds": config.gemini_cli_timeout_seconds,
    }
