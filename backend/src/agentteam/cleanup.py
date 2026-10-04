"""Tidy per-run git worktrees and local agent branches left behind by finished runs.

    uv run python -m agentteam.cleanup --dry-run
    uv run python -m agentteam.cleanup --older-than-hours 24

Automatic cleanup already happens for delivered runs (PR opened) and rejected runs. This
command handles the rest: runs that failed, errored, were stopped, or found nothing to change.

It never touches a run that is still active (pending, running, awaiting_approval, approved,
delivering). `done` runs are excluded by default: without delivery configured, a done run's
local branch is the ONLY copy of the work. Pass --statuses to override, deliberately.
"""

import argparse
import sys
from pathlib import Path

from .config import Settings
from .tracing import Tracer
from .workspace import cleanup_workspace

ACTIVE = {"pending", "running", "awaiting_approval", "approved", "delivering"}
DEFAULT_STATUSES = ["failed", "error", "stopped", "no_changes", "rejected"]


def cleanup_runs(
    settings: Settings,
    tracer: Tracer,
    statuses: list[str],
    older_than_hours: float,
    dry_run: bool,
) -> list[tuple[str, dict[str, bool]]]:
    """Returns (run_id, what_was_or_would_be_removed) for each run that had a workspace."""
    refused = ACTIVE & set(statuses)
    if refused:
        raise ValueError(f"refusing to clean active runs: {sorted(refused)}")
    repo = Path(settings.toy_repo_path or "")
    if not (repo / ".git").exists():
        raise ValueError("TOY_REPO_PATH is not set to a git repository")
    workspaces = Path(settings.workspaces_dir)
    results: list[tuple[str, dict[str, bool]]] = []
    for run_id in tracer.runs_with_status(statuses, older_than_hours):
        if dry_run:
            exists = (workspaces / run_id[:12]).exists()
            if exists:
                results.append((run_id, {"worktree": True, "branch": True}))
            continue
        removed = cleanup_workspace(repo, workspaces, run_id)
        if removed["worktree"] or removed["branch"]:
            results.append((run_id, removed))
    return results


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    load_dotenv()
    p = argparse.ArgumentParser(prog="agentteam.cleanup")
    p.add_argument("--statuses", default=",".join(DEFAULT_STATUSES))
    p.add_argument("--older-than-hours", type=float, default=24.0)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    settings = Settings.from_env()
    tracer = Tracer(settings.database_path)
    statuses = [s.strip() for s in args.statuses.split(",") if s.strip()]
    try:
        results = cleanup_runs(settings, tracer, statuses, args.older_than_hours, args.dry_run)
    except ValueError as exc:
        print(f"error: {exc}")
        return 2
    verb = "would remove" if args.dry_run else "removed"
    for run_id, removed in results:
        parts = [k for k, v in removed.items() if v]
        print(f"{verb} {', '.join(parts)} for run {run_id[:12]}")
    print(f"{len(results)} run workspace(s) {'to clean' if args.dry_run else 'cleaned'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
