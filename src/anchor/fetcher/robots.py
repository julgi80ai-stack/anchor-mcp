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


def _is_unavailable(status: int) -> bool:
    return status >= 500


@dataclass(frozen=True)
class RobotsVerdict:
    allowed: bool
    bytes_down: int  # robots.txt를 새로 받았다면 그 크기, 캐시였다면 0


class RobotsGate:
    def __init__(
        self,
        repository,
        client: httpx.Client,
        *,
        user_agent: str,
        ttl_seconds: int = 86400,
        respect_robots: bool = True,
    ) -> None:
        self._repository = repository
        self._client = client
        self._user_agent = user_agent
        self._ttl_seconds = ttl_seconds
        self._respect_robots = respect_robots

    def check(self, url: str) -> RobotsVerdict:
        if not self._respect_robots:
            return RobotsVerdict(allowed=True, bytes_down=0)

        origin = self._origin(url)
        body, bytes_down, unavailable = self._get_robots_body(origin)
        if unavailable:
            # RFC 9309 §2.3.1.4: robots.txt를 받을 수 없으면(5xx) 전면 거부로
            # 간주한다. 서버가 과부하로 규칙을 못 주는 바로 그 순간에 무제한
            # 접근으로 전환하는 것은 정직한 클라이언트가 아니다 (D-003).
            return RobotsVerdict(allowed=False, bytes_down=bytes_down)
        if body is None:
            return RobotsVerdict(allowed=True, bytes_down=bytes_down)

        parser = Protego.parse(body)
        return RobotsVerdict(
            allowed=parser.can_fetch(url, self._user_agent), bytes_down=bytes_down
        )

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
                (cached.body if cached.fetch_status == 200 else None),
                0,
                _is_unavailable(cached.fetch_status),
            )

        try:
            response = self._client.get(
                f"{origin}/robots.txt",
                headers={"User-Agent": self._user_agent},
                timeout=10.0,
            )
            status = response.status_code
            body = response.text if status == 200 else ""
            bytes_down = len(response.content)
        except httpx.HTTPError:
            # robots.txt에 접근조차 못 했다 → 판정 불능. 캐시하지 않아 다음
            # 호출에서 다시 시도하되, 이번 요청은 보류한다 (RFC 9309 §2.3.1.4).
            return None, 0, True

        # 5xx는 짧게만 캐시한다 — 일시 장애로 하루 동안 막히면 안 된다.
        self._repository.set_robots(origin, body, status, utcnow_iso())
        # RFC 9309 §2.3.1.3: 4xx는 "제한 없음"으로 취급한다.
        return (body if status == 200 else None), bytes_down, _is_unavailable(status)
