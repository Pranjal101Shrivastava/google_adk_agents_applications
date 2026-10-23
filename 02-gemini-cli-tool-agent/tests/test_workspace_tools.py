"""Tests for the workspace lifecycle tools.

Cloning is exercised against a real local git repository, so the clone path is
genuinely covered without touching the network.
"""

from __future__ import annotations

import pytest

from gemini_cli_agent import workspace as ws
from gemini_cli_agent.config import config
from gemini_cli_agent.constants import StateKeys
from gemini_cli_agent.tools.workspace_tools import (
    cleanup_workspace,
    list_active_workspaces,
    list_workspace_files,
    prepare_workspace,
    read_workspace_file,
)


# --- prepare_workspace ------------------------------------------------
async def test_prepare_rejects_a_disallowed_host(tool_context, workspace_root):
    result = await prepare_workspace("https://evil.test/a/b", tool_context)
    assert result["status"] == "error"
    assert "ALLOWED_CLONE_HOSTS" in result["message"]


async def test_prepare_rejects_a_blank_url(tool_context, workspace_root):
    result = await prepare_workspace("", tool_context)
    assert result["status"] == "error"


async def test_prepare_clones_a_real_repository(
    tool_context, workspace_root, local_repo, monkeypatch
):
    """Allow a file path for this test only, to exercise the clone path offline."""
    monkeypatch.setattr(ws, "normalise_repo_url", lambda url: url)

    result = await prepare_workspace(str(local_repo), tool_context)

    assert result["status"] == "success", result
    assert result["commit"]
    assert (workspace_root / result["workspace_id"] / "src" / "main.py").is_file()
    # The active workspace is recorded for later turns.
    assert tool_context.state[StateKeys.ACTIVE_WORKSPACE]["workspace_id"] == (
        result["workspace_id"]
    )


async def test_prepare_reports_a_clone_failure_without_a_traceback(
    tool_context, workspace_root, tmp_path, monkeypatch
):
    monkeypatch.setattr(ws, "normalise_repo_url", lambda url: url)
    result = await prepare_workspace(str(tmp_path / "does-not-exist"), tool_context)
    assert result["status"] == "error"
    assert "git clone failed" in result["message"]
    assert "Traceback" not in result["message"]


async def test_prepare_enforces_the_workspace_cap(
    tool_context, workspace_root, monkeypatch
):
    monkeypatch.setattr(config, "max_concurrent_workspaces", 1)
    ws._WORKSPACES["already-here"] = ws.Workspace(
        workspace_id="already-here",
        repo_url="https://github.com/a/b",
        ref=None,
        path=workspace_root / "already-here",
    )

    result = await prepare_workspace("https://github.com/c/d", tool_context)

    assert result["status"] == "error"
    assert "cleanup_workspace" in result["message"]


async def test_prepare_enforces_the_size_cap(
    tool_context, workspace_root, local_repo, monkeypatch
):
    monkeypatch.setattr(ws, "normalise_repo_url", lambda url: url)
    monkeypatch.setattr(config, "max_workspace_mb", 0)  # anything exceeds this

    result = await prepare_workspace(str(local_repo), tool_context)

    assert result["status"] == "error"
    assert "MAX_WORKSPACE_MB" in result["message"]
    # The oversized checkout was not left behind.
    assert not list(workspace_root.iterdir())


# --- list_workspace_files ---------------------------------------------
async def test_listing_returns_repository_entries(fake_workspace):
    result = await list_workspace_files(fake_workspace.workspace_id)
    assert result["status"] == "success"
    assert "README.md" in result["entries"]
    assert "src/" in result["entries"]


async def test_listing_skips_noise_directories(fake_workspace):
    """node_modules in a listing is wasted context, every time."""
    result = await list_workspace_files(fake_workspace.workspace_id)
    assert not any("node_modules" in entry for entry in result["entries"])


async def test_listing_respects_max_depth(fake_workspace):
    shallow = await list_workspace_files(fake_workspace.workspace_id, max_depth=1)
    deep = await list_workspace_files(fake_workspace.workspace_id, max_depth=3)
    assert not any(entry.count("/") > 1 for entry in shallow["entries"])
    assert len(deep["entries"]) > len(shallow["entries"])


async def test_listing_refuses_traversal(fake_workspace):
    result = await list_workspace_files(fake_workspace.workspace_id, subdirectory="../..")
    assert result["status"] == "error"


async def test_listing_an_unknown_workspace_errors():
    result = await list_workspace_files("nope")
    assert result["status"] == "error"
    assert "nope" in result["message"]


# --- read_workspace_file ----------------------------------------------
async def test_reading_a_file_returns_its_content(fake_workspace):
    result = await read_workspace_file(fake_workspace.workspace_id, "src/main.py")
    assert result["status"] == "success"
    assert "print('hi')" in result["content"]


async def test_reading_a_directory_is_an_error(fake_workspace):
    result = await read_workspace_file(fake_workspace.workspace_id, "src")
    assert result["status"] == "error"
    assert "not a file" in result["message"]


async def test_reading_refuses_traversal(fake_workspace):
    result = await read_workspace_file(fake_workspace.workspace_id, "../../etc/passwd")
    assert result["status"] == "error"


async def test_oversized_file_is_refused_with_a_pointer_to_the_cli(
    fake_workspace, monkeypatch
):
    import gemini_cli_agent.tools.workspace_tools as wt

    monkeypatch.setattr(wt, "MAX_READ_BYTES", 5)
    result = await read_workspace_file(fake_workspace.workspace_id, "src/main.py")
    assert result["status"] == "error"
    assert "Gemini CLI" in result["message"]


# --- cleanup / listing ------------------------------------------------
async def test_cleanup_removes_the_workspace_from_disk(tool_context, fake_workspace):
    tool_context.state[StateKeys.ACTIVE_WORKSPACE] = fake_workspace.to_dict()
    path = fake_workspace.path

    result = await cleanup_workspace(fake_workspace.workspace_id, tool_context)

    assert result["status"] == "success"
    assert not path.exists()
    assert tool_context.state[StateKeys.ACTIVE_WORKSPACE] is None


async def test_cleanup_of_an_unknown_workspace_errors(tool_context):
    result = await cleanup_workspace("nope", tool_context)
    assert result["status"] == "error"


async def test_cleanup_leaves_a_different_active_workspace_alone(
    tool_context, fake_workspace, workspace_root
):
    other = ws.Workspace(
        workspace_id="other-1234",
        repo_url="https://github.com/x/y",
        ref=None,
        path=workspace_root / "other-1234",
    )
    ws._WORKSPACES[other.workspace_id] = other
    tool_context.state[StateKeys.ACTIVE_WORKSPACE] = other.to_dict()

    await cleanup_workspace(fake_workspace.workspace_id, tool_context)

    assert tool_context.state[StateKeys.ACTIVE_WORKSPACE]["workspace_id"] == "other-1234"


async def test_active_workspace_listing_reports_the_cap(fake_workspace):
    result = await list_active_workspaces()
    assert result["count"] == 1
    assert result["limit"] == config.max_concurrent_workspaces


# --- registry hygiene -------------------------------------------------
def test_get_workspace_drops_a_vanished_directory(fake_workspace):
    import shutil

    shutil.rmtree(fake_workspace.path)
    with pytest.raises(ws.WorkspaceError, match="no longer exists"):
        ws.get_workspace(fake_workspace.workspace_id)
    # And it is no longer counted against the cap.
    assert fake_workspace.workspace_id not in ws._WORKSPACES


def test_directory_size_ignores_symlinks(fake_workspace, tmp_path):
    big = tmp_path / "big.bin"
    big.write_bytes(b"0" * 2_000_000)
    (fake_workspace.path / "link-to-big").symlink_to(big)
    # The 2 MB file is outside the workspace; following the link would
    # misreport the size and defeat the cap.
    assert ws.directory_size_mb(fake_workspace.path) < 1.0
