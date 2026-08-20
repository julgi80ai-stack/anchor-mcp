# SPDX-License-Identifier: Apache-2.0
"""포착 범위 계측과 그 문턱의 근거 (D-239, SPEC §5.5).

이 파일의 절반은 **거짓 경보를 만들지 않았다는 증거**다. 문턱은 임의값이
아니라 코퍼스 관측에서 나왔고, 코퍼스가 달라지면 여기서 깨져야 한다.

축 (구조 다양성): 산문 기사 / 규격 문서형(주석이 `<aside>`에 사는 문서) /
목록·표 중심 / `<figcaption>`이 있는 문서 / 내비게이션이 본문보다 큰 문서 /
산문 단위가 없는 색인 / PDF / plain text. 골든 36건은 전부 "깨끗한 산문"
하나뿐이라 이 축이 없었다 — `tests/fixtures/structure/`가 그 축을 연다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from anchor.models import Coverage
from anchor.normalize import coverage as coverage_metric
from anchor.normalize.coverage import (
    COVERAGE_BLIND_SPOT_RATIO,
    COVERAGE_WARN_RATIO,
    coverage_notes,
    measure_html,
)
from anchor.normalize.extract import to_normalized

GOLDEN = Path(__file__).parent.parent / "fixtures" / "golden"
STRUCTURE = Path(__file__).parent.parent / "fixtures" / "structure"


def _coverage_of(path: Path) -> Coverage:
    raw = path.read_bytes()
    return to_normalized(raw, "text/html").coverage


def _character_retention(path: Path) -> float:
    """기각된 후보 (가): 저장 문자수 / 가시 텍스트 문자수."""
    import lxml.html

    raw = path.read_bytes()
    body = to_normalized(raw, "text/html").text
    tree = lxml.html.document_fromstring(raw.decode("utf-8"))
    for tag in ("script", "style", "head", "noscript"):
        for element in list(tree.iter(tag)):
            element.drop_tree()
    visible = re.sub(r"\s+", " ", tree.text_content()).strip()
    return len(body) / len(visible)


# -- 축이 실제로 열려 있는가 -------------------------------------------------


def test_structure_corpus_covers_the_axis():
    """픽스처가 구조 다양성 축을 실제로 담고 있는지 (D-076과 같은 판단).

    담고 있는지만 본다 — 옳게 처리하는지는 아래 검사들이 맡는다.
    """
    sources = {p.stem: p.read_text("utf-8") for p in STRUCTURE.glob("*.html")}
    assert len(sources) >= 5
    axis = {
        "주석이 <aside>에 사는 문서": lambda h: "<aside" in h,
        "그림 설명": lambda h: "<figcaption" in h,
        "표": lambda h: "<table" in h,
        "목록": lambda h: "<ul" in h or "<ol" in h,
        "내비게이션": lambda h: "<nav" in h,
        "푸터": lambda h: "<footer" in h,
        "정정 고지": lambda h: "correction" in h,
        "XML 선언": lambda h: h.lstrip().startswith("<?xml"),
        "같은 서두의 반복": lambda h: h.count("Note: this requirement applies to") >= 20,
        "분모 붕괴(링크 행이 페이지를 채운다)": lambda h: h.count("<div class='post'>") >= 40,
    }
    missing = [name for name, pred in axis.items() if not any(pred(h) for h in sources.values())]
    assert not missing, f"구조 축이 픽스처에 없다: {missing}"


# -- 채택한 지표: 산문 블록 포착률 -------------------------------------------


def test_extraction_blind_spot_is_measured_not_assumed():
    """주석이 `<aside>`에 사는 문서에서 추출기는 그 전부를 버린다.

    이것이 D-239가 가리키는 사각지대다. 고치는 것은 추출 범위가 아니라
    **침묵**이다 — 얼마를 못 봤는지가 값으로 나와야 한다.
    """
    coverage = _coverage_of(STRUCTURE / "sidebar-spec.html")
    assert coverage.basis == "html-prose"
    assert coverage.ratio is not None and coverage.ratio < COVERAGE_WARN_RATIO
    dropped = dict(coverage.dropped)
    assert dropped.get("aside", 0) >= 10, dropped
    # 정정 고지가 실제로 저장 본문에 없다 — 경고가 가리키는 것이 이것이다.
    body = to_normalized((STRUCTURE / "sidebar-spec.html").read_bytes(), "text/html").text
    assert "erratum 7042" not in body


def test_figure_captions_are_counted_when_they_vanish():
    coverage = _coverage_of(STRUCTURE / "caption-atlas.html")
    assert dict(coverage.dropped).get("figcaption", 0) >= 3


def test_index_page_without_prose_reports_unmeasured_not_zero():
    """산문 단위가 없는 색인에서 0%는 사실이 아니다 — 잴 것이 없었다."""
    coverage = _coverage_of(STRUCTURE / "index-links.html")
    assert coverage.basis == "no-prose"
    assert coverage.ratio is None
    assert coverage_notes(coverage, raw_changed=True) == ()


# -- 거짓 경보 검증 ----------------------------------------------------------


def test_nav_heavy_news_page_is_silent_although_most_bytes_are_dropped():
    """정상 뉴스 기사 — 내비게이션·쿠키 배너·푸터·사이트맵이 본문보다 크다.

    **기각한 후보 (가)를 여기서 실증한다**: 문자 보존율은 경보 문턱 아래로
    떨어지지만, 그 페이지의 추출은 옳다. 보존율로 경보를 걸었다면 정상
    기사마다 울렸을 것이다.
    """
    path = STRUCTURE / "news-boilerplate.html"
    retention = _character_retention(path)
    coverage = _coverage_of(path)
    assert retention < COVERAGE_WARN_RATIO, (
        f"픽스처가 (가)의 오탐 조건을 담지 못한다: 보존율 {retention:.3f}"
    )
    assert coverage.ratio > COVERAGE_WARN_RATIO, (
        f"정상 기사에서 경보가 울렸다: 포착률 {coverage.ratio:.3f}"
    )
    assert coverage_notes(coverage) == ()


def test_golden_corpus_never_raises_a_coverage_warning():
    """골든 36건(깨끗한 산문)은 하나도 경보를 울리지 않아야 한다."""
    noisy = []
    for path in sorted(GOLDEN.glob("*.html")):
        coverage = _coverage_of(path)
        if coverage_notes(coverage):
            noisy.append((path.stem, coverage.ratio))
    assert noisy == [], f"정상 문서에서 경보: {noisy}"


def test_golden_corpus_stays_above_the_blind_spot_threshold():
    """`raw_changed`와 겹칠 때의 문턱(2/3)도 골든에서는 울리지 않는다."""
    below = [
        (path.stem, round(_coverage_of(path).ratio, 3))
        for path in sorted(GOLDEN.glob("*.html"))
        if (_coverage_of(path).ratio or 1.0) < COVERAGE_BLIND_SPOT_RATIO
    ]
    assert below == [], f"정상 문서가 사각지대 문턱 아래로 내려갔다: {below}"


def test_ordinary_structures_are_measured_but_quiet():
    """목록·표·그림 중심 문서는 값이 나오되 경보는 없다."""
    for name in ("list-table.html", "caption-atlas.html", "xml-declaration.html"):
        coverage = _coverage_of(STRUCTURE / name)
        assert coverage.ratio is not None
        assert coverage_notes(coverage) == (), name


# -- 경로별로 무엇을 재는가 (D-243) ------------------------------------------


def test_plain_text_reports_whole_document_not_a_measured_ratio():
    """text/plain은 고른 것이 없으므로 버린 영역도 없다."""
    document = to_normalized("본문이 전부다.\n\n두 번째 문단.".encode(), "text/plain")
    assert document.coverage.basis == "whole-document"
    assert document.coverage.ratio == 1.0
    assert coverage_notes(document.coverage, raw_changed=True) == ()


def test_pdf_admits_it_cannot_measure_rather_than_claiming_full_coverage():
    """PDF에서 1.0은 거짓 확신이다 — 2단 조판은 문자를 잃지 않고 순서만 뒤섞는다."""
    pypdf = pytest.importorskip("pypdf")
    import io

    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    from anchor.errors import UnsupportedContent

    with pytest.raises(UnsupportedContent):
        to_normalized(buffer.getvalue(), "application/pdf")
    # 텍스트 레이어가 있는 PDF 경로의 계약은 함수 자체로 고정한다.
    assert coverage_metric.not_measurable().basis == "not-measurable"
    assert coverage_metric.not_measurable().ratio is None
    assert coverage_metric.not_measurable().measured is False


# -- 계측 자체의 견고성 ------------------------------------------------------


def test_measurement_failure_reports_unknown_and_never_raises():
    """계측이 페치를 죽이면 안 된다 — 모르면 모른다고 한다."""
    assert measure_html("", "").basis in ("unknown", "no-prose")
    assert measure_html("\x00\x00\x00", "x").basis in ("unknown", "no-prose")


def test_nested_blocks_are_counted_once():
    """`<li><p>…</p></li>`처럼 셀 수 있는 블록이 겹치면 한 번만 센다.

    두 번 세면 목록·표·정의 목록이 많은 문서에서 분모가 부풀어, 같은
    문서가 구조에 따라 다른 포착률을 갖는다.
    """
    sentence = "이 문장은 사십 자를 넘기기 위해 충분히 길게 적어 둔 산문 한 줄이다."
    html = (
        f"<html><body><ul><li><p>{sentence}</p></li></ul>"
        f"<table><tr><td><p>{sentence}</p></td></tr></table></body></html>"
    )
    # 저장 본문에도 두 블록이 그대로 있다 — 원래 이 픽스처는 본문에 한 벌만
    # 두고 100% 포착을 단언했다. 담기지 않은 블록을 포착으로 세라는 뜻이 되어
    # 그 자체가 D-271의 요구였다(조치 절차 4: 픽스처에 정상값을 박지 않는다).
    coverage = measure_html(html, f"{sentence}\n\n{sentence}")
    assert coverage.prose_chars == 2 * len(sentence), "겹친 블록을 두 번 이상 셌다"
    assert coverage.captured_chars == coverage.prose_chars


@pytest.mark.parametrize(
    "html, body",
    [
        # 표 → 파이프 표기
        (
            "<html><body><table><tr><td>304</td><td>{text}</td></tr></table></body></html>",
            "| 304 | {text} |",
        ),
        # 코드 블록 → 울타리 + 들여쓰기
        (
            "<html><body><pre>{text}</pre></body></html>",
            "```\n    {text}\n```",
        ),
        # 제목 → `##` 접두, 인용 → `>` 접두
        (
            "<html><body><h2>{text}</h2></body></html>",
            "## {text}",
        ),
        (
            "<html><body><blockquote><p>{text}</p></blockquote></body></html>",
            "> {text}",
        ),
        # 줄바꿈이 다르게 접힌 문단
        (
            "<html><body><p>{text}</p></body></html>",
            "{wrapped}",
        ),
    ],
)
def test_markdown_reformatting_does_not_count_as_a_dropped_block(html, body):
    """마크다운 변환은 앞머리를 손댄다 — 지문이 그것을 견뎌야 한다.

    이 내성은 지문을 여러 곳에서 뽑아 만드는 것이 아니라(그러면 긴 문서에서
    충돌해 과대계상이 된다) 골격 정규화로 만든다.
    """
    text = "The validator supplied by the client still matches, so the stored copy may be reused."
    wrapped = text.replace(" so the", "\nso the")
    coverage = measure_html(
        html.format(text=text), body.format(text=text, wrapped=wrapped)
    )
    assert coverage.dropped == (), coverage
    assert coverage.captured_chars == coverage.prose_chars


def test_multiline_code_block_survives_whitespace_reflow():
    """`<pre>` 안의 줄바꿈·들여쓰기는 마크다운에서 다시 짜인다.

    지문을 원문 그대로 비교하면 코드 블록이 통째로 "미포착"이 된다 — 골든
    ko-10에서 실제로 그랬다(0.52 → 0.90). 그 내성은 골격 정규화가 만든다.
    """
    source = "x = 1\ny = compute(x)\nreturn y + normalize(x)"
    html = f"<html><body><pre><code>{source}</code></pre></body></html>"
    body = "```\n" + "\n".join("    " + line for line in source.split("\n")) + "\n```"
    coverage = measure_html(html, body)
    assert coverage.dropped == (), coverage
    assert coverage.captured_chars == coverage.prose_chars


def test_captured_chars_can_never_exceed_the_stored_body():
    """포착 문자수가 저장 본문보다 많으면 담을 수 없는 것을 담았다고 말한 것이다.

    지문을 여러 곳에서 뽑던 초안이 정확히 그렇게 깨졌다 — 실측에서 RFC 9110의
    포착 문자수가 14,178자로 나왔는데 저장 본문 자체가 5,590자였다.
    """
    offenders = []
    corpus = sorted(list(GOLDEN.glob("*.html")) + list(STRUCTURE.glob("*.html")))
    for path in corpus:
        raw = path.read_bytes()
        body = to_normalized(raw, "text/html").text
        coverage = measure_html(raw.decode("utf-8"), body)
        if (coverage.captured_chars or 0) > len(body):
            offenders.append((path.stem, coverage.captured_chars, len(body)))
    assert offenders == [], f"포착 문자수가 저장 본문을 넘었다: {offenders}"


def test_short_labels_are_not_prose_units():
    """40자 미만은 메뉴 항목·라벨·표 머리글이지 산문이 아니다.

    세면 두 방향으로 값이 망가진다: 분모가 짧은 조각으로 부풀고, 두세 글자
    지문("OK"·"Code")은 어느 본문에나 들어 있어 **미포착인 것을 포착으로**
    세게 된다.
    """
    sentence = (
        "The validator supplied by the client still matches, so the stored copy "
        "may be reused without another download."
    )
    labels = "".join(f"<td>{label}</td>" for label in
                     ("Code", "OK", "1", "of", "The", "Menu", "Share", "Print"))
    html = f"<html><body><p>{sentence}</p><table><tr>{labels}</tr></table></body></html>"
    coverage = measure_html(html, sentence)
    assert coverage.prose_chars == len(sentence), "짧은 라벨이 산문으로 계상됐다"


def test_repeated_closing_clauses_do_not_inflate_the_count():
    """규범 문서는 같은 마무리 절을 반복한다 — 그 반복이 계측을 속이면 안 된다."""
    path = STRUCTURE / "repeated-tails.html"
    body = to_normalized(path.read_bytes(), "text/html").text
    coverage = measure_html(path.read_text("utf-8"), body)
    assert coverage.captured_chars <= len(body)
    assert dict(coverage.dropped).get("aside", 0) >= 30
    assert coverage.ratio < COVERAGE_WARN_RATIO


def test_link_dense_navigation_is_not_counted_as_prose():
    links = "".join(f'<li><a href="/{i}">Section number {i} of the handbook</a></li>' for i in range(8))
    html = f"<html><body><nav><ul>{links}</ul></nav></body></html>"
    coverage = measure_html(html, "")
    assert coverage.basis == "no-prose"


# -- 문장은 판정을 바꾸지 않는다 ---------------------------------------------


def test_notes_never_claim_the_verdict_changed():
    coverage = Coverage(basis="html-prose", prose_chars=1000, captured_chars=30,
                        dropped=(("aside", 4),))
    notes = coverage_notes(coverage, raw_changed=True)
    assert len(notes) == 2
    joined = " ".join(notes)
    for forbidden in ("changed", "ALTERED", "변경되었습니다", "달라졌습니다."):
        assert forbidden not in joined.replace("The raw bytes differed", "")
    assert "3%" in joined
    assert "aside×4" in joined


def test_blind_spot_note_needs_both_signals():
    """`raw_changed` 하나만으로도, 포착률 하나만으로도 그 문장은 나오지 않는다."""
    healthy = Coverage(basis="html-prose", prose_chars=1000, captured_chars=990)
    assert coverage_notes(healthy, raw_changed=True) == ()
    narrow = Coverage(basis="html-prose", prose_chars=1000, captured_chars=500)
    assert coverage_notes(narrow, raw_changed=False) == ()
    assert len(coverage_notes(narrow, raw_changed=True)) == 1


def test_unknown_coverage_says_nothing():
    assert coverage_notes(Coverage.unknown(), raw_changed=True) == ()
    assert coverage_notes(None) == ()


def test_sub_one_percent_is_not_collapsed_to_zero():
    """RFC 9110의 실측은 3.6%다. `0%`로 적으면 계측이 고장 난 것처럼 읽힌다."""
    from anchor.normalize.coverage import format_ratio

    assert format_ratio(0.0036) == "0.36%"
    assert format_ratio(0.036) == "4%"
    assert format_ratio(0.452) == "45%"
    coverage = Coverage(basis="html-prose", prose_chars=100_000, captured_chars=360)
    (note,) = coverage_notes(coverage)
    assert "0.36%" in note


# -- 지문이 유일성을 보장하지 못하는 자리 (D-271) ----------------------------


def _visible_chars(html: str) -> int:
    """가시 텍스트 문자수 — 구현과 무관하게 테스트가 스스로 잰다."""
    import lxml.html

    tree = lxml.html.document_fromstring(re.sub(r"^[\s﻿]*<\?xml[^>]*\?>", "", html))
    for tag in ("script", "style", "noscript", "template", "head", "svg", "math"):
        for element in list(tree.iter(tag)):
            element.drop_tree()
    return len(re.sub(r"\s+", " ", tree.text_content()).strip())


def test_repeated_opening_clauses_do_not_credit_absent_blocks():
    """같은 서두로 시작하는 블록이 30개면 지문 하나가 30개를 살려 낸다.

    꼬리 충돌(`repeated-tails`)은 조치했는데 **머리 충돌**은 픽스처에 없었다.
    저장 본문에 없는 정정 고지가 '포착'으로 계상되면, 도구는 침묵이 아니라
    **적극적인 거짓**을 말한다 — 못 본 것을 다 봤다고 한다.
    """
    path = STRUCTURE / "repeated-heads.html"
    body = to_normalized(path.read_bytes(), "text/html").text
    coverage = measure_html(path.read_text("utf-8"), body)
    # 정정 고지는 실제로 저장 본문에 없다.
    assert "erratum 7005" not in body
    assert "erratum 7029" not in body
    # 그렇다면 그 블록들은 미포착으로 세어져야 한다.
    assert dict(coverage.dropped).get("aside", 0) >= 25, coverage.dropped
    assert coverage.ratio is not None and coverage.ratio < 0.75, coverage
    assert coverage.captured_chars <= len(body)


def test_a_single_giant_block_cannot_be_captured_by_a_tiny_body():
    """블록 하나가 42만 자여도, 85자짜리 본문이 그것을 담을 수는 없다.

    앞머리 16자만 보고 통째로 포착으로 세면 저장 본문의 5,000배를 '봤다'고
    말하게 된다. 신용은 **저장 본문에서 실제로 차지한 자리**를 넘지 않는다.
    """
    sentence = "The archive keeps the bytes it received and the time it received them. "
    html = f"<html><body><p>{sentence * 6000}</p></body></html>"
    body = sentence[:85]
    coverage = measure_html(html, body)
    assert coverage.captured_chars <= len(body)
    assert coverage.ratio < COVERAGE_WARN_RATIO
    assert coverage_notes(coverage), "42만 자 중 85자를 보고도 침묵했다"


def test_punctuation_only_blocks_are_not_prose_units():
    """골격이 비는 블록(`* `의 반복)은 지문이 빈 문자열이 되어 **어느 본문에나**
    들어 있다. 그것을 산문으로 세면 분모도 분자도 함께 거짓이 된다."""
    sentence = (
        "The validator supplied by the client still matches, so the stored copy "
        "may be reused without another download."
    )
    separators = "".join("<p>" + "* " * 30 + "</p>" for _ in range(5))
    html = f"<html><body><p>{sentence}</p>{separators}</body></html>"
    coverage = measure_html(html, sentence)
    assert coverage.prose_chars == len(sentence), "구분선이 산문으로 계상됐다"
    assert coverage.captured_chars == len(sentence)


def test_the_stored_body_vouches_for_one_block_at_a_time():
    """같은 문장이 문서에 두 번, 저장 본문에 한 번 있으면 신용도 한 번이다."""
    sentence = (
        "The validator supplied by the client still matches, so the stored copy "
        "may be reused without another download."
    )
    html = f"<html><body><p>{sentence}</p><p>{sentence}</p></body></html>"
    coverage = measure_html(html, sentence)
    assert coverage.prose_chars == 2 * len(sentence)
    assert coverage.captured_chars <= len(sentence)


# -- XML 선언 한 줄에 계측이 침묵하지 않는가 (D-272) -------------------------


def test_xml_declaration_does_not_silence_the_measurement():
    """`<?xml …?>`로 시작하는 XHTML은 `_HTML_TYPES`의 정면 지원 경로다.

    lxml은 인코딩 선언이 붙은 **문자열** 입력을 거부한다. 그 예외를 삼키면
    계측 전체가 `unknown`이 되어 아무 말도 하지 않는다 — 선언 한 줄이 사각지대
    고지를 통째로 지운다.
    """
    path = STRUCTURE / "xml-declaration.html"
    body = to_normalized(path.read_bytes(), "text/html").text
    coverage = measure_html(path.read_text("utf-8"), body)
    assert coverage.basis == "html-prose", coverage
    assert coverage.ratio is not None and coverage.ratio > COVERAGE_BLIND_SPOT_RATIO
    assert coverage_notes(coverage, raw_changed=True) == (), "정상 XHTML 문서에서 경보"


def test_xml_declared_document_still_reports_its_blind_spot():
    """선언이 붙었다고 사각지대가 사라지지는 않는다 — 반대 방향도 확인한다."""
    lead = (
        "The archive keeps the bytes it received and the time it received them, so a "
        "quotation can be checked again later."
    )
    notices = "".join(
        f"<aside><p>Erratum {7000 + i} withdraws the published limit stated in section "
        f"{i} and republishes it as {40 + i} milliseconds with immediate effect.</p></aside>"
        for i in range(20)
    )
    html = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<html><body><article><h1>Profile</h1><p>{lead}</p></article>"
        f"<div id='comments'>{notices}</div></body></html>"
    )
    body = to_normalized(html.encode("utf-8"), "text/html").text
    coverage = measure_html(html, body)
    assert coverage.basis == "html-prose"
    assert coverage.ratio < COVERAGE_WARN_RATIO, coverage
    assert dict(coverage.dropped).get("aside", 0) >= 15
    assert coverage_notes(coverage)


# -- 분모가 붕괴하면 비율을 주장하지 않는다 (D-273) --------------------------


def test_sparse_prose_page_does_not_claim_full_coverage():
    """산문 단위가 페이지에 비해 무시할 만큼 적으면 100%는 거짓 확신이다.

    실웹 blog.rust-lang.org: 가시 15,661자 중 저장 본문은 162자인데 산문
    단위가 105자뿐이라 `ratio=1.0`이 나왔다. `no-prose`(모른다)보다 나쁘다 —
    침묵이 아니라 **다 봤다는 주장**이기 때문이다.
    """
    path = STRUCTURE / "sparse-prose-hub.html"
    html = path.read_text("utf-8")
    body = to_normalized(path.read_bytes(), "text/html").text
    # 픽스처가 그 조건을 실제로 담는다: 본문은 페이지의 한 줌이다.
    assert len(body) < _visible_chars(html) / 10
    coverage = measure_html(html, body)
    assert coverage.ratio is None, f"한 줌만 보고 {coverage.ratio}를 주장했다"
    assert coverage.basis == "no-prose"
    assert coverage_notes(coverage, raw_changed=True) == ()


def test_prose_share_threshold_sits_in_an_observed_empty_band():
    """문턱은 임의값이 아니라 코퍼스 관측의 빈 구간에서 나온다 (§12 v1.11).

    관측(픽스처 45건 + 실웹 8건): 허브·색인 페이지는 0.007~0.018이고, 비율을
    재는 문서는 0.358(내비게이션이 본문보다 큰 뉴스) 이상, 골든은 0.82
    이상이다. 그 사이 0.018~0.358이 빈 구간이며 문턱은 거기 놓인다.
    """
    from anchor.normalize.coverage import COVERAGE_MIN_PROSE_SHARE, _measure

    shares = {}
    for path in sorted(list(GOLDEN.glob("*.html")) + list(STRUCTURE.glob("*.html"))):
        measured = _measure(path.read_text("utf-8"),
                            to_normalized(path.read_bytes(), "text/html").text)
        if measured is None or not measured.visible_chars or not measured.prose_chars:
            continue
        shares[path.stem] = measured.prose_chars / measured.visible_chars

    above = {n: s for n, s in shares.items() if s >= COVERAGE_MIN_PROSE_SHARE}
    below = {n: s for n, s in shares.items() if s < COVERAGE_MIN_PROSE_SHARE}
    assert below, "분모 붕괴 축이 코퍼스에 없다 — 문턱을 뒷받침할 관측이 없다"
    assert min(above.values()) > COVERAGE_MIN_PROSE_SHARE * 2, (
        f"비율을 재는 문서가 문턱에 붙어 있다: {sorted(above.items(), key=lambda i: i[1])[:3]}"
    )
    assert max(below.values()) < COVERAGE_MIN_PROSE_SHARE / 2, (
        f"침묵시키는 문서가 문턱에 붙어 있다: {sorted(below.items(), key=lambda i: -i[1])[:3]}"
    )


def test_a_block_contained_in_another_is_not_credited_twice():
    """한 블록의 본문 자리가 다른 블록의 근거로 또 쓰이면 안 된다.

    앞 블록의 글이 뒤 블록을 통째로 품고 있으면 앞머리도 꼬리도 그 안에서
    맞는다. 그 자리를 두 번 세면 저장 본문보다 많은 글자를 봤다고 말하게 된다.
    """
    long_block = (
        "Alpha. The archive keeps the bytes it received and the time it received "
        "them, so a quotation can be checked again later."
    )
    inner = long_block[len("Alpha. ") :]
    html = f"<html><body><p>{long_block}</p><p>{inner}</p></body></html>"
    coverage = measure_html(html, long_block)
    assert coverage.prose_chars == len(long_block) + len(inner)
    assert coverage.captured_chars <= len(long_block)


def test_many_identical_paragraphs_are_each_credited_once():
    """같은 문단이 100번 반복되고 본문에도 100번 있으면 100번 다 포착이다.

    같은 앞머리를 가진 블록끼리 탐색 위치를 이어받지 않으면, 뒤쪽 블록들이
    이미 쓰인 자리만 되풀이해 훑다가 후보 상한에 걸려 통째로 미포착이 된다 —
    반복이 많은 규격 문서에서 값이 이유 없이 무너진다.
    """
    sentence = (
        "The archive keeps the bytes it received and the time it received them, "
        "so a quotation can be checked again later."
    )
    copies = 100
    html = "<html><body>" + f"<p>{sentence}</p>" * copies + "</body></html>"
    body = "\n\n".join([sentence] * copies)
    coverage = measure_html(html, body)
    assert coverage.prose_chars == copies * len(sentence)
    assert coverage.ratio > 0.99, coverage
    assert coverage.captured_chars <= len(body)


def test_candidate_scan_stays_far_below_its_cost_ceiling():
    """후보 탐색 상한은 사실을 정하는 문턱이 아니라 비용 상한이다.

    코퍼스에서 실제로 도는 횟수가 상한에 가까워지면 그때부터는 상한이 **값을
    바꾸기 시작한다** — 그 순간을 여기서 잡는다.
    """
    import lxml.html

    from anchor.normalize.coverage import _MAX_CANDIDATES, _is_free, _skeleton_index

    worst = {}
    for path in sorted(list(GOLDEN.glob("*.html")) + list(STRUCTURE.glob("*.html"))):
        html = path.read_text("utf-8")
        body = to_normalized(path.read_bytes(), "text/html").text
        tree = lxml.html.document_fromstring(
            re.sub(r"^[\s﻿]*<\?xml[^>]*\?>", "", html)
        )
        for tag in ("script", "style", "noscript", "template", "head", "svg", "math"):
            for element in list(tree.iter(tag)):
                element.drop_tree()
        target, _, _ = _skeleton_index(body)
        searched, starts, ends = {}, [], []
        seen = 0
        for element in tree.iter():
            tag = element.tag
            if not isinstance(tag, str) or tag not in coverage_metric._COUNTED:
                continue
            if any(
                isinstance(child.tag, str) and child.tag in coverage_metric._CONTAINER
                for child in element.iterdescendants()
            ):
                continue
            text = re.sub(r"\s+", " ", element.text_content()).strip()
            if len(text) < coverage_metric._MIN_UNIT_CHARS:
                continue
            links = sum(
                len(re.sub(r"\s+", " ", a.text_content()).strip())
                for a in element.iter("a")
            )
            if links / len(text) > coverage_metric._MAX_LINK_DENSITY:
                continue
            unit = coverage_metric._skeleton(text)
            if len(unit) < coverage_metric._PROBE_CHARS:
                continue
            head, tail = unit[:16], unit[-16:]
            scan = searched.get(head, 0)
            attempts = 0
            while attempts < _MAX_CANDIDATES:
                position = target.find(head, scan)
                if position < 0:
                    break
                attempts += 1
                scan = position + 1
                stop = target.find(tail, position, min(position + len(unit), len(target)))
                if stop >= 0 and _is_free(starts, ends, position, stop + 16):
                    searched[head] = scan
                    index = len([s for s in starts if s <= position])
                    starts.insert(index, position)
                    ends.insert(index, stop + 16)
                    break
            seen = max(seen, attempts)
        worst[path.stem] = seen
    highest = max(worst.values())
    assert highest * 2 <= _MAX_CANDIDATES, (
        f"후보 탐색이 비용 상한에 근접했다: {sorted(worst.items(), key=lambda i: -i[1])[:3]}"
    )
