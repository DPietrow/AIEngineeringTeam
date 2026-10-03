import asyncio
import json
import sqlite3

import pytest
from pydantic import BaseModel

from agentteam.budget import SpendCapExceeded
from agentteam.db import connect
from agentteam.tracing import NoActiveRun, traced


def rows(db_path, sql, *args):
    conn = connect(db_path)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def test_span_success_writes_start_and_end_events(tracer, db_path):
    run_id = tracer.create_run("task")
    with tracer.run(run_id), tracer.span("work", kind="step", input={"x": 1}) as s:
        s.set_output({"y": 2})

    events = rows(db_path, "SELECT * FROM events WHERE run_id = ? ORDER BY id", run_id)
    assert [e["type"] for e in events] == ["run.created", "span.start", "span.end"]
    end = json.loads(events[2]["data"])
    assert end["status"] == "ok"
    assert end["output"] == {"y": 2}
    assert end["duration_ms"] >= 0

    span = rows(db_path, "SELECT * FROM spans WHERE run_id = ?", run_id)[0]
    assert span["status"] == "ok"
    assert json.loads(span["input"]) == {"x": 1}


def test_nested_spans_record_parent(tracer, db_path):
    run_id = tracer.create_run("task")
    with tracer.run(run_id), tracer.span("outer"), tracer.span("inner"):
        pass
    by_name = {r["name"]: r for r in rows(db_path, "SELECT * FROM spans")}
    assert by_name["inner"]["parent_id"] == by_name["outer"]["id"]
    assert by_name["outer"]["parent_id"] is None


def test_error_records_traceback_and_reraises(tracer, db_path):
    run_id = tracer.create_run("task")
    with pytest.raises(ValueError), tracer.run(run_id), tracer.span("boom"):
        raise ValueError("bad thing")
    span = rows(db_path, "SELECT * FROM spans")[0]
    assert span["status"] == "error"
    assert span["error"] == "ValueError: bad thing"
    assert "Traceback" in span["traceback"]
    assert "test_tracing.py" in span["traceback"]


def test_decorator_captures_args_result_and_callsite(tracer, db_path):
    class Spec(BaseModel):
        n: int

    @traced(kind="agent")
    def add(a, b=2):
        return Spec(n=a + b)

    run_id = tracer.create_run("task")
    with tracer.run(run_id):
        add(1)

    span = rows(db_path, "SELECT * FROM spans")[0]
    assert json.loads(span["input"]) == {"a": 1, "b": 2}
    assert json.loads(span["output"]) == {"n": 3}
    assert span["kind"] == "agent"
    assert span["callsite_file"].endswith("test_tracing.py")
    assert span["callsite_line"] > 0


def test_async_decorator(tracer, db_path):
    @traced()
    async def double(x):
        return x * 2

    run_id = tracer.create_run("task")

    async def main():
        with tracer.run(run_id):
            return await double(4)

    assert asyncio.run(main()) == 8
    assert json.loads(rows(db_path, "SELECT output FROM spans")[0]["output"]) == 8


def test_secrets_never_reach_the_database(tracer, db_path):
    run_id = tracer.create_run("task")
    secret = "sk-ant-api03-SUPERSECRETVALUE123456"
    with (
        tracer.run(run_id),
        tracer.span("call", input={"api_key": secret, "prompt": f"use {secret}"}) as s,
    ):
        s.set_output("Authorization: Bearer abcdefghijklmnopqrstuvwxyz")

    conn = connect(db_path)
    try:
        dump = "\n".join(
            str(tuple(r)) for t in ("spans", "events") for r in conn.execute(f"SELECT * FROM {t}")
        )
    finally:
        conn.close()
    assert "SUPERSECRET" not in dump
    assert "abcdefghijklmnopqrstuvwxyz" not in dump
    assert "[REDACTED]" in dump


def test_events_are_append_only(tracer, db_path):
    tracer.create_run("task")
    conn = connect(db_path)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("UPDATE events SET type = 'x'")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM events")
    finally:
        conn.close()


def test_wal_mode_enabled(tracer, db_path):
    assert rows(db_path, "PRAGMA journal_mode")[0][0] == "wal"


def test_usage_and_cost_roll_up_to_run(tracer, db_path):
    run_id = tracer.create_run("task")
    with tracer.run(run_id), tracer.span("llm1", kind="llm") as s:
        s.set_usage(model="m", input_tokens=10, output_tokens=5, cost_usd=0.01)
    with tracer.run(run_id), tracer.span("llm2", kind="llm") as s:
        s.set_usage(model="m", input_tokens=10, output_tokens=5, cost_usd=0.02)
    total = rows(db_path, "SELECT total_cost_usd FROM runs WHERE id = ?", run_id)[0][0]
    assert total == pytest.approx(0.03)


def test_spend_cap_blocks_next_llm_call(tracer, db_path):
    run_id = tracer.create_run("task")
    with tracer.run(run_id):
        with tracer.span("big", kind="llm") as s:
            s.set_usage(model="m", input_tokens=1, output_tokens=1, cost_usd=0.06)
        with pytest.raises(SpendCapExceeded), tracer.span("next", kind="llm"):
            pass
    types = [r["type"] for r in rows(db_path, "SELECT type FROM events ORDER BY id")]
    assert "budget.exceeded" in types
    assert not rows(db_path, "SELECT 1 FROM spans WHERE name = 'next'")


def test_span_outside_run_raises(tracer):
    with pytest.raises(NoActiveRun), tracer.span("orphan"):
        pass
