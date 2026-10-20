"""WP-02: retry classes, exponential backoff with jitter, Retry-After. Spec 6.5."""

from __future__ import annotations

import random
from dataclasses import dataclass

from refcollector.config import Limits
from refcollector.domain.enums import StepState
from refcollector.errors import Cancelled, RcError

BACKOFF_CAP_S = 60.0


@dataclass(frozen=True)
class RetryDecision:
    state: StepState  # retry_wait, failed_retryable, failed_permanent or cancelled
    delay_s: float = 0.0  # only for retry_wait


def backoff(
    attempt: int, *, base_s: float = 1.0, cap_s: float = BACKOFF_CAP_S, rng: random.Random | None = None
) -> float:
    """Exponential backoff with full jitter: uniform(0, min(cap, base * 2**(attempt-1)))."""
    ceiling = min(cap_s, base_s * 2 ** max(attempt - 1, 0))
    return (rng or random).uniform(ceiling / 10, ceiling)


def classify(
    exc: BaseException,
    attempt: int,
    max_attempts: int,
    limits: Limits,
    *,
    base_s: float = 1.0,
    rng: random.Random | None = None,
) -> RetryDecision:
    """Map a handler failure to the next step state. `attempt` is the attempt that just failed.

    - Cancelled -> cancelled.
    - Timeout or retryable RcError -> retry_wait while attempts are left, else failed_retryable.
      Retry-After is honoured as the delay. Retry-After above
      `provider_exhausted_retry_after_s` = provider exhausted: no in-job retry, failed_retryable
      (control_job(retry_failed) can re-run it later).
    - Anything else -> failed_permanent.
    """
    if isinstance(exc, Cancelled):
        return RetryDecision(StepState.CANCELLED)
    retryable = isinstance(exc, TimeoutError) or (isinstance(exc, RcError) and exc.retryable)
    if not retryable:
        return RetryDecision(StepState.FAILED_PERMANENT)
    retry_after = exc.retry_after if isinstance(exc, RcError) else None
    if retry_after is not None and retry_after > limits.provider_exhausted_retry_after_s:
        return RetryDecision(StepState.FAILED_RETRYABLE)
    if attempt >= max_attempts:
        return RetryDecision(StepState.FAILED_RETRYABLE)
    delay = retry_after if retry_after is not None else backoff(attempt, base_s=base_s, rng=rng)
    return RetryDecision(StepState.RETRY_WAIT, delay)
