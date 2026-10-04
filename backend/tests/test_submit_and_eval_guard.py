"""Submit-tool repair/validation in the agent loop, and the eval repo guard."""

import asyncio
import dataclasses
import json
import subprocess

import pytest

from agentteam.agent_loop import run_agent_loop
from agentteam.app import create_app
from agentteam.evals.__main__ import select_eval_repo
from agentteam.llm import FakeLLM, _text_turn, _tool_turn
from agentteam.mcp_toolbox import Toolbox
from agentteam.schemas import VerdictBody
from agentteam.tracing import set_tracer

SUBMIT = {
    "name": "submit_verdict",
    "description": "x",
    "input_schema": VerdictBody.model_json_schema(),
}
GOOD = {"decision": "approved", "summary": "fine", "comments": []}


@pytest.fixture
def tracer(settings):
    app = create_app({"TESTING": True, "DATABASE_PATH": settings.database_path})
    t = app.extensions["tracer"]
    set_tracer(t)
    return t


def run_loop(tracer, script, max_steps=6, **kw):
    run_id = tracer.create_run("t")

    async def go():
        async with Toolbox(tracer, []) as toolbox:
            return await run_agent_loop(
                llm=FakeLLM(tracer, script=script),
                tracer=tracer,
                name="review",
                system="s",
                user="u",
                toolbox=toolbox,
                max_steps=max_steps,
                submit_tool=SUBMIT,
                **kw,
            )

    with tracer.run(run_id):
        return asyncio.run(go()), run_id


def test_stringified_list_in_submission_is_repaired(tracer):
    bad = {
        "decision": "changes_requested",
        "summary": "no",
        "comments": json.dumps([{"path": "a.py", "line": 1, "severity": "major", "message": "m"}]),
    }
    result, _ = run_loop(
        tracer,
        lambda *a: _tool_turn("1", "submit_verdict", bad),
        validate_submit=VerdictBody.model_validate,
    )
    assert isinstance(result.submitted["comments"], list)
    VerdictBody.model_validate(result.submitted)


def test_invalid_submission_is_sent_back_and_can_be_fixed(tracer):
    calls = []

    def script(name, system, messages, tools):
        calls.append(messages[-1])
        if len(calls) == 1:
            return _tool_turn("1", "submit_verdict", {"decision": "maybe", "summary": "s"})
        return _tool_turn("2", "submit_verdict", GOOD)

    result, _ = run_loop(tracer, script, validate_submit=VerdictBody.model_validate)
    assert result.submitted == GOOD and result.steps == 2
    feedback = calls[1]["content"][0]
    assert feedback["is_error"] and "Invalid submission" in feedback["content"]


def test_model_that_never_fixes_its_submission_hits_the_step_cap(tracer):
    from agentteam.agent_loop import StepLimitExceeded

    with pytest.raises(StepLimitExceeded):
        run_loop(
            tracer,
            lambda *a: _tool_turn("1", "submit_verdict", {"decision": "maybe"}),
            validate_submit=VerdictBody.model_validate,
            max_steps=3,
        )


def test_model_is_nudged_twice_before_giving_up(tracer):
    seen = []

    def script(name, system, messages, tools):
        seen.append(len(messages))
        if len(seen) == 3:  # after two nudges the model complies
            return _tool_turn("1", "submit_verdict", GOOD)
        return _text_turn("I think it is fine.")

    result, _ = run_loop(tracer, script)
    assert result.submitted == GOOD and result.steps == 3

    # ...but a model that keeps refusing is given up on after the nudges are used up.
    result, _ = run_loop(tracer, lambda *a: _text_turn("no"))
    assert result.submitted is None and result.steps == 3


# --- eval repo guard -------------------------------------------------------------------


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repos(tmp_path):
    live = tmp_path / "live"
    live.mkdir()
    git(live, "init", "-q")
    git(live, "remote", "add", "origin", "https://example.invalid/x.git")
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    git(frozen, "init", "-q")
    return live, frozen


def test_eval_guard_refuses_a_repo_with_a_remote(settings, repos):
    live, _ = repos
    s = dataclasses.replace(settings, toy_repo_path=str(live), eval_toy_repo_path=None)
    msg = select_eval_repo(s, allow_live=False, fake=False)
    assert isinstance(msg, str) and "git remote" in msg
    # explicit override, and the free fake mode, are allowed
    assert not isinstance(select_eval_repo(s, allow_live=True, fake=False), str)
    assert not isinstance(select_eval_repo(s, allow_live=False, fake=True), str)


def test_eval_repo_setting_takes_priority(settings, repos):
    live, frozen = repos
    s = dataclasses.replace(settings, toy_repo_path=str(live), eval_toy_repo_path=str(frozen))
    chosen = select_eval_repo(s, allow_live=False, fake=False)
    assert chosen.toy_repo_path == str(frozen)
    # the live repo setting is untouched on the original object
    assert s.toy_repo_path == str(live)


def test_eval_guard_reports_missing_repo(settings):
    s = dataclasses.replace(settings, toy_repo_path=None, eval_toy_repo_path=None)
    assert "No toy repo" in select_eval_repo(s, allow_live=False, fake=False)
