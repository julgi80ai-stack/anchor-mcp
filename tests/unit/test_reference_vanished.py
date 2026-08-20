# SPDX-License-Identifier: Apache-2.0
"""사라진 참조 (D-251) — 저장소 계층.

gc가 실제로 지우기 시작하자(D-249) 잠자던 경합이 깨어났다. 앵커를 달려던
버전과 대조하던 버전이 그 사이 회수되면 생 `sqlite3.IntegrityError`가 공개
API 밖으로 샜다 — 라이브러리도 `AnchorError` 하나로 받는다는 SPEC §8의
약속이 깨진다.

축을 먼저 연다 (조치 절차 6):

① **사라진 대상** — 문서(D-185, 병합이 지운다) / 버전(D-251, gc가 지운다).
② **진입점** — `insert_anchor(created_version)` / `insert_verification(
   checked_version)` / `get_version_text`.
③ **사라진 뒤의 계약** — 이 둘은 같은 무게가 아니다. 앵커의 버전은 **계약**
   (인용 당시 원문을 되살린다, §1.2)이라 없으면 앵커를 만들 수 없다. 검증의
   버전은 **관측 로그**라 스키마가 이미 `ON DELETE SET NULL`로 답을 정해 뒀다
   — 회수되면 NULL이다. 삽입 직후에 회수돼도 같은 행이 남아야 한다.

삭제는 전부 **실제 gc**가 한다. 생 `DELETE`로 만든 상태는 gc가 실제로 만드는
상태와 다를 수 있고, 그러면 픽스처가 정상값을 하드코딩한 셈이 된다(조치 절차 4).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from anchor.errors import AnchorError, DocumentNotFound, VersionNotFound
from anchor.store.repository import Repository

NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)


def ago(**kwargs) -> str:
    return (NOW - timedelta(**kwargs)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def repo(tmp_path):
    repository = Repository(tmp_path / "vanished.db")
    yield repository
    repository.close()


def seed(repo: Repository, *, versions: int = 3, index: int = 0):
    """문서 하나에 판본 여러 개. 마지막 것이 지금 서빙되는 판본이다."""
    document = repo.create_document(
        url=f"https://example.test/doc-{index}",
        original_url=f"https://example.test/doc-{index}",
        title=f"문서 {index}",
        now=ago(days=400),
    )
    made = []
    for v in range(versions):
        body = f"v{v:03d} 판본의 본문이다. " + ("문장이 계속 이어진다. " * 30)
        made.append(
            repo.insert_version(
                document_id=document.id,
                text_hash=f"b3:t-{document.id}-{v}",
                raw_hash=f"b3:r-{document.id}-{v}",
                pipeline_version="trafilatura/2.2.0+norm/3",
                captured_at=ago(days=versions - v),
                byte_size=len(body.encode()),
                normalized_text=body,
                http_status=200,
                observe=(v == versions - 1),
            )
        )
    return document, made


def reclaim_oldest(repo: Repository, made) -> str:
    """gc를 실제로 돌려 가장 오래된 판본을 회수한다.

    회수됐다는 것을 **관측해서** 돌려준다 — 기대값을 적어 두면 gc의 보호
    집합이 바뀐 날 이 픽스처가 조용히 무효가 된다.
    """
    deleted, _freed = repo.collect_garbage_versions(keep=1)
    assert deleted > 0, "gc가 아무것도 회수하지 않았다 — 픽스처가 무효다"
    gone = made[0].id
    assert repo.get_version(gone) is None, "회수 대상이 실제로 사라지지 않았다"
    return gone


# -- 축② 진입점: insert_anchor -----------------------------------------------


def test_insert_anchor_reports_vanished_version_as_domain_error(repo):
    """앵커의 버전은 **계약**이다. 사라졌으면 그렇다고 말한다 (생 예외가 아니라)."""
    document, made = seed(repo)
    gone = reclaim_oldest(repo, made)

    with pytest.raises(VersionNotFound) as caught:
        repo.insert_anchor(
            document_id=document.id,
            created_version=gone,
            exact="v000 판본의 본문이다.",
            prefix="", suffix="", position_hint=0,
            exact_hash="b3:e-0", quality="ok", note=None,
            created_at=ago(days=1), cited_url=document.original_url,
        )
    assert gone in str(caught.value)
    assert isinstance(caught.value, AnchorError)
    # 무슨 일이 일어났는지 말해야 한다 — 문서가 아니라 **버전**이 사라졌다.
    assert not isinstance(caught.value, DocumentNotFound)


def test_insert_anchor_still_reports_vanished_document(repo):
    """D-185 회귀 — 옆에 한 줄을 더했다고 원래 있던 판별이 사라지면 안 된다."""
    document, made = seed(repo)
    with repo._connection as connection:
        connection.execute("DELETE FROM documents WHERE id = ?", (document.id,))

    with pytest.raises(DocumentNotFound):
        repo.insert_anchor(
            document_id=document.id,
            created_version=made[-1].id,
            exact="v002 판본의 본문이다.",
            prefix="", suffix="", position_hint=0,
            exact_hash="b3:e-1", quality="ok", note=None,
            created_at=ago(days=1), cited_url=document.original_url,
        )


def test_insert_anchor_still_succeeds_on_a_live_version(repo):
    """판별력 확인 — 살아 있는 버전에는 그대로 앵커가 달린다."""
    document, made = seed(repo)
    reclaim_oldest(repo, made)
    created = repo.insert_anchor(
        document_id=document.id,
        created_version=made[-1].id,
        exact="v002 판본의 본문이다.",
        prefix="", suffix="", position_hint=0,
        exact_hash="b3:e-2", quality="ok", note=None,
        created_at=ago(days=1), cited_url=document.original_url,
    )
    assert created.created_version == made[-1].id
    # 인용 복원 불변식: 앵커가 붙잡은 버전은 언제나 읽힌다.
    assert repo.get_version_text(created.created_version)


# -- 축② 진입점: insert_verification ------------------------------------------


def test_insert_verification_records_null_when_version_vanished(repo):
    """검증의 버전은 **관측 로그**다 — 회수되면 NULL이 답이라고 스키마가 이미 정했다.

    그 답은 삽입 **직전에** 회수된 경우에도 같아야 한다. 1밀리초 뒤에
    회수됐다면 `ON DELETE SET NULL`이 같은 NULL을 만들었을 것이다.
    """
    document, made = seed(repo)
    anchor = repo.insert_anchor(
        document_id=document.id,
        created_version=made[-1].id,
        exact="v002 판본의 본문이다.",
        prefix="", suffix="", position_hint=0,
        exact_hash="b3:e-3", quality="ok", note=None,
        created_at=ago(days=1), cited_url=document.original_url,
    )
    gone = reclaim_oldest(repo, made)

    recorded = repo.insert_verification(
        anchor_id=anchor.id,
        checked_version=gone,
        checked_at=ago(hours=1),
        state="INTACT",
        match_score=1.0,
        edit_distance=0,
        found_offset=0,
        found_text=None,
        elapsed_ms=7,
    )
    assert recorded is None, "회수된 버전을 가리키는 척해서는 안 된다"

    rows = repo._connection.execute(
        "SELECT checked_version, state FROM verifications WHERE anchor_id = ?",
        (anchor.id,),
    ).fetchall()
    assert len(rows) == 1, "관측 기록 자체가 사라졌다"
    assert rows[0]["checked_version"] is None
    assert rows[0]["state"] == "INTACT", "판정을 바꾸지 않는다"


def test_insert_verification_returns_the_version_it_actually_recorded(repo):
    """판별력 확인 — 살아 있는 버전은 그대로 기록되고 그대로 돌려준다."""
    document, made = seed(repo)
    anchor = repo.insert_anchor(
        document_id=document.id,
        created_version=made[-1].id,
        exact="v002 판본의 본문이다.",
        prefix="", suffix="", position_hint=0,
        exact_hash="b3:e-4", quality="ok", note=None,
        created_at=ago(days=1), cited_url=document.original_url,
    )
    recorded = repo.insert_verification(
        anchor_id=anchor.id,
        checked_version=made[-1].id,
        checked_at=ago(hours=1),
        state="ALTERED",
        match_score=0.9,
        edit_distance=3,
        found_offset=1,
        found_text="바뀐 문장",
        elapsed_ms=9,
    )
    assert recorded == made[-1].id
    row = repo._connection.execute(
        "SELECT checked_version FROM verifications WHERE anchor_id = ?", (anchor.id,)
    ).fetchone()
    assert row["checked_version"] == made[-1].id


def test_insert_verification_still_rejects_an_unknown_anchor(repo):
    """앵커 쪽 FK는 완화하지 않는다 — 그건 계약이지 관측 로그가 아니다."""
    with pytest.raises((sqlite3.IntegrityError, AnchorError)):
        repo.insert_verification(
            anchor_id="없는-앵커",
            checked_version=None,
            checked_at=ago(hours=1),
            state="INTACT",
            match_score=1.0,
            edit_distance=0,
            found_offset=0,
            found_text=None,
            elapsed_ms=1,
        )


# -- 축② 진입점: get_version_text ---------------------------------------------


def test_get_version_text_reports_missing_version_as_domain_error(repo):
    """`KeyError`는 `AnchorError`가 아니다 — CLI가 트레이스백으로 죽는다."""
    document, made = seed(repo)
    gone = reclaim_oldest(repo, made)

    with pytest.raises(VersionNotFound) as caught:
        repo.get_version_text(gone)
    assert isinstance(caught.value, AnchorError)
    assert gone in str(caught.value)
