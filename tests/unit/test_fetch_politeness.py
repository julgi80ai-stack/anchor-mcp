# SPDX-License-Identifier: Apache-2.0
"""네트워크 예절 회귀 (D-002~006 / D-009 / D-010).

SPEC §5.4의 "정직한 클라이언트"는 기능이 아니라 전제다. 우회하지 않는 것
못지않게, 서버가 지정한 대기를 지키고 상한을 지키는 것이 계약이다.
"""

from __future__ import annotations

import threading
import time

import pytest

import anchor.fetcher.ratelimit as ratelimit
from anchor.fetcher.client import MAX_RETRY_AFTER_SECONDS, _parse_retry_after
from anchor.fetcher.ratelimit import HostRateLimiter
from tests import fake_clock


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
    """서버가 지정하면 **그 값**이다 — 우리 백오프로 늘리지 않는다 (D-238).

    예전에는 `max(requested, backoff)`였다. 더 정중한 방향이지만 서버의
    지시를 덮어쓰는 것이고, 그만큼 호출자를 붙잡는다: `Retry-After: 1`을 준
    서버에게 백오프 1·2·4초가 총 7초를 기다리게 했다(실측 7,015ms). SPEC §10
    "실패는 빨라야 한다"는 추가 대기를 **서버가 지정한 만큼만** 허용한다.
    """
    from anchor.fetcher.client import ConditionalFetcher

    fetcher = ConditionalFetcher(
        client=None, user_agent="t", max_content_bytes=1024, retry_backoff_base=1.0
    )
    assert fetcher._retry_delay(_Response("30"), 0) == 30.0
    # 백오프가 지정값보다 커지는 회차에서도 지정값을 지킨다 — 여기가 옛
    # `max()`와 갈리는 자리다(attempt 2의 백오프는 4초).
    assert fetcher._retry_delay(_Response("1"), 0) == 1.0
    assert fetcher._retry_delay(_Response("1"), 2) == 1.0
    # 지정이 없으면 그때만 우리가 물러설 시간을 정한다.
    assert fetcher._retry_delay(_Response(None), 2) == 4.0


def test_a_zero_or_stale_retry_after_still_has_a_floor():
    """지정을 존중하는 것과 예절을 버리는 것은 다르다 (D-275).

    축은 `Retry-After` **값**이다: 0 / 음수 / 과거 HTTP-date / 해석 불가 /
    지정 없음 / 정상값 / 상한 초과. 조치 전에는 앞의 셋이 전부 대기 0이 되어
    429를 준 서버를 6ms 안에 네 번 두드렸다(실측 4회/7.3ms).
    """
    from email.utils import format_datetime
    from datetime import datetime, timedelta, timezone

    from anchor.fetcher.client import ConditionalFetcher

    stale = format_datetime(
        datetime.now(timezone.utc) - timedelta(days=3650), usegmt=True
    )
    # 바닥은 설정에서 온다 — 백오프 기준값과 호스트 간격 중 큰 쪽.
    fetcher = ConditionalFetcher(
        client=None,
        user_agent="t",
        max_content_bytes=1024,
        retry_backoff_base=0.01,
        min_retry_delay=0.5,
    )
    for raw in ("0", "-5", stale, "0.001"):
        assert fetcher._retry_delay(_Response(raw), 0) == 0.5, raw
    # 해석 불가·지정 없음도 바닥 아래로 내려가지 않는다.
    assert fetcher._retry_delay(_Response("곧"), 0) == 0.5
    assert fetcher._retry_delay(_Response(None), 0) == 0.5
    # 바닥은 덮개가 아니다 — 지정이 더 길면 지정을 지킨다.
    assert fetcher._retry_delay(_Response("5"), 0) == 5.0
    # 상한 초과는 여전히 재시도하지 않는다.
    assert fetcher._retry_delay(_Response("3600"), 0) is None


def test_a_floor_beyond_the_ceiling_stops_retrying_rather_than_sleeping_forever():
    """설정이 아주 느린 호스트(0.005 rps → 200초)를 지시해도 상한이 이긴다."""
    from anchor.fetcher.client import ConditionalFetcher

    fetcher = ConditionalFetcher(
        client=None,
        user_agent="t",
        max_content_bytes=1024,
        retry_backoff_base=1.0,
        min_retry_delay=200.0,
    )
    assert fetcher._retry_delay(_Response("1"), 0) is None
    assert fetcher._retry_delay(_Response(None), 0) is None


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


def test_sequential_rate_is_unchanged(monkeypatch):
    """버스트 뒤의 간격은 정확히 1/rate다 — 주입한 시계로 못박는다 (D-224).

    예전에는 벽시계 구간(`0.25 <= elapsed < 0.6`)으로 쟀다. 상한은 "느리면
    실패"이고 그건 계약이 아니다 — macOS CI 러너에서 코드 변경 없이 0.624s·
    0.740s로 빨개졌다(3.11·3.12). 반대로 상한을 넉넉히 풀면 잡아야 할 회귀
    (버스트 무시 → 0.5s, 대기량 오산 → 0.4~0.6s)가 그 안에 숨는다. 시계를
    주입하면 잠든 **값**을 직접 단언할 수 있어 상한이 필요 없어지고 판별력은
    올라간다. 실제로 시간이 흐르는지는 위의 실시간 시험이 지킨다.
    """
    clock = fake_clock.install(monkeypatch, ratelimit)
    limiter = HostRateLimiter(rate=10.0, burst=2)
    for _ in range(5):
        limiter.acquire("example.invalid")
    # 버스트 2개는 즉시, 나머지 3개는 각각 정확히 1/rate = 0.1초를 기다린다.
    assert clock.slept == [pytest.approx(0.1)] * 3, clock.slept
    fake_clock.assert_close(clock.elapsed, 0.3, what="5회 acquire의 총 대기")


# -- D-237: 403은 초대받았을 때만 다시 두드린다 ------------------------------


def _response(status: int, retry_after: str | None = None):
    from anchor.fetcher.client import FetchResponse

    return FetchResponse(
        status=status,
        content=b"",
        content_type="text/html",
        etag=None,
        last_modified=None,
        retry_after=retry_after,
        final_url="https://example.test/a",
        bytes_down=0,
        elapsed_ms=0,
    )


def test_plain_403_is_not_retried():
    """안티봇의 항구적 거부에 매번 7초(1+2+4)를 쓸 이유가 없다 (SPEC §5.4)."""
    from anchor.fetcher.client import _may_retry

    assert _may_retry(_response(403)) is False


def test_403_with_a_retry_after_is_an_invitation():
    """서버가 대기 시간을 명시했으면 그 말을 무시하는 것도 정직하지 않다."""
    from anchor.fetcher.client import _may_retry

    assert _may_retry(_response(403, "5")) is True


def test_403_with_an_unreadable_retry_after_is_not_an_invitation():
    from anchor.fetcher.client import _may_retry

    assert _may_retry(_response(403, "곧")) is False


def test_429_is_still_retried():
    """레이트 제한의 규약은 그대로다 — 물러났다 다시 오는 것이 그 뜻이다."""
    from anchor.fetcher.client import _may_retry

    assert _may_retry(_response(429)) is True
    assert _may_retry(_response(429, "3")) is True
