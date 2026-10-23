"""Security tests for the checkout sandbox.

This agent clones arbitrary third-party repositories and then runs a
subprocess against them, so these are the tests that matter most. Each one
encodes an attack the sandbox is supposed to stop.
"""

from __future__ import annotations

import pytest

from gemini_cli_agent.workspace import (
    WorkspaceError,
    normalise_repo_url,
    repo_name_from_url,
    resolve_within,
    validate_ref,
)


# --- URL allowlisting ---------------------------------------------------
@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/google/adk-python",
        "https://github.com/google/adk-python.git",
        "https://gitlab.com/group/project",
        "https://bitbucket.org/team/repo",
        "git@github.com:google/adk-python.git",
        "https://www.github.com/google/adk-python",
    ],
)
def test_allowed_urls_pass(url):
    assert normalise_repo_url(url) == url


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://github.com/a/b", "plaintext http is downgradeable"),
        ("file:///etc/passwd", "local filesystem read"),
        ("git://github.com/a/b", "unauthenticated git protocol"),
        ("ftp://github.com/a/b", "not a git transport"),
        ("https://evil.test/a/b", "host not on the allowlist"),
        ("https://github.com.evil.test/a/b", "suffix-confusion lookalike host"),
        ("https://notgithub.com/a/b", "substring lookalike host"),
        ("", "empty input"),
        ("   ", "whitespace only"),
    ],
)
def test_rejected_urls(url, reason):
    with pytest.raises(WorkspaceError):
        normalise_repo_url(url)


def test_url_starting_with_dash_is_rejected():
    """git would parse a leading dash as an option, not a URL."""
    with pytest.raises(WorkspaceError, match="may not start with"):
        normalise_repo_url("--upload-pack=/bin/sh")


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/a/b\nrm -rf /",
        "https://github.com/a/b\rwhoami",
        "https://github.com/a/b\x00",
    ],
)
def test_control_characters_are_rejected(url):
    with pytest.raises(WorkspaceError, match="control characters"):
        normalise_repo_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/a/b; rm -rf /",
        "https://github.com/a/b && whoami",
        "https://github.com/a/b`id`",
        "https://github.com/a/b$(id)",
        "https://github.com/a/b|tee /tmp/x",
        "https://github.com/a/b with spaces",
    ],
)
def test_shell_metacharacters_in_the_path_are_rejected(url):
    """The host is allow-listed here, so the path check is what must catch these.

    The primary guarantee is still that git is invoked with an argv list and
    ``shell=False``, so even a URL that passed validation could not be
    reinterpreted as a command. This is the second layer.
    """
    with pytest.raises(WorkspaceError):
        normalise_repo_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/a/b?upload-pack=evil",
        "https://github.com/a/b#fragment",
    ],
)
def test_query_strings_and_fragments_are_rejected(url):
    with pytest.raises(WorkspaceError):
        normalise_repo_url(url)


def test_nested_group_paths_are_still_accepted():
    """GitLab subgroups are legitimate; the path check must not break them."""
    url = "https://gitlab.com/group/subgroup/project.git"
    assert normalise_repo_url(url) == url


def test_subdomains_of_allowed_hosts_are_accepted():
    assert normalise_repo_url("https://raw.github.com/a/b")


# --- Ref validation ----------------------------------------------------
@pytest.mark.parametrize(
    "ref", ["main", "v1.2.3", "release/2024-01", "feature/ABC-123_fix", "a" * 40]
)
def test_valid_refs(ref):
    assert validate_ref(ref) == ref


@pytest.mark.parametrize("ref", ["", "   ", None])
def test_blank_refs_become_none(ref):
    assert validate_ref(ref) is None


@pytest.mark.parametrize(
    "ref",
    [
        "--upload-pack=touch /tmp/pwned",
        "-x",
        "main; rm -rf /",
        "main && whoami",
        "main`id`",
        "main$(id)",
        "main with spaces",
        "main\nnewline",
        "a" * 300,
    ],
)
def test_dangerous_refs_are_rejected(ref):
    with pytest.raises(WorkspaceError):
        validate_ref(ref)


# --- Directory naming --------------------------------------------------
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/google/adk-python", "adk-python"),
        ("https://github.com/google/adk-python.git", "adk-python"),
        ("https://github.com/google/adk-python/", "adk-python"),
        ("git@github.com:google/adk.python.git", "adk.python"),
    ],
)
def test_repo_name_extraction(url, expected):
    assert repo_name_from_url(url) == expected


def test_repo_name_strips_path_separators():
    """A name used as a directory must not be able to climb out of it."""
    assert "/" not in repo_name_from_url("https://github.com/a/..%2f..%2fetc")


# --- Path confinement --------------------------------------------------
@pytest.mark.parametrize("relative", ["", ".", "src", "src/main.py", "src/deep"])
def test_paths_inside_the_workspace_resolve(fake_workspace, relative):
    resolved = resolve_within(fake_workspace, relative)
    assert resolved == fake_workspace.path.resolve() or (
        fake_workspace.path.resolve() in resolved.parents
    )


@pytest.mark.parametrize(
    "relative",
    [
        "..",
        "../..",
        "../../etc/passwd",
        "src/../../escape",
        "/etc/passwd",
        "src/../../../../../../etc/shadow",
    ],
)
def test_traversal_attempts_are_refused(fake_workspace, relative):
    with pytest.raises(WorkspaceError, match="outside the workspace"):
        resolve_within(fake_workspace, relative)


def test_symlink_escape_is_refused(fake_workspace, tmp_path):
    """A symlink planted in a cloned repo must not widen the sandbox."""
    outside = tmp_path / "outside-secret"
    outside.mkdir()
    (outside / "secret.txt").write_text("sensitive\n")
    (fake_workspace.path / "escape-link").symlink_to(outside)

    with pytest.raises(WorkspaceError, match="outside the workspace"):
        resolve_within(fake_workspace, "escape-link/secret.txt")
