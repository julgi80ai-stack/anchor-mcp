# SPDX-License-Identifier: Apache-2.0
"""텍스트 정규화 (SPEC §5.3). 모든 해시와 앵커의 기준이 되는 문자열을 만든다.

규칙을 바꾸면 NORM_VERSION을 올려야 한다 — 이 값은 text_hash의 숨은
입력이며, versions.pipeline_version에 기록되어 `renormalized` 판정의
근거가 된다.

정규화의 목적은 둘이다. ① 바이트는 달라도 사람이 읽는 본문이 같으면 같은
해시를 내는 것 ② 사용자가 **화면에서 복사한 문장**이 저장된 본문에서
그대로 발견되는 것. ②는 실사용의 유일한 진입 경로이므로, 화면에 보이지
않는 것(마크다운 서식 기호, 추출기가 남긴 태그, 조판용 줄바꿈, 유니코드
공백 변종)은 전부 지우거나 통일한다.
"""

from __future__ import annotations

import re
import unicodedata

NORM_VERSION = "2"

# 화면에서 공백 하나로 보이는 문자들. 개수·종류가 달라도 같은 본문이어야
# 한다 — 프랑스어 정서법의 NBSP, 조판용 얇은 공백, CJK 전각 공백 등.
_UNICODE_SPACES = "               　"
_SPACE_TRANSLATION = {ord(char): " " for char in _UNICODE_SPACES}
# 폭이 없어 화면에 나타나지 않는 문자들 — 복사본에는 섞이고 원문에는 없거나
# 그 반대인 경우가 흔하므로 양쪽 모두에서 지운다.
_ZERO_WIDTH = "​‌‍⁠﻿­‎‏"
_SPACE_TRANSLATION.update({ord(char): None for char in _ZERO_WIDTH})

# 추출기가 남긴 원시 HTML (각주 <sup>12</sup> 등). 산문의 부등호와 헷갈리지
# 않도록 태그 모양일 때만 지운다.
_HTML_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9]*(?:\s[^<>]*)?/?>")

# 마크다운 강조 기호. 화면에는 굵기·기울기로 보일 뿐 글자가 아니다.
# `_`는 snake_case를 훼손하므로 건드리지 않는다.
_STRONG = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", re.DOTALL)
_EMPHASIS = re.compile(r"(?<!\*)\*(?=\S)([^*]+?)(?<=\S)\*(?!\*)")

_HORIZONTAL_WS = re.compile(r"[ \t]+")
_EXCESS_NEWLINES = re.compile(r"\n{3,}")

# 줄이 구조를 여는 신호. 이런 줄은 앞줄과 이어붙이지 않는다.
_STRUCTURAL = re.compile(r"^(?:#{1,6}\s|[-*+]\s|>\s?|\||\d+[.)]\s|```|~~~)")

# 단어를 공백으로 나누지 않는 문자 체계 — 한자·가나·CJK 문장부호.
# **한글은 제외한다**: 한국어는 일본어·중국어와 달리 어절을 공백으로 띄우므로,
# 줄바꿈을 공백 없이 이으면 없던 붙임이 생긴다.
_WORDLESS_RANGES = (
    (0x2E80, 0x2FFF),   # 한자 부수
    (0x3000, 0x303F),   # CJK 문장부호
    (0x3040, 0x30FF),   # 히라가나·가타카나
    (0x3400, 0x4DBF),   # 한자 확장 A
    (0x4E00, 0x9FFF),   # 한자 기본
    (0xF900, 0xFAFF),   # 한자 호환
    (0xFF00, 0xFF60),   # 전각 형태
    (0x20000, 0x2FA1F),  # 한자 확장 B 이상
)

# 한글 (참고용 — 공백을 쓰므로 잇기 규칙에서는 라틴과 같이 취급한다).
_HANGUL_RANGES = ((0x1100, 0x11FF), (0x3130, 0x318F), (0xA960, 0xA97F), (0xAC00, 0xD7FF))


def _in_ranges(char: str, ranges: tuple[tuple[int, int], ...]) -> bool:
    code = ord(char)
    return any(start <= code <= end for start, end in ranges)


def is_wordless_script(char: str) -> bool:
    """공백으로 단어를 나누지 않는 문자인가 (일본어·중국어). 한글은 아니다."""
    return _in_ranges(char, _WORDLESS_RANGES)


def is_hangul(char: str) -> bool:
    return _in_ranges(char, _HANGUL_RANGES)


# 일본어·중국어 한 글자가 담는 정보량이 라틴 문자의 대략 몇 배인가.
# 글자 수를 기준으로 잡힌 임계·비율을 문자 체계에 맞게 환산하는 데 쓴다.
WORDLESS_INFORMATION_RATIO = 2.5


def wordless_density(text: str) -> float:
    """공백으로 단어를 나누지 않는 문자(한자·가나)의 비율. 한글은 제외."""
    letters = [char for char in text if not char.isspace()]
    if not letters:
        return 0.0
    return sum(1 for char in letters if is_wordless_script(char)) / len(letters)


def is_wordless_text(text: str) -> bool:
    return wordless_density(text) >= 0.5


def _strip_inline_markup(text: str) -> str:
    text = _HTML_TAG.sub("", text)
    text = _STRONG.sub(r"\1", text)
    return _EMPHASIS.sub(r"\1", text)


def _unwrap_prose(lines: list[str]) -> list[str]:
    """조판용 줄바꿈을 접는다 (D-052).

    HTML 원문이 문단 안에서 줄을 바꾸거나 PDF가 고정 열 폭으로 조판되면
    한 문장이 여러 줄로 쪼개진다. 화면에서는 한 줄로 보이므로 복사한
    인용문과 어긋난다. 구조를 여는 줄(제목·목록·표·인용·코드 울타리)과
    코드 블록 안쪽은 건드리지 않는다.
    """
    output: list[str] = []
    in_code_fence = False
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            in_code_fence = not in_code_fence
            output.append(line)
            continue
        if (
            in_code_fence
            or not output
            or not line.strip()
            or not output[-1].strip()
            or _STRUCTURAL.match(stripped)
            or _STRUCTURAL.match(output[-1].lstrip())
        ):
            output.append(line)
            continue
        previous = output[-1]
        # 일본어·중국어는 줄바꿈 자리에 공백이 없다(브라우저도 그렇게 렌더한다).
        # 한국어·라틴 문자는 줄바꿈이 공백을 대신하므로 공백을 넣어 잇는다.
        joiner = (
            ""
            if is_wordless_script(previous[-1]) and is_wordless_script(stripped[0])
            else " "
        )
        output[-1] = previous + joiner + stripped
    return output


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = text.translate(_SPACE_TRANSLATION)
    text = _strip_inline_markup(text)
    lines = [_HORIZONTAL_WS.sub(" ", line).rstrip() for line in text.split("\n")]
    text = "\n".join(_unwrap_prose(lines))
    text = _EXCESS_NEWLINES.sub("\n\n", text)
    return text.strip()


_HYPHEN_BREAK = re.compile(r"(?<=[^\W\d_])-\n(?=[a-zÀ-ɏ])")


def dehyphenate(text: str) -> str:
    """줄 끝 분철을 잇는다 — 조판 PDF 전용 (SPEC §5.3의 PDF 경로).

    `method-\\nological` → `methodological`. 줄 끝에 놓인 정당한 하이픈
    (`well-known`)까지 붙여버릴 수 있으나, PDF에서는 분철이 압도적으로
    흔하다. HTML에는 적용하지 않는다.
    """
    return _HYPHEN_BREAK.sub("", text)
