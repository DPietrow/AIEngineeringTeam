import asyncio
from dataclasses import dataclass

from ..agent_loop import run_agent_loop
from ..llm import LLM
from ..mcp_toolbox import ServerSpec, Toolbox, forbid_git_paths, only
from ..prompts import load_prompt
from ..schemas import DesignSpec, Patch, TestReport, Verdict
from ..tracing import Tracer, traced
from ..workspace import Workspace, collect_patch

# Read + write inside the workspace. No move_file (destructive), no media reads.
FILESYSTEM_TOOLS = (
    "read_text_file",
    "read_multiple_files",
    "write_file",
    "edit_file",
    "create_directory",
    "list_directory",
    "directory_tree",
    "search_files",
    "get_file_info",
)


@dataclass
class NoChanges:
    """Implementation finished without changing anything. `explanation` is the agent's own
    final message, i.e. a claim, shown to the human but never trusted as a fact."""

    explanation: str


def _feedback_text(feedback: TestReport | Verdict) -> str:
    if isinstance(feedback, TestReport):
        return (
            "Your previous attempt FAILED automated checks. Fix the problems below, then stop.\n"
            f"Test report:\n{feedback.model_dump_json(indent=2, exclude={'run_id', 'created_at'})}"
        )
    return (
        "A reviewer requested changes to your previous attempt. Address every blocker and major "
        "comment, then stop.\n"
        f"Review:\n{feedback.model_dump_json(indent=2, exclude={'run_id', 'created_at'})}"
    )


class Implementation:
    """Design spec in, committed patch on a workspace branch out. Re-runs with feedback when
    tests fail or the reviewer asks for changes."""

    def __init__(self, llm: LLM, tracer: Tracer, max_steps: int = 20) -> None:
        self.llm = llm
        self.tracer = tracer
        self.max_steps = max_steps

    @traced("implementation", kind="agent")
    def implement(
        self,
        run_id: str,
        spec: DesignSpec,
        workspace: Workspace,
        attempt: int = 1,
        feedback: TestReport | Verdict | None = None,
    ) -> Patch | NoChanges:
        summary = asyncio.run(self._implement(spec, workspace, feedback))
        diff, files = collect_patch(workspace, f"agent (attempt {attempt}): {spec.summary[:60]}")
        if not files:
            # The patch is cumulative, so an empty one means nothing was ever changed: the
            # request was already satisfied (or the agent gave up). Not a crash.
            return NoChanges(explanation=summary)
        return Patch(
            run_id=run_id,
            branch=workspace.branch,
            diff=diff,
            files_changed=files,
            rationale=summary,
            attempt=attempt,
        )

    async def _implement(
        self, spec: DesignSpec, workspace: Workspace, feedback: TestReport | Verdict | None
    ) -> str:
        filesystem = ServerSpec(
            name="filesystem",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", str(workspace.path)],
            policy=only(*FILESYSTEM_TOOLS),
            guard=forbid_git_paths,
        )
        system = load_prompt("implementation").replace("{{workspace}}", str(workspace.path))
        user = f"Design spec:\n{spec.model_dump_json(indent=2, exclude={'run_id', 'created_at'})}"
        if feedback is not None:
            user += f"\n\n{_feedback_text(feedback)}"
        async with Toolbox(self.tracer, [filesystem]) as toolbox:
            result = await run_agent_loop(
                llm=self.llm,
                tracer=self.tracer,
                name="implementation",
                system=system,
                user=user,
                toolbox=toolbox,
                max_steps=self.max_steps,
            )
        return result.final_text
