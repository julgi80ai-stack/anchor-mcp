# SPDX-License-Identifier: Apache-2.0
"""예외 계층. 403은 403으로 보고한다 — 우회 경로는 없다 (SPEC §5.4)."""

from __future__ import annotations


class AnchorError(Exception):
    """Anchor의 모든 예외의 뿌리."""


class RobotsDisallowed(AnchorError):
    """robots.txt가 해당 URL의 페치를 거부했다. 네트워크 요청 없이 반환된다.

    `reason`이 `"explicit"`이면 규칙을 읽었고 그 규칙이 막은 것이고,
    `"unavailable"`이면 규칙을 물어보지 못한 것이다(호스트 소멸·5xx).
    전자는 사이트 소유자의 의사이므로 어떤 우회도 하지 않는다. 후자는
    의사를 확인할 수 없는 상태일 뿐이므로, **다른 호스트인 공개 아카이브**를
    확인하는 것까지 막지는 않는다 (SPEC §5.2 6단계).
    """

    def __init__(self, message: str, *, reason: str = "explicit") -> None:
        super().__init__(message)
        self.reason = reason


class ContentTooLarge(AnchorError):
    """응답 본문이 max_content_bytes를 초과했다."""


class UnsupportedContent(AnchorError):
    """처리 범위 밖의 콘텐츠 유형 (텍스트 레이어 없는 스캔 PDF 등, SPEC §5.3)."""


class ExtractionFailed(AnchorError):
    """본문 추출기가 문서에서 본문을 찾지 못했다."""


class ConfigError(AnchorError):
    """설정 파일·환경변수의 문법 또는 값 범위 오류."""


class QuoteNotFound(AnchorError):
    """인용문이 원문에 없다. 존재하지 않는 인용은 기록하지 않는다 (SPEC §6.1)."""


class QuoteTooShort(AnchorError):
    """인용문이 최소 길이(기본 12자) 미만 — 병리적 매칭 케이스 차단."""


class DocumentNotFound(AnchorError):
    """주어진 id 또는 URL에 해당하는 문서가 캐시에 없다."""


class FetchFailed(AnchorError):
    """HTTP 획득 실패. http_status에 서버가 준 상태를 그대로 담는다."""

    def __init__(self, message: str, *, http_status: int | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status
