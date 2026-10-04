"""The CI regression gate and baseline files."""

import json

import pytest

from agentteam.config import Settings
from agentteam.evals import __main__ as cli
from agentteam.evals.report import gate


def run(label, by_case, *, suite_hash="s1", truncated=False, kind="review"):
    return {
        "label": label,
        "suite": "toy",
        "suite_hash": suite_hash,
        "config_hash": "c",
        "model": "m",
        "trials": 1,
        "total_cost_usd": 0.25,
        "truncated": truncated,
        "summary": {
            kind: {
                "by_case": {cid: {"pass_rate": rate, "trials": 1} for cid, rate in by_case.items()}
            }
        },
    }


BASE = run("base", {"a": 1.0, "b": 1.0, "flaky": 0.67})


def test_gate_passes_when_stable_cases_stay_right():
    md, failures = gate(BASE, run("cand", {"a": 1.0, "b": 1.0, "flaky": 0.0}))
    assert failures == []
    assert "flaky in baseline" in md  # a case that was already flaky cannot fail the gate


def test_gate_fails_when_a_stable_case_breaks():
    md, failures = gate(BASE, run("cand", {"a": 1.0, "b": 0.0, "flaky": 1.0}))
    assert len(failures) == 1 and "case b" in failures[0]
    assert "REGRESSION" in md


def test_gate_tolerance_allows_one_flaky_trial_of_three():
    cand = run("cand", {"a": 2 / 3, "b": 1.0, "flaky": 1.0})
    assert gate(BASE, cand)[1]  # strict: fails
    assert gate(BASE, cand, tolerance=0.34)[1] == []
    assert gate(BASE, run("cand", {"a": 1 / 3, "b": 1.0}), tolerance=0.34)[1]  # two lost: fails


def test_gate_skips_cases_the_candidate_did_not_run_but_needs_some_overlap():
    assert gate(BASE, run("cand", {"a": 1.0}))[1] == []  # subset is fine
    failures = gate(BASE, run("cand", {"zzz": 1.0}))[1]
    assert failures and "nothing was checked" in failures[0]


def test_gate_fails_on_a_truncated_run_and_notes_a_changed_suite():
    md, failures = gate(BASE, run("cand", {"a": 1.0}, truncated=True, suite_hash="s2"))
    assert any("spend cap" in f for f in failures)
    assert "suite changed" in md


# --- baseline files through the CLI ---------------------------------------------------


@pytest.fixture
def settings_for_cli(tmp_path):
    return Settings(database_path=str(tmp_path / "x.db"))


def test_baseline_roundtrip_and_gate_cli(tmp_path, settings_for_cli, capsys):
    full = run("hard-haiku-clean", {"a": 1.0, "b": 1.0})
    full["results"] = [{"huge": "x" * 1000}]  # traces/patches must not end up in the baseline
    src = tmp_path / "scorecard.json"
    src.write_text(json.dumps(full), encoding="utf-8")
    out = tmp_path / "baselines" / "review-hard.json"

    ns = cli.argparse.Namespace(ref=str(src), out=str(out), name="review-hard")
    assert cli.cmd_baseline(ns, settings_for_cli) == 0
    slim = json.loads(out.read_text(encoding="utf-8"))
    assert slim["label"] == "review-hard" and "results" not in slim

    good = tmp_path / "good.json"
    good.write_text(json.dumps(run("ci", {"a": 1.0, "b": 1.0})), encoding="utf-8")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(run("ci", {"a": 1.0, "b": 0.0})), encoding="utf-8")

    def go(candidate, tol=0.0):
        ns = cli.argparse.Namespace(baseline=str(out), candidate=str(candidate), tolerance=tol)
        return cli.cmd_gate(ns, settings_for_cli)

    assert go(good) == 0
    assert go(bad) == 1
    assert "REGRESSION" in capsys.readouterr().out


def test_gate_writes_the_github_step_summary(tmp_path, settings_for_cli, monkeypatch):
    p = tmp_path / "b.json"
    p.write_text(json.dumps(BASE), encoding="utf-8")
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    ns = cli.argparse.Namespace(baseline=str(p), candidate=str(p), tolerance=0.0)
    assert cli.cmd_gate(ns, settings_for_cli) == 0
    assert "Eval gate" in summary.read_text(encoding="utf-8")


def test_gate_cli_reports_missing_or_malformed_inputs(tmp_path, settings_for_cli, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"label": "x"}), encoding="utf-8")
    ns = cli.argparse.Namespace(baseline=str(bad), candidate=str(bad), tolerance=0.0)
    assert cli.cmd_gate(ns, settings_for_cli) == 2
    assert "not an eval scorecard" in capsys.readouterr().out
