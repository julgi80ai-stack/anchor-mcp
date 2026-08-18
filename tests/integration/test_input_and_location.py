# SPDX-License-Identifier: Apache-2.0
"""입력과 Location — 계층 밖으로 새는 예외 (2차 감사 D-106·D-108)."""

from __future__ import annotations

import pytest

from anchor.config import Config
from anchor.errors import FetchFailed, InvalidURL
from anchor.service import Anchor


def _config(tmp_path) -> Config:
    return Config(db_path=tmp_path / "store.db", rate_limit_rps=1000.0, retry_backoff_base=0.01)


@pytest.mark.parametrize(
    "location",
    ["mailto:someone@example.com", "about:blank", "urn:isbn:0451450523"],
    ids=["mailto", "about", "urn"],
)
def test_unfollowable_location_is_a_fetch_failure(tmp_path, fixture_server, location):
    """따라갈 수 없는 Location은 **그 문서의 실패**다 (D-106).

    `httpx.InvalidURL`은 `httpx.HTTPError`의 하위가 아니라서 except를
    빠져나간다. `AnchorError`가 아니면 verify()가 잡지 못해 **문서 하나
    때문에 재검증 배치 전체가 중단**되고 나머지 인용의 결과가 사라진다.
    """
    base_url, state = fixture_server
    state.redirects = {"/hop": location}
    with Anchor(config=_config(tmp_path)) as anchor:
        with pytest.raises(FetchFailed) as caught:
            anchor.fetch(f"{base_url}/hop", max_age=0)
    assert caught.value.reason == "redirect"


def test_schemeless_input_is_not_blamed_on_the_site_owner(tmp_path):
    """스킴 없는 입력이 "robots.txt가 거부"로 보고되면 안 된다 (D-108).

    사용자의 오타를 사이트 소유자의 거부로 뒤집어씌우는 것이다. 아카이브
    폴백이 켜져 있으면 그 오타로 아카이브 조회까지 나간다.
    """
    with Anchor(config=_config(tmp_path)) as anchor:
        with pytest.raises(InvalidURL):
            anchor.fetch("example.com/article")
