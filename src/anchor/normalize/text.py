# SPDX-License-Identifier: Apache-2.0
"""텍스트 정규화 (SPEC §5.3). 모든 해시와 앵커의 기준이 되는 문자열을 만든다.

규칙을 바꾸면 NORM_VERSION을 올려야 한다 — 이 값은 text_hash의 숨은
입력이며, versions.pipeline_version에 기록되어 `renormalized` 판정의
근거가 된다.
"""

from __future__ import annotations

import re
import unicodedata

NORM_VERSION = "1"

_HORIZONTAL_WS = re.compile(r"[ \t]+")
_EXCESS_NEWLINES = re.compile(r"\n{3,}")


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    lines = (_HORIZONTAL_WS.sub(" ", line).rstrip() for line in text.split("\n"))
    text = "\n".join(lines)
    text = _EXCESS_NEWLINES.sub("\n\n", text)
    return text.strip()
