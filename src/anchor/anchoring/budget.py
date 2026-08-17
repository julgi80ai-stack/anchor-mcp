# SPDX-License-Identifier: Apache-2.0
"""앵커당 시간 예산 (SPEC §6.2). 초과 시 판정을 강제하지 않고 UNRESOLVED를
반환한다 — 모르는 것을 모른다고 말하는 것이 틀린 답을 빠르게 주는 것보다 낫다."""

from __future__ import annotations

import time


class Budget:
    def __init__(self, budget_ms: float) -> None:
        self._deadline = time.monotonic() + budget_ms / 1000.0

    def remaining_seconds(self) -> float:
        return max(0.0, self._deadline - time.monotonic())

    def exhausted(self) -> bool:
        return time.monotonic() >= self._deadline
