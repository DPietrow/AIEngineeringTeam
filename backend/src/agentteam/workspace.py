"""Per-run isolated workspace: a git worktree of the toy repo on its own branch.

Agents never touch the real checkout. The worktree is where Implementation writes and
(later) where tests run; the resulting commit is the patch.
"""

import base64
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

BOT_NAME = "agentteam-bot"
BOT_EMAIL = "agentteam-bot@users.noreply.github.com"


class GitError(RuntimeError):
    pass


@dataclass
class Workspace:
    repo: Path
    path: Path
    branch: str
    base_commit: str


def _git(cwd: Path, *args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})},
    )
    if result.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def create_workspace(
    repo: Path, workspaces_dir: Path, run_id: str, base_ref: str = "main"
) -> Workspace:
    repo = repo.resolve()
    short = run_id[:12]
    path = (workspaces_dir / short).resolve()
    branch = f"agent/{short}"
    base_commit = _git(repo, "rev-parse", base_ref).strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "add", "-b", branch, str(path), base_commit)
    return Workspace(repo=repo, path=path, branch=branch, base_commit=base_commit)


def collect_patch(ws: Workspace, message: str) -> tuple[str, list[str]]:
    """Commit anything new the agent changed, then return the CUMULATIVE patch vs the base
    commit as (unified diff, changed files). Safe to call after every attempt."""
    _git(ws.path, "add", "-A")
    if _git(ws.path, "diff", "--cached", "--name-only").strip():
        _git(
            ws.path,
            "-c",
            f"user.name={BOT_NAME}",
            "-c",
            f"user.email={BOT_EMAIL}",
            "commit",
            "-m",
            message,
        )
    files = [
        f for f in _git(ws.path, "diff", "--name-only", ws.base_commit, "HEAD").splitlines() if f
    ]
    if not files:
        return "", []
    return _git(ws.path, "diff", ws.base_commit, "HEAD"), files


def existing_workspace(
    repo: Path, workspaces_dir: Path, run_id: str, base_ref: str = "main"
) -> Workspace:
    """Re-open the worktree a finished pipeline left behind (used when delivering an
    approved run, possibly in a different process than the one that built the patch)."""
    repo = repo.resolve()
    short = run_id[:12]
    path = (workspaces_dir / short).resolve()
    if not path.exists():
        raise GitError(f"workspace for run {short} no longer exists: {path}")
    branch = _git(path, "rev-parse", "--abbrev-ref", "HEAD").strip()
    return Workspace(
        repo=repo, path=path, branch=branch, base_commit=_git(repo, "rev-parse", base_ref).strip()
    )


def push_branch(ws: Workspace, token: str | None, remote: str = "origin") -> None:
    """Push the agent's branch (never force, never any other branch).

    This is deliberately NOT an agent tool: the harness pushes, after a human approved. The
    token travels in an environment variable (not argv, so it is not visible in process lists
    and never reaches a trace) as an HTTP auth header scoped to github.com.
    """
    if not ws.branch.startswith("agent/"):
        raise GitError(f"refusing to push non-agent branch {ws.branch!r}")
    env: dict[str, str] = {}
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env = {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
            "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}",
        }
    _git(ws.path, "push", remote, f"{ws.branch}:refs/heads/{ws.branch}", env=env)


def cleanup_workspace(
    repo: Path, workspaces_dir: Path, run_id: str, *, delete_branch: bool = True
) -> dict[str, bool]:
    """Remove a run's worktree and its local `agent/<run>` branch. Idempotent and tolerant of
    things already being gone. Only ever touches `agent/*` branches. Returns what was removed.

    The caller decides WHEN this is safe (the work is on GitHub, or a human rejected it, or the
    run failed): this function does not check.
    """
    short = run_id[:12]
    path = (workspaces_dir / short).resolve()
    branch = f"agent/{short}"
    repo = repo.resolve()
    removed = {"worktree": False, "branch": False}
    if path.exists():
        _git(repo, "worktree", "remove", "--force", str(path))
        removed["worktree"] = True
    _git(repo, "worktree", "prune")
    if delete_branch and _git(repo, "branch", "--list", branch).strip():
        _git(repo, "branch", "-D", branch)
        removed["branch"] = True
    return removed


def remove_workspace(ws: Workspace, delete_branch: bool = False) -> None:
    _git(ws.repo, "worktree", "remove", "--force", str(ws.path))
    if delete_branch:
        _git(ws.repo, "branch", "-D", ws.branch)
