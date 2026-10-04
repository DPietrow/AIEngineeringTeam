"""State machine tests: agents are stubbed so each loop and cap is exercised precisely."""

import dataclasses
import json

import pytest

from agentteam.agents.implementation import Implementation
from agentteam.agents.review import Review
from agentteam.agents.testing import Testing
from agentteam.api import stream_events
from agentteam.app import create_app
from agentteam.db import connect
from agentteam.llm import FakeLLM
from agentteam.orchestrator import Orchestrator
from agentteam.schemas import Patch, TestReport, Verdict
from agentteam.tracing import set_tracer

from .test_pipeline import parse_sse


@pytest.fixture
def app(settings):
    return create_app({"TESTING": True, "DATABASE_PATH": settings.database_path})


@pytest.fixture
def tracer(app):
    t = app.extensions["tracer"]
    set_tracer(t)
    return t


class Script:
    """Scripted outcomes for the stubbed agents."""

    def __init__(self, monkeypatch, tests=(True,), verdicts=("approved",)):
        self.tests = list(tests)
        self.verdicts = list(verdicts)
        self.feedback = []  # feedback handed to each implementation attempt

        def implement(agent, run_id, spec, workspace, attempt=1, feedback=None):
            self.feedback.append(feedback)
            return Patch(run_id=run_id, branch="agent/x", diff="d", files_changed=["a.py"])

        def test(agent, run_id, workspace):
            ok = self.tests.pop(0)
            return TestReport(run_id=run_id, passed=ok, summary="ok" if ok else "boom")

        def review(agent, run_id, spec, patch, report, workspace):
            decision = self.verdicts.pop(0)
            return Verdict(run_id=run_id, decision=decision, summary=decision)

        monkeypatch.setattr(Implementation, "implement", implement)
        monkeypatch.setattr(Testing, "test", test)
        monkeypatch.setattr(Review, "review", review)


def execute(app, tracer, settings, **overrides):
    cfg = dataclasses.replace(settings, **overrides)
    run_id = tracer.create_run("task")
    tracer.claim_next_run()
    Orchestrator(tracer, FakeLLM(tracer), cfg).execute(run_id, "task")
    conn = connect(app.config["DATABASE_PATH"])
    try:
        run = dict(conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone())
        events = [
            (r["type"], json.loads(r["data"]))
            for r in conn.execute(
                "SELECT type, data FROM events WHERE run_id = ? ORDER BY id", (run_id,)
            )
        ]
    finally:
        conn.close()
    return run_id, run, events


def transitions(events):
    return [(d["from"], d["to"]) for t, d in events if t == "state.transition"]


def test_happy_path(app, tracer, settings, monkeypatch):
    script = Script(monkeypatch)
    _, run, events = execute(app, tracer, settings)
    assert run["status"] == "done"
    assert transitions(events) == [
        ("design", "implement"),
        ("implement", "test"),
        ("test", "review"),
        ("review", "done"),
    ]
    assert script.feedback == [None]
    assert not [e for e in events if e[0] == "loop.retry"]


def test_test_failure_loops_back_with_feedback_then_recovers(app, tracer, settings, monkeypatch):
    script = Script(monkeypatch, tests=(False, True))
    _, run, events = execute(app, tracer, settings)
    assert run["status"] == "done"
    assert ("test", "implement") in transitions(events)
    assert len(script.feedback) == 2
    assert script.feedback[0] is None
    assert script.feedback[1].passed is False  # the failing report was handed back
    retries = [d for t, d in events if t == "loop.retry"]
    assert retries == [{"loop": "test", "retry": 1, "cap": settings.max_test_retries}]


def test_test_retry_cap_fails_the_run(app, tracer, settings, monkeypatch):
    script = Script(monkeypatch, tests=(False,) * 10)
    _, run, events = execute(app, tracer, settings, max_test_retries=2)
    assert run["status"] == "failed"
    assert len(script.feedback) == 3  # first attempt + 2 retries, then stop
    assert transitions(events)[-1] == ("test", "failed")
    assert len([e for e in events if e[0] == "loop.retry"]) == 2
    assert not any(t == "state.transition" and d["to"] == "review" for t, d in events)


def test_changes_requested_loops_then_approves(app, tracer, settings, monkeypatch):
    script = Script(monkeypatch, tests=(True, True), verdicts=("changes_requested", "approved"))
    _, run, events = execute(app, tracer, settings)
    assert run["status"] == "done"
    assert len(script.feedback) == 2
    assert script.feedback[1].decision == "changes_requested"
    assert [d["loop"] for t, d in events if t == "loop.retry"] == ["review"]


def test_review_cap_fails_the_run(app, tracer, settings, monkeypatch):
    script = Script(monkeypatch, tests=(True,) * 10, verdicts=("changes_requested",) * 10)
    _, run, events = execute(app, tracer, settings, max_review_rounds=1)
    assert run["status"] == "failed"
    assert len(script.feedback) == 2
    assert transitions(events)[-1] == ("review", "failed")


def test_stream_ends_when_run_fails(app, tracer, settings, monkeypatch):
    Script(monkeypatch, tests=(False,) * 10)
    run_id, run, _ = execute(app, tracer, settings, max_test_retries=0)
    assert run["status"] == "failed"
    events = parse_sse(stream_events(app.config["DATABASE_PATH"], run_id, 0, poll_interval=0.01))
    assert events[-1][1] == "end"
