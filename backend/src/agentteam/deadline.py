"""Cooperative stop conditions for a run: a wall-clock deadline and a lost lease.

The pipeline is synchronous and single-threaded, so neither can interrupt a running call.
Instead both are checked at every safe point: before each agent step (each model call), each
orchestrator loop iteration, before the git push, and before sleeping between API retries. The
worst overshoot is one step: bounded by the API timeout (120 s), the MCP call timeout (60 s) and
the sandbox timeout (120 s), which is why those exist too. Both travel in contextvars, like the
tracer's run id, so no function signatures change.

Lease fencing, for more than one worker. A worker holds a lease on its run and a heartbeat thread
renews it. If the worker stalls (a laptop sleeps, a VM is paused) past the lease, another
worker's sweep recovers the run and may hand it to someone else. When the stalled worker wakes
it must stop at once and write nothing: `Fence` is how it finds out. The heartbeat sets
`lost` the moment a renewal fails, `check_deadline` then raises LeaseLost, and
`Tracer.set_run_status` refuses to write a status for a run the worker no longer owns.
"""

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

_deadline: ContextVar[float | None] = ContextVar("run_deadline", default=None)


class RunTimeout(RuntimeError):
    """The run exceeded its wall-clock budget."""


class LeaseLost(RuntimeError):
    """This worker no longer owns the run (it was recovered or reassigned). Stop, write nothing."""


@dataclass
class Fence:
    worker_id: str
    lost: threading.Event = field(default_factory=threading.Event)


_fence: ContextVar[Fence | None] = ContextVar("lease_fence", default=None)


@contextmanager
def run_deadline(timeout_s: float | None) -> Iterator[None]:
    """Start the clock. None or 0 disables the limit only when None; 0 means already expired."""
    token = _deadline.set(None if timeout_s is None else time.monotonic() + timeout_s)
    try:
        yield
    finally:
        _deadline.reset(token)


@contextmanager
def lease_fence(fence: Fence) -> Iterator[Fence]:
    """Mark the current context as running under a worker's lease."""
    token = _fence.set(fence)
    try:
        yield fence
    finally:
        _fence.reset(token)


def current_fence() -> Fence | None:
    return _fence.get()


def remaining() -> float | None:
    """Seconds left, or None if no deadline is set."""
    d = _deadline.get()
    return None if d is None else d - time.monotonic()


def check_deadline() -> None:
    fence = _fence.get()
    if fence is not None and fence.lost.is_set():
        raise LeaseLost(f"worker {fence.worker_id} no longer owns this run")
    left = remaining()
    if left is not None and left <= 0:
        raise RunTimeout("run exceeded its wall-clock time limit")
