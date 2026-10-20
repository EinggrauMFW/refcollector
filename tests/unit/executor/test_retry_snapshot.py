from __future__ import annotations

import random

from refcollector.config import Limits
from refcollector.contracts.ports import snapshot_hash
from refcollector.domain.enums import NetworkMode, StepState, StepType
from refcollector.domain.models import Project, Step
from refcollector.errors import Cancelled, InvalidInput, ProviderUnavailable, RateLimited
from refcollector.executor.retry import backoff, classify
from refcollector.executor.snapshot import build_snapshot, reuse_key

LIMITS = Limits()


def test_retryable_with_attempts_left_waits_backoff() -> None:
    d = classify(ProviderUnavailable("x"), attempt=1, max_attempts=5, limits=LIMITS, rng=random.Random(1))
    assert d.state is StepState.RETRY_WAIT
    assert 0 < d.delay_s <= 1.0


def test_retry_after_is_honoured() -> None:
    d = classify(RateLimited("x", retry_after=7), attempt=1, max_attempts=5, limits=LIMITS)
    assert d.state is StepState.RETRY_WAIT
    assert d.delay_s == 7


def test_retry_after_beyond_exhaustion_threshold_is_not_retried() -> None:
    d = classify(RateLimited("x", retry_after=120), attempt=1, max_attempts=5, limits=LIMITS)
    assert d.state is StepState.FAILED_RETRYABLE


def test_attempts_exhausted_is_failed_retryable() -> None:
    d = classify(ProviderUnavailable("x"), attempt=5, max_attempts=5, limits=LIMITS)
    assert d.state is StepState.FAILED_RETRYABLE


def test_timeout_is_retryable() -> None:
    assert classify(TimeoutError(), attempt=1, max_attempts=5, limits=LIMITS).state is StepState.RETRY_WAIT


def test_permanent_and_unknown_errors() -> None:
    assert classify(InvalidInput("x"), 1, 5, LIMITS).state is StepState.FAILED_PERMANENT
    assert classify(ValueError("x"), 1, 5, LIMITS).state is StepState.FAILED_PERMANENT
    assert classify(Cancelled("x"), 1, 5, LIMITS).state is StepState.CANCELLED


def test_backoff_grows_and_is_capped() -> None:
    rng = random.Random(0)
    for attempt in range(1, 10):
        d = backoff(attempt, base_s=1.0, cap_s=30.0, rng=rng)
        assert 0 < d <= min(30.0, 2 ** (attempt - 1))


def test_snapshot_and_reuse_key(settings) -> None:  # type: ignore[no-untyped-def]
    p = Project(project_id="prj_1", name="p", network_mode=NetworkMode.OFFLINE)
    snap = build_snapshot(settings, p)
    assert snap.network_mode is NetworkMode.OFFLINE
    assert snap.provider_allowlist == settings.enabled_providers
    assert snap.contact_email == settings.email
    h = snapshot_hash(snap)
    assert h == snapshot_hash(build_snapshot(settings, p))
    step = Step(
        step_id="stp_1",
        job_id="job_1",
        project_id="prj_1",
        step_type=StepType.RESOLVE,
        subject_id="occ_1",
        input_fingerprint="fp",
        snapshot_hash=h,
    )
    assert reuse_key(step) == ("resolve", "fp", h)
