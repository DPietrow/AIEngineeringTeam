"""Deterministic graders. Pass/fail is decided by code, tests and real exit codes, never by
asking a model whether its own work is good.

A trial passes when every *required* grade passes. Non-required grades are reported as
warnings (they show up on the scorecard but do not fail the trial).
"""

import fnmatch
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from ..sandbox import SandboxConfig, run_in_sandbox

HIDDEN_TEST_NAME = "test_hidden_acceptance.py"
_IGNORE = shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".venv")


@dataclass
class Grade:
    name: str
    passed: bool
    detail: str = ""
    required: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


def grade_status(status: str, reason: str = "") -> Grade:
    return Grade("pipeline_done", status == "done", f"run ended '{status}' {reason}".strip())


def grade_budget(cost: float, cap: float) -> Grade:
    return Grade("within_budget", cost <= cap, f"${cost:.4f} (cap ${cap:.2f})")


def grade_scope(files: list[str], allowed: tuple[str, ...]) -> Grade:
    outside = [f for f in files if not any(fnmatch.fnmatch(f, pat) for pat in allowed)]
    return Grade("in_scope", not outside, f"outside allowed paths: {outside}" if outside else "")


def _diff_blocks(diff: str) -> dict[str, str]:
    blocks: dict[str, str] = {}
    for chunk in re.split(r"(?m)^diff --git ", diff):
        if not chunk.strip():
            continue
        match = re.match(r"a/(\S+) b/(\S+)", chunk)
        if match:
            blocks[match.group(2)] = chunk
    return blocks


def grade_tests_not_weakened(diff: str) -> Grade:
    """Agents can 'pass' by deleting or weakening tests. Catch the obvious cases: a deleted test
    file, or fewer assertions in tests/ than before."""
    added = removed = 0
    deleted: list[str] = []
    for path, block in _diff_blocks(diff).items():
        if not path.startswith("tests/"):
            continue
        if "deleted file mode" in block:
            deleted.append(path)
        for line in block.splitlines():
            if line.startswith("+") and not line.startswith("+++") and "assert" in line:
                added += 1
            elif line.startswith("-") and not line.startswith("---") and "assert" in line:
                removed += 1
    ok = not deleted and added >= removed
    detail = f"deleted test files: {deleted}; " if deleted else ""
    return Grade("tests_not_weakened", ok, f"{detail}assertions +{added} / -{removed}")


def _tail(text: str, limit: int = 600) -> str:
    text = text.strip()
    return text if len(text) <= limit else "..." + text[-limit:]


def grade_with_hidden_tests(
    workspace: Path, acceptance_file: Path, cfg: SandboxConfig, scratch_dir: Path
) -> list[Grade]:
    """Copy the finished workspace, then (1) run the project's existing tests as a regression
    check and (2) run the hidden acceptance tests. The agents never see the hidden file."""
    scratch_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="grade-", dir=scratch_dir))
    try:
        shutil.copytree(workspace, tmp / "ws", ignore=_IGNORE)
        ws = tmp / "ws"
        quiet = ["pytest", "-q", "-p", "no:cacheprovider"]
        regression = run_in_sandbox(ws, quiet, cfg)
        (ws / "tests").mkdir(exist_ok=True)
        shutil.copyfile(acceptance_file, ws / "tests" / HIDDEN_TEST_NAME)
        acceptance = run_in_sandbox(ws, [*quiet, f"tests/{HIDDEN_TEST_NAME}"], cfg)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return [
        Grade(
            "regression_suite",
            regression.exit_code == 0 and not regression.timed_out,
            _tail(regression.stdout + regression.stderr),
        ),
        Grade(
            "hidden_acceptance",
            acceptance.exit_code == 0 and not acceptance.timed_out,
            _tail(acceptance.stdout + acceptance.stderr),
        ),
    ]


def grade_review_decision(expected: str, decision: str, comments: list[dict]) -> list[Grade]:
    serious = [c for c in comments if c.get("severity") in ("blocker", "major")]
    grades = [
        Grade("correct_decision", decision == expected, f"expected {expected}, got {decision}")
    ]
    if expected == "changes_requested":
        grades.append(
            Grade(
                "cites_serious_issue",
                bool(serious),
                f"{len(serious)} blocker/major comments",
                required=False,
            )
        )
    return grades
