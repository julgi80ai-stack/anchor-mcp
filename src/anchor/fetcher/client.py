# SPDX-License-Identifier: Apache-2.0
"""httpx 기반 조건부 GET (SPEC §5.2 4단계, RFC 9110).

정직한 클라이언트로 동작한다: 위장 없음, Retry-After 존중,
403/429는 지수 백오프 재시도 후 그대로 보고 (SPEC §5.4).

리다이렉트는 클라이언트에 맡기지 않고 직접 따라간다. 목적지마다 robots를
다시 판정해야 하기 때문이다 — 자동 추종에 맡기면 금지된 경로·호스트를
그대로 가져오게 된다 (D-001).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace

import httpx

from anchor.errors import ContentTooLarge, FetchFailed, RobotsDisallowed

ACCEPT_HEADER = "text/html, application/xhtml+xml, text/plain, application/pdf"
RETRYABLE_STATUSES = frozenset({403, 429})
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
MAX_RETRIES = 3
# 이보다 오래 기다리라는 응답은 재시도하지 않고 그대로 보고한다. 몰래
# 일찍 두드리는 것보다 "확인 불가"가 정직하다 (SPEC §5.4).
MAX_RETRY_AFTER_SECONDS = 60.0


@dataclass(frozen=True)
class FetchResponse:
    status: int
    content: bytes
    content_type: str
    etag: str | None
    last_modified: str | None
    retry_after: str | None
    final_url: str
    bytes_down: int
    elapsed_ms: int
    location: str | None = None  # 3xx의 Location 헤더


class ConditionalFetcher:
    def __init__(
        self,
        client: httpx.Client,
        *,
        user_agent: str,
        max_content_bytes: int,
        retry_backoff_base: float = 1.0,
        max_redirects: int = 5,
    ) -> None:
        self._client = client
        self._user_agent = user_agent
        self._max_content_bytes = max_content_bytes
        self._retry_backoff_base = retry_backoff_base
        self._max_redirects = max_redirects

    def get(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        before_hop: Callable[[str], int] | None = None,
    ) -> FetchResponse:
        """조건부 GET. `before_hop`은 매 홉 직전에 호출되어 robots 판정과
        레이트 제한을 수행하고, 그 과정에서 내려받은 바이트를 돌려준다."""
        headers = {"User-Agent": self._user_agent, "Accept": ACCEPT_HEADER}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        started = time.monotonic()
        overhead_bytes = 0
        attempted_bytes = 0
        current = url

        for hop in range(self._max_redirects + 1):
            if before_hop is not None:
                overhead_bytes += before_hop(current)

            response = self._request(current, headers)
            attempted_bytes += response.bytes_down
            for attempt in range(MAX_RETRIES):
                if response.status not in RETRYABLE_STATUSES:
                    break
                delay = self._retry_delay(response, attempt)
                if delay is None:  # 서버가 지정한 대기가 상한을 넘는다
                    break
                time.sleep(delay)
                response = self._request(current, headers)
                attempted_bytes += response.bytes_down

            if response.status not in REDIRECT_STATUSES:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                return replace(
                    response,
                    elapsed_ms=elapsed_ms,
                    bytes_down=attempted_bytes + overhead_bytes,
                )

            location = response.location
            if not location:
                raise FetchFailed(
                    f"Redirect without Location — 목적지 없는 리다이렉트: {current}",
                    http_status=response.status,
                )
            current = str(httpx.URL(current).join(location))

        raise FetchFailed(
            f"Too many redirects — 리다이렉트 한도 초과: {url}", http_status=None
        )

    def _retry_delay(self, response: FetchResponse, attempt: int) -> float | None:
        """다음 재시도까지의 대기. 서버가 상한보다 긴 대기를 지정하면 None."""
        backoff = self._retry_backoff_base * (2**attempt)
        raw = response.retry_after
        if raw is None:
            return backoff
        requested = _parse_retry_after(raw)
        if requested is None:  # 해석 불가 — 우리 백오프로 물러선다
            return backoff
        if requested > MAX_RETRY_AFTER_SECONDS:
            # 지정 시각보다 일찍 두드리지 않는다 (SPEC §5.4 "항상 존중").
            return None
        return max(requested, backoff)

    def _request(self, url: str, headers: dict[str, str]) -> FetchResponse:
        try:
            with self._client.stream("GET", url, headers=headers) as response:
                declared = response.headers.get("Content-Length")
                if declared and declared.isdigit() and int(declared) > self._max_content_bytes:
                    raise ContentTooLarge(
                        f"Content-Length {declared} exceeds the "
                        f"{self._max_content_bytes}-byte limit — 본문 크기 상한 초과"
                    )
                # 크기 상한은 실패 응답에도 적용한다. 거대한 오류 페이지나
                # 차단 인터스티셜을 통째로 버퍼링하지 않는다 (D-002).
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > self._max_content_bytes:
                        raise ContentTooLarge(
                            f"Body exceeds the {self._max_content_bytes}-byte limit — "
                            "본문 크기 상한 초과"
                        )
                    chunks.append(chunk)
                content = b"".join(chunks)

                fetched = FetchResponse(
                    status=response.status_code,
                    content=content,
                    content_type=response.headers.get("Content-Type", ""),
                    etag=response.headers.get("ETag"),
                    last_modified=response.headers.get("Last-Modified"),
                    retry_after=response.headers.get("Retry-After"),
                    final_url=str(response.url),
                    bytes_down=len(content),
                    elapsed_ms=0,
                    location=response.headers.get("Location"),
                )
                return fetched
        except httpx.TimeoutException as error:
            raise FetchFailed(f"Timeout — 타임아웃: {url}") from error
        except httpx.TooManyRedirects as error:
            raise FetchFailed(f"Too many redirects — 리다이렉트 한도 초과: {url}") from error
        except httpx.HTTPError as error:
            raise FetchFailed(f"Network error — 네트워크 오류: {url} ({error})") from error


def _parse_retry_after(raw: str) -> float | None:
    """`Retry-After`를 초로. 숫자와 HTTP-date를 모두 받는다 (D-004/D-005)."""
    raw = raw.strip()
    try:
        seconds = float(raw)
    except ValueError:
        pass
    else:
        # NaN·무한대는 float()가 통과시키므로 여기서 걸러야 한다 (D-005).
        if seconds != seconds or seconds in (float("inf"), float("-inf")):
            return None
        return max(0.0, seconds)

    from email.utils import parsedate_to_datetime

    try:
        moment = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max(0.0, (moment - now).total_seconds())
