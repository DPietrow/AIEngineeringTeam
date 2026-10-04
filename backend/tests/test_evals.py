import json
import os
import shutil
from pathlib import Path

import pytest

from agentteam.evals import __main__ as cli
from agentteam.evals import graders as G
from agentteam.evals.report import compare, scorecard
from agentteam.evals.runner import CaseResult, apply_operations, run_eval, summarize
from agentteam.evals.store import list_eval_runs, load_eval_run, save_eval_run
from agentteam.evals.suite import load_suite
from agentteam.llm import FakeLLM
from agentteam.sandbox import SandboxConfig, run_in_sandbox
from agentteam.worker import build_tracer

from .conftest import needs_npx

SUITE_PATH = Path(__file__).parents[1] / "evals" / "suite.json"
LOCAL = SandboxConfig(mode="local", timeout_s=60)


def _toy_repo() -> Path | None:
    # The suite needs the PRISTINE toy app (as scaffolded). The live toy repo moves on every
    # time an agent's PR is merged, so prefer a frozen sibling clone (see README, Evals).
    candidates = [
        os.environ.get("EVAL_TOY_REPO_PATH"),
        Path(__file__).parents[3] / "agentteam-toy-eval",
        os.environ.get("TOY_REPO_PATH"),
        Path(__file__).parents[3] / "agentteam-toy",
    ]
    for c in candidates:
        if c and (Path(c) / "toyapp" / "app.py").exists():
            return Path(c)
    return None


needs_toy = pytest.mark.skipif(_toy_repo() is None, reason="agentteam-toy repo not found")


def copy_toy(tmp_path: Path) -> Path:
    dest = tmp_path / "toy"
    shutil.copytree(
        _toy_repo(),
        dest,
        ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", ".pytest_cache", "t"),
    )
    return dest


# --- the suite itself is sound -------------------------------------------------------


def test_suite_loads_with_stable_hash():
    a, b = load_suite(SUITE_PATH), load_suite(SUITE_PATH)
    assert a.hash == b.hash and len(a.coding) >= 5 and len(a.review) >= 6
    assert {c.expected for c in a.review} == {"approved", "changes_requested"}
    assert all(c.reference for c in a.coding)


@needs_toy
@pytest.mark.parametrize("case_id", [c.id for c in load_suite(SUITE_PATH).coding])
def test_hidden_tests_fail_on_base_and_pass_on_reference_solution(tmp_path, case_id):
    """Validates the grader: if a hidden test passes on the untouched repo, or fails on a
    known-correct solution, every score built on it would be meaningless."""
    suite = load_suite(SUITE_PATH)
    case = next(c for c in suite.coding if c.id == case_id)
    repo = copy_toy(tmp_path)
    hidden = repo / "tests" / G.HIDDEN_TEST_NAME
    shutil.copyfile(suite.acceptance_path(case), hidden)
    args = ["pytest", "-q", "-p", "no:cacheprovider", f"tests/{G.HIDDEN_TEST_NAME}"]

    base = run_in_sandbox(repo, args, LOCAL)
    assert base.exit_code != 0, (
        "hidden tests pass on the base repo. Either they test nothing, or the toy repo is no "
        "longer pristine (an agent PR was merged): point EVAL_TOY_REPO_PATH at a frozen clone "
        "of the scaffold commit (see README, Evals)"
    )

    apply_operations(repo, case.reference)
    solved = run_in_sandbox(repo, args, LOCAL)
    assert solved.exit_code == 0, solved.stdout[-800:]

    everything = run_in_sandbox(repo, ["pytest", "-q", "-p", "no:cacheprovider"], LOCAL)
    assert everything.exit_code == 0, "reference solution broke existing tests"


@needs_toy
def test_every_seeded_review_patch_applies_cleanly(tmp_path):
    suite = load_suite(SUITE_PATH)
    for case in suite.review:
        repo = tmp_path / case.id
        shutil.copytree(
            _toy_repo(), repo, ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", "t")
        )
        apply_operations(repo, case.operations)


@needs_toy
def test_seeded_bad_patches_pass_their_tests_so_only_review_can_catch_them(tmp_path):
    """The bad-patch cases are only a fair test of the reviewer if the test suite is green."""
    suite = load_suite(SUITE_PATH)
    for case in suite.review:
        repo = tmp_path / case.id
        shutil.copytree(
            _toy_repo(), repo, ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", "t")
        )
        apply_operations(repo, case.operations)
        res = run_in_sandbox(repo, ["pytest", "-q", "-p", "no:cacheprovider"], LOCAL)
        assert res.exit_code == 0, f"{case.id}: {res.stdout[-500:]}"


def test_holdout_cases_exist_and_are_tagged():
    suite = load_suite(SUITE_PATH)
    holdout = [c for c in suite.review if "holdout" in c.tags]
    assert len([c for c in holdout if c.expected == "changes_requested"]) >= 4
    assert (
        len([c for c in holdout if c.expected == "approved"]) >= 2
    )  # controls guard over-blocking


def test_prompt_variants_change_the_prompt_and_the_config_hash(monkeypatch):
    from agentteam.prompts import config_hash, load_prompt

    monkeypatch.delenv("PROMPT_VARIANTS", raising=False)
    v2, h2 = load_prompt("review"), config_hash("m")
    monkeypatch.setenv("PROMPT_VARIANTS", "review=v1")
    v1, h1 = load_prompt("review"), config_hash("m")
    assert v1 != v2 and h1 != h2
    assert "NOT sufficient" in v2 and "NOT sufficient" not in v1
    monkeypatch.setenv("PROMPT_VARIANTS", "review=nope")
    with pytest.raises(ValueError, match="no prompt variant"):
        load_prompt("review")


def test_apply_operations_rejects_ambiguous_replace(tmp_path):
    (tmp_path / "f.txt").write_text("a\na\n")
    with pytest.raises(ValueError, match="exactly once"):
        apply_operations(tmp_path, [{"op": "replace", "path": "f.txt", "old": "a", "new": "b"}])


# --- graders ---------------------------------------------------------------------------

DIFF_DELETE_TEST = (
    "diff --git a/tests/test_store.py b/tests/test_store.py\n"
    "deleted file mode 100644\n--- a/tests/test_store.py\n+++ /dev/null\n"
    "@@ -1,2 +0,0 @@\n-def test_x():\n-    assert 1\n"
)
DIFF_ADD_TEST = (
    "diff --git a/tests/test_app.py b/tests/test_app.py\n--- a/tests/test_app.py\n"
    "+++ b/tests/test_app.py\n@@ -1,0 +2,2 @@\n+def test_y():\n+    assert 2\n"
)


def test_scope_grader():
    ok = G.grade_scope(["toyapp/app.py", "tests/test_app.py"], ("toyapp/*", "tests/*"))
    assert ok.passed
    bad = G.grade_scope(["toyapp/app.py", ".github/ci.yml"], ("toyapp/*", "tests/*"))
    assert not bad.passed and ".github/ci.yml" in bad.detail


def test_tests_not_weakened_grader():
    assert G.grade_tests_not_weakened(DIFF_ADD_TEST).passed
    deleted = G.grade_tests_not_weakened(DIFF_DELETE_TEST)
    assert not deleted.passed and "tests/test_store.py" in deleted.detail
    fewer = DIFF_ADD_TEST.replace("+    assert 2", "+    pass")  # one assert fewer than removed
    assert not G.grade_tests_not_weakened(DIFF_DELETE_TEST.replace("deleted file mode", "x")).passed
    assert G.grade_tests_not_weakened(fewer).passed  # nothing removed, so not 'weakened'


def test_status_budget_and_review_graders():
    assert G.grade_status("done").passed and not G.grade_status("failed", "x").passed
    assert G.grade_budget(0.1, 0.4).passed and not G.grade_budget(0.5, 0.4).passed
    grades = G.grade_review_decision("changes_requested", "approved", [])
    assert not grades[0].passed and not grades[1].required
    assert G.grade_review_decision("approved", "approved", [])[0].passed


# --- runner, store, report, CLI (full pipeline with the fake LLM) -----------------------


@pytest.fixture
def mini_suite(tmp_path):
    root = tmp_path / "suite"
    (root / "acceptance").mkdir(parents=True)
    (root / "acceptance" / "ok.py").write_text(
        "from pathlib import Path\n\n\ndef test_notes_exist():\n"
        "    assert Path('AGENT_NOTES.md').exists()\n"
    )
    (root / "acceptance" / "never.py").write_text("def test_never():\n    assert False\n")
    suite = {
        "name": "mini",
        "cases": [
            {
                "id": "passes",
                "task": "Add a notes file",
                "acceptance": "acceptance/ok.py",
                "allowed_paths": ["*"],
                "reference": [],
            },
            {
                "id": "fails",
                "task": "Add a notes file",
                "acceptance": "acceptance/never.py",
                "allowed_paths": ["*"],
                "reference": [],
            },
        ],
        "review_cases": [
            {
                "id": "fine",
                "description": "ok",
                "expected": "approved",
                "spec": {"summary": "s", "changes": [], "acceptance_criteria": []},
                "operations": [{"op": "write", "path": "x.txt", "content": "hi"}],
            },
            {
                "id": "bad",
                "description": "bad",
                "expected": "changes_requested",
                "spec": {"summary": "s", "changes": [], "acceptance_criteria": []},
                "operations": [{"op": "write", "path": "x.txt", "content": "hi"}],
            },
        ],
    }
    path = root / "suite.json"
    path.write_text(json.dumps(suite))
    return path


@needs_npx
def test_run_eval_end_to_end_with_fake_llm(settings, mini_suite):
    tracer = build_tracer(settings)
    suite = load_suite(mini_suite)
    seen = []
    report = run_eval(
        settings, tracer, FakeLLM(tracer), suite, label="t1", trials=1, on_result=seen.append
    )
    by_id = {r.case_id: r for r in report.results}
    assert by_id["passes"].passed and not by_id["fails"].passed
    assert [g.name for g in by_id["fails"].grades if g.required and not g.passed] == [
        "hidden_acceptance"
    ]
    assert by_id["passes"].metrics["status"] == "done"
    assert by_id["passes"].metrics["files_changed"] == ["AGENT_NOTES.md"]
    # The fake reviewer approves everything: it passes the control, misses the bad patch.
    assert by_id["fine"].passed and not by_id["bad"].passed
    s = report.summary
    assert s["coding"]["pass_rate"] == 0.5 and s["coding"]["pass_at_k"] == 0.5
    assert s["review"]["catch_rate"] == 0.0 and s["review"]["false_block_rate"] == 0.0
    assert s["coding"]["failed_graders"] == {"hidden_acceptance": 1}
    assert len(seen) == 4
    # Worktrees and branches are cleaned up so evals do not litter the repo.
    assert not (Path(settings.workspaces_dir) / report.results[0].run_id[:12]).exists()

    save_eval_run(settings.database_path, report)
    loaded = load_eval_run(settings.database_path, "t1")
    assert loaded["suite_hash"] == suite.hash and len(loaded["results"]) == 4
    assert loaded["results"][0]["run_id"] and loaded["summary"]["coding"]["trials"] == 2
    assert "Eval scorecard: t1" in scorecard(loaded)
    assert list_eval_runs(settings.database_path)[0]["label"] == "t1"


def test_harness_error_is_recorded_not_fatal(settings, mini_suite, monkeypatch):
    from agentteam.evals import runner

    def boom(*a, **k):
        raise RuntimeError("grader exploded")

    monkeypatch.setattr(runner, "run_coding_case", boom)
    tracer = build_tracer(settings)
    report = run_eval(
        settings, tracer, FakeLLM(tracer), load_suite(mini_suite), label="err", include_review=False
    )
    assert len(report.results) == 2 and not any(r.passed for r in report.results)
    assert report.results[0].grades[0].name == "harness_error"
    assert "grader exploded" in report.results[0].error


@needs_npx
def test_review_only_eval_works_in_a_fresh_process_state(settings, mini_suite, monkeypatch):
    """Regression: review-only runs never construct an Orchestrator, so nothing had called
    set_tracer and every case died with 'Tracer not configured'."""
    from agentteam import tracing

    monkeypatch.setattr(tracing, "_tracer", None)
    tracer = build_tracer(settings)
    report = run_eval(
        settings, tracer, FakeLLM(tracer), load_suite(mini_suite), label="ro", include_coding=False
    )
    assert [r.case_id for r in report.results] == ["fine", "bad"]
    assert not any(r.error for r in report.results)
    assert report.results[0].passed  # the fake reviewer approves the control


def test_run_metrics_tolerates_non_dict_root_output(settings):
    from agentteam.evals.store import run_metrics

    tracer = build_tracer(settings)
    run_id = tracer.create_run("t")
    with tracer.run(run_id), tracer.span("run", kind="orchestrator") as root:
        root.set_output("a plain string, not a dict")
    assert run_metrics(settings.database_path, run_id)["attempts"] == 0


@needs_npx
def test_eval_spend_cap_truncates(settings, mini_suite):
    tracer = build_tracer(settings)
    report = run_eval(
        settings, tracer, FakeLLM(tracer), load_suite(mini_suite), label="cap", max_cost_usd=0.0
    )
    assert report.truncated and report.results == []


@needs_npx
def test_cli_run_list_show_compare(settings, mini_suite, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_PATH", settings.database_path)
    monkeypatch.setenv("TOY_REPO_PATH", settings.toy_repo_path)
    monkeypatch.setenv("WORKSPACES_DIR", settings.workspaces_dir)
    monkeypatch.setenv("SANDBOX_MODE", "local")
    monkeypatch.setenv("LLM_MODE", "fake")
    for label in ("base", "cand"):
        rc = cli.main(
            ["run", "--suite", str(mini_suite), "--label", label, "--fake", "--only", "coding"]
        )
        assert rc == 0
    assert cli.main(["list"]) == 0 and cli.main(["show", "base"]) == 0
    assert cli.main(["compare", "base", "cand"]) == 0  # identical runs: no regression
    assert cli.main(["compare", "base", "nope"]) == 2
    assert "Eval scorecard" in capsys.readouterr().out


# --- compare flags regressions -------------------------------------------------------------


def fake_result(case_id, passed, cost=0.1):
    grades = [G.Grade("hidden_acceptance", passed)]
    metrics = {"cost_usd": cost, "duration_s": 10, "llm_calls": 8, "attempts": 1}
    return CaseResult(case_id, "coding", 1, "r", passed, grades, metrics)


def as_run(label, results):
    return {
        "label": label,
        "suite": "s",
        "suite_hash": "h",
        "config_hash": "c",
        "model": "m",
        "trials": 1,
        "total_cost_usd": 1.0,
        "summary": summarize(results),
        "results": [],
    }


def test_compare_flags_regressions_and_improvements():
    base = as_run("base", [fake_result("a", True), fake_result("b", True), fake_result("c", False)])
    worse = as_run(
        "worse", [fake_result("a", True), fake_result("b", False), fake_result("c", False)]
    )
    better = as_run(
        "better", [fake_result("a", True), fake_result("b", True), fake_result("c", True)]
    )

    md, regressions = compare(base, worse)
    assert regressions and "case b" in " ".join(regressions) and "WORSE" in md
    md2, none = compare(base, better)
    assert none == [] and "better" in md2


def test_summarize_cost_per_pass_and_pass_at_k():
    results = [
        fake_result("a", True, 0.2),
        fake_result("a", False, 0.2),
        fake_result("b", False, 0.1),
    ]
    c = summarize(results)["coding"]
    assert c["pass_rate"] == pytest.approx(1 / 3) and c["pass_at_k"] == 0.5
    assert c["cost_per_pass_usd"] == pytest.approx(0.5)
