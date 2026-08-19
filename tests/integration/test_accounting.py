# SPDX-License-Identifier: Apache-2.0
"""회계의 정합 (SPEC §7.7) — D-130~D-136.

지표가 **실패의 종류**에 좌우되면 그 지표로는 아무것도 판정할 수 없다.
예외로 끝난 호출도, 무산된 폴백이 쓴 바이트도, 되돌림 문서의 실제 본문
크기도 회계에 그대로 남아야 한다.

축: outcome(성공·cache_hit·예외 실패·아카이브 구제) × 실패 종류(robots
explicit/unavailable·타임아웃·리다이렉트 한도·크기 상한·추출 실패) ×
문서 상태(정상·되돌림·아카이브) × 표시 버킷 합산 × 프로세스 수명(WAL).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from anchor.config import Config
from anchor.errors import AnchorError
from anchor.service import Anchor

from .conftest import article_html


def _anchor(tmp_path: Path, **overrides) -> Anchor:
    config = Config(
        db_path=tmp_path / "store.db",
        rate_limit_rps=1000.0,
        retry_backoff_base=0.01,
        **overrides,
    )
    return Anchor(db_path=config.db_path, config=config)


def _log(anchor: Anchor) -> list[dict]:
    rows = anchor._repository._connection.execute(
        "SELECT document_id, outcome, http_status, bytes_down FROM fetch_log ORDER BY id"
    ).fetchall()
    return [dict(row) for row in rows]


# -- D-130: 예외로 끝난 실패도 회계에 남는다 ------------------------------


def test_hit_rate_is_not_inflated_by_exception_failures(tmp_path, fixture_server):
    """cache_hit 5 + 타임아웃 10 (총 16회 호출) → hit_rate는 0.3125다 (D-130).

    실패가 예외로 끝나면 `fetch_log` 행이 아예 없어 분모가 줄고, 같은
    시나리오의 실패를 HTTP 500으로 바꾸면 값이 달라진다 — 지표가 실패의
    종류에 좌우된다.
    """
    base, state = fixture_server
    with _anchor(tmp_path, timeout_seconds=0.05) as anchor:
        anchor.fetch(f"{base}/article")  # created
        for _ in range(5):
            anchor.fetch(f"{base}/article")  # cache_hit

        state.response_delay = 0.4
        for _ in range(10):
            with pytest.raises(AnchorError):
                anchor.fetch(f"{base}/article", max_age=0)

        window = anchor.cache_stats()["last_30d"]

    assert window["requests"] == 16, "예외로 끝난 호출도 요청이다"
    assert window["errors"] == 10
    assert window["hit_rate"] == 0.3125


def _robots_bytes(state) -> int:
    return len(state.robots.encode("utf-8"))


# 실패 픽스처의 축은 두 갈래다.
#   (1) **응답을 끝까지 받은** 실패 — 지연·상태코드·크기 상한·추출 실패.
#   (2) **이미 받은 뒤 끊긴** 실패 — 연결 절단·읽기 타임아웃.
# (2)가 없으면 "받은 바이트는 전부 계상한다"(SPEC §7.7 불변식 2)가 어느
# 계층에서 깨져도 보이지 않는다. 그래서 세 계층(문서 본문·robots.txt·아카이브
# 조회) 전부에 (2)를 연다 (D-212·D-213·D-214).
#
# 각 사례는 **기대 바이트**를 함께 든다. 행 수와 outcome만 재면 회계가
# 0을 적어도 초록이다 — 실제로 그렇게 세 자리가 살아남았다.
_BIG_ARTICLE = article_html(nonce="big", extra_sentence=" 덧붙임 문장." * 4000)
_BIG_ROBOTS = b"# " + b"x" * 60_000 + b"\nUser-agent: *\nAllow: /\n"
_BIG_PAYLOAD = (
    '{"original_uri": "x", "mementos": {}, "pad": "' + "x" * 60_000 + '"}'
)


def _prepare_body_cut(state) -> None:
    state.bodies["/big"] = _BIG_ARTICLE
    state.cut_after["/big"] = 50_000


def _prepare_body_stall(state) -> None:
    state.bodies["/big"] = _BIG_ARTICLE
    state.stall_after["/big"] = 40_000
    state.stall_seconds = 2.0


def _prepare_robots_cut(state) -> None:
    state.robots_body_override = _BIG_ROBOTS
    state.cut_after["/robots.txt"] = 50_000


def _prepare_archive_cut(state) -> None:
    state.status_override = 404  # 원본 소멸 → 아카이브 조회로 넘어간다
    state.archive_payload_override = _BIG_PAYLOAD
    state.cut_after["/api/json/"] = 40_000


FAILURE_CASES = [
    # (이름, 서버 준비, 설정 덮어쓰기(dict 또는 base→dict), 경로, 미리 등록할지,
    #  기대 바이트(state→int))
    (
        "robots_explicit_unregistered",
        lambda s: None,
        {},
        "/private",
        False,
        _robots_bytes,
    ),
    (
        "robots_unavailable_unregistered",
        lambda s: setattr(s, "robots_status", 503),
        {},
        "/article",
        False,
        _robots_bytes,
    ),
    (
        "timeout",
        lambda s: setattr(s, "response_delay", 0.4),
        {"timeout_seconds": 0.05},
        "/article",
        False,
        _robots_bytes,
    ),
    (
        "redirect_limit",
        lambda s: s.redirects.update({"/a": "/b", "/b": "/a"}),
        {"max_redirects": 2},
        "/a",
        False,
        lambda s: _robots_bytes(s) + 3 * len(s.redirect_body),
    ),
    (
        "content_too_large",
        lambda s: None,
        {"max_content_bytes": 200},
        "/article",
        False,
        # 선언된 Content-Length에서 거절하므로 본문은 한 바이트도 오지 않는다.
        _robots_bytes,
    ),
    (
        "extraction_failed",
        lambda s: s.bodies.update({"/empty": "<html><body></body></html>"}),
        {},
        "/empty",
        False,
        lambda s: _robots_bytes(s) + len(s.bodies["/empty"].encode("utf-8")),
    ),
    (
        "timeout_registered",
        lambda s: setattr(s, "response_delay", 0.4),
        {"timeout_seconds": 0.05},
        "/article",
        True,
        # robots는 이미 캐시됐고 본문은 오지 않았다 — 0이 정답이다.
        lambda s: 0,
    ),
    # -- (2) 이미 받은 뒤 끊긴다 -----------------------------------------
    (
        "body_cut_mid_transfer",
        _prepare_body_cut,
        {},
        "/big",
        False,
        lambda s: _robots_bytes(s) + s.sent_bytes["/big"],
    ),
    (
        "body_stall_mid_transfer",
        _prepare_body_stall,
        {"timeout_seconds": 0.5},
        "/big",
        False,
        lambda s: _robots_bytes(s) + s.sent_bytes["/big"],
    ),
    (
        "robots_cut_mid_transfer",
        _prepare_robots_cut,
        {},
        "/article",
        False,
        lambda s: s.sent_bytes["/robots.txt"],
    ),
    (
        "archive_lookup_cut_mid_transfer",
        _prepare_archive_cut,
        lambda base: {"archive_fallback_enabled": True, "archive_aggregator": base},
        "/article",
        False,
        lambda s: _robots_bytes(s) + s.sent_bytes["/api/json/"],
    ),
]


@pytest.mark.parametrize(
    "name,prepare,overrides,path,pre_register,expect",
    FAILURE_CASES,
    ids=[case[0] for case in FAILURE_CASES],
)
def test_every_failure_kind_leaves_one_row_with_the_bytes_it_downloaded(
    tmp_path, fixture_server, name, prepare, overrides, path, pre_register, expect
):
    """실패의 종류마다 정확히 한 행 (D-130), 그 행의 바이트는 실수령량이다.

    미등록 문서도 예외가 아니고, **본문 도중에 끊긴** 실패도 예외가 아니다
    (D-212~D-214, SPEC §7.7 불변식 2).
    """
    base, state = fixture_server
    options = overrides(base) if callable(overrides) else overrides
    with _anchor(tmp_path, **options) as anchor:
        if pre_register:
            anchor.fetch(f"{base}{path}")
        before = len(_log(anchor))
        prepare(state)
        with pytest.raises(AnchorError):
            anchor.fetch(f"{base}{path}", max_age=0)
        rows = _log(anchor)[before:]

    assert len(rows) == 1, f"{name}: 실패 1회 = 회계 1행 (관측 {len(rows)}행)"
    assert rows[0]["outcome"] == "error"
    assert rows[0]["bytes_down"] == expect(state), (
        f"{name}: 실수령 {expect(state)}B 중 {rows[0]['bytes_down']}B만 계상됐다"
    )


# -- D-134: 실패해도 이미 내려받은 바이트는 계상한다 ----------------------


def test_bytes_downloaded_before_a_mid_redirect_refusal_are_kept(tmp_path, fixture_server):
    """리다이렉트 도중 robots가 거부해도 따라온 홉의 바이트는 남는다 (D-134).

    페처의 지역변수(`overhead_bytes`·`attempted_bytes`)에 있던 바이트가
    예외와 함께 사라지면, 실제로 나간 트래픽이 회계에서 통째로 증발한다.
    """
    base, state = fixture_server
    robots_bytes = len(state.robots.encode("utf-8"))
    with _anchor(tmp_path, robots_ttl_seconds=0) as anchor:
        anchor.fetch(f"{base}/start")  # 등록 — D-130과 분리해서 본다
        before = len(_log(anchor))
        state.redirects["/start"] = "/private"  # robots가 막는 목적지
        with pytest.raises(AnchorError):
            anchor.fetch(f"{base}/start", max_age=0)
        rows = _log(anchor)[before:]

    assert len(rows) == 1
    floor = len(state.redirect_body) + robots_bytes * 2
    assert rows[0]["bytes_down"] >= floor, (
        f"실제로 내려받은 최소 {floor}B 중 {rows[0]['bytes_down']}B만 계상됐다"
    )


# -- D-135: 무산된 아카이브 조회의 바이트 ---------------------------------


def test_futile_archive_lookup_bytes_are_accounted(tmp_path, fixture_server):
    """'memento 없음'으로 끝난 조회의 바이트도 회계에 남는다 (D-135)."""
    base, state = fixture_server
    state.status_override = 404
    state.archive_html = article_html(nonce="arch")
    # 애그리게이터가 200으로 답하되 memento는 없다 — 바이트는 이미 나갔다.
    state.archive_payload_override = '{"original_uri": "x", "mementos": {}}'
    payload_bytes = len(state.archive_payload_override.encode("utf-8"))
    with _anchor(
        tmp_path, archive_fallback_enabled=True, archive_aggregator=base
    ) as anchor:
        with pytest.raises(AnchorError):
            anchor.fetch(f"{base}/article")
        rows = _log(anchor)

    assert len(rows) == 1
    assert rows[0]["bytes_down"] >= payload_bytes, (
        f"애그리게이터 응답 {payload_bytes}B가 어디에도 없다 "
        f"(계상 {rows[0]['bytes_down']}B)"
    )


# -- D-133: 사용자 호출 1회 = 회계 1행 ------------------------------------


def test_archive_rescue_after_unavailable_robots_logs_one_row(
    tmp_path, fixture_server_factory
):
    """robots 판정 불능 → 아카이브 구제는 **한 행**이고 outcome은 최종 결과다.

    원본 호스트와 아카이브 호스트가 달라야 성립하는 경로다 — 같은 서버면
    아카이브의 robots도 5xx라 구제 자체가 일어나지 않는다 (D-133).
    """
    origin, origin_state = fixture_server_factory()
    archive, archive_state = fixture_server_factory()
    archive_state.archive_html = article_html(nonce="arch")

    with _anchor(
        tmp_path,
        robots_ttl_seconds=0,
        archive_fallback_enabled=True,
        archive_aggregator=archive,
    ) as anchor:
        anchor.fetch(f"{origin}/article")  # created
        before = len(_log(anchor))
        origin_state.robots_status = 503  # 규칙을 물어보지 못한다
        result = anchor.fetch(f"{origin}/article", max_age=0)
        rows = _log(anchor)[before:]

    assert result.outcome == "archive"
    assert len(rows) == 1, f"호출 1회가 {len(rows)}행으로 계상됐다"
    assert rows[0]["outcome"] == "archive", "최종 성공한 호출이 errors에도 잡힌다"


# -- D-131: 절감 추정은 지금 서빙되는 본문 기준 ---------------------------


def test_bytes_saved_uses_the_body_currently_served(tmp_path, fixture_server):
    """되돌림 문서의 절감 추정은 `documents.current_version` 기준이다 (D-131).

    captured_at 최대 버전으로 근사하면, 되돌림 뒤 실제로는 작은 본문을
    서빙하는 문서의 모든 cache_hit이 거대한 옛 버전 크기로 평가된다.
    """
    base, state = fixture_server
    small = article_html(nonce="small")
    large = article_html(nonce="large", extra_sentence=" 덧붙임 문장." * 4000)
    state.etag = None  # 조건부 GET을 끄고 본문 교체를 그대로 관측한다
    state.bodies["/doc"] = small

    with _anchor(tmp_path) as anchor:
        anchor.fetch(f"{base}/doc")                      # created  → small
        state.bodies["/doc"] = large
        anchor.fetch(f"{base}/doc", max_age=0)           # changed  → large
        state.bodies["/doc"] = small
        reverted = anchor.fetch(f"{base}/doc", max_age=0)  # changed → small (되돌림)
        anchor.fetch(f"{base}/doc")                      # cache_hit
        window = anchor.cache_stats()["last_30d"]
        version = anchor._repository.current_version(reverted.document_id)

    assert version is not None and version.byte_size == len(small.encode("utf-8"))
    assert window["cache_hits"] == 1
    assert window["bytes_saved_estimate"] == len(small.encode("utf-8")), (
        "되돌림 문서의 절감 추정이 지금 서빙되지 않는 옛 판본 크기로 부풀었다"
    )


# -- D-136: 내역의 합 = 총 요청 수 ----------------------------------------


# created·renormalized는 changed와 다른 사건이므로 각자의 버킷을 진다
# (D-227) — 합이 requests와 같아야 한다는 불변식(D-136)은 그대로다.
_BUCKETS = (
    "cache_hits", "not_modified", "unchanged", "created", "changed",
    "renormalized", "archive", "errors",
)


def test_stat_buckets_sum_to_the_reported_request_count(tmp_path, fixture_server):
    """SPEC §7.7의 내역은 총계를 나눈 것이다 — archive가 빠지면 합이 안 맞는다."""
    base, state = fixture_server
    state.status_override = 404
    state.archive_html = article_html(nonce="arch")

    with _anchor(
        tmp_path, archive_fallback_enabled=True, archive_aggregator=base
    ) as anchor:
        assert anchor.fetch(f"{base}/article").outcome == "archive"
        anchor.fetch(f"{base}/article")  # cache_hit
        with pytest.raises(AnchorError):
            anchor.fetch(f"{base}/private")  # error
        window = anchor.cache_stats()["last_30d"]

    detail = sum(window[name] for name in _BUCKETS)
    assert window["requests"] == 3
    assert detail == window["requests"], f"내역 {detail} ≠ 요청 {window['requests']}"


def test_every_outcome_the_pipeline_writes_lands_in_a_bucket(tmp_path, fixture_server):
    """파이프라인이 쓰는 outcome을 한 번씩 다 내고 합을 맞춘다 (D-136).

    한 종류만 내는 워크로드로는 "표시되지 않는 버킷"이 보이지 않는다.
    """
    base, state = fixture_server
    with _anchor(
        tmp_path, archive_fallback_enabled=True, archive_aggregator=base
    ) as anchor:
        assert anchor.fetch(f"{base}/article").outcome == "created"
        assert anchor.fetch(f"{base}/article").outcome == "cache_hit"
        assert anchor.fetch(f"{base}/article", max_age=0).outcome == "not_modified"
        state.etags["/article"] = '"v2"'  # 검증자만 바뀌고 본문은 그대로
        assert anchor.fetch(f"{base}/article", max_age=0).outcome == "unchanged"
        state.bodies["/article"] = article_html(nonce="n1", extra_sentence=" 새 문장이 붙었다.")
        state.etags["/article"] = '"v3"'
        assert anchor.fetch(f"{base}/article", max_age=0).outcome == "changed"
        with pytest.raises(AnchorError):
            anchor.fetch(f"{base}/private")
        state.status_override = 404  # 원본 소멸 → 아카이브 구제
        state.archive_html = article_html(nonce="arch")
        assert anchor.fetch(f"{base}/gone").outcome == "archive"

        window = anchor.cache_stats()["last_30d"]
        observed = {
            row["outcome"] for row in _log(anchor)
        }

    assert observed == {
        "created", "cache_hit", "not_modified", "unchanged", "changed", "error", "archive"
    }
    detail = sum(window[name] for name in _BUCKETS)
    assert window["requests"] == 7
    assert detail == window["requests"], f"내역 {detail} ≠ 요청 {window['requests']}"


def test_verify_refetch_failures_are_accounted_too(tmp_path, fixture_server):
    """같은 결함은 다른 진입점에도 있다 — verify의 재페치 실패도 회계에 남는다."""
    base, state = fixture_server
    quote = "학술 문헌이 참조한 웹 콘텐츠의 약 75%가 3년 안에 어느 정도 변경된 것으로"
    with _anchor(tmp_path, timeout_seconds=0.05) as anchor:
        anchor.fetch(f"{base}/article")
        anchor.cite(f"{base}/article", quote)
        before = len(_log(anchor))
        state.response_delay = 0.4
        report = anchor.verify()
        rows = _log(anchor)[before:]

    assert report.summary["UNREACHABLE"] == 1
    assert [row["outcome"] for row in rows] == ["error"]


def test_cli_stats_shows_every_bucket(tmp_path, fixture_server):
    """CLI의 내역도 같은 정합이어야 한다 — archive가 보이지 않으면 합이 안 맞는다."""
    from typer.testing import CliRunner

    from anchor.cli import app

    base, state = fixture_server
    state.status_override = 404
    state.archive_html = article_html(nonce="arch")
    db = tmp_path / "store.db"
    config = Config(
        db_path=db,
        rate_limit_rps=1000.0,
        archive_fallback_enabled=True,
        archive_aggregator=base,
    )
    with Anchor(db_path=db, config=config) as anchor:
        anchor.fetch(f"{base}/article")

    result = CliRunner().invoke(app, ["stats", "--db", str(db)])
    assert result.exit_code == 0, result.output
    assert "archive 1" in result.output, result.output


# -- D-132: disk_bytes는 저장소의 크기다 ----------------------------------


def _grow(anchor: Anchor, versions: int) -> str:
    document = anchor._repository.create_document(
        url="https://example.com/big",
        original_url="https://example.com/big",
        title=None,
        now="2026-08-01T00:00:00Z",
    )
    for index in range(versions):
        # 압축되지 않는 본문이어야 WAL이 실제로 커진다.
        body = "".join(f"{index}-{n}-{n * index % 977} 문단 " for n in range(2000))
        anchor._repository.insert_version(
            document_id=document.id,
            text_hash=f"b3:text-{index:04d}",
            raw_hash=f"b3:raw-{index:04d}",
            pipeline_version="trafilatura/2.2.0+norm/3",
            captured_at=f"2026-08-01T{index // 60:02d}:{index % 60:02d}:00Z",
            byte_size=len(body),
            normalized_text=body,
            http_status=200,
        )
    return document.id


def test_disk_bytes_reports_the_store_not_the_write_ahead_log(tmp_path):
    """장기 실행 프로세스에서도 disk_bytes는 실적재량이어야 한다 (D-132).

    커넥션을 계속 여는 `anchor serve`에서 -wal은 체크포인트 뒤에도 줄지
    않아, 같은 저장소가 새 프로세스에서와 몇 배 다르게 보고된다.
    """
    with _anchor(tmp_path) as anchor:
        _grow(anchor, 120)
        reported = anchor._repository.disk_bytes()
        main = (tmp_path / "store.db").stat().st_size

    assert reported <= main + 64 * 1024, (
        f"disk_bytes {reported:,}B가 본체 {main:,}B의 "
        f"{reported / max(main, 1):.1f}배로 보고됐다"
    )


def test_gc_does_not_inflate_disk_bytes(tmp_path):
    """gc 직후의 실적재량이 늘어나면 안 된다 — VACUUM이 DB를 WAL로 다시 쓴다.

    보고되는 숫자만이 아니라 **파일 자체**를 본다. 측정 시점의 회수에만
    기대면, 회계를 조회하지 않는 사용자에게는 gc가 파일을 두 배로 남긴다.
    """
    with _anchor(tmp_path) as anchor:
        _grow(anchor, 120)
        before = anchor._repository.disk_bytes()
        anchor.collect_garbage(keep=1)
        wal = Path(str(tmp_path / "store.db") + "-wal")
        wal_after_gc = wal.stat().st_size if wal.exists() else 0
        after = anchor._repository.disk_bytes()

    assert after <= before, f"gc 뒤 disk_bytes가 {before:,} → {after:,}로 늘었다"
    assert wal_after_gc <= 64 * 1024, (
        f"gc가 DB를 통째로 WAL({wal_after_gc:,}B)에 다시 쓰고 회수하지 않았다"
    )


def test_a_corrupt_write_ahead_log_does_not_leak_a_raw_sqlite_error(tmp_path):
    """WAL이 깨져도 공개 API는 `AnchorError` 하나로만 말한다 (D-215, SPEC §8).

    `PRAGMA wal_checkpoint(TRUNCATE)`가 던지는 것은 `OperationalError`만이
    아니다 — 복구가 필요한 크기의 WAL에서 헤더가 깨져 있으면 상위 클래스
    `DatabaseError`("file is not a database")가 나온다. 좁게 잡으면
    `cache_stats()`가 생 sqlite3 예외로 죽어 CLI 오류 표면 계약이 깨진다.
    커넥션을 계속 여는 `anchor serve`가 정확히 이 배치다. 걷어내지 못하면
    걷어내지 못한 대로, 그 순간 파일 크기를 사실대로 보고한다.
    """
    import sqlite3

    db = tmp_path / "store.db"
    with _anchor(tmp_path) as anchor:
        # 자동 체크포인트 문턱(기본 1000쪽 ≈ 4MB)을 넘겨야 새 커넥션이
        # WAL 복구를 시도하고, 그때 비로소 헤더를 읽는다. 작은 WAL로는
        # 손상이 조용히 무시돼 이 시험이 헛돈다.
        _grow(anchor, 120)
        wal = Path(str(db) + "-wal")
        (page_size,) = anchor._repository._connection.execute(
            "PRAGMA page_size"
        ).fetchone()
        assert wal.stat().st_size > 1000 * page_size, (
            f"픽스처가 복구를 요구할 만큼 WAL을 키우지 못했다 ({wal.stat().st_size:,}B)"
        )
        with open(wal, "r+b") as handle:
            handle.write(b"\x00" * 64)  # 헤더 파괴 — 잘라내기와는 다른 손상이다

        # 픽스처가 실제로 결함 조건을 만들었는지 먼저 확인한다. 여기서
        # `OperationalError`가 나오면 이 시험은 판별력이 없다.
        probe = sqlite3.connect(db, timeout=1.0, isolation_level=None)
        try:
            with pytest.raises(sqlite3.DatabaseError) as caught:
                probe.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            probe.close()
        assert not isinstance(caught.value, sqlite3.OperationalError), (
            f"픽스처가 만든 것은 좁은 예외였다: {caught.value!r}"
        )

        stats = anchor.cache_stats()

    assert stats["disk_bytes"] > 0


def test_a_failure_after_the_body_lookup_does_not_add_a_second_row(
    tmp_path, fixture_server, monkeypatch
):
    """조치 코드도 새 결함원이다 (조치 절차 3).

    성공을 기록한 **뒤에** 터지는 예외가 있으면 한 호출이 다시 두 행이
    된다 — gc가 방금 그 버전을 지우는 창이 그 자리다.
    """
    from anchor.errors import DocumentNotFound

    base, _state = fixture_server
    with _anchor(tmp_path) as anchor:
        monkeypatch.setattr(
            anchor._repository,
            "get_version_text",
            lambda version_id: (_ for _ in ()).throw(DocumentNotFound("gc가 지웠다")),
        )
        with pytest.raises(DocumentNotFound):
            anchor.fetch(f"{base}/article")
        rows = _log(anchor)

    assert len(rows) == 1, f"호출 1회가 {len(rows)}행으로 계상됐다"
    assert rows[0]["outcome"] == "error"
