"""Delivery agent: opens the pull request, after a human approved the run.

Division of labour (least privilege):
- The HARNESS pushes the branch (`workspace.push_branch`): the model never holds git
  credentials and cannot push anything else.
- The AGENT writes the PR title and body and calls the official GitHub MCP server, which
  is launched with `GITHUB_TOOLS=create_pull_request` (the server itself exposes nothing else)
  and re-filtered by our policy. A guard rejects any call whose repo, head or base differs
  from the run's real values, so a confused or manipulated model cannot open a PR elsewhere.
- The PR record (url, number) is taken from the real tool result, never from model prose.
"""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from ..agent_loop import AgentError, run_agent_loop
from ..config import Settings
from ..llm import LLM
from ..mcp_toolbox import Guard, ServerSpec, Toolbox, ToolNotAllowed, only
from ..prompts import load_prompt
from ..schemas import DesignSpec, Patch, PullRequest, TestReport, Verdict
from ..tracing import Tracer, traced
from ..workspace import Workspace

CREATE_PR_TOOL = "create_pull_request"


def make_pr_guard(owner: str, repo: str, head: str, base: str) -> Guard:
    expected = {"owner": owner, "repo": repo, "head": head, "base": base}

    def guard(tool: str, args: dict[str, Any]) -> None:
        if tool != CREATE_PR_TOOL:
            raise ToolNotAllowed(f"only {CREATE_PR_TOOL} is allowed, not {tool}")
        for key, want in expected.items():
            if args.get(key) != want:
                raise ToolNotAllowed(f"{key} must be {want!r}, got {args.get(key)!r}")

    return guard


@dataclass
class _PRRecorder:
    """Captures the real outcome of create_pull_request from the tool result."""

    calls: list[dict[str, Any]] = field(default_factory=list)
    url: str | None = None
    number: int | None = None

    def __call__(self, tool: str, args: dict[str, Any], result: Any) -> None:
        if not tool.endswith(CREATE_PR_TOOL) or result.is_error:
            return
        self.calls.append(args)
        try:
            data = json.loads(result.text)
        except ValueError:
            data = {}
        if isinstance(data, dict):
            url = data.get("html_url") or data.get("url")
            if isinstance(url, str):
                self.url = url
            if isinstance(data.get("number"), int):
                self.number = data["number"]
        if self.url and self.number is None:  # derive from ".../pull/<n>"
            tail = self.url.rstrip("/").rsplit("/", 1)[-1]
            self.number = int(tail) if tail.isdigit() else None


class Delivery:
    def __init__(self, llm: LLM, tracer: Tracer, settings: Settings) -> None:
        self.llm = llm
        self.tracer = tracer
        self.settings = settings

    @traced("delivery", kind="agent")
    def deliver(
        self,
        run_id: str,
        spec: DesignSpec,
        patch: Patch,
        report: TestReport,
        verdict: Verdict,
        workspace: Workspace,
    ) -> PullRequest:
        return asyncio.run(self._deliver(run_id, spec, patch, report, verdict, workspace))

    async def _deliver(
        self,
        run_id: str,
        spec: DesignSpec,
        patch: Patch,
        report: TestReport,
        verdict: Verdict,
        workspace: Workspace,
    ) -> PullRequest:
        s = self.settings
        assert s.github_repo and s.github_token
        owner, repo = s.github_repo.split("/", 1)
        base = s.github_base_branch
        github = ServerSpec(
            name="github",
            command=s.github_mcp_command,
            args=list(s.github_mcp_args),
            policy=only(CREATE_PR_TOOL),
            guard=make_pr_guard(owner, repo, workspace.branch, base),
            env={
                "GITHUB_PERSONAL_ACCESS_TOKEN": s.github_token,
                "GITHUB_TOOLS": CREATE_PR_TOOL,
            },
        )
        exclude = {"run_id", "created_at"}
        user = (
            f"Run id: {run_id}\n"
            f"owner: {owner}\nrepo: {repo}\nhead: {workspace.branch}\nbase: {base}\n\n"
            f"Design spec:\n{spec.model_dump_json(indent=2, exclude=exclude)}\n\n"
            f"Changed files: {', '.join(patch.files_changed)}\n\n"
            "Test report (real exit codes):\n"
            f"{report.model_dump_json(indent=2, exclude=exclude)}\n\n"
            f"Review verdict:\n{verdict.model_dump_json(indent=2, exclude=exclude)}"
        )
        recorder = _PRRecorder()
        async with Toolbox(self.tracer, [github]) as toolbox:
            await run_agent_loop(
                llm=self.llm,
                tracer=self.tracer,
                name="delivery",
                system=load_prompt("delivery"),
                user=user,
                toolbox=toolbox,
                max_steps=s.max_delivery_steps,
                on_tool_result=recorder,
            )
        if not recorder.calls or not recorder.url:
            raise AgentError("delivery finished without a confirmed pull request")
        sent = recorder.calls[-1]
        return PullRequest(
            run_id=run_id,
            title=str(sent.get("title", "")),
            body=str(sent.get("body", "")),
            head_branch=workspace.branch,
            base_branch=base,
            url=recorder.url,
            number=recorder.number,
        )
