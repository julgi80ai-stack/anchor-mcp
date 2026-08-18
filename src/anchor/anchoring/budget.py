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

    def credit(self, seconds: float) -> None:
        """예산을 seconds만큼 뒤로 민다. 배경 워커가 GIL 양보로 잠든 시간은
        매칭 작업이 아니므로 앵커 예산에서 청구하지 않는다 — 청구하면 같은
        앵커의 판정이 동기 경로(MISSING)와 task 경로(UNRESOLVED)로 갈리고,
        §7.3이 task를 기본 경로로 정하므로 낮은 한계가 기본이 된다 (D-196)."""
        self._deadline += seconds
