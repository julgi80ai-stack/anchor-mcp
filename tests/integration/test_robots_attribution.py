# SPDX-License-Identifier: Apache-2.0
"""robots 판정의 귀속·사유·회복 축 (D-097).

축 둘을 함께 연다.

① **판정의 주체**: 막힌 것이 입력 URL의 호스트인가, 리다이렉트를 따라간
   **다른 호스트**인가. 한 서버 픽스처로는 이 축이 존재하지 않는다 — 두
   호스트가 있어야 "아무것도 금지하지 않은 사이트가 금지한 것으로 기록됐다"를
   관측할 수 있다.
② **거부의 사유**: 규칙을 읽었고 그 규칙이 막은 것(`explicit`)인가, 규칙을
   물어보지 못한 것(`unavailable` — 5xx)인가. robots.txt가 늘 200을 주는
   픽스처에는 이 축이 없다.

여기에 시간 축 하나가 더 붙는다 — 규칙이 풀린 **뒤**에도 기록이 False로
남는가(회복). 저장소 전체에 `True`로 되돌리는 호출처가 없었다.
"""

from __future__ import annotations

import pytest

from anchor.config import Config
from anchor.errors import RobotsDisallowed
from anchor.service import Anchor

DISALLOW_ALL = "User-agent: *\nDisallow: /\n"


def _anchor(tmp_path, name="store.db") -> Anchor:
    config = Config(
        db_path=tmp_path / name,
        rate_limit_rps=1000.0,
        retry_backoff_base=0.01,
        # 판정을 캐시하면 "규칙이 바뀌었다"는 축 자체가 사라진다.
        robots_ttl_seconds=0,
    )
    return Anchor(db_path=config.db_path, config=config)


def _document_by_url(anchor: Anchor, url: str):
    for document in anchor.list_documents().documents:
        if document.url.endswith(url):
            return document
    raise AssertionError(f"문서가 없다: {url}")


def test_denial_at_redirect_destination_is_not_charged_to_the_origin(
    tmp_path, fixture_server_factory
):
    """① 막은 것은 목적지 호스트다 — 출발지 문서에 금지 표시를 남기면 거짓이다."""
    origin_url, origin_state = fixture_server_factory()
    other_url, other_state = fixture_server_factory()

    with _anchor(tmp_path) as anchor:
        anchor.fetch(f"{origin_url}/article")   # 출발지 문서 생성 (robots 허용)
        anchor.fetch(f"{other_url}/article")    # 목적지 문서 생성 (robots 허용)

        origin_state.redirects = {"/article": f"{other_url}/article"}
        other_state.robots = DISALLOW_ALL       # 목적지 호스트만 금지로 돌아섰다

        with pytest.raises(RobotsDisallowed):
            anchor.fetch(f"{origin_url}/article", max_age=0)

        origin = [d for d in anchor.list_documents().documents if d.url.startswith(origin_url)][0]
        destination = [d for d in anchor.list_documents().documents if d.url.startswith(other_url)][0]
        assert origin.robots_allowed is True, (
            "아무것도 금지하지 않은 출발지 호스트가 금지된 것으로 기록됐다"
        )
        assert destination.robots_allowed is False, (
            "정작 금지한 호스트의 문서에는 기록이 남지 않았다"
        )


def test_temporarily_unavailable_robots_is_not_recorded_as_owner_denial(
    tmp_path, fixture_server
):
    """② 5xx는 '소유자가 막았다'가 아니라 '물어보지 못했다'이다.

    이 열은 소유자의 규칙이 막는가를 담는다. 일시 장애를 explicit과 같이
    적으면, 장애가 끝난 뒤에도 그 사이트는 '금지'로 남는다.
    """
    base_url, state = fixture_server
    with _anchor(tmp_path) as anchor:
        anchor.fetch(f"{base_url}/article")
        state.robots_status = 503

        with pytest.raises(RobotsDisallowed) as excinfo:
            anchor.fetch(f"{base_url}/article", max_age=0)
        assert excinfo.value.reason == "unavailable"

        document = _document_by_url(anchor, "/article")
        assert document.robots_allowed is True, (
            "판정 불능(5xx)이 소유자의 명시적 거부와 같이 기록됐다"
        )


def test_explicit_denial_is_recorded_and_recovers_when_rules_change(tmp_path, fixture_server):
    """③ 명시적 거부는 기록하되, 규칙이 풀리면 되돌아와야 한다.

    되돌리는 호출처가 없으면 사이트가 허용으로 바뀐 뒤에도 영구히 금지로
    남는다 — 사실이 아닌 상태가 캐시에 고정된다.
    """
    base_url, state = fixture_server
    with _anchor(tmp_path) as anchor:
        anchor.fetch(f"{base_url}/article")
        assert _document_by_url(anchor, "/article").robots_allowed is True

        state.robots = DISALLOW_ALL
        with pytest.raises(RobotsDisallowed) as excinfo:
            anchor.fetch(f"{base_url}/article", max_age=0)
        assert excinfo.value.reason == "explicit"
        assert _document_by_url(anchor, "/article").robots_allowed is False

        state.robots = "User-agent: *\nDisallow: /private\n"  # 규칙이 풀렸다
        anchor.fetch(f"{base_url}/article", max_age=0)
        assert _document_by_url(anchor, "/article").robots_allowed is True, (
            "robots가 다시 허용하는데 기록은 영구 금지로 남았다"
        )
