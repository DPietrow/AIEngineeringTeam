import os
from dataclasses import dataclass

from .llm import DEFAULT_MODEL
from .sandbox import DEFAULT_IMAGE, SandboxConfig


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

    # Per-agent step caps (tool-use turns).
    max_architect_steps: int = 10
    max_implementation_steps: int = 20
    max_testing_steps: int = 8
    max_review_steps: int = 10

    # Failure-loop caps: 3 retries per loop, then the run fails instead of looping forever.
    max_test_retries: int = 3
    max_review_rounds: int = 3

    # Sandbox for the Testing agent. "local" has NO isolation; it is for tests/dev only.
    sandbox_mode: str = "docker"  # docker | local
    sandbox_image: str = DEFAULT_IMAGE
    sandbox_memory: str = "512m"
    sandbox_cpus: str = "1.0"
    sandbox_pids_limit: int = 128
    sandbox_timeout_s: int = 120

    def sandbox(self) -> SandboxConfig:
        return SandboxConfig(
            image=self.sandbox_image,
            memory=self.sandbox_memory,
            cpus=self.sandbox_cpus,
            pids_limit=self.sandbox_pids_limit,
            timeout_s=self.sandbox_timeout_s,
            mode=self.sandbox_mode,
        )

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ.get
        return cls(
            database_path=env("DATABASE_PATH", cls.database_path),
            run_spend_cap_usd=float(env("RUN_SPEND_CAP_USD", cls.run_spend_cap_usd)),
            global_spend_cap_usd=float(env("GLOBAL_SPEND_CAP_USD", cls.global_spend_cap_usd)),
            llm_mode=env("LLM_MODE", cls.llm_mode).lower(),
            llm_model=env("LLM_MODEL", cls.llm_model),
            anthropic_api_key=env("ANTHROPIC_API_KEY") or None,
            toy_repo_path=env("TOY_REPO_PATH") or None,
            workspaces_dir=env("WORKSPACES_DIR", cls.workspaces_dir),
            max_architect_steps=int(env("MAX_ARCHITECT_STEPS", cls.max_architect_steps)),
            max_implementation_steps=int(
                env("MAX_IMPLEMENTATION_STEPS", cls.max_implementation_steps)
            ),
            max_testing_steps=int(env("MAX_TESTING_STEPS", cls.max_testing_steps)),
            max_review_steps=int(env("MAX_REVIEW_STEPS", cls.max_review_steps)),
            max_test_retries=int(env("MAX_TEST_RETRIES", cls.max_test_retries)),
            max_review_rounds=int(env("MAX_REVIEW_ROUNDS", cls.max_review_rounds)),
            sandbox_mode=env("SANDBOX_MODE", cls.sandbox_mode).lower(),
            sandbox_image=env("SANDBOX_IMAGE", cls.sandbox_image),
            sandbox_memory=env("SANDBOX_MEMORY", cls.sandbox_memory),
            sandbox_cpus=env("SANDBOX_CPUS", cls.sandbox_cpus),
            sandbox_pids_limit=int(env("SANDBOX_PIDS_LIMIT", cls.sandbox_pids_limit)),
            sandbox_timeout_s=int(env("SANDBOX_TIMEOUT_S", cls.sandbox_timeout_s)),
        )
