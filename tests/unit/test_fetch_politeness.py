# SPDX-License-Identifier: Apache-2.0
"""네트워크 예절 회귀 (D-002~006 / D-009 / D-010).

SPEC §5.4의 "정직한 클라이언트"는 기능이 아니라 전제다. 우회하지 않는 것
못지않게, 서버가 지정한 대기를 지키고 상한을 지키는 것이 계약이다.
"""

from __future__ import annotations

import threading
import time

import pytest

from anchor.fetcher.client import MAX_RETRY_AFTER_SECONDS, _parse_retry_after
from anchor.fetcher.ratelimit import HostRateLimiter


# -- D-004 / D-005 Retry-After ----------------------------------------------


def test_numeric_retry_after():
    assert _parse_retry_after("120") == 120.0
    assert _parse_retry_after(" 30 ") == 30.0
    assert _parse_retry_after("-5") == 0.0


def test_http_date_retry_after_is_understood():
    """이전에는 HTTP-date를 무시하고 백오프로 물러섰다 (D-004)."""
    from email.utils import format_datetime
    from datetime import datetime, timedelta, timezone

    future = datetime.now(timezone.utc) + timedelta(seconds=90)
    seconds = _parse_retry_after(format_datetime(future, usegmt=True))
    assert seconds is not None and 80 < seconds < 100


def test_malformed_retry_after_is_rejected_not_raised():
    """`nan`이 time.sleep까지 흘러 ValueError를 내던 문제 (D-005)."""
    for raw in ("nan", "inf", "-inf", "soon", ""):
        assert _parse_retry_after(raw) is None


class _Response:
    def __init__(self, retry_after):
        self.retry_after = retry_after


def test_long_retry_after_stops_retrying(monkeypatch):
    """서버가 상한보다 긴 대기를 지정하면 몰래 일찍 두드리지 않는다."""
    from anchor.fetcher.client import ConditionalFetcher

    fetcher = ConditionalFetcher(
        client=None, user_agent="t", max_content_bytes=1024, retry_backoff_base=1.0
    )
    assert fetcher._retry_delay(_Response("3600"), 0) is None
    assert fetcher._retry_delay(_Response(str(MAX_RETRY_AFTER_SECONDS - 1)), 0) is not None


def test_retry_after_wins_over_backoff():
    from anchor.fetcher.client import ConditionalFetcher

    fetcher = ConditionalFetcher(
        client=None, user_agent="t", max_content_bytes=1024, retry_backoff_base=1.0
    )
    assert fetcher._retry_delay(_Response("30"), 0) == 30.0


# -- D-009 / D-010 레이트 제한 ----------------------------------------------


def test_zero_or_negative_rate_is_rejected_at_construction():
    """이전에는 첫 페치 도중 ZeroDivisionError로 죽었다 (D-010)."""
    with pytest.raises(ValueError):
        HostRateLimiter(rate=0.0)
    with pytest.raises(ValueError):
        HostRateLimiter(rate=-1.0)
    with pytest.raises(ValueError):
        HostRateLimiter(rate=1.0, burst=0)


def test_concurrent_acquire_respects_rate():
    """D-009: 동시 호출자가 한꺼번에 깨어나 버스트가 되살아나면 안 된다."""
    limiter = HostRateLimiter(rate=10.0, burst=2)
    started = time.monotonic()
    threads = [
        threading.Thread(target=limiter.acquire, args=("example.invalid",))
        for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    elapsed = time.monotonic() - started
    # 버스트 2개는 즉시, 나머지 6개는 0.1초 간격 → 최소 0.55초
    assert elapsed >= 0.5, f"레이트 제한이 무력화됐다 ({elapsed:.2f}s)"


def test_sequential_rate_is_unchanged():
    limiter = HostRateLimiter(rate=10.0, burst=2)
    started = time.monotonic()
    for _ in range(5):
        limiter.acquire("example.invalid")
    assert 0.25 <= time.monotonic() - started < 0.6
