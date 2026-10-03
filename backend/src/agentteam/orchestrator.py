"""Orchestrator: owns run state and routes work between agents.

Graph so far: Architect -> Implementation. Testing, Review, Delivery and the capped
failure loops plug in here next.
"""

from pathlib import Path

from .agent_loop import AgentError
from .agents.architect import Architect
from .agents.implementation import Implementation
from .budget import SpendCapExceeded
from .config import Settings
from .llm import LLM
from .tracing import Tracer, set_tracer
from .workspace import create_workspace


class Orchestrator:
    def __init__(self, tracer: Tracer, llm: LLM, settings: Settings) -> None:
        self.tracer = tracer
        self.llm = llm
        self.settings = settings
        set_tracer(tracer)  # @traced agents resolve the process-wide tracer

    def _toy_repo(self) -> Path:
        if not self.settings.toy_repo_path:
            raise AgentError("TOY_REPO_PATH is not set; point it at the repo agents should modify")
        repo = Path(self.settings.toy_repo_path)
        if not (repo / ".git").exists():
            raise AgentError(f"TOY_REPO_PATH is not a git repository: {repo}")
        return repo

    def execute(self, run_id: str, task: str) -> None:
        """Run a claimed run to completion. Never raises; failures become run status."""
        try:
            with (
                self.tracer.run(run_id),
                self.tracer.span("run", kind="orchestrator", input={"task": task}) as root,
            ):
                repo = self._toy_repo()

                architect = Architect(
                    self.llm, self.tracer, repo, self.settings.max_architect_steps
                )
                spec = architect.design(run_id, task)
                self.tracer.emit("artifact.created", {"artifact": "design_spec", "data": spec})

                with self.tracer.span(
                    "workspace.create", kind="step", input={"repo": str(repo)}
                ) as ws_span:
                    workspace = create_workspace(repo, Path(self.settings.workspaces_dir), run_id)
                    ws_span.set_output(workspace)

                implementation = Implementation(
                    self.llm, self.tracer, self.settings.max_implementation_steps
                )
                patch = implementation.implement(run_id, spec, workspace)
                self.tracer.emit("artifact.created", {"artifact": "patch", "data": patch})

                root.set_output({"design_spec": spec, "patch": patch})
            self.tracer.set_run_status(run_id, "done")
        except SpendCapExceeded as exc:
            self.tracer.set_run_status(run_id, "stopped", reason=str(exc))
        except Exception as exc:
            self.tracer.set_run_status(run_id, "error", error=f"{type(exc).__name__}: {exc}")
