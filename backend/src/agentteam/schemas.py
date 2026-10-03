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


class DesignSpecBody(BaseModel):
    """The part of a design spec the model writes (also its tool-call schema)."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    changes: list[PlannedChange]
    acceptance_criteria: list[str]
    risks: list[str] = Field(default_factory=list)


class DesignSpec(Artifact, DesignSpecBody):
    pass


# --- Implementation -> patch --------------------------------------------------


class Patch(Artifact):
    branch: str
    diff: str
    files_changed: list[str]
    rationale: str = ""
    attempt: int = Field(default=1, ge=1)


# --- Testing -> test report ---------------------------------------------------


class CommandRun(BaseModel):
    """One sandboxed command the Testing agent ran. Recorded from real exit codes, never
    from what the model says happened."""

    model_config = ConfigDict(extra="forbid")

    command: str
    exit_code: int
    duration_s: float = Field(ge=0)
    timed_out: bool = False
    output_tail: str = ""


class TestFailure(BaseModel):
    __test__ = False  # not a pytest class
    model_config = ConfigDict(extra="forbid")

    name: str
    message: str


class TestReport(Artifact):
    __test__ = False  # not a pytest class

    passed: bool
    runs: list[CommandRun] = Field(default_factory=list)
    missing_checks: list[str] = Field(default_factory=list)
    tests_run: int = Field(default=0, ge=0)
    failures: list[TestFailure] = Field(default_factory=list)
    summary: str = ""


# --- Review -> verdict --------------------------------------------------------


class ReviewComment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    line: int | None = Field(default=None, ge=1)
    severity: Literal["blocker", "major", "minor", "nit"]
    message: str


class VerdictBody(BaseModel):
    """The part of a verdict the model writes (also its tool-call schema)."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["approved", "changes_requested"]
    summary: str
    comments: list[ReviewComment] = Field(default_factory=list)


class Verdict(Artifact, VerdictBody):
    pass


# --- Delivery -> pull request -------------------------------------------------


class PullRequest(Artifact):
    title: str
    body: str
    head_branch: str
    base_branch: str = "main"
    url: str | None = None
    number: int | None = Field(default=None, ge=1)
