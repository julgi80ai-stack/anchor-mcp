# SPDX-License-Identifier: Apache-2.0
"""재시도의 **간격** 회귀 (D-275, SPEC §5.4 "정직한 클라이언트").

D-238이 `max(요청값, 백오프)`를 지정값 우선으로 바꾸면서 하한이 통째로
사라졌다. 그 결과 `Retry-After: 0`·음수·과거 HTTP-date를 준 서버를 **6ms
안에 4번** 두드린다 — 429를 준 서버에게 특히 그렇다. 정중함은 이 프로젝트의
전제이지 기능이 아니다.

축은 `Retry-After` **값**이다: 없음 / `0` / 음수 / 과거 날짜 / 해석 불가 /
정상값 / 상한 초과. 횟수만 세는 픽스처로는 이 결함이 보이지 않으므로
(조치 전에도 횟수는 같다) **요청 시각**을 축으로 연다.
"""

from __future__ import annotations

from email.utils import format_datetime
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from anchor.config import Config
from anchor.errors import FetchFailed
from anchor.service import Anchor


# 이 시험의 예절 단위. 실제 배포값(1 rps → 1초)으로 재면 한 케이스에 3초가
# 들어 축을 다 열 수 없다. 하한이 **설정에서 나온다**는 사실은 그대로 재고,
# 벽시계 상한 대신 "간격이 하한 이상인가"만 단언한다 (D-224의 교훈).
_RPS = 20.0            # → 호스트 간격 0.05초
_BACKOFF_BASE = 0.01   # → 우리 백오프의 바닥 0.01초
_FLOOR = max(_BACKOFF_BASE, 1.0 / _RPS)


def _anchor(tmp_path: Path) -> Anchor:
    config = Config(
        db_path=tmp_path / "store.db",
        rate_limit_rps=_RPS,
        retry_backoff_base=_BACKOFF_BASE,
    )
    return Anchor(db_path=config.db_path, config=config)


def _past_http_date() -> str:
    return format_datetime(
        datetime.now(timezone.utc) - timedelta(days=3650), usegmt=True
    )


def _hits(state, path: str) -> list[float]:
    return [when for where, when in state.request_times if where == path]


def _gaps(times: list[float]) -> list[float]:
    return [b - a for a, b in zip(times, times[1:])]


# (이름, 상태, Retry-After, 기대 요청 수, 하한을 지켜야 하는가)
_CASES = [
    ("429 지정 없음", 429, None, 4, True),
    ("429 Retry-After: 0", 429, "0", 4, True),
    ("429 Retry-After: -5", 429, "-5", 4, True),
    ("429 Retry-After: 과거 날짜", 429, _past_http_date(), 4, True),
    ("429 Retry-After: 상한 초과", 429, "3600", 1, False),
    ("403 Retry-After: 0", 403, "0", 4, True),
    ("403 지정 없음", 403, None, 1, False),
    ("403 Retry-After: 해석 불가", 403, "곧", 1, False),
]


@pytest.mark.parametrize(
    "label,status,retry_after,expected_requests,must_wait",
    _CASES,
    ids=[case[0] for case in _CASES],
)
def test_retries_never_go_below_the_hosts_own_interval(
    tmp_path, fixture_server, label, status, retry_after, expected_requests, must_wait
):
    base, state = fixture_server
    state.status_override = status
    state.status_override_headers = {} if retry_after is None else {"Retry-After": retry_after}

    with _anchor(tmp_path) as anchor:
        with pytest.raises(FetchFailed):
            anchor.fetch(f"{base}/article", max_age=0)

    times = _hits(state, "/article")
    assert len(times) == expected_requests, f"{label}: 요청 횟수가 달라졌다 ({times})"
    if not must_wait:
        return
    gaps = _gaps(times)
    assert gaps, f"{label}: 재시도가 없다"
    assert min(gaps) >= _FLOOR * 0.9, (
        f"{label}: 재시도 간격 {min(gaps)*1000:.1f}ms가 하한 "
        f"{_FLOOR*1000:.0f}ms보다 짧다 — 429를 준 서버를 그 간격으로 두드리고 있다"
    )


def test_a_server_specified_wait_is_still_honoured_when_longer_than_the_floor(
    tmp_path, fixture_server
):
    """하한은 바닥이지 덮개가 아니다 — 지정값이 더 길면 지정값을 지킨다."""
    base, state = fixture_server
    state.status_override = 429
    state.status_override_headers = {"Retry-After": "0.3"}

    with _anchor(tmp_path) as anchor:
        with pytest.raises(FetchFailed):
            anchor.fetch(f"{base}/article", max_age=0)

    gaps = _gaps(_hits(state, "/article"))
    assert gaps and min(gaps) >= 0.27, f"지정한 0.3초를 지키지 않았다 ({gaps})"
