import json
import subprocess
from pathlib import Path

import pytest

from agentteam.app import create_app
from agentteam.cleanup import cleanup_runs
from agentteam.db import connect
from agentteam.llm import FakeLLM, _text_turn, _tool_turn
from agentteam.orchestrator import Orchestrator
from agentteam.tracing import set_tracer
from agentteam.workspace import cleanup_workspace, create_workspace

from .conftest import needs_npx


@pytest.fixture
def app(settings):
    return create_app({"TESTING": True, "DATABASE_PATH": settings.database_path})


@pytest.fixture
def tracer(app):
    t = app.extensions["tracer"]
    set_tracer(t)
    return t


def branches(repo: Path) -> str:
    return subprocess.run(
        ["git", "branch", "--list", "agent/*"], cwd=repo, capture_output=True, text=True
    ).stdout


def events(app, run_id: str) -> list[dict]:
    conn = connect(app.config["DATABASE_PATH"])
    try:
        rows = conn.execute("SELECT type, data FROM events WHERE run_id = ?", (run_id,)).fetchall()
    finally:
        conn.close()
    return [{"type": r["type"], "data": json.loads(r["data"])} for r in rows]


def status_of(app, run_id: str) -> str:
    conn = connect(app.config["DATABASE_PATH"])
    try:
        return conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()[0]
    finally:
        conn.close()


def make_run_with_workspace(tracer, settings, toy_repo, status: str) -> str:
    run_id = tracer.create_run("t")
    create_workspace(toy_repo, Path(settings.workspaces_dir), run_id)
    tracer.set_run_status(run_id, status)
    return run_id


# --- no_changes -----------------------------------------------------------


@needs_npx
def test_nothing_to_change_is_its_own_outcome_not_an_error(app, tracer, settings):
    spec = {
        "summary": "already there",
        "changes": [{"path": "app.py", "action": "modify", "description": "none needed"}],
        "acceptance_criteria": ["endpoint exists"],
    }

    def script(name, system, messages, tools):
        if name.startswith("architect"):
            return _tool_turn("s", "submit_design_spec", spec)
        return _text_turn("The endpoint and its test already exist; no change is needed.")

    run_id = tracer.create_run("Add something that already exists")
    tracer.claim_next_run()
    Orchestrator(tracer, FakeLLM(tracer, script=script), settings).execute(run_id, "task")

    assert status_of(app, run_id) == "no_changes"
    evs = events(app, run_id)
    final = [e for e in evs if e["type"] == "run.status"][-1]["data"]
    assert "already exist" in final["reason"]
    assert any(e["type"] == "state.transition" and e["data"]["to"] == "no_changes" for e in evs)
    assert not any(
        e["type"] == "artifact.created" and e["data"]["artifact"] == "patch" for e in evs
    )
    # The implementation span did not crash: nothing in the trace is marked as an error.
    conn = connect(app.config["DATABASE_PATH"])
    try:
        assert conn.execute("SELECT count(*) FROM spans WHERE status = 'error'").fetchone()[0] == 0
    finally:
        conn.close()


def test_no_changes_is_terminal_for_the_stream(app, tracer):
    from agentteam.api import stream_events

    run_id = tracer.create_run("t")
    tracer.set_run_status(run_id, "no_changes", reason="x")
    frames = list(stream_events(app.config["DATABASE_PATH"], run_id, 0, poll_interval=0.01))
    assert frames[-1].startswith("event: end")


# --- cleanup --------------------------------------------------------------


def test_cleanup_workspace_is_idempotent(settings, toy_repo):
    ws = create_workspace(toy_repo, Path(settings.workspaces_dir), "abc123def456xyz")
    assert ws.path.exists() and "agent/abc123def456" in branches(toy_repo)

    first = cleanup_workspace(toy_repo, Path(settings.workspaces_dir), "abc123def456xyz")
    assert first == {"worktree": True, "branch": True}
    assert not ws.path.exists()
    assert branches(toy_repo).strip() == ""

    again = cleanup_workspace(toy_repo, Path(settings.workspaces_dir), "abc123def456xyz")
    assert again == {"worktree": False, "branch": False}


def test_rejected_run_is_cleaned_by_the_worker_sweep(app, tracer, settings, toy_repo):
    run_id = make_run_with_workspace(tracer, settings, toy_repo, "awaiting_approval")
    orch = Orchestrator(tracer, FakeLLM(tracer), settings)
    assert orch.cleanup_rejected() is False  # nothing rejected yet: leave it alone
    assert (Path(settings.workspaces_dir) / run_id[:12]).exists()

    assert tracer.decide_gate(run_id, "reject", "no") is True
    assert orch.cleanup_rejected() is True
    assert not (Path(settings.workspaces_dir) / run_id[:12]).exists()
    assert branches(toy_repo).strip() == ""
    assert any(e["type"] == "workspace.cleaned" for e in events(app, run_id))
    assert orch.cleanup_rejected() is False  # already cleaned: not repeated


def test_cleanup_command_only_touches_finished_unsuccessful_runs(app, tracer, settings, toy_repo):
    failed = make_run_with_workspace(tracer, settings, toy_repo, "failed")
    waiting = make_run_with_workspace(tracer, settings, toy_repo, "awaiting_approval")
    running = make_run_with_workspace(tracer, settings, toy_repo, "running")
    workspaces = Path(settings.workspaces_dir)

    dry = cleanup_runs(settings, tracer, ["failed", "error"], 0, dry_run=True)
    assert [r for r, _ in dry] == [failed]
    assert (workspaces / failed[:12]).exists()  # dry run changes nothing

    done = cleanup_runs(settings, tracer, ["failed", "error"], 0, dry_run=False)
    assert [r for r, _ in done] == [failed]
    assert not (workspaces / failed[:12]).exists()
    assert (workspaces / waiting[:12]).exists()  # active runs are never touched
    assert (workspaces / running[:12]).exists()


def test_cleanup_command_refuses_active_statuses(tracer, settings):
    with pytest.raises(ValueError, match="active"):
        cleanup_runs(settings, tracer, ["awaiting_approval"], 0, dry_run=False)
    with pytest.raises(ValueError, match="active"):
        cleanup_runs(settings, tracer, ["failed", "delivering"], 0, dry_run=False)


def test_cleanup_command_older_than_filter(tracer, settings, toy_repo):
    make_run_with_workspace(tracer, settings, toy_repo, "failed")
    assert cleanup_runs(settings, tracer, ["failed"], 24, dry_run=True) == []  # too recent
