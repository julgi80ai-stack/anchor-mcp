# SPDX-License-Identifier: Apache-2.0
"""호스트별 토큰 버킷 레이트 제한 (SPEC §5.2 3단계)."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class _Bucket:
    tokens: float
    updated: float


@dataclass
class HostRateLimiter:
    """호스트별 토큰 버킷. 스레드 안전하다 (D-009).

    락이 없으면 동시 호출자들이 같은 대기 시간을 자고 한꺼번에 깨어나
    설정값의 몇 배로 대상 서버를 두드린다 — robots를 지키는 도구가 예절
    계약을 어기는 셈이라, 라이브러리 직접 사용까지 고려해 락을 둔다.
    """

    rate: float = 1.0
    burst: int = 3
    _buckets: dict[str, _Bucket] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        if self.rate <= 0:
            raise ValueError(
                f"requests_per_second must be positive, got {self.rate} — "
                "레이트 제한은 양수여야 합니다 (0은 '무제한'이 아닙니다)"
            )
        if self.burst < 1:
            raise ValueError(
                f"burst must be at least 1, got {self.burst} — burst는 1 이상이어야 합니다"
            )

    def acquire(self, host: str) -> None:
        """토큰이 생길 때까지 블로킹한 뒤 하나를 소비한다."""
        with self._lock:
            now = time.monotonic()
            bucket = self._buckets.setdefault(
                host, _Bucket(tokens=float(self.burst), updated=now)
            )
            bucket.tokens = min(
                float(self.burst), bucket.tokens + (now - bucket.updated) * self.rate
            )
            bucket.updated = now
            if bucket.tokens < 1.0:
                wait = (1.0 - bucket.tokens) / self.rate
                # 대기하는 동안에도 락을 쥔다 — 놓으면 여러 스레드가 같은
                # 시각에 깨어나 버스트가 되살아난다.
                time.sleep(wait)
                bucket.tokens = 1.0
                bucket.updated = time.monotonic()
            bucket.tokens -= 1.0
