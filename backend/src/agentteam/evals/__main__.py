"""CLI:  uv run python -m agentteam.evals <command>

run      run the suite and save a scorecard      (--label, --trials, --cases, --only, --fake ...)
compare  compare two saved runs by label or id   (exits 1 if the candidate regressed)
list     list saved eval runs
show     print the scorecard of a saved run
"""

import argparse
import dataclasses
import os
import sys
from pathlib import Path

from ..config import Settings
from ..llm import FakeLLM
from ..prompts import active_variants
from ..worker import build_llm, build_tracer
from .report import compare, scorecard
from .runner import CaseResult, report_to_json, run_eval
from .store import list_eval_runs, load_eval_run, save_eval_run
from .suite import load_suite

DEFAULT_SUITE = "evals/suite.json"


def _print_result(r: CaseResult) -> None:
    mark = "PASS" if r.passed else "FAIL"
    cost = r.metrics.get("cost_usd", 0.0)
    print(
        f"  [{mark}] {r.kind:6} {r.case_id} (trial {r.trial})  ${cost:.3f}  run={r.run_id}",
        flush=True,
    )
    for g in r.grades:
        if g.required and not g.passed:
            print(f"         - {g.name}: {g.detail[:160]}", flush=True)


def cmd_run(args: argparse.Namespace, settings: Settings) -> int:
    suite = load_suite(args.suite)
    if args.variant:  # e.g. --variant review=v1 re-runs an older prompt for an A/B comparison
        os.environ["PROMPT_VARIANTS"] = ",".join(args.variant)
        print(f"Prompt variants active: {active_variants()}")
    if args.sandbox:
        settings = dataclasses.replace(settings, sandbox_mode=args.sandbox)
    tracer = build_tracer(settings)
    llm = FakeLLM(tracer) if args.fake else build_llm(tracer, settings)
    if args.fake:
        print("Using the FAKE LLM: this checks the plumbing only, scores are meaningless.")
    if settings.sandbox_mode == "local":
        print("WARNING: SANDBOX_MODE=local gives no isolation. Use docker for real evals.")
    label = args.label or f"eval-{Path(args.suite).stem}"
    case_ids = set(args.cases.split(",")) if args.cases else None
    print(
        f"Suite {suite.name} ({len(suite.coding)} coding, {len(suite.review)} review) "
        f"hash={suite.hash}, trials={args.trials}, model={llm.model}"
    )
    report = run_eval(
        settings,
        tracer,
        llm,
        suite,
        label=label,
        trials=args.trials,
        case_ids=case_ids,
        include_coding=args.only in (None, "coding"),
        include_review=args.only in (None, "review"),
        tag=args.tag,
        exclude_tag=args.exclude_tag,
        max_cost_usd=args.max_cost,
        keep_workspaces=args.keep_workspaces,
        on_result=_print_result,
    )
    save_eval_run(settings.database_path, report)
    out_dir = Path(settings.database_path).parent / "evals"
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = load_eval_run(settings.database_path, report.id)
    md = scorecard(saved)
    (out_dir / f"{label}.md").write_text(md, encoding="utf-8")
    (out_dir / f"{label}.json").write_text(report_to_json(report), encoding="utf-8")
    print("\n" + md)
    print(f"Saved eval run {report.id} (label {label}) and {out_dir / (label + '.md')}")
    return 0


def cmd_compare(args: argparse.Namespace, settings: Settings) -> int:
    a, b = (load_eval_run(settings.database_path, ref) for ref in (args.baseline, args.candidate))
    for ref, run in ((args.baseline, a), (args.candidate, b)):
        if run is None:
            print(f"No eval run found for {ref!r}. Try: list")
            return 2
    md, regressions = compare(a, b)
    print(md)
    return 1 if regressions else 0


def cmd_list(_: argparse.Namespace, settings: Settings) -> int:
    for r in list_eval_runs(settings.database_path):
        c = r["summary"].get("coding", {})
        v = r["summary"].get("review", {})
        print(
            f"{r['started_at'][:19]}  {r['id']}  {r['label']:24} model={r['model']}  "
            f"coding={c.get('pass_rate', float('nan')):.0%}  review={v.get('accuracy', float('nan')):.0%}  "
            f"${r['total_cost_usd']:.3f}"
        )
    return 0


def cmd_show(args: argparse.Namespace, settings: Settings) -> int:
    run = load_eval_run(settings.database_path, args.ref)
    if run is None:
        print(f"No eval run found for {args.ref!r}")
        return 2
    print(scorecard(run))
    return 0


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    load_dotenv()
    p = argparse.ArgumentParser(prog="agentteam.evals")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run")
    r.add_argument("--suite", default=DEFAULT_SUITE)
    r.add_argument("--label")
    r.add_argument("--trials", type=int, default=1)
    r.add_argument("--cases", help="comma-separated case ids")
    r.add_argument("--only", choices=["coding", "review"])
    r.add_argument("--tag", help="review cases: only those with this tag (e.g. holdout)")
    r.add_argument("--exclude-tag", help="review cases: skip those with this tag")
    r.add_argument("--variant", action="append", help="prompt variant, e.g. review=v1 (repeatable)")
    r.add_argument("--fake", action="store_true", help="use the free fake LLM (plumbing check)")
    r.add_argument("--sandbox", choices=["docker", "local"])
    r.add_argument("--max-cost", type=float, default=3.0, help="stop starting trials past this USD")
    r.add_argument("--keep-workspaces", action="store_true")
    r.set_defaults(fn=cmd_run)

    c = sub.add_parser("compare")
    c.add_argument("baseline")
    c.add_argument("candidate")
    c.set_defaults(fn=cmd_compare)

    sub.add_parser("list").set_defaults(fn=cmd_list)
    s = sub.add_parser("show")
    s.add_argument("ref")
    s.set_defaults(fn=cmd_show)

    args = p.parse_args(argv)
    return args.fn(args, Settings.from_env())


if __name__ == "__main__":
    sys.exit(main())
