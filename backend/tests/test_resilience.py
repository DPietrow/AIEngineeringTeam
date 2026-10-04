import json
import random
import time
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from agentteam.agent_loop import run_agent_loop
from agentteam.app import create_app
from agentteam.config import Settings
from agentteam.db import connect, init_db
from agentteam.deadline import RunTimeout, check_deadline, remaining, run_deadline
from agentteam.llm import AnthropicLLM, FakeLLM
from agentteam.mcp_toolbox import Toolbox
from agentteam.orchestrator import Orchestrator
from agentteam.retry import backoff_delay, call_with_retries, is_retryable
from agentteam.tracing import set_tracer
from agentteam.worker import Heartbeat, work_once


@pytest.fixture
def app(settings):
    return create_app({"TESTING": True, "DATABASE_PATH": settings.database_path})


@pytest.fixture
def tracer(app):
    t = app.extensions["tracer"]
    set_tracer(t)
    return t


def api_error(status: int, retry_after: str | None = None) -> anthropic.APIStatusError:
    headers = {"retry-after": retry_after} if retry_after else {}
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status, request=request, headers=headers)
    return anthropic.APIStatusError("boom", response=response, body=None)


def events(app, run_id, type_=None):
    conn = connect(app.config["DATABASE_PATH"])
    try:
        rows = conn.execute("SELECT type, data FROM events WHERE run_id = ?", (run_id,)).fetchall()
    finally:
        conn.close()
    out = [{"type": r["type"], "data": json.loads(r["data"])} for r in rows]
    return [e for e in out if type_ is None or e["type"] == type_]


def row(app, run_id):
    conn = connect(app.config["DATABASE_PATH"])
    try:
        return dict(conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone())
    finally:
        conn.close()


# --- classification and backoff ---------------------------------------------


@pytest.mark.parametrize("status", [408, 409, 429, 500, 502, 503, 529])
def test_transient_statuses_are_retryable(status):
    assert is_retryable(api_error(status))


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_client_errors_are_not_retryable(status):
    assert not is_retryable(api_error(status))


def test_connection_errors_and_timeouts_are_retryable():
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    assert is_retryable(anthropic.APIConnectionError(request=request))
    assert is_retryable(anthropic.APITimeoutError(request=request))
    assert not is_retryable(ValueError("bug in our code"))


def test_backoff_grows_is_capped_and_honours_retry_after():
    rng = random.Random(1)
    exc = api_error(429)
    ceilings = [max(backoff_delay(n, 1.0, 30.0, exc, rng) for _ in range(200)) for n in (1, 2, 3)]
    assert ceilings[0] <= 1.0 < ceilings[1] <= 2.0 < ceilings[2] <= 4.0
    assert backoff_delay(20, 1.0, 30.0, exc, rng) <= 30.0  # capped
    assert backoff_delay(1, 1.0, 30.0, api_error(429, "7"), rng) == 7.0  # server hint wins
    assert backoff_delay(1, 1.0, 30.0, api_error(429, "9999"), rng) == 60.0  # but bounded


# --- call_with_retries -------------------------------------------------------


def flaky(failures: list[Exception], result="ok"):
    state = {"calls": 0}

    def fn():
        state["calls"] += 1
        if state["calls"] <= len(failures):
            raise failures[state["calls"] - 1]
        return result

    return fn, state


def test_retries_then_succeeds_and_records_each_retry(app, tracer):
    run_id = tracer.create_run("t")
    fn, state = flaky([api_error(529), api_error(503)])
    sleeps: list[float] = []
    with tracer.run(run_id):
        out = call_with_retries(fn, tracer=tracer, sleep=sleeps.append, rng=random.Random(0))
    assert out == "ok" and state["calls"] == 3 and len(sleeps) == 2
    retries = events(app, run_id, "llm.retry")
    assert [e["data"]["attempt"] for e in retries] == [1, 2]
    assert retries[0]["data"]["status_code"] == 529


def test_gives_up_after_max_retries_and_raises_the_original_error(tracer):
    fn, state = flaky([api_error(503)] * 10)
    sleeps: list[float] = []
    with pytest.raises(anthropic.APIStatusError):
        call_with_retries(fn, tracer=None, max_retries=2, sleep=sleeps.append)
    assert state["calls"] == 3 and len(sleeps) == 2  # first try + 2 retries


def test_non_retryable_errors_fail_immediately():
    fn, state = flaky([api_error(401)])
    sleeps: list[float] = []
    with pytest.raises(anthropic.APIStatusError):
        call_with_retries(fn, tracer=None, sleep=sleeps.append)
    assert state["calls"] == 1 and sleeps == []


def test_retry_sleep_never_runs_past_the_run_deadline():
    fn, _ = flaky([api_error(429, "30")])
    with run_deadline(5), pytest.raises(RunTimeout, match="retrying"):
        call_with_retries(fn, tracer=None, sleep=lambda s: None)


def test_anthropic_llm_retries_through_the_real_wrapper(tracer):
    calls = {"n": 0}

    def create(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise api_error(529)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="hello")],
            usage=SimpleNamespace(input_tokens=5, output_tokens=2),
            stop_reason="end_turn",
        )

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    llm = AnthropicLLM(tracer, client=client, sleep=lambda s: None)
    run_id = tracer.create_run("t")
    with tracer.run(run_id):
        turn = llm.converse(
            name="x.step1", system="s", messages=[{"role": "user", "content": "hi"}], tools=[]
        )
    assert turn.text == "hello" and calls["n"] == 2


# --- deadline ---------------------------------------------------------------


def test_deadline_lifecycle():
    assert remaining() is None
    check_deadline()  # no deadline set: never raises
    with run_deadline(60):
        assert 0 < remaining() <= 60
        check_deadline()
    assert remaining() is None
    with run_deadline(0), pytest.raises(RunTimeout):
        check_deadline()


def test_agent_loop_stops_between_steps_when_time_is_up(tracer):
    run_id = tracer.create_run("t")
    llm = FakeLLM(tracer, script=lambda *a: pytest.fail("no model call after the deadline"))
    import asyncio

    async def go():
        async with Toolbox(tracer, []) as toolbox:
            await run_agent_loop(
                llm=llm, tracer=tracer, name="x", system="s", user="u", toolbox=toolbox, max_steps=5
            )

    with tracer.run(run_id), run_deadline(0), pytest.raises(RunTimeout):
        asyncio.run(go())


def test_run_that_exceeds_its_time_limit_ends_timed_out(app, tracer, settings):
    import dataclasses

    tight = dataclasses.replace(settings, run_timeout_s=0)
    run_id = tracer.create_run("t")
    tracer.claim_next_run()
    Orchestrator(tracer, FakeLLM(tracer), tight).execute(run_id, "task")
    assert row(app, run_id)["status"] == "timed_out"
    final = events(app, run_id, "run.status")[-1]["data"]
    assert final["status"] == "timed_out" and "time limit" in final["reason"]


def test_timed_out_is_terminal_for_the_event_stream(app, tracer):
    from agentteam.api import stream_events

    run_id = tracer.create_run("t")
    tracer.set_run_status(run_id, "timed_out", reason="x")
    frames = list(stream_events(app.config["DATABASE_PATH"], run_id, 0, poll_interval=0.01))
    assert frames[-1].startswith("event: end")


# --- leases and crash recovery -----------------------------------------------


def test_claim_with_a_worker_takes_a_lease_and_renewal_extends_it(app, tracer):
    run_id = tracer.create_run("t")
    tracer.claim_next_run("w1", lease_s=10)
    first = row(app, run_id)
    assert first["claimed_by"] == "w1" and first["lease_expires_at"]
    time.sleep(0.01)
    assert tracer.renew_lease(run_id, "w1", 100) is True
    assert row(app, run_id)["lease_expires_at"] > first["lease_expires_at"]
    assert tracer.renew_lease(run_id, "someone-else", 100) is False


def test_dead_workers_running_run_is_marked_error_and_open_spans_closed(app, tracer):
    run_id = tracer.create_run("t")
    tracer.claim_next_run("w1", lease_s=-1)  # lease already expired: the worker "died"
    with tracer.run(run_id):
        cm = tracer.span("agent-work")
        cm.__enter__()  # span is opened and never closed, as if the process was killed

    recovered = tracer.recover_orphans()
    assert recovered == [{"run_id": run_id, "was": "running", "now": "error"}]
    r = row(app, run_id)
    assert r["status"] == "error" and r["claimed_by"] is None and r["lease_expires_at"] is None
    assert events(app, run_id, "run.recovered")[0]["data"]["action"] == "marked_error"
    conn = connect(app.config["DATABASE_PATH"])
    try:
        span = conn.execute(
            "SELECT status, error FROM spans WHERE run_id = ?", (run_id,)
        ).fetchone()
    finally:
        conn.close()
    assert span["status"] == "error" and "worker lost" in span["error"]
    assert tracer.recover_orphans() == []  # recovering twice does nothing


def test_dead_workers_delivery_is_requeued_for_another_try(app, tracer):
    run_id = tracer.create_run("t")
    tracer.set_run_status(run_id, "awaiting_approval")
    assert tracer.decide_gate(run_id, "approve")
    assert tracer.claim_next_delivery("w1", lease_s=-1) == run_id
    assert tracer.recover_orphans() == [{"run_id": run_id, "was": "delivering", "now": "approved"}]
    assert row(app, run_id)["status"] == "approved"
    assert tracer.claim_next_delivery("w2", lease_s=60) == run_id  # picked up again


def test_live_and_leaseless_runs_are_never_recovered(app, tracer):
    healthy = tracer.create_run("healthy")
    tracer.claim_next_run("w1", lease_s=3600)
    leaseless = tracer.create_run("eval-style")
    tracer.set_run_status(leaseless, "running")  # how the eval harness marks its runs
    assert tracer.recover_orphans() == []
    assert row(app, healthy)["status"] == "running"
    assert row(app, leaseless)["status"] == "running"


def test_heartbeat_keeps_a_slow_run_alive(app, tracer):
    run_id = tracer.create_run("t")
    tracer.claim_next_run("w1", lease_s=0.3)
    with Heartbeat(tracer, run_id, "w1", 0.3):
        time.sleep(0.8)  # well past one lease, but the heartbeat renews it every 0.1 s
        assert tracer.recover_orphans() == []
    assert row(app, run_id)["status"] == "running"


def test_work_once_with_a_worker_id_runs_a_leased_run_to_completion(app, tracer, settings):
    run_id = tracer.create_run("t")
    broken = Settings(**{**settings.__dict__, "toy_repo_path": None})  # run ends 'error' fast
    assert work_once(tracer, Orchestrator(tracer, FakeLLM(tracer), broken), "w1", 60) is True
    assert row(app, run_id)["status"] == "error"


def test_existing_databases_gain_the_lease_columns(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE runs (id TEXT PRIMARY KEY, task TEXT NOT NULL, status TEXT NOT NULL "
        "DEFAULT 'pending', config_hash TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
        "total_cost_usd REAL NOT NULL DEFAULT 0)"
    )
    conn.commit()
    conn.close()
    init_db(path)
    conn = connect(path)
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(runs)")}
    finally:
        conn.close()
    assert {"claimed_by", "lease_expires_at"} <= cols
