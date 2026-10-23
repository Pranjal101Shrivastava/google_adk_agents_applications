"""Offline fixtures for the Gemini CLI tool agent tests.

No network, no git remotes, no Gemini CLI binary required. Where a real git
repository is needed, one is created locally with ``git init``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest


class FakeToolContext:
    """Minimal stand-in for ``google.adk.tools.ToolContext``."""

    def __init__(self, state: dict[str, Any] | None = None) -> None:
        self.state: dict[str, Any] = dict(state or {})


@pytest.fixture
def tool_context() -> FakeToolContext:
    return FakeToolContext()


@pytest.fixture
def workspace_root(tmp_path, monkeypatch) -> Path:
    """Point the config's workspace root at a per-test temp directory."""
    from gemini_cli_agent.config import config

    root = tmp_path / "workspaces"
    root.mkdir()
    monkeypatch.setattr(config, "workspace_root", root)
    return root


@pytest.fixture(autouse=True)
def clean_registry():
    """Keep the process-local workspace registry from leaking between tests."""
    from gemini_cli_agent import workspace as ws

    ws._WORKSPACES.clear()
    yield
    ws._WORKSPACES.clear()


@pytest.fixture
def local_repo(tmp_path) -> Path:
    """Create a small local git repository to clone from."""
    repo = tmp_path / "origin-repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "main.py").write_text("def main():\n    return 42\n")
    (repo / "README.md").write_text("# fixture repo\n")

    env = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
    run = lambda *args: subprocess.run(  # noqa: E731
        ["git", *args], cwd=repo, check=True, capture_output=True, env={**env, "PATH": "/usr/bin:/bin"}
    )
    run("init", "-q", "-b", "main")
    run("-c", "user.email=t@test", "-c", "user.name=t", "add", ".")
    run("-c", "user.email=t@test", "-c", "user.name=t", "commit", "-qm", "init")
    return repo


@pytest.fixture
def fake_workspace(workspace_root):
    """Register a workspace backed by a real directory, without cloning."""
    from gemini_cli_agent import workspace as ws

    path = workspace_root / "fixture-repo-abcd1234"
    (path / "src" / "deep" / "deeper").mkdir(parents=True)
    (path / "src" / "main.py").write_text("print('hi')\n")
    (path / "src" / "deep" / "nested.py").write_text("x = 1\n")
    (path / "node_modules").mkdir()
    (path / "node_modules" / "junk.js").write_text("// noise\n")
    (path / "README.md").write_text("# fixture\n")

    workspace = ws.Workspace(
        workspace_id="fixture-repo-abcd1234",
        repo_url="https://github.com/fixture/repo",
        ref=None,
        path=path,
        commit="0" * 40,
        size_mb=0.01,
    )
    ws._WORKSPACES[workspace.workspace_id] = workspace
    return workspace
