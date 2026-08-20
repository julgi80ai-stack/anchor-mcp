# SPDX-License-Identifier: Apache-2.0
"""회수와 보존 (SPEC §4.2) — 8단계-라: D-248 D-249 D-250.

축을 먼저 연다. 결함 하나를 재현하는 픽스처는 같은 축의 이웃 결함을 전부
살려 보낸다(조치 절차 6).

① **버전이 붙잡힌 이유** — 앵커(`created_version`) / 현재 서빙 포인터
   (`documents.current_version`) / 검증 로그(`verifications.checked_version`) /
   문서당 최근 N개. 앞의 둘은 **계약**이고 셋째는 **관측 로그**다. 셋을 같은
   무게로 두면 회수가 영영 0건이 된다.
② **검증 이력의 나이** — 최신 1건 / 보존 기간 이내 / 보존 기간 초과.
③ **앵커당 검증 건수** — 1건 / 다수.
④ **저장소 규모** — 계획(EXPLAIN)은 규모와 무관하게 성립해야 하고, 락 점유는
   규모가 커질수록 드러난다.
⑤ **동시성** — gc·프루닝이 도는 동안 같은 프로세스의 다른 조회가 진행되는가.

정상값을 하드코딩하지 않는다(조치 절차 4). 지연 상한은 **같은 저장소의 유휴
지연을 재서** 배수로 잡고, 보존 기간 경계는 설정에서 읽어 만든다.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from anchor.config import Config
from anchor.service import Anchor
from anchor.store.repository import _RECLAIMABLE_VERSIONS_SQL, Repository

NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)


def iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def ago(**kwargs) -> str:
    return iso(NOW - timedelta(**kwargs))


@pytest.fixture
def repo(tmp_path):
    repository = Repository(tmp_path / "reclaim.db")
    yield repository
    repository.close()


def make_document(repo: Repository, index: int = 0) -> str:
    document = repo.create_document(
        url=f"https://example.test/doc-{index}",
        original_url=f"https://example.test/doc-{index}",
        title=f"문서 {index}",
        now=ago(days=400),
    )
    return document.id


def add_version(repo: Repository, document_id: str, tag: str, *, day: int, serve: bool):
    body = f"{tag} 판본의 본문이다. " + ("문장이 계속 이어진다. " * 30)
    return repo.insert_version(
        document_id=document_id,
        text_hash=f"b3:t-{document_id}-{tag}",
        raw_hash=f"b3:r-{document_id}-{tag}",
        pipeline_version="trafilatura/2.2.0+norm/3",
        captured_at=ago(days=day),
        byte_size=len(body.encode()),
        normalized_text=body,
        http_status=200,
        observe=serve,
    )


def seed_verify_workflow(
    repo: Repository,
    *,
    documents: int = 1,
    versions_per_doc: int = 41,
    anchor_on: int = 0,
    verify_every_version: bool = True,
) -> dict:
    """이 프로젝트가 권장하는 워크플로 — 페치하고, 인용하고, **매번 재검증**한다.

    축①③을 함께 연다: 각 문서는 앵커 1개를 갖고, 새 판본이 생길 때마다 그
    앵커를 재검증한다. 그러면 `verifications.checked_version`이 사실상 모든
    버전을 붙잡는다.
    """
    seeded = {"documents": [], "versions": {}, "anchors": {}}
    for d in range(documents):
        document_id = make_document(repo, d)
        version_ids = []
        for v in range(versions_per_doc):
            version = add_version(
                repo, document_id, f"v{v:03d}",
                day=versions_per_doc - v, serve=(v == versions_per_doc - 1),
            )
            version_ids.append(version.id)
        anchor = repo.insert_anchor(
            document_id=document_id,
            created_version=version_ids[anchor_on],
            exact=f"{'v%03d' % anchor_on} 판본의 본문이다.",
            prefix="", suffix="", position_hint=0,
            exact_hash=f"b3:e-{d}", quality="ok", note=None,
            created_at=ago(days=300), cited_url=f"https://example.test/doc-{d}",
        )
        if verify_every_version:
            for v, version_id in enumerate(version_ids):
                repo.insert_verification(
                    anchor_id=anchor.id, checked_version=version_id,
                    checked_at=ago(days=versions_per_doc - v),
                    state="INTACT", match_score=1.0, edit_distance=0,
                    found_offset=0, found_text=None, elapsed_ms=3,
                )
        seeded["documents"].append(document_id)
        seeded["versions"][document_id] = version_ids
        seeded["anchors"][document_id] = anchor
    return seeded


def version_ids(repo: Repository, document_id: str) -> set[str]:
    return {version.id for version in repo.list_versions(document_id)}


# -- D-249: gc가 영원히 0건을 지운다 ----------------------------------------


def test_gc_reclaims_versions_under_the_recommended_verify_workflow(repo):
    """1문서·41버전·매회 재검증 → 조치 전에는 `deleted_versions == 0`이었다.

    SPEC §4.2가 "문서당 최근 N개 보존"을 약속하고서 그 규칙에 **영영 도달하지
    못한다**. 정기적으로 verify하는 것은 이 프로젝트가 권장하는 워크플로다.
    """
    seeded = seed_verify_workflow(repo, versions_per_doc=41)
    document_id = seeded["documents"][0]

    result = repo.collect_garbage(keep=5)

    assert result["deleted_versions"] > 0, "검증 이력이 버전을 영구히 붙잡고 있다"
    remaining = version_ids(repo, document_id)
    anchor = seeded["anchors"][document_id]
    served = repo.current_version(document_id)
    # 남는 것은 **계약**뿐이다: 인용된 버전 + 지금 서빙되는 버전 + 최근 keep개.
    contract = {anchor.created_version, served.id} | set(seeded["versions"][document_id][-5:])
    assert remaining == contract, sorted(remaining - contract)


def test_gc_keeps_every_cited_version_no_matter_how_old(repo):
    """가장 오래된 판본을 인용했어도 그 버전은 회수 대상이 아니다 (SPEC §1.2)."""
    seeded = seed_verify_workflow(repo, versions_per_doc=30, anchor_on=0)
    document_id = seeded["documents"][0]
    cited = seeded["anchors"][document_id].created_version

    repo.collect_garbage(keep=3)

    assert cited in version_ids(repo, document_id)
    assert repo.get_version(cited) is not None


def test_gc_keeps_the_currently_served_version_even_when_it_is_old(repo):
    """되돌림 — 지금 서빙되는 본문이 캡처 시각 최대값이 아닐 수 있다 (D-080)."""
    document_id = make_document(repo)
    ids = [add_version(repo, document_id, f"v{i}", day=40 - i, serve=False).id for i in range(30)]
    # 원문이 옛 판본으로 되돌아갔다 — 포인터를 오래된 행으로 옮긴다.
    repo.observe_version(document_id, ids[0], ago(days=1))
    repo._connection.execute(
        "UPDATE documents SET current_version = ? WHERE id = ?", (ids[0], document_id)
    )

    repo.collect_garbage(keep=5)

    assert ids[0] in version_ids(repo, document_id), "서빙 중인 본문이 회수됐다"


def test_verification_row_survives_the_version_it_checked(repo):
    """관측 기록은 남고 **그때 본 버전 참조만** 사라진다 (ON DELETE SET NULL)."""
    seeded = seed_verify_workflow(repo, versions_per_doc=20)
    document_id = seeded["documents"][0]
    before = repo._connection.execute("SELECT COUNT(*) FROM verifications").fetchone()[0]

    repo.collect_garbage(keep=2)

    after = repo._connection.execute(
        "SELECT COUNT(*), SUM(checked_version IS NULL) FROM verifications"
    ).fetchone()
    assert after[0] == before, "검증 이력 행이 사라졌다 — 관측 로그는 남아야 한다"
    assert after[1] > 0, "회수된 버전을 가리키던 참조가 NULL이 되지 않았다"
    # "T에 검증했다·결과는 무엇"은 그대로다.
    states = repo._connection.execute(
        "SELECT DISTINCT state FROM verifications"
    ).fetchall()
    assert [row[0] for row in states] == ["INTACT"]


def test_a_reclaimed_check_reads_as_needing_recheck_not_as_verified(repo, tmp_path):
    """D-084 의미론: 어느 버전을 검증했는지 모르면 **보수적으로 재검증 필요**."""
    seeded = seed_verify_workflow(repo, versions_per_doc=20)
    document_id = seeded["documents"][0]
    anchor = seeded["anchors"][document_id]
    # 마지막 검증이 **회수 대상인** 옛 버전을 본 상태로 만든다. [0]은 앵커가
    # 인용한 버전이라 계약으로 보호되므로 [1]을 쓴다 — 픽스처가 스스로
    # 결함을 비켜 가지 않게 한다.
    reclaimable = seeded["versions"][document_id][1]
    assert reclaimable != anchor.created_version
    repo._connection.execute("DELETE FROM verifications")
    repo.insert_verification(
        anchor_id=anchor.id, checked_version=reclaimable,
        checked_at=ago(days=1), state="INTACT", match_score=1.0, edit_distance=0,
        found_offset=0, found_text=None, elapsed_ms=3,
    )
    repo.collect_garbage(keep=2)

    checked = repo.latest_verified_version(anchor.id)
    assert checked is not None, "검증 시각 기록까지 사라졌다"
    assert checked[1] is None, "회수된 버전 참조가 남아 있다"
    served = repo.current_version(document_id)
    assert checked[1] != served.id, "NULL이 '현재 본문을 검증함'으로 읽히면 안 된다"


# -- 인용 복원 불변식 -------------------------------------------------------


def test_repeated_gc_never_loses_a_cited_version_text(repo):
    """gc를 몇 번 돌리든 **모든 앵커**의 인용 당시 본문이 복원된다.

    이것이 깨지면 조치 전체가 무효다 (SPEC §1.2).
    """
    seeded = seed_verify_workflow(repo, documents=4, versions_per_doc=25, anchor_on=1)
    originals = {
        document_id: repo.get_version_text(anchor.created_version)
        for document_id, anchor in seeded["anchors"].items()
    }
    assert all(text for text in originals.values()), "픽스처 전제: 본문이 실제로 있다"

    for round_index in range(5):
        # 라운드마다 새 판본과 새 검증이 쌓인다 — 실제 워크플로가 그렇다.
        for document_id, anchor in seeded["anchors"].items():
            fresh = add_version(
                repo, document_id, f"r{round_index}", day=0, serve=True
            )
            repo.insert_verification(
                anchor_id=anchor.id, checked_version=fresh.id,
                checked_at=ago(days=0), state="INTACT", match_score=1.0,
                edit_distance=0, found_offset=0, found_text=None, elapsed_ms=3,
            )
        repo.collect_garbage(keep=3)
        for document_id, anchor in seeded["anchors"].items():
            assert repo.get_version(anchor.created_version) is not None, (
                f"라운드 {round_index}: 인용 당시 버전이 사라졌다 ({document_id})"
            )
            assert repo.get_version_text(anchor.created_version) == originals[document_id]


# -- D-248: 인덱스 부재 -----------------------------------------------------


REFERENCE_TABLES = ("anchors", "documents")


def test_gc_candidate_scan_does_not_full_scan_the_reference_tables(repo):
    """보호 여부 확인은 **탐색**이어야 한다 — 후보 행마다 전체 훑기면 2차 곡선."""
    seed_verify_workflow(repo, documents=3, versions_per_doc=25)
    plan = [
        row[-1]
        for row in repo._connection.execute(
            "EXPLAIN QUERY PLAN " + _RECLAIMABLE_VERSIONS_SQL, (20,)
        ).fetchall()
    ]
    offenders = [line for line in plan if line.startswith("SCAN ") and (
        any(line.startswith(f"SCAN {table}") for table in REFERENCE_TABLES)
        or line in ("SCAN a", "SCAN d")
    )]
    assert offenders == [], f"{offenders} — 계획 전체: {plan}"


def test_delete_of_a_version_does_not_full_scan_the_referencing_tables(repo):
    """FK 확인·SET NULL도 인덱스를 타야 한다 — 없으면 삭제가 2차 곡선이다."""
    seed_verify_workflow(repo, versions_per_doc=10)
    indexes = {
        row[0]
        for row in repo._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        ).fetchall()
    }
    for column_index in (
        "idx_anchors_created_version",
        "idx_verif_version",
        "idx_documents_current_version",
    ):
        assert column_index in indexes, sorted(indexes)


def _probe_latency_ms(repo: Repository, version_id: str, rounds: int = 40) -> float:
    samples = []
    for _ in range(rounds):
        started = time.perf_counter()
        repo.get_version(version_id)
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    return samples[len(samples) // 2]


def test_gc_does_not_hold_the_store_for_its_whole_duration(repo):
    """gc가 도는 동안 같은 프로세스의 다른 조회가 **진행돼야** 한다.

    상한은 **이 저장소의 유휴 지연을 재서** 배수로 잡는다 — 머신마다 다른
    절대값을 하드코딩하면 픽스처가 스스로 거짓말을 한다(조치 절차 4).
    """
    seeded = seed_verify_workflow(repo, documents=40, versions_per_doc=40)
    probe_id = seeded["versions"][seeded["documents"][0]][-1]
    idle = _probe_latency_ms(repo, probe_id)

    samples: list[float] = []
    stop = threading.Event()

    def prober() -> None:
        while not stop.is_set():
            started = time.perf_counter()
            repo.get_version(probe_id)
            samples.append((time.perf_counter() - started) * 1000)
            time.sleep(0.001)

    thread = threading.Thread(target=prober, daemon=True)
    thread.start()
    try:
        repo.collect_garbage(keep=5)
    finally:
        stop.set()
        thread.join(timeout=10)

    assert len(samples) > 10, f"gc가 도는 내내 조회가 한 번도 못 들어갔다 ({len(samples)}건)"
    worst = max(samples)
    # 유휴 지연의 배수로 잰다. 조치 전에는 3만 배였다.
    assert worst < max(idle * 400, 250), f"최악 {worst:.1f}ms / 유휴 {idle:.3f}ms"


# -- D-250: 프루닝 부재 -----------------------------------------------------


def test_prunes_verifications_past_retention_but_keeps_the_latest_per_anchor(repo):
    """축②③: 최신 1건은 나이와 무관하게 남고, 그 밖은 보존 기간이 지나면 간다.

    최신 1건은 D-084(어느 버전을 검증했는가)가 쓴다.
    """
    document_id = make_document(repo)
    version = add_version(repo, document_id, "v0", day=1, serve=True)
    anchor = repo.insert_anchor(
        document_id=document_id, created_version=version.id,
        exact="v0 판본의 본문이다.", prefix="", suffix="", position_hint=0,
        exact_hash="b3:e", quality="ok", note=None, created_at=ago(days=300),
        cited_url="https://example.test/doc-0",
    )
    # 앵커 B는 **아주 오래된 검증 1건뿐**이다 — 최신 1건 규칙이 이 행을 살린다.
    lonely = repo.insert_anchor(
        document_id=document_id, created_version=version.id,
        exact="문장이 계속 이어진다. 문장이 계속 이어진다.", prefix="", suffix="",
        position_hint=20, exact_hash="b3:e2", quality="ok", note=None,
        created_at=ago(days=300), cited_url="https://example.test/doc-0",
    )
    ages = [900, 400, 200, 91, 89, 3]
    for age in ages:
        repo.insert_verification(
            anchor_id=anchor.id, checked_version=version.id, checked_at=ago(days=age),
            state="INTACT", match_score=1.0, edit_distance=0, found_offset=0,
            found_text=None, elapsed_ms=3,
        )
    repo.insert_verification(
        anchor_id=lonely.id, checked_version=version.id, checked_at=ago(days=900),
        state="INTACT", match_score=1.0, edit_distance=0, found_offset=0,
        found_text=None, elapsed_ms=3,
    )

    pruned = repo.prune_verifications(before=ago(days=90))

    survivors = sorted(
        row[0] for row in repo._connection.execute(
            "SELECT checked_at FROM verifications WHERE anchor_id = ?", (anchor.id,)
        ).fetchall()
    )
    assert survivors == sorted([ago(days=89), ago(days=3)]), survivors
    assert pruned == 4
    assert repo.latest_verified_version(lonely.id) is not None, (
        "검증이 1건뿐인 앵커의 유일한 기록이 나이 때문에 사라졌다"
    )


def test_prunes_fetch_log_past_retention_and_keeps_the_reported_window(repo):
    document_id = make_document(repo)
    for age in (500, 401, 399, 40, 29, 1):
        repo.log_fetch(
            document_id=document_id, requested_at=ago(days=age), outcome="cache_hit",
            http_status=200, bytes_down=0, elapsed_ms=4,
        )

    pruned = repo.prune_fetch_log(before=ago(days=400))

    remaining = sorted(
        row[0] for row in repo._connection.execute(
            "SELECT requested_at FROM fetch_log"
        ).fetchall()
    )
    assert pruned == 2
    assert remaining == sorted([ago(days=399), ago(days=40), ago(days=29), ago(days=1)])
    # SPEC §7.7이 보고하는 30일 창은 통째로 살아 있어야 한다.
    window = repo.fetch_stats_since(ago(days=30))
    assert window["requests"] == 2


def test_prunes_expired_robots_cache_but_keeps_fresh_entries(repo):
    repo.set_robots("https://stale.test", "User-agent: *", 200, ago(days=3))
    repo.set_robots("https://fresh.test", "User-agent: *\nDisallow:", 200, ago(hours=1))

    pruned = repo.prune_robots_cache(before=ago(days=1))

    assert pruned == 1
    assert repo.get_robots("https://stale.test") is None
    assert repo.get_robots("https://fresh.test") is not None


def test_document_aliases_are_not_pruned(repo):
    """별칭은 조회 키다 — 지우면 캐시가 깨지고 같은 문서가 다시 만들어진다.

    나이 열도 없고 행도 작다. **제거하지 않는 것이 결정이다** (8단계-라).
    """
    document_id = make_document(repo)
    add_version(repo, document_id, "v0", day=1, serve=True)
    repo.add_alias("https://old.test/doc-0", document_id)

    repo.collect_garbage(keep=1)

    assert repo.get_document_by_any_url("https://old.test/doc-0") is not None


def test_pruning_releases_the_store_between_batches(repo):
    """긴 트랜잭션 하나로 지우면 D-248에서 고친 것을 도로 만든다."""
    document_id = make_document(repo)
    version = add_version(repo, document_id, "v0", day=1, serve=True)
    for index in range(4000):
        repo.log_fetch(
            document_id=document_id, requested_at=ago(days=500 + index % 30),
            outcome="cache_hit", http_status=200, bytes_down=0, elapsed_ms=1,
        )
    idle = _probe_latency_ms(repo, version.id)

    samples: list[float] = []
    stop = threading.Event()

    def prober() -> None:
        while not stop.is_set():
            started = time.perf_counter()
            repo.get_version(version.id)
            samples.append((time.perf_counter() - started) * 1000)

    thread = threading.Thread(target=prober, daemon=True)
    thread.start()
    try:
        pruned = repo.prune_fetch_log(before=ago(days=400))
    finally:
        stop.set()
        thread.join(timeout=10)

    assert pruned == 4000
    assert len(samples) > 10, "프루닝이 도는 내내 조회가 못 들어갔다"
    assert max(samples) < max(idle * 400, 250), f"최악 {max(samples):.1f}ms / 유휴 {idle:.3f}ms"


# -- 진입점: 라이브러리·CLI --------------------------------------------------


def test_library_gc_reports_what_it_pruned(tmp_path):
    """SPEC §8 라이브러리 경로도 같은 보존 정책을 받는다."""
    path = tmp_path / "lib.db"
    repository = Repository(path)
    try:
        seeded = seed_verify_workflow(repository, versions_per_doc=30)
        # 보존 기간(기본 90일)을 넘긴 검증 이력 — 최신 1건이 아니므로 간다.
        for age in (900, 800):
            repository.insert_verification(
                anchor_id=seeded["anchors"][seeded["documents"][0]].id,
                checked_version=None, checked_at=ago(days=age), state="UNREACHABLE",
                match_score=None, edit_distance=None, found_offset=None,
                found_text=None, elapsed_ms=1,
            )
        repository.log_fetch(
            document_id=repository.list_documents()[0].id, requested_at=ago(days=900),
            outcome="cache_hit", http_status=200, bytes_down=0, elapsed_ms=1,
        )
        repository.set_robots("https://stale.test", "User-agent: *", 200, ago(days=9))
    finally:
        repository.close()

    with Anchor(db_path=path, config=Config(db_path=path, keep_versions=5)) as anchor:
        result = anchor.collect_garbage()

    assert result["deleted_versions"] > 0
    assert result["pruned_verifications"] > 0
    assert result["pruned_fetch_log"] == 1
    assert result["pruned_robots_cache"] == 1


def test_cli_gc_reports_what_it_pruned(tmp_path):
    from typer.testing import CliRunner

    from anchor.cli import app

    path = tmp_path / "cli.db"
    repository = Repository(path)
    try:
        seed_verify_workflow(repository, versions_per_doc=30)
    finally:
        repository.close()

    result = CliRunner().invoke(app, ["gc", "--db", str(path), "--keep", "5"])

    assert result.exit_code == 0, result.output
    assert "삭제 0개" not in result.output, result.output
    assert "검증 이력" in result.output, result.output


# -- 마이그레이션 이후에도 같은 계약 ------------------------------------------


def test_reclaim_works_on_a_store_that_came_through_migration(tmp_path):
    """v9 DB를 올린 뒤에도 회수가 되고 인용은 복원된다."""
    from tests.unit.test_migration_with_data import build_old_db

    path = tmp_path / "migrated.db"
    build_old_db(path, 9)
    repository = Repository(path)
    try:
        anchor = repository.get_anchor("anc-1")
        assert anchor is not None
        # v1 픽스처의 blob은 압축된 본문이 아니므로(`X'00'`) 바이트로 비교한다 —
        # 복원 가능성의 요구는 "그때의 바이트가 그대로 있는가"다.
        blob = repository._connection.execute(
            "SELECT content_blob FROM versions WHERE id = ?", (anchor.created_version,)
        ).fetchone()[0]
        # v1 픽스처의 검증 이력은 ver-2를 붙잡고 있다.
        repository.collect_garbage(keep=1)
        assert repository.get_version(anchor.created_version) is not None
        assert repository._connection.execute(
            "SELECT content_blob FROM versions WHERE id = ?", (anchor.created_version,)
        ).fetchone()[0] == blob
        connection = repository._connection
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        rows = connection.execute("SELECT COUNT(*) FROM verifications").fetchone()[0]
        assert rows == 1, "관측 로그가 사라졌다"
    finally:
        repository.close()


def test_sqlite_reports_the_new_delete_action(tmp_path):
    """`ON DELETE SET NULL`이 실제 스키마에 있는가 (신규 DB)."""
    path = tmp_path / "fresh.db"
    Repository(path).close()
    connection = sqlite3.connect(path)
    try:
        keys = connection.execute("PRAGMA foreign_key_list(verifications)").fetchall()
    finally:
        connection.close()
    actions = {row[2]: row[6] for row in keys}  # {참조 테이블: on_delete}
    assert actions["versions"] == "SET NULL", keys


# -- D-084 의미론이 **진입점에서** 성립하는가 --------------------------------


def _serve_new_version(repo: Repository, document_id: str, tag: str):
    version = add_version(repo, document_id, tag, day=0, serve=True)
    return version


def test_list_documents_calls_a_reclaimed_check_pending_not_verified(tmp_path):
    """`list_documents(has_pending_verification=True)`가 실제 필터 경로다.

    NULL이 "현재 본문을 검증했다"로 읽히면, 그 필터로 대상을 좁히는 워크플로가
    판정이 뒤집힌 문서를 영영 다시 보지 않는다 (D-084).
    """
    path = tmp_path / "pending.db"
    repository = Repository(path)
    try:
        seeded = seed_verify_workflow(repository, versions_per_doc=20)
        document_id = seeded["documents"][0]
        anchor = seeded["anchors"][document_id]
        repository._connection.execute("DELETE FROM verifications")
        # 마지막 검증이 본 것은 **회수 대상인** 옛 버전이다.
        repository.insert_verification(
            anchor_id=anchor.id, checked_version=seeded["versions"][document_id][1],
            checked_at=ago(days=1), state="INTACT", match_score=1.0, edit_distance=0,
            found_offset=0, found_text=None, elapsed_ms=3,
        )
    finally:
        repository.close()

    with Anchor(db_path=path, config=Config(db_path=path, keep_versions=2)) as anchor_api:
        anchor_api.collect_garbage()
        pending = anchor_api.list_documents(has_pending_verification=True)
        settled = anchor_api.list_documents(has_pending_verification=False)

    assert [d.id for d in pending] == [document_id], pending
    assert settled == []


def test_a_check_of_the_served_version_is_not_turned_pending_by_gc(tmp_path):
    """반대 방향도 사실이어야 한다 — gc가 멀쩡한 검증을 "재검증 필요"로 만들지 않는다.

    서빙 중인 버전은 계약으로 보호되므로 그 버전을 본 검증은 참조를 잃지 않는다.
    모든 문서를 영구히 pending으로 만드는 것도 거짓을 말하는 일이다.
    """
    path = tmp_path / "settled.db"
    repository = Repository(path)
    try:
        seeded = seed_verify_workflow(repository, versions_per_doc=20)
        document_id = seeded["documents"][0]
        anchor = seeded["anchors"][document_id]
        served = repository.current_version(document_id)
        repository._connection.execute("DELETE FROM verifications")
        repository.insert_verification(
            anchor_id=anchor.id, checked_version=served.id, checked_at=ago(days=1),
            state="INTACT", match_score=1.0, edit_distance=0, found_offset=0,
            found_text=None, elapsed_ms=3,
        )
    finally:
        repository.close()

    with Anchor(db_path=path, config=Config(db_path=path, keep_versions=2)) as anchor_api:
        anchor_api.collect_garbage()
        pending = anchor_api.list_documents(has_pending_verification=True)
        settled = anchor_api.list_documents(has_pending_verification=False)

    assert pending == []
    assert [d.id for d in settled] == [document_id]


def test_retention_cutoffs_and_stored_timestamps_share_one_format():
    """프루닝은 **문자열 비교**다 — 형식이 갈리면 조용히 틀린 것을 지운다.

    경계선은 `iso_ago`가 만들고, 비교 대상은 `utcnow_iso`가 쓴 값이다
    (`checked_at`·`requested_at`·`fetched_at` 셋 다). 둘이 같은 고정폭
    형식이어야 사전식 비교가 시간 비교가 된다.
    """
    import re

    from anchor.models import iso_ago, utcnow_iso

    shape = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    now, cutoff = utcnow_iso(), iso_ago(86400)
    assert shape.match(now), now
    assert shape.match(cutoff), cutoff
    assert cutoff < now, (cutoff, now)


def test_pruning_uses_timestamps_written_by_the_production_writers(tmp_path):
    """픽스처가 지어낸 문자열이 아니라 **실제 기록자**가 쓴 값으로 판별한다."""
    from anchor.models import iso_ago, utcnow_iso

    path = tmp_path / "fmt.db"
    repository = Repository(path)
    try:
        document_id = make_document(repository)
        version = add_version(repository, document_id, "v0", day=1, serve=True)
        anchor = repository.insert_anchor(
            document_id=document_id, created_version=version.id,
            exact="v0 판본의 본문이다.", prefix="", suffix="", position_hint=0,
            exact_hash="b3:e", quality="ok", note=None, created_at=utcnow_iso(),
            cited_url="https://example.test/doc-0",
        )
        # 하나는 아주 오래된 관측, 하나는 방금 — 실제 기록자의 형식 그대로.
        repository.insert_verification(
            anchor_id=anchor.id, checked_version=version.id,
            checked_at=iso_ago(400 * 86400), state="INTACT", match_score=1.0,
            edit_distance=0, found_offset=0, found_text=None, elapsed_ms=1,
        )
        repository.insert_verification(
            anchor_id=anchor.id, checked_version=version.id, checked_at=utcnow_iso(),
            state="INTACT", match_score=1.0, edit_distance=0, found_offset=0,
            found_text=None, elapsed_ms=1,
        )
        repository.log_fetch(
            document_id=document_id, requested_at=iso_ago(500 * 86400),
            outcome="cache_hit", http_status=200, bytes_down=0, elapsed_ms=1,
        )
        repository.log_fetch(
            document_id=document_id, requested_at=utcnow_iso(), outcome="cache_hit",
            http_status=200, bytes_down=0, elapsed_ms=1,
        )
        repository.set_robots("https://old.test", "User-agent: *", 200, iso_ago(90000))
        repository.set_robots("https://new.test", "User-agent: *", 200, utcnow_iso())
    finally:
        repository.close()

    with Anchor(db_path=path, config=Config(db_path=path)) as anchor_api:
        result = anchor_api.collect_garbage()

    assert result["pruned_verifications"] == 1
    assert result["pruned_fetch_log"] == 1
    assert result["pruned_robots_cache"] == 1
