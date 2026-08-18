# SPDX-License-Identifier: Apache-2.0
"""앵커당 시간 예산 (SPEC §6.2). 초과 시 판정을 강제하지 않고 UNRESOLVED를
반환한다 — 모르는 것을 모른다고 말하는 것이 틀린 답을 빠르게 주는 것보다 낫다."""

from __future__ import annotations

import time

# 크레딧(양보로 잠든 시간의 상환) 총량 상한 — 예산 대비 비율 (D-203).
# 무상한이면 "앵커 하나의 예산"이 경합에 비례해 탄력적이 된다: 실측 앵커당
# 최대 971ms(예산의 4.9배), 서비스 계층 p99 345·478ms — §10 "최악 사례
# p99 < 250ms" 위반. 15%면 기본 예산 200ms에서 최대 230ms — 게이트(250ms)에
# 스케줄링 지터 여유 20ms를 남기고, 통상 잠듦 비율(~8%)을 여유 있게 덮는다.
# 상한 너머의 잠듦은 예산을 먹고 경계 문서는 UNRESOLVED로 보류된다 —
# 실측된 경합의 정직한 보고다.
_CREDIT_CAP_FRACTION = 0.15


class Budget:
    def __init__(self, budget_ms: float) -> None:
        self._deadline = time.monotonic() + budget_ms / 1000.0
        self._credit_left = budget_ms / 1000.0 * _CREDIT_CAP_FRACTION

    def remaining_seconds(self) -> float:
        return max(0.0, self._deadline - time.monotonic())

    def exhausted(self) -> bool:
        return time.monotonic() >= self._deadline

    def credit(self, seconds: float) -> None:
        """예산을 잠든 시간만큼 뒤로 민다 — 상한(_CREDIT_CAP_FRACTION)까지만.

        배경 워커가 GIL 양보로 잠든 시간은 매칭 작업이 아니므로 앵커 예산에서
        청구하지 않는다 — 청구하면 같은 앵커의 판정이 동기 경로(MISSING)와
        task 경로(UNRESOLVED)로 갈리고, §7.3이 task를 기본 경로로 정하므로
        낮은 한계가 기본이 된다 (D-196). 단 무상한 상환은 반대편 계약("앵커
        하나의 예산"이 벽시계를 묶는다, §10 p99 250ms)을 깬다 (D-203) —
        판정의 경로 무관성과 시간의 유한성을 상한이 중재한다."""
        granted = min(seconds, self._credit_left)
        self._credit_left -= granted
        self._deadline += granted
