"""CLI:  uv run python -m agentteam.evals <command>

run      run the suite and save a scorecard      (--label, --trials, --cases, --only, --fake ...)
compare  compare two saved runs by label or id   (exits 1 if the candidate regressed)
gate     CI check against a committed baseline   (exits 1 if a stable case now fails)
baseline freeze a saved run as evals/baselines/*.json
list     list saved eval runs
show     print the scorecard of a saved run
"""

import argparse
import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path

from ..config import Settings
from ..llm import FakeLLM
from ..prompts import active_variants
from ..worker import build_llm, build_tracer
from .report import compare, gate, scorecard
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


def _remotes(repo: Path) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(repo), "remote"], capture_output=True, text=True, check=False
    )
    return out.stdout.split()


def select_eval_repo(settings: Settings, *, allow_live: bool, fake: bool) -> Settings | str:
    """Point the settings at the eval repo, or return an error message.

    Real evals must run on a frozen clone with no remote: the live toy repo gains merged agent
    PRs, which silently turns cases like 'add /notes/count' into no-ops and makes reviewers
    flag duplicate routes. A repo with any git remote is treated as live.
    """
    repo = settings.eval_toy_repo_path or settings.toy_repo_path
    if not repo or not Path(repo).is_dir():
        return "No toy repo: set EVAL_TOY_REPO_PATH (or TOY_REPO_PATH) to a git repository."
    if not fake and not allow_live and _remotes(Path(repo)):
        return (
            f"Refusing to run evals on {repo}: it has a git remote, so it looks like the live "
            "repo, which accumulates merged PRs and invalidates cases. Point EVAL_TOY_REPO_PATH "
            "at a frozen clone with its remote removed (see README), or pass --allow-live-repo."
        )
    return dataclasses.replace(settings, toy_repo_path=str(repo))


def cmd_run(args: argparse.Namespace, settings: Settings) -> int:
    chosen = select_eval_repo(settings, allow_live=args.allow_live_repo, fake=args.fake)
    if isinstance(chosen, str):
        print(chosen, file=sys.stderr)
        return 2
    settings = chosen
    print(f"Toy repo: {settings.toy_repo_path}")
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
        f"hash={suite.hash}, trials={args.trials}, model={llm.model_label}"
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
    if args.fail_on_error:
        broken = [r for r in report.results if r.error]
        if broken:
            print(f"\n{len(broken)} case(s) hit a harness error (not a wrong answer):")
            for r in broken[:10]:
                print(f"  {r.case_id} trial {r.trial}: {r.error[:200]}")
            return 1
    return 0


REQUIRED_KEYS = ("label", "suite_hash", "config_hash", "model", "trials", "summary")


def load_ref(settings: Settings, ref: str) -> dict | None:
    """A saved eval run: a path to a .json scorecard/baseline file, else an id or label in the
    database. Files are how baselines live in the repo and how CI reads them."""
    path = Path(ref)
    if ref.endswith(".json") and path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        missing = [k for k in REQUIRED_KEYS if k not in data]
        if missing:
            raise ValueError(f"{ref}: not an eval scorecard/baseline (missing {missing})")
        data.setdefault("total_cost_usd", 0.0)
        data.setdefault("truncated", False)
        return data
    return load_eval_run(settings.database_path, ref)


def _load_both(settings: Settings, refs: tuple[str, str]) -> tuple[dict, dict] | None:
    runs = []
    for ref in refs:
        try:
            run = load_ref(settings, ref)
        except ValueError as exc:
            print(exc)
            return None
        if run is None:
            print(f"No eval run found for {ref!r}. Try: list")
            return None
        runs.append(run)
    return runs[0], runs[1]


def cmd_compare(args: argparse.Namespace, settings: Settings) -> int:
    runs = _load_both(settings, (args.baseline, args.candidate))
    if runs is None:
        return 2
    md, regressions = compare(*runs)
    print(md)
    return 1 if regressions else 0


def cmd_gate(args: argparse.Namespace, settings: Settings) -> int:
    runs = _load_both(settings, (args.baseline, args.candidate))
    if runs is None:
        return 2
    md, failures = gate(*runs, tolerance=args.tolerance)
    print(md)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:  # shows up on the GitHub Actions run page
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(md + "\n")
    return 1 if failures else 0


def cmd_baseline(args: argparse.Namespace, settings: Settings) -> int:
    """Freeze a saved run as a compact baseline file to commit (no traces, no patches)."""
    try:
        run = load_ref(settings, args.ref)
    except ValueError as exc:
        print(exc)
        return 2
    if run is None:
        print(f"No eval run found for {args.ref!r}. Try: list")
        return 2
    keep = ("id", "label", "suite", "suite_hash", "config_hash", "model", "trials")
    keep += ("started_at", "total_cost_usd", "truncated", "summary")
    slim = {k: run[k] for k in keep if k in run}
    slim["label"] = args.name or run["label"]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(slim, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote baseline {out} (from run {run.get('id', '?')}, model {run['model']})")
    return 0


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
    r.add_argument(
        "--fail-on-error",
        action="store_true",
        help="exit 1 if any case hit a harness error (setup, git, crash), whatever its score",
    )
    r.add_argument(
        "--allow-live-repo",
        action="store_true",
        help="run even if the toy repo has a git remote (results may be invalid)",
    )
    r.set_defaults(fn=cmd_run)

    c = sub.add_parser("compare")
    c.add_argument("baseline")
    c.add_argument("candidate")
    c.set_defaults(fn=cmd_compare)

    g = sub.add_parser("gate", help="CI gate: exit 1 if a case the baseline always got right fails")
    g.add_argument("baseline", help="label, run id, or path to a baseline .json")
    g.add_argument("candidate", help="label, run id, or path to a scorecard .json")
    g.add_argument(
        "--tolerance",
        type=float,
        default=0.0,
        help="correctness a stable case may lose: 0 for 1 trial, ~0.34 for 3 trials",
    )
    g.set_defaults(fn=cmd_gate)

    b = sub.add_parser("baseline", help="write a compact baseline file from a saved run")
    b.add_argument("ref", help="label, run id, or path to a scorecard .json")
    b.add_argument("--out", required=True, help="e.g. evals/baselines/review-hard.json")
    b.add_argument("--name", help="label to store in the file (default: the run's label)")
    b.set_defaults(fn=cmd_baseline)

    sub.add_parser("list").set_defaults(fn=cmd_list)
    s = sub.add_parser("show")
    s.add_argument("ref")
    s.set_defaults(fn=cmd_show)

    args = p.parse_args(argv)
    return args.fn(args, Settings.from_env())


if __name__ == "__main__":
    sys.exit(main())
