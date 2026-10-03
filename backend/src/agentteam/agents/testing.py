"""Testing agent: runs the project's checks in the sandbox through the Terminal MCP.

The model chooses what to run, but the report is built from real exit codes captured as the
commands execute, never from what the model says happened. `passed` is true only if every
required check ran, finished, and exited 0.
"""

import asyncio
import json
import re
import sys
from dataclasses import dataclass

from ..agent_loop import run_agent_loop
from ..llm import LLM
from ..mcp_toolbox import ServerSpec, Toolbox, only
from ..prompts import load_prompt
from ..sandbox import SandboxConfig
from ..schemas import CommandRun, TestFailure, TestReport
from ..tracing import Tracer, traced
from ..workspace import Workspace

REQUIRED_CHECKS = {"pytest": "pytest", "ruff check": "ruff check"}

_FAILED_LINE = re.compile(r"^FAILED (\S+)(?: - (.*))?$", re.MULTILINE)
_PASSED = re.compile(r"(\d+) passed")
_FAILED = re.compile(r"(\d+) failed")
_ERRORS = re.compile(r"(\d+) errors?")


def parse_pytest(output: str) -> tuple[int, list[TestFailure]]:
    """Extract (tests_run, failures) from pytest's output."""
    passed = sum(int(n) for n in _PASSED.findall(output))
    failed = sum(int(n) for n in _FAILED.findall(output))
    errors = sum(int(n) for n in _ERRORS.findall(output))
    failures = [TestFailure(name=m[0], message=m[1] or "") for m in _FAILED_LINE.findall(output)]
    return passed + failed + errors, failures


def _tail(text: str, limit: int = 4000) -> str:
    return text if len(text) <= limit else "...\n" + text[-limit:]


@dataclass
class _Recorder:
    runs: list[CommandRun]

    def __call__(self, tool: str, args: dict, result) -> None:
        if not tool.endswith("run_command"):
            return
        try:
            data = json.loads(result.text)
        except ValueError:  # rejected command or tool error: not a real run
            return
        if not isinstance(data, dict) or "exit_code" not in data:
            return
        self.runs.append(
            CommandRun(
                command=str(data.get("command", args.get("command", ""))),
                exit_code=int(data["exit_code"]),
                duration_s=float(data.get("duration_s", 0)),
                timed_out=bool(data.get("timed_out", False)),
                output_tail=_tail(f"{data.get('stdout', '')}\n{data.get('stderr', '')}".strip()),
            )
        )


def evaluate(runs: list[CommandRun]) -> tuple[bool, list[str]]:
    """passed is true only if each required check's LATEST run finished and exited 0."""
    missing: list[str] = []
    ok = True
    for label, prefix in REQUIRED_CHECKS.items():
        matching = [r for r in runs if r.command.strip().startswith(prefix)]
        if not matching:
            missing.append(label)
            ok = False
        elif matching[-1].exit_code != 0 or matching[-1].timed_out:
            ok = False
    return ok, missing


class Testing:
    __test__ = False  # not a pytest class

    def __init__(
        self, llm: LLM, tracer: Tracer, sandbox: SandboxConfig, max_steps: int = 8
    ) -> None:
        self.llm = llm
        self.tracer = tracer
        self.sandbox = sandbox
        self.max_steps = max_steps

    @traced("testing", kind="agent")
    def test(self, run_id: str, workspace: Workspace) -> TestReport:
        return asyncio.run(self._test(run_id, workspace))

    async def _test(self, run_id: str, workspace: Workspace) -> TestReport:
        terminal = ServerSpec(
            name="terminal",
            command=sys.executable,
            args=[
                "-m",
                "agentteam.mcp_servers.terminal_server",
                "--workspace",
                str(workspace.path),
                "--mode",
                self.sandbox.mode,
                "--image",
                self.sandbox.image,
                "--timeout",
                str(self.sandbox.timeout_s),
                "--memory",
                self.sandbox.memory,
                "--cpus",
                self.sandbox.cpus,
                "--pids-limit",
                str(self.sandbox.pids_limit),
            ],
            policy=only("run_command"),
        )
        recorder = _Recorder(runs=[])
        async with Toolbox(self.tracer, [terminal]) as toolbox:
            result = await run_agent_loop(
                llm=self.llm,
                tracer=self.tracer,
                name="testing",
                system=load_prompt("testing"),
                user="Verify the project in the workspace: run the tests and the linter.",
                toolbox=toolbox,
                max_steps=self.max_steps,
                on_tool_result=recorder,
            )

        passed, missing = evaluate(recorder.runs)
        pytest_runs = [r for r in recorder.runs if r.command.strip().startswith("pytest")]
        tests_run, failures = parse_pytest(pytest_runs[-1].output_tail) if pytest_runs else (0, [])
        return TestReport(
            run_id=run_id,
            passed=passed,
            runs=recorder.runs,
            missing_checks=missing,
            tests_run=tests_run,
            failures=failures,
            summary=result.final_text,
        )
