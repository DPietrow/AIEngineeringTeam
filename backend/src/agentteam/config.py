import os
from dataclasses import dataclass

from .llm import DEFAULT_MODEL


@dataclass(frozen=True)
class Settings:
    database_path: str = "data/agentteam.db"
    run_spend_cap_usd: float = 1.00
    global_spend_cap_usd: float = 10.00
    llm_mode: str = "auto"  # auto | anthropic | fake
    llm_model: str = DEFAULT_MODEL
    anthropic_api_key: str | None = None
    toy_repo_path: str | None = None
    workspaces_dir: str = "data/workspaces"
    max_architect_steps: int = 8
    max_implementation_steps: int = 20

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_path=os.environ.get("DATABASE_PATH", cls.database_path),
            run_spend_cap_usd=float(os.environ.get("RUN_SPEND_CAP_USD", cls.run_spend_cap_usd)),
            global_spend_cap_usd=float(
                os.environ.get("GLOBAL_SPEND_CAP_USD", cls.global_spend_cap_usd)
            ),
            llm_mode=os.environ.get("LLM_MODE", cls.llm_mode).lower(),
            llm_model=os.environ.get("LLM_MODEL", cls.llm_model),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
            toy_repo_path=os.environ.get("TOY_REPO_PATH") or None,
            workspaces_dir=os.environ.get("WORKSPACES_DIR", cls.workspaces_dir),
            max_architect_steps=int(os.environ.get("MAX_ARCHITECT_STEPS", cls.max_architect_steps)),
            max_implementation_steps=int(
                os.environ.get("MAX_IMPLEMENTATION_STEPS", cls.max_implementation_steps)
            ),
        )
