"""Review agent: strictly read-only. It sees the diff, the spec and the test report, and may
read files in the workspace, but it has no write tools and no terminal."""

import asyncio

from ..agent_loop import AgentError, run_agent_loop
from ..llm import LLM
from ..mcp_toolbox import ServerSpec, Toolbox, forbid_git_paths, read_only
from ..prompts import load_prompt
from ..schemas import DesignSpec, Patch, TestReport, Verdict, VerdictBody
from ..tracing import Tracer, traced
from ..workspace import Workspace

SUBMIT_VERDICT = {
    "name": "submit_verdict",
    "description": "Submit the final review verdict. Call exactly once when done.",
    "input_schema": VerdictBody.model_json_schema(),
}


class Review:
    def __init__(self, llm: LLM, tracer: Tracer, max_steps: int = 10) -> None:
        self.llm = llm
        self.tracer = tracer
        self.max_steps = max_steps

    @traced("review", kind="agent")
    def review(
        self,
        run_id: str,
        spec: DesignSpec,
        patch: Patch,
        report: TestReport,
        workspace: Workspace,
    ) -> Verdict:
        return asyncio.run(self._review(run_id, spec, patch, report, workspace))

    async def _review(
        self,
        run_id: str,
        spec: DesignSpec,
        patch: Patch,
        report: TestReport,
        workspace: Workspace,
    ) -> Verdict:
        filesystem = ServerSpec(
            name="filesystem",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", str(workspace.path)],
            policy=read_only,
            guard=forbid_git_paths,
        )
        exclude = {"run_id", "created_at"}
        user = (
            f"Workspace root: {workspace.path}\n\n"
            f"Design spec:\n{spec.model_dump_json(indent=2, exclude=exclude)}\n\n"
            f"Test report (real exit codes from the sandbox):\n"
            f"{report.model_dump_json(indent=2, exclude=exclude)}\n\n"
            f"Patch under review (unified diff against the base commit):\n{patch.diff}"
        )
        async with Toolbox(self.tracer, [filesystem]) as toolbox:
            result = await run_agent_loop(
                llm=self.llm,
                tracer=self.tracer,
                name="review",
                system=load_prompt("review"),
                user=user,
                toolbox=toolbox,
                max_steps=self.max_steps,
                submit_tool=SUBMIT_VERDICT,
                validate_submit=VerdictBody.model_validate,
            )
        if result.submitted is None:
            raise AgentError("reviewer finished without submitting a verdict")
        body = VerdictBody.model_validate(result.submitted)
        return Verdict(run_id=run_id, **body.model_dump())
