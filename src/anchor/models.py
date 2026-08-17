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
        raise ValueError(f"ISO 8601 기간 형식이 아닙니다: {value!r} (예: P7D, PT12H)")
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


@dataclass(frozen=True)
class Version:
    id: str
    document_id: str
    text_hash: str
    raw_hash: str
    pipeline_version: str
    captured_at: str
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


@dataclass(frozen=True)
class CiteResult:
    """SPEC §7.2 `cite` 출력에 대응."""

    anchor_id: str
    document_id: str
    version_id: str
    offset: int
    quality: Quality
    warnings: tuple[str, ...]
    created_at: str


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


@dataclass(frozen=True)
class VerifyReport:
    checked: int
    summary: dict[str, int]
    attention: tuple[AttentionItem, ...]
    anchor_ids: tuple[str, ...]  # 이번에 검증한 앵커들 (SPEC §8 예시의 재내보내기용)
    requests: int
    bytes_down: int


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
    content: str | None = field(default=None, repr=False)

    @property
    def id(self) -> str:
        """SPEC §8 예시(`doc.id`) 호환 — document_id의 별칭."""
        return self.document_id
