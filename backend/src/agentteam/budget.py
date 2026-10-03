"""Spend cap: checked before every LLM span starts."""

from dataclasses import dataclass
from pathlib import Path

from .db import connect


class SpendCapExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class SpendGuard:
    db_path: str | Path
    run_cap_usd: float
    global_cap_usd: float

    def costs(self, run_id: str) -> tuple[float, float]:
        conn = connect(self.db_path)
        try:
            run = conn.execute(
                "SELECT COALESCE(total_cost_usd, 0) FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            total = conn.execute("SELECT COALESCE(SUM(total_cost_usd), 0) FROM runs").fetchone()
        finally:
            conn.close()
        return (run[0] if run else 0.0), total[0]

    def check(self, run_id: str) -> None:
        run_cost, global_cost = self.costs(run_id)
        if run_cost >= self.run_cap_usd:
            raise SpendCapExceeded(
                f"run spend ${run_cost:.4f} reached the per-run cap ${self.run_cap_usd:.2f}"
            )
        if global_cost >= self.global_cap_usd:
            raise SpendCapExceeded(
                f"global spend ${global_cost:.4f} reached the global cap ${self.global_cap_usd:.2f}"
            )
