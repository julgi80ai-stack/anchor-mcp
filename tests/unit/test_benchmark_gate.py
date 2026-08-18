# SPDX-License-Identifier: Apache-2.0
"""§10 게이트의 판정 논리 (D-189).

게이트가 "판정 불가"를 실패도 통과도 아닌 제3의 상태로 두면, 그 뒤의
"모든 게이트 통과"가 근거 없는 문장이 된다 — 예산의 7배짜리 회귀가
SKIP 뒤에 숨는 것을 실측으로 확인했다. 판정은 항상 내리되, 기계의 커밋
바닥은 빼고 잰다.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "benchmarks"))

from run_micro import judge_cache_hit  # noqa: E402


def test_fast_machine_uses_the_absolute_budget():
    ok, _ = judge_cache_hit(p95=10.0, floor=2.0)
    assert ok
    ok, _ = judge_cache_hit(p95=18.0, floor=2.0)
    assert not ok, "빠른 기계의 회귀가 통과됐다"


def test_slow_disk_judges_the_net_cost():
    """바닥이 예산을 넘는 기계에서도 판정은 **내린다** — 건너뛰지 않는다."""
    ok, detail = judge_cache_hit(p95=18.0, floor=17.0)
    assert ok, f"디스크 바닥을 코드 회귀로 오인했다: {detail}"
    ok, detail = judge_cache_hit(p95=118.0, floor=17.0)
    assert not ok, f"예산의 7배 회귀가 판정을 빠져나갔다: {detail}"
