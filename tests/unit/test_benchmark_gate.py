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

import pytest

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


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_under_load_gate_fails_when_churn_dies(monkeypatch):
    """D-198: 부하 게이트는 부하가 실제로 있었는지 스스로 확인해야 한다.

    churn 스레드가 즉사해도 PASS·exit 0이면, 이 게이트가 지키려는 회귀
    (양보 기제 소실)가 churn 쪽 임포트·시그니처를 깨는 순간 게이트가
    스스로 무력화된다 — 실증: ImportError로 즉사하는 사본에서 PASS 6.27ms.
    """
    import run_micro

    def boom(*args, **kwargs):
        raise RuntimeError("churn 사망 재현")

    monkeypatch.setattr(run_micro, "match_anchor", boom)
    monkeypatch.setattr(run_micro, "FAILURES", [])
    run_micro.bench_cache_hit_under_load()
    assert run_micro.FAILURES, "churn이 죽었는데 부하 게이트가 통과했다"
