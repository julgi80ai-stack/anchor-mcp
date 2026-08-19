# SPDX-License-Identifier: Apache-2.0
"""도구가 사실과 다른 문장을 말하지 않는다 (8단계-가, D-227~D-235).

판정(INTACT/ALTERED/MISSING)은 여기서 하나도 바뀌지 않는다. 바뀌는 것은
**도구가 그 판정에 대해 하는 말**이다 — 첫 페치를 "바뀌었다"라고, 죽은
호스트를 "사이트 주인이 막았다"라고, 아무것도 검증하지 못한 배치를
"이상 없음"이라고 말하던 자리들이다.

축을 먼저 연다: outcome 종류(created·changed·renormalized) × robots 사유
(explicit·unavailable) × 검증 실패 상태(GONE·UNREACHABLE) × 판본 나이
(신선·오래됨) × 출현 횟수(1회·여러 번) × 원본 바이트(같음·다름) × 대조
출처(live·archive) × 추출 파이프라인(같음·다름).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from anchor.cli import app
from anchor.config import Config
from anchor.errors import AnchorError, RobotsDisallowed
from anchor.normalize import extract
from anchor.normalize.extract import NormalizedDoc
from anchor.service import Anchor

from .conftest import article_html

QUOTE = "학술 문헌이 참조한 웹 콘텐츠의 약 75%가 3년 안에 어느 정도 변경된 것으로"


def _config(tmp_path: Path, **overrides) -> Config:
    return Config(
        db_path=tmp_path / "store.db",
        rate_limit_rps=1000.0,
        retry_backoff_base=0.01,
        **overrides,
    )


def _anchor(tmp_path: Path, **overrides) -> Anchor:
    config = _config(tmp_path, **overrides)
    return Anchor(db_path=config.db_path, config=config)


def _repoint_pipeline(monkeypatch, label: str) -> None:
    """추출 파이프라인이 바뀐 척한다 — 본문 꼬리와 pipeline_version 둘 다."""
    original = extract.to_normalized

    def altered(raw: bytes, content_type: str) -> NormalizedDoc:
        doc = original(raw, content_type)
        return NormalizedDoc(
            text=doc.text + f"\n\n[{label}가 붙인 꼬리]",
            title=doc.title,
            pipeline_version=f"{label}/999.0+norm/2",
        )

    monkeypatch.setattr(extract, "to_normalized", altered)


# -- D-227: created를 changed라 부르지 않는다 --------------------------------


def test_first_fetch_is_not_counted_as_changed(tmp_path, fixture_server):
    """첫 페치는 created다. 사용자 실 저장소의 "changed 60"이 여기서 났다."""
    base, _ = fixture_server
    with _anchor(tmp_path) as anchor:
        assert anchor.fetch(f"{base}/article").outcome == "created"
        window = anchor.cache_stats()["last_30d"]

    assert window["created"] == 1
    assert window["changed"] == 0, "첫 페치가 '바뀌었다'로 계상됐다"


def test_stats_keeps_created_changed_and_renormalized_apart(
    tmp_path, fixture_server, monkeypatch
):
    """세 outcome은 서로 다른 사건이다 — 한 버킷에 묶으면 어느 것도 못 읽는다."""
    base, state = fixture_server
    with _anchor(tmp_path) as anchor:
        anchor.fetch(f"{base}/article")                       # created
        state.html = article_html(extra_sentence=" 새 문장이 붙었다.")
        state.etag = '"v2"'
        assert anchor.fetch(f"{base}/article", max_age=0).outcome == "changed"
        state.etag = None
        _repoint_pipeline(monkeypatch, "trafilatura")
        assert anchor.fetch(f"{base}/article", max_age=0).outcome == "renormalized"
        window = anchor.cache_stats()["last_30d"]

    assert (window["created"], window["changed"], window["renormalized"]) == (1, 1, 1)
    detail = sum(
        window[name]
        for name in (
            "cache_hits", "not_modified", "unchanged", "changed",
            "created", "renormalized", "archive", "errors",
        )
    )
    assert detail == window["requests"], f"내역 {detail} ≠ 요청 {window['requests']}"


def test_cli_stats_does_not_call_a_first_fetch_a_change(tmp_path, fixture_server):
    base, _ = fixture_server
    db = tmp_path / "store.db"
    with Anchor(db_path=db, config=_config(tmp_path)) as anchor:
        anchor.fetch(f"{base}/article")

    result = CliRunner().invoke(app, ["stats", "--db", str(db)])
    assert result.exit_code == 0, result.output
    assert "created 1" in result.output, result.output
    assert "changed 1" not in result.output, result.output


# -- D-228: 죽은 호스트는 robots 거부가 아니다 -------------------------------


def test_explicit_robots_refusal_says_the_owner_refused(tmp_path, fixture_server):
    base, _ = fixture_server
    with _anchor(tmp_path) as anchor:
        with pytest.raises(RobotsDisallowed) as caught:
            anchor.fetch(f"{base}/private")
    message = str(caught.value)
    assert caught.value.reason == "explicit"
    assert "robots.txt" in message
    assert "물어보지" not in message, message


def test_unavailable_robots_does_not_claim_the_owner_refused(tmp_path, fixture_server):
    """5xx robots는 "막혔다"가 아니라 "물어보지 못했다"이다 (SPEC §5.2 2단계)."""
    base, state = fixture_server
    state.robots_status = 503
    with _anchor(tmp_path) as anchor:
        with pytest.raises(RobotsDisallowed) as caught:
            anchor.fetch(f"{base}/article")
    message = str(caught.value)
    assert caught.value.reason == "unavailable"
    assert "물어보지" in message, message
    assert "거부" not in message, f"판정 불능이 소유자의 거부로 보고됐다: {message}"


def test_failed_archive_rescue_is_told_not_swallowed(tmp_path, fixture_server):
    """구제를 시도했다 실패한 것과 시도조차 안 한 것은 다른 사실이다."""
    base, state = fixture_server
    state.robots_status = 503
    state.archive_html = None  # 아카이브에도 없다
    with _anchor(
        tmp_path, archive_fallback_enabled=True, archive_aggregator=base
    ) as anchor:
        with pytest.raises(RobotsDisallowed) as attempted:
            anchor.fetch(f"{base}/article")
    assert "아카이브" in str(attempted.value), str(attempted.value)

    with _anchor(tmp_path / "off") as anchor:
        with pytest.raises(RobotsDisallowed) as skipped:
            anchor.fetch(f"{base}/article")
    assert "아카이브" not in str(skipped.value), str(skipped.value)


def test_cli_reports_the_reason_it_could_not_ask(tmp_path, fixture_server):
    base, state = fixture_server
    state.robots_status = 503
    db = tmp_path / "store.db"
    result = CliRunner().invoke(app, ["fetch", f"{base}/article", "--db", str(db)])
    assert result.exit_code == 1
    assert "물어보지" in result.output, result.output


# -- D-229: 아무것도 검증하지 못한 배치는 "이상 없음"이 아니다 ---------------


def test_unreachable_lands_in_attention(tmp_path, fixture_server):
    base, state = fixture_server
    with _anchor(tmp_path, timeout_seconds=0.05) as anchor:
        anchor.fetch(f"{base}/article")
        anchor.cite(f"{base}/article", QUOTE)
        state.response_delay = 0.4
        report = anchor.verify()

    assert report.summary["UNREACHABLE"] == 1
    assert [item.state for item in report.attention] == ["UNREACHABLE"]


def test_every_failure_state_needing_action_is_in_attention(tmp_path, fixture_server_factory):
    """GONE·UNREACHABLE 둘 다 — 한쪽만 넣으면 다른 쪽이 조용히 사라진다."""
    gone_base, gone_state = fixture_server_factory()
    slow_base, slow_state = fixture_server_factory()
    with _anchor(tmp_path, timeout_seconds=0.05) as anchor:
        anchor.fetch(f"{gone_base}/article")
        anchor.cite(f"{gone_base}/article", QUOTE)
        anchor.fetch(f"{slow_base}/article")
        anchor.cite(f"{slow_base}/article", QUOTE)
        gone_state.status_override = 404
        slow_state.response_delay = 0.4
        report = anchor.verify()

    assert {item.state for item in report.attention} == {"GONE", "UNREACHABLE"}
    assert len(report.attention) == 2


# -- D-230: cite는 어느 시점 판본에 닻을 내렸는지 말한다 ---------------------


def test_cite_reports_the_age_of_the_body_it_anchored(tmp_path, fixture_server):
    base, _ = fixture_server
    with _anchor(tmp_path) as anchor:
        fetched = anchor.fetch(f"{base}/article")
        result = anchor.cite(f"{base}/article", QUOTE)

    assert result.captured_at == fetched.captured_at
    assert result.last_checked_at is not None
    assert not any("오래" in warning for warning in result.warnings)


def test_cite_warns_when_the_anchored_body_is_stale(tmp_path, fixture_server):
    """cite는 어떤 경우에도 네트워크에 나가지 않는다 — 나이를 말해 주지
    않으면 사용자는 며칠 전 스냅샷에 인용을 걸면서 그 사실을 모른다."""
    base, _ = fixture_server
    with _anchor(tmp_path) as anchor:
        anchor.fetch(f"{base}/article")
        anchor._repository._connection.execute(
            "UPDATE documents SET last_checked_at = '2026-01-01T00:00:00Z'"
        )
        result = anchor.cite(f"{base}/article", QUOTE)

    assert result.last_checked_at == "2026-01-01T00:00:00Z"
    assert any("오래" in warning for warning in result.warnings), result.warnings


def test_cli_cite_shows_when_the_body_was_captured(tmp_path, fixture_server):
    base, _ = fixture_server
    db = tmp_path / "store.db"
    with Anchor(db_path=db, config=_config(tmp_path)) as anchor:
        anchor.fetch(f"{base}/article")

    result = CliRunner().invoke(app, ["cite", f"{base}/article", QUOTE, "--db", str(db)])
    assert result.exit_code == 0, result.output
    assert "캡처" in result.output, result.output


# -- D-231: 중복 출현은 저장돼야 다음 세션의 verify가 안다 -------------------


def test_duplicate_occurrence_survives_the_session(tmp_path, fixture_server):
    """경고가 cite 응답 문자열로만 있으면, 다른 세션의 verify는 모호성을 모른다."""
    base, state = fixture_server
    repeated = "이 문장은 문서 안에서 두 번 되풀이된다는 사실이 중요하다."
    state.html = article_html(extra_sentence=f" {repeated}").replace(
        "</article>", f"<p>{repeated}</p></article>"
    )
    db = tmp_path / "store.db"
    with Anchor(db_path=db, config=_config(tmp_path)) as anchor:
        anchor.fetch(f"{base}/article")
        cited = anchor.cite(f"{base}/article", repeated)
    assert any("회 이상" in warning for warning in cited.warnings), cited.warnings

    with Anchor(db_path=db, config=_config(tmp_path)) as anchor:
        record = anchor._repository.get_anchor(cited.anchor_id)
        assert record is not None
        assert record.occurrences is not None and record.occurrences > 1
        report = anchor.verify(anchor_ids=[cited.anchor_id])

    assert report.ambiguous == 1, "모호한 앵커가 보고서 어디에도 없다"


def test_unambiguous_anchor_is_not_flagged(tmp_path, fixture_server):
    base, _ = fixture_server
    with _anchor(tmp_path) as anchor:
        anchor.fetch(f"{base}/article")
        cited = anchor.cite(f"{base}/article", QUOTE)
        assert anchor._repository.get_anchor(cited.anchor_id).occurrences == 1
        report = anchor.verify()
    assert report.ambiguous == 0


# -- D-232: 바이트는 달라졌고 추출 본문만 같다 -------------------------------


def test_unchanged_still_reports_that_the_raw_bytes_moved(tmp_path, fixture_server):
    """추출 사각지대의 유일한 저비용 탐지 수단이다. 판정은 그대로 unchanged."""
    base, state = fixture_server
    with _anchor(tmp_path) as anchor:
        first = anchor.fetch(f"{base}/article")
        assert first.raw_changed is False
        state.html = article_html(nonce="n1")  # <script> 안만 다르다
        state.etag = '"v2"'
        again = anchor.fetch(f"{base}/article", max_age=0)

    assert again.outcome == "unchanged", "판정을 바꾸면 안 된다"
    assert again.raw_changed is True, "원본 바이트가 달라진 사실이 버려졌다"


def test_identical_bytes_do_not_claim_a_raw_change(tmp_path, fixture_server):
    base, state = fixture_server
    with _anchor(tmp_path) as anchor:
        anchor.fetch(f"{base}/article")
        state.etag = '"v2"'  # 검증자만 바꿔 200 재수신을 강제한다
        again = anchor.fetch(f"{base}/article", max_age=0)

    assert again.outcome == "unchanged"
    assert again.raw_changed is False


# -- D-234: 개별 항목의 출처 ------------------------------------------------


def test_cli_marks_which_attention_line_was_compared_to_an_archive(
    tmp_path, fixture_server_factory, monkeypatch
):
    live_base, live_state = fixture_server_factory()
    dead_base, dead_state = fixture_server_factory()
    db = tmp_path / "store.db"
    # CLI는 자기 설정을 스스로 읽는다 — 환경변수로 폴백을 켜야 그 경로가 산다.
    monkeypatch.setenv("ANCHOR_ARCHIVE_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("ANCHOR_ARCHIVE_AGGREGATOR", dead_base)
    monkeypatch.setenv("ANCHOR_RATE_LIMIT_RPS", "1000")
    monkeypatch.setenv("ANCHOR_RETRY_BACKOFF_BASE", "0.01")
    config = _config(
        tmp_path, archive_fallback_enabled=True, archive_aggregator=dead_base
    )
    with Anchor(db_path=db, config=config) as anchor:
        anchor.fetch(f"{live_base}/article")
        anchor.cite(f"{live_base}/article", QUOTE)
        anchor.fetch(f"{dead_base}/article")
        anchor.cite(f"{dead_base}/article", QUOTE)
        # 원본은 죽고 아카이브에는 인용문이 사라진 본문이 있다.
        dead_state.status_override = 404
        dead_state.archive_html = article_html().replace(
            "학술 문헌이 참조한 웹 콘텐츠의 약 75%가 3년 안에 어느 정도 변경된 것으로\n조사되었다.",
            "그 문장은 삭제되었다.",
        )
        # 살아 있는 쪽에서도 인용문을 지운다 — 두 줄이 함께 나와야 축이 열린다.
        live_state.html = article_html().replace(
            "학술 문헌이 참조한 웹 콘텐츠의 약 75%가 3년 안에 어느 정도 변경된 것으로\n조사되었다.",
            "그 문장은 삭제되었다.",
        )
        live_state.etag = '"v2"'

    result = CliRunner().invoke(app, ["verify", "--db", str(db)])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    marked = [line for line in lines if "출처: archive" in line]
    assert len(marked) == 2, result.output  # 집계 1줄(D-218) + 개별 1줄


# -- D-235: 파이프라인이 바뀌면 앵커가 전멸한다 ------------------------------


def test_verify_says_when_the_extraction_pipeline_changed(
    tmp_path, fixture_server, monkeypatch
):
    """원문은 한 글자도 안 바뀌었는데 경보가 쏟아지는 유일한 이유다.
    판정은 바꾸지 않는다 — 사실만 표시한다."""
    base, state = fixture_server
    with _anchor(tmp_path) as anchor:
        anchor.fetch(f"{base}/article")
        anchor.cite(f"{base}/article", QUOTE)
        state.etag = None
        _repoint_pipeline(monkeypatch, "readability-lxml")
        report = anchor.verify()

    assert report.pipeline_changed == 1, "파이프라인이 달라진 사실이 어디에도 없다"


def test_same_pipeline_is_not_flagged(tmp_path, fixture_server):
    base, state = fixture_server
    with _anchor(tmp_path) as anchor:
        anchor.fetch(f"{base}/article")
        anchor.cite(f"{base}/article", QUOTE)
        state.etag = None
        report = anchor.verify()

    assert report.pipeline_changed == 0
    assert report.summary["INTACT"] == 1


# -- D-233: 도메인 밖 예외가 CLI 트레이스백을 만들지 않는다 ------------------


def test_unrequested_304_is_a_domain_error_not_an_assertion(tmp_path, fixture_server):
    """검증자를 보낸 적이 없는데 304가 왔다 — 서버가 규약을 어긴 것이다."""
    from anchor.errors import FetchFailed

    base, state = fixture_server
    state.status_override = 304
    with _anchor(tmp_path) as anchor:
        with pytest.raises(FetchFailed) as caught:
            anchor.fetch(f"{base}/article")
    assert caught.value.http_status == 304


def test_cli_does_not_traceback_on_an_unrequested_304(tmp_path, fixture_server):
    base, state = fixture_server
    state.status_override = 304
    result = CliRunner().invoke(
        app, ["fetch", f"{base}/article", "--db", str(tmp_path / "store.db")]
    )
    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.output, result.output


# -- D-237: 403에 7초를 쓰지 않는다 (진입점 전체) ---------------------------


def _document_requests(state) -> int:
    return sum(1 for path in state.requests if path == "/article")


def test_a_plain_403_is_reported_once_not_four_times(tmp_path, fixture_server):
    base, state = fixture_server
    state.status_override = 403
    with _anchor(tmp_path) as anchor:
        with pytest.raises(AnchorError):
            anchor.fetch(f"{base}/article")
    assert _document_requests(state) == 1, state.requests


def test_a_403_that_names_a_wait_is_still_retried(tmp_path, fixture_server):
    base, state = fixture_server
    state.status_override = 403
    state.status_override_headers = {"Retry-After": "0"}
    with _anchor(tmp_path) as anchor:
        with pytest.raises(AnchorError):
            anchor.fetch(f"{base}/article")
    assert _document_requests(state) == 4, state.requests


def test_429_keeps_its_retry_contract(tmp_path, fixture_server):
    base, state = fixture_server
    state.status_override = 429
    with _anchor(tmp_path) as anchor:
        with pytest.raises(AnchorError):
            anchor.fetch(f"{base}/article")
    assert _document_requests(state) == 4, state.requests


# -- D-236: 되돌림 뒤 TimeMap의 last (서비스 경로) ---------------------------


def test_timemap_last_memento_survives_a_rollback(tmp_path, fixture_server):
    base, state = fixture_server
    body_a = article_html()
    body_b = article_html(extra_sentence=" 잠깐 붙었다 사라진 문장이다.")
    with _anchor(tmp_path) as anchor:
        first = anchor.fetch(f"{base}/article")
        state.html, state.etag = body_b, '"v2"'
        anchor.fetch(f"{base}/article", max_age=0)
        state.html, state.etag = body_a, '"v3"'
        rolled_back = anchor.fetch(f"{base}/article", max_age=0)
        assert rolled_back.version_id == first.version_id  # 옛 행 재사용
        link = anchor.get_timemap(f"{base}/article")["body"]

    last_line = [line for line in link.splitlines() if 'rel="' in line and "last" in line]
    assert len(last_line) == 1, link
    assert first.version_id in last_line[0], link


def test_stale_warning_does_not_degenerate_when_max_age_is_zero(tmp_path, fixture_server):
    """`default_max_age = 0`은 "매번 재확인"이지 "모든 인용이 오래됐다"가 아니다.

    문턱을 그대로 쓰면 방금 페치한 판본까지 경고를 받는다 — 항상 울리는
    경고에는 정보가 없다 (D-230 자가 감사).
    """
    base, _ = fixture_server
    with _anchor(tmp_path, default_max_age=0) as anchor:
        anchor.fetch(f"{base}/article")
        fresh = anchor.cite(f"{base}/article", QUOTE)
        assert not any("오래" in warning for warning in fresh.warnings), fresh.warnings
        anchor._repository._connection.execute(
            "UPDATE documents SET last_checked_at = '2026-01-01T00:00:00Z'"
        )
        stale = anchor.cite(f"{base}/article", QUOTE + " 조사되었다.")
    assert any("오래" in warning for warning in stale.warnings), stale.warnings
