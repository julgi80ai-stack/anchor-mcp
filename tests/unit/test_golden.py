# SPDX-License-Identifier: Apache-2.0
"""골든 테스트 (SPEC §12): 실제 HTML 스냅샷 30건 → 기대 본문.

픽스처는 본 프로젝트를 위해 직접 저작한 A등급 문서(한국어 10 + 영어 20,
SPEC §12.1)다. 추출기(trafilatura) 업그레이드로 산출이 달라지면 여기서
잡힌다 — 달라진 출력이 정당하면 기대 파일을 재생성하고 NORM/파이프라인
버전 정책(§5.3)을 함께 검토한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from anchor.normalize.extract import to_normalized

GOLDEN_DIR = Path(__file__).parent.parent / "fixtures" / "golden"
CASES = sorted(path.stem for path in GOLDEN_DIR.glob("*.html"))


def test_golden_corpus_shape():
    assert len(CASES) == 30
    korean = [name for name in CASES if name.startswith("ko-")]
    assert len(korean) >= 10  # 최소 10건은 한국어 (SPEC §12.1)


@pytest.mark.parametrize("stem", CASES)
def test_extraction_matches_golden(stem):
    html = (GOLDEN_DIR / f"{stem}.html").read_bytes()
    expected = (GOLDEN_DIR / f"{stem}.expected.md").read_text("utf-8")
    doc = to_normalized(html, "text/html; charset=utf-8")
    assert doc.text == expected
    # 노이즈(nav/footer/script)는 본문에 들어오면 안 된다.
    assert "abBucket" not in doc.text
    assert "Copyright 2026" not in doc.text
