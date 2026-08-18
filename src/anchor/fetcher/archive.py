# SPDX-License-Identifier: Apache-2.0
"""아카이브 폴백 (SPEC §5.2 6단계).

문서가 사라져도 인용이 완전히 무효화되지 않도록, GONE 판정 전에 공개
아카이브를 한 번 확인한다. 이 경로에서 얻은 버전은 source='archive'로
표시되어 "원본이 아니라 아카이브에서 확인됨"을 항상 알 수 있다.

폴백 대상은 자체 호스팅 MemGator(설정의 aggregator) 또는 Wayback CDX API.
기본 비활성 — 외부 서비스에 조용히 의존하지 않는다 (SPEC §9). MemGator는
HTTP로만 호출하며 코드를 포함하지 않는다 (ADR-0002 방식 1). 애그리게이터·
아카이브 API 호출은 사용자가 명시적으로 활성화한 API 연동이므로 robots
판정 없이 레이트 제한과 User-Agent만 적용한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

WAYBACK_BASE = "https://web.archive.org"


@dataclass(frozen=True)
class ArchiveHit:
    uri_m: str  # Memento URI-M
    memento_datetime: str  # ISO 8601 UTC
    content: bytes
    content_type: str
    bytes_down: int  # 조회 + 본문 전체


class ArchiveFallback:
    def __init__(
        self,
        client: httpx.Client,
        *,
        enabled: bool,
        aggregator: str,
        timeout_seconds: float,
        user_agent: str,
        ratelimit=None,  # HostRateLimiter 호환: acquire(host)
        robots=None,  # RobotsGate 호환: check(url) -> RobotsVerdict
    ) -> None:
        self.enabled = enabled
        self._client = client
        self._aggregator = aggregator.rstrip("/")
        self._timeout = timeout_seconds
        self._headers = {"User-Agent": user_agent}
        self._ratelimit = ratelimit
        # 애그리게이터가 지목했다는 이유로 아무 URI나 가져오지 않는다 (D-090).
        # 직접 페치가 `RobotsDisallowed(explicit)`로 막히는 경로를 애그리게이터
        # 한 겹으로 우회할 수 있으면, "explicit은 어떤 우회도 하지 않는다"
        # (SPEC §5.2)가 말뿐이 된다. 우리가 가져오는 것에는 전부 판정을 건다.
        self._robots = robots

    def _get(self, url: str, **kwargs) -> httpx.Response:
        if self._ratelimit is not None:
            self._ratelimit.acquire(httpx.URL(url).host or "")
        return self._client.get(url, headers=self._headers, timeout=self._timeout, **kwargs)

    def lookup(self, url: str) -> ArchiveHit | None:
        """URI-R로 가장 최근 URI-M을 찾아 본문까지 가져온다. 실패는 None —
        폴백의 실패가 원래의 실패 보고(gone/forbidden)를 가려서는 안 된다."""
        if not self.enabled:
            return None
        try:
            if self._aggregator:
                found = self._query_memgator(url)
            else:
                found = self._query_cdx(url)
            if found is None:
                return None
            uri_m, memento_datetime, lookup_bytes = found
            if self._robots is not None and not self._robots.check(uri_m).allowed:
                return None

            response = self._get(uri_m)
            if response.status_code != 200 or not response.content:
                return None
            return ArchiveHit(
                uri_m=uri_m,
                memento_datetime=memento_datetime,
                content=response.content,
                content_type=response.headers.get("Content-Type", ""),
                bytes_down=lookup_bytes + len(response.content),
            )
        except Exception:
            # 폴백의 실패가 **원래의 실패 보고를 가려서는 안 된다** (D-089).
            # 애그리게이터·CDX 응답은 우리가 통제하지 않는 입력이라, 파싱에서
            # 어떤 예외가 나오든 여기서 끝낸다 — 새어 나가면 `AnchorError`가
            # 아니라서 `verify()`가 잡지 못하고 보고서 전체가 사라진다.
            return None

    # -- 백엔드 ------------------------------------------------------------

    def _query_memgator(self, url: str) -> tuple[str, str, int] | None:
        """MemGator Time Travel 호환 API: GET <aggregator>/api/json/<URI-R>."""
        response = self._get(f"{self._aggregator}/api/json/{url}")
        if response.status_code != 200:
            return None
        try:
            mementos = json.loads(response.content).get("mementos", {})
            last = mementos.get("last") or (mementos.get("list") or [None])[-1]
            if not last:
                return None
            return last["uri"], _to_iso(last["datetime"]), len(response.content)
        except (ValueError, KeyError, TypeError):
            return None

    def _query_cdx(self, url: str) -> tuple[str, str, int] | None:
        """Wayback CDX API에서 가장 최근 200 스냅샷 하나를 고른다."""
        response = self._get(
            f"{WAYBACK_BASE}/cdx/search/cdx",
            params={
                "url": url,
                "output": "json",
                "filter": "statuscode:200",
                "limit": "-1",  # 최신 1건
            },
        )
        if response.status_code != 200 or not response.content.strip():
            return None
        try:
            rows = json.loads(response.content)
        except ValueError:
            return None
        if len(rows) < 2:  # 첫 행은 헤더
            return None
        header, snapshot = rows[0], rows[-1]
        record = dict(zip(header, snapshot))
        timestamp = record["timestamp"]
        original = record["original"]
        # id_ 접미사는 재생 UI 없이 원본 바이트를 돌려준다.
        uri_m = f"{WAYBACK_BASE}/web/{timestamp}id_/{original}"
        return uri_m, _cdx_timestamp_to_iso(timestamp), len(response.content)


def _to_iso(value: str) -> str:
    """애그리게이터가 준 Memento 시각을 ISO 8601로 확인·변환한다 (D-091).

    검증 없이 저장하면 그 문자열이 `versions.captured_at`에 영구히 남아,
    이후 그 문서의 `get_timemap`이 매번 실패한다 — 지우는 공개 API가 없으므로
    문서가 회복 불가능하게 오염된다. 해석할 수 없으면 폴백 자체를 포기한다.
    """
    from email.utils import parsedate_to_datetime

    if not isinstance(value, str):
        raise ValueError(f"Memento datetime is not a string: {value!r}")
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        moment = parsedate_to_datetime(value)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _cdx_timestamp_to_iso(timestamp: str) -> str:
    moment = datetime.strptime(timestamp, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")
