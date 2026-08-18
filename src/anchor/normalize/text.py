# SPDX-License-Identifier: Apache-2.0
"""텍스트 정규화 (SPEC §5.3). 모든 해시와 앵커의 기준이 되는 문자열을 만든다.

규칙을 바꾸면 NORM_VERSION을 올려야 한다 — 이 값은 text_hash의 숨은
입력이며, versions.pipeline_version에 기록되어 `renormalized` 판정의
근거가 된다.

정규화의 목적은 둘이고 **서로 반대 방향으로 당긴다**.

  ① 사용자가 **화면에서 복사한 문장**이 저장 본문에서 그대로 발견될 것.
     화면에 보이지 않는 것(조판용 줄바꿈, 서식 기호, 유니코드 공백 변종)은
     지우거나 통일해야 한다. 이것이 NORM_VERSION 2의 동기였다.
  ② **화면에 있는 것을 지우지 말 것.** 2차 감사가 찾아낸 반대편 실패다 —
     ①만 보고 만든 규칙이 산문의 `<updated>`를 삼키고 `2*3*4`를 `234`로
     바꿔, 저장된 증거가 원문이 아니게 됐다. 근거 무결성 계층이 근거를
     훼손하면 그 위의 모든 판정이 함께 틀린다.

NORM_VERSION 3의 구조 원칙: **줄과 블록을 먼저 인식하고, 지우는 규칙은
산문 줄 안에서만 적용한다.** 2에서는 전역 치환이 줄·울타리 인식보다 앞서
있었고, 그것이 군집 1 결함 22건의 공통 뿌리였다.
"""

from __future__ import annotations

import re
import unicodedata

NORM_VERSION = "3"

# 화면에서 줄바꿈으로 렌더되는 문자들. 줄 구분자로 통일하지 않으면 본문 안에
# 남아, 그 지점을 넘겨 복사한 인용문이 영영 발견되지 않는다 (D-065).
_LINE_BREAK_CHARS = "\r  "
_LINE_BREAK_RE = re.compile(f"\r\n|[{_LINE_BREAK_CHARS}]")

# 화면에서 공백 하나로 보이는 문자들 — 프랑스어 정서법의 NBSP, 조판용 얇은
# 공백, CJK 전각 공백 등. 개수·종류가 달라도 같은 본문이어야 한다.
_UNICODE_SPACES = "               　"
_CHAR_TRANSLATION = {ord(char): " " for char in _UNICODE_SPACES}
# 폭이 없고 **표시에도 관여하지 않는** 문자만 지운다. ZWJ(U+200D)는 이모지
# 시퀀스를, ZWNJ(U+200C)는 페르시아어 정서법을, LRM/RLM은 문단 방향을
# 만든다 — 전부 글자다 (D-068).
_CHAR_TRANSLATION.update({ord(char): None for char in "​⁠﻿­"})

# 추출기가 남긴 서식 전용 인라인 태그만 지운다 (D-055).
#
# 태그 **모양**을 전부 지우면 산문에 적힌 요소명(`<updated>`·`<feed>`)과
# 제네릭 타입(`List<Entry>`)까지 사라진다. 그러면 원문이 `<updated>`에서
# `<published>`로 개정돼도 같은 문자열로 붕괴해 verify가 INTACT를 보고한다.
# D-051이 실제로 겨냥한 것은 `<sup>12</sup>` 각주였으므로 거기로 좁힌다.
_INLINE_TAGS = "sup|sub|wbr|span|em|strong|b|i|u|s|small|mark"
_INLINE_TAG_RE = re.compile(rf"</?(?:{_INLINE_TAGS})(?:\s[^<>]*)?/?>", re.IGNORECASE)
# `<br>`는 줄바꿈이므로 공백으로 바꾼다.
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)

# 마크다운 강조. **어절 내부는 건드리지 않는다** — `2*3*4`를 `234`로 바꾸면
# 원문에 없던 수치를 만들어내는 것이다 (D-057). 여는 기호 앞과 닫는 기호
# 뒤가 낱말 문자면 강조가 아니라 산문·수식으로 본다.
# 경계 조건은 **비대칭**이다. 여는 기호 앞이 낱말 문자면 강조가 아니라
# 산문·수식이다(`2*3*4`). 반면 닫는 기호 **뒤**는 낱말 문자여도 된다 —
# 한국어·일본어는 조사가 강조 바로 뒤에 붙는다(`**굵게**도`).
_STRONG_RE = re.compile(r"(?<![\w*])\*\*(?=\S)([^*]+?)(?<=\S)\*\*(?!\*)")
_EMPHASIS_RE = re.compile(r"(?<![\w*])\*(?=\S)([^*]+?)(?<=\S)\*(?!\*)")
_MARKUP_PASSES = 4  # 제거가 새 매치를 만들 수 있어 고정점까지 돈다 (D-059)

_INLINE_CODE_RE = re.compile(r"`[^`]*`")
_HORIZONTAL_WS = re.compile(r"[ \t]+")
_EXCESS_NEWLINES = re.compile(r"\n{3,}")

# 구조를 여는 줄. 이런 줄은 앞줄과 이어붙이지 않는다.
#
# 경계가 헐거우면 산문을 구조로 오인해 접기를 막는다 (D-063): 번호 목록을
# `\d+[.)]`로 잡으면 한국어 날짜 표기 `2026. 3. 15.`가 걸리고, 인용을
# `>\s?`로 잡으면 `>보다 큰 값`이 걸린다.
_STRUCTURAL_RE = re.compile(
    r"^(?:"
    r"#{1,6}\s"          # 제목
    r"|[-*+]\s"          # 목록
    r"|\d{1,2}[.)]\s"    # 번호 목록 (2자리까지 — 연도·큰 수는 산문이다)
    r"|>\s"              # 인용
    r"|```|~~~"          # 코드 울타리
    r"|\[\^[^\]]+\]:"    # 각주 정의
    r"|\[[^\]]+\]\s*$"   # INI 절 머리
    r")"
)
# 계속 줄을 흡수할 수 있는 구조는 목록 항목뿐이다. 제목·표·각주 정의가 뒤
# 줄을 흡수하면 접합 결과가 더는 구조로 보이지 않아, 다음 호출에서 또 접히며
# 결과가 달라진다(멱등성 위반, D-059).
_LIST_ITEM_RE = re.compile(r"^(?:[-*+]\s|\d{1,2}[.)]\s)")
# setext 밑줄은 제목 길이에 맞춰 짧을 수 있다(`제목` 두 글자 → `==`).
_SETEXT_RE = re.compile(r"^(?:=+|-{2,})\s*$")   # setext 밑줄 / YAML 구분선
_YAML_FENCE_RE = re.compile(r"^(?:---|\.\.\.)\s*$")
_TABLE_ROW_RE = re.compile(r"^\|.*\|\s*$")

# 단어를 공백으로 나누지 않는 문자 체계 — 한자·가나·CJK 문장부호.
# **한글은 제외한다**: 한국어는 어절을 공백으로 띄운다.
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
_HANGUL_RANGES = ((0x1100, 0x11FF), (0x3130, 0x318F), (0xA960, 0xA97F), (0xAC00, 0xD7FF))

# 일본어·중국어 한 글자가 담는 정보량이 라틴 문자의 대략 몇 배인가.
WORDLESS_INFORMATION_RATIO = 2.5

# 브라우저는 CJK 문자 사이의 줄바꿈을 공백 없이 렌더한다(CSS Text 3의 segment
# break 규칙). 그런데 추출기는 그 줄바꿈을 공백으로 바꿔 내보내므로, 화면에
# 붙어 보이던 문장이 저장 본문에서는 공백을 사이에 두게 되어 복사 인용이
# 실패한다 (D-061). 문서가 일본어·중국어일 때만 되돌린다.
_WORDLESS_CLASS = "[" + "".join(
    f"\\U{start:08x}-\\U{end:08x}" for start, end in _WORDLESS_RANGES
) + "]"
_WORDLESS_SPACE_RE = re.compile(f"(?<={_WORDLESS_CLASS}) +(?={_WORDLESS_CLASS})")


def _in_ranges(char: str, ranges: tuple[tuple[int, int], ...]) -> bool:
    code = ord(char)
    return any(start <= code <= end for start, end in ranges)


def is_wordless_script(char: str) -> bool:
    """공백으로 단어를 나누지 않는 문자인가 (일본어·중국어). 한글은 아니다."""
    return _in_ranges(char, _WORDLESS_RANGES)


def is_hangul(char: str) -> bool:
    return _in_ranges(char, _HANGUL_RANGES)


def wordless_density(text: str) -> float:
    """공백으로 단어를 나누지 않는 문자(한자·가나)의 비율. 한글은 제외."""
    letters = [char for char in text if not char.isspace()]
    if not letters:
        return 0.0
    return sum(1 for char in letters if is_wordless_script(char)) / len(letters)


def is_wordless_text(text: str) -> bool:
    return wordless_density(text) >= 0.5


# -- 인라인 마크업 ------------------------------------------------------------


def _strip_inline_markup(line: str) -> str:
    """한 줄 안에서만 서식 기호를 걷어낸다. 인라인 코드는 건드리지 않는다."""
    pieces = []
    position = 0
    for match in _INLINE_CODE_RE.finditer(line):
        pieces.append(_strip_markup_fragment(line[position : match.start()]))
        pieces.append(match.group(0))  # `code` 는 그대로
        position = match.end()
    pieces.append(_strip_markup_fragment(line[position:]))
    return "".join(pieces)


def _strip_markup_fragment(fragment: str) -> str:
    for _ in range(_MARKUP_PASSES):
        updated = _BR_RE.sub(" ", fragment)
        updated = _INLINE_TAG_RE.sub("", updated)
        updated = _STRONG_RE.sub(r"\1", updated)
        updated = _EMPHASIS_RE.sub(r"\1", updated)
        if updated == fragment:
            return fragment
        fragment = updated
    return fragment


# -- 블록 분류 ----------------------------------------------------------------

_CODE = "code"
_STRUCTURAL = "structural"
_PROSE = "prose"
_BLANK = "blank"


def _classify(lines: list[str]) -> list[tuple[str, str]]:
    """줄마다 종류와 정규화된 본문을 함께 낸다.

    코드 울타리 안쪽과 들여쓴 코드는 무엇도 건드리지 않는다 (D-056).

    분류는 **공백을 정규화한 뒤의 줄**로 한다. 원본 줄로 분류하면 `rstrip()`이
    구조 표지의 뒤 공백을 없애(`> ` → `>`) 같은 입력이 다음 호출에서 산문으로
    분류되고, 결과가 달라진다(멱등성 위반, D-059).
    """
    entries: list[tuple[str, str]] = []
    in_fence = False
    for line in lines:
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            entries.append((_CODE, line.rstrip()))
            continue
        if in_fence or line[:4] == "    " or line.startswith("\t"):
            entries.append((_CODE, line.rstrip()))
            continue
        normalized = _HORIZONTAL_WS.sub(" ", line).rstrip()
        if not normalized:
            entries.append((_BLANK, ""))
        elif (
            _STRUCTURAL_RE.match(normalized)
            or _SETEXT_RE.match(normalized)
            or _YAML_FENCE_RE.match(normalized)
            or _TABLE_ROW_RE.match(normalized)
        ):
            entries.append((_STRUCTURAL, normalized))
        else:
            entries.append((_PROSE, normalized))
    return entries


_RECORD_KEY_RE = re.compile(r"^\S+:\s")
_ASSIGNMENT_RE = re.compile(r"^\S+=")


def _looks_like_records(run: list[str]) -> bool:
    """같은 모양이 반복되는 줄들인가 (로그·CSV·표·설정).

    조판 줄바꿈과 달리 이런 줄은 각자가 완결된 항목이므로 이어붙이면
    인접하지 않던 값이 이웃이 되어 **없던 문장이 만들어진다** (D-060).
    조판된 산문은 이런 규칙성이 없다.
    """
    if len(run) < 2:
        return False
    if all(line[:1].isdigit() for line in run):
        return True
    if all(_RECORD_KEY_RE.match(line) for line in run):
        return True
    if all(_ASSIGNMENT_RE.match(line) for line in run):
        return True
    for delimiter in (",", "|", "\t"):
        counts = {line.count(delimiter) for line in run}
        if len(counts) == 1 and counts.pop() >= 1:
            return True
    return False


# 줄 끝 분철과 잘린 URL — 공백을 넣으면 낱말과 주소가 깨진다 (D-067).
# 앞에 낱말 문자가 있어야 한다. 그러지 않으면 목록 표지 `- `가 걸려 다음 줄을
# 붙여버리고, 그 결과가 더는 목록으로 보이지 않아 멱등성이 깨진다.
_JOIN_WITHOUT_SPACE_RE = re.compile(r"[^\W\d_]-$|\S/$")


def _fold_run(run: list[str], joiner: str) -> str:
    """조판 줄바꿈으로 쪼개진 한 문단을 되붙인다."""
    folded = run[0]
    for line in run[1:]:
        if _JOIN_WITHOUT_SPACE_RE.search(folded):
            folded += line
        else:
            folded += joiner + line
    return folded


def _fold(entries: list[tuple[str, str]], joiner: str) -> list[tuple[str, str]]:
    output: list[tuple[str, str]] = []
    index = 0
    while index < len(entries):
        kind, line = entries[index]
        if kind in (_CODE, _BLANK):
            output.append((kind, line))
            index += 1
            continue

        # 구조 줄 중 **목록 항목만** 뒤따르는 산문 줄을 흡수한다. 목록 항목이
        # 여러 줄에 걸치는 것은 흔하고(D-064), 나머지 구조는 그 자체로 완결이다.
        run = [line.strip()]
        index += 1
        if kind == _PROSE or _LIST_ITEM_RE.match(line.strip()):
            while index < len(entries) and entries[index][0] == _PROSE:
                run.append(entries[index][1].strip())
                index += 1

        if len(run) > 1 and _looks_like_records(run if kind == _PROSE else run[1:]):
            output.extend((kind if index_offset == 0 else _PROSE, item)
                          for index_offset, item in enumerate(run))
        else:
            output.append((kind, _fold_run(run, joiner)))
    return output


def normalize_text(text: str) -> str:
    text = _LINE_BREAK_RE.sub("\n", text)
    # 바깥 공백을 **분류 전에** 걷는다. 마지막에 걷으면 첫 줄의 들여쓰기가
    # 그때 사라져 다음 호출에서 분류가 달라진다(멱등성 위반, D-059).
    text = text.translate(_CHAR_TRANSLATION).strip()

    entries = _classify(text.split("\n"))

    # 접합 시 공백을 넣을지는 **문서 전체의 문자 체계**로 정한다. 경계 문자
    # 하나만 보면 일본어 문서의 라틴 낱말 뒤에 없던 공백이 들어가고, 한국어
    # 문서의 한자 경계에서 없던 붙임이 생긴다 (D-061).
    joiner = "" if is_wordless_text(text) else " "
    folded = _fold(entries, joiner)

    # 서식은 **접합이 끝난 뒤에** 걷는다. 접합 전에 걷으면 두 줄이 합쳐지며
    # 새 매치가 생겨(`*b` + `漢*` → `*b 漢*`) 다음 호출의 결과가 달라진다.
    lines = [
        line if kind == _CODE
        else _HORIZONTAL_WS.sub(" ", _strip_inline_markup(line)).strip()
        for kind, line in folded
    ]
    if not joiner:  # 일본어·중국어 문서
        lines = [
            line if kind == _CODE else _WORDLESS_SPACE_RE.sub("", line)
            for (kind, _), line in zip(folded, lines)
        ]

    # 문자 제거가 끝난 **뒤에** NFC를 건다 — 폭 없는 문자를 사이에서 지우면
    # 기반 문자와 결합 문자가 비로소 인접하는데, 앞에서 걸면 그 조합이
    # 결합형으로 남아 화면 복사문(NFC)과 어긋난다 (D-058).
    result = unicodedata.normalize("NFC", "\n".join(lines))
    return _EXCESS_NEWLINES.sub("\n\n", result).strip()


_HYPHEN_BREAK = re.compile(r"(?<=[^\W\d_])-\n(?=[a-zÀ-ɏ])")


def dehyphenate(text: str) -> str:
    """줄 끝 분철을 잇는다 — 조판 PDF 전용 (SPEC §5.3의 PDF 경로).

    `method-\\nological` → `methodological`. 줄 끝에 놓인 정당한 하이픈
    (`well-known`)까지 붙여버릴 수 있으나, PDF에서는 분철이 압도적으로
    흔하다. HTML에는 적용하지 않는다.
    """
    return _HYPHEN_BREAK.sub("", text)
