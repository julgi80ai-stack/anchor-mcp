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
        body, bytes_down = self._get_robots_body(origin)
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

    def _get_robots_body(self, origin: str) -> tuple[str | None, int]:
        cached = self._repository.get_robots(origin)
        if cached is not None and age_seconds(cached.fetched_at) <= self._ttl_seconds:
            return (cached.body if cached.fetch_status == 200 else None), 0

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
            # robots.txt 자체에 접근 불가 → 판정 불능. 문서 요청을 막지는 않되
            # 캐시하지 않아 다음 호출에서 다시 시도한다.
            return None, 0

        self._repository.set_robots(origin, body, status, utcnow_iso())
        # RFC 9309: 4xx는 "제한 없음"으로 취급한다.
        return (body if status == 200 else None), bytes_down
