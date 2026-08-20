# SPDX-License-Identifier: Apache-2.0
"""robots.txt 판정 (SPEC §5.2 1단계, RFC 9309).

호스트(origin)별 24시간 캐시를 SQLite에 둔다. CLI는 매 호출이 새
프로세스이므로, 캐시를 영속화하지 않으면 robots.txt 재요청 때문에
"두 번째 호출은 네트워크 0바이트"라는 v0.1 완료 기준을 지킬 수 없다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
from protego import Protego

from anchor.models import age_seconds, utcnow_iso


# RFC 9309 §2.3.1.4 "unavailable" — 규칙을 알 수 없으므로 전면 거부.
_UNAVAILABLE_TTL_SECONDS = 300

# RFC 9309 §2.3.1.2는 최소 5홉 추종을 요구한다. 따라가지 않으면 흔한 구성
# (http→https, CDN 이관, 캐노니컬 정리)에서 규칙이 통째로 사라진다 (D-094).
_MAX_ROBOTS_REDIRECTS = 5
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


def _is_unavailable(status: int) -> bool:
    return status >= 500


def _is_successful(status: int) -> bool:
    """RFC 9309 §2.3.1.1 "Successful access" — **2xx 전부**다 (D-282).

    `status == 200`만 성공으로 보면, 변환 프록시가 `203`으로 내려준 robots.txt가
    "규칙 없음"이 되어 **사이트 소유자의 규칙을 통째로 무시**한다. 4xx를
    "제한 없음"으로 읽는 것(§2.3.1.3)은 규약이지만 2xx를 그렇게 읽는 것은
    규약 위반이고, 우리 쪽에서는 정체성 위반이다.
    """
    return 200 <= status < 300


@dataclass(frozen=True)
class RobotsVerdict:
    allowed: bool
    bytes_down: int  # robots.txt를 새로 받았다면 그 크기, 캐시였다면 0
    # 거부 사유. "explicit"은 규칙을 읽었고 그 규칙이 막은 것,
    # "unavailable"은 규칙을 물어보지 못한 것이다. 둘은 다르게 다뤄야 한다.
    reason: str = "allowed"


class RobotsGate:
    def __init__(
        self,
        repository,
        client: httpx.Client,
        *,
        user_agent: str,
        ttl_seconds: int = 86400,
        respect_robots: bool = True,
        max_content_bytes: int = 10 * 1024 * 1024,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._repository = repository
        self._client = client
        self._user_agent = user_agent
        self._ttl_seconds = ttl_seconds
        self._respect_robots = respect_robots
        # robots.txt도 응답이다 — 크기 상한과 타임아웃이 여기만 비켜갈 이유가
        # 없다. 비켜가면 20MB robots.txt가 통째로 캐시 DB에 들어앉고, 설정
        # 1초짜리 타임아웃이 10초를 기다린다 (D-096, SPEC §5.4).
        self._max_content_bytes = max_content_bytes
        self._timeout_seconds = timeout_seconds

    def check(self, url: str) -> RobotsVerdict:
        if not self._respect_robots:
            return RobotsVerdict(allowed=True, bytes_down=0)

        origin = self._origin(url)
        body, bytes_down, unavailable = self._get_robots_body(origin)
        if unavailable:
            # RFC 9309 §2.3.1.4: robots.txt를 받을 수 없으면(5xx) 전면 거부로
            # 간주한다. 서버가 과부하로 규칙을 못 주는 바로 그 순간에 무제한
            # 접근으로 전환하는 것은 정직한 클라이언트가 아니다 (D-003).
            return RobotsVerdict(allowed=False, bytes_down=bytes_down, reason="unavailable")
        if body is None:
            return RobotsVerdict(allowed=True, bytes_down=bytes_down)

        # 선두 BOM을 지운다. 남으면 파서가 `﻿User-agent:`를 지시자로 읽지
        # 못해 **규칙 그룹 전체를 버린다** — Windows 편집기로 저장된
        # robots.txt에서 흔하고, RFC 9309 §2.3은 선두 BOM 무시를 규정한다
        # (D-095). 결과는 소유자가 쓴 금지가 없는 것이 되는 것이다.
        parser = Protego.parse(body.lstrip("\ufeff"))
        allowed = parser.can_fetch(url, self._user_agent)
        return RobotsVerdict(
            allowed=allowed,
            bytes_down=bytes_down,
            reason="allowed" if allowed else "explicit",
        )

    def _request_robots(self, origin: str, account) -> tuple[int | None, str]:
        """robots.txt를 받는다. 리다이렉트를 따라가고 크기 상한을 적용한다.

        반환: (상태, 본문). 상태가 None이면 추종 한도 초과.

        내려받은 바이트는 반환값이 아니라 `account`로 **받는 즉시** 흘린다
        (D-213). 지역 합계로 돌려주면 전송 도중 예외가 난 홉의 바이트가
        예외와 함께 사라진다 — 선행 홉들의 바이트까지 통째로.
        """
        url = f"{origin}/robots.txt"
        # 홉 한도가 루프까지 함께 막는다 — 자기 자신을 가리키는 리다이렉트도
        # 한도에서 끝나 "규칙을 알 수 없음"이 된다. 방문 집합을 따로 두는
        # 것은 요청 몇 번을 아낄 뿐 판정을 바꾸지 않아 두지 않는다.
        for _ in range(_MAX_ROBOTS_REDIRECTS + 1):
            status, body, location = self._one_hop(url, account)
            if 300 <= status < 400 and (status not in _REDIRECT_STATUSES or not location):
                # Location 없는 3xx·목록 밖 3xx(300·305) — 규칙을 물어보지
                # 못했다. "제한 없음"으로 캐시하면 같은 판정 불능인 홉 한도
                # 초과(거부)와 판정이 갈린다 (D-192).
                return None, ""
            if status not in _REDIRECT_STATUSES:
                return status, body
            url = str(httpx.URL(url).join(location))
        return None, ""

    def _one_hop(self, url: str, account) -> tuple[int, str, str | None]:
        received = 0
        try:
            with self._client.stream(
                "GET",
                url,
                headers={"User-Agent": self._user_agent},
                timeout=self._timeout_seconds,
            ) as response:
                chunks: list[bytes] = []
                for chunk in response.iter_bytes():
                    received += len(chunk)
                    if received > self._max_content_bytes:
                        # 상한을 넘긴 robots.txt는 **읽은 데까지만** 쓴다. 경계
                        # 청크는 잘라서 보관한다 — 통째로 버리면 상한이 전송
                        # 청크보다 작은 구성에서 규칙 전체가 사라져 전면 허용이
                        # 24시간 캐시된다 (D-191).
                        keep = self._max_content_bytes - (received - len(chunk))
                        chunks.append(chunk[:keep])
                        truncated = True
                        break
                    chunks.append(chunk)
                else:
                    truncated = False
                content = b"".join(chunks)
                status = response.status_code
                if _is_successful(status):
                    # 선언된 charset을 존중한다. utf-8 하드코딩은 UTF-16 문서를
                    # 전부 U+FFFD로 만들어 규칙이 통째로 사라진다 (D-190).
                    encoding = response.charset_encoding or "utf-8"
                    try:
                        body = content.decode(encoding, errors="replace")
                    except (LookupError, ValueError):
                        body = content.decode("utf-8", errors="replace")
                    if truncated and body.endswith("\ufffd"):
                        # 절단이 다중바이트 문자 중간을 잘랐다 — 꼬리의 대체
                        # 문자는 데이터가 아니라 자른 흔적이다.
                        body = body[:-1]
                else:
                    body = ""
                return status, body, response.headers.get("Location")
        finally:
            # 회계는 보관량이 아니라 **실수령량**이다 (D-191). 그리고 전송이
            # 도중에 끊겨도 받은 데까지는 받은 것이다 — 여기서 흘리지 않으면
            # 예외와 함께 사라진다 (D-213).
            if received:
                account(received)

    @staticmethod
    def _origin(url: str) -> str:
        parsed = httpx.URL(url)
        return f"{parsed.scheme}://{parsed.netloc.decode()}"

    def _get_robots_body(self, origin: str) -> tuple[str | None, int, bool]:
        """(본문, 내려받은 바이트, 판정 불능 여부)를 돌려준다."""
        cached = self._repository.get_robots(origin)
        ttl = (
            _UNAVAILABLE_TTL_SECONDS
            if cached is not None and _is_unavailable(cached.fetch_status)
            else self._ttl_seconds
        )
        if cached is not None and age_seconds(cached.fetched_at) <= ttl:
            return (
                (cached.body if _is_successful(cached.fetch_status) else None),
                0,
                _is_unavailable(cached.fetch_status),
            )

        received = 0

        def account(count: int) -> None:
            nonlocal received
            received += count

        try:
            status, body = self._request_robots(origin, account)
        except httpx.HTTPError:
            # robots.txt에 접근조차 못 했다 → 판정 불능. 캐시하지 않아 다음
            # 호출에서 다시 시도하되, 이번 요청은 보류한다 (RFC 9309 §2.3.1.4).
            # 다만 그때까지 받은 바이트는 상수 0으로 버리지 않는다 — 선행
            # 리다이렉트 홉의 본문까지 통째로 증발한다 (D-213).
            return None, received, True

        if status is None:
            # 추종 한도를 넘겼다 = 규칙을 물어보지 못했다. 캐시하지 않는다.
            return None, received, True
        # 5xx는 짧게만 캐시한다 — 일시 장애로 하루 동안 막히면 안 된다.
        self._repository.set_robots(origin, body, status, utcnow_iso())
        # RFC 9309 §2.3.1.3: 4xx는 "제한 없음"으로 취급한다.
        return (body if _is_successful(status) else None), received, _is_unavailable(status)
