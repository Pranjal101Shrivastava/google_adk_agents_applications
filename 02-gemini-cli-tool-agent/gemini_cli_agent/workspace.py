"""Sandboxed git checkouts for the Gemini CLI to analyse.

Everything in this module exists because the agent clones **untrusted
third-party source** and then runs a subprocess against it. The rules it
enforces:

* Only ``https://`` (or ``git@``-style SSH) URLs on an allow-listed host.
* No shell. Every subprocess call takes an argument list, so a repository URL
  containing ``; rm -rf /`` is a URL that fails to clone, not a command.
* Workspaces live under one root, and every path handed back to a tool is
  re-checked to be inside it, which stops ``../`` traversal.
* Clones are shallow, size-capped and count-capped.

The reference implementations of this pattern interpolate the URL into a
``shell=True`` string. That is the hole this module closes.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from .config import config

logger = logging.getLogger(__name__)

# ``git@host:owner/repo.git`` - the SCP-like syntax git accepts for SSH.
_SCP_LIKE = re.compile(r"^(?P<user>[\w.-]+)@(?P<host>[\w.-]+):(?P<path>[\w./~-]+)$")

# A git ref we are willing to pass to `git checkout`. Deliberately strict:
# no spaces, no `-` prefix (which would be read as a flag), no shell glyphs.
_SAFE_REF = re.compile(r"^(?!-)[\w][\w./-]{0,200}$")

# The repository path portion of a URL: `/owner/repo` or `/group/sub/repo.git`.
# Nothing a real repository path needs is excluded, and shell glyphs, spaces,
# query strings and fragments all are. subprocess never sees a shell, so this
# is defence in depth rather than the primary control.
_SAFE_URL_PATH = re.compile(r"^/?[\w.~-]+(?:/[\w.~-]+)*/?$")


class WorkspaceError(RuntimeError):
    """Raised when a checkout is rejected or fails."""


@dataclass
class Workspace:
    """A prepared, on-disk checkout the CLI may be pointed at."""

    workspace_id: str
    repo_url: str
    ref: str | None
    path: Path
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    commit: str | None = None
    size_mb: float = 0.0

    def to_dict(self) -> dict[str, object]:
        """Serialise for a tool response. ``path`` is stringified for JSON."""
        return {
            "workspace_id": self.workspace_id,
            "repo_url": self.repo_url,
            "ref": self.ref,
            "path": str(self.path),
            "commit": self.commit,
            "size_mb": round(self.size_mb, 2),
            "created_at": self.created_at.isoformat(),
        }


# Process-local registry. A multi-replica deployment gets one registry per
# replica, which is fine: a workspace is local to the container that cloned it.
_WORKSPACES: dict[str, Workspace] = {}


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
def normalise_repo_url(repo_url: str) -> str:
    """Validate a repository URL and return it in canonical form.

    Args:
        repo_url: The URL as supplied by the model or the user.

    Returns:
        The validated URL, whitespace-stripped.

    Raises:
        WorkspaceError: If the URL is malformed, uses a scheme we do not
            accept, or points at a host outside the allowlist.
    """
    candidate = (repo_url or "").strip()
    if not candidate:
        raise WorkspaceError("No repository URL was provided.")

    # Reject control characters outright; they have no business in a URL and
    # are the classic smuggling vector for argument injection.
    if any(ord(ch) < 32 for ch in candidate):
        raise WorkspaceError("Repository URL contains control characters.")

    # A leading dash would be parsed by git as an option, not a URL.
    if candidate.startswith("-"):
        raise WorkspaceError("Repository URL may not start with '-'.")

    if scp_match := _SCP_LIKE.match(candidate):
        host = scp_match.group("host").lower()
        if not _SAFE_URL_PATH.match(scp_match.group("path")):
            raise WorkspaceError(
                f"Repository path {scp_match.group('path')!r} contains unexpected "
                "characters. Expected something like owner/repo.git."
            )
    else:
        parsed = urlparse(candidate)
        if parsed.scheme not in ("https", "ssh"):
            raise WorkspaceError(
                f"Unsupported URL scheme {parsed.scheme!r}. Use https:// or an "
                "ssh git@host:owner/repo URL. file://, git:// and http:// are "
                "refused."
            )
        if not parsed.hostname:
            raise WorkspaceError("Repository URL has no host.")
        host = parsed.hostname.lower()
        if parsed.query or parsed.fragment:
            raise WorkspaceError(
                "Repository URL may not carry a query string or fragment."
            )
        if not _SAFE_URL_PATH.match(parsed.path or "/"):
            raise WorkspaceError(
                f"Repository path {parsed.path!r} contains characters a git path "
                "has no reason to contain. Expected something like /owner/repo."
            )

    allowed = config.allowed_clone_hosts
    # Match the host itself or any subdomain of an allow-listed host, so
    # `github.com` covers `www.github.com` but never `github.com.evil.test`.
    if not any(host == a or host.endswith("." + a) for a in allowed):
        raise WorkspaceError(
            f"Host {host!r} is not in ALLOWED_CLONE_HOSTS ({', '.join(allowed)})."
        )

    return candidate


def validate_ref(ref: str | None) -> str | None:
    """Validate a git ref, returning ``None`` for an absent one.

    Raises:
        WorkspaceError: If the ref could be read as a flag or contains
            characters a branch name has no reason to contain.
    """
    if ref is None or not str(ref).strip():
        return None
    candidate = str(ref).strip()
    if not _SAFE_REF.match(candidate):
        raise WorkspaceError(
            f"Ref {candidate!r} is not a plain branch, tag or commit name."
        )
    return candidate


def repo_name_from_url(repo_url: str) -> str:
    """Derive a filesystem-safe directory name from a repository URL."""
    tail = repo_url.rstrip("/").split("/")[-1]
    tail = tail.removesuffix(".git")
    # Keep only characters that are unambiguous on every filesystem.
    safe = re.sub(r"[^A-Za-z0-9._-]", "-", tail).strip("-._")
    return safe or "repo"


# --------------------------------------------------------------------------
# Path confinement
# --------------------------------------------------------------------------
def resolve_within(workspace: Workspace, relative_path: str) -> Path:
    """Resolve ``relative_path`` inside *workspace*, refusing escapes.

    Args:
        workspace: The workspace the path must stay inside.
        relative_path: A path relative to the workspace root. ``""`` or ``"."``
            mean the root itself.

    Returns:
        The resolved absolute path.

    Raises:
        WorkspaceError: If the path resolves outside the workspace, whether via
            ``..`` segments, an absolute path, or a symlink.
    """
    root = workspace.path.resolve()
    target = (root / (relative_path or ".")).resolve()
    if target != root and root not in target.parents:
        raise WorkspaceError(
            f"Path {relative_path!r} resolves outside the workspace."
        )
    return target


# --------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------
def directory_size_mb(path: Path) -> float:
    """Return the on-disk size of *path* in megabytes, following no symlinks."""
    total = 0
    for item in path.rglob("*"):
        if item.is_symlink():
            continue
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:  # a file that vanished mid-walk
            continue
    return total / (1024 * 1024)


def _run_git(args: list[str], cwd: Path | None = None, timeout: int | None = None):
    """Run git with an argument list and never a shell.

    ``shell=False`` is the whole point: argv entries are passed to execve
    verbatim, so no part of a repository URL or ref can be reinterpreted as a
    command.
    """
    logger.debug("git %s", " ".join(args))
    return subprocess.run(  # noqa: S603 - argv list, shell=False, fixed binary
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout or config.clone_timeout_seconds,
        check=False,
        shell=False,
    )


def create_workspace(repo_url: str, ref: str | None = None) -> Workspace:
    """Clone *repo_url* into a fresh sandboxed workspace.

    Args:
        repo_url: Repository to clone. Validated against the host allowlist.
        ref: Optional branch, tag or commit to check out.

    Returns:
        The registered :class:`Workspace`.

    Raises:
        WorkspaceError: On validation failure, a clone error, a timeout, or a
            checkout that exceeds ``MAX_WORKSPACE_MB``.
    """
    url = normalise_repo_url(repo_url)
    safe_ref = validate_ref(ref)

    if len(_WORKSPACES) >= config.max_concurrent_workspaces:
        raise WorkspaceError(
            f"Workspace limit reached ({config.max_concurrent_workspaces}). "
            "Call cleanup_workspace on one you no longer need."
        )

    workspace_id = f"{repo_name_from_url(url)}-{uuid.uuid4().hex[:8]}"
    root = config.workspace_root
    root.mkdir(parents=True, exist_ok=True)
    path = root / workspace_id

    clone_args = [
        "clone",
        "--depth",
        str(config.clone_depth),
        "--single-branch",
        # Do not run repository-supplied hooks or filters during clone.
        "--no-tags",
        "--config",
        "core.hooksPath=/dev/null",
    ]
    if safe_ref:
        clone_args += ["--branch", safe_ref]
    # `--` ends option parsing, so even a URL that survived validation cannot
    # be treated as a flag.
    clone_args += ["--", url, str(path)]

    result = _run_git(clone_args)
    if result.returncode != 0:
        shutil.rmtree(path, ignore_errors=True)
        if safe_ref:
            # A ref that is a commit SHA cannot be cloned with --branch; retry
            # with a default clone, then check the ref out.
            logger.info("Branch clone of %s failed; retrying then checking out ref.", url)
            retry = _run_git(
                [
                    "clone",
                    "--single-branch",
                    "--no-tags",
                    "--config",
                    "core.hooksPath=/dev/null",
                    "--",
                    url,
                    str(path),
                ]
            )
            if retry.returncode != 0:
                shutil.rmtree(path, ignore_errors=True)
                raise WorkspaceError(_git_error("clone", retry.stderr))
            checkout = _run_git(["checkout", "--", safe_ref], cwd=path)
            if checkout.returncode != 0:
                checkout = _run_git(["checkout", safe_ref], cwd=path)
            if checkout.returncode != 0:
                shutil.rmtree(path, ignore_errors=True)
                raise WorkspaceError(_git_error("checkout", checkout.stderr))
        else:
            raise WorkspaceError(_git_error("clone", result.stderr))

    size_mb = directory_size_mb(path)
    if size_mb > config.max_workspace_mb:
        shutil.rmtree(path, ignore_errors=True)
        raise WorkspaceError(
            f"Checkout is {size_mb:.0f} MB, over the {config.max_workspace_mb} MB "
            "limit. Raise MAX_WORKSPACE_MB or pick a smaller repository."
        )

    head = _run_git(["rev-parse", "HEAD"], cwd=path, timeout=30)
    commit = head.stdout.strip() if head.returncode == 0 else None

    workspace = Workspace(
        workspace_id=workspace_id,
        repo_url=url,
        ref=safe_ref,
        path=path,
        commit=commit,
        size_mb=size_mb,
    )
    _WORKSPACES[workspace_id] = workspace
    logger.info(
        "Prepared workspace %s (%.1f MB) at %s", workspace_id, size_mb, path
    )
    return workspace


def _git_error(operation: str, stderr: str) -> str:
    """Build a short, non-leaky error message from git's stderr."""
    detail = (stderr or "").strip().splitlines()
    tail = detail[-1] if detail else "no detail from git"
    return f"git {operation} failed: {tail}"


def get_workspace(workspace_id: str) -> Workspace:
    """Look up a registered workspace.

    Raises:
        WorkspaceError: If the ID is unknown or its directory has vanished.
    """
    workspace = _WORKSPACES.get(workspace_id)
    if workspace is None:
        known = ", ".join(sorted(_WORKSPACES)) or "none"
        raise WorkspaceError(
            f"Unknown workspace_id {workspace_id!r}. Prepared workspaces: {known}."
        )
    if not workspace.path.is_dir():
        _WORKSPACES.pop(workspace_id, None)
        raise WorkspaceError(
            f"Workspace {workspace_id!r} no longer exists on disk; prepare it again."
        )
    return workspace


def list_workspaces() -> list[Workspace]:
    """Return every registered workspace, oldest first."""
    return sorted(_WORKSPACES.values(), key=lambda w: w.created_at)


def destroy_workspace(workspace_id: str) -> bool:
    """Delete a workspace from disk and from the registry.

    Returns:
        ``True`` if a workspace was removed, ``False`` if the ID was unknown.
    """
    workspace = _WORKSPACES.pop(workspace_id, None)
    if workspace is None:
        return False
    shutil.rmtree(workspace.path, ignore_errors=True)
    logger.info("Destroyed workspace %s", workspace_id)
    return True


def destroy_all_workspaces() -> int:
    """Delete every workspace. Returns the number removed."""
    count = 0
    for workspace_id in list(_WORKSPACES):
        if destroy_workspace(workspace_id):
            count += 1
    return count
