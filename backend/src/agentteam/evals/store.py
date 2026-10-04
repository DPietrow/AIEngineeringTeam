"""Eval persistence. All eval SQL lives here, which keeps the Postgres cutover to one file
(plus db.py / tracing.py / budget.py)."""

import json
from pathlib import Path
from typing import Any

from ..db import connect, write_tx


def save_eval_run(db_path: str | Path, report: Any) -> None:
    with write_tx(db_path) as conn:
        conn.execute(
            "INSERT INTO eval_runs (id, label, suite, suite_hash, config_hash, model, trials, "
            "started_at, ended_at, total_cost_usd, truncated, summary) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                report.id,
                report.label,
                report.suite,
                report.suite_hash,
                report.config_hash,
                report.model,
                report.trials,
                report.started_at,
                report.ended_at,
                report.total_cost_usd,
                int(report.truncated),
                json.dumps(report.summary),
            ),
        )
        for r in report.results:
            conn.execute(
                "INSERT INTO eval_results (eval_run_id, case_id, kind, trial, run_id, passed, "
                "grades, metrics, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    report.id,
                    r.case_id,
                    r.kind,
                    r.trial,
                    r.run_id,
                    int(r.passed),
                    json.dumps([g.to_dict() for g in r.grades]),
                    json.dumps(r.metrics),
                    r.error,
                ),
            )


def load_eval_run(db_path: str | Path, ref: str) -> dict | None:
    """Find an eval run by id or by label (the most recent one with that label)."""
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM eval_runs WHERE id = ? OR label = ? ORDER BY started_at DESC LIMIT 1",
            (ref, ref),
        ).fetchone()
        if row is None:
            return None
        run = dict(row)
        run["summary"] = json.loads(run["summary"] or "{}")
        results = []
        for r in conn.execute(
            "SELECT * FROM eval_results WHERE eval_run_id = ? ORDER BY id", (run["id"],)
        ):
            d = dict(r)
            d["grades"] = json.loads(d["grades"])
            d["metrics"] = json.loads(d["metrics"])
            d["passed"] = bool(d["passed"])
            results.append(d)
        run["results"] = results
        return run
    finally:
        conn.close()


def list_eval_runs(db_path: str | Path, limit: int = 20) -> list[dict]:
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, label, suite, suite_hash, config_hash, model, trials, started_at, "
            "total_cost_usd, summary FROM eval_runs ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        d = dict(row)
        d["summary"] = json.loads(d["summary"] or "{}")
        out.append(d)
    return out


def run_metrics(db_path: str | Path, run_id: str) -> dict[str, Any]:
    """Everything the scorecard needs about one finished run, read back from the trace."""
    conn = connect(db_path)
    try:
        run = conn.execute(
            "SELECT status, total_cost_usd FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        agg = conn.execute(
            "SELECT COALESCE(SUM(input_tokens), 0) AS tin, COALESCE(SUM(output_tokens), 0) AS tout, "
            "COALESCE(SUM(cache_read_tokens), 0) AS cread, "
            "COALESCE(SUM(cache_write_tokens), 0) AS cwrite, "
            "SUM(CASE WHEN kind = 'llm' THEN 1 ELSE 0 END) AS llm_calls, "
            "SUM(CASE WHEN kind = 'mcp' THEN 1 ELSE 0 END) AS tool_calls, "
            "SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS error_spans "
            "FROM spans WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        root = conn.execute(
            "SELECT duration_ms, output FROM spans WHERE run_id = ? AND parent_id IS NULL",
            (run_id,),
        ).fetchone()
        status_events = conn.execute(
            "SELECT data FROM events WHERE run_id = ? AND type = 'run.status' ORDER BY id DESC",
            (run_id,),
        ).fetchall()
        patch_event = conn.execute(
            "SELECT data FROM events WHERE run_id = ? AND type = 'artifact.created' "
            'AND data LIKE \'%"artifact": "patch"%\' ORDER BY id DESC LIMIT 1',
            (run_id,),
        ).fetchone()
    finally:
        conn.close()

    outcome = json.loads(root["output"]) if root and root["output"] else {}
    if not isinstance(outcome, dict):  # e.g. a truncated payload is stored as a plain string
        outcome = {}
    last_status = json.loads(status_events[0]["data"]) if status_events else {}
    patch = json.loads(patch_event["data"]).get("data") if patch_event else None
    return {
        "status": run["status"] if run else "missing",
        "cost_usd": float(run["total_cost_usd"]) if run else 0.0,
        "tokens_in": int(agg["tin"] or 0),
        "tokens_out": int(agg["tout"] or 0),
        "cache_read_tokens": int(agg["cread"] or 0),
        "cache_write_tokens": int(agg["cwrite"] or 0),
        "llm_calls": int(agg["llm_calls"] or 0),
        "tool_calls": int(agg["tool_calls"] or 0),
        "error_spans": int(agg["error_spans"] or 0),
        "duration_s": round((root["duration_ms"] or 0) / 1000, 2) if root else 0.0,
        "attempts": outcome.get("attempts", 0),
        "test_retries": outcome.get("test_retries", 0),
        "review_rounds": outcome.get("review_rounds", 0),
        "reason": last_status.get("reason") or last_status.get("error") or "",
        "patch": patch,
    }
