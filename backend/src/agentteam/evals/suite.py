"""Eval suite definition, loaded from JSON so cases are data, not code.

Two kinds of case:
- coding: a task for the whole team. A hidden acceptance test file (never shown to any agent)
  decides whether the result actually works.
- review: a seeded patch (good or bad) applied to the repo and handed to the Review agent,
  which is graded on whether it makes the right call.
"""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CodingCase:
    id: str
    task: str
    acceptance: str  # path to the hidden pytest file, relative to the suite directory
    allowed_paths: tuple[str, ...] = ("toyapp/*", "tests/*", "docs/*", "README.md")
    max_cost_usd: float = 0.40
    difficulty: str = "easy"
    # Known-correct edits, used only to validate the grader itself (never shown to agents).
    reference: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ReviewCase:
    id: str
    description: str
    expected: str  # "approved" | "changes_requested"
    spec: dict[str, Any]  # DesignSpecBody fields
    operations: list[dict[str, Any]]  # replace | write | delete, applied to the base repo
    report_passed: bool = True  # the (real-looking) test report the reviewer is shown
    tags: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Suite:
    name: str
    root: Path
    coding: list[CodingCase]
    review: list[ReviewCase]
    hash: str

    def acceptance_path(self, case: CodingCase) -> Path:
        return self.root / case.acceptance


def load_suite(path: str | Path) -> Suite:
    path = Path(path).resolve()
    raw = json.loads(path.read_text(encoding="utf-8"))
    root = path.parent
    coding = [
        CodingCase(
            id=c["id"],
            task=c["task"],
            acceptance=c["acceptance"],
            allowed_paths=tuple(c.get("allowed_paths", CodingCase.allowed_paths)),
            max_cost_usd=float(c.get("max_cost_usd", CodingCase.max_cost_usd)),
            difficulty=c.get("difficulty", "easy"),
            reference=c.get("reference", []),
        )
        for c in raw.get("cases", [])
    ]
    review = [
        ReviewCase(
            id=c["id"],
            description=c["description"],
            expected=c["expected"],
            spec=c["spec"],
            operations=c["operations"],
            report_passed=bool(c.get("report_passed", True)),
            tags=tuple(c.get("tags", [])),
        )
        for c in raw.get("review_cases", [])
    ]
    ids = [c.id for c in coding] + [c.id for c in review]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case ids in suite")
    for case in review:
        if case.expected not in ("approved", "changes_requested"):
            raise ValueError(f"review case {case.id}: bad expected value {case.expected!r}")

    digest = hashlib.sha256(path.read_bytes())
    for case in coding:  # the suite's identity includes the hidden tests
        digest.update((root / case.acceptance).read_bytes())
    return Suite(
        name=raw.get("name", path.stem),
        root=root,
        coding=coding,
        review=review,
        hash=digest.hexdigest()[:12],
    )
