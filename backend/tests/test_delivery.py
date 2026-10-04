import dataclasses
import json
import subprocess
import sys
from pathlib import Path

import pytest

from agentteam.agents.delivery import make_pr_guard
from agentteam.app import create_app
from agentteam.db import connect
from agentteam.llm import FakeLLM
from agentteam.mcp_toolbox import ToolNotAllowed
from agentteam.orchestrator import Orchestrator
from agentteam.tracing import set_tracer
from agentteam.worker import work_once
from agentteam.workspace import GitError, Workspace, push_branch

from .conftest import git, needs_npx

FAKE_SERVER = str(Path(__file__).parent / "fake_github_server.py")
TOKEN = "ghp_faketokenfaketokenfaketoken1234"


@pytest.fixture
def remote(tmp_path, toy_repo):
    bare = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(bare)], check=True, capture_output=True
    )
    git(toy_repo, "remote", "add", "origin", str(bare))
    return bare


@pytest.fixture
def gh_settings(settings, tmp_path, remote):
    return dataclasses.replace(
        settings,
        github_token=TOKEN,
        github_repo="octo/toy",
        github_mcp_command=sys.executable,
        github_mcp_args=(FAKE_SERVER, "--log", str(tmp_path / "gh.log")),
    )


@pytest.fixture
def app(settings):
    return create_app({"TESTING": True, "DATABASE_PATH": settings.database_path})


@pytest.fixture
def tracer(app):
    t = app.extensions["tracer"]
    set_tracer(t)
    return t


def status_of(app, run_id):
    conn = connect(app.config["DATABASE_PATH"])
    try:
        return conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()[0]
    finally:
        conn.close()


# --- guard ----------------------------------------------------------------


def test_guard_accepts_exact_values_and_rejects_everything_else():
    guard = make_pr_guard("octo", "toy", "agent/abc", "main")
    ok = {"owner": "octo", "repo": "toy", "head": "agent/abc", "base": "main", "title": "t"}
    guard("create_pull_request", ok)
    for key, bad in [
        ("owner", "evil"),
        ("repo", "other"),
        ("head", "agent/zzz"),
        ("base", "release"),
    ]:
        with pytest.raises(ToolNotAllowed):
            guard("create_pull_request", {**ok, key: bad})
    with pytest.raises(ToolNotAllowed):
        guard("delete_repository", ok)


# --- push -----------------------------------------------------------------


def test_push_refuses_non_agent_branch(tmp_path):
    ws = Workspace(repo=tmp_path, path=tmp_path, branch="main", base_commit="x")
    with pytest.raises(GitError, match="non-agent"):
        push_branch(ws, TOKEN)


# --- gate -----------------------------------------------------------------


def test_gate_decisions_are_single_use(tracer, app):
    rid = tracer.create_run("t")
    assert tracer.decide_gate(rid, "approve") is False  # not awaiting yet
    tracer.set_run_status(rid, "awaiting_approval")
    assert tracer.decide_gate(rid, "approve") is True
    assert status_of(app, rid) == "approved"
    assert tracer.decide_gate(rid, "reject") is False  # already decided
    assert tracer.claim_next_delivery() == rid
    assert tracer.claim_next_delivery() is None


def test_reject_is_terminal(tracer, app):
    rid = tracer.create_run("t")
    tracer.set_run_status(rid, "awaiting_approval")
    assert tracer.decide_gate(rid, "reject", "not what I wanted") is True
    assert status_of(app, rid) == "rejected"
    assert tracer.claim_next_delivery() is None


def test_api_gate_endpoints(app, tracer):
    client = app.test_client()
    rid = tracer.create_run("t")
    assert client.post(f"/api/runs/{rid}/reject").status_code == 409  # not awaiting
    tracer.set_run_status(rid, "awaiting_approval")
    assert client.post(f"/api/runs/{rid}/approve").status_code == 409  # delivery not configured
    assert client.post(f"/api/runs/{rid}/reject", json={"reason": "x" * 2000}).status_code == 400
    assert client.post(f"/api/runs/{rid}/reject", json={"reason": "no"}).status_code == 202
    assert status_of(app, rid) == "rejected"

    app.config["DELIVERY_ENABLED"] = True
    rid2 = tracer.create_run("t2")
    tracer.set_run_status(rid2, "awaiting_approval")
    assert client.post(f"/api/runs/{rid2}/approve").status_code == 202
    assert status_of(app, rid2) == "approved"


# --- end to end -----------------------------------------------------------


@needs_npx
def test_full_flow_gate_push_and_pr(app, tracer, gh_settings, remote, tmp_path):
    log = tmp_path / "gh.log"
    orch = Orchestrator(tracer, FakeLLM(tracer), gh_settings)
    client = app.test_client()
    run_id = client.post("/api/runs", json={"task": "Add a notes file"}).get_json()["id"]

    assert work_once(tracer, orch) is True
    assert status_of(app, run_id) == "awaiting_approval"
    assert not log.exists()  # nothing touched GitHub before a human decided
    branches = subprocess.run(
        ["git", "branch", "--list"], cwd=remote, capture_output=True, text=True
    ).stdout
    assert "agent/" not in branches  # and nothing was pushed

    app.config["DELIVERY_ENABLED"] = True
    assert client.post(f"/api/runs/{run_id}/approve").status_code == 202
    assert work_once(tracer, orch) is True
    assert work_once(tracer, orch) is False

    assert status_of(app, run_id) == "done"
    detail = client.get(f"/api/runs/{run_id}").get_json()
    pr = detail["artifacts"][-1]
    assert pr["artifact"] == "pull_request"
    assert pr["data"]["url"] == "https://github.com/octo/toy/pull/7"
    assert pr["data"]["number"] == 7
    assert pr["data"]["head_branch"] == f"agent/{run_id[:12]}"

    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert [c["tool"] for c in calls] == ["create_pull_request"]  # the dangerous tool never ran
    assert calls[0]["args"]["head"] == f"agent/{run_id[:12]}"

    pushed = subprocess.run(
        ["git", "branch", "--list"], cwd=remote, capture_output=True, text=True
    ).stdout
    assert f"agent/{run_id[:12]}" in pushed

    # Cleanup after delivery: the local worktree and branch are gone, the remote branch stays.
    assert not (Path(gh_settings.workspaces_dir) / run_id[:12]).exists()
    local = subprocess.run(
        ["git", "branch", "--list", "agent/*"],
        cwd=gh_settings.toy_repo_path,
        capture_output=True,
        text=True,
    ).stdout
    assert local.strip() == ""
    assert any(a["artifact"] == "pull_request" for a in detail["artifacts"])

    conn = connect(app.config["DATABASE_PATH"])
    try:
        everything = json.dumps(
            [dict(r) for r in conn.execute("SELECT data FROM events")]
            + [dict(r) for r in conn.execute("SELECT input, output FROM spans")]
        )
    finally:
        conn.close()
    assert TOKEN not in everything  # the token never reaches the trace
    names = {s["name"] for s in detail["spans"]}
    assert {"deliver", "git.push", "delivery", "mcp.github.create_pull_request"} <= names
    assert not any(s["name"] == "mcp.github.delete_repository" for s in detail["spans"])


@needs_npx
def test_delivery_off_ends_run_done(app, tracer, settings):
    orch = Orchestrator(tracer, FakeLLM(tracer), settings)  # no GitHub settings
    run_id = tracer.create_run("t")
    tracer.claim_next_run()
    orch.execute(run_id, "Add a notes file")
    assert status_of(app, run_id) == "done"
