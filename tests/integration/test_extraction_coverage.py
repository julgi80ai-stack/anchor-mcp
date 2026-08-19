# SPDX-License-Identifier: Apache-2.0
"""추출 사각지대를 도구가 스스로 말한다 (8단계-나, D-239~D-243).

**판정은 하나도 바뀌지 않는다.** 본문으로 뽑히지 않는 영역이 통째로 개정돼도
`text_hash`는 같고 판정은 `unchanged`이며 앵커는 `INTACT`다 — 그것이 이
파이프라인의 사실이다. 여기서 고치는 것은 그 사실을 **말하지 않던 침묵**이다:
도구가 문서의 얼마를 보고 그 말을 하는지 함께 밝히게 한다.

축(구조 다양성)은 `tests/fixtures/structure/`가 연다. 이 파일은 그 축 위에서
결함이 사는 조건 — 개정이 추출 밖에서 일어나는 조건 — 을 재현한다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from anchor.cli import app
from anchor.config import Config
from anchor.normalize.coverage import COVERAGE_WARN_RATIO
from anchor.service import Anchor

STRUCTURE = Path(__file__).parent.parent / "fixtures" / "structure"
SPEC_HTML = (STRUCTURE / "sidebar-spec.html").read_text("utf-8")
NEWS_HTML = (STRUCTURE / "news-boilerplate.html").read_text("utf-8")

# 저장 본문 안에 실제로 있는 문장 (추출된 영역).
SPEC_QUOTE = "This section introduces message framing and the vocabulary used throughout."
NEWS_QUOTE = (
    "The city council voted on Tuesday evening to approve the harbour rebuild"
)


def _anchor(tmp_path: Path) -> Anchor:
    config = Config(
        db_path=tmp_path / "store.db",
        rate_limit_rps=1000.0,
        retry_backoff_base=0.01,
    )
    return Anchor(db_path=config.db_path, config=config)


def _revise_the_invisible_region(html: str) -> tuple[str, int]:
    """추출 밖 영역(`<aside>`)의 규범 요구를 뒤집는다: MUST NOT → MAY."""
    pieces = html.split("<aside")
    flipped = 0
    for index in range(1, len(pieces)):
        count = pieces[index].count("MUST NOT")
        flipped += count
        pieces[index] = pieces[index].replace("MUST NOT", "MAY")
    return "<aside".join(pieces), flipped


# -- 결함이 사는 조건이 실제로 재현되는가 -------------------------------------


def test_revision_outside_the_extracted_body_leaves_every_verdict_untouched(
    tmp_path, fixture_server
):
    """규범 요구를 뒤집어도 `unchanged`·같은 `text_hash`·`INTACT`.

    이것은 고칠 대상이 아니라 **재현해야 할 사실**이다. 이 사실이 성립하는
    한, 사용자에게 남는 유일한 방어는 "우리가 얼마를 보는가"를 아는 것이다.
    """
    base, state = fixture_server
    state.html = SPEC_HTML
    with _anchor(tmp_path) as anchor:
        first = anchor.fetch(f"{base}/article")
        cited = anchor.cite(f"{base}/article", SPEC_QUOTE)

        revised, flipped = _revise_the_invisible_region(SPEC_HTML)
        assert flipped >= 6, "픽스처가 사각지대 안의 규범 요구를 담지 못했다"
        state.html = revised
        state.etag = '"v2"'
        again = anchor.fetch(f"{base}/article", max_age=0)
        report = anchor.verify(anchor_ids=[cited.anchor_id])

    assert again.outcome == "unchanged"
    assert again.text_hash == first.text_hash
    assert report.summary["INTACT"] == 1
    assert report.attention == ()


def test_the_response_now_says_how_much_of_the_document_was_seen(
    tmp_path, fixture_server
):
    """같은 조건에서 응답이 침묵하지 않는다 (D-239)."""
    base, state = fixture_server
    state.html = SPEC_HTML
    with _anchor(tmp_path) as anchor:
        result = anchor.fetch(f"{base}/article")

    assert result.coverage.basis == "html-prose"
    assert result.coverage.ratio < COVERAGE_WARN_RATIO
    assert dict(result.coverage.dropped).get("aside", 0) >= 10
    assert result.notes, "포착 범위가 좁은데 아무 말도 하지 않는다"
    assert "aside" in result.notes[0]


def test_raw_change_and_narrow_coverage_are_reported_together(tmp_path, fixture_server):
    """"바이트는 바뀌었고 우리는 일부만 본다" — D-232와 D-239의 결합 (D-242)."""
    base, state = fixture_server
    state.html = SPEC_HTML
    with _anchor(tmp_path) as anchor:
        anchor.fetch(f"{base}/article")
        state.html, _ = _revise_the_invisible_region(SPEC_HTML)
        state.etag = '"v2"'
        again = anchor.fetch(f"{base}/article", max_age=0)

    assert again.outcome == "unchanged", "판정을 바꾸면 안 된다"
    assert again.raw_changed is True
    blind = [note for note in again.notes if "raw bytes differed" in note]
    assert blind, again.notes


def test_coverage_is_remeasured_when_the_invisible_region_grows(tmp_path, fixture_server):
    """정정 고지가 새로 붙으면 산문 총량이 늘고 포착률이 떨어진다.

    본문이 같다고(`unchanged`) 옛 측정값을 그대로 두면 **지금의 사실**을
    말하지 못한다.
    """
    base, state = fixture_server
    state.html = SPEC_HTML
    added = (
        '<aside class="correction"><p>Correction (2026-08-20): every requirement in '
        "this handbook has been superseded by the second edition, and implementers "
        "MUST NOT treat the text above as current guidance any longer.</p></aside>"
    )
    with _anchor(tmp_path) as anchor:
        first = anchor.fetch(f"{base}/article")
        state.html = SPEC_HTML.replace("</body>", added + "</body>")
        state.etag = '"v2"'
        again = anchor.fetch(f"{base}/article", max_age=0)
        # 응답만 보면 계측이 저장까지 갔는지 알 수 없다 — 판본을 다시 읽는다.
        stored, _ = anchor.get_version(again.version_id)

    assert again.outcome == "unchanged"
    assert again.version_id == first.version_id, "같은 본문이므로 같은 판본이다"
    assert again.coverage.prose_chars > first.coverage.prose_chars
    assert again.coverage.ratio < first.coverage.ratio
    assert stored.coverage.prose_chars == again.coverage.prose_chars, (
        "다시 잰 값이 판본에 남지 않았다 — 다음 cite는 옛 사실을 말한다"
    )


# -- 거짓 경보 검증 ----------------------------------------------------------


def test_ordinary_news_article_is_measured_and_silent(tmp_path, fixture_server):
    """내비게이션·쿠키 배너·푸터가 본문보다 큰 정상 기사 — 값은 나오되 침묵."""
    base, state = fixture_server
    state.html = NEWS_HTML
    with _anchor(tmp_path) as anchor:
        result = anchor.fetch(f"{base}/article")
        cited = anchor.cite(f"{base}/article", NEWS_QUOTE)
        report = anchor.verify(anchor_ids=[cited.anchor_id])

    assert result.coverage.ratio is not None
    assert result.notes == (), result.notes
    assert cited.warnings == (), cited.warnings
    assert report.low_coverage == 0


def test_narrow_coverage_does_not_leak_into_the_verdict(tmp_path, fixture_server):
    """포착 범위가 좁아도 `outcome`은 그대로다 — 정보만 는다."""
    base, state = fixture_server
    state.html = SPEC_HTML
    with _anchor(tmp_path) as anchor:
        first = anchor.fetch(f"{base}/article")
        assert first.outcome == "created"
        state.html = SPEC_HTML.replace(
            "This section introduces authentication",
            "This section now introduces authentication",
        )
        state.etag = '"v2"'
        again = anchor.fetch(f"{base}/article", max_age=0)
    assert again.outcome == "changed"


# -- 다른 진입점 (재현 통과 ≠ 완료) ------------------------------------------


def test_cite_says_what_it_is_anchoring_into(tmp_path, fixture_server):
    """`cite`는 어떤 경우에도 네트워크에 나가지 않는다 — 저장된 값이 유일한 길 (D-240)."""
    base, state = fixture_server
    state.html = SPEC_HTML
    with _anchor(tmp_path) as anchor:
        anchor.fetch(f"{base}/article")
        cited = anchor.cite(f"{base}/article", SPEC_QUOTE)

    assert cited.coverage.basis == "html-prose"
    assert cited.coverage.ratio < COVERAGE_WARN_RATIO
    narrow = [w for w in cited.warnings if "re-verify as" in w]
    assert narrow, cited.warnings


def test_verify_report_counts_narrow_coverage_even_when_everything_is_intact(
    tmp_path, fixture_server
):
    """전부 INTACT인 보고서에서 이 사실이 사라지면 안 된다 (D-241, D-093과 같은 판단)."""
    base, state = fixture_server
    state.html = SPEC_HTML
    with _anchor(tmp_path) as anchor:
        anchor.fetch(f"{base}/article")
        anchor.cite(f"{base}/article", SPEC_QUOTE)
        report = anchor.verify()

    assert report.summary["INTACT"] == 1
    assert report.attention == ()
    assert report.low_coverage == 1


def test_stored_version_remembers_its_coverage(tmp_path, fixture_server):
    base, state = fixture_server
    state.html = SPEC_HTML
    with _anchor(tmp_path) as anchor:
        result = anchor.fetch(f"{base}/article")
        version, _ = anchor.get_version(result.version_id)
    assert version.coverage.basis == "html-prose"
    assert version.coverage.dropped


def test_cache_hit_carries_the_stored_coverage_without_recomputing(
    tmp_path, fixture_server, monkeypatch
):
    """캐시 히트는 추출을 하지 않는다 — 계측 비용도 실리면 안 된다."""
    base, state = fixture_server
    state.html = SPEC_HTML
    with _anchor(tmp_path) as anchor:
        first = anchor.fetch(f"{base}/article")

        from anchor.normalize import coverage as coverage_module

        def forbidden(*args, **kwargs):  # pragma: no cover - 호출되면 실패다
            raise AssertionError("캐시 히트 경로에서 포착 범위를 다시 쟀다")

        monkeypatch.setattr(coverage_module, "measure_html", forbidden)
        hit = anchor.fetch(f"{base}/article")

    assert hit.outcome == "cache_hit"
    assert hit.coverage.basis == "html-prose"
    assert hit.coverage.prose_chars == first.coverage.prose_chars


def test_not_modified_keeps_the_stored_coverage(tmp_path, fixture_server):
    """304에는 원본 HTML이 없다 — 모르는 것으로 아는 것을 덮지 않는다."""
    base, state = fixture_server
    state.html = SPEC_HTML
    with _anchor(tmp_path) as anchor:
        first = anchor.fetch(f"{base}/article")
        again = anchor.fetch(f"{base}/article", max_age=0)
    assert again.outcome == "not_modified"
    assert again.coverage.prose_chars == first.coverage.prose_chars


def test_cli_prints_the_coverage_line(tmp_path, fixture_server):
    base, state = fixture_server
    state.html = SPEC_HTML
    runner = CliRunner()
    result = runner.invoke(
        app, ["fetch", f"{base}/article", "--db", str(tmp_path / "cli.db")]
    )
    assert result.exit_code == 0, result.output
    assert "포착 범위" in result.output
    assert "미포착 aside" in result.output


def test_cli_json_carries_the_derived_ratio(tmp_path, fixture_server):
    base, state = fixture_server
    state.html = SPEC_HTML
    runner = CliRunner()
    result = runner.invoke(
        app, ["fetch", f"{base}/article", "--json", "--db", str(tmp_path / "cli.db")]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["coverage"]["ratio"] < COVERAGE_WARN_RATIO
    assert payload["notes"]


def test_cli_verify_reports_narrow_coverage(tmp_path, fixture_server):
    base, state = fixture_server
    state.html = SPEC_HTML
    db = str(tmp_path / "cli.db")
    runner = CliRunner()
    assert runner.invoke(app, ["fetch", f"{base}/article", "--db", db]).exit_code == 0
    assert (
        runner.invoke(app, ["cite", f"{base}/article", SPEC_QUOTE, "--db", db]).exit_code
        == 0
    )
    result = runner.invoke(app, ["verify", "--db", db])
    assert result.exit_code == 0, result.output
    assert "포착 범위 좁음" in result.output


# -- 다른 경로: 아카이브 · 폴백 추출기 · 산문 없는 문서 ----------------------


def test_archive_snapshot_is_measured_too(tmp_path, fixture_server, monkeypatch):
    """아카이브에서 되살린 본문도 사각지대를 가진다 — 재지 않으면 원본이
    사라진 뒤의 판정이 무엇을 보고 내려졌는지 영영 알 수 없다."""
    from anchor.fetcher import archive as archive_module

    base, state = fixture_server
    monkeypatch.setattr(archive_module, "WAYBACK_BASE", base)
    config = Config(
        db_path=tmp_path / "store.db",
        rate_limit_rps=1000.0,
        retry_backoff_base=0.01,
        archive_fallback_enabled=True,
        archive_aggregator="",
    )
    state.html = NEWS_HTML
    with Anchor(db_path=config.db_path, config=config) as anchor:
        anchor.fetch(f"{base}/article")
        state.status_override = 404          # 원본 사망
        state.archive_html = SPEC_HTML       # 아카이브에는 사각지대가 큰 판본
        recovered = anchor.fetch(f"{base}/article", max_age=0)

    assert recovered.source == "archive"
    assert recovered.coverage.basis == "html-prose"
    assert dict(recovered.coverage.dropped).get("aside", 0) >= 10
    assert recovered.notes


def test_document_without_prose_units_says_it_could_not_measure(tmp_path, fixture_server):
    """색인 페이지에서 0%는 사실이 아니다 — 잴 것이 없었다 (D-243과 같은 판단)."""
    base, state = fixture_server
    state.html = (STRUCTURE / "index-links.html").read_text("utf-8")
    with _anchor(tmp_path) as anchor:
        result = anchor.fetch(f"{base}/article")
    assert result.coverage.basis == "no-prose"
    assert result.coverage.ratio is None
    assert result.notes == ()

    runner = CliRunner()
    printed = runner.invoke(
        app, ["fetch", f"{base}/article", "--db", str(tmp_path / "cli.db")]
    )
    assert printed.exit_code == 0, printed.output
    assert "측정 불가" in printed.output


def test_coverage_is_measured_against_the_body_that_gets_stored(tmp_path, fixture_server):
    """폴백 추출기가 채택되면 계측도 그쪽을 기준으로 해야 한다 — 아니면
    저장되지 않은 본문을 잰 셈이 된다."""
    from anchor.normalize.extract import (
        READABILITY_PIPELINE_VERSION,
        to_normalized,
    )

    # D-072 픽스처와 같은 모양: trafilatura가 블록 구분 없는 평문을 돌려준다.
    html = (
        "<html><body><div><h1>Service status</h1>"
        "<p>Degraded performance on the ingest path since 09:20 UTC, and the "
        "team is working on the backlog that built up behind it.</p>"
        "</div></body></html>"
    )
    document = to_normalized(html.encode("utf-8"), "text/html")
    if document.pipeline_version != READABILITY_PIPELINE_VERSION:
        pytest.skip("이 입력에서 폴백이 채택되지 않는다 — 추출기 판단이 바뀌었다")
    assert document.coverage.basis == "html-prose"
    assert document.coverage.captured_chars > 0
