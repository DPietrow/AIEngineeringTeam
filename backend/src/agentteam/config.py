import os
import shlex
from dataclasses import dataclass

from .llm import DEFAULT_MODEL
from .sandbox import DEFAULT_IMAGE, SandboxConfig

AGENTS = ("architect", "implementation", "testing", "review", "delivery")


@dataclass(frozen=True)
class Settings:
    database_path: str = "data/agentteam.db"
    run_spend_cap_usd: float = 1.00
    global_spend_cap_usd: float = 10.00
    llm_mode: str = "auto"  # auto | anthropic | fake
    llm_model: str = DEFAULT_MODEL
    anthropic_api_key: str | None = None
    toy_repo_path: str | None = None
    # Evals need a pristine, frozen copy of the toy repo (no remote), because the live repo
    # accumulates merged agent PRs that turn eval cases into no-ops. Falls back to toy_repo_path.
    eval_toy_repo_path: str | None = None
    workspaces_dir: str = "data/workspaces"

    # Per-agent model overrides, e.g. (("review", "claude-sonnet-5-5"),). An agent with no entry
    # uses llm_model. Set via MODEL_ARCHITECT / MODEL_IMPLEMENTATION / MODEL_TESTING /
    # MODEL_REVIEW / MODEL_DELIVERY.
    model_overrides: tuple[tuple[str, str], ...] = ()
    # Prompt caching: cache the growing conversation prefix between agent turns (cuts input
    # cost on long loops). PROMPT_CACHING=0 turns it off, e.g. to A/B its effect.
    prompt_caching: bool = True

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

    # Resilience. A run that exceeds run_timeout_s ends "timed_out". Transient API failures
    # (429, 5xx, 529, dropped connections) are retried with backoff before a run is failed.
    run_timeout_s: float = 900.0
    llm_max_retries: int = 4
    llm_retry_base_delay_s: float = 1.0
    llm_timeout_s: float = 120.0
    # A worker holds a lease on the run it is executing and renews it every lease_s / 3. If the
    # worker dies, the lease expires and the run is recovered (see Tracer.recover_orphans).
    worker_lease_s: float = 60.0

    # Delivery (GitHub PR behind a human gate). Disabled unless BOTH token and repo are set;
    # when disabled, an approved review ends the run as 'done' exactly as before.
    github_token: str | None = None
    github_repo: str | None = None  # "owner/name"
    github_base_branch: str = "main"
    github_remote: str = "origin"
    max_delivery_steps: int = 5
    # How to launch the GitHub MCP server. The default is the official Docker image.
    github_mcp_command: str = "docker"
    github_mcp_args: tuple[str, ...] = (
        "run",
        "-i",
        "--rm",
        "-e",
        "GITHUB_PERSONAL_ACCESS_TOKEN",
        "-e",
        "GITHUB_TOOLS",
        "ghcr.io/github/github-mcp-server",
    )

    # API authentication. Auth is enforced when api_password is set. jwt_secret signs the login
    # tokens and must be a separate, long random value (never derived from the password, or
    # anyone holding a token could guess the password offline). Tokens last jwt_ttl_s (default
    # one year); rotating jwt_secret logs everyone out. auth_required refuses to start without
    # a password, so a production deploy cannot silently run open.
    api_password: str | None = None
    jwt_secret: str | None = None
    jwt_ttl_s: int = 365 * 24 * 3600
    auth_required: bool = False
    # Browser origins allowed to call the API cross-origin (e.g. the Vercel frontend), comma
    # separated in CORS_ORIGINS. Empty = same-origin only. trust_proxy: the API sits behind
    # exactly one reverse proxy (Caddy/nginx/Render) whose X-Forwarded-For can be believed.
    cors_origins: tuple[str, ...] = ()
    trust_proxy: bool = False

    @property
    def model_signature(self) -> str:
        """Model setup as one string, hashed into the config hash so runs with different
        per-agent models are never confused. With no overrides it is just the model name, so
        existing config hashes do not change."""
        extra = "|".join(f"{a}={m}" for a, m in sorted(self.model_overrides))
        return f"{self.llm_model}|{extra}" if extra else self.llm_model

    def model_for(self, agent: str) -> str:
        return dict(self.model_overrides).get(agent, self.llm_model)

    @property
    def delivery_enabled(self) -> bool:
        return bool(self.github_token and self.github_repo and "/" in self.github_repo)

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
            model_overrides=tuple(
                (agent, env(f"MODEL_{agent.upper()}", ""))
                for agent in AGENTS
                if env(f"MODEL_{agent.upper()}", "")
            ),
            prompt_caching=env("PROMPT_CACHING", "1").lower() not in ("0", "false", "no", "off"),
            anthropic_api_key=env("ANTHROPIC_API_KEY") or None,
            toy_repo_path=env("TOY_REPO_PATH") or None,
            eval_toy_repo_path=env("EVAL_TOY_REPO_PATH") or None,
            workspaces_dir=env("WORKSPACES_DIR", cls.workspaces_dir),
            max_architect_steps=int(env("MAX_ARCHITECT_STEPS", cls.max_architect_steps)),
            max_implementation_steps=int(
                env("MAX_IMPLEMENTATION_STEPS", cls.max_implementation_steps)
            ),
            max_testing_steps=int(env("MAX_TESTING_STEPS", cls.max_testing_steps)),
            max_review_steps=int(env("MAX_REVIEW_STEPS", cls.max_review_steps)),
            max_test_retries=int(env("MAX_TEST_RETRIES", cls.max_test_retries)),
            max_review_rounds=int(env("MAX_REVIEW_ROUNDS", cls.max_review_rounds)),
            run_timeout_s=float(env("RUN_TIMEOUT_S", cls.run_timeout_s)),
            llm_max_retries=int(env("LLM_MAX_RETRIES", cls.llm_max_retries)),
            llm_retry_base_delay_s=float(env("LLM_RETRY_BASE_DELAY_S", cls.llm_retry_base_delay_s)),
            llm_timeout_s=float(env("LLM_TIMEOUT_S", cls.llm_timeout_s)),
            worker_lease_s=float(env("WORKER_LEASE_S", cls.worker_lease_s)),
            github_token=env("GITHUB_TOKEN") or None,
            github_repo=env("GITHUB_REPO") or None,
            github_base_branch=env("GITHUB_BASE_BRANCH", cls.github_base_branch),
            github_remote=env("GITHUB_REMOTE", cls.github_remote),
            max_delivery_steps=int(env("MAX_DELIVERY_STEPS", cls.max_delivery_steps)),
            github_mcp_command=env("GITHUB_MCP_COMMAND", cls.github_mcp_command),
            github_mcp_args=(
                tuple(shlex.split(env("GITHUB_MCP_ARGS", ""), posix=True)) or cls.github_mcp_args
            ),
            sandbox_mode=env("SANDBOX_MODE", cls.sandbox_mode).lower(),
            sandbox_image=env("SANDBOX_IMAGE", cls.sandbox_image),
            sandbox_memory=env("SANDBOX_MEMORY", cls.sandbox_memory),
            sandbox_cpus=env("SANDBOX_CPUS", cls.sandbox_cpus),
            sandbox_pids_limit=int(env("SANDBOX_PIDS_LIMIT", cls.sandbox_pids_limit)),
            sandbox_timeout_s=int(env("SANDBOX_TIMEOUT_S", cls.sandbox_timeout_s)),
            api_password=env("API_PASSWORD") or None,
            jwt_secret=env("JWT_SECRET") or None,
            jwt_ttl_s=int(env("JWT_TTL_S", cls.jwt_ttl_s)),
            auth_required=env("AUTH_REQUIRED", "0").lower() in ("1", "true", "yes", "on"),
            cors_origins=tuple(
                o.strip().rstrip("/") for o in env("CORS_ORIGINS", "").split(",") if o.strip()
            ),
            trust_proxy=env("TRUST_PROXY", "0").lower() in ("1", "true", "yes", "on"),
        )
