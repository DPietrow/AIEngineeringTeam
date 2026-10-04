"""Worker process: claims pending runs from SQLite and executes them.

Run with:  uv run python -m agentteam.worker
"""

import contextlib
import logging
import os
import socket
import threading
import time

from .budget import SpendGuard
from .config import Settings
from .llm import LLM, AnthropicLLM, FakeLLM
from .orchestrator import Orchestrator
from .prompts import config_hash
from .redact import register_secret
from .tracing import Tracer

log = logging.getLogger("agentteam.worker")
SWEEP_INTERVAL_S = 30.0  # how often an idle or busy loop looks for runs whose worker died


def build_llm(tracer: Tracer, settings: Settings) -> LLM:
    use_real = settings.llm_mode == "anthropic" or (
        settings.llm_mode == "auto" and settings.anthropic_api_key
    )
    if use_real:
        if not settings.anthropic_api_key:
            raise SystemExit("LLM_MODE=anthropic but ANTHROPIC_API_KEY is not set")
        register_secret(settings.anthropic_api_key)
        llm = AnthropicLLM(
            tracer,
            api_key=settings.anthropic_api_key,
            model=settings.llm_model,
            model_overrides=dict(settings.model_overrides),
            prompt_caching=settings.prompt_caching,
            max_retries=settings.llm_max_retries,
            retry_base_delay_s=settings.llm_retry_base_delay_s,
            timeout_s=settings.llm_timeout_s,
        )
        log.info(
            "LLM: Anthropic %s, prompt caching %s",
            llm.model_label,
            "on" if settings.prompt_caching else "off",
        )
        return llm
    log.warning("LLM: FAKE mode (no API key). Runs use canned responses and cost nothing.")
    return FakeLLM(tracer)


def build_tracer(settings: Settings) -> Tracer:
    guard = SpendGuard(
        settings.database_path,
        run_cap_usd=settings.run_spend_cap_usd,
        global_cap_usd=settings.global_spend_cap_usd,
    )
    return Tracer(settings.database_path, guard=guard)


class Heartbeat:
    """Renews a run's lease from a background thread while the worker is busy.

    The pipeline blocks the worker's main thread for minutes, so renewal has to come from
    another thread. If the process dies, the heartbeat dies with it, the lease expires, and
    another worker's recovery sweep picks the run up.
    """

    def __init__(self, tracer: Tracer, run_id: str, worker_id: str, lease_s: float) -> None:
        self.tracer, self.run_id, self.worker_id, self.lease_s = tracer, run_id, worker_id, lease_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._beat, daemon=True, name="lease-heartbeat")

    def _beat(self) -> None:
        while not self._stop.wait(self.lease_s / 3):
            try:
                if not self.tracer.renew_lease(self.run_id, self.worker_id, self.lease_s):
                    log.warning("run %s: lease lost (recovered by another worker?)", self.run_id)
                    return
            except Exception:  # a transient DB error must not kill the run; try again next beat
                log.exception("run %s: lease renewal failed", self.run_id)

    def __enter__(self) -> "Heartbeat":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=5)


def work_once(
    tracer: Tracer,
    orchestrator: Orchestrator,
    worker_id: str | None = None,
    lease_s: float = 60.0,
) -> bool:
    """Do one unit of work. With a worker_id, the run is leased and heartbeated."""
    # Human-approved runs first: someone is waiting on them and they are cheap.
    approved = tracer.claim_next_delivery(worker_id, lease_s)
    if approved is not None:
        log.info("run %s approved; delivering", approved)
        with _leased(tracer, approved, worker_id, lease_s):
            orchestrator.deliver(approved)
        log.info("run %s delivery finished", approved)
        return True
    claimed = tracer.claim_next_run(worker_id, lease_s)
    if claimed is None:
        # Idle: tidy up after runs a human rejected (the API process cannot touch the repo).
        return orchestrator.cleanup_rejected()
    run_id, task = claimed
    log.info("run %s started", run_id)
    with _leased(tracer, run_id, worker_id, lease_s):
        orchestrator.execute(run_id, task)
    log.info("run %s finished", run_id)
    return True


def _leased(tracer: Tracer, run_id: str, worker_id: str | None, lease_s: float):
    if worker_id is None:
        return contextlib.nullcontext()
    return Heartbeat(tracer, run_id, worker_id, lease_s)


def recover(tracer: Tracer) -> None:
    for r in tracer.recover_orphans():
        log.warning("recovered run %s: %s -> %s (worker died)", r["run_id"], r["was"], r["now"])


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    settings = Settings.from_env()
    register_secret(settings.github_token)
    tracer = build_tracer(settings)
    orchestrator = Orchestrator(tracer, build_llm(tracer, settings), settings)
    log.info(
        "worker ready (db=%s, config=%s, delivery=%s)",
        settings.database_path,
        config_hash(settings.model_signature),
        f"on -> {settings.github_repo}" if settings.delivery_enabled else "off",
    )
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    log.info(
        "worker id %s, run timeout %gs, lease %gs",
        worker_id,
        settings.run_timeout_s,
        settings.worker_lease_s,
    )
    recover(tracer)  # a previous worker may have died mid-run
    last_sweep = time.monotonic()
    while True:
        if not work_once(tracer, orchestrator, worker_id, settings.worker_lease_s):
            time.sleep(0.5)
        if time.monotonic() - last_sweep >= SWEEP_INTERVAL_S:
            recover(tracer)
            last_sweep = time.monotonic()


if __name__ == "__main__":
    main()
