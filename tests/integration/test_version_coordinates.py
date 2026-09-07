# SPDX-License-Identifier: Apache-2.0
"""버전을 가리키는 **두 좌표계**가 섞여 있지 않은가 (2차 감사 D-083·D-084).

v3에서 "현재 원문 버전"(포인터)과 "캡처 시각"(시간축)을 분리했다. 그런데
소비하는 쪽 절반이 옛 좌표계를 계속 썼다. 되돌림과 아카이브에서 둘이
갈리므로, 섞이면 **일어난 적 없는 전이**를 보여주거나 바뀐 문서를
"검증할 것 없음"으로 분류한다.
"""

from __future__ import annotations

from anchor.config import Config
from anchor.service import Anchor
from tests.integration.conftest import article_html


def _config(tmp_path) -> Config:
    return Config(db_path=tmp_path / "store.db", rate_limit_rps=1000.0)


def _serve(state, body: str, etag: str) -> None:
    state.html = body
    state.etag = etag


# -- D-083: latest는 포인터, latest~N은 캡처 시각 ----------------------------


def test_diff_after_a_revert_is_not_empty(tmp_path, fixture_server):
    """되돌림 직후의 기본 diff가 **빈 diff**가 되면 안 된다 (D-083).

    직전 fetch가 `changed`를 보고했는데 `diff_versions`가 아무것도 내놓지
    않으면 사용자는 "달라진 게 없다"로 읽는다. `latest`는 포인터를 따르고
    `latest~1`은 캡처 시각을 따라서, 되돌림에서 둘이 같은 행을 가리킨다.
    """
    base_url, state = fixture_server
    body_a, body_b = state.html, article_html(nonce="b", extra_sentence=" B 판본이다.")
    with Anchor(config=_config(tmp_path)) as anchor:
        anchor.fetch(f"{base_url}/article")
        _serve(state, body_b, '"b"')
        anchor.fetch(f"{base_url}/article", max_age=0)
        _serve(state, body_a, '"a2"')  # 되돌림 — A로 돌아왔다
        reverted = anchor.fetch(f"{base_url}/article", max_age=0)
        assert reverted.outcome in {"changed", "unchanged"}

        diff = anchor.diff_versions(f"{base_url}/article")

    assert diff.strip(), "되돌림 직후의 기본 diff가 비었다"
    assert "B 판본이다" in diff, "직전에 서빙되던 판본이 diff에 없다"


def test_diff_shows_a_transition_that_actually_happened(tmp_path, fixture_server):
    """A→B→A→C→A에서 기본 diff는 **C→A**여야 한다 (D-083).

    캡처 시각으로 한 칸 물러나면 B가 나오는데, B에서 A로 간 적은 없다.
    일어난 적 없는 전이를 근거로 제시하는 것은 판정을 틀리는 것과 같다.
    """
    base_url, state = fixture_server
    body_a = state.html
    body_b = article_html(nonce="b", extra_sentence=" B 판본이다.")
    body_c = article_html(nonce="c", extra_sentence=" C 판본이다.")
    with Anchor(config=_config(tmp_path)) as anchor:
        url = f"{base_url}/article"
        anchor.fetch(url)
        for index, body in enumerate((body_b, body_a, body_c, body_a)):
            _serve(state, body, f'"v{index}"')
            anchor.fetch(url, max_age=0)

        diff = anchor.diff_versions(url)

    assert "C 판본이다" in diff, "직전에 서빙되던 C가 diff에 없다"
    assert "B 판본이다" not in diff, "일어난 적 없는 전이(B→A)를 보여줬다"


def test_every_stored_version_is_reachable_by_a_ref(tmp_path, fixture_server):
    """중간 판본에 **도달할 방법이 있어야** 한다 (D-083).

    저장은 해 놓고 어떤 `latest~N`으로도 가리킬 수 없으면, 그 판본은
    사실상 없는 것이다 — 인용 당시 텍스트를 확인하는 이 도구의 목적에
    정면으로 걸린다.
    """
    base_url, state = fixture_server
    body_a = state.html
    body_b = article_html(nonce="b", extra_sentence=" B 판본이다.")
    body_c = article_html(nonce="c", extra_sentence=" C 판본이다.")
    with Anchor(config=_config(tmp_path)) as anchor:
        url = f"{base_url}/article"
        anchor.fetch(url)
        for index, body in enumerate((body_b, body_a, body_c, body_a)):
            _serve(state, body, f'"v{index}"')
            anchor.fetch(url, max_age=0)

        document = anchor._resolve_document(url)
        stored = {v.id for v in anchor._repository.list_versions(document.id)}
        reachable = set()
        for back in range(len(stored)):
            version, _ = anchor.get_version(document_id=document.id, ref=f"latest~{back}")
            reachable.add(version.id)

    assert stored == reachable, f"도달 불가능한 판본 {len(stored - reachable)}개"


# -- D-084: 재검증이 남았는가 -------------------------------------------------


def test_reverted_body_counts_as_pending_verification(tmp_path, fixture_server):
    """되돌림으로 현재 본문이 바뀌면 **재검증 대상**이어야 한다 (D-084).

    `current_version.captured_at` 기준이면 되돌림에서 재사용된 행의 옛
    시각과 비교하게 돼, 본문이 방금 바뀌었는데도 False가 나온다. 그 필터로
    대상을 좁히는 워크플로는 판정이 뒤집힌 문서를 영영 재검증하지 않는다.
    """
    base_url, state = fixture_server
    body_a = state.html
    body_b = article_html(nonce="b", extra_sentence=" B 판본이다.")
    with Anchor(config=_config(tmp_path)) as anchor:
        url = f"{base_url}/article"
        fetched = anchor.fetch(url, include_content=True)
        quote = "링크는 살아 있지만 내용이 바뀌는 인용 표류가 가장 위험하다."
        assert quote in (fetched.content or "")
        anchor.cite(url, quote=quote)
        anchor.verify()
        assert anchor.list_documents(has_pending_verification=True).documents == ()

        _serve(state, body_b, '"b"')
        anchor.fetch(url, max_age=0)
        anchor.verify()
        assert anchor.list_documents(has_pending_verification=True).documents == ()

        _serve(state, body_a, '"a2"')  # 되돌림 — 앵커의 판정이 뒤집힌다
        anchor.fetch(url, max_age=0)
        pending = anchor.list_documents(has_pending_verification=True).documents

    assert len(pending) == 1, "본문이 되돌아왔는데 재검증 대상이 아니라고 답했다"


def test_unchanged_reobservation_is_still_an_observation(tmp_path, fixture_server):
    """본문이 그대로여도 **관측은 일어났다** (D-083).

    포인터가 안 바뀐다는 이유로 기록하지 않으면 `last_observed_at`이 첫
    관측에 멈춘 채 굳는다 — 반년간 매일 확인한 문서가 "반년 전에 마지막으로
    살아 있었다"고 말한다. 포인터의 이동과 관측의 발생은 다른 사건이다.
    """
    base_url, state = fixture_server
    with Anchor(config=_config(tmp_path)) as anchor:
        url = f"{base_url}/article"
        first = anchor.fetch(url)
        version = anchor._repository.get_version(first.version_id)
        assert version is not None
        before = version.last_observed_seq

        again = anchor.fetch(url, max_age=0)
        assert again.outcome in {"not_modified", "unchanged"}
        after = anchor._repository.get_version(first.version_id)

    assert after is not None
    assert after.last_observed_seq > before, "변화가 없다는 이유로 관측을 기록하지 않았다"


def test_a_new_version_sorts_first_before_it_is_observed(tmp_path, fixture_server):
    """삽입 시점에 이미 순번을 받아야 한다 (D-083).

    삽입과 관측 기록은 별개의 트랜잭션이다. 그 사이에 프로세스가 죽으면
    방금 넣은 판본이 순번 0으로 남아 **가장 오래된 것처럼** 정렬된다 —
    `latest~1`이 지금 본문을 가리키게 된다.
    """
    base_url, state = fixture_server
    with Anchor(config=_config(tmp_path)) as anchor:
        url = f"{base_url}/article"
        anchor.fetch(url)
        _serve(state, article_html(nonce="b", extra_sentence=" B 판본이다."), '"b"')
        anchor.fetch(url, max_age=0)
        document = anchor._resolve_document(url)

        # 관측 기록 없이 새 판본만 넣는다 (삽입 직후 중단된 상태).
        inserted = anchor._repository.insert_version(
            document_id=document.id,
            text_hash="b3:new",
            raw_hash="b3:raw-new",
            pipeline_version="p",
            captured_at="2026-08-18T00:00:00Z",
            byte_size=10,
            normalized_text="새 본문이다.",
            http_status=200,
        )
        ordered = anchor._repository.list_versions_by_observation(document.id)

    assert ordered[0].id == inserted.id, "방금 넣은 판본이 가장 오래된 것처럼 정렬됐다"
