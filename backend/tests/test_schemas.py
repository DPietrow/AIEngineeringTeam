import pytest
from pydantic import ValidationError

from agentteam.schemas import (
    DesignSpec,
    Patch,
    PullRequest,
    TestReport,
    Verdict,
)


def test_design_spec_round_trip():
    spec = DesignSpec(
        run_id="r1",
        summary="Add feature",
        changes=[{"path": "a.py", "action": "modify", "description": "tweak"}],
        acceptance_criteria=["tests pass"],
    )
    assert DesignSpec.model_validate_json(spec.model_dump_json()) == spec


def test_unknown_fields_rejected():
    with pytest.raises(ValidationError):
        Patch(run_id="r1", branch="b", diff="", files_changed=[], bogus=1)


def test_verdict_decision_is_constrained():
    with pytest.raises(ValidationError):
        Verdict(run_id="r1", decision="maybe", summary="?")


def test_test_report_and_pull_request_defaults():
    report = TestReport(run_id="r1", passed=True, command="pytest", exit_code=0, duration_s=1.2)
    assert report.failures == []
    pr = PullRequest(run_id="r1", title="t", body="b", head_branch="feat/x")
    assert pr.base_branch == "main"
