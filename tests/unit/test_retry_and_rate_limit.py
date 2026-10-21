import httpx
import pytest

from app.core.rate_limit import FixedWindowLimiter, TokenBucket
from app.core.retry import RetryableError, RetryPolicy, call_with_retry, is_retryable


def _status_error(code: int, headers=None) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://x.example")
    response = httpx.Response(code, request=request, headers=headers or {})
    return httpx.HTTPStatusError("err", request=request, response=response)


def test_retries_transient_errors_with_exponential_backoff():
    sleeps, attempts = [], []

    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise httpx.ConnectError("down")
        return "ok"

    policy = RetryPolicy(max_attempts=4, base_delay=1, max_delay=30, jitter=False)
    assert call_with_retry(flaky, policy, sleep=sleeps.append) == "ok"
    assert sleeps == [1, 2]


def test_does_not_retry_client_errors():
    calls = []

    def bad():
        calls.append(1)
        raise _status_error(404)

    with pytest.raises(httpx.HTTPStatusError):
        call_with_retry(bad, RetryPolicy(max_attempts=5), sleep=lambda _: None)
    assert len(calls) == 1


def test_gives_up_after_max_attempts():
    sleeps = []
    with pytest.raises(RetryableError):
        call_with_retry(
            lambda: (_ for _ in ()).throw(RetryableError("x")),
            RetryPolicy(max_attempts=3, jitter=False),
            sleep=sleeps.append,
        )
    assert len(sleeps) == 2


def test_honours_retry_after_header():
    retryable, after = is_retryable(_status_error(429, {"Retry-After": "7"}))
    assert retryable and after == 7.0
    assert RetryPolicy(max_delay=5).delay_for(1, after) == 5  # capped
    assert is_retryable(_status_error(503))[0]
    assert not is_retryable(ValueError("x"))[0]


def test_token_bucket_blocks_until_tokens_refill():
    now = [0.0]
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        now[0] += seconds

    bucket = TokenBucket(2.0, capacity=1, clock=lambda: now[0], sleep=sleep)
    assert bucket.acquire() == 0
    waited = bucket.acquire()
    assert waited == pytest.approx(0.5)
    assert not bucket.try_acquire()


def test_fixed_window_limiter():
    now = [0.0]
    limiter = FixedWindowLimiter(2, 60, clock=lambda: now[0])
    assert limiter.hit("a")[0] and limiter.hit("a")[0]
    allowed, remaining, reset = limiter.hit("a")
    assert not allowed and remaining == 0 and reset == 60
    assert limiter.hit("b")[0]  # per-client
    now[0] = 61
    assert limiter.hit("a")[0]
