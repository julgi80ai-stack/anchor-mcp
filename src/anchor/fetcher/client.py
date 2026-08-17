# SPDX-License-Identifier: Apache-2.0
"""httpx 기반 조건부 GET (SPEC §5.2 4단계, RFC 9110).

정직한 클라이언트로 동작한다: 위장 없음, Retry-After 존중,
403/429는 지수 백오프 재시도 후 그대로 보고 (SPEC §5.4).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from anchor.errors import ContentTooLarge, FetchFailed

ACCEPT_HEADER = "text/html, application/xhtml+xml, text/plain"
RETRYABLE_STATUSES = frozenset({403, 429})
MAX_RETRIES = 3
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


class ConditionalFetcher:
    def __init__(
        self,
        client: httpx.Client,
        *,
        user_agent: str,
        max_content_bytes: int,
        retry_backoff_base: float = 1.0,
    ) -> None:
        self._client = client
        self._user_agent = user_agent
        self._max_content_bytes = max_content_bytes
        self._retry_backoff_base = retry_backoff_base

    def get(
        self, url: str, *, etag: str | None = None, last_modified: str | None = None
    ) -> FetchResponse:
        headers = {"User-Agent": self._user_agent, "Accept": ACCEPT_HEADER}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        started = time.monotonic()
        response = self._request(url, headers)
        for attempt in range(MAX_RETRIES):
            if response.status not in RETRYABLE_STATUSES:
                break
            time.sleep(self._retry_delay(response, attempt))
            response = self._request(url, headers)

        elapsed_ms = int((time.monotonic() - started) * 1000)
        return FetchResponse(
            **{**response.__dict__, "elapsed_ms": elapsed_ms}
        )

    def _retry_delay(self, response: FetchResponse, attempt: int) -> float:
        backoff = self._retry_backoff_base * (2**attempt)
        if response.retry_after is not None:
            try:
                return min(max(float(response.retry_after), backoff), MAX_RETRY_AFTER_SECONDS)
            except ValueError:
                pass  # HTTP-date 형식은 v0.1에서 지수 백오프로 대신한다
        return backoff

    def _request(self, url: str, headers: dict[str, str]) -> FetchResponse:
        try:
            with self._client.stream("GET", url, headers=headers) as response:
                if response.status_code == 200:
                    declared = response.headers.get("Content-Length")
                    if declared and declared.isdigit() and int(declared) > self._max_content_bytes:
                        raise ContentTooLarge(
                            f"Content-Length {declared}가 상한 {self._max_content_bytes}를 초과"
                        )
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > self._max_content_bytes:
                            raise ContentTooLarge(
                                f"본문이 상한 {self._max_content_bytes}바이트를 초과"
                            )
                        chunks.append(chunk)
                    content = b"".join(chunks)
                else:
                    response.read()
                    content = response.content

                return FetchResponse(
                    status=response.status_code,
                    content=content,
                    content_type=response.headers.get("Content-Type", ""),
                    etag=response.headers.get("ETag"),
                    last_modified=response.headers.get("Last-Modified"),
                    retry_after=response.headers.get("Retry-After"),
                    final_url=str(response.url),
                    bytes_down=len(content),
                    elapsed_ms=0,
                )
        except httpx.TimeoutException as error:
            raise FetchFailed(f"타임아웃: {url}") from error
        except httpx.TooManyRedirects as error:
            raise FetchFailed(f"리다이렉트 한도 초과: {url}") from error
        except httpx.HTTPError as error:
            raise FetchFailed(f"네트워크 오류: {url} ({error})") from error
