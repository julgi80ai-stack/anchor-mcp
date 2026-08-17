# SPDX-License-Identifier: Apache-2.0
"""버전 간 통합 diff (SPEC §7.4)."""

from __future__ import annotations

import difflib

from anchor.models import Version


def unified_diff(
    from_version: Version,
    from_text: str,
    to_version: Version,
    to_text: str,
    *,
    context_lines: int = 2,
) -> str:
    r"""통합 diff 직렬화 (SPEC §7.4).

    정규화된 본문은 항상 개행 없이 끝나므로(`strip()`), 마지막 줄이 바뀌면
    `-`행과 `+`행이 한 줄로 붙어 어떤 diff 파서도 읽지 못했다 (D-025).
    줄 단위로 직접 조립하고 `\ No newline at end of file`을 명시한다.
    """
    if context_lines < 0:
        raise ValueError(
            f"context_lines must be >= 0, got {context_lines} — "
            "context_lines는 0 이상이어야 합니다"
        )

    from_lines = from_text.split("\n")
    to_lines = to_text.split("\n")
    rendered = list(
        difflib.unified_diff(
            from_lines,
            to_lines,
            fromfile=f"{from_version.id} ({from_version.captured_at})",
            tofile=f"{to_version.id} ({to_version.captured_at})",
            n=context_lines,
            lineterm="",
        )
    )
    if not rendered:
        return ""

    output: list[str] = []
    for index, line in enumerate(rendered):
        output.append(line)
        # 본문 줄(마지막 것)에는 개행이 없다는 사실을 표기한다.
        is_body = line[:1] in (" ", "-", "+") and not line.startswith(("---", "+++"))
        if is_body and _is_final_line(line, from_lines, to_lines, rendered, index):
            output.append("\\ No newline at end of file")
    return "\n".join(output) + "\n"


def _is_final_line(
    line: str, from_lines: list[str], to_lines: list[str], rendered: list[str], index: int
) -> bool:
    """이 diff 줄이 원본/수정본의 마지막 줄을 나타내는가."""
    payload = line[1:]
    marker = line[0]
    if marker in (" ", "-") and payload == from_lines[-1]:
        remaining = rendered[index + 1 :]
        if not any(item[:1] in (" ", "-") for item in remaining):
            return True
    if marker in (" ", "+") and payload == to_lines[-1]:
        remaining = rendered[index + 1 :]
        if not any(item[:1] in (" ", "+") for item in remaining):
            return True
    return False
