# SPDX-License-Identifier: Apache-2.0
"""robots 판정이 **실제로 지켜지는가** (2차 감사 D-090·094·095·096·098).

이 프로젝트는 "403은 403으로 보고한다"를 정체성으로 내건다 (SPEC §1.3, §5.4).
그 약속은 robots를 읽는 코드가 흔한 구성에서 규칙을 통째로 흘려버리는 순간
말뿐이 된다 — 사이트 소유자가 `Disallow: /`라고 썼는데 가져오는 것은,
우회하지 않겠다는 선언보다 나쁘다. 선언이 있으니 아무도 확인하지 않는다.
"""

from __future__ import annotations

import pytest

from anchor.config import Config
from anchor.errors import ConfigError, RobotsDisallowed
from anchor.service import Anchor


def _config(tmp_path, **overrides) -> Config:
    return Config(db_path=tmp_path / "store.db", rate_limit_rps=1000.0, **overrides)


# -- D-094: robots.txt가 3xx일 때 ---------------------------------------------


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_redirected_robots_is_followed_not_discarded(tmp_path, fixture_server, status):
    """robots.txt가 리다이렉트되면 **따라가야** 한다 (D-094).

    `status != 200`이라는 이유로 본문을 버리면 `Disallow: /`조차 무시된다.
    http→https, CDN 이관, 캐노니컬 정리 — 전부 평범한 구성이고 그때마다
    사이트 전체를 가져가게 된다. 게다가 그 판정이 24시간 캐시된다.
    RFC 9309 §2.3.1.2는 최소 5홉 추종을 요구한다.
    """
    base_url, state = fixture_server
    state.robots = "User-agent: *\nDisallow: /\n"
    state.robots_status = status
    state.robots_location = f"{base_url}/robots-canonical.txt"
    with Anchor(config=_config(tmp_path)) as anchor:
        with pytest.raises(RobotsDisallowed) as caught:
            anchor.fetch(f"{base_url}/article")
    assert caught.value.reason == "explicit", "소유자의 명시적 거부를 흘려버렸다"


def test_robots_redirect_loop_is_treated_as_unavailable(tmp_path, fixture_server):
    """추종에는 끝이 있어야 한다 — 무한 루프는 "규칙을 모른다"이다."""
    base_url, state = fixture_server
    state.robots_status = 302
    state.robots_location = f"{base_url}/robots.txt"  # 자기 자신
    with Anchor(config=_config(tmp_path)) as anchor:
        with pytest.raises(RobotsDisallowed) as caught:
            anchor.fetch(f"{base_url}/article")
    assert caught.value.reason == "unavailable"


# -- D-095: 선두 BOM ----------------------------------------------------------


def test_leading_bom_does_not_discard_the_rule_group(tmp_path, fixture_server):
    """선두 UTF-8 BOM 때문에 규칙 그룹이 통째로 버려지면 안 된다 (D-095).

    파서가 `﻿User-agent:`를 지시자로 인식하지 못한다. Windows 편집기로
    저장된 robots.txt에서 흔하고, RFC 9309 §2.3은 선두 BOM 무시를 규정한다.
    결과는 D-094와 같다 — 소유자가 쓴 금지가 없는 것이 된다.
    """
    base_url, state = fixture_server
    state.robots_body_override = "﻿User-agent: *\nDisallow: /\n".encode("utf-8")
    with Anchor(config=_config(tmp_path)) as anchor:
        with pytest.raises(RobotsDisallowed) as caught:
            anchor.fetch(f"{base_url}/article")
    assert caught.value.reason == "explicit", "BOM 하나에 규칙 전체가 사라졌다"


# -- D-096: 크기 상한과 타임아웃 ----------------------------------------------


def test_oversized_robots_does_not_land_in_the_database(tmp_path, fixture_server):
    """robots.txt에도 크기 상한이 걸려야 한다 (D-096).

    SPEC §5.4는 "크기 상한은 모든 응답에 적용"이라고 적혀 있는데 robots만
    `ConditionalFetcher`를 우회한다. 20MB robots.txt를 전부 버퍼링해 SQLite에
    넣으면 캐시 DB가 그만큼 부푼다.
    """
    base_url, state = fixture_server
    filler = "# " + "가" * 200 + "\n"
    state.robots_body_override = (
        "User-agent: *\nDisallow: /private\n" + filler * 4000
    ).encode("utf-8")
    config = _config(tmp_path, max_content_bytes=64 * 1024)
    with Anchor(config=config) as anchor:
        anchor.fetch(f"{base_url}/article")  # 규칙을 못 읽었어도 페치는 성립
        stored = anchor._repository.get_robots(base_url)
    assert stored is not None
    assert len(stored.body.encode("utf-8")) <= 64 * 1024, "상한을 넘겨 저장했다"


def test_robots_request_uses_the_configured_timeout(tmp_path, fixture_server):
    """robots 요청도 설정된 타임아웃을 써야 한다 (D-096).

    10초가 하드코딩돼 있어, 사용자가 1초로 줄여도 느린 robots 하나가
    그 열 배를 기다리게 만든다.
    """
    import time

    base_url, state = fixture_server
    state.robots_delay = 1.2
    config = _config(tmp_path, timeout_seconds=0.3, retry_backoff_base=0.01)
    started = time.monotonic()
    with Anchor(config=config) as anchor:
        with pytest.raises(RobotsDisallowed):
            anchor.fetch(f"{base_url}/article")
    elapsed = time.monotonic() - started
    assert elapsed < 1.2, f"설정 0.3초인데 {elapsed:.1f}초를 기다렸다"


# -- D-098: 빈 User-Agent -----------------------------------------------------


def test_empty_user_agent_is_rejected_at_configuration_time(tmp_path):
    """자기를 밝히지 않는 요청은 보내지 않는다 (D-098).

    빈 UA는 robots 매칭도 빈 토큰으로 하게 만든다 — 규칙을 지키겠다면서
    누구인지 말하지 않는 것이다 (SPEC §5.4).
    """
    with pytest.raises(ConfigError):
        _config(tmp_path, user_agent="")
    with pytest.raises(ConfigError):
        _config(tmp_path, user_agent="   ")


# -- D-090: 아카이브 URI-M의 robots 우회 --------------------------------------


def test_aggregator_supplied_uri_is_not_fetched_from_an_arbitrary_host(
    tmp_path, fixture_server
):
    """애그리게이터가 지목했다는 이유로 아무 URI나 가져오면 안 된다 (D-090).

    직접 페치는 `RobotsDisallowed(explicit)`로 막히는데, 같은 경로를
    애그리게이터가 지목하면 검증 없이 GET한다. "explicit은 어떤 우회도 하지
    않는다"(SPEC §5.2)가 애그리게이터 한 겹으로 무력해진다.
    """
    base_url, state = fixture_server
    state.robots = "User-agent: *\nDisallow: /private\n"
    state.archive_html = "<html><body><article><p>아카이브 본문이다.</p></article></body></html>"
    # 애그리게이터가 **robots가 막은 경로**를 URI-M으로 지목한다.
    # 살아 있는 호스트여야 한다 — 해석 불가 호스트로 시험하면 그냥 네트워크
    # 실패라서, 판정이 있든 없든 통과한다(그런 테스트는 아무것도 증명하지 않는다).
    state.archive_payload_override = (
        '{"mementos": {"last": {"uri": "%s/private/secret",' % base_url
        + ' "datetime": "2026-08-01T12:34:56Z"}}}'
    )
    config = _config(tmp_path, archive_fallback_enabled=True, archive_aggregator=base_url)
    with Anchor(config=config) as anchor:
        hit = anchor._archive.lookup(f"{base_url}/article")
    assert hit is None, "robots가 막은 경로를 애그리게이터 경유로 가져왔다"
