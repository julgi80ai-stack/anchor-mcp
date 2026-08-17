# SPDX-License-Identifier: Apache-2.0
"""Anchor — AI가 사용한 웹 근거의 시간적·출처적 무결성 계층.

Memento(RFC 7089)의 로컬 클라이언트. v1.0까지 구현됨: fetch + 해시 +
변경 감지(v0.1), 앵커 생성·재검증 + 근사 매칭 + 시간 예산(v0.2),
MCP 서버 + 도구 9종 + Tasks 확장(v0.3), PDF·gc·내보내기 CLI(v0.4), 아카이브 폴백(v0.5), 골든·속성·상호운용 테스트와 CI 매트릭스·벤치마크 게이트(v1.0).
SPEC §13 로드맵 참조.
"""

from anchor.service import Anchor

__version__ = "1.1.0"
__all__ = ["Anchor", "__version__"]
