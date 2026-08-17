# SPDX-License-Identifier: Apache-2.0
"""blake3 래퍼. 해시 문자열은 `b3:<hex>` 형식이다 (SPEC §7.1 예시)."""

from __future__ import annotations

import blake3


def hash_bytes(data: bytes) -> str:
    return "b3:" + blake3.blake3(data).hexdigest()


def hash_text(text: str) -> str:
    return hash_bytes(text.encode("utf-8"))
