# SPDX-License-Identifier: Apache-2.0
"""리다이렉트가 **문서의 정체성**을 망가뜨리지 않는가 (D-099·100·101·102·103·104).

링크 부패를 다루는 도구는 리다이렉트를 가장 자주 만난다. 그런데 그 경로에서
"이 본문이 어느 문서의 것인가"를 잘못 정하면, 사용자는 **자기가 인용한 적 없는
문서의 본문**을 근거로 받는다. 판정이 틀리는 것보다 나쁘다 — 판정 대상 자체가
바뀐 것이라 어떤 재검증으로도 드러나지 않는다.
"""

from __future__ import annotations

import threading

import httpx
import pytest

from anchor.config import Config
from anchor.errors import AnchorError
from anchor.service import Anchor


def _config(tmp_path, **overrides) -> Config:
    return Config(
        db_path=tmp_path / "store.db",
        rate_limit_rps=1000.0,
        retry_backoff_base=0.01,
        **overrides,
    )


def _page(title: str, sentence: str) -> str:
    return (
        f"<html><head><title>{title}</title></head><body><article>"
        f"<p>{sentence} 이 문단은 충분히 길어야 앵커를 만들 수 있으므로 문장을 하나 더 둔다. "
        f"조건부 요청과 본문 해시를 함께 쓰면 재페치를 줄일 수 있다.</p>"
        f"</article></body></html>"
    )


# -- D-099: 남의 문서 이력에 본문이 삽입된다 ----------------------------------


def test_a_former_alias_serving_its_own_content_is_not_folded_into_the_old_document(
    tmp_path, fixture_server
):
    """옛 별칭이 자기 콘텐츠를 서빙하기 시작하면 **다른 문서**다 (D-099).

    별칭으로 찾은 문서를 최종 URL이 무관해진 뒤에도 적재 대상으로 쓴다.
    별칭 URL이 리다이렉트를 멈추고 자기 본문을 주면 `final_url == norm_url`이라
    교정 분기가 아예 실행되지 않는다 — 목적지 문서의 이력에 **다른 리소스의
    본문**이 새 버전으로 꽂히고, 원문은 변한 적 없는데 `changed`로 보고된다.
    그 앵커의 Robust Links는 그 문장이 존재한 적 없는 URL로 출처를 찍는다.
    """
    base_url, state = fixture_server
    state.bodies = {
        "/target": _page("목적지", "목적지 문서의 고유한 문장이다."),
        "/alias": _page("별칭", "별칭이 스스로 서빙하기 시작한 전혀 다른 문장이다."),
    }
    state.etags = {"/target": '"t1"', "/alias": '"a1"'}
    state.redirects = {"/alias": f"{base_url}/target"}

    with Anchor(config=_config(tmp_path)) as anchor:
        first = anchor.fetch(f"{base_url}/alias")  # 별칭 → 목적지
        target_id = first.document_id

        state.redirects = {}  # 별칭이 리다이렉트를 멈추고 자기 본문을 준다
        second = anchor.fetch(f"{base_url}/alias", max_age=0)

        target_text = anchor.get_version(document_id=target_id, ref="latest")[1]

    assert "별칭이 스스로 서빙" not in target_text, (
        "목적지 문서의 이력에 다른 리소스의 본문이 꽂혔다"
    )
    assert second.document_id != target_id, "별칭의 본문이 목적지 문서로 귀속됐다"


# -- D-100: 조건부 헤더가 리다이렉트 목적지로 나간다 --------------------------


def test_validators_are_not_sent_to_the_redirect_destination(tmp_path, fixture_server):
    """원본의 `If-None-Match`가 목적지로 그대로 나가면 안 된다 (D-100).

    목적지가 RFC 9110 §13.1.3대로 자기 검증자와 비교해 정직하게 304를 주면,
    Anchor는 `not_modified`로 읽고 **옛 문서의 옛 본문을 현재 내용으로 계속
    반환**한다. 이 경로에선 별칭 등록도 `documents.url` 갱신도 없어 문서가
    이사한 사실이 영구히 감지되지 않는다. 부수로 타 호스트에 ETag가 샌다.
    """
    base_url, state = fixture_server
    port = base_url.rsplit(":", 1)[1]
    other_host = f"http://localhost:{port}"  # 같은 서버, 다른 오리진
    state.bodies = {
        "/first": _page("첫 문서", "첫 문서의 고유한 문장이다."),
        "/second": _page("이사한 문서", "이사한 곳의 새로운 문장이다."),
    }
    state.etags = {"/first": '"same"', "/second": '"same"'}  # 흔한 조합

    with Anchor(config=_config(tmp_path)) as anchor:
        anchor.fetch(f"{base_url}/first")
        state.redirects = {"/first": f"{other_host}/second"}
        moved = anchor.fetch(f"{base_url}/first", max_age=0)
        text = anchor.get_version(document_id=moved.document_id, ref="latest")[1]

    assert moved.outcome != "not_modified", "목적지의 304를 '변한 것 없음'으로 읽었다"
    assert "이사한 곳의 새로운 문장" in text, "이사한 사실이 감지되지 않았다"


# -- D-101: 캐노니컬 리다이렉트에서의 경합 ------------------------------------


def test_two_urls_redirecting_to_one_target_do_not_collide(tmp_path, fixture_server):
    """두 URL이 같은 목적지로 가면 스트라이프가 갈려 충돌한다 (D-101).

    락 키는 **입력 URL**인데 문서 생성은 **최종 URL**로 한다. 캐노니컬
    리다이렉트는 링크 부패 도구가 가장 흔히 만나는 형태다. 두 스레드가 나란히
    "문서 없음"으로 판단하면 맨 `sqlite3.IntegrityError`가 호출자에게 올라가고
    한쪽 fetch의 버전·회계가 유실된다.
    """
    base_url, state = fixture_server
    state.bodies = {"/canonical": _page("정본", "정본 문서의 문장이다.")}
    state.redirects = {
        "/one": f"{base_url}/canonical",
        "/two": f"{base_url}/canonical",
    }

    errors: list[BaseException] = []
    results: list[str] = []

    def worker(path: str) -> None:
        try:
            with Anchor(config=_config(tmp_path)) as anchor:
                results.append(anchor.fetch(f"{base_url}{path}", max_age=0).document_id)
        except BaseException as error:  # noqa: BLE001 — 무엇이 새는지가 관심사다
            errors.append(error)

    threads = [threading.Thread(target=worker, args=(p,)) for p in ("/one", "/two")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, f"경합에서 예외가 샜다: {errors[0]!r}"
    assert len(set(results)) == 1, "같은 정본이 두 문서로 갈렸다"


# -- D-102: 일시 리다이렉트가 정본 URL을 덮어쓴다 -----------------------------


@pytest.mark.parametrize("status", [302, 307])
def test_temporary_redirect_does_not_rewrite_the_canonical_url(
    tmp_path, fixture_server, status
):
    """302/307은 **일시**다 — 정본 URL을 영구히 바꾸면 안 된다 (D-102).

    EU 동의 장벽·지역 게이트처럼 인터스티셜을 거치는 구성에서, 그 인터스티셜
    URL이 Memento의 URI-R(`rel="original"`)로 기록되고 장벽이 사라진 뒤에도
    되돌아오지 않는다.
    """
    base_url, state = fixture_server
    state.bodies = {
        "/article": _page("본문", "원래 자리의 문장이다."),
        "/consent": _page("동의", "동의 장벽 뒤에서 서빙되는 문장이다."),
    }
    state.redirect_status = status
    state.redirects = {"/article": f"{base_url}/consent"}

    with Anchor(config=_config(tmp_path)) as anchor:
        result = anchor.fetch(f"{base_url}/article")
        document = anchor._resolve_document(result.document_id)

    assert document.url == f"{base_url}/article", (
        f"{status}(일시)인데 정본 URL이 인터스티셜로 바뀌었다: {document.url}"
    )


def test_permanent_redirect_still_rewrites_the_canonical_url(tmp_path, fixture_server):
    """301은 영구다 — 정본 URL이 목적지로 바뀌어야 한다 (회귀 방지)."""
    base_url, state = fixture_server
    state.bodies = {"/new": _page("새 자리", "새 자리의 문장이다.")}
    state.redirect_status = 301
    state.redirects = {"/old": f"{base_url}/new"}

    with Anchor(config=_config(tmp_path)) as anchor:
        result = anchor.fetch(f"{base_url}/old")
        document = anchor._resolve_document(result.document_id)

    assert document.url == f"{base_url}/new"


# -- D-103: 뒤늦게 생긴 리다이렉트가 두 문서를 엮는다 -------------------------


def test_a_late_redirect_keeps_the_audit_trail_coherent(tmp_path, fixture_server):
    """각각 등록된 A·B 사이에 뒤늦게 A→B 영구 리다이렉트가 생기면 (D-103).

    A 문서는 남아 있는데 페치 결과는 B로 간다 → A의 앵커가 B의 본문과
    대조되고 `verifications.checked_version`에 **다른 문서의 버전 id**가
    기록된다. 감사 추적이 앞뒤가 맞지 않고, `has_pending_verification`은
    검증한 버전과 현재 버전이 영영 달라 참으로 굳는다.

    판정 자체(MISSING)는 다투지 않는다 — 301은 "영구히 옮겼다"이고, 옮겨간
    곳에 그 문장이 없다면 인용은 실제로 무효다. 다퉈야 하는 것은 **누구의
    이력에 그 사실이 적히는가**이다. 301로 하나가 된 두 행은 합쳐야 한다.
    """
    base_url, state = fixture_server
    state.bodies = {
        "/a": _page("A", "A 문서에만 있는 고유한 문장이다."),
        "/b": _page("B", "B 문서에만 있는 전혀 다른 문장이다."),
    }
    state.etags = {"/a": '"a1"', "/b": '"b1"'}

    with Anchor(config=_config(tmp_path)) as anchor:
        a = anchor.fetch(f"{base_url}/a", include_content=True)
        b = anchor.fetch(f"{base_url}/b")
        quote = "A 문서에만 있는 고유한 문장이다."
        assert quote in (a.content or "")
        cited = anchor.cite(f"{base_url}/a", quote=quote)

        state.redirect_status = 301
        state.redirects = {"/a": f"{base_url}/b"}  # 뒤늦게 A가 B로 이관됐다
        anchor.verify(anchor_ids=[cited.anchor_id])

        checked = anchor._repository.latest_verified_version(cited.anchor_id)
        assert checked is not None and checked[1] is not None
        version = anchor._repository.get_version(checked[1])
        (record,) = anchor._repository.select_anchors(anchor_ids=[cited.anchor_id])
        documents = {d.id for d in anchor._repository.list_documents()}

    assert version is not None
    assert version.document_id == record.document_id, (
        "검증 기록이 **다른 문서의 버전**을 가리킨다"
    )
    assert record.document_id == b.document_id, "영구 리다이렉트로 하나가 된 두 행이 남았다"
    assert a.document_id not in documents, "합쳐진 뒤에도 옛 문서 행이 남아 있다"
    # 옛 URL로도 여전히 찾을 수 있어야 한다 — 합쳤다고 잃어버리면 안 된다.
    with Anchor(config=_config(tmp_path)) as anchor:
        assert anchor._resolve_document(f"{base_url}/a").id == b.document_id


# -- D-104: https → http 다운그레이드 -----------------------------------------


def test_https_to_http_downgrade_is_refused(tmp_path):
    """https로 요청했는데 평문으로 내려가는 리다이렉트를 조용히 따라가면 안 된다.

    무결성 보장이 없는 채널에서 받은 본문이 인용 근거가 되고, 정본 URL이
    평문 `http://`로 기록된다 (D-104). 실제 TLS 없이 재현하기 위해 전송
    계층을 대신 세운다 — 결함이 사는 자리는 스킴 비교이지 TLS가 아니다.
    """
    from anchor.errors import FetchFailed
    from anchor.fetcher.client import ConditionalFetcher

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.scheme == "https":
            return httpx.Response(301, headers={"Location": "http://example.test/plain"})
        return httpx.Response(200, content=b"<html><body><p>plain</p></body></html>")

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        fetcher = ConditionalFetcher(
            client, user_agent="test", max_content_bytes=1_000_000, retry_backoff_base=0.001
        )
        with pytest.raises(AnchorError) as caught:
            fetcher.get("https://example.test/secure")
    assert isinstance(caught.value, FetchFailed)
    assert caught.value.reason == "redirect"


# -- D-183: 일시 리다이렉트를 만난 별칭 ---------------------------------------


def test_an_alias_that_flaps_to_a_temporary_redirect_keeps_its_identity(
    tmp_path, fixture_server
):
    """별칭이 301에서 302로 흔들려도 **여전히 같은 리소스를 가리킨다** (D-183).

    D-099 조치가 "리다이렉트 중이 아니다"를 `moved=False`로 판정해, 일시
    리다이렉트로 도달한 별칭을 "자기 콘텐츠를 서빙하기 시작했다"로 오독했다
    — 별칭이 삭제되고, 별칭 URL을 정본으로 하는 유령 문서가 **목적지의
    본문**을 담은 채 생긴다. 별칭은 그 문장을 서빙한 적이 없다.
    """
    base_url, state = fixture_server
    state.bodies = {"/target": _page("목적지", "목적지 문서의 고유한 문장이다.")}
    state.etags = {"/target": '"t1"'}
    state.redirects = {"/alias": f"{base_url}/target"}
    state.redirect_statuses = {"/alias": 301}

    with Anchor(config=_config(tmp_path)) as anchor:
        first = anchor.fetch(f"{base_url}/alias")
        # 배포로 301이 302로 흔들리고 ETag도 갱신됐다 — 304가 결함 분기를
        # 가리지 않도록 목적지가 200을 주는 조건이다.
        state.redirect_statuses = {"/alias": 302}
        state.etags = {"/target": '"t2"'}
        second = anchor.fetch(f"{base_url}/alias", max_age=0)

        documents = anchor._repository.list_documents()
        aliases = anchor._repository._connection.execute(
            "SELECT url, document_id FROM document_aliases"
        ).fetchall()

    assert second.document_id == first.document_id, "같은 리소스가 두 문서로 갈렸다"
    assert len(documents) == 1, f"유령 문서가 생겼다: {[d.url for d in documents]}"
    assert len(aliases) == 1, "별칭이 파괴됐다"


def test_a_consent_wall_on_the_target_does_not_poison_the_target_history(
    tmp_path, fixture_server
):
    """목적지에 동의 장벽(302)이 생겼다 사라져도 유령 문서가 남으면 안 된다 (D-183).

    유령 문서가 생기면 장벽이 사라진 뒤의 재페치에서 `merge_document`가
    발화해, **별칭 URL이 서빙한 적 없는 판본**이 병합으로 남는다.

    장벽 본문이 정본 문서의 관측 이력에 남는 것 자체는 다투지 않는다 —
    302로 받은 본문은 HTTP 의미론상 요청한 리소스의 현재 표현이고(RFC 9110),
    "지금 서빙되는 본문"을 기록하는 것은 사실 보고다. 다퉈야 하는 것은
    문서의 **정체성**이다: 문서는 하나여야 하고, 장벽이 걷히면 현재 본문이
    원래 본문으로 돌아와야 한다.
    """
    base_url, state = fixture_server
    state.bodies = {
        "/target": _page("목적지", "목적지 문서의 고유한 문장이다."),
        "/consent": _page("동의", "동의 장벽 뒤에서 서빙되는 문장이다."),
    }
    state.etags = {"/target": '"t1"', "/consent": '"c1"'}
    state.redirects = {"/alias": f"{base_url}/target"}
    state.redirect_statuses = {"/alias": 301}

    with Anchor(config=_config(tmp_path)) as anchor:
        first = anchor.fetch(f"{base_url}/alias")
        # 목적지에 동의 장벽이 생겼다 (302 인터스티셜)
        state.redirects = {"/alias": f"{base_url}/target", "/target": f"{base_url}/consent"}
        state.redirect_statuses = {"/alias": 301, "/target": 302}
        anchor.fetch(f"{base_url}/alias", max_age=0)
        # 장벽이 사라졌다
        state.redirects = {"/alias": f"{base_url}/target"}
        state.redirect_statuses = {"/alias": 301}
        final = anchor.fetch(f"{base_url}/alias", max_age=0)

        current = anchor._repository.current_version(final.document_id)
        assert current is not None
        current_text = anchor._repository.get_version_text(current.id)
        documents = anchor._repository.list_documents()

    assert len(documents) == 1, f"유령 문서가 남았다: {[d.url for d in documents]}"
    assert "목적지 문서의 고유한 문장" in current_text, (
        "장벽이 걷혔는데 현재 본문이 돌아오지 않았다"
    )
    assert "동의 장벽" not in current_text


# -- D-194: 등록된 문서의 이사 -------------------------------------------------


def test_an_existing_document_that_moves_updates_its_canonical_url(
    tmp_path, fixture_server
):
    """이미 등록된 문서가 301로 이사하면 정본 URL이 따라가야 한다 (D-194).

    목적지에 문서가 없으면 `documents.url`이 옛 주소에 남고 자기 자신을
    가리키는 별칭이 생긴다 — SPEC v1.8 "영구만 정본 URL을 바꾼다"가 신규
    문서에서만 성립하는 반쪽이었다.
    """
    base_url, state = fixture_server
    state.bodies = {
        "/old": _page("문서", "이사하는 문서의 문장이다."),
        "/new": _page("문서", "이사하는 문서의 문장이다."),
    }
    state.etags = {"/old": '"v1"', "/new": '"v2"'}

    with Anchor(config=_config(tmp_path)) as anchor:
        first = anchor.fetch(f"{base_url}/old")
        state.redirect_status = 301
        state.redirects = {"/old": f"{base_url}/new"}
        moved = anchor.fetch(f"{base_url}/old", max_age=0)

        document = anchor._resolve_document(moved.document_id)
        aliases = anchor._repository._connection.execute(
            "SELECT url, document_id FROM document_aliases"
        ).fetchall()

    assert moved.document_id == first.document_id, "이사가 새 문서를 만들었다"
    assert document.url == f"{base_url}/new", f"정본 URL이 옛 주소에 남았다: {document.url}"
    assert all(row["url"] != document.url for row in aliases), "자기 자신을 가리키는 별칭"
    # 옛 URL로도 여전히 찾을 수 있어야 한다
    with Anchor(config=_config(tmp_path)) as anchor:
        assert anchor._resolve_document(f"{base_url}/old").id == first.document_id


# -- D-187: 정규화가 손대는 목적지에서의 조건부 요청 ---------------------------


@pytest.mark.parametrize(
    "suffix",
    ["?b=2&a=1", "#top", "?utm_source=nl&id=3"],
    ids=["질의 정렬", "프래그먼트", "추적 파라미터"],
)
def test_validators_reach_a_destination_the_normalizer_touches(
    tmp_path, fixture_server, suffix
):
    """정규화가 문자열을 바꾸는 목적지에도 검증자가 실려야 한다 (D-187).

    비교가 날것 문자열이면 질의 정렬·추적 파라미터·프래그먼트가 붙는 순간
    영영 일치하지 않아 조건부 요청이 조용히 무력해진다 — D-007의 회귀선이
    다시 무너지고 매 재확인이 전체 본문을 다시 내려받는다.
    """
    base_url, state = fixture_server
    state.bodies = {"/new": _page("정본", "정본 문서의 문장이다.")}
    state.etags = {"/new": '"n1"'}
    state.redirect_status = 301
    state.redirects = {"/entry": f"{base_url}/new{suffix}"}

    with Anchor(config=_config(tmp_path)) as anchor:
        anchor.fetch(f"{base_url}/entry")
        again = anchor.fetch(f"{base_url}/entry", max_age=0)

    assert again.outcome == "not_modified", (
        f"조건부 요청이 무력화됐다 (outcome={again.outcome})"
    )


# -- D-184·D-186: 병합과 좌표계·Memento ---------------------------------------


def test_merge_preserves_the_observation_order(tmp_path, fixture_server):
    """병합된 문서의 `latest~N`이 **일어난 순서**를 말해야 한다 (D-184).

    두 문서는 각자 1부터 순번을 매겼으므로 옮기기만 하면 같은 순번이
    여러 개 남고, 관측 최신순이 거짓이 된다.
    """
    base_url, state = fixture_server
    state.bodies = {
        "/a": _page("A", "A 문서의 첫 문장이다."),
        "/b": _page("B", "B 문서의 첫 문장이다."),
    }
    state.etags = {"/a": '"a1"', "/b": '"b1"'}
    with Anchor(config=_config(tmp_path)) as anchor:
        anchor.fetch(f"{base_url}/a")
        state.bodies["/a"] = _page("A", "A 문서의 둘째 문장이다.")
        state.etags["/a"] = '"a2"'
        anchor.fetch(f"{base_url}/a", max_age=0)
        anchor.fetch(f"{base_url}/b")
        state.bodies["/b"] = _page("B", "B 문서의 둘째 문장이다.")
        state.etags["/b"] = '"b2"'
        anchor.fetch(f"{base_url}/b", max_age=0)

        state.redirect_status = 301
        state.redirects = {"/a": f"{base_url}/b"}
        final = anchor.fetch(f"{base_url}/a", max_age=0)

        ordered = anchor._repository.list_versions_by_observation(final.document_id)
        sequences = [v.last_observed_seq for v in ordered]
        observed = [v.last_observed_at for v in ordered]

    assert len(sequences) == len(set(sequences)), f"순번이 겹친다: {sequences}"
    assert observed == sorted(observed, reverse=True), (
        f"관측 최신순이 시각 순서와 어긋난다: {observed}"
    )


def test_merge_does_not_rewrite_a_citations_memento(tmp_path, fixture_server):
    """병합이 인용의 Memento-Datetime을 바꾸면 안 된다 (D-186).

    같은 본문이 양쪽에 있을 때 아무 쪽이나 지우면, 사용자가 인용한 시점의
    캡처가 사라지고 Robust Links의 `data-versiondate`가 다른 스냅샷을
    가리킨다 — 이 도구의 존재 이유가 걸린 자리다. 같은 리소스(301)의 같은
    본문이라면 **가장 이른 캡처**가 그 본문의 Memento-Datetime이다.
    """
    base_url, state = fixture_server
    same_body = _page("기사", "캐노니컬 정리 전후로 동일한 기사 본문이다.")
    state.bodies = {"/2019/old": same_body, "/canonical": same_body}
    state.etags = {"/2019/old": '"o1"', "/canonical": '"c1"'}

    with Anchor(config=_config(tmp_path)) as anchor:
        old = anchor.fetch(f"{base_url}/2019/old", include_content=True)
        quote = "캐노니컬 정리 전후로 동일한 기사 본문이다."
        assert quote in (old.content or "")
        cited = anchor.cite(f"{base_url}/2019/old", quote=quote)
        before = anchor._repository.get_version(
            anchor._repository.select_anchors(anchor_ids=[cited.anchor_id])[0].created_version
        )
        assert before is not None

        # 캡처 시각이 초 단위라 같은 초의 두 캡처는 구분되지 않는다 —
        # "다른 시점의 캡처"라는 이 테스트의 전제를 실제로 만든다.
        import time as _time

        _time.sleep(1.1)
        anchor.fetch(f"{base_url}/canonical")
        state.redirect_status = 301
        state.redirects = {"/2019/old": f"{base_url}/canonical"}
        anchor.fetch(f"{base_url}/2019/old", max_age=0)

        record = anchor._repository.select_anchors(anchor_ids=[cited.anchor_id])[0]
        after = anchor._repository.get_version(record.created_version)

    assert after is not None, "인용의 근거 버전 행이 사라졌다"
    assert after.captured_at == before.captured_at, (
        f"Memento-Datetime이 바뀌었다: {before.captured_at} → {after.captured_at}"
    )


def test_verify_survives_a_merge_that_lands_mid_batch(tmp_path, fixture_server):
    """검증 도중 다른 프로세스의 병합으로 문서가 사라져도 배치가 살아야 한다 (D-185).

    앵커 조회와 문서 조회 **사이**에 병합이 끼어들면 `assert document is not
    None`이 터진다 — `AnchorError`가 아니라서 `verify()`의 방어에도 걸리지
    않고 보고서 전체가 사라진다. 경합을 기다리지 않고 그 틈을 직접 연다.
    """
    from anchor.store.repository import Repository

    base_url, state = fixture_server
    state.bodies = {
        "/a": _page("A", "A 문서에만 있는 고유한 문장이다."),
        "/b": _page("B", "B 문서에만 있는 전혀 다른 문장이다."),
    }
    state.etags = {"/a": '"a1"', "/b": '"b1"'}

    with Anchor(config=_config(tmp_path)) as anchor:
        a = anchor.fetch(f"{base_url}/a", include_content=True)
        b = anchor.fetch(f"{base_url}/b")
        cited = anchor.cite(f"{base_url}/a", quote="A 문서에만 있는 고유한 문장이다.")

        other = Repository(tmp_path / "store.db")
        original = anchor._repository.get_document
        fired = {"done": False}

        def racing_get_document(document_id):
            if not fired["done"] and document_id == a.document_id:
                fired["done"] = True
                other.merge_document(a.document_id, b.document_id)
            return original(document_id)

        anchor._repository.get_document = racing_get_document  # type: ignore[method-assign]
        try:
            report = anchor.verify(anchor_ids=[cited.anchor_id])
        finally:
            anchor._repository.get_document = original  # type: ignore[method-assign]
            other.close()

    assert fired["done"], "창이 열리지 않았다"
    assert report.checked == 1, "병합에 걸린 앵커가 검증에서 소리 없이 빠졌다"
