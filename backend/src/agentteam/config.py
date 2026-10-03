import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_path: str = "data/agentteam.db"
    run_spend_cap_usd: float = 1.00
    global_spend_cap_usd: float = 10.00

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_path=os.environ.get("DATABASE_PATH", cls.database_path),
            run_spend_cap_usd=float(os.environ.get("RUN_SPEND_CAP_USD", cls.run_spend_cap_usd)),
            global_spend_cap_usd=float(
                os.environ.get("GLOBAL_SPEND_CAP_USD", cls.global_spend_cap_usd)
            ),
        )
