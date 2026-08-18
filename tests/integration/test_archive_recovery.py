# SPDX-License-Identifier: Apache-2.0
"""아카이브 폴백에서 **빠져나올 수 있는가** (2차 감사 D-087·088·089·177).

§5.2 6단계의 목적은 원본이 죽었을 때 인용을 구제하는 것이다. 그런데 들어가는
길만 있고 나오는 길이 없으면, 일시적인 장애 한 번이 그 문서의 인용을 영구히
무효로 만든다 — 구제 장치가 정반대로 작동한다.
"""

from __future__ import annotations

import socket

import pytest

from anchor.config import Config
from anchor.errors import FetchFailed
from anchor.service import Anchor
from tests.integration.conftest import article_html


def _archive_config(tmp_path, base_url: str, **overrides) -> Config:
    return Config(
        db_path=tmp_path / "store.db",
        rate_limit_rps=1000.0,
        retry_backoff_base=0.01,
        archive_fallback_enabled=True,
        archive_aggregator=base_url,
        **overrides,
    )


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


# -- D-087: 아카이브에서 빠져나오기 ------------------------------------------


def test_origin_revival_replaces_the_archive_version(tmp_path, fixture_server):
    """원본이 되살아나면 **원본 본문**으로 돌아와야 한다.

    폴백이 문서의 검증자(etag)를 그대로 두면, 부활한 원본이 정직하게 304를
    돌려주고 Anchor는 그 304를 "변한 것 없음"으로 읽어 아카이브 본문을 계속
    현재 본문으로 보고한다. `force_refresh`로도 벗어나지 못한다.
    """
    base_url, state = fixture_server
    state.archive_html = article_html(extra_sentence=" 아카이브 판본의 문장이다.")
    with Anchor(config=_archive_config(tmp_path, base_url)) as anchor:
        first = anchor.fetch(f"{base_url}/article")
        assert first.source == "live"

        state.status_override = 404
        rescued = anchor.fetch(f"{base_url}/article", max_age=0)
        assert rescued.source == "archive", "폴백이 동작하지 않았다"

        # 원본이 **같은 검증자로** 되살아난다 (CDN 일시 장애의 전형).
        # 폴백이 etag를 버리지 않았다면 여기서 304가 돌아와 고착된다.
        state.status_override = None
        revived = anchor.fetch(f"{base_url}/article", max_age=0)

    assert revived.source == "live", "원본이 살아났는데 아카이브에 고착됐다"
    assert "아카이브 판본의 문장이다" not in (revived.content or "")


def test_origin_revival_with_identical_text_is_recorded_as_live(tmp_path, fixture_server):
    """본문이 아카이브와 같아도 **출처는 원본**으로 기록돼야 한다.

    `unchanged` 판정이 본문 해시만 보고 출처를 보지 않으면 archive 행을
    재사용해, 살아 있는 원문의 인용에 아카이브 URI-M과 과거 날짜가 달린다.
    """
    base_url, state = fixture_server
    state.archive_html = state.html  # 아카이브와 원본의 본문이 같다
    with Anchor(config=_archive_config(tmp_path, base_url)) as anchor:
        anchor.fetch(f"{base_url}/article")
        state.status_override = 404
        rescued = anchor.fetch(f"{base_url}/article", max_age=0)
        assert rescued.source == "archive"

        state.status_override = None
        revived = anchor.fetch(f"{base_url}/article", max_age=0)

    assert revived.source == "live", "같은 본문이라는 이유로 아카이브 행이 재사용됐다"


# -- D-088: 폴백에 들어갈 자격 -----------------------------------------------


def test_transient_timeout_does_not_fall_back_to_the_archive(tmp_path, fixture_server):
    """회복 가능한 실패는 "확인 불가"이지 "사라졌다"가 아니다.

    타임아웃으로 폴백이 돌면 옛 스냅샷이 현재 본문이 되고, 이어지는 verify가
    멀쩡한 인용을 MISSING으로 **단정**한다 — 모르면 보류한다는 계약에 반한다.
    """
    base_url, state = fixture_server
    state.archive_html = article_html(extra_sentence=" 아카이브 판본이다.")
    config = _archive_config(tmp_path, base_url, timeout_seconds=0.3)
    with Anchor(config=config) as anchor:
        anchor.fetch(f"{base_url}/article")
        state.response_delay = 1.5
        with pytest.raises(FetchFailed):
            anchor.fetch(f"{base_url}/article", max_age=0)
        state.response_delay = 0.0
        again = anchor.fetch(f"{base_url}/article", max_age=0)
    assert again.source == "live"


def test_vanished_host_still_falls_back(tmp_path, fixture_server):
    """호스트가 통째로 사라진 경우는 폴백의 본래 목적이다 (D-053).

    D-088 조치가 이 경로까지 막으면 이 프로젝트의 대표 시나리오가 다시 죽는다.
    """
    base_url, state = fixture_server
    state.archive_html = article_html(extra_sentence=" 아카이브만 남았다.")
    dead = f"http://127.0.0.1:{_free_port()}"
    config = _archive_config(tmp_path, base_url)
    with Anchor(config=config) as anchor:
        # 애그리게이터는 살아 있는 픽스처 서버, 대상 호스트만 죽어 있다
        rescued = anchor.fetch(f"{dead}/article", max_age=0)
    assert rescued.source == "archive"


# -- D-089: 아카이브 응답이 이상할 때 ----------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        '{"mementos": []}',
        '{"mementos": {"last": {"uri": "%(base)s/web/x", "datetime": 12345}}}',
        '{"mementos": {"last": {"datetime": "2026-08-01T12:34:56Z"}}}',
        '[{"not": "an object"}]',
        '{"mementos": {"last": {"uri": "http://[bad:port:1]/x", "datetime": "2026-08-01T12:34:56Z"}}}',
    ],
    ids=["빈 배열", "datetime이 숫자", "uri 없음", "최상위가 배열", "깨진 URI"],
)
def test_malformed_archive_response_reports_the_original_failure(
    tmp_path, fixture_server, payload
):
    """폴백의 실패가 **원래의 실패 보고를 가려서는 안 된다**.

    아카이브 응답 파싱에서 새어 나온 예외는 `AnchorError`가 아니므로
    `verify()`가 잡지 못하고, 문서 하나 때문에 검증 보고서 전체가 사라진다.
    """
    base_url, state = fixture_server
    state.archive_payload_override = payload % {"base": base_url}
    state.archive_html = article_html()
    with Anchor(config=_archive_config(tmp_path, base_url)) as anchor:
        state.status_override = 404
        with pytest.raises(FetchFailed) as caught:
            anchor.fetch(f"{base_url}/article", max_age=0)
    assert caught.value.http_status == 404, "원래의 404가 다른 예외에 가려졌다"


# -- D-177: 되돌림 재사용과 gc의 경합 ----------------------------------------


def test_reusing_a_reverted_version_survives_concurrent_collection(tmp_path, fixture_server):
    """되돌림을 관측하는 순간 gc가 재사용 대상을 지워도 페치가 살아남아야 한다.

    "찾기 → 별도 트랜잭션에서 가리키기" 사이의 틈으로 맨 `sqlite3.IntegrityError`가
    MCP 호출자에게 올라갔다. 재사용 대상은 **현재 버전이 아닌** 옛 버전이므로
    gc가 지울 자격이 있다.
    """
    base_url, state = fixture_server
    original = state.html
    with Anchor(config=_archive_config(tmp_path, base_url)) as anchor:
        first = anchor.fetch(f"{base_url}/article")
        state.html = article_html(nonce="n1", extra_sentence=" 중간 판본이다.")
        state.etag = '"v2"'
        anchor.fetch(f"{base_url}/article", max_age=0)

        # gc가 옛 버전을 회수한 상황 — 현재 버전이 아니므로 지울 자격이 있다
        anchor._repository._connection.execute(
            "DELETE FROM versions WHERE id = ?", (first.version_id,)
        )

        state.html = original  # 되돌림 — 방금 사라진 그 본문으로 돌아왔다
        state.etag = '"v3"'
        result = anchor.fetch(f"{base_url}/article", max_age=0)

    assert result.version_id, "재사용 대상이 사라지자 페치가 실패했다"
    assert result.version_id != first.version_id
