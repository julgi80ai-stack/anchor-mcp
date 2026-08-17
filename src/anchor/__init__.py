# SPDX-License-Identifier: Apache-2.0
"""Anchor — AI가 사용한 웹 근거의 시간적·출처적 무결성 계층.

Memento(RFC 7089)의 로컬 클라이언트. v0.1은 fetch + 해시 + 변경 감지만
제공한다 (SPEC §13 로드맵).
"""

from anchor.service import Anchor

__version__ = "0.1.0"
__all__ = ["Anchor", "__version__"]
