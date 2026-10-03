import asyncio

from ..agent_loop import AgentError, run_agent_loop
from ..llm import LLM
from ..mcp_toolbox import ServerSpec, Toolbox, forbid_git_paths, only
from ..prompts import load_prompt
from ..schemas import DesignSpec, Patch
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


class Implementation:
    """Design spec in, committed patch on a workspace branch out."""

    def __init__(self, llm: LLM, tracer: Tracer, max_steps: int = 20) -> None:
        self.llm = llm
        self.tracer = tracer
        self.max_steps = max_steps

    @traced("implementation", kind="agent")
    def implement(self, run_id: str, spec: DesignSpec, workspace: Workspace) -> Patch:
        summary = asyncio.run(self._implement(spec, workspace))
        diff, files = collect_patch(workspace, f"agent: {spec.summary[:60]}")
        if not files:
            raise AgentError("implementation finished without changing any files")
        return Patch(
            run_id=run_id,
            branch=workspace.branch,
            diff=diff,
            files_changed=files,
            rationale=summary,
        )

    async def _implement(self, spec: DesignSpec, workspace: Workspace) -> str:
        filesystem = ServerSpec(
            name="filesystem",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", str(workspace.path)],
            policy=only(*FILESYSTEM_TOOLS),
            guard=forbid_git_paths,
        )
        system = load_prompt("implementation").replace("{{workspace}}", str(workspace.path))
        async with Toolbox(self.tracer, [filesystem]) as toolbox:
            result = await run_agent_loop(
                llm=self.llm,
                tracer=self.tracer,
                name="implementation",
                system=system,
                user=f"Design spec:\n{spec.model_dump_json(indent=2)}",
                toolbox=toolbox,
                max_steps=self.max_steps,
            )
        return result.final_text
