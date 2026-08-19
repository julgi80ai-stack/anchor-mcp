# SPDX-License-Identifier: Apache-2.0
"""도메인 데이터클래스와 시간·식별자 도우미."""

from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum


class Quality(StrEnum):
    """앵커 품질 (SPEC §6.1). SHORT는 32자 미만 — 시간 예산 절반 적용."""

    OK = "ok"
    SHORT = "short"


def uuid7() -> str:
    """RFC 9562 UUIDv7 문자열. 시간순 정렬이 가능해 PK로 쓴다."""
    ts_ms = time.time_ns() // 1_000_000
    value = (
        (ts_ms & 0xFFFF_FFFF_FFFF) << 80
        | 0x7 << 76
        | secrets.randbits(12) << 64
        | 0b10 << 62
        | secrets.randbits(62)
    )
    return str(uuid.UUID(int=value))


def utcnow_iso() -> str:
    """ISO 8601 UTC 문자열 (초 단위, Z 표기). 모든 시각 컬럼의 형식."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def age_seconds(since_iso: str) -> float:
    return (datetime.now(timezone.utc) - parse_iso(since_iso)).total_seconds()


_DURATION_RE = None  # 지연 컴파일


def parse_iso_duration(value: str) -> float:
    """ISO 8601 기간(P7D, PT12H, P1DT6H30M …)을 초로 변환한다."""
    global _DURATION_RE
    import re

    if _DURATION_RE is None:
        _DURATION_RE = re.compile(
            r"^P(?:(?P<weeks>\d+)W)?(?:(?P<days>\d+)D)?"
            r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$"
        )
    match = _DURATION_RE.match(value.strip().upper())
    if not match or not any(match.groupdict().values()):
        raise ValueError(f"Not an ISO 8601 duration: {value!r} (e.g. P7D, PT12H) — ISO 8601 기간 형식이 아닙니다")
    parts = {key: float(group or 0) for key, group in match.groupdict().items()}
    return (
        parts["weeks"] * 604800
        + parts["days"] * 86400
        + parts["hours"] * 3600
        + parts["minutes"] * 60
        + parts["seconds"]
    )


def iso_ago(seconds: float) -> str:
    """현재로부터 seconds 이전 시각의 ISO 8601 UTC 문자열."""
    moment = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class Document:
    id: str
    url: str
    original_url: str
    title: str | None
    first_seen_at: str
    last_checked_at: str
    status: str  # live | gone | forbidden | paywalled
    etag: str | None
    last_modified: str | None
    robots_allowed: bool
    current_version: str | None = None  # 원문이 지금 서빙하는 본문의 버전 (v3)


@dataclass(frozen=True)
class Version:
    id: str
    document_id: str
    text_hash: str
    raw_hash: str
    pipeline_version: str
    captured_at: str      # 처음 캡처된 때 (Memento-Datetime)
    last_observed_at: str   # 원문에서 마지막으로 관측된 때 (v6, D-083)
    last_observed_seq: int  # 그 관측의 순서 — 같은 초의 두 관측을 가른다
    byte_size: int
    char_count: int
    http_status: int
    source: str  # live | archive
    source_uri: str | None


@dataclass(frozen=True)
class AnchorRecord:
    id: str
    document_id: str
    created_version: str
    exact: str
    prefix: str
    suffix: str
    position_hint: int
    exact_hash: str
    quality: str  # ok | short
    note: str | None
    created_at: str
    # 생성 시점에 인용문이 원문에서 몇 번 나왔는가 (v7, D-231). 앵커는 첫
    # 출현에 묶이므로(D-047) 2 이상이면 어느 인스턴스가 "그" 인용인지 모호
    # 하다. `cite` 응답 문자열로만 두면 다른 세션에서 재검증하는 사용자는
    # 그 모호성을 알 길이 없다. `None`은 v7 이전에 만들어져 **모르는** 것이다.
    occurrences: int | None = None


@dataclass(frozen=True)
class CiteResult:
    """SPEC §7.2 `cite` 출력에 대응."""

    anchor_id: str
    document_id: str
    version_id: str
    # 앵커가 붙은 판본의 출처 (live | archive). SPEC §5.2는 아카이브에서
    # 확인된 것을 사용자가 **항상** 알 수 있어야 한다고 규정하는데, 그 사실이
    # `fetch_document`·`get_version`에만 있었다 (D-093) — 원본이 죽어 아카이브
    # 스냅샷에 앵커를 단 것과 원본에 단 것이 응답에서 구분되지 않았다.
    source: str
    offset: int
    quality: Quality
    warnings: tuple[str, ...]
    created_at: str
    # 앵커를 단 판본의 나이 (D-230). `cite`는 **어떤 경우에도 네트워크에
    # 나가지 않으므로**, 이 둘이 없으면 사용자는 며칠 전 스냅샷에 인용을
    # 걸면서 그 사실을 모른다. captured_at은 그 본문이 처음 캡처된 때이고
    # last_checked_at은 그 문서를 원본과 마지막으로 대조한 때다.
    captured_at: str = ""
    last_checked_at: str = ""


@dataclass(frozen=True)
class AttentionItem:
    """조치가 필요한 검증 결과 (SPEC §7.3 attention)."""

    anchor_id: str
    url: str
    state: str
    before: str
    after: str | None
    match_score: float | None
    edit_distance: int | None
    truncated: bool = False  # 문서가 상한에서 잘려 일부만 검사했다
    # 앵커를 만든 자리와 지금 찾은 자리. 둘이 멀면 같은 문서의 **다른 절**에서
    # 왔다는 뜻이다 — 편집거리만 보고는 알 수 없다 (D-115).
    position_hint: int | None = None
    found_offset: int | None = None
    # 무엇과 대조했는가 (live | archive). 대조 자체가 없었던 항목(GONE·
    # UNREACHABLE)은 None이다 — 출처를 지어내지 않는다 (D-093).
    source: str | None = None
    # 이 인용문이 생성 시점에 원문에서 몇 번 나왔는가 (D-231). 2 이상이면
    # 앵커가 어느 인스턴스를 가리키는지 모호하다. None은 모른다는 뜻이다.
    occurrences: int | None = None
    # 앵커를 만든 판본과 대조 판본의 추출 파이프라인이 다른가 (D-235).
    # 참이면 원문이 그대로여도 경보가 날 수 있다 — **판정은 바꾸지 않고**
    # 사실만 표시한다.
    pipeline_changed: bool = False


@dataclass(frozen=True)
class VerifyReport:
    checked: int
    summary: dict[str, int]
    # 대조 상대의 출처별 집계 (live | archive | none). `attention`에만 출처를
    # 달면 **전부 INTACT인 보고서에서 그 사실이 통째로 사라진다** — 원본은
    # 404이고 아카이브 스냅샷만 봤다는 것이 "이상 없음"으로 읽힌다 (D-093).
    # 합은 항상 `checked`와 같다.
    sources: dict[str, int]
    attention: tuple[AttentionItem, ...]
    anchor_ids: tuple[str, ...]  # 이번에 검증한 앵커들 (SPEC §8 예시의 재내보내기용)
    stopped_early: bool  # 취소·종료 신호로 중도 종료됐는가 (부분 결과)
    requests: int
    not_modified: int  # 304로 끝난 요청 수 (SPEC §7.3 network)
    bytes_down: int
    # 검증한 앵커 중 인용문이 원문에 여러 번 나오던 것의 수 (D-231).
    # `attention`에만 달면 모호한 채 INTACT가 된 앵커에서 그 사실이 사라진다 —
    # 사용자가 인용한 인스턴스가 지워져도 다른 인스턴스가 잡히기 때문이다.
    ambiguous: int = 0
    # 앵커를 만든 판본과 대조 판본의 추출 파이프라인이 달랐던 앵커의 수
    # (D-235). 이 값이 크면 경보의 원인이 원문 변경이 아닐 수 있다.
    pipeline_changed: int = 0


@dataclass(frozen=True)
class Network:
    bytes_down: int
    elapsed_ms: int


@dataclass(frozen=True)
class FetchResult:
    """SPEC §7.1 `fetch_document` 출력에 대응하는 결과."""

    document_id: str
    version_id: str
    url: str
    title: str | None
    outcome: str  # cache_hit | not_modified | unchanged | changed | renormalized | created
    captured_at: str
    text_hash: str
    char_count: int
    source: str
    network: Network
    # 원본 바이트는 달라졌는데 추출된 본문은 같았는가 (D-232). "원문은
    # 바뀌었는데 우리가 보는 영역 밖에서 바뀌었다"를 정확히 가리키는
    # 신호다 — 추출 사각지대의 저비용 탐지 수단이므로 버리지 않는다.
    # **판정(outcome)은 이 값과 무관하다.**
    raw_changed: bool = False
    content: str | None = field(default=None, repr=False)

    @property
    def id(self) -> str:
        """SPEC §8 예시(`doc.id`) 호환 — document_id의 별칭."""
        return self.document_id
