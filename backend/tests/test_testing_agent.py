from agentteam.agents.testing import Testing, evaluate, parse_pytest
from agentteam.llm import FakeLLM, _text_turn, _tool_turn
from agentteam.sandbox import SandboxConfig
from agentteam.schemas import CommandRun
from agentteam.workspace import Workspace


def run(command, exit_code=0, timed_out=False):
    return CommandRun(
        command=command, exit_code=exit_code, duration_s=0.1, timed_out=timed_out, output_tail=""
    )


def test_evaluate_requires_every_check_to_pass():
    assert evaluate([run("pytest -q"), run("ruff check .")]) == (True, [])
    assert evaluate([run("pytest -q")]) == (False, ["ruff check"])
    assert evaluate([]) == (False, ["pytest", "ruff check"])
    assert evaluate([run("pytest", 1), run("ruff check .")])[0] is False
    assert evaluate([run("pytest"), run("ruff check .", 124, timed_out=True)])[0] is False


def test_evaluate_uses_the_latest_run_of_each_check():
    assert evaluate([run("pytest", 1), run("pytest"), run("ruff check .")])[0] is True
    assert evaluate([run("pytest"), run("pytest", 1), run("ruff check .")])[0] is False


def test_parse_pytest():
    out = (
        "FAILED tests/test_a.py::test_x - assert 1 == 2\n"
        "FAILED tests/test_a.py::test_y\n"
        "2 failed, 3 passed in 0.12s\n"
    )
    tests_run, failures = parse_pytest(out)
    assert tests_run == 5
    assert [(f.name, f.message) for f in failures] == [
        ("tests/test_a.py::test_x", "assert 1 == 2"),
        ("tests/test_a.py::test_y", ""),
    ]
    assert parse_pytest("no tests ran") == (0, [])


def make_project(tmp_path, test_body="def test_ok():\n    assert True\n"):
    (tmp_path / "test_sample.py").write_text(test_body)
    return Workspace(repo=tmp_path, path=tmp_path, branch="agent/x", base_commit="0" * 40)


def agent(tracer):
    return Testing(FakeLLM(tracer), tracer, SandboxConfig(mode="local", timeout_s=60))


def test_real_checks_pass(tracer, tmp_path):
    run_id = tracer.create_run("t")
    with tracer.run(run_id):
        report = agent(tracer).test(run_id, make_project(tmp_path))
    assert report.passed is True and report.missing_checks == []
    assert [r.exit_code for r in report.runs] == [0, 0]
    assert report.tests_run == 1


def test_failing_test_fails_the_report_regardless_of_model_claims(tracer, tmp_path):
    run_id = tracer.create_run("t")
    ws = make_project(tmp_path, "def test_bad():\n    assert 1 == 2\n")
    with tracer.run(run_id):
        report = agent(tracer).test(run_id, ws)
    # The fake model's summary says it ran both checks; the exit codes decide the outcome.
    assert report.passed is False
    assert report.runs[0].exit_code != 0
    assert [f.name for f in report.failures] == ["test_sample.py::test_bad"]


def test_skipped_check_is_reported_missing(tracer, tmp_path):
    def only_pytest(name, system, messages, tools):
        if sum(1 for m in messages if m["role"] == "assistant") == 0:
            return _tool_turn("t1", "terminal__run_command", {"command": "pytest -q"})
        return _text_turn("All good!")  # claims success without running ruff

    run_id = tracer.create_run("t")
    t = Testing(
        FakeLLM(tracer, script=only_pytest), tracer, SandboxConfig(mode="local", timeout_s=60)
    )
    with tracer.run(run_id):
        report = t.test(run_id, make_project(tmp_path))
    assert report.passed is False and report.missing_checks == ["ruff check"]


def test_rejected_command_is_not_recorded_as_a_run(tracer, tmp_path):
    def sneaky(name, system, messages, tools):
        if sum(1 for m in messages if m["role"] == "assistant") == 0:
            return _tool_turn("t1", "terminal__run_command", {"command": "rm -rf /"})
        return _text_turn("done")

    run_id = tracer.create_run("t")
    t = Testing(FakeLLM(tracer, script=sneaky), tracer, SandboxConfig(mode="local", timeout_s=60))
    with tracer.run(run_id):
        report = t.test(run_id, make_project(tmp_path))
    assert report.runs == [] and report.passed is False
