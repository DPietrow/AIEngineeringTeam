"""Per-run wall-clock deadline, enforced cooperatively.

The pipeline is synchronous and single-threaded, so a deadline cannot interrupt a running call.
Instead it is checked at every safe point: before each agent step (each model call), each
orchestrator loop iteration, and before sleeping between API retries. The worst overshoot is
one step: bounded by the API timeout (120 s), the MCP call timeout (60 s) and the sandbox
timeout (120 s), which is why those exist too. The deadline travels in a contextvar, like the
tracer's run id, so no function signatures change.
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_deadline: ContextVar[float | None] = ContextVar("run_deadline", default=None)


class RunTimeout(RuntimeError):
    """The run exceeded its wall-clock budget."""


@contextmanager
def run_deadline(timeout_s: float | None) -> Iterator[None]:
    """Start the clock. None or 0 disables the limit only when None; 0 means already expired."""
    token = _deadline.set(None if timeout_s is None else time.monotonic() + timeout_s)
    try:
        yield
    finally:
        _deadline.reset(token)


def remaining() -> float | None:
    """Seconds left, or None if no deadline is set."""
    d = _deadline.get()
    return None if d is None else d - time.monotonic()


def check_deadline() -> None:
    left = remaining()
    if left is not None and left <= 0:
        raise RunTimeout("run exceeded its wall-clock time limit")
