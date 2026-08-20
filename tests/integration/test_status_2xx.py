# SPDX-License-Identifier: Apache-2.0
"""2xx 성공 응답의 축 (D-282, RFC 9110 §15.3).

`_ingest_200`으로 가는 문은 `status == 200` 하나였고, 304를 뺀 **나머지
전부**가 `FetchFailed`였다. 200만 있는 픽스처에서는 이 축이 존재하지 않는다.

- **203 Non-Authoritative Information**은 원 서버의 200 응답을 변환 프록시가
  **변형한 표현 그 자체**이며 캐시 가능하다(RFC 9110 §15.3.4). 회사 게이트웨이·
  통신사 압축 프록시 뒤의 사용자에게는 이것이 그 문서의 평범한 응답이다.
  본문을 다 받아 놓고 버리면 그 사용자는 인용을 **영영 재검증하지 못한다** —
  404·410이 아니므로 `verify`는 영구히 `UNREACHABLE`이다.
- **202 Accepted**의 본문은 요청한 리소스가 아니라 처리 상태 모니터다. 판본으로
  저장하면 안 된다. 다만 "실패"라고 말하는 것도 사실이 아니다 — 서버가 아직
  표현을 주지 않았을 뿐이다.
- **201·204·206**은 요청한 표현이 아니다. 지금 처리가 옳다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from anchor.config import Config
from anchor.errors import FetchFailed
from anchor.service import Anchor

from .conftest import article_html

QUOTE = "학술 문헌이 참조한 웹 콘텐츠의 약 75%가 3년 안에 어느 정도 변경된 것으로"


def _anchor(tmp_path: Path) -> Anchor:
    config = Config(
        db_path=tmp_path / "store.db",
        rate_limit_rps=1000.0,
        retry_backoff_base=0.01,
    )
    return Anchor(db_path=config.db_path, config=config)


def _serve(state, status: int) -> None:
    state.status_override = None if status == 200 else status
    state.status_override_with_body = True


@pytest.mark.parametrize("status", [201, 202, 204, 206])
def test_a_2xx_that_is_not_the_requested_representation_is_not_stored(
    tmp_path, fixture_server, status
):
    base, state = fixture_server
    _serve(state, status)
    with _anchor(tmp_path) as anchor:
        with pytest.raises(FetchFailed) as caught:
            anchor.fetch(f"{base}/article", max_age=0)
    assert caught.value.http_status == status


def test_202_says_the_server_has_not_produced_a_representation_yet(
    tmp_path, fixture_server
):
    """`202`의 본문은 처리 상태 모니터다. 저장하지 않는 판단은 옳지만,
    "HTTP 202"라는 한 줄은 그 사실을 흐린다 — 사용자는 오류로 읽는다."""
    base, state = fixture_server
    _serve(state, 202)
    with _anchor(tmp_path) as anchor:
        with pytest.raises(FetchFailed) as caught:
            anchor.fetch(f"{base}/article", max_age=0)
    message = str(caught.value)
    assert "202" in message
    assert "Accepted" in message, "202를 다른 실패와 같은 문장으로 말하고 있다"


def test_203_is_the_document_transformed_by_a_proxy_not_a_failure(
    tmp_path, fixture_server
):
    """변환 프록시 뒤의 사용자에게 203은 그 문서의 평범한 응답이다."""
    base, state = fixture_server
    _serve(state, 203)
    with _anchor(tmp_path) as anchor:
        result = anchor.fetch(f"{base}/article", max_age=0)
        stored, text = anchor.get_version(result.version_id)

    assert result.outcome == "created"
    assert result.char_count > 0
    assert QUOTE in text
    assert stored.http_status == 203, "변형된 표현이라는 사실이 기록되지 않았다"


def test_203_keeps_working_across_refetches_and_verification(tmp_path, fixture_server):
    """받은 본문을 버리면 그 프록시 뒤의 인용은 영구히 `UNREACHABLE`이 된다."""
    base, state = fixture_server
    _serve(state, 203)
    with _anchor(tmp_path) as anchor:
        anchor.fetch(f"{base}/article", max_age=0)
        cited = anchor.cite(f"{base}/article", QUOTE)
        again = anchor.fetch(f"{base}/article", max_age=0)
        state.html = article_html(extra_sentence=" 프록시 뒤에서도 개정은 일어난다.")
        changed = anchor.fetch(f"{base}/article", max_age=0)
        report = anchor.verify(anchor_ids=[cited.anchor_id])

    assert again.outcome == "unchanged"
    assert changed.outcome == "changed"
    assert report.summary.get("UNREACHABLE", 0) == 0, "203이 재검증을 막고 있다"
    assert report.summary.get("INTACT", 0) == 1


def test_203_is_accounted_as_a_success_not_an_error(tmp_path, fixture_server):
    base, state = fixture_server
    _serve(state, 203)
    with _anchor(tmp_path) as anchor:
        anchor.fetch(f"{base}/article", max_age=0)
        stats = anchor.cache_stats()["last_30d"]
    assert stats["errors"] == 0
    assert stats["created"] == 1


def test_a_transformed_robots_txt_still_binds_us(tmp_path, fixture_server):
    """같은 프록시는 robots.txt도 203으로 내려준다 (D-282).

    RFC 9309 §2.3.1.1의 "successful access"는 2xx 전부다. 200만 성공으로 보면
    203으로 온 `Disallow`가 "규칙 없음"이 되어 **사이트 소유자의 규칙을 통째로
    무시**한다 — 4xx를 제한 없음으로 읽는 것(§2.3.1.3)과 전혀 다른 일이다.
    """
    from anchor.errors import RobotsDisallowed

    base, state = fixture_server
    state.robots = "User-agent: *\nDisallow: /\n"
    state.robots_status = 203
    with _anchor(tmp_path) as anchor:
        with pytest.raises(RobotsDisallowed):
            anchor.fetch(f"{base}/article", max_age=0)


def test_a_transformed_archive_replay_is_still_a_memento(tmp_path, fixture_server):
    """아카이브 재생본도 같은 프록시를 지난다 (D-282)."""
    base, state = fixture_server
    state.status_override = 404  # 원본은 죽었다
    state.archive_html = article_html(nonce="archived")
    state.archive_replay_status = 203
    config = Config(
        db_path=tmp_path / "store.db",
        rate_limit_rps=1000.0,
        retry_backoff_base=0.01,
        archive_fallback_enabled=True,
        archive_aggregator=base,
    )
    with Anchor(db_path=config.db_path, config=config) as anchor:
        result = anchor.fetch(f"{base}/article", max_age=0)
    assert result.outcome == "archive"
    assert result.source == "archive"
