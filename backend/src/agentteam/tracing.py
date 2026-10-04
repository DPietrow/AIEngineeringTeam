"""Tracing core: every LLM call, MCP call and agent step is a span.

A span records full input/output, timing, tokens and cost, the call-site file:line,
and a full traceback on errors. Span start/end are also appended to the events table
(the SSE source). All payloads are redacted before they touch SQLite.
"""

from __future__ import annotations

import contextvars
import functools
import inspect
import json
import os
import sys
import time
import traceback as _tb
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .budget import SpendCapExceeded, SpendGuard
from .db import connect, init_db, write_tx
from .pricing import compute_cost
from .redact import redact

MAX_FIELD_CHARS = 1_000_000

_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("run_id", default=None)
_span_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("span_id", default=None)


class NoActiveRun(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _lease(worker_id: str | None, lease_s: float) -> str | None:
    """Lease expiry timestamp, or None when there is no worker (no lease, never recovered)."""
    if worker_id is None:
        return None
    return (datetime.now(UTC) + timedelta(seconds=lease_s)).isoformat()


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=repr)


def to_jsonable(obj: Any, _depth: int = 0) -> Any:
    if _depth > 20:
        return repr(obj)
    if obj is None or isinstance(obj, bool | int | float | str):
        return obj
    if isinstance(obj, BaseModel):
        return to_jsonable(obj.model_dump(mode="json"), _depth + 1)
    if is_dataclass(obj) and not isinstance(obj, type):
        return to_jsonable(asdict(obj), _depth + 1)
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v, _depth + 1) for k, v in obj.items()}
    if isinstance(obj, list | tuple | set | frozenset):
        return [to_jsonable(v, _depth + 1) for v in obj]
    if isinstance(obj, bytes | bytearray):
        return f"<{len(obj)} bytes>"
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, BaseException):
        return f"{type(obj).__name__}: {obj}"
    return repr(obj)


def _truncate(obj: Any) -> Any:
    if isinstance(obj, str):
        if len(obj) > MAX_FIELD_CHARS:
            return obj[:MAX_FIELD_CHARS] + f"...[truncated {len(obj) - MAX_FIELD_CHARS} chars]"
        return obj
    if isinstance(obj, dict):
        return {k: _truncate(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_truncate(v) for v in obj]
    return obj


def clean(obj: Any) -> Any:
    """JSON-safe, redacted, size-capped copy of any value."""
    return _truncate(redact(to_jsonable(obj)))


def _callsite(stacklevel: int = 0) -> tuple[str, int]:
    """First frame outside this module and contextlib: where the traced code was called.

    stacklevel skips that many additional frames (for helpers that open spans on behalf
    of their caller, e.g. the LLM client).
    """
    me = os.path.normcase(__file__)
    f = sys._getframe(1)
    while f is not None:
        name = os.path.normcase(f.f_code.co_filename)
        if name != me and not name.endswith("contextlib.py"):
            if stacklevel > 0:
                stacklevel -= 1
                f = f.f_back
                continue
            try:
                rel = os.path.relpath(f.f_code.co_filename)
            except ValueError:  # different drive on Windows
                rel = f.f_code.co_filename
            return rel.replace("\\", "/"), f.f_lineno
        f = f.f_back
    return "<unknown>", 0


class SpanHandle:
    def __init__(self, span_id: str) -> None:
        self.id = span_id
        self.output: Any = None
        self.model: str | None = None
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None
        self.cache_read_tokens: int | None = None
        self.cache_write_tokens: int | None = None
        self.cost_usd: float = 0.0

    def set_output(self, value: Any) -> None:
        self.output = value

    def set_usage(
        self,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        cost_usd: float | None = None,
    ) -> None:
        """`input_tokens` is UNCACHED input only; cache reads/writes are tracked separately
        because they are priced differently (see pricing.py)."""
        self.model = model
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_tokens = cache_read_tokens
        self.cache_write_tokens = cache_write_tokens
        self.cost_usd = (
            cost_usd
            if cost_usd is not None
            else compute_cost(
                model, input_tokens, output_tokens, cache_write_tokens, cache_read_tokens
            )
        )


class Tracer:
    def __init__(self, db_path: str | Path, guard: SpendGuard | None = None) -> None:
        self.db_path = db_path
        self.guard = guard
        init_db(db_path)

    # --- runs -----------------------------------------------------------------

    def create_run(self, task: str, config_hash: str | None = None) -> str:
        run_id = uuid.uuid4().hex
        now = _now()
        with write_tx(self.db_path) as conn:
            conn.execute(
                "INSERT INTO runs (id, task, status, config_hash, created_at, updated_at) "
                "VALUES (?, ?, 'pending', ?, ?, ?)",
                (run_id, task, config_hash, now, now),
            )
            self._insert_event(
                conn, run_id, None, "run.created", {"task": task, "config_hash": config_hash}
            )
        return run_id

    def claim_next_run(
        self, worker_id: str | None = None, lease_s: float = 60.0
    ) -> tuple[str, str] | None:
        """Atomically move the oldest pending run to 'running'. Returns (run_id, task).

        With a worker_id the claim also takes a lease (see renew_lease / recover_orphans).
        Without one (tests, the eval harness) the run has no lease and is never recovered.
        """
        with write_tx(self.db_path) as conn:
            row = conn.execute(
                "SELECT id, task FROM runs WHERE status = 'pending' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE runs SET status = 'running', updated_at = ?, claimed_by = ?, "
                "lease_expires_at = ? WHERE id = ?",
                (_now(), worker_id, _lease(worker_id, lease_s), row["id"]),
            )
            self._insert_event(conn, row["id"], None, "run.status", {"status": "running"})
            return row["id"], row["task"]

    def renew_lease(self, run_id: str, worker_id: str, lease_s: float) -> bool:
        """Heartbeat: extend this worker's lease. False if the run is no longer ours (it was
        recovered after a long stall), so the caller knows it lost the run."""
        with write_tx(self.db_path) as conn:
            cur = conn.execute(
                "UPDATE runs SET lease_expires_at = ? "
                "WHERE id = ? AND claimed_by = ? AND status IN ('running', 'delivering')",
                (_lease(worker_id, lease_s), run_id, worker_id),
            )
            return cur.rowcount == 1

    def recover_orphans(self) -> list[dict[str, str]]:
        """Find runs whose worker died (lease expired) and put them in a sane state.

        - 'running'    -> 'error'. Re-running would repeat the spend on a half-built workspace,
                          so a human resubmits instead.
        - 'delivering' -> 'approved'. The human already approved; pushing is idempotent, so the
                          worker simply tries delivery again (a duplicate-PR rejection from
                          GitHub would surface as a clear error).
        Spans left 'running' by the dead worker are closed as errors, so the trace does not show
        work that is still in progress when nothing is. Runs without a lease are never touched.
        """
        recovered: list[dict[str, str]] = []
        now = _now()
        with write_tx(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, status, claimed_by FROM runs "
                "WHERE status IN ('running', 'delivering') "
                "AND lease_expires_at IS NOT NULL AND lease_expires_at < ?",
                (now,),
            ).fetchall()
            for row in rows:
                run_id, was = row["id"], row["status"]
                new_status = "error" if was == "running" else "approved"
                reason = f"worker {row['claimed_by']} stopped responding while the run was {was}"
                conn.execute(
                    "UPDATE spans SET status = 'error', ended_at = ?, error = ? "
                    "WHERE run_id = ? AND status = 'running'",
                    (now, "worker lost: " + reason, run_id),
                )
                conn.execute(
                    "UPDATE runs SET status = ?, updated_at = ?, claimed_by = NULL, "
                    "lease_expires_at = NULL WHERE id = ?",
                    (new_status, now, run_id),
                )
                action = "marked_error" if was == "running" else "requeued_delivery"
                self._insert_event(
                    conn,
                    run_id,
                    None,
                    "run.recovered",
                    {"was": was, "now": new_status, "action": action, "reason": reason},
                )
                status_data: dict[str, Any] = {"status": new_status}
                status_data["error" if new_status == "error" else "reason"] = reason
                self._insert_event(conn, run_id, None, "run.status", status_data)
                recovered.append({"run_id": run_id, "was": was, "now": new_status})
        return recovered

    def decide_gate(self, run_id: str, decision: str, reason: str = "") -> bool:
        """Record the human decision on a run waiting at the approval gate.

        Only a run in 'awaiting_approval' can be decided, and only once: the conditional update
        makes double-clicks and races harmless. Returns False if the run was not waiting.
        approve -> 'approved' (the worker then delivers it); reject -> 'rejected' (terminal).
        """
        if decision not in ("approve", "reject"):
            raise ValueError(f"unknown decision {decision!r}")
        new_status = "approved" if decision == "approve" else "rejected"
        with write_tx(self.db_path) as conn:
            cur = conn.execute(
                "UPDATE runs SET status = ?, updated_at = ? "
                "WHERE id = ? AND status = 'awaiting_approval'",
                (new_status, _now(), run_id),
            )
            if cur.rowcount != 1:
                return False
            self._insert_event(
                conn, run_id, None, "gate.decision", {"decision": decision, "reason": reason}
            )
            self._insert_event(conn, run_id, None, "run.status", {"status": new_status})
            return True

    def claim_next_delivery(
        self, worker_id: str | None = None, lease_s: float = 60.0
    ) -> str | None:
        """Atomically move the oldest human-approved run to 'delivering'. Returns its id."""
        with write_tx(self.db_path) as conn:
            row = conn.execute(
                "SELECT id FROM runs WHERE status = 'approved' ORDER BY updated_at LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE runs SET status = 'delivering', updated_at = ?, claimed_by = ?, "
                "lease_expires_at = ? WHERE id = ?",
                (_now(), worker_id, _lease(worker_id, lease_s), row["id"]),
            )
            self._insert_event(conn, row["id"], None, "run.status", {"status": "delivering"})
            return str(row["id"])

    def rejected_runs_to_clean(self) -> list[str]:
        """Rejected runs whose workspace has not been cleaned up yet."""
        conn = connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT id FROM runs WHERE status = 'rejected' AND NOT EXISTS ("
                "SELECT 1 FROM events e WHERE e.run_id = runs.id "
                "AND e.type IN ('workspace.cleaned', 'workspace.cleanup_failed'))"
            ).fetchall()
        finally:
            conn.close()
        return [str(r["id"]) for r in rows]

    def runs_with_status(self, statuses: list[str], older_than_hours: float) -> list[str]:
        """Ids of runs in one of `statuses` that were last updated more than N hours ago."""
        cutoff = (datetime.now(UTC) - timedelta(hours=older_than_hours)).isoformat()
        marks = ",".join("?" for _ in statuses)
        conn = connect(self.db_path)
        try:
            rows = conn.execute(
                f"SELECT id FROM runs WHERE status IN ({marks}) AND updated_at < ? "
                "ORDER BY created_at",
                (*statuses, cutoff),
            ).fetchall()
        finally:
            conn.close()
        return [str(r["id"]) for r in rows]

    def run_task(self, run_id: str) -> str | None:
        conn = connect(self.db_path)
        try:
            row = conn.execute("SELECT task FROM runs WHERE id = ?", (run_id,)).fetchone()
        finally:
            conn.close()
        return None if row is None else str(row["task"])

    def load_artifacts(self, run_id: str) -> dict[str, dict[str, Any]]:
        """Latest artifact of each kind for a run, as plain dicts (artifact name -> data)."""
        conn = connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT data FROM events WHERE run_id = ? AND type = 'artifact.created' "
                "ORDER BY id",
                (run_id,),
            ).fetchall()
        finally:
            conn.close()
        latest: dict[str, dict[str, Any]] = {}
        for row in rows:
            payload = json.loads(row["data"])
            latest[payload["artifact"]] = payload["data"]
        return latest

    def set_run_status(self, run_id: str, status: str, **extra: Any) -> None:
        with write_tx(self.db_path) as conn:
            conn.execute(
                "UPDATE runs SET status = ?, updated_at = ? WHERE id = ?", (status, _now(), run_id)
            )
            self._insert_event(conn, run_id, None, "run.status", {"status": status, **extra})

    @contextmanager
    def run(self, run_id: str) -> Iterator[str]:
        token = _run_id.set(run_id)
        try:
            yield run_id
        finally:
            _run_id.reset(token)

    # --- events ---------------------------------------------------------------

    def _insert_event(
        self, conn: Any, run_id: str, span_id: str | None, type_: str, data: dict[str, Any]
    ) -> int:
        cur = conn.execute(
            "INSERT INTO events (run_id, span_id, type, ts, data) VALUES (?, ?, ?, ?, ?)",
            (run_id, span_id, type_, _now(), _dumps(clean(data))),
        )
        return int(cur.lastrowid)

    def emit(
        self,
        type_: str,
        data: dict[str, Any] | None = None,
        *,
        run_id: str | None = None,
        span_id: str | None = None,
    ) -> int:
        """Append a custom event (state transition, retry, budget, ...)."""
        run_id = run_id or _run_id.get()
        if run_id is None:
            raise NoActiveRun("emit() called outside a run context")
        span_id = span_id or _span_id.get()
        with write_tx(self.db_path) as conn:
            return self._insert_event(conn, run_id, span_id, type_, data or {})

    # --- spans ----------------------------------------------------------------

    @contextmanager
    def span(
        self, name: str, *, kind: str = "step", input: Any = None, stacklevel: int = 0
    ) -> Iterator[SpanHandle]:
        run_id = _run_id.get()
        if run_id is None:
            raise NoActiveRun(f"span {name!r} started outside a run context (use tracer.run)")
        parent_id = _span_id.get()

        if kind == "llm" and self.guard is not None:
            try:
                self.guard.check(run_id)
            except SpendCapExceeded as exc:
                self.emit("budget.exceeded", {"message": str(exc)}, run_id=run_id)
                raise

        callsite_file, callsite_line = _callsite(stacklevel)
        span_id = uuid.uuid4().hex
        started_at = _now()
        t0 = time.perf_counter()
        clean_input = clean(input)

        with write_tx(self.db_path) as conn:
            conn.execute(
                "INSERT INTO spans (id, run_id, parent_id, name, kind, status, started_at, input, "
                "callsite_file, callsite_line) VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?, ?)",
                (
                    span_id,
                    run_id,
                    parent_id,
                    name,
                    kind,
                    started_at,
                    _dumps(clean_input),
                    callsite_file,
                    callsite_line,
                ),
            )
            self._insert_event(
                conn,
                run_id,
                span_id,
                "span.start",
                {
                    "name": name,
                    "kind": kind,
                    "parent_id": parent_id,
                    "input": clean_input,
                    "callsite": {"file": callsite_file, "line": callsite_line},
                },
            )

        handle = SpanHandle(span_id)
        token = _span_id.set(span_id)
        status, error, trace = "ok", None, None
        try:
            yield handle
        except BaseException as exc:
            status = "error"
            error = f"{type(exc).__name__}: {exc}"
            trace = "".join(_tb.format_exception(exc))
            raise
        finally:
            _span_id.reset(token)
            self._finish(run_id, handle, status, error, trace, t0)

    def _finish(
        self,
        run_id: str,
        handle: SpanHandle,
        status: str,
        error: str | None,
        trace: str | None,
        t0: float,
    ) -> None:
        duration_ms = (time.perf_counter() - t0) * 1000
        output = clean(handle.output)
        error = clean(error)
        trace = clean(trace)
        usage = {
            "model": handle.model,
            "input_tokens": handle.input_tokens,
            "output_tokens": handle.output_tokens,
            "cache_read_tokens": handle.cache_read_tokens,
            "cache_write_tokens": handle.cache_write_tokens,
            "cost_usd": handle.cost_usd,
        }
        with write_tx(self.db_path) as conn:
            conn.execute(
                "UPDATE spans SET status = ?, ended_at = ?, duration_ms = ?, output = ?, "
                "error = ?, traceback = ?, model = ?, input_tokens = ?, output_tokens = ?, "
                "cache_read_tokens = ?, cache_write_tokens = ?, cost_usd = ? WHERE id = ?",
                (
                    status,
                    _now(),
                    duration_ms,
                    _dumps(output),
                    error,
                    trace,
                    handle.model,
                    handle.input_tokens,
                    handle.output_tokens,
                    handle.cache_read_tokens,
                    handle.cache_write_tokens,
                    handle.cost_usd,
                    handle.id,
                ),
            )
            if handle.cost_usd:
                conn.execute(
                    "UPDATE runs SET total_cost_usd = total_cost_usd + ?, updated_at = ? "
                    "WHERE id = ?",
                    (handle.cost_usd, _now(), run_id),
                )
            self._insert_event(
                conn,
                run_id,
                handle.id,
                "span.end",
                {
                    "status": status,
                    "duration_ms": duration_ms,
                    "output": output,
                    "error": error,
                    "traceback": trace,
                    "usage": usage,
                },
            )


# --- module-level convenience -------------------------------------------------

_tracer: Tracer | None = None


def set_tracer(tracer: Tracer) -> None:
    global _tracer
    _tracer = tracer


def get_tracer() -> Tracer:
    if _tracer is None:
        raise RuntimeError("Tracer not configured; call set_tracer(Tracer(...)) at startup")
    return _tracer


def traced(
    name: str | None = None,
    *,
    kind: str = "step",
    capture_input: bool = True,
    capture_output: bool = True,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator: wrap a sync or async function in a span."""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        span_name = name or fn.__qualname__
        sig = inspect.signature(fn)

        def build_input(args: tuple, kwargs: dict) -> Any:
            if not capture_input:
                return None
            try:
                bound = sig.bind(*args, **kwargs)
                bound.apply_defaults()
                return {k: v for k, v in bound.arguments.items() if k not in ("self", "cls")}
            except TypeError:
                return {"args": list(args), "kwargs": kwargs}

        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def awrapper(*args: Any, **kwargs: Any) -> Any:
                with get_tracer().span(span_name, kind=kind, input=build_input(args, kwargs)) as s:
                    result = await fn(*args, **kwargs)
                    if capture_output:
                        s.set_output(result)
                    return result

            return awrapper

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with get_tracer().span(span_name, kind=kind, input=build_input(args, kwargs)) as s:
                result = fn(*args, **kwargs)
                if capture_output:
                    s.set_output(result)
                return result

        return wrapper

    return decorator
