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
    lines = difflib.unified_diff(
        from_text.splitlines(keepends=True),
        to_text.splitlines(keepends=True),
        fromfile=f"{from_version.id} ({from_version.captured_at})",
        tofile=f"{to_version.id} ({to_version.captured_at})",
        n=context_lines,
    )
    return "".join(lines)
