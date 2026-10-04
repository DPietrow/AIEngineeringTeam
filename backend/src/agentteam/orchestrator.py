"""Orchestrator: a state machine that owns run state and routes work between agents.

    DESIGN -> IMPLEMENT -> TEST -> REVIEW -> AWAITING_APPROVAL -> (human) -> DONE
                  ^           |        |
                  +-----------+--------+   (tests fail / changes requested)

With delivery configured, a reviewer-approved run parks at AWAITING_APPROVAL (the worker moves
on). A human approves or rejects in the dashboard; on approve the worker pushes the branch and
the Delivery agent opens the PR (`deliver`). Without GitHub settings, REVIEW goes straight to DONE.

Each failure loop is capped (3 retries by default). Exceeding a cap ends the run as
FAILED rather than looping forever. Every state transition and retry is emitted as an event,
so the whole path is visible in the trace stream.
"""

from enum import StrEnum
from pathlib import Path

from .agent_loop import AgentError
from .agents.architect import Architect
from .agents.delivery import Delivery
from .agents.implementation import Implementation, NoChanges
from .agents.review import Review
from .agents.testing import Testing
from .budget import SpendCapExceeded
from .config import Settings
from .deadline import RunTimeout, check_deadline, run_deadline
from .llm import LLM
from .schemas import DesignSpec, Patch, TestReport, Verdict
from .tracing import Tracer, set_tracer
from .workspace import (
    Workspace,
    cleanup_workspace,
    create_workspace,
    existing_workspace,
    push_branch,
)


class State(StrEnum):
    DESIGN = "design"
    IMPLEMENT = "implement"
    TEST = "test"
    REVIEW = "review"
    AWAITING_APPROVAL = "awaiting_approval"  # human gate; the worker is free while it waits
    DONE = "done"
    NO_CHANGES = "no_changes"  # request already satisfied / nothing to change (not an error)
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
                run_deadline(self.settings.run_timeout_s),
                self.tracer.run(run_id),
                self.tracer.span("run", kind="orchestrator", input={"task": task}) as root,
            ):
                outcome = self._pipeline(run_id, task)
                root.set_output(outcome)
            if outcome["state"] == State.DONE.value:
                self.tracer.set_run_status(run_id, "done")
            elif outcome["state"] == State.AWAITING_APPROVAL.value:
                self.tracer.set_run_status(run_id, "awaiting_approval")
            elif outcome["state"] == State.NO_CHANGES.value:
                self.tracer.set_run_status(run_id, "no_changes", reason=outcome["reason"])
            else:
                self.tracer.set_run_status(run_id, "failed", reason=outcome["reason"])
        except SpendCapExceeded as exc:
            self.tracer.set_run_status(run_id, "stopped", reason=str(exc))
        except RunTimeout as exc:
            limit = f"{self.settings.run_timeout_s:g}s"
            self.tracer.set_run_status(run_id, "timed_out", reason=f"{exc} (limit {limit})")
        except Exception as exc:
            self.tracer.set_run_status(run_id, "error", error=f"{type(exc).__name__}: {exc}")

    def deliver(self, run_id: str) -> None:
        """Deliver a run a human approved: push its branch, then have the Delivery agent open
        the PR. Never raises; failures become run status. The worker calls this for runs the
        API moved to 'approved' (see Tracer.decide_gate / claim_next_delivery)."""
        s = self.settings
        try:
            with (
                run_deadline(s.run_timeout_s),
                self.tracer.run(run_id),
                self.tracer.span("deliver", kind="orchestrator", input={"run_id": run_id}) as root,
            ):
                if not s.delivery_enabled:
                    raise AgentError("delivery is not configured (GITHUB_TOKEN / GITHUB_REPO)")
                artifacts = self.tracer.load_artifacts(run_id)
                missing = {"design_spec", "patch", "test_report", "verdict"} - set(artifacts)
                if missing:
                    raise AgentError(f"cannot deliver: missing artifacts {sorted(missing)}")
                workspace = existing_workspace(
                    self._toy_repo(), Path(s.workspaces_dir), run_id, s.github_base_branch
                )
                with self.tracer.span(
                    "git.push",
                    kind="step",
                    input={"branch": workspace.branch, "remote": s.github_remote},
                ) as push_span:
                    push_branch(workspace, s.github_token, s.github_remote)
                    push_span.set_output({"pushed": workspace.branch})
                pr = Delivery(self.llm, self.tracer, s).deliver(
                    run_id,
                    DesignSpec.model_validate(artifacts["design_spec"]),
                    Patch.model_validate(artifacts["patch"]),
                    TestReport.model_validate(artifacts["test_report"]),
                    Verdict.model_validate(artifacts["verdict"]),
                    workspace,
                )
                self._artifact("pull_request", pr)
                self._transition(State.AWAITING_APPROVAL, State.DONE, "human approved; PR opened")
                root.set_output({"pull_request": pr})
                # The branch now lives on GitHub; the local checkout is no longer needed.
                self._cleanup(run_id, "pull request opened")
            self.tracer.set_run_status(run_id, "done")
        except SpendCapExceeded as exc:
            self.tracer.set_run_status(run_id, "stopped", reason=str(exc))
        except RunTimeout as exc:
            limit = f"{s.run_timeout_s:g}s"
            self.tracer.set_run_status(run_id, "timed_out", reason=f"{exc} (limit {limit})")
        except Exception as exc:
            self.tracer.set_run_status(run_id, "error", error=f"{type(exc).__name__}: {exc}")

    def _cleanup(self, run_id: str, why: str) -> None:
        """Remove a run's worktree and local branch. Best effort: a cleanup problem must never
        change a run's outcome, so failures are recorded as an event instead of raised."""
        s = self.settings
        try:
            removed = cleanup_workspace(self._toy_repo(), Path(s.workspaces_dir), run_id)
            self.tracer.emit("workspace.cleaned", {"why": why, **removed}, run_id=run_id)
        except Exception as exc:
            self.tracer.emit(
                "workspace.cleanup_failed",
                {"why": why, "error": f"{type(exc).__name__}: {exc}"},
                run_id=run_id,
            )

    def cleanup_rejected(self) -> bool:
        """Worker idle task: clean up runs a human rejected (the API cannot touch the repo).
        Returns True if it cleaned anything."""
        run_ids = self.tracer.rejected_runs_to_clean()
        for run_id in run_ids:
            self._cleanup(run_id, "rejected by a human")
        return bool(run_ids)

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

        while state not in (State.DONE, State.FAILED, State.AWAITING_APPROVAL, State.NO_CHANGES):
            check_deadline()
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
                result = implementation.implement(run_id, spec, workspace, attempt, feedback)
                if isinstance(result, NoChanges):
                    # Not a crash and not a failed attempt: nothing needed (or nothing was
                    # possible). Surfaced as its own terminal outcome with the agent's claim.
                    reason = result.explanation.strip()[:500] or "implementation made no changes"
                    state = self._transition(state, State.NO_CHANGES, reason)
                    continue
                patch = result
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
                    if s.delivery_enabled:
                        state = self._transition(
                            state, State.AWAITING_APPROVAL, "reviewer approved; waiting for a human"
                        )
                    else:
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
