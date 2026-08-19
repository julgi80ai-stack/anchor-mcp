# SPDX-License-Identifier: Apache-2.0
"""아카이브 폴백 (SPEC §5.2 6단계).

문서가 사라져도 인용이 완전히 무효화되지 않도록, GONE 판정 전에 공개
아카이브를 한 번 확인한다. 이 경로에서 얻은 버전은 source='archive'로
표시되어 "원본이 아니라 아카이브에서 확인됨"을 항상 알 수 있다.

폴백 대상은 자체 호스팅 MemGator(설정의 aggregator) 또는 Wayback CDX API.
기본 비활성 — 외부 서비스에 조용히 의존하지 않는다 (SPEC §9). MemGator는
HTTP로만 호출하며 코드를 포함하지 않는다 (ADR-0002 방식 1).

robots 판정의 경계 (D-090·D-193): **본문을 가져오는 URI-M 요청에는 robots
판정이 걸린다** — 직접 페치가 explicit으로 막히는 경로를 애그리게이터 한
겹으로 우회할 수 없어야 하기 때문이다. 애그리게이터·CDX **조회** 요청은
사용자가 명시적으로 활성화한 API 연동이므로 레이트 제한과 User-Agent만
적용한다 — 그 서비스의 이용 약관을 따르는 것으로 본다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

WAYBACK_BASE = "https://web.archive.org"


@dataclass(frozen=True)
class _Received:
    """받은 만큼의 응답 (D-214).

    전송이 본문 도중에 끊기면 그때까지 도착한 바이트만 담긴다 — 그 바이트도
    회계에 남아야 하기 때문에 `httpx.Response`를 그대로 돌려주지 않는다.
    """

    status_code: int
    content: bytes
    headers: httpx.Headers


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

    def _get(self, url: str, on_bytes=None, **kwargs) -> _Received:
        if self._ratelimit is not None:
            self._ratelimit.acquire(httpx.URL(url).host or "")
        received = 0
        try:
            # 통째로 버퍼링하는 `client.get`이 아니라 흘려 받는다. 버퍼링은
            # 전송이 도중에 끊긴 순간 이미 받은 청크를 예외와 함께 버려,
            # 계상할 바이트 자체가 남지 않는다 (D-214).
            with self._client.stream(
                "GET", url, headers=self._headers, timeout=self._timeout, **kwargs
            ) as response:
                chunks: list[bytes] = []
                for chunk in response.iter_bytes():
                    received += len(chunk)
                    chunks.append(chunk)
                return _Received(
                    status_code=response.status_code,
                    content=b"".join(chunks),
                    headers=response.headers,
                )
        finally:
            # 조회가 무산돼도, 전송이 끊겨도 이 바이트는 이미 나갔다
            # (D-135·D-214). 성공 반환 경로의 `ArchiveHit.bytes_down`에만
            # 실으면 무산된 조회의 트래픽이 회계 어디에도 남지 않는다.
            if on_bytes is not None and received:
                on_bytes(received)

    def lookup(self, url: str, *, on_bytes=None) -> ArchiveHit | None:
        """URI-R로 가장 최근 URI-M을 찾아 본문까지 가져온다. 실패는 None —
        폴백의 실패가 원래의 실패 보고(gone/forbidden)를 가려서는 안 된다.

        `on_bytes`는 받는 즉시 호출되므로, 어떻게 끝나든 실제로 쓴 바이트가
        호출자의 회계에 남는다 (D-135)."""
        if not self.enabled:
            return None
        try:
            if self._aggregator:
                found = self._query_memgator(url, on_bytes)
            else:
                found = self._query_cdx(url, on_bytes)
            if found is None:
                return None
            uri_m, memento_datetime, lookup_bytes = found
            if self._robots is not None:
                verdict = self._robots.check(uri_m)
                if on_bytes is not None:
                    on_bytes(verdict.bytes_down)
                if not verdict.allowed:
                    return None

            response = self._get(uri_m, on_bytes)
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

    def _query_memgator(self, url: str, on_bytes=None) -> tuple[str, str, int] | None:
        """MemGator Time Travel 호환 API: GET <aggregator>/api/json/<URI-R>."""
        response = self._get(f"{self._aggregator}/api/json/{url}", on_bytes)
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

    def _query_cdx(self, url: str, on_bytes=None) -> tuple[str, str, int] | None:
        """Wayback CDX API에서 가장 최근 200 스냅샷 하나를 고른다."""
        response = self._get(
            f"{WAYBACK_BASE}/cdx/search/cdx",
            on_bytes,
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
        header, snapshots = rows[0], rows[1:]
        if "statuscode" not in header:
            # 우리가 `filter=statuscode:200`을 **요청했다는 사실**은 응답이 그
            # 필터를 지켰다는 증거가 아니다 (D-092). 열이 없으면 그 행이 200
            # 스냅샷인지 **알 수 없고**, 모르는 것을 아는 것처럼 다루면
            # 아카이브된 404 오류 페이지가 "구제된 본문"이 되어 그 위의 인용이
            # MISSING으로 단정된다. 구제를 포기하고 원 상태(gone)를 사실대로
            # 보고하는 편이 낫다 — 안 쓴 것보다 나빠지지 않는다 (§5.4).
            return None
        for snapshot in reversed(snapshots):  # 가장 최근 200 스냅샷
            record = dict(zip(header, snapshot))
            if str(record.get("statuscode")) != "200":
                continue
            timestamp = record.get("timestamp")
            original = record.get("original")
            if not timestamp or not original:
                continue
            try:
                captured_at = _cdx_timestamp_to_iso(timestamp)
            except ValueError:
                # 이 행의 시각을 읽지 못한 것이 더 오래된 200 스냅샷까지
                # 포기할 이유는 아니다.
                continue
            # id_ 접미사는 재생 UI 없이 원본 바이트를 돌려준다.
            uri_m = f"{WAYBACK_BASE}/web/{timestamp}id_/{original}"
            return uri_m, captured_at, len(response.content)
        return None


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
