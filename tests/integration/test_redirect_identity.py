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
