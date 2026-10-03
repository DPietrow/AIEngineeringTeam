import dataclasses
import json
import subprocess

import pytest

from agentteam.agent_loop import StepLimitExceeded
from agentteam.agents.architect import Architect
from agentteam.api import stream_events
from agentteam.app import create_app
from agentteam.db import connect
from agentteam.llm import FakeLLM, _tool_turn
from agentteam.orchestrator import Orchestrator
from agentteam.prompts import config_hash
from agentteam.tracing import NoActiveRun, set_tracer
from agentteam.worker import work_once

from .conftest import needs_npx


@pytest.fixture
def app(settings):
    return create_app({"TESTING": True, "DATABASE_PATH": settings.database_path})


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


def run_status(app, run_id):
    conn = connect(app.config["DATABASE_PATH"])
    try:
        return dict(conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone())
    finally:
        conn.close()


def submit(summary="s"):
    return _tool_turn(
        "submit",
        "submit_design_spec",
        {
            "summary": summary,
            "changes": [{"path": "app.py", "action": "modify", "description": "d"}],
            "acceptance_criteria": ["ok"],
        },
    )


def test_post_runs_validates_input(app):
    client = app.test_client()
    assert client.post("/api/runs", json={}).status_code == 400
    assert client.post("/api/runs", json={"task": "   "}).status_code == 400
    assert client.post("/api/runs", json={"task": "x" * 6000}).status_code == 400


@needs_npx
def test_end_to_end_run_produces_spec_patch_and_trace(app, tracer, settings, toy_repo):
    client = app.test_client()
    res = client.post("/api/runs", json={"task": "Add a notes file"})
    assert res.status_code == 202
    run_id = res.get_json()["id"]

    orchestrator = Orchestrator(tracer, FakeLLM(tracer), settings)
    assert work_once(tracer, orchestrator) is True
    assert work_once(tracer, orchestrator) is False  # queue drained

    detail = client.get(f"/api/runs/{run_id}").get_json()
    assert detail["run"]["status"] == "done", detail["run"]
    assert detail["run"]["config_hash"] == config_hash(app.config["LLM_MODEL"])
    names = [s["name"] for s in detail["spans"]]
    assert names == [
        "run",
        "architect",
        "llm.architect.step1",
        "workspace.create",
        "implementation",
        "llm.implementation.step1",
        "mcp.filesystem.write_file",
        "llm.implementation.step2",
        "testing",
        "llm.testing.step1",
        "mcp.terminal.run_command",
        "llm.testing.step2",
        "mcp.terminal.run_command",
        "llm.testing.step3",
        "review",
        "llm.review.step1",
    ]
    assert [a["artifact"] for a in detail["artifacts"]] == [
        "design_spec",
        "patch",
        "test_report",
        "verdict",
    ]
    assert detail["artifacts"][2]["data"]["passed"] is True
    assert detail["artifacts"][3]["data"]["decision"] == "approved"

    patch = detail["artifacts"][1]["data"]
    assert patch["files_changed"] == ["AGENT_NOTES.md"]
    assert "AGENT_NOTES.md" in patch["diff"]
    assert detail["run"]["total_cost_usd"] == 0  # fake LLM is free

    # The change lives on its own branch of the toy repo; main is untouched.
    branches = subprocess.run(
        ["git", "branch", "--list", "agent/*"], cwd=toy_repo, capture_output=True, text=True
    ).stdout
    assert patch["branch"] in branches
    assert not (toy_repo / "AGENT_NOTES.md").exists()

    llm_span = next(s for s in detail["spans"] if s["name"] == "llm.implementation.step1")
    assert llm_span["callsite_file"].endswith("agent_loop.py")  # not llm.py


def test_architect_reads_docs_through_mcp(app, tracer, toy_repo):
    def script(name, system, messages, tools):
        if sum(1 for m in messages if m["role"] == "assistant") == 0:
            assert "docs__read_doc" in {t["name"] for t in tools}
            assert not any(t["name"].startswith("filesystem") for t in tools)
            return _tool_turn("t1", "docs__list_docs", {})
        listing = messages[-1]["content"][0]["content"]
        assert "docs/guide.md" in listing
        return submit("from docs")

    run_id = tracer.create_run("task")
    with tracer.run(run_id):
        spec = Architect(FakeLLM(tracer, script=script), tracer, toy_repo).design(run_id, "task")
    assert spec.summary == "from docs"

    conn = connect(app.config["DATABASE_PATH"])
    try:
        names = [r[0] for r in conn.execute("SELECT name FROM spans ORDER BY started_at")]
    finally:
        conn.close()
    assert "mcp.docs.list_docs" in names


def test_step_limit_marks_run_error(app, tracer, settings):
    def loop_forever(name, system, messages, tools):
        return _tool_turn(f"t{len(messages)}", "docs__list_docs", {})

    tight = dataclasses.replace(settings, max_architect_steps=2)
    run_id = tracer.create_run("task")
    tracer.claim_next_run()
    Orchestrator(tracer, FakeLLM(tracer, script=loop_forever), tight).execute(run_id, "task")

    run = run_status(app, run_id)
    assert run["status"] == "error"
    events = [
        e["type"] for e in connect(app.config["DATABASE_PATH"]).execute("SELECT type FROM events")
    ]
    assert "agent.step_limit" in events
    assert StepLimitExceeded.__name__ in json.dumps(
        [dict(r) for r in connect(app.config["DATABASE_PATH"]).execute("SELECT data FROM events")]
    )


def test_missing_toy_repo_marks_run_error(app, tracer, settings):
    broken = dataclasses.replace(settings, toy_repo_path=None)
    run_id = tracer.create_run("task")
    tracer.claim_next_run()
    Orchestrator(tracer, FakeLLM(tracer), broken).execute(run_id, "task")
    assert run_status(app, run_id)["status"] == "error"


def test_llm_failure_marks_run_error(app, tracer, settings):
    class Boom(FakeLLM):
        def _converse(self, *a, **k):
            raise RuntimeError("api down")

    run_id = tracer.create_run("task")
    tracer.claim_next_run()
    Orchestrator(tracer, Boom(tracer), settings).execute(run_id, "task")

    conn = connect(app.config["DATABASE_PATH"])
    try:
        failed = conn.execute("SELECT name FROM spans WHERE status = 'error'").fetchall()
    finally:
        conn.close()
    assert run_status(app, run_id)["status"] == "error"
    assert {r[0] for r in failed} == {"run", "architect", "llm.architect.step1"}


def test_sse_stream_replays_and_resumes(app, tracer):
    run_id = tracer.create_run("task")
    tracer.claim_next_run()
    with tracer.run(run_id), tracer.span("work"):
        tracer.emit("artifact.created", {"artifact": "x"})
    tracer.set_run_status(run_id, "done")
    db = app.config["DATABASE_PATH"]

    events = parse_sse(stream_events(db, run_id, 0, poll_interval=0.01))
    types = [e[1] for e in events]
    assert types[0] == "run.created"
    assert types[-1] == "end"
    assert {"span.start", "span.end", "artifact.created"} <= set(types)

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


def test_architect_requires_a_run(tracer, toy_repo):
    with pytest.raises(NoActiveRun):
        Architect(FakeLLM(tracer), tracer, toy_repo).design("r", "task")
