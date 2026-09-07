# SPDX-License-Identifier: Apache-2.0
"""캐시에 무엇이 있는지 물어볼 수 있어야 한다 (D-284·D-285, 그리고 verify의 분모).

실사용에서 세 자리가 동시에 막혔다. `list_documents`가 전량 반환뿐이라 응답이
토큰 한도를 넘겨 도구가 자기 응답으로 호출자를 막았고, 문서별 앵커 수가 없어
"앵커 달린 문서만"을 추릴 수 없었으며, `MOVED`가 몇 건인지는 알려 주면서 **어느
앵커인지 물어볼 곳이 없었다**. 셋 다 저장은 하는데 꺼낼 길이 없는 경우다.

그리고 그 셋보다 큰 것: `checked: 104, ALTERED: 0`에는 **분모가 없다.** 앵커가
안 걸린 문헌과 앵커가 걸렸고 멀쩡한 문헌을 구분할 신호가 응답에 하나도 없어서,
탐지 실패가 무해한 침묵이 아니라 **거짓 안심**이 된다. A-1(문서의 1%만 보고
`unchanged`를 다른 문서와 같은 확신으로 말했다)이 앵커 집합에서 재발한 것이다.

축을 먼저 연다: 문서당 앵커 수(0·1·여럿) × 검증 이력(없음·한 번·여러 번이라
최신이 다름) × 검증 상태(INTACT·MOVED) × limit 경계(미만·정확히 상한·초과) ×
조회 URL(정본·별칭·캐시에 없음) × verify 범위(전체·anchor_ids·document_ids).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from anchor.config import Config
from anchor.service import Anchor

from .conftest import article_html

QUOTE = "학술 문헌이 참조한 웹 콘텐츠의 약 75%가 3년 안에 어느 정도 변경된 것으로"
QUOTE2 = "조건부 요청과 본문 해시를 함께 쓰면 재페치와 재파싱을 모두 줄일 수 있다."

# 인용문을 `hint_radius`(500자) 밖으로 밀어내는 앞 문단.
_SHIFT = "<p>" + "이 문단은 인용문을 뒤로 밀기 위해 앞에 붙인 것이다. " * 40 + "</p>"


def _anchor(tmp_path: Path, **overrides) -> Anchor:
    config = Config(
        db_path=tmp_path / "store.db",
        rate_limit_rps=1000.0,
        retry_backoff_base=0.01,
        **overrides,
    )
    return Anchor(db_path=config.db_path, config=config)


@pytest.fixture
def stocked(tmp_path, fixture_server):
    """앵커 수가 서로 다른 문서 셋을 만든다 — 0·1·2.

    셋 다 앵커가 있으면 `has_anchors`도 `anchor_count`도 판별력이 0이 된다
    (조치 절차 4: 픽스처에 정상값을 하드코딩하지 않는다).
    """
    base, state = fixture_server
    state.bodies["/none"] = article_html(nonce="none")
    state.bodies["/one"] = article_html(nonce="one")
    state.bodies["/two"] = article_html(nonce="two")
    with _anchor(tmp_path) as anchor:
        bare = anchor.fetch(f"{base}/none")
        one = anchor.fetch(f"{base}/one")
        two = anchor.fetch(f"{base}/two")
        anchor.cite(one.document_id, QUOTE)
        anchor.cite(two.document_id, QUOTE)
        anchor.cite(two.document_id, QUOTE2)
        yield anchor, base, {"none": bare, "one": one, "two": two}


# -- D-284: 목록이 호출자를 막지 않는다 -----------------------------------


def test_documents_carry_their_anchor_count(stocked):
    """앵커 수는 세면 나온다 — 없으면 "앵커 달린 문서만"을 추릴 수 없다."""
    anchor, _base, docs = stocked
    listed = {row.id: row for row in anchor.list_documents().documents}

    assert listed[docs["none"].document_id].anchor_count == 0
    assert listed[docs["one"].document_id].anchor_count == 1
    assert listed[docs["two"].document_id].anchor_count == 2


def test_has_anchors_filters_both_ways(stocked):
    """참과 거짓 **양쪽**을 건다. 한쪽만 재면 필터가 상수를 돌려줘도 초록이다."""
    anchor, _base, docs = stocked
    with_anchors = anchor.list_documents(has_anchors=True)
    without = anchor.list_documents(has_anchors=False)

    assert {row.id for row in with_anchors.documents} == {
        docs["one"].document_id,
        docs["two"].document_id,
    }
    assert {row.id for row in without.documents} == {
        docs["none"].document_id
    }


def test_the_listing_says_how_many_it_did_not_show(stocked):
    """자른 사실을 고지한다. **정확히 상한만큼**일 때는 자른 것이 아니다.

    조용히 자르면 호출자는 그것을 전부로 읽는다 — 도구가 자기에 대해 거짓을
    말하는 자리다 (D-227과 같은 규칙).
    """
    anchor, _base, _docs = stocked
    cut = anchor.list_documents(limit=2)
    exact = anchor.list_documents(limit=3)

    assert cut.total == 3
    assert cut.returned == 2
    assert cut.truncated is True
    assert exact.returned == 3
    assert exact.truncated is False, "상한과 같은 수는 잘린 것이 아니다"


def test_offset_walks_the_whole_list_without_gaps_or_repeats(stocked):
    """페이지를 이어 붙이면 전체와 같다 — 겹치지도, 빠지지도 않는다."""
    anchor, _base, _docs = stocked
    first = anchor.list_documents(limit=2, offset=0).documents
    second = anchor.list_documents(limit=2, offset=2).documents
    whole = anchor.list_documents().documents

    walked = [row.id for row in list(first) + list(second)]
    assert walked == [row.id for row in whole]
    assert len(set(walked)) == 3


def test_lookup_by_url_answers_which_ones_are_not_here(stocked):
    """호출자가 준 URL 목록에 대해 **없는 것도 말한다** (P1의 바깥쪽).

    코퍼스(서지 등)와 대조하려면 "이 중 무엇이 캐시에 있는가"만으로는 부족하고
    **무엇이 없는가**를 알아야 한다. URL은 호출자가 준다 — 그것이 이 서버의
    계약이고, 우리는 그 목록을 해석하지 않는다.
    """
    anchor, base, docs = stocked
    absent = f"{base}/never-fetched"
    result = anchor.list_documents(urls=[f"{base}/one", absent])

    assert [row.id for row in result.documents] == [docs["one"].document_id]
    assert result.unmatched_urls == (absent,)


def test_lookup_by_url_follows_the_alias_we_recorded(tmp_path, fixture_server):
    """리다이렉트 전 URL로 물어도 찾는다 — 별칭표는 이미 그러라고 있는 것이다.

    코퍼스의 URL은 대개 리다이렉트 **전**의 것이다(DOI 등). 정본 URL만 맞추면
    가진 문서를 "없다"고 답하게 된다.
    """
    base, state = fixture_server
    state.redirects["/old"] = "/article"
    with _anchor(tmp_path) as anchor:
        fetched = anchor.fetch(f"{base}/old")
        result = anchor.list_documents(urls=[f"{base}/old"])

    assert [row.id for row in result.documents] == [fetched.document_id]
    assert result.unmatched_urls == ()


# -- D-285: MOVED를 지목할 수 있다 ----------------------------------------


def test_anchors_can_be_listed_with_their_latest_state(stocked):
    """앵커를 마지막 검증 상태와 함께 연다. 검증 전 앵커는 상태가 None이다.

    `MOVED`가 `attention`에 없는 것은 설계가 맞다 — 조치가 필요 없기 때문이다
    (SPEC §6.3). 그러나 `summary`가 "MOVED 3"이라고 말하면서 **어느 앵커인지
    물어볼 곳이 없으면** 그 수는 확인할 수 없는 주장이다.
    """
    anchor, _base, docs = stocked
    before = anchor.list_anchors().anchors
    assert len(before) == 3
    assert {row.state for row in before} == {None}, "검증 전에는 상태를 지어내지 않는다"

    anchor.verify()
    after = {row.anchor_id: row for row in anchor.list_anchors().anchors}
    assert {row.state for row in after.values()} == {"INTACT"}
    assert all(row.url for row in after.values())
    assert sum(
        1 for row in after.values() if row.document_id == docs["two"].document_id
    ) == 2


def test_listing_anchors_by_state_finds_the_moved_ones(tmp_path, fixture_server):
    """문단이 앞으로 밀려 위치만 바뀐 앵커를 `state="MOVED"`로 지목한다."""
    base, state = fixture_server
    with _anchor(tmp_path) as anchor:
        fetched = anchor.fetch(f"{base}/article")
        cited = anchor.cite(fetched.document_id, QUOTE)
        anchor.verify()

        # 인용문 **앞에** 문단을 끼워 위치만 민다 — 본문은 그대로다.
        # `hint_radius`(기본 500자)를 넘겨 밀어야 1단계(힌트 주변 완전 일치)를
        # 벗어나 2단계(문서 전체 완전 일치 = MOVED)로 간다. 90자만 밀면 앵커는
        # 그대로 INTACT라 이 게이트의 판별력이 0이 된다.
        state.html = state.html.replace("<article>", "<article>\n" + _SHIFT)
        # 본문이 바뀌면 검증자도 바뀐다. 픽스처의 ETag는 `"v1"` 고정이라
        # 그대로 두면 조건부 요청이 304를 받아 **바뀐 본문을 영영 못 본다** —
        # 그러면 이 게이트는 판별력이 0이다 (조치 절차 4).
        state.etag = '"v2"'
        report = anchor.verify()
        assert report.summary.get("MOVED") == 1, "픽스처가 MOVED를 만들지 못했다"

        moved = anchor.list_anchors(state="MOVED").anchors
        intact = anchor.list_anchors(state="INTACT").anchors

    assert [row.anchor_id for row in moved] == [cited.anchor_id]
    assert intact == (), "이 앵커는 더 이상 INTACT가 아니다"


def test_anchor_state_is_the_latest_verification_not_the_first(tmp_path, fixture_server):
    """여러 번 검증했으면 **마지막** 상태다 — 첫 기록이 남아 덮이지 않아야 한다."""
    base, state = fixture_server
    with _anchor(tmp_path) as anchor:
        fetched = anchor.fetch(f"{base}/article")
        anchor.cite(fetched.document_id, QUOTE)
        anchor.verify()
        assert anchor.list_anchors().anchors[0].state == "INTACT"

        state.html = state.html.replace("<article>", "<article>\n" + _SHIFT)
        # 본문이 바뀌면 검증자도 바뀐다. 픽스처의 ETag는 `"v1"` 고정이라
        # 그대로 두면 조건부 요청이 304를 받아 **바뀐 본문을 영영 못 본다** —
        # 그러면 이 게이트는 판별력이 0이다 (조치 절차 4).
        state.etag = '"v2"'
        anchor.verify()
        rows = anchor.list_anchors().anchors

    assert [row.state for row in rows] == ["MOVED"]


# -- P1: verify에 분모가 있다 ---------------------------------------------


def test_verify_reports_what_it_did_not_look_at(stocked):
    """`ALTERED: 0`은 분모 없이는 근거 없는 안심이다 (A-1의 재발).

    앵커가 걸린 문서와 캐시에 있는 문서의 수를 함께 실으면, 무엇을 **안 봤는지**
    가 응답 자체에서 드러난다. 판정은 하나도 바꾸지 않는다 — 사실만 더한다.
    """
    anchor, _base, _docs = stocked
    report = anchor.verify()

    assert report.checked == 3
    assert report.scope.anchors_in_cache == 3
    assert report.scope.documents_checked == 2
    assert report.scope.documents_with_anchors == 2
    assert report.scope.documents_in_cache == 3, (
        "앵커가 없는 문서 1건이 분모에서 사라지면 그 침묵이 보이지 않는다"
    )


def test_a_partial_verify_says_it_was_partial(stocked):
    """일부만 검증하면 `checked`가 전체 앵커 수보다 작다는 것이 응답에 남는다."""
    anchor, _base, docs = stocked
    report = anchor.verify(document_ids=[docs["one"].document_id])

    assert report.checked == 1
    assert report.scope.anchors_in_cache == 3, "본 것이 전부인 척하지 않는다"
    assert report.scope.documents_checked == 1
    assert report.scope.documents_with_anchors == 2
