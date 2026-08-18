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
    assert len(CASES) >= 36
    korean = [name for name in CASES if name.startswith("ko-")]
    assert len(korean) >= 10  # 최소 10건은 한국어 (SPEC §12.1)


def test_golden_corpus_exercises_the_normalization_rules():
    """코퍼스가 정규화 규칙을 실제로 밟는지 확인한다 (D-076).

    2차 감사에서 골든 30건은 NORM_VERSION 2가 도입한 규칙을 **하나도** 시험하지
    않았다 — `<pre>`·표·문단 내 줄바꿈·CJK·결합 문자·산문 속 별표가 전부 0건.
    그래서 정규화가 본문을 삭제하고 있는데도 30건이 전부 통과했다. 커버리지가
    다시 0으로 내려가지 못하도록 여기서 고정한다.
    """
    sources = {stem: (GOLDEN_DIR / f"{stem}.html").read_text("utf-8") for stem in CASES}
    bodies = {stem: (GOLDEN_DIR / f"{stem}.expected.md").read_text("utf-8") for stem in CASES}

    def any_body(predicate) -> bool:
        return any(predicate(text) for text in bodies.values())

    def any_source(predicate) -> bool:
        return any(predicate(text) for text in sources.values())

    checks = {
        "코드 블록": lambda body: "```" in body,
        "코드 블록 안 들여쓰기": lambda body: "\n    " in body,
        "표": lambda body: "\n| " in body,
        "목록": lambda body: "\n- " in body,
        "산문 속 별표": lambda body: "2*3*4" in body,
        "산문 속 꺾쇠": lambda body: "List<String>" in body,
        "인라인 코드": lambda body: "`" in body,
        "한자·가나": lambda body: any(
            "\u3040" <= ch <= "\u30ff" or "\u4e00" <= ch <= "\u9fff" for ch in body
        ),
        "결합 문자 합성형": lambda body: "café" in body,
        "위첨자 각주 제거": lambda body: "각주12" in body,
    }
    missing = [name for name, predicate in checks.items() if not any_body(predicate)]
    assert not missing, f"골든이 밟지 않는 구조: {missing}"

    # 아래는 **코퍼스가 그 조건을 담고 있는지**만 본다. 파이프라인이 그것을
    # 옳게 처리하는지는 증명하지 않는다 — 원본만 보는 검사를 "커버됨"으로
    # 세면, 규칙을 정반대로 처리해도 커버리지 수치가 올라간다. 처리 결과의
    # 검증은 위 `checks`(본문 관찰)와 `test_normalize_rules.py`가 맡는다.
    source_checks = {
        "문단 안 줄바꿈": lambda html: "\n" in html.split("<p>", 1)[-1].split("</p>", 1)[0],
        "유니코드 공백": lambda html: any(ch in html for ch in "\u00a0\u202f\u2009"),
        "폭 없는 문자": lambda html: any(ch in html for ch in "\u200b\u00ad"),
        "위첨자 각주": lambda html: "<sup>" in html,
    }
    missing_sources = [name for name, predicate in source_checks.items() if not any_source(predicate)]
    assert not missing_sources, f"골든 원본이 담지 않는 조건: {missing_sources}"


def test_extractor_drops_zero_width_joiners(tmp_path):
    """추출기가 ZWJ를 지운다는 **사실**을 고정한다 (D-173).

    정규화는 ZWJ를 보존하지만(`test_normalize_rules`), trafilatura가 그 앞에서
    지워 버려 저장 본문의 이모지가 화면과 달라진다. 골든 `en-31`의 기대 본문이
    이 손실을 담고 있는데, 그것을 "정답"으로 오해하지 않도록 손실 지점을
    명시해 둔다. 추출기가 고쳐지면 이 테스트가 먼저 빨개진다.
    """
    source = (GOLDEN_DIR / "en-31.html").read_text("utf-8")
    body = (GOLDEN_DIR / "en-31.expected.md").read_text("utf-8")
    assert "\u200d" in source, "픽스처가 ZWJ를 담고 있어야 한다"
    assert "\u200d" not in body, "추출기 동작이 바뀌었다 — D-173을 재평가할 것"


@pytest.mark.parametrize("stem", CASES)
def test_extraction_matches_golden(stem):
    html = (GOLDEN_DIR / f"{stem}.html").read_bytes()
    expected = (GOLDEN_DIR / f"{stem}.expected.md").read_text("utf-8")
    doc = to_normalized(html, "text/html; charset=utf-8")
    assert doc.text == expected
    # 노이즈(nav/footer/script)는 본문에 들어오면 안 된다.
    assert "abBucket" not in doc.text
    assert "Copyright 2026" not in doc.text
