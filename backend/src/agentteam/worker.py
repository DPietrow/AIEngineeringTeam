"""Worker process: claims pending runs from SQLite and executes them.

Run with:  uv run python -m agentteam.worker
"""

import logging
import time

from .budget import SpendGuard
from .config import Settings
from .llm import LLM, AnthropicLLM, FakeLLM
from .orchestrator import Orchestrator
from .prompts import config_hash
from .redact import register_secret
from .tracing import Tracer

log = logging.getLogger("agentteam.worker")


def build_llm(tracer: Tracer, settings: Settings) -> LLM:
    use_real = settings.llm_mode == "anthropic" or (
        settings.llm_mode == "auto" and settings.anthropic_api_key
    )
    if use_real:
        if not settings.anthropic_api_key:
            raise SystemExit("LLM_MODE=anthropic but ANTHROPIC_API_KEY is not set")
        register_secret(settings.anthropic_api_key)
        log.info("LLM: Anthropic %s", settings.llm_model)
        return AnthropicLLM(tracer, api_key=settings.anthropic_api_key, model=settings.llm_model)
    log.warning("LLM: FAKE mode (no API key). Runs use canned responses and cost nothing.")
    return FakeLLM(tracer)


def build_tracer(settings: Settings) -> Tracer:
    guard = SpendGuard(
        settings.database_path,
        run_cap_usd=settings.run_spend_cap_usd,
        global_cap_usd=settings.global_spend_cap_usd,
    )
    return Tracer(settings.database_path, guard=guard)


def work_once(tracer: Tracer, orchestrator: Orchestrator) -> bool:
    claimed = tracer.claim_next_run()
    if claimed is None:
        return False
    run_id, task = claimed
    log.info("run %s started", run_id)
    orchestrator.execute(run_id, task)
    log.info("run %s finished", run_id)
    return True


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    settings = Settings.from_env()
    tracer = build_tracer(settings)
    orchestrator = Orchestrator(tracer, build_llm(tracer, settings))
    log.info(
        "worker ready (db=%s, config=%s)", settings.database_path, config_hash(settings.llm_model)
    )
    while True:
        if not work_once(tracer, orchestrator):
            time.sleep(0.5)


if __name__ == "__main__":
    main()
