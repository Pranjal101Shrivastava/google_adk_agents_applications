"""Workspace lifecycle, exposed as ADK function tools.

These tools wrap :mod:`gemini_cli_agent.workspace`. They never raise: a tool
that throws gives the model a stack trace, whereas a ``{"status": "error",
"message": ...}`` dict gives it something it can act on or report.
"""

from __future__ import annotations

import logging
from typing import Any

from google.adk.tools import ToolContext

from ..config import config
from ..constants import StateKeys
from ..workspace import (
    WorkspaceError,
    create_workspace,
    destroy_workspace,
    get_workspace,
    list_workspaces,
    resolve_within,
)

logger = logging.getLogger(__name__)

# Guard rails for the file-reading tool. The CLI is the right way to read a lot
# of code; these tools are for peeking at one specific file.
MAX_READ_BYTES = 200_000
MAX_LISTING_ENTRIES = 400

# Directories that are never worth showing in a listing.
_NOISE_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "dist",
        "build",
        ".next",
        "target",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
    }
)


async def prepare_workspace(
    repo_url: str,
    tool_context: ToolContext,
    ref: str = "",
) -> dict[str, Any]:
    """Clone a git repository into a sandboxed workspace for analysis.

    Call this before any other repository tool. The clone is shallow,
    size-capped, and restricted to the deployment's allow-listed git hosts.

    Args:
        repo_url: An ``https://`` or ``git@host:owner/repo`` URL.
        tool_context: ADK tool context (supplied by the runtime).
        ref: Optional branch, tag or commit to check out. Defaults to the
            repository's default branch.

    Returns:
        On success, the workspace metadata including the ``workspace_id`` that
        every later tool call needs. On failure, an actionable error message.
    """
    try:
        workspace = create_workspace(repo_url, ref or None)
    except WorkspaceError as exc:
        logger.info("Rejected workspace request for %r: %s", repo_url, exc)
        return {"status": "error", "message": str(exc)}
    except Exception as exc:  # noqa: BLE001 - never surface a traceback to the model
        logger.exception("Unexpected failure preparing a workspace")
        return {"status": "error", "message": f"Could not prepare the workspace: {exc}"}

    tool_context.state[StateKeys.ACTIVE_WORKSPACE] = workspace.to_dict()

    return {
        "status": "success",
        **workspace.to_dict(),
        "summary": (
            f"Cloned {workspace.repo_url} at "
            f"{(workspace.commit or 'unknown')[:8]} into workspace "
            f"{workspace.workspace_id} ({workspace.size_mb:.1f} MB)."
        ),
    }


async def list_workspace_files(
    workspace_id: str,
    subdirectory: str = "",
    max_depth: int = 2,
) -> dict[str, Any]:
    """List files and directories in a prepared workspace.

    Useful for orienting yourself before writing a CLI task — knowing the
    repository is a monorepo with eight packages changes what you ask the CLI
    to do.

    Args:
        workspace_id: ID returned by ``prepare_workspace``.
        subdirectory: Path inside the repository. Defaults to the root.
        max_depth: How many directory levels to descend (1-5).

    Returns:
        A dict with the ``entries`` list, or an error message.
    """
    try:
        workspace = get_workspace(workspace_id)
        root = resolve_within(workspace, subdirectory)
    except WorkspaceError as exc:
        return {"status": "error", "message": str(exc)}

    if not root.is_dir():
        return {
            "status": "error",
            "message": f"{subdirectory or '.'} is not a directory in this repository.",
        }

    depth = max(1, min(int(max_depth or 1), 5))
    entries: list[str] = []
    truncated = False

    for path in sorted(root.rglob("*")):
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if len(relative.parts) > depth:
            continue
        if any(part in _NOISE_DIRS for part in relative.parts):
            continue
        if len(entries) >= MAX_LISTING_ENTRIES:
            truncated = True
            break
        suffix = "/" if path.is_dir() else ""
        entries.append(f"{relative}{suffix}")

    return {
        "status": "success",
        "workspace_id": workspace_id,
        "subdirectory": subdirectory or ".",
        "entry_count": len(entries),
        "truncated": truncated,
        "entries": entries,
    }


async def read_workspace_file(
    workspace_id: str,
    file_path: str,
) -> dict[str, Any]:
    """Read one text file from a prepared workspace.

    Prefer ``run_gemini_cli`` for understanding a codebase — it reads many
    files and reasons across them. Use this tool when you need the literal
    contents of one specific file, for instance to quote a config value.

    Args:
        workspace_id: ID returned by ``prepare_workspace``.
        file_path: Path to the file, relative to the repository root.

    Returns:
        The file's ``content``, or an error message.
    """
    try:
        workspace = get_workspace(workspace_id)
        target = resolve_within(workspace, file_path)
    except WorkspaceError as exc:
        return {"status": "error", "message": str(exc)}

    if not target.is_file():
        return {"status": "error", "message": f"{file_path!r} is not a file."}

    size = target.stat().st_size
    if size > MAX_READ_BYTES:
        return {
            "status": "error",
            "message": (
                f"{file_path!r} is {size // 1024} KB, over the "
                f"{MAX_READ_BYTES // 1024} KB read limit. Ask the Gemini CLI about "
                "it instead of reading it whole."
            ),
        }

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"status": "error", "message": f"Could not read {file_path!r}: {exc}"}

    return {
        "status": "success",
        "file_path": file_path,
        "size_bytes": size,
        "line_count": content.count("\n") + 1,
        "content": content,
    }


async def cleanup_workspace(
    workspace_id: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Delete a workspace from disk once the analysis is finished.

    Workspaces are capped in number, so clean up when you are done rather than
    waiting to hit the limit.

    Args:
        workspace_id: ID returned by ``prepare_workspace``.
        tool_context: ADK tool context (supplied by the runtime).

    Returns:
        Whether the workspace was removed.
    """
    removed = destroy_workspace(workspace_id)

    active = tool_context.state.get(StateKeys.ACTIVE_WORKSPACE) or {}
    if isinstance(active, dict) and active.get("workspace_id") == workspace_id:
        tool_context.state[StateKeys.ACTIVE_WORKSPACE] = None

    if not removed:
        return {
            "status": "error",
            "message": f"No workspace {workspace_id!r} to clean up.",
        }
    return {
        "status": "success",
        "workspace_id": workspace_id,
        "summary": f"Removed workspace {workspace_id}.",
    }


async def list_active_workspaces() -> dict[str, Any]:
    """List every workspace this agent currently holds on disk.

    Returns:
        The workspaces and the configured cap.
    """
    workspaces = [w.to_dict() for w in list_workspaces()]
    return {
        "status": "success",
        "count": len(workspaces),
        "limit": config.max_concurrent_workspaces,
        "workspaces": workspaces,
    }
