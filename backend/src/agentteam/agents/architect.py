import asyncio
import sys
from pathlib import Path

from ..agent_loop import AgentError, run_agent_loop
from ..llm import LLM
from ..mcp_toolbox import ServerSpec, Toolbox, read_only
from ..prompts import load_prompt
from ..schemas import DesignSpec, DesignSpecBody
from ..tracing import Tracer, traced

SUBMIT_DESIGN_SPEC = {
    "name": "submit_design_spec",
    "description": "Submit the final design spec. Call exactly once when done.",
    "input_schema": DesignSpecBody.model_json_schema(),
}


class Architect:
    """Task in, design spec out. Reads repo docs through the read-only Documentation MCP."""

    def __init__(self, llm: LLM, tracer: Tracer, docs_root: Path, max_steps: int = 8) -> None:
        self.llm = llm
        self.tracer = tracer
        self.docs_root = docs_root
        self.max_steps = max_steps

    @traced("architect", kind="agent")
    def design(self, run_id: str, task: str) -> DesignSpec:
        return asyncio.run(self._design(run_id, task))

    async def _design(self, run_id: str, task: str) -> DesignSpec:
        docs = ServerSpec(
            name="docs",
            command=sys.executable,
            args=["-m", "agentteam.mcp_servers.docs_server", "--root", str(self.docs_root)],
            policy=read_only,
        )
        async with Toolbox(self.tracer, [docs]) as toolbox:
            result = await run_agent_loop(
                llm=self.llm,
                tracer=self.tracer,
                name="architect",
                system=load_prompt("architect"),
                user=f"Task:\n{task}",
                toolbox=toolbox,
                max_steps=self.max_steps,
                submit_tool=SUBMIT_DESIGN_SPEC,
            )
        if result.submitted is None:
            raise AgentError("architect finished without submitting a design spec")
        body = DesignSpecBody.model_validate(result.submitted)
        return DesignSpec(run_id=run_id, **body.model_dump())
