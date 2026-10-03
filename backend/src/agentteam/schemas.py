"""Typed handoff artifacts passed between agents.

Design spec -> Patch -> Test report -> Verdict -> Pull request.
All models forbid unknown fields so malformed agent output fails loudly.
"""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def _now() -> datetime:
    return datetime.now(UTC)


class Artifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    created_at: datetime = Field(default_factory=_now)


# --- Architect -> design spec -------------------------------------------------


class PlannedChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    action: Literal["create", "modify", "delete"]
    description: str


class DesignSpec(Artifact):
    summary: str
    changes: list[PlannedChange]
    acceptance_criteria: list[str]
    risks: list[str] = Field(default_factory=list)


# --- Implementation -> patch --------------------------------------------------


class Patch(Artifact):
    branch: str
    diff: str
    files_changed: list[str]
    rationale: str = ""
    attempt: int = Field(default=1, ge=1)


# --- Testing -> test report ---------------------------------------------------


class TestFailure(BaseModel):
    __test__ = False  # not a pytest class
    model_config = ConfigDict(extra="forbid")

    name: str
    message: str


class TestReport(Artifact):
    __test__ = False  # not a pytest class

    passed: bool
    command: str
    exit_code: int
    duration_s: float = Field(ge=0)
    timed_out: bool = False
    tests_run: int = Field(default=0, ge=0)
    failures: list[TestFailure] = Field(default_factory=list)
    output_tail: str = ""


# --- Review -> verdict --------------------------------------------------------


class ReviewComment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    line: int | None = Field(default=None, ge=1)
    severity: Literal["blocker", "major", "minor", "nit"]
    message: str


class Verdict(Artifact):
    decision: Literal["approved", "changes_requested"]
    summary: str
    comments: list[ReviewComment] = Field(default_factory=list)


# --- Delivery -> pull request -------------------------------------------------


class PullRequest(Artifact):
    title: str
    body: str
    head_branch: str
    base_branch: str = "main"
    url: str | None = None
    number: int | None = Field(default=None, ge=1)
