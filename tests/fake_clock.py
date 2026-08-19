# SPDX-License-Identifier: Apache-2.0
"""주입 가능한 가상 시계 (D-224).

**왜 있는가.** 이 저장소의 몇몇 시험이 기제를 벽시계 *상한*으로 쟀다. 상한은
"느리면 실패"이고 그건 계약이 아니다 — 부하 걸린 CI 러너에서 코드 회귀 없이
빨개진다(macOS 러너 실측: 레이트 제한 0.624s·0.740s / 이론 0.3s, 예산 크레딧
누수 5.5ms / 허용 5ms). 이 캠페인은 같은 실패를 세 번 겪었다(D-189 → D-209 →
D-210): **기계 상태를 코드 회귀로 오인**하는 것.

상한을 느슨하게 푸는 것은 해가 아니다 — 그러면 잡아야 할 회귀까지 놓친다
(D-202의 항진식 게이트). 시계를 주입하면 잠든 시간·경과의 **정확한 값**을
단언할 수 있어, 상한이 필요 없어지면서 판별력은 오히려 올라간다.

대상 모듈이 `import time` 후 `time.monotonic()`/`time.sleep()`만 쓰는 경우에
한해 모듈의 `time` 이름을 이 객체로 바꿔 끼운다(현재 대상: `ratelimit`,
`budget`, `approx`, `repository`). 진짜 시간이 흐르는지는 별도의 실시간
시험이 지킨다 — 여기서 재는 것은 **계산**이지 스케줄러가 아니다.
"""

from __future__ import annotations

import pytest


class FakeClock:
    """`time` 모듈의 최소 대역 — `monotonic()`과 `sleep()`만 흉내낸다."""

    def __init__(self, start: float = 1000.0, sleep_floor: float = 0.0) -> None:
        self.start = start
        self.now = start
        self.sleep_floor = sleep_floor
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        """요청한 만큼 시간을 민다. `sleep_floor`는 `sleep(0)`의 실비용 모형이다.

        `time.sleep(0)`은 공짜가 아니다 — 이 머신 실측 84µs이고, 그 잠듦이
        앵커 예산에서 청구되는지가 D-196의 쟁점이다. 0을 0으로 두면 그
        쟁점 자체가 픽스처에서 사라진다(정상값 하드코딩 금지, 조치 절차 4).
        """
        self.slept.append(seconds)
        self.now += max(seconds, self.sleep_floor)

    @property
    def elapsed(self) -> float:
        return self.now - self.start


def install(monkeypatch, *modules, start: float = 1000.0, sleep_floor: float = 0.0) -> FakeClock:
    """`modules`가 보는 `time`을 하나의 가상 시계로 바꾼다.

    여러 모듈을 함께 넘기면 **같은** 시계를 공유한다 — 예산(`budget`)과
    양보(`approx`)처럼 한쪽이 잰 시간을 다른 쪽이 상환하는 관계에서는
    시계가 갈라지면 아무것도 못 잰다.
    """
    clock = FakeClock(start=start, sleep_floor=sleep_floor)
    for module in modules:
        assert hasattr(module, "time"), f"{module.__name__}에 time이 없다"
        monkeypatch.setattr(module, "time", clock)
    return clock


def assert_close(actual: float, expected: float, *, what: str = "") -> None:
    """가상 시계에서는 부동소수 오차만 허용한다 — 스케줄링 여유는 없다."""
    assert actual == pytest.approx(expected, abs=1e-9), (
        f"{what}: {actual * 1000:.3f}ms (기대 {expected * 1000:.3f}ms)"
    )
