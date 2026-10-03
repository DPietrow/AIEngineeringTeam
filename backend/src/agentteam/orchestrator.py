"""Orchestrator: a state machine that owns run state and routes work between agents.

    DESIGN -> IMPLEMENT -> TEST -> REVIEW -> DONE
                  ^           |        |
                  +-----------+--------+   (tests fail / changes requested)

Each failure loop is capped (3 retries by default). Exceeding a cap ends the run as
FAILED rather than looping forever. Every state transition and retry is emitted as an event,
so the whole path is visible in the trace stream.
"""

from enum import StrEnum
from pathlib import Path

from .agent_loop import AgentError
from .agents.architect import Architect
from .agents.implementation import Implementation
from .agents.review import Review
from .agents.testing import Testing
from .budget import SpendCapExceeded
from .config import Settings
from .llm import LLM
from .schemas import DesignSpec, Patch, TestReport, Verdict
from .tracing import Tracer, set_tracer
from .workspace import Workspace, create_workspace


class State(StrEnum):
    DESIGN = "design"
    IMPLEMENT = "implement"
    TEST = "test"
    REVIEW = "review"
    DONE = "done"
    FAILED = "failed"


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

    def _transition(self, src: State, dst: State, reason: str = "") -> State:
        self.tracer.emit("state.transition", {"from": src.value, "to": dst.value, "reason": reason})
        return dst

    def _artifact(self, name: str, data: object) -> None:
        self.tracer.emit("artifact.created", {"artifact": name, "data": data})

    def execute(self, run_id: str, task: str) -> None:
        """Run a claimed run to completion. Never raises; failures become run status."""
        try:
            with (
                self.tracer.run(run_id),
                self.tracer.span("run", kind="orchestrator", input={"task": task}) as root,
            ):
                outcome = self._pipeline(run_id, task)
                root.set_output(outcome)
            if outcome["state"] == State.DONE.value:
                self.tracer.set_run_status(run_id, "done")
            else:
                self.tracer.set_run_status(run_id, "failed", reason=outcome["reason"])
        except SpendCapExceeded as exc:
            self.tracer.set_run_status(run_id, "stopped", reason=str(exc))
        except Exception as exc:
            self.tracer.set_run_status(run_id, "error", error=f"{type(exc).__name__}: {exc}")

    def _pipeline(self, run_id: str, task: str) -> dict:
        s = self.settings
        repo = self._toy_repo()
        architect = Architect(self.llm, self.tracer, repo, s.max_architect_steps)
        implementation = Implementation(self.llm, self.tracer, s.max_implementation_steps)
        testing = Testing(self.llm, self.tracer, s.sandbox(), s.max_testing_steps)
        review = Review(self.llm, self.tracer, s.max_review_steps)

        state = State.DESIGN
        spec: DesignSpec | None = None
        workspace: Workspace | None = None
        patch: Patch | None = None
        report: TestReport | None = None
        verdict: Verdict | None = None
        feedback: TestReport | Verdict | None = None
        attempt = test_retries = review_rounds = 0
        reason = ""

        while state not in (State.DONE, State.FAILED):
            if state is State.DESIGN:
                spec = architect.design(run_id, task)
                self._artifact("design_spec", spec)
                with self.tracer.span(
                    "workspace.create", kind="step", input={"repo": str(repo)}
                ) as ws_span:
                    workspace = create_workspace(repo, Path(s.workspaces_dir), run_id)
                    ws_span.set_output(workspace)
                state = self._transition(state, State.IMPLEMENT, "design ready")

            elif state is State.IMPLEMENT:
                assert spec is not None and workspace is not None
                attempt += 1
                patch = implementation.implement(run_id, spec, workspace, attempt, feedback)
                self._artifact("patch", patch)
                state = self._transition(state, State.TEST, f"attempt {attempt} patched")

            elif state is State.TEST:
                assert workspace is not None
                report = testing.test(run_id, workspace)
                self._artifact("test_report", report)
                if report.passed:
                    feedback = None
                    state = self._transition(state, State.REVIEW, "checks passed")
                else:
                    test_retries += 1
                    if test_retries > s.max_test_retries:
                        reason = f"tests still failing after {s.max_test_retries} retries"
                        state = self._transition(state, State.FAILED, reason)
                    else:
                        self.tracer.emit(
                            "loop.retry",
                            {"loop": "test", "retry": test_retries, "cap": s.max_test_retries},
                        )
                        feedback = report
                        state = self._transition(state, State.IMPLEMENT, "tests failed")

            elif state is State.REVIEW:
                assert spec and patch and report and workspace
                verdict = review.review(run_id, spec, patch, report, workspace)
                self._artifact("verdict", verdict)
                if verdict.decision == "approved":
                    state = self._transition(state, State.DONE, "approved")
                else:
                    review_rounds += 1
                    if review_rounds > s.max_review_rounds:
                        reason = f"changes still requested after {s.max_review_rounds} rounds"
                        state = self._transition(state, State.FAILED, reason)
                    else:
                        self.tracer.emit(
                            "loop.retry",
                            {"loop": "review", "retry": review_rounds, "cap": s.max_review_rounds},
                        )
                        feedback = verdict
                        state = self._transition(state, State.IMPLEMENT, "changes requested")

        return {
            "state": state.value,
            "reason": reason,
            "attempts": attempt,
            "test_retries": test_retries,
            "review_rounds": review_rounds,
            "design_spec": spec,
            "patch": patch,
            "test_report": report,
            "verdict": verdict,
        }
