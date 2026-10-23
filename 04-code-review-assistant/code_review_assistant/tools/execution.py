"""Running generated tests against submitted code.

This is the most dangerous tool in the repository: it executes code a model
wrote, against code a stranger submitted. The controls below are **defence in
depth, not a sandbox**:

* a separate process, so a crash or an exit does not take the agent down;
* ``python -E -s``, so ``PYTHONPATH``, ``PYTHONHOME`` and the user site
  directory cannot inject modules into the run;
* ``RLIMIT_CPU``, ``RLIMIT_AS``, ``RLIMIT_NPROC`` and ``RLIMIT_FSIZE``, so a
  fork bomb, a memory bomb or a disk-filling loop hits a wall;
* a wall-clock timeout with process-group kill, so ``while True: pass`` ends;
* a scrubbed environment and a throwaway working directory.

What they do **not** do is contain a determined attacker. There is no network
namespace, no filesystem namespace and no seccomp filter here. For a
deployment that accepts code from people you do not trust, run the whole agent
inside a gVisor sandbox (Cloud Run's second-generation execution environment),
or move this tool behind a dedicated single-use container. The tool result says
so, and ``ENABLE_CODE_EXECUTION=false`` turns it off entirely.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import resource
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from google.adk.tools import ToolContext

from ..config import config
from ..constants import StateKeys

logger = logging.getLogger(__name__)

# Environment variables the child keeps. Everything else is dropped, so no
# credential reachable by the agent is reachable by submitted code.
_ENV_ALLOWLIST = ("PATH", "LANG", "LC_ALL")

# pytest's summary line: "2 failed, 3 passed, 1 skipped in 0.12s"
_SUMMARY_COUNT = re.compile(r"(\d+)\s+(passed|failed|error|errors|skipped|xfailed|xpassed)")


def _apply_limits() -> None:
    """Apply resource limits in the child, just before exec.

    Runs between fork and exec, so it must stay simple and allocation-free.
    """
    # CPU seconds. Catches an infinite loop that the wall-clock timeout would
    # otherwise have to wait out.
    resource.setrlimit(
        resource.RLIMIT_CPU,
        (config.execution_cpu_seconds, config.execution_cpu_seconds + 2),
    )
    # Address space. Catches `[0] * 10**12`.
    memory_bytes = config.execution_memory_mb * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    # Max file size written. Catches a disk-filling loop.
    file_bytes = 32 * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_FSIZE, (file_bytes, file_bytes))
    # Core dumps are not useful here and can be large.
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    try:
        # Process count. Catches a fork bomb. Not available on every platform,
        # and a limit too low would stop pytest itself from running.
        resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
    except (ValueError, OSError):
        pass
    # New process group, so a timeout can kill the whole tree rather than just
    # the interpreter that spawned it.
    os.setsid()


def _child_env() -> dict[str, str]:
    env = {k: os.environ[k] for k in _ENV_ALLOWLIST if k in os.environ}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    # No network credentials, no cloud project, no API keys.
    return env


def _parse_pytest_output(stdout: str) -> dict[str, int]:
    """Extract pass/fail counts from pytest's summary line."""
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    # The last summary line wins; earlier ones may be from a sub-run.
    for line in reversed(stdout.splitlines()):
        if "passed" not in line and "failed" not in line and "error" not in line:
            continue
        found = _SUMMARY_COUNT.findall(line)
        if not found:
            continue
        for number, label in found:
            key = "errors" if label.startswith("error") else label
            if key in counts:
                counts[key] += int(number)
        break
    return counts


def _run_pytest(directory: Path) -> dict[str, Any]:
    """Run pytest in *directory* under limits. Blocking; call via a thread."""
    argv = [
        sys.executable,
        # -E ignores PYTHON* environment variables; -s skips the user site
        # directory. Not -I or -S: both of those also suppress the interpreter's
        # own site-packages, which is where pytest lives, so the run would fail
        # before it collected anything.
        "-E",
        "-s",
        "-m",
        "pytest",
        "-q",
        "--no-header",
        "-p",
        "no:cacheprovider",
        "--tb=short",
        str(directory),
    ]

    try:
        completed = subprocess.run(  # noqa: S603 - argv list, shell=False
            argv,
            cwd=str(directory),
            env=_child_env(),
            capture_output=True,
            text=True,
            timeout=config.execution_timeout_seconds,
            check=False,
            shell=False,
            preexec_fn=_apply_limits,  # noqa: PLW1509 - the point of this function
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "error",
            "error_type": "timeout",
            "message": (
                f"Tests exceeded the {config.execution_timeout_seconds}s wall-clock "
                "limit and were killed. The usual cause is an infinite loop or a "
                "blocking call in the submitted code."
            ),
            "stdout": (exc.stdout or "")[:4000] if isinstance(exc.stdout, str) else "",
        }
    except OSError as exc:
        return {
            "status": "error",
            "error_type": "exec",
            "message": f"Could not start the test process: {exc}",
        }

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    counts = _parse_pytest_output(stdout)

    # A negative return code means the child was killed by a signal, which
    # here means a resource limit fired: SIGXCPU (-24) from RLIMIT_CPU, or
    # SIGKILL (-9) from the OOM path. pytest never exits this way on its own,
    # and treating it as "tests ran" would report a resource bomb as a pass.
    if completed.returncode < 0:
        signal_number = -completed.returncode
        limit = {
            24: f"the {config.execution_cpu_seconds}s CPU limit (RLIMIT_CPU)",
            9: "a hard kill, usually the memory limit (RLIMIT_AS)",
            11: "a segmentation fault in the submitted code",
        }.get(signal_number, f"signal {signal_number}")
        return {
            "status": "error",
            "error_type": "resource_limit",
            "signal": signal_number,
            "message": (
                f"The test process was killed by {limit}. The submitted code "
                "most likely contains an infinite loop or an unbounded "
                "allocation. Report this as a resource problem in the code - it "
                "is not a passing test run."
            ),
            "stdout": stdout[:4000],
        }

    # pytest exit codes: 0 all passed, 1 tests failed, 2 interrupted,
    # 3 internal error, 4 usage error, 5 no tests collected.
    if completed.returncode == 5:
        return {
            "status": "error",
            "error_type": "no_tests",
            "message": (
                "pytest collected no tests. The generated test file is probably "
                "missing `test_`-prefixed functions."
            ),
            "stdout": stdout[:4000],
        }
    if completed.returncode in (2, 3, 4):
        return {
            "status": "error",
            "error_type": "pytest_error",
            "exit_code": completed.returncode,
            "message": (
                "pytest could not run the tests - this is a problem with the test "
                "file itself, not a test failure."
            ),
            "stdout": stdout[:4000],
            "stderr": stderr[:2000],
        }

    return {
        "status": "success",
        "exit_code": completed.returncode,
        "all_passed": completed.returncode == 0 and counts["passed"] > 0,
        **counts,
        "total": counts["passed"] + counts["failed"] + counts["errors"],
        "stdout": stdout[:8000],
        "stderr": stderr[:2000],
        "truncated": len(stdout) > 8000,
    }


async def run_tests(
    test_code: str,
    tool_context: ToolContext,
    code_under_test: str = "",
) -> dict[str, Any]:
    """Run pytest tests against the submitted code in an isolated subprocess.

    Write the tests importing from ``submission``, which is the module name the
    submitted code is written to::

        from submission import my_function

        def test_handles_empty_input():
            assert my_function([]) == 0

    Args:
        test_code: A complete pytest module. Must contain ``test_``-prefixed
            functions, and must import what it needs from ``submission``.
        tool_context: ADK tool context (supplied by the runtime).
        code_under_test: The code to test. Defaults to whatever
            ``analyze_code_structure`` stored, which is normally what you want;
            pass it explicitly only to test a *fixed* version.

    Returns:
        Pass/fail counts, pytest's output, and on failure an ``error_type``
        distinguishing a genuine test failure from a timeout, an unrunnable
        test file, or execution being disabled.
    """
    if not config.enable_code_execution:
        return {
            "status": "error",
            "error_type": "disabled",
            "message": (
                "Code execution is disabled in this deployment "
                "(ENABLE_CODE_EXECUTION=false). Review the code statically and "
                "say plainly that the tests were not run - do not describe "
                "untested code as working."
            ),
        }

    source = code_under_test or tool_context.state.get(StateKeys.CODE_TO_REVIEW, "")
    if not source:
        return {
            "status": "error",
            "message": "No code under test. Run analyze_code_structure first.",
        }
    if not (test_code or "").strip():
        return {"status": "error", "message": "The `test_code` argument was empty."}

    if len(test_code) > config.max_code_chars:
        return {
            "status": "error",
            "message": f"Test code exceeds the {config.max_code_chars}-character limit.",
        }

    with tempfile.TemporaryDirectory(prefix="code-review-run-") as directory:
        path = Path(directory)
        (path / "submission.py").write_text(source, encoding="utf-8")
        (path / "test_submission.py").write_text(test_code, encoding="utf-8")

        result = await asyncio.to_thread(_run_pytest, path)

    result["isolation_note"] = (
        "Ran in a separate process with CPU, memory, process-count and file-size "
        "limits, a scrubbed environment and a throwaway working directory. This "
        "is defence in depth, not a true sandbox - there is no network or "
        "filesystem namespace. Do not treat it as safe for untrusted input "
        "without an outer sandbox such as gVisor."
    )

    tool_context.state[StateKeys.TEST_REPORT] = result
    tool_context.state[StateKeys.GENERATED_TESTS] = test_code

    logger.info(
        "Tests: status=%s passed=%s failed=%s",
        result.get("status"),
        result.get("passed"),
        result.get("failed"),
    )
    return result


async def run_fix_tests(
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Re-run the generated tests against the fixed code.

    Uses the test code from the review phase unchanged — that is what makes
    this a verification rather than a second opinion. Rewriting the tests to
    suit the fix would prove nothing.

    Args:
        tool_context: ADK tool context (supplied by the runtime).

    Returns:
        The same shape as ``run_tests``, stored under the fix-phase state key.
    """
    fixed_code = tool_context.state.get(StateKeys.FIXED_CODE, "")
    test_code = tool_context.state.get(StateKeys.GENERATED_TESTS, "")

    if not fixed_code:
        return {
            "status": "error",
            "message": "No fixed code in state. The fixer must run before validation.",
        }
    if not test_code:
        return {
            "status": "error",
            "message": (
                "No tests from the review phase, so the fix cannot be verified. "
                "Say that the fix is unverified rather than claiming it works."
            ),
        }

    result = await run_tests(test_code, tool_context, code_under_test=fixed_code)
    tool_context.state[StateKeys.FIX_TEST_REPORT] = result
    return result


def execution_environment_report() -> dict[str, Any]:
    """Report how test execution is configured and isolated.

    Call this when a user asks whether their code is actually being run, or
    how it is contained.

    Returns:
        The current limits and an honest statement of the isolation boundary.
    """
    return {
        "enabled": config.enable_code_execution,
        "interpreter": sys.executable,
        "wall_clock_timeout_seconds": config.execution_timeout_seconds,
        "cpu_seconds": config.execution_cpu_seconds,
        "memory_mb": config.execution_memory_mb,
        "flags": ["-E (ignore PYTHON* env vars)", "-s (no user site directory)"],
        "limits_applied": [
            "RLIMIT_CPU",
            "RLIMIT_AS",
            "RLIMIT_FSIZE",
            "RLIMIT_CORE",
            "RLIMIT_NPROC (where supported)",
        ],
        "environment": f"scrubbed to {list(_ENV_ALLOWLIST)}",
        "isolation_boundary": (
            "Process-level only. No network namespace, no filesystem namespace, "
            "no seccomp filter. Suitable for code from users you trust; for "
            "untrusted input, run the agent inside gVisor or a single-use "
            "container."
        ),
    }
