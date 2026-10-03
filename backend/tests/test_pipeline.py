import json

import pytest

from agentteam.agents.architect import Architect
from agentteam.api import stream_events
from agentteam.app import create_app
from agentteam.db import connect
from agentteam.llm import FakeLLM
from agentteam.orchestrator import Orchestrator
from agentteam.prompts import config_hash
from agentteam.tracing import set_tracer
from agentteam.worker import work_once


@pytest.fixture
def app(tmp_path):
    return create_app({"TESTING": True, "DATABASE_PATH": str(tmp_path / "app.db")})


@pytest.fixture
def tracer(app):
    t = app.extensions["tracer"]
    set_tracer(t)
    return t


def parse_sse(frames):
    out = []
    for frame in frames:
        if frame.startswith(":"):
            continue
        fields = dict(line.split(": ", 1) for line in frame.strip().split("\n"))
        out.append((fields.get("id"), fields["event"], json.loads(fields["data"])))
    return out


def test_post_runs_validates_input(app):
    client = app.test_client()
    assert client.post("/api/runs", json={}).status_code == 400
    assert client.post("/api/runs", json={"task": "   "}).status_code == 400
    assert client.post("/api/runs", json={"task": "x" * 6000}).status_code == 400


def test_end_to_end_run_produces_spec_and_trace(app, tracer):
    client = app.test_client()
    res = client.post("/api/runs", json={"task": "Add a /ping endpoint"})
    assert res.status_code == 202
    run_id = res.get_json()["id"]

    assert work_once(tracer, Orchestrator(tracer, FakeLLM(tracer))) is True
    assert work_once(tracer, Orchestrator(tracer, FakeLLM(tracer))) is False  # queue drained

    detail = client.get(f"/api/runs/{run_id}").get_json()
    assert detail["run"]["status"] == "done"
    assert detail["run"]["config_hash"] == config_hash(app.config["LLM_MODEL"])
    names = [s["name"] for s in detail["spans"]]
    assert names == ["run", "architect", "llm.design_spec"]
    assert detail["artifacts"][0]["artifact"] == "design_spec"
    assert detail["artifacts"][0]["data"]["changes"][0]["path"] == "src/example.py"

    llm_span = detail["spans"][2]
    assert llm_span["kind"] == "llm"
    assert llm_span["cost_usd"] == 0  # fake LLM is free
    assert detail["run"]["total_cost_usd"] == 0
    assert llm_span["input"]["user"].startswith("Task:")
    # call-site is attributed to the Architect, not the LLM helper
    assert llm_span["callsite_file"].endswith("architect.py")


def test_sse_stream_replays_and_resumes(app, tracer):
    run_id = tracer.create_run("task")
    tracer.claim_next_run()
    Orchestrator(tracer, FakeLLM(tracer)).execute(run_id, "task")
    db = app.config["DATABASE_PATH"]

    events = parse_sse(stream_events(db, run_id, 0, poll_interval=0.01))
    types = [e[1] for e in events]
    assert types[0] == "run.created"
    assert types[-1] == "end"
    assert "span.start" in types and "span.end" in types and "artifact.created" in types

    ids = [int(e[0]) for e in events if e[0]]
    assert ids == sorted(ids)

    resumed = parse_sse(stream_events(db, run_id, ids[2], poll_interval=0.01))
    assert [int(e[0]) for e in resumed if e[0]] == ids[3:]


def test_sse_unknown_run(app):
    frames = list(stream_events(app.config["DATABASE_PATH"], "nope", 0, poll_interval=0.01))
    assert frames[0].startswith("event: error")


def test_sse_heartbeat_while_run_is_idle(app, tracer):
    run_id = tracer.create_run("task")  # stays pending, so the stream stays open
    gen = stream_events(
        app.config["DATABASE_PATH"], run_id, 10**9, poll_interval=0.01, heartbeat_interval=0.02
    )
    assert next(gen).startswith(": heartbeat")
    gen.close()


def test_claim_is_exclusive(tracer):
    tracer.create_run("only one")
    assert tracer.claim_next_run() is not None
    assert tracer.claim_next_run() is None


def test_llm_failure_marks_run_error(app, tracer):
    class Boom(FakeLLM):
        def _call(self, *a, **k):
            raise RuntimeError("api down")

    run_id = tracer.create_run("task")
    tracer.claim_next_run()
    Orchestrator(tracer, Boom(tracer)).execute(run_id, "task")

    conn = connect(app.config["DATABASE_PATH"])
    try:
        status = conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()[0]
        failed = conn.execute("SELECT name FROM spans WHERE status = 'error'").fetchall()
    finally:
        conn.close()
    assert status == "error"
    assert {r[0] for r in failed} == {"run", "architect", "llm.design_spec"}


def test_architect_requires_a_run(tracer):
    from agentteam.tracing import NoActiveRun

    with pytest.raises(NoActiveRun):
        Architect(FakeLLM(tracer)).design("r", "task")
