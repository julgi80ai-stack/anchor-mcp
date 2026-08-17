# SPDX-License-Identifier: Apache-2.0
"""페치 계층 판정 상태(GONE/UNREACHABLE)의 케이스 확장 (SPEC §12: 7상태
각각 최소 5케이스 — 매처 상태 5종은 tests/unit/test_matcher.py가 맡는다)."""

from __future__ import annotations

import pytest

from anchor.config import Config
from anchor.models import utcnow_iso
from anchor.service import Anchor

FIVE_QUOTES = [
    "AI 크롤러 트래픽의 절반 이상이 변하지 않은 페이지를 다시 가져오는 데 쓰인다.",
    "조건부 요청만으로도 상당 부분을 제거할 수 있다.",
    "학술 문헌이 참조한 웹 콘텐츠의 약 75%가 3년 안에 어느 정도 변경된 것으로",
    "링크는 살아 있지만 내용이 바뀌는 인용 표류가 가장 위험하다.",
    "조건부 요청과 본문 해시를 함께 쓰면 재페치와 재파싱을 모두 줄일 수 있다.",
]


def cite_all(anchor, base_url):
    fetched = anchor.fetch(f"{base_url}/article")
    for quote in FIVE_QUOTES:
        anchor.cite(fetched.document_id, quote)
    return fetched


def test_gone_five_anchors_on_404(fixture_server, anchor):
    base_url, state = fixture_server
    cite_all(anchor, base_url)
    state.status_override = 404

    report = anchor.verify()
    assert report.checked == 5
    assert report.summary["GONE"] == 5
    assert len(report.attention) == 5  # GONE은 전부 조치 대상


def test_gone_on_410(fixture_server, anchor):
    base_url, state = fixture_server
    fetched = anchor.fetch(f"{base_url}/article")
    anchor.cite(fetched.document_id, FIVE_QUOTES[3])
    state.status_override = 410

    report = anchor.verify()
    assert report.summary["GONE"] == 1
    (document,) = anchor.list_documents()
    assert document.status == "gone"


@pytest.mark.parametrize("status", [402, 403, 429])
def test_unreachable_on_http_failure(fixture_server, anchor, status):
    base_url, state = fixture_server
    fetched = anchor.fetch(f"{base_url}/article")
    anchor.cite(fetched.document_id, FIVE_QUOTES[0])
    state.status_override = status

    report = anchor.verify()
    assert report.summary["UNREACHABLE"] == 1
    assert report.attention == ()  # UNREACHABLE은 재시도 대상이지 조치 대상이 아니다


def test_unreachable_on_robots_revocation(fixture_server, anchor):
    """사이트가 robots로 접근을 막으면 확인 불가 — 인용 무효가 아니다."""
    base_url, state = fixture_server
    fetched = anchor.fetch(f"{base_url}/article")
    anchor.cite(fetched.document_id, FIVE_QUOTES[0])
    # robots 캐시(24h)를 거부로 교체해 다음 검증에서 차단되게 한다.
    anchor._repository.set_robots(base_url, "User-agent: *\nDisallow: /\n", 200, utcnow_iso())

    report = anchor.verify()
    assert report.summary["UNREACHABLE"] == 1


def test_unreachable_on_timeout(fixture_server, tmp_path):
    base_url, state = fixture_server
    config = Config(
        db_path=tmp_path / "timeout.db",
        rate_limit_rps=1000.0,
        retry_backoff_base=0.01,
        timeout_seconds=0.5,
    )
    with Anchor(db_path=config.db_path, config=config) as anchor:
        fetched = anchor.fetch(f"{base_url}/article")
        anchor.cite(fetched.document_id, FIVE_QUOTES[0])
        state.response_delay = 1.2

        report = anchor.verify()
        assert report.summary["UNREACHABLE"] == 1
    state.response_delay = 0.0
