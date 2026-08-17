# SPDX-License-Identifier: Apache-2.0
"""gc 보존 규칙 (SPEC §4.2): 최근 keep개 + 앵커·검증 참조 버전은 남는다."""

from __future__ import annotations

import pytest

from anchor.store.repository import Repository


@pytest.fixture
def repo(tmp_path):
    repository = Repository(tmp_path / "gc.db")
    yield repository
    repository.close()


def seed_document(repo: Repository, version_count: int) -> tuple[str, list[str]]:
    doc = repo.create_document(
        url="https://example.com/a", original_url="https://example.com/a",
        title=None, now="2026-08-01T00:00:00Z",
    )
    version_ids = []
    for index in range(version_count):
        version = repo.insert_version(
            document_id=doc.id,
            text_hash=f"b3:text-{index:03d}",
            raw_hash=f"b3:raw-{index:03d}",
            pipeline_version="trafilatura/2.2.0+norm/1",
            captured_at=f"2026-08-01T00:{index:02d}:00Z",
            byte_size=100,
            normalized_text=f"버전 {index}의 본문이다. " * 5,
            http_status=200,
        )
        version_ids.append(version.id)
    return doc.id, version_ids


def test_gc_deletes_only_orphans_beyond_keep(repo):
    document_id, version_ids = seed_document(repo, 25)
    # 오래된 버전 하나(2번째)를 앵커가 참조한다.
    repo.insert_anchor(
        document_id=document_id, created_version=version_ids[1],
        exact="버전 1의 본문이다.", prefix="", suffix="", position_hint=0,
        exact_hash="b3:x", quality="ok", note=None, created_at="2026-08-01T01:00:00Z",
    )

    deleted, freed = repo.collect_garbage_versions(keep=20)

    # 후보는 가장 오래된 5개(0~4), 그중 앵커가 참조하는 1개는 보존 → 4개 삭제.
    assert deleted == 4
    assert freed > 0
    remaining = {v.id for v in repo.list_versions(document_id)}
    assert version_ids[1] in remaining          # 앵커 참조 버전 생존
    assert version_ids[0] not in remaining      # 고아는 삭제
    assert set(version_ids[5:]).issubset(remaining)  # 최근 20개 생존
    assert len(remaining) == 21


def test_gc_preserves_verification_referenced_versions(repo):
    document_id, version_ids = seed_document(repo, 25)
    anchor = repo.insert_anchor(
        document_id=document_id, created_version=version_ids[24],
        exact="버전 24의 본문이다.", prefix="", suffix="", position_hint=0,
        exact_hash="b3:x", quality="ok", note=None, created_at="2026-08-01T01:00:00Z",
    )
    repo.insert_verification(
        anchor_id=anchor.id, checked_version=version_ids[2],
        checked_at="2026-08-02T00:00:00Z", state="INTACT", match_score=1.0,
        edit_distance=0, found_offset=0, found_text=None, elapsed_ms=1,
    )

    deleted, _ = repo.collect_garbage_versions(keep=20)

    remaining = {v.id for v in repo.list_versions(document_id)}
    assert version_ids[2] in remaining  # 검증 이력이 참조 → 보존
    assert deleted == 4  # 0,1,3,4


def test_gc_noop_when_under_keep(repo):
    seed_document(repo, 5)
    assert repo.collect_garbage_versions(keep=20) == (0, 0)
