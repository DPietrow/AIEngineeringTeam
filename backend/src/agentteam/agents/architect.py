from ..llm import LLM
from ..prompts import load_prompt
from ..schemas import DesignSpec, DesignSpecBody
from ..tracing import traced


class Architect:
    """Task description in, design spec out. No tools yet (Documentation MCP comes later)."""

    def __init__(self, llm: LLM) -> None:
        self.llm = llm

    @traced("architect", kind="agent")
    def design(self, run_id: str, task: str) -> DesignSpec:
        body = self.llm.structured(
            name="design_spec",
            system=load_prompt("architect"),
            user=f"Task:\n{task}",
            schema=DesignSpecBody,
        )
        return DesignSpec(run_id=run_id, **body.model_dump())
