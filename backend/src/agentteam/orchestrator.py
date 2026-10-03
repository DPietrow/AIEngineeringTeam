"""Orchestrator: owns run state and routes work between agents.

For now the graph is just Architect. Later stages (Implementation, Testing, Review,
Delivery) and the capped failure loops plug in here.
"""

from .agents.architect import Architect
from .budget import SpendCapExceeded
from .llm import LLM
from .tracing import Tracer, set_tracer


class Orchestrator:
    def __init__(self, tracer: Tracer, llm: LLM) -> None:
        self.tracer = tracer
        set_tracer(tracer)  # @traced agents resolve the process-wide tracer
        self.architect = Architect(llm)

    def execute(self, run_id: str, task: str) -> None:
        """Run a claimed run to completion. Never raises; failures become run status."""
        try:
            with (
                self.tracer.run(run_id),
                self.tracer.span("run", kind="orchestrator", input={"task": task}) as root,
            ):
                spec = self.architect.design(run_id, task)
                self.tracer.emit("artifact.created", {"artifact": "design_spec", "data": spec})
                root.set_output({"design_spec": spec})
            self.tracer.set_run_status(run_id, "done")
        except SpendCapExceeded as exc:
            self.tracer.set_run_status(run_id, "stopped", reason=str(exc))
        except Exception as exc:
            self.tracer.set_run_status(run_id, "error", error=f"{type(exc).__name__}: {exc}")
