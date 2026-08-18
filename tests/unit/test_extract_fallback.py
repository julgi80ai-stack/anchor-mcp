# SPDX-License-Identifier: Apache-2.0
"""readability-lxml 폴백 (SPEC §5.3): trafilatura 실패 시 두 번째 추출기."""

from __future__ import annotations

import pytest
import trafilatura

from anchor.errors import ExtractionFailed
from anchor.normalize import extract

HTML = """<!DOCTYPE html><html><body><div id="content">
<p>첫 문단이다. 폴백 추출기가 이 본문을 찾아야 한다. 문장을 몇 개 더 붙여
길이를 확보한다. 본문 추출기는 짧은 문서를 종종 거부하기 때문이다.</p>
<p>둘째 문단이다. 폴백 경로에서도 마크다운 변환과 정규화는 동일하게
적용되어야 한다.</p>
</div></body></html>"""


def test_fallback_used_when_trafilatura_returns_none(monkeypatch):
    monkeypatch.setattr(trafilatura, "extract", lambda *a, **k: None)
    doc = extract.to_normalized(HTML.encode("utf-8"), "text/html")
    assert "폴백 추출기가 이 본문을 찾아야 한다" in doc.text
    assert doc.pipeline_version == extract.READABILITY_PIPELINE_VERSION
    assert doc.pipeline_version.startswith("readability-lxml/")


def test_extraction_failed_when_both_extractors_fail(monkeypatch):
    monkeypatch.setattr(trafilatura, "extract", lambda *a, **k: None)
    monkeypatch.setattr(extract, "_readability_fallback", lambda html: None)
    with pytest.raises(ExtractionFailed):
        extract.to_normalized(HTML.encode("utf-8"), "text/html")


def test_primary_path_keeps_trafilatura_pipeline_version():
    doc = extract.to_normalized(HTML.encode("utf-8"), "text/html")
    assert doc.pipeline_version == extract.PIPELINE_VERSION


# -- D-069: 중복 Content-Type 헤더 -------------------------------------------


def test_duplicate_content_type_header_is_accepted():
    """서버가 헤더를 두 번 보내면 httpx가 `, `로 이어 붙인다 — 정상 HTML이다."""
    doc = extract.to_normalized(HTML.encode("utf-8"), "text/html, text/html")
    assert "폴백 추출기가 이 본문을 찾아야 한다" in doc.text or doc.text


def test_duplicate_content_type_with_charset_is_accepted():
    doc = extract.to_normalized(
        HTML.encode("utf-8"), "text/html; charset=utf-8, text/html; charset=utf-8"
    )
    assert doc.text


# -- D-071: 정규화 후 빈 본문 -------------------------------------------------


def test_extraction_failed_when_normalization_empties_the_body(monkeypatch):
    """추출은 성공했는데 정규화가 비우는 경우 — 빈 판본을 저장하면 안 된다.

    `char_count: 0` 버전이 `changed`로 저장되면 그 문서의 앵커가 전부 MISSING
    ("인용 철회 또는 대체 검토")으로 뒤집힌다. 추출 실패가 인용 무효로 둔갑한다.
    """
    monkeypatch.setattr(trafilatura, "extract", lambda *a, **k: "<span></span><sup></sup>")
    monkeypatch.setattr(extract, "_readability_fallback", lambda html: None)
    with pytest.raises(ExtractionFailed):
        extract.to_normalized(HTML.encode("utf-8"), "text/html")


# -- D-072: 짧은 페이지에서 블록 구분이 무너진다 -----------------------------


SHORT_PAGE = """<!DOCTYPE html><html><body><article>
<h1>Service status</h1>
<h2>Degraded performance</h2>
<p>Between 09:12 and 10:47 UTC some requests to the reporting endpoint
returned HTTP 503. The cause was an expired certificate on one node.</p>
</article></body></html>"""


def test_headings_do_not_glue_onto_the_following_paragraph(monkeypatch):
    """제목이 뒤 문단에 낱말째 붙으면 화면 복사 인용이 성립하지 않는다."""
    monkeypatch.setattr(
        trafilatura,
        "extract",
        lambda *a, **k: (
            "Service statusDegraded performanceBetween 09:12 and 10:47 UTC some "
            "requests to the reporting endpoint returned HTTP 503."
        ),
    )
    doc = extract.to_normalized(SHORT_PAGE.encode("utf-8"), "text/html")
    assert "statusDegraded" not in doc.text
    assert "performanceBetween" not in doc.text


def test_plain_text_that_normalizes_to_empty_is_rejected():
    """빈 본문 가드는 HTML 분기에만 있으면 안 된다 (D-071의 형제 경로).

    `.txt`/`.md` 원문이 한 번 잘린 응답이나 빈 CDN 페이지를 돌려주면, 빈 판본이
    저장되고 그 문서의 앵커가 전부 MISSING("인용 철회 또는 대체 검토")으로
    뒤집힌다 — 추출 실패가 인용 무효로 둔갑한다.
    """
    with pytest.raises(ExtractionFailed):
        extract.to_normalized("\n\n   \n\t\n   \n".encode("utf-8"), "text/plain")
