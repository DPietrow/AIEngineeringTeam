"""Scorecards (markdown) and run-to-run comparison.

`compare` is the point of the whole harness: change a prompt, a model or a cap, re-run the
suite, and see in numbers whether things got better, worse, or just cost more.
"""

from typing import Any


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.0f}%"


def _usd(x: float | None) -> str:
    return "n/a" if x is None else f"${x:.3f}"


def scorecard(run: dict[str, Any]) -> str:
    """`run` is the dict from store.load_eval_run (or an equivalent built from an EvalReport)."""
    s = run["summary"]
    lines = [
        f"# Eval scorecard: {run['label']}",
        "",
        f"- suite `{run['suite']}` (hash `{run['suite_hash']}`), config `{run['config_hash']}`, "
        f"model `{run['model']}`, trials per case: {run['trials']}",
        f"- total cost ${run['total_cost_usd']:.3f}"
        + ("  **(truncated by the eval spend cap)**" if run.get("truncated") else ""),
        "",
    ]
    c = s.get("coding")
    if c:
        lines += [
            "## Coding (full team, hidden acceptance tests)",
            "",
            f"- trial pass rate: **{_pct(c['pass_rate'])}**  |  pass@k (any trial passes): "
            f"{_pct(c['pass_at_k'])}",
            f"- cost per passing trial: {_usd(c['cost_per_pass_usd'])}  |  total {_usd(c['total_cost_usd'])}",
            f"- mean duration {c['mean_duration_s']}s  |  mean LLM calls {c['mean_llm_calls']}  |  "
            f"retry rate {_pct(c['retry_rate'])}",
        ]
        if c.get("cache_read_share") is not None:
            lines.append(
                f"- prompt cache: {_pct(c['cache_read_share'])} of input tokens were reads"
            )
        if c["failed_graders"]:
            lines.append(f"- failed graders: {c['failed_graders']}")
        lines += [
            "",
            "| case | pass rate | trials | mean cost | mean time |",
            "|---|---|---|---|---|",
        ]
        for cid, d in c["by_case"].items():
            lines.append(
                f"| {cid} | {_pct(d['pass_rate'])} | {d['trials']} | {_usd(d['mean_cost_usd'])} | "
                f"{d['mean_duration_s']}s |"
            )
        lines.append("")
    r = s.get("review")
    if r:
        lines += [
            "## Review (seeded good and bad patches)",
            "",
            f"- accuracy **{_pct(r['accuracy'])}**  |  bad patches caught {_pct(r['catch_rate'])}  |  "
            f"good patches wrongly blocked {_pct(r['false_block_rate'])}",
        ]
        for tag, d in r.get("by_tag", {}).items():
            lines.append(
                f"- **{tag}** cases only: accuracy {_pct(d['accuracy'])}  |  "
                f"bad patches caught {_pct(d['catch_rate'])}  ({d['trials']} trials)"
            )
        lines += ["", "| case | expected | correct | decisions |", "|---|---|---|---|"]
        for cid, d in r["by_case"].items():
            # Older saved runs stored only the last trial: {"passed", "decision"}.
            rate = d.get("pass_rate", 1.0 if d.get("passed") else 0.0)
            decisions = d.get("decisions") or [d.get("decision", "?")]
            lines.append(
                f"| {cid} | {d.get('expected', '?')} | {_pct(rate)} | {', '.join(decisions)} |"
            )
        lines.append("")
    results = run.get("results", [])
    failures = [x for x in results if not x["passed"]]
    if failures:
        lines += ["## Failures (open the run id in the trace)", ""]
        for x in failures:
            bad = [g for g in x["grades"] if g["required"] and not g["passed"]]
            why = "; ".join(f"{g['name']}: {g['detail'][:120]}" for g in bad) or (
                x.get("error") or ""
            )
            lines.append(f"- `{x['case_id']}` trial {x['trial']} run `{x['run_id']}`: {why}")
        lines.append("")
    return "\n".join(lines)


_HIGHER_IS_BETTER = {
    ("coding", "pass_rate"),
    ("coding", "pass_at_k"),
    ("review", "accuracy"),
    ("review", "catch_rate"),
}
_LOWER_IS_BETTER = {
    ("coding", "cost_per_pass_usd"),
    ("coding", "total_cost_usd"),
    ("coding", "mean_duration_s"),
    ("coding", "mean_llm_calls"),
    ("coding", "retry_rate"),
    ("review", "false_block_rate"),
}


def gate(
    baseline: dict[str, Any], candidate: dict[str, Any], tolerance: float = 0.0
) -> tuple[str, list[str]]:
    """CI regression gate. Returns (markdown, failures); any failure means "do not merge".

    Aggregate numbers are too noisy to gate on with a few trials, so this is judged per case:
    a case the baseline got right every time (a *stable* case) must still be right, within
    `tolerance` (0 for a single-trial CI run; about 0.34 for a 3-trial run, which lets one flaky
    trial through). Cases that were already flaky in the baseline cannot fail the gate, and
    cases the candidate did not run (a subset) are skipped. This catches real regressions
    ("the reviewer now approves the admin-endpoint patch it used to catch") without flaking on
    noise.
    """
    lines = [
        f"# Eval gate: {candidate['label']} vs baseline {baseline['label']}",
        "",
        f"- baseline: model `{baseline['model']}`, config `{baseline['config_hash']}`, "
        f"suite `{baseline['suite_hash']}`, {baseline['trials']} trial(s) per case",
        f"- candidate: model `{candidate['model']}`, config `{candidate['config_hash']}`, "
        f"suite `{candidate['suite_hash']}`, {candidate['trials']} trial(s) per case, "
        f"cost ${candidate['total_cost_usd']:.3f}",
        f"- tolerance: {tolerance:.2f} (a stable case may lose at most this much correctness)",
    ]
    if baseline["suite_hash"] != candidate["suite_hash"]:
        lines.append(
            "- **note: the suite changed since the baseline was recorded**; only cases present "
            "in both are compared. Re-record the baseline when you change the suite."
        )
    failures: list[str] = []
    if candidate.get("truncated"):
        failures.append("the run was cut short by the eval spend cap, so it is incomplete")

    compared = 0
    lines += ["", "| case | baseline | candidate | verdict |", "|---|---|---|---|"]
    for section in ("review", "coding"):
        sa, sb = baseline["summary"].get(section), candidate["summary"].get(section)
        if not sa or not sb:
            continue
        for cid, da in sa["by_case"].items():
            db = sb["by_case"].get(cid)
            if db is None:
                continue
            compared += 1
            pa, pb = da.get("pass_rate", 0.0), db.get("pass_rate", 0.0)
            stable = pa >= 0.99
            regressed = stable and pb < pa - tolerance - 1e-9
            if regressed:
                failures.append(
                    f"{section} case {cid}: always right in the baseline, now {_pct(pb)}"
                )
            verdict = "REGRESSION" if regressed else ("ok" if stable else "flaky in baseline")
            lines.append(f"| {cid} | {_pct(pa)} | {_pct(pb)} | {verdict} |")
    if compared == 0:
        failures.append("no cases in common with the baseline, so nothing was checked")
    lines += ["", "**Failures:** " + ("; ".join(failures) if failures else "none"), ""]
    return "\n".join(lines), failures


def compare(a: dict[str, Any], b: dict[str, Any]) -> tuple[str, list[str]]:
    """Compare run `a` (baseline) with `b` (candidate). Returns (markdown, regressions)."""
    lines = [
        f"# Compare: {a['label']} -> {b['label']}",
        "",
        f"- config `{a['config_hash']}` -> `{b['config_hash']}`; model `{a['model']}` -> `{b['model']}`",
    ]
    if a["suite_hash"] != b["suite_hash"]:
        lines.append(
            f"- **warning: suites differ** (`{a['suite_hash']}` vs `{b['suite_hash']}`); "
            "results are not strictly comparable"
        )
    lines += ["", "| metric | baseline | candidate | change |", "|---|---|---|---|"]
    regressions: list[str] = []
    for section in ("coding", "review"):
        sa, sb = a["summary"].get(section), b["summary"].get(section)
        if not sa or not sb:
            continue
        for key in (*(k for k in sa if (section, k) in _HIGHER_IS_BETTER | _LOWER_IS_BETTER),):
            va, vb = sa.get(key), sb.get(key)
            if va is None or vb is None:
                continue
            delta = vb - va
            if abs(delta) < 1e-9:
                mark = "="
            else:
                good = delta > 0 if (section, key) in _HIGHER_IS_BETTER else delta < 0
                mark = "better" if good else "WORSE"
                if not good and (section, key) in {("coding", "pass_rate"), ("review", "accuracy")}:
                    regressions.append(f"{section}.{key}: {va:.2f} -> {vb:.2f}")
            fmt = (
                _pct
                if key
                in {
                    "pass_rate",
                    "pass_at_k",
                    "accuracy",
                    "catch_rate",
                    "false_block_rate",
                    "retry_rate",
                }
                else (lambda v: f"{v:g}")
            )
            lines.append(f"| {section}.{key} | {fmt(va)} | {fmt(vb)} | {mark} |")
    ca, cb = a["summary"].get("coding"), b["summary"].get("coding")
    if ca and cb:
        lines += [
            "",
            "## Per-case pass rate",
            "",
            "| case | baseline | candidate |",
            "|---|---|---|",
        ]
        for cid in sorted(set(ca["by_case"]) | set(cb["by_case"])):
            pa = ca["by_case"].get(cid, {}).get("pass_rate")
            pb = cb["by_case"].get(cid, {}).get("pass_rate")
            flag = "  <- regression" if pa is not None and pb is not None and pb < pa else ""
            if flag:
                regressions.append(f"case {cid}: {_pct(pa)} -> {_pct(pb)}")
            lines.append(f"| {cid} | {_pct(pa)} | {_pct(pb)}{flag} |")
    lines += ["", "**Regressions:** " + ("; ".join(regressions) if regressions else "none"), ""]
    return "\n".join(lines), regressions
