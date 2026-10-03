"""HTTP API: create/list/inspect runs and stream their events over SSE."""

import json
import time
from collections.abc import Iterator
from typing import Any

from flask import Blueprint, Response, current_app, jsonify, request

from .db import connect
from .prompts import config_hash
from .tracing import Tracer

bp = Blueprint("api", __name__, url_prefix="/api")

TERMINAL_STATUSES = {"done", "error", "stopped"}
MAX_TASK_CHARS = 5000


def _tracer() -> Tracer:
    return current_app.extensions["tracer"]


def _db_path() -> str:
    return current_app.config["DATABASE_PATH"]


def _loads(value: str | None) -> Any:
    return json.loads(value) if value else None


@bp.post("/runs")
def create_run():
    body = request.get_json(silent=True) or {}
    task = body.get("task")
    if not isinstance(task, str) or not task.strip():
        return jsonify(error="'task' must be a non-empty string"), 400
    if len(task) > MAX_TASK_CHARS:
        return jsonify(error=f"'task' exceeds {MAX_TASK_CHARS} characters"), 400
    model = current_app.config.get("LLM_MODEL", "")
    run_id = _tracer().create_run(task.strip(), config_hash=config_hash(model))
    return jsonify(id=run_id, status="pending"), 202


@bp.get("/runs")
def list_runs():
    conn = connect(_db_path())
    try:
        rows = conn.execute(
            "SELECT id, task, status, config_hash, created_at, updated_at, total_cost_usd "
            "FROM runs ORDER BY created_at DESC LIMIT 100"
        ).fetchall()
    finally:
        conn.close()
    return jsonify([dict(r) for r in rows])


@bp.get("/runs/<run_id>")
def get_run(run_id: str):
    conn = connect(_db_path())
    try:
        run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if run is None:
            return jsonify(error="run not found"), 404
        spans = conn.execute(
            "SELECT * FROM spans WHERE run_id = ? ORDER BY started_at", (run_id,)
        ).fetchall()
        artifacts = conn.execute(
            "SELECT data FROM events WHERE run_id = ? AND type = 'artifact.created' ORDER BY id",
            (run_id,),
        ).fetchall()
    finally:
        conn.close()
    span_list = []
    for s in spans:
        d = dict(s)
        d["input"] = _loads(d["input"])
        d["output"] = _loads(d["output"])
        span_list.append(d)
    return jsonify(
        run=dict(run), spans=span_list, artifacts=[json.loads(a["data"]) for a in artifacts]
    )


def stream_events(
    db_path: str,
    run_id: str,
    after: int = 0,
    *,
    poll_interval: float = 0.25,
    heartbeat_interval: float = 15.0,
) -> Iterator[str]:
    """SSE frames for a run, starting after event id `after`. Ends once the run is terminal."""
    conn = connect(db_path)
    last_id = after
    last_sent = time.monotonic()
    try:
        while True:
            # Read status BEFORE events: a terminal status then guarantees all events
            # (committed in the same transaction as the status) are already visible.
            row = conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                yield 'event: error\ndata: {"error": "run not found"}\n\n'
                return
            events = conn.execute(
                "SELECT id, type, data FROM events WHERE run_id = ? AND id > ? "
                "ORDER BY id LIMIT 500",
                (run_id, last_id),
            ).fetchall()
            for e in events:
                last_id = e["id"]
                yield f"id: {e['id']}\nevent: {e['type']}\ndata: {e['data']}\n\n"
                last_sent = time.monotonic()
            if events:
                continue
            if row["status"] in TERMINAL_STATUSES:
                yield "event: end\ndata: {}\n\n"
                return
            if time.monotonic() - last_sent >= heartbeat_interval:
                yield ": heartbeat\n\n"
                last_sent = time.monotonic()
            time.sleep(poll_interval)
    finally:
        conn.close()


@bp.get("/runs/<run_id>/events")
def run_events(run_id: str):
    raw = request.headers.get("Last-Event-ID") or request.args.get("after") or "0"
    try:
        after = max(0, int(raw))
    except ValueError:
        return jsonify(error="invalid Last-Event-ID"), 400
    return Response(
        stream_events(_db_path(), run_id, after),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # stop nginx-style proxies from buffering the stream
        },
    )
