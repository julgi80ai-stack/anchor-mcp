# SPDX-License-Identifier: Apache-2.0
"""경계·방어 경로 커버리지 (SPEC §12 커버리지 목표: anchoring·normalize 95%+)."""

from __future__ import annotations

import pytest
import trafilatura

from anchor.anchoring import matcher
from anchor.anchoring.matcher import ALTERED, MOVED, UNRESOLVED, match_anchor
from anchor.errors import ExtractionFailed, UnsupportedContent
from anchor.normalize import extract

TEXT = "앞부분 본문이다. " * 10 + "여기 인용문이 그대로 있다." + " 뒷부분 본문이다." * 10


# -- matcher -----------------------------------------------------------------


def test_stage3_skipped_when_context_empty():
    """문서 맨 앞 앵커는 prefix가 비어 3단계를 건너뛴다."""
    result = match_anchor(
        "완전히 다른 내용의 문서다. " * 20,
        exact="여기 인용문이 그대로 있다.",
        prefix="",
        suffix=" 뒷부분",
        position_hint=0,
        budget_ms=200,
    )
    assert result.state in (UNRESOLVED, "MISSING", ALTERED)


def test_context_helper_defensive_moved_branch():
    """후보 본문이 완전 일치하는 방어 분기 — 헬퍼 직접 호출로만 도달한다."""
    from anchor.anchoring.budget import Budget

    text = "머리말 " + "PREFIX여기 인용문이 그대로 있다.SUFFIX" + " 꼬리말"
    result = matcher._match_by_context(
        text, "여기 인용문이 그대로 있다.", "PREFIX", "SUFFIX", 4, Budget(200), False
    )
    assert result is not None and result.state == MOVED


def test_context_helper_advances_past_false_prefix():
    """첫 prefix 뒤에 suffix가 없으면 다음 후보로 넘어가야 한다."""
    from anchor.anchoring.budget import Budget

    text = "PREFIX 엉뚱한 내용 " * 2 + "PREFIX변형된 인용문이 여기 있다더라.SUFFIX"
    result = matcher._match_by_context(
        text, "변형된 인용문이 여기 있었다.", "PREFIX", "SUFFIX", 6, Budget(200), False
    )
    assert result is not None and result.state == ALTERED


def test_unresolved_between_stage3_and_stage4(monkeypatch):
    """3단계 이후 예산 소진 지점의 정직한 무응답."""

    class ScriptedBudget:
        def __init__(self, *_args, **_kwargs):
            self.calls = 0

        def exhausted(self):
            self.calls += 1
            return self.calls >= 2  # 1차(2단계 후) 통과, 2차(3단계 후) 소진

        def remaining_seconds(self):
            return 1.0

    monkeypatch.setattr(matcher, "Budget", ScriptedBudget)
    result = match_anchor(
        TEXT,
        exact="문서에 존재하지 않는 인용문이다, 절대로.",
        prefix="존재하지 않는 앞맥락",
        suffix="존재하지 않는 뒷맥락",
        position_hint=0,
        budget_ms=200,
    )
    assert result.state == UNRESOLVED


# -- extract -----------------------------------------------------------------


def test_decode_bytes_fallback_when_charset_undetected(monkeypatch):
    class NoBest:
        def best(self):
            return None

    monkeypatch.setattr(extract, "from_bytes", lambda raw: NoBest())
    assert extract.decode_bytes("가나다".encode("utf-8")) == "가나다"


def test_text_plain_passthrough():
    doc = extract.to_normalized("본문  텍스트다.\n\n\n\n끝.".encode("utf-8"), "text/plain")
    assert doc.text == "본문 텍스트다.\n\n끝."
    assert doc.title is None


def test_unsupported_media_type():
    with pytest.raises(UnsupportedContent):
        extract.to_normalized(b"\x89PNG...", "image/png")


def test_title_fallback_paths(monkeypatch):
    html = "<html><body><p>" + "본문 문장이다. " * 20 + "</p></body></html>"
    monkeypatch.setattr(trafilatura, "extract_metadata", lambda *_: (_ for _ in ()).throw(RuntimeError))

    class Boom:
        def __init__(self, *_):
            raise RuntimeError("no title")

    monkeypatch.setattr(extract, "ReadabilityDocument", Boom)
    doc = extract.to_normalized(html.encode("utf-8"), "text/html")
    assert doc.title is None


def test_fallback_internal_failure_leads_to_extraction_failed(monkeypatch):
    monkeypatch.setattr(trafilatura, "extract", lambda *a, **k: None)
    monkeypatch.setattr(
        extract.markdownify, "markdownify", lambda *_: (_ for _ in ()).throw(RuntimeError)
    )
    with pytest.raises(ExtractionFailed):
        extract.to_normalized(b"<html><body><p>x</p></body></html>", "text/html")


def test_corrupt_pdf_raises_extraction_failed():
    with pytest.raises(ExtractionFailed):
        extract.to_normalized(b"%PDF-1.4 corrupted garbage", "application/pdf")
