# SPDX-License-Identifier: Apache-2.0
"""호스트별 토큰 버킷 레이트 제한 (SPEC §5.2 3단계)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class _Bucket:
    tokens: float
    updated: float


@dataclass
class HostRateLimiter:
    rate: float = 1.0
    burst: int = 3
    _buckets: dict[str, _Bucket] = field(default_factory=dict)

    def acquire(self, host: str) -> None:
        """토큰이 생길 때까지 블로킹한 뒤 하나를 소비한다."""
        now = time.monotonic()
        bucket = self._buckets.setdefault(host, _Bucket(tokens=float(self.burst), updated=now))
        bucket.tokens = min(float(self.burst), bucket.tokens + (now - bucket.updated) * self.rate)
        bucket.updated = now
        if bucket.tokens < 1.0:
            wait = (1.0 - bucket.tokens) / self.rate
            time.sleep(wait)
            bucket.tokens = 1.0
            bucket.updated = time.monotonic()
        bucket.tokens -= 1.0
