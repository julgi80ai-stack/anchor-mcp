# SPDX-License-Identifier: Apache-2.0
"""리다이렉트 처리 회귀 (D-001 / D-007).

감사 실증: ① 목적지에 robots 재판정이 없어 금지 경로·타 호스트를 그대로
가져왔다 ② 조회키(입력 URL)와 저장키(최종 URL)가 갈려 캐시가 영구히
빗나갔다 — v0.1 완료 기준("두 번째 호출 네트워크 0바이트") 위반.
"""

from __future__ import annotations

import pytest

from anchor.errors import RobotsDisallowed

QUOTE = "링크는 살아 있지만 내용이 바뀌는 인용 표류가 가장 위험하다."


def test_redirect_destination_is_robots_checked(fixture_server, anchor):
    """D-001: 리다이렉터를 거쳐도 금지 경로는 가져오면 안 된다."""
    base_url, state = fixture_server
    state.redirects = {"/go": "/private/report"}

    with pytest.raises(RobotsDisallowed):
        anchor.fetch(f"{base_url}/go")

    assert not any(path.startswith("/private") for path in state.requests), (
        f"금지 경로를 실제로 가져왔다: {state.requests}"
    )


def test_redirected_url_hits_cache_on_second_call(fixture_server, anchor):
    """D-007: 리다이렉트되는 URL도 두 번째 호출은 네트워크 0바이트."""
    base_url, state = fixture_server
    state.redirects = {"/old": "/article"}

    first = anchor.fetch(f"{base_url}/old")
    assert first.outcome == "created"
    assert first.network.bytes_down > 0
    requests_after_first = list(state.requests)

    second = anchor.fetch(f"{base_url}/old")
    assert second.outcome == "cache_hit", "리다이렉트 URL이 캐시를 못 찾았다"
    assert second.network.bytes_down == 0
    assert state.requests == requests_after_first, "서버에 요청이 나갔다"


def test_conditional_request_uses_stored_validators_after_redirect(fixture_server, anchor):
    """D-007: 재확인 시 ETag가 실려 304를 받아야 한다."""
    base_url, state = fixture_server
    state.redirects = {"/old": "/article"}

    anchor.fetch(f"{base_url}/old")
    result = anchor.fetch(f"{base_url}/old", max_age=0)
    assert result.outcome == "not_modified", "조건부 요청이 무력화됐다"
    # 본문은 한 바이트도 받지 않았지만, 리다이렉트 홉의 안내 문서는 실제로
    # 받았다 — 회계는 실제로 나간 트래픽을 말한다 (D-134).
    assert result.network.bytes_down == len(state.redirect_body)


def test_original_url_resolves_for_cite_and_timemap(fixture_server, anchor):
    """D-007: 사용자가 처음 넣은 URL로도 문서를 참조할 수 있어야 한다."""
    base_url, state = fixture_server
    state.redirects = {"/old": "/article"}
    anchor.fetch(f"{base_url}/old")

    cited = anchor.cite(f"{base_url}/old", QUOTE)
    assert cited.anchor_id
    timemap = anchor.get_timemap(f"{base_url}/old")
    assert 'rel="original"' in timemap["body"]


def test_trailing_slash_redirect_is_absorbed(fixture_server, anchor):
    """SPEC §5.1 5단계: 말미 슬래시는 리다이렉트 응답을 우선한다."""
    base_url, state = fixture_server
    state.redirects = {"/article": "/article/", "/article/": None}
    # `/article/`은 실제 문서를 서빙하도록 맵에서 제거
    del state.redirects["/article/"]
    state.redirects["/article"] = "/article/"

    # 픽스처 서버는 `/article/`도 기사 본문을 준다
    first = anchor.fetch(f"{base_url}/article")
    assert first.outcome == "created"
    second = anchor.fetch(f"{base_url}/article")
    assert second.outcome == "cache_hit"
    assert second.network.bytes_down == 0


def test_redirect_loop_is_reported_not_followed_forever(fixture_server, anchor):
    base_url, state = fixture_server
    state.redirects = {"/a": "/b", "/b": "/a"}
    from anchor.errors import FetchFailed

    with pytest.raises(FetchFailed):
        anchor.fetch(f"{base_url}/a")


def test_robots_unavailable_blocks_fetch(fixture_server, tmp_path):
    """D-003: robots.txt가 5xx면 규칙을 알 수 없으므로 전면 거부 (RFC 9309)."""
    from anchor.config import Config
    from anchor.service import Anchor

    base_url, state = fixture_server
    state.redirects = {"/robots.txt": None}
    del state.redirects["/robots.txt"]

    # robots.txt만 503을 내도록 상태를 바꾼다.
    original_robots = state.robots
    state.robots = original_robots  # 본문은 그대로, 상태코드만 아래에서 조작

    class _Failing(dict):
        pass

    config = Config(db_path=tmp_path / "r.db", rate_limit_rps=1000.0)
    with Anchor(db_path=config.db_path, config=config) as instance:
        # 픽스처는 robots에 상태코드를 못 넣으므로 저장소에 5xx를 직접 심는다.
        from anchor.models import utcnow_iso

        instance._repository.set_robots(base_url, "", 503, utcnow_iso())
        with pytest.raises(RobotsDisallowed):
            instance.fetch(f"{base_url}/article")
    assert not any(p == "/article" for p in state.requests)


def test_non_200_body_respects_size_limit(fixture_server, tmp_path):
    """D-002: 거대한 오류 페이지를 통째로 버퍼링하지 않는다."""
    from anchor.config import Config
    from anchor.errors import ContentTooLarge
    from anchor.service import Anchor

    base_url, state = fixture_server
    state.status_override = 500
    state.status_override_with_body = True
    state.html = "x" * 200_000

    config = Config(
        db_path=tmp_path / "big.db", rate_limit_rps=1000.0, max_content_bytes=1024
    )
    with Anchor(db_path=config.db_path, config=config) as instance:
        with pytest.raises(ContentTooLarge):
            instance.fetch(f"{base_url}/article")
    state.status_override = None
    state.status_override_with_body = False
