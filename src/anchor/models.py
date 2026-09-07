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
class Coverage:
    """저장 본문이 원본 문서의 얼마를 담고 있는가 (D-239, SPEC §5.5).

    **판정이 아니라 사실이다.** `outcome`도 `INTACT/ALTERED`도 이 값으로
    달라지지 않는다 — 다만 그 판정이 문서의 얼마를 보고 내려진 것인지를
    함께 밝힌다. 측정 방법과 문턱의 근거는 `anchor.normalize.coverage`.

    basis:
      html-prose      HTML에서 산문 단위를 세어 잰 값.
      whole-document  text/plain — 고른 것이 없으니 버린 영역도 없다.
      no-prose        HTML이지만 셀 만한 산문 단위가 없다(색인 페이지).
      not-measurable  PDF — 가시 텍스트를 독립적으로 잴 수단이 없다.
      unknown         재지 못했거나(파싱 실패) 저장된 적이 없다(v8 이전 행).

    `ratio`가 `None`이면 **모른다**는 뜻이다. 1.0으로 채우면 사각지대가
    확신으로 둔갑한다.
    """

    basis: str
    prose_chars: int | None = None
    captured_chars: int | None = None
    # 저장 본문에서 발견되지 않은 블록의 (구조 이름, 개수). 정정 고지가
    # 실릴 법한 구조(aside·figcaption·details·blockquote·dd·caption)로
    # 귀속시킨 이름이다.
    dropped: tuple[tuple[str, int], ...] = ()

    @property
    def measured(self) -> bool:
        return self.basis in ("html-prose", "whole-document")

    @property
    def ratio(self) -> float | None:
        if self.basis == "whole-document":
            return 1.0
        if self.basis != "html-prose":
            return None
        if not self.prose_chars:
            return None
        return (self.captured_chars or 0) / self.prose_chars

    @staticmethod
    def unknown() -> "Coverage":
        return Coverage(basis="unknown")

    def encode_dropped(self) -> str | None:
        """저장용 표기 — `"aside:13 p:1"`. 빈 값은 NULL이 아니라 빈 문자열이다
        (v8 이후 행에서 "없다"와 "모른다"가 갈려야 한다)."""
        if self.basis == "unknown":
            return None
        return " ".join(f"{tag}:{count}" for tag, count in self.dropped)

    def as_payload(self) -> dict:
        """응답용 표현. `ratio`는 파생값이라 `asdict`에 담기지 않으므로 여기서
        명시적으로 싣는다 — 빠지면 응답을 읽는 쪽이 스스로 나눠야 한다."""
        return {
            "basis": self.basis,
            "prose_chars": self.prose_chars,
            "captured_chars": self.captured_chars,
            "ratio": self.ratio,
            "dropped": [{"structure": tag, "blocks": n} for tag, n in self.dropped],
        }

    @staticmethod
    def decode_dropped(value: str | None) -> tuple[tuple[str, int], ...]:
        if not value:
            return ()
        items: list[tuple[str, int]] = []
        for chunk in value.split():
            tag, _, count = chunk.rpartition(":")
            if not tag:
                continue
            try:
                items.append((tag, int(count)))
            except ValueError:
                continue
        return tuple(items)


@dataclass(frozen=True)
class Redirect:
    """이번 호출에서 실제로 따라간 리다이렉트 (v9, D-247). **사실이지 판단이 아니다.**

    기사가 삭제되고 홈으로 301되는 soft-404는 이 도구가 가장 자주 만나는
    링크 부패의 모양이지만, 우리는 그것을 **판정하지 않는다** — 해시 비교로는
    정상 개정과 구분되지 않고(모든 개정이 해시를 바꾼다), 유사도 문턱은 곧
    판단이다. 대신 이미 있는 신호를 건넨다: "영구 리다이렉트가 있었다"와
    "그 문서의 앵커가 전부 MISSING"을 잇는 것은 에이전트의 일이다.
    """

    to: str          # 도달한 곳 (정규화된 최종 URL)
    permanent: bool  # 301·308이면 참. 302·307·303은 거짓이다 (SPEC §5.1)


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
    # 이 문서에 걸린 앵커의 수 (D-284). **목록을 만들 때 센 값**이고, `None`은
    # 세지 않았다는 뜻이다 — 0으로 채우면 "앵커가 없다"는 단정이 된다. 이 수가
    # 없으면 "앵커 달린 문서만"을 추릴 수 없고, 실사용에서 문서 367건 중 앵커가
    # 붙은 것은 46건이었다.
    anchor_count: int | None = None


@dataclass(frozen=True)
class DocumentListing:
    """`list_documents`의 결과 (D-284).

    목록만 돌려주면 호출자는 그것을 **전부**로 읽는다. 실사용에서 무필터 호출이
    102,765자로 MCP 토큰 한도를 넘겨 응답이 파일로 떨어졌다 — 도구가 자기
    응답으로 호출자를 막은 것이다. 상한을 두되 **자른 사실을 함께 싣는다**
    (D-227과 같은 규칙: 도구는 자기에 대해 거짓을 말하지 않는다).
    """

    documents: tuple[Document, ...]
    total: int      # 필터를 통과한 전체 수 (상한을 적용하기 **전**)
    returned: int   # 실제로 실은 수
    truncated: bool  # 상한에서 잘렸는가. 정확히 상한만큼은 **잘린 것이 아니다**
    # `urls`로 물었을 때 캐시에 없던 URL (D-284). 코퍼스와 대조하려면 "무엇이
    # 있는가"만으로 부족하고 **무엇이 없는가**를 알아야 한다. 우리는 이 목록을
    # 해석하지 않는다 — URL은 호출자가 준다 (SPEC §1.3).
    unmatched_urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class AnchorSummary:
    """앵커 하나와 그 **마지막** 검증 상태 (D-285).

    `MOVED`가 `attention`에 없는 것은 설계가 맞다 — 조치가 필요 없기 때문이다
    (SPEC §6.3). 그러나 `summary`가 "MOVED 3"이라고 말하면서 어느 앵커인지
    물어볼 곳이 없으면 그 수는 확인할 수 없는 주장이 된다. 상태가 `None`이면
    **아직 한 번도 검증되지 않았다**는 뜻이다 — INTACT로 채우면 단정이 된다.
    """

    anchor_id: str
    document_id: str
    url: str          # 이 앵커가 인용한 URL (없으면 문서의 현재 URL)
    exact: str
    quality: str
    created_at: str
    state: str | None = None
    checked_at: str | None = None


@dataclass(frozen=True)
class AnchorListing:
    """`list_anchors`의 결과 (D-285). 자른 사실은 `DocumentListing`과 같은 규칙."""

    anchors: tuple[AnchorSummary, ...]
    total: int
    returned: int
    truncated: bool


@dataclass(frozen=True)
class VerifyScope:
    """이 보고서가 **무엇을 안 봤는가** (P1, A-1의 재발 방지).

    `checked: 104, ALTERED: 0`에는 분모가 없다. 앵커가 안 걸린 문헌과 앵커가
    걸렸고 멀쩡한 문헌을 구분할 신호가 응답에 없으면, 탐지 실패가 무해한 침묵이
    아니라 **거짓 안심**이 된다 — 문서의 1%만 보고 `unchanged`를 다른 문서와
    같은 확신으로 말했던 A-1이 앵커 집합에서 되풀이되는 자리다.

    **판정은 하나도 바꾸지 않는다.** 사실만 더한다. 무엇을 인용했어야 하는지는
    우리가 모르고 판정하지도 않는다 — 코퍼스와의 대조는 `list_documents(urls=)`로
    호출자가 한다.
    """

    anchors_in_cache: int
    documents_checked: int
    documents_with_anchors: int
    documents_in_cache: int


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
    # 그 마지막 관측에서 원본이 준 바이트의 해시 (v11, D-274). `raw_hash`는
    # 이 본문을 **만든** 바이트이고 이것은 마지막으로 **본** 바이트다.
    # `None`은 v11 이전 행이라 모른다는 뜻이다.
    last_observed_raw_hash: str | None = None
    # 이 본문이 원본 문서의 얼마를 담고 있는가 (v8, D-239). `cite`는 어떤
    # 경우에도 네트워크에 나가지 않으므로, 저장해 두지 않으면 인용을 거는
    # 순간에 그 사실을 말할 수 없다. v8 이전 행은 basis="unknown"이다.
    coverage: Coverage = field(default_factory=Coverage.unknown)


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
    # 세기는 `OCCURRENCE_COUNT_LIMIT`에서 멈추므로 그 값은 "그 이상"이다.
    occurrences: int | None = None
    # 이 앵커가 **인용한 URL** (v9, D-246). 생성 시점 문서의 `original_url`이다.
    # 문서를 경유해 답할 수 없는 이유는 문서가 사라질 수 있기 때문이다 —
    # 병합은 source의 `original_url`을 버리고 그 행을 지우므로, 옮겨간 앵커는
    # target의 정체성을 물려받는다. 앵커는 "이 URL의 이 문장을 인용했다"는
    # 기록이어야 한다. `None`은 v9 이전에 만들어져 **모르는** 것이다.
    cited_url: str | None = None


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
    # 닻을 내린 판본이 원본 문서의 얼마를 담고 있는가 (D-240). 인용을 거는
    # 순간이 사용자가 그 본문을 신뢰하기로 결정하는 순간이다.
    coverage: Coverage = field(default_factory=Coverage.unknown)


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
    # 그 세기가 상한에서 멈췄는가 (D-279). 참이면 `occurrences`는 "정확히
    # 그만큼"이 아니라 "그 이상"이다 — 50번 나오는 인용문도 8로 저장된다.
    # CLI·`cite` 경고 문자열은 "회 이상"을 붙여 이 한정을 말해 왔지만 MCP
    # JSON에는 그 말이 없었고, 우리 소비자는 사람이 아니라 에이전트다.
    occurrences_capped: bool = False
    # 앵커를 만든 판본과 대조 판본의 추출 파이프라인이 다른가 (D-235).
    # 참이면 원문이 그대로여도 경보가 날 수 있다 — **판정은 바꾸지 않고**
    # 사실만 표시한다.
    pipeline_changed: bool = False
    # 대조한 판본이 원본 문서의 얼마를 담고 있었는가 (D-241). None은 모른다.
    coverage_ratio: float | None = None


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
    # 대조 판본의 포착 범위가 경보 문턱 아래였던 앵커의 수 (D-241). 그런
    # 문서에서는 사각지대의 개정이 판정에 나타나지 않으므로, INTACT가
    # "본 범위 안에서 이상 없음"이라는 뜻으로 좁아진다. `attention`에만
    # 달면 전부 INTACT인 보고서에서 이 사실이 통째로 사라진다 (D-093과
    # 같은 판단). **판정은 바꾸지 않는다.**
    low_coverage: int = 0
    # 이 보고서가 **무엇을 안 봤는가** (P1). `checked`와 `summary`만으로는
    # "앵커가 안 걸린 문헌"과 "앵커가 걸렸고 멀쩡한 문헌"이 구분되지 않아,
    # `ALTERED: 0`이 근거 없는 안심으로 읽힌다. 판정은 바꾸지 않는다.
    scope: VerifyScope = field(
        default_factory=lambda: VerifyScope(0, 0, 0, 0)
    )


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
    # 저장 본문이 이 문서의 얼마를 담고 있는가 (D-239). 캐시 히트·304에는
    # 원본 HTML이 없으므로 저장된 값(없으면 unknown)을 그대로 싣는다.
    coverage: Coverage = field(default_factory=Coverage.unknown)
    # 포착 범위에 대해 할 말 (D-239·D-242). 판정을 바꾸지 않는 사실 문장이다.
    notes: tuple[str, ...] = ()
    # 이번 호출에서 리다이렉트를 따라갔는가 (D-247). `None`은 두 가지다 —
    # 리다이렉트가 없었거나(200 직행), 이번 호출이 네트워크에 나가지 않아
    # **볼 기회가 없었다**(cache_hit). 후자는 `outcome`이 이미 말한다.
    # **판정(outcome)은 이 값과 무관하다.**
    redirect: Redirect | None = None
    content: str | None = field(default=None, repr=False)

    @property
    def id(self) -> str:
        """SPEC §8 예시(`doc.id`) 호환 — document_id의 별칭."""
        return self.document_id
