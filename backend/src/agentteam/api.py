"""HTTP API: create/list/inspect runs and stream their events over SSE."""

import json
import logging
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from flask import Blueprint, Response, current_app, jsonify, request

from .auth import issue_token, password_matches
from .db import connect
from .prompts import config_hash
from .tracing import Tracer

bp = Blueprint("api", __name__, url_prefix="/api")
auth_log = logging.getLogger("agentteam.auth")

# done: finished (PR opened when delivery is on). failed: a retry cap was hit. error: crashed.
# stopped: spend cap. rejected: a human declined at the approval gate. no_changes: the agents
# found nothing to change (request already satisfied). Non-terminal gate statuses:
# awaiting_approval -> approved -> delivering.
# timed_out: exceeded the run's wall-clock limit.
TERMINAL_STATUSES = {"done", "failed", "error", "stopped", "rejected", "no_changes", "timed_out"}
MAX_TASK_CHARS = 5000


def _tracer() -> Tracer:
    return current_app.extensions["tracer"]


def _db_path() -> str:
    return current_app.config["DATABASE_PATH"]


def _loads(value: str | None) -> Any:
    return json.loads(value) if value else None


def _client_key() -> str:
    return request.remote_addr or "unknown"


def _too_many(retry_after: float):
    resp = jsonify(error="too many requests, slow down", retry_after_s=round(retry_after, 1))
    resp.status_code = 429
    resp.headers["Retry-After"] = str(max(1, int(retry_after + 0.999)))
    return resp


def _limited(name: str):
    """Returns a 429 response if the named limiter is exhausted, else records a hit."""
    limiter = current_app.extensions["limiters"][name]
    wait = limiter.retry_after("global")
    if wait > 0:
        return _too_many(wait)
    limiter.hit("global")
    return None


@bp.get("/auth/status")
def auth_status():
    """Public: lets the dashboard decide whether to show the login screen."""
    return jsonify(auth_required=bool(current_app.config.get("API_PASSWORD")))


ACTIVE_STATUSES = ("pending", "running", "approved", "delivering")


def activity_file(db_path: str) -> Path:
    return Path(db_path).parent / "last_activity"


def touch_activity(db_path: str) -> None:
    """Records 'someone used the API just now' (a file, so all API processes share it)."""
    try:
        p = activity_file(db_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
    except OSError:
        pass  # idle tracking must never break a request


def idle_report(db_path: str, now: datetime | None = None) -> dict[str, Any]:
    """What the spin-down tooling needs: is anything running, and how long has it been quiet?"""
    now = now or datetime.now(UTC)
    conn = connect(db_path)
    try:
        marks = ",".join("?" for _ in ACTIVE_STATUSES)
        active = conn.execute(
            f"SELECT COUNT(*) AS n FROM runs WHERE status IN ({marks})", ACTIVE_STATUSES
        ).fetchone()["n"]
        waiting = conn.execute(
            "SELECT COUNT(*) AS n FROM runs WHERE status = 'awaiting_approval'"
        ).fetchone()["n"]
        newest = conn.execute("SELECT MAX(updated_at) AS t FROM runs").fetchone()["t"]
    finally:
        conn.close()
    last: datetime | None = None
    if newest:
        last = datetime.fromisoformat(newest)
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
    try:
        touched = datetime.fromtimestamp(activity_file(db_path).stat().st_mtime, UTC)
        last = touched if last is None or touched > last else last
    except OSError:
        pass
    idle_minutes = None if last is None else max(0.0, (now - last).total_seconds() / 60)
    return {
        "active_runs": active,
        "awaiting_approval": waiting,
        "idle_minutes": None if idle_minutes is None else round(idle_minutes, 1),
    }


@bp.get("/idle")
def idle():
    """Authenticated. Used by deploy/do_cli.py and the idle-shutdown workflow."""
    return jsonify(idle_report(_db_path()))


@bp.post("/login")
def login():
    password = current_app.config.get("API_PASSWORD")
    if not password:
        return jsonify(error="authentication is not enabled on this server"), 404
    limiter = current_app.extensions["limiters"]["login"]
    wait = limiter.retry_after(_client_key())
    if wait > 0:  # checked BEFORE the password, so a locked-out client cannot keep guessing
        auth_log.warning("login locked out for %s", _client_key())
        return _too_many(wait)
    body = request.get_json(silent=True) or {}
    supplied = body.get("password")
    if not isinstance(supplied, str) or not password_matches(supplied, password):
        limiter.hit(_client_key())
        auth_log.warning("failed login from %s", _client_key())
        return jsonify(error="wrong password"), 401
    limiter.reset(_client_key())
    token, expires_at = issue_token(
        current_app.config["JWT_SECRET"], int(current_app.config["JWT_TTL_S"])
    )
    auth_log.info("login from %s", _client_key())
    return jsonify(token=token, expires_at=expires_at)


@bp.post("/runs")
def create_run():
    if (limited := _limited("create")) is not None:
        return limited
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


def _decide(run_id: str, decision: str):
    if (limited := _limited("decide")) is not None:
        return limited
    body = request.get_json(silent=True) or {}
    reason = body.get("reason", "")
    if not isinstance(reason, str) or len(reason) > 1000:
        return jsonify(error="'reason' must be a string of at most 1000 characters"), 400
    if decision == "approve" and not current_app.config.get("DELIVERY_ENABLED"):
        return jsonify(error="delivery is not configured (GITHUB_TOKEN / GITHUB_REPO)"), 409
    if not _tracer().decide_gate(run_id, decision, reason):
        return jsonify(error="run is not awaiting approval"), 409
    return jsonify(id=run_id, status="approved" if decision == "approve" else "rejected"), 202


@bp.post("/runs/<run_id>/approve")
def approve_run(run_id: str):
    return _decide(run_id, "approve")


@bp.post("/runs/<run_id>/reject")
def reject_run(run_id: str):
    return _decide(run_id, "reject")


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
