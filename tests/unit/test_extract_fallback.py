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
