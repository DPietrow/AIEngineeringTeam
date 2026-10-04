"""Retry transient LLM API failures with exponential backoff and jitter.

Only failures that are worth retrying are retried: dropped connections, timeouts, 408/409,
429 (rate limited), and 5xx including 529 (overloaded). Everything else (bad request, bad key,
permission) fails immediately: retrying cannot fix it and would only burn time.

Every retry is recorded as an `llm.retry` event, so a slow run is explainable from its trace
instead of looking mysteriously idle. Sleeping honours the run's wall-clock deadline.
"""

import contextlib
import random
import time
from collections.abc import Callable
from typing import Any, TypeVar

try:  # the SDK is optional: fake mode and tests need neither it nor a key
    import anthropic
except ImportError:  # pragma: no cover
    anthropic = None  # type: ignore[assignment]

from .deadline import RunTimeout, remaining
from .tracing import NoActiveRun, Tracer

T = TypeVar("T")
MAX_RETRY_AFTER_S = 60.0
RETRYABLE_STATUS = {408, 409, 429}


def is_retryable(exc: BaseException) -> bool:
    if anthropic is not None and isinstance(exc, anthropic.APIConnectionError):  # + timeouts
        return True
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status in RETRYABLE_STATUS or status >= 500
    return False


def _retry_after(exc: BaseException) -> float | None:
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if not headers:
        return None
    try:
        return float(headers.get("retry-after"))
    except (TypeError, ValueError):
        return None


def backoff_delay(
    attempt: int, base_s: float, max_s: float, exc: BaseException, rng: random.Random
) -> float:
    """Delay before retry number `attempt` (1-based). The server's retry-after wins when given;
    otherwise full jitter over an exponentially growing window, which avoids retry stampedes."""
    hinted = _retry_after(exc)
    if hinted is not None:
        return max(0.0, min(hinted, MAX_RETRY_AFTER_S))
    return rng.uniform(0, min(max_s, base_s * 2 ** (attempt - 1)))


def call_with_retries(  # noqa: UP047 (TypeVar kept: PEP 695 syntax needs a newer interpreter)
    fn: Callable[[], T],
    *,
    tracer: Tracer | None,
    max_retries: int = 4,
    base_delay_s: float = 1.0,
    max_delay_s: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
    label: str = "llm",
) -> T:
    rng = rng or random.Random()
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as exc:
            if not is_retryable(exc) or attempt >= max_retries:
                raise
            attempt += 1
            delay = backoff_delay(attempt, base_delay_s, max_delay_s, exc, rng)
            left = remaining()
            if left is not None and delay >= left:
                raise RunTimeout(
                    f"run time limit reached while retrying a failing {label} call "
                    f"({type(exc).__name__})"
                ) from exc
            _record(tracer, label, attempt, max_retries, delay, exc)
            sleep(delay)


def _record(
    tracer: Tracer | None,
    label: str,
    attempt: int,
    max_retries: int,
    delay: float,
    exc: BaseException,
) -> None:
    if tracer is None:
        return
    data: dict[str, Any] = {
        "what": label,
        "attempt": attempt,
        "max_retries": max_retries,
        "delay_s": round(delay, 2),
        "error": type(exc).__name__,
        "status_code": getattr(exc, "status_code", None),
    }
    # Outside a run (a script) retrying still works, it is just unrecorded.
    with contextlib.suppress(NoActiveRun):
        tracer.emit("llm.retry", data)
