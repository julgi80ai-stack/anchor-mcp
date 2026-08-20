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


class InvalidURL(AnchorError):
    """입력 URL이 http(s) URL이 아니다 — 스킴 없음·포트 오류 등 (D-107·D-108).

    잘못된 입력은 잘못된 입력이라고 말한다. 맨 `ValueError`로 새면 MCP
    배치가 죽고, robots 판정까지 흘러가면 사용자의 오타가 "사이트 소유자의
    거부"로 보고된다 — 사실 보고 원칙 위반 중 가장 나쁜 부류다.
    """


class QuoteNotFound(AnchorError):
    """인용문이 원문에 없다. 존재하지 않는 인용은 기록하지 않는다 (SPEC §6.1)."""


class QuoteTooShort(AnchorError):
    """인용문이 최소 길이(기본 12자) 미만 — 병리적 매칭 케이스 차단."""


class DocumentNotFound(AnchorError):
    """주어진 id 또는 URL에 해당하는 문서가 캐시에 없다."""


class VersionNotFound(AnchorError):
    """가리킨 버전이 저장소에 없다 — 대개 그 사이 gc가 회수했다 (D-251).

    버전을 지우는 경로는 둘뿐이다: `collect_garbage`(보존 정책 밖의 판본)와
    `merge_document`(같은 본문의 중복 제거). 둘 다 다른 스레드·다른 프로세스가
    언제든 할 수 있는 일이므로, **읽고 나서 쓰기까지의 사이**에 대상이
    사라지는 일은 평범하다. 그때 생 `sqlite3.IntegrityError`나 `KeyError`가
    새면 CLI는 트레이스백으로 죽고 MCP 배치는 통째로 무너진다 — 라이브러리도
    `AnchorError` 하나로 받는다는 SPEC §8의 약속이 깨진다.

    **인용된 버전에는 이 예외가 날 수 없다.** gc의 보호 집합이 앵커가 붙잡은
    버전을 지우지 않기 때문이다(SPEC §4.2) — 이것이 §1.2의 인용 복원 불변식이다.
    """


class FetchFailed(AnchorError):
    """HTTP 획득 실패. http_status에 서버가 준 상태를 그대로 담는다.

    `reason`은 **아카이브 폴백에 들어갈 자격**을 가른다 (D-088).
    `"network"`(연결 자체가 안 됨 — 호스트 소멸)는 폴백의 본래 목적이지만,
    `"timeout"`·`"redirect"`는 회복 가능한 일시 실패다. 그것까지 폴백으로
    보내면 옛 스냅샷이 현재 본문이 되고 멀쩡한 인용이 MISSING으로 단정된다.
    """

    def __init__(
        self, message: str, *, http_status: int | None = None, reason: str = "status"
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.reason = reason


class StorageError(AnchorError):
    """로컬 저장소를 열거나 쓸 수 없다 — 손상된 DB·디렉터리 경로·권한 (D-138).

    `sqlite3.DatabaseError`가 그대로 올라오면 사용자의 오타 하나가 모든 CLI
    명령을 트레이스백으로 죽인다. 트레이스백은 "도구가 깨졌다"는 뜻인데
    잘못된 `--db` 값은 그런 뜻이 아니다. 저장소 계층에서 도메인화하므로
    라이브러리 직접 사용 경로(SPEC §8)도 같은 보장을 받는다.
    """
