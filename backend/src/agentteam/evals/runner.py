"""Runs eval cases through the real pipeline and grades the results.

Coding cases go through the full orchestrator (Architect -> Implementation -> Testing ->
Review). Each trial is an ordinary traced run, so any failure can be opened in the trace.
Review cases bypass the rest of the team and hand a seeded patch straight to the Review agent.
"""

import json
import shutil
import statistics
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..agents.review import Review
from ..budget import SpendCapExceeded
from ..config import Settings
from ..llm import LLM
from ..orchestrator import Orchestrator
from ..prompts import config_hash
from ..schemas import CommandRun, DesignSpec, Patch, TestReport
from ..tracing import Tracer, set_tracer
from ..workspace import Workspace, _git, collect_patch, create_workspace, remove_workspace
from . import graders as G
from .store import run_metrics
from .suite import CodingCase, ReviewCase, Suite


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class CaseResult:
    case_id: str
    kind: str  # coding | review
    trial: int
    run_id: str | None
    passed: bool
    grades: list[G.Grade]
    metrics: dict[str, Any]
    error: str | None = None


@dataclass
class EvalReport:
    id: str
    label: str
    suite: str
    suite_hash: str
    config_hash: str
    model: str
    trials: int
    started_at: str
    ended_at: str = ""
    total_cost_usd: float = 0.0
    truncated: bool = False
    results: list[CaseResult] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


# --- coding cases -----------------------------------------------------------------


def _workspace_patch(ws_path: Path) -> tuple[str, list[str]]:
    """Cumulative diff of the agent's branch against main, read from git (not from the trace,
    which may truncate large diffs)."""
    base = _git(ws_path, "merge-base", "main", "HEAD").strip()
    files = [f for f in _git(ws_path, "diff", "--name-only", base, "HEAD").splitlines() if f]
    return (_git(ws_path, "diff", base, "HEAD") if files else ""), files


def run_coding_case(
    case: CodingCase,
    trial: int,
    suite: Suite,
    tracer: Tracer,
    llm: LLM,
    settings: Settings,
    keep_workspaces: bool = False,
) -> CaseResult:
    run_id = tracer.create_run(case.task, config_hash=config_hash(settings.llm_model))
    # Mark running directly: claim_next_run() could steal an unrelated pending run.
    tracer.set_run_status(run_id, "running")
    Orchestrator(tracer, llm, settings).execute(run_id, case.task)

    m = run_metrics(settings.database_path, run_id)
    grades = [
        G.grade_status(m["status"], m["reason"]),
        G.grade_budget(m["cost_usd"], case.max_cost_usd),
    ]
    ws_path = Path(settings.workspaces_dir).resolve() / run_id[:12]
    error = None
    files: list[str] = []
    if ws_path.exists():
        try:
            diff, files = _workspace_patch(ws_path)
            grades.append(G.grade_scope(files, case.allowed_paths))
            grades.append(G.grade_tests_not_weakened(diff))
            if files:
                grades += G.grade_with_hidden_tests(
                    ws_path,
                    suite.acceptance_path(case),
                    settings.sandbox(),
                    Path(settings.workspaces_dir).resolve() / "_grading",
                )
            else:
                grades += [
                    G.Grade("regression_suite", False, "no changes were made"),
                    G.Grade("hidden_acceptance", False, "no changes were made"),
                ]
        except Exception as exc:  # grading infrastructure failure, not an agent failure
            error = f"grading failed: {type(exc).__name__}: {exc}"
        finally:
            if not keep_workspaces:
                _cleanup(ws_path, settings)
    else:
        grades += [G.Grade("hidden_acceptance", False, "no workspace was produced")]

    m.pop("patch", None)
    m["files_changed"] = files
    passed = error is None and all(g.passed for g in grades if g.required)
    return CaseResult(case.id, "coding", trial, run_id, passed, grades, m, error)


def _cleanup(ws_path: Path, settings: Settings) -> None:
    repo = Path(settings.toy_repo_path or ".").resolve()
    branch = f"agent/{ws_path.name}"
    try:
        remove_workspace(Workspace(repo=repo, path=ws_path, branch=branch, base_commit=""), True)
    except Exception:
        shutil.rmtree(ws_path, ignore_errors=True)


# --- review cases -----------------------------------------------------------------


def _text(value: str | list[str]) -> str:
    return "\n".join(value) if isinstance(value, list) else value


def apply_operations(root: Path, operations: list[dict[str, Any]]) -> None:
    for op in operations:
        target = root / op["path"]
        kind = op["op"]
        if kind == "replace":
            old, new = _text(op["old"]), _text(op["new"])
            text = target.read_text(encoding="utf-8")
            if text.count(old) != 1:
                raise ValueError(
                    f"{op['path']}: 'old' must match exactly once (found {text.count(old)})"
                )
            target.write_text(text.replace(old, new), encoding="utf-8", newline="\n")
        elif kind == "write":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_text(op["content"]), encoding="utf-8", newline="\n")
        elif kind == "delete":
            target.unlink()
        else:
            raise ValueError(f"unknown operation {kind!r}")


def run_review_case(
    case: ReviewCase, trial: int, tracer: Tracer, llm: LLM, settings: Settings
) -> CaseResult:
    run_id = tracer.create_run(f"[eval-review] {case.id}: {case.description}")
    tracer.set_run_status(run_id, "running")
    ws: Workspace | None = None
    verdict_dict: dict[str, Any] = {}
    error = None
    try:
        with (
            tracer.run(run_id),
            tracer.span("run", kind="orchestrator", input={"case": case.id}) as root,
        ):
            repo = Path(settings.toy_repo_path or "")
            ws = create_workspace(repo, Path(settings.workspaces_dir), run_id)
            apply_operations(ws.path, case.operations)
            diff, files = collect_patch(ws, f"eval seeded patch: {case.id}")
            spec = DesignSpec(run_id=run_id, **case.spec)
            patch = Patch(run_id=run_id, branch=ws.branch, diff=diff, files_changed=files)
            runs = [
                CommandRun(
                    command="pytest -q", exit_code=0 if case.report_passed else 1, duration_s=1.0
                ),
                CommandRun(command="ruff check .", exit_code=0, duration_s=0.3),
            ]
            report = TestReport(run_id=run_id, passed=case.report_passed, runs=runs, tests_run=10)
            verdict = Review(llm, tracer, settings.max_review_steps).review(
                run_id, spec, patch, report, ws
            )
            verdict_dict = verdict.model_dump(mode="json")
            root.set_output(verdict_dict)
        tracer.set_run_status(run_id, "done")
    except SpendCapExceeded as exc:
        tracer.set_run_status(run_id, "stopped", reason=str(exc))
        error = f"spend cap: {exc}"
    except Exception as exc:
        tracer.set_run_status(run_id, "error", error=f"{type(exc).__name__}: {exc}")
        error = f"{type(exc).__name__}: {exc}"
    finally:
        if ws is not None:
            try:
                remove_workspace(ws, delete_branch=True)
            except Exception:
                shutil.rmtree(ws.path, ignore_errors=True)

    m = run_metrics(settings.database_path, run_id)
    m.pop("patch", None)
    decision = verdict_dict.get("decision", "none")
    m.update(expected=case.expected, decision=decision, tags=list(case.tags))
    grades = (
        G.grade_review_decision(case.expected, decision, verdict_dict.get("comments", []))
        if error is None
        else [G.Grade("correct_decision", False, error)]
    )
    passed = all(g.passed for g in grades if g.required)
    return CaseResult(case.id, "review", trial, run_id, passed, grades, m, error)


# --- suite --------------------------------------------------------------------------


def summarize(results: list[CaseResult]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    coding = [r for r in results if r.kind == "coding"]
    if coding:
        by_case: dict[str, list[CaseResult]] = {}
        for r in coding:
            by_case.setdefault(r.case_id, []).append(r)
        passes = sum(r.passed for r in coding)
        total_cost = sum(r.metrics.get("cost_usd", 0.0) for r in coding)
        failing = Counter(g.name for r in coding for g in r.grades if g.required and not g.passed)
        out["coding"] = {
            "cases": len(by_case),
            "trials": len(coding),
            "pass_rate": passes / len(coding),
            "pass_at_k": sum(any(t.passed for t in ts) for ts in by_case.values()) / len(by_case),
            "total_cost_usd": round(total_cost, 4),
            "cost_per_pass_usd": round(total_cost / passes, 4) if passes else None,
            "mean_duration_s": round(
                statistics.fmean(r.metrics.get("duration_s", 0) for r in coding), 2
            ),
            "mean_llm_calls": round(
                statistics.fmean(r.metrics.get("llm_calls", 0) for r in coding), 1
            ),
            "retry_rate": sum(r.metrics.get("attempts", 0) > 1 for r in coding) / len(coding),
            "failed_graders": dict(failing),
            "by_case": {
                cid: {
                    "pass_rate": sum(t.passed for t in ts) / len(ts),
                    "trials": len(ts),
                    "mean_cost_usd": round(
                        statistics.fmean(t.metrics.get("cost_usd", 0) for t in ts), 4
                    ),
                    "mean_duration_s": round(
                        statistics.fmean(t.metrics.get("duration_s", 0) for t in ts), 2
                    ),
                }
                for cid, ts in by_case.items()
            },
        }
    review = [r for r in results if r.kind == "review"]
    if review:
        bad = [r for r in review if r.metrics.get("expected") == "changes_requested"]
        good = [r for r in review if r.metrics.get("expected") == "approved"]
        out["review"] = {
            "trials": len(review),
            "accuracy": sum(r.passed for r in review) / len(review),
            "catch_rate": (sum(r.passed for r in bad) / len(bad)) if bad else None,
            "false_block_rate": (sum(not r.passed for r in good) / len(good)) if good else None,
            "total_cost_usd": round(sum(r.metrics.get("cost_usd", 0.0) for r in review), 4),
            "by_tag": {
                t: {
                    "trials": len(rs),
                    "accuracy": sum(r.passed for r in rs) / len(rs),
                    "catch_rate": (
                        sum(
                            r.passed for r in rs if r.metrics.get("expected") == "changes_requested"
                        )
                        / n_bad
                        if (
                            n_bad := sum(
                                r.metrics.get("expected") == "changes_requested" for r in rs
                            )
                        )
                        else None
                    ),
                }
                for t in ("holdout",)
                if (rs := [r for r in review if t in r.metrics.get("tags", [])])
            },
            "by_case": {
                cid: {
                    "pass_rate": sum(r.passed for r in rs) / len(rs),
                    "trials": len(rs),
                    "decisions": [r.metrics.get("decision") for r in rs],
                    "expected": rs[0].metrics.get("expected"),
                }
                for cid in dict.fromkeys(r.case_id for r in review)
                if (rs := [r for r in review if r.case_id == cid])
            },
        }
    return out


def run_eval(
    settings: Settings,
    tracer: Tracer,
    llm: LLM,
    suite: Suite,
    *,
    label: str,
    trials: int = 1,
    case_ids: set[str] | None = None,
    include_coding: bool = True,
    include_review: bool = True,
    tag: str | None = None,
    exclude_tag: str | None = None,
    max_cost_usd: float = 3.0,
    keep_workspaces: bool = False,
    on_result: Callable[[CaseResult], None] | None = None,
) -> EvalReport:
    # @traced agents resolve the process-wide tracer. Coding cases get it from Orchestrator(),
    # but review-only runs never build one, so set it here.
    set_tracer(tracer)
    report = EvalReport(
        id=uuid.uuid4().hex[:12],
        label=label,
        suite=suite.name,
        suite_hash=suite.hash,
        config_hash=config_hash(settings.llm_model),
        model=llm.model,
        trials=trials,
        started_at=_now(),
    )
    jobs: list[tuple[str, str, int, Callable[[], CaseResult]]] = []
    for trial in range(1, trials + 1):
        if include_coding:
            for c in suite.coding:
                if case_ids is None or c.id in case_ids:
                    jobs.append(
                        (
                            "coding",
                            c.id,
                            trial,
                            lambda c=c, t=trial: run_coding_case(
                                c, t, suite, tracer, llm, settings, keep_workspaces
                            ),
                        )
                    )
        if include_review:
            for rc in suite.review:
                if (
                    (case_ids is None or rc.id in case_ids)
                    and (tag is None or tag in rc.tags)
                    and (exclude_tag is None or exclude_tag not in rc.tags)
                ):
                    jobs.append(
                        (
                            "review",
                            rc.id,
                            trial,
                            lambda rc=rc, t=trial: run_review_case(rc, t, tracer, llm, settings),
                        )
                    )

    for kind, case_id, trial, job in jobs:
        if report.total_cost_usd >= max_cost_usd:
            report.truncated = True  # eval-level budget: stop starting new trials
            break
        try:
            result = job()
        except KeyboardInterrupt:
            report.truncated = True  # Ctrl+C: keep and save everything finished so far
            break
        except Exception as exc:
            # A harness bug must not discard the results (and money) already spent.
            err = f"harness error: {type(exc).__name__}: {exc}"
            result = CaseResult(
                case_id, kind, trial, None, False, [G.Grade("harness_error", False, err)], {}, err
            )
        report.results.append(result)
        report.total_cost_usd = round(
            report.total_cost_usd + result.metrics.get("cost_usd", 0.0), 6
        )
        if on_result:
            on_result(result)

    report.ended_at = _now()
    report.summary = summarize(report.results)
    return report


def report_to_json(report: EvalReport) -> str:
    def conv(r: CaseResult) -> dict:
        return {**r.__dict__, "grades": [g.to_dict() for g in r.grades]}

    data = {**report.__dict__, "results": [conv(r) for r in report.results]}
    return json.dumps(data, indent=2, default=str)
