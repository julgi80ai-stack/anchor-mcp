# SPDX-License-Identifier: Apache-2.0
"""매칭 루프의 GIL 양보 (D-120).

백그라운드 verify의 매칭 루프는 순수 파이썬이라 GIL 슬라이스를 통째로 쥐고,
캐시 히트 경로는 GIL을 수십 번 얻어야 하므로 대기가 획득 횟수만큼 누적된다
— 실측 p95 87~100ms(§10 게이트 15ms의 6배). 조치는 예산 확인 주기마다의
명시적 양보이고, 여기서는 **양보가 실제로 일어나는지**를 타이밍 없이
확인한다. 지연의 게이트 판정 자체는 `benchmarks/run_micro.py`의
`bench_cache_hit_under_load`가 맡는다 (유휴 벤치만 있으면 이 회귀는 CI를
통과한다 — D-120이 살아남은 이유).
"""

from __future__ import annotations

import time

from anchor.anchoring import approx, matcher
from anchor.anchoring.budget import Budget


def _count_yields(monkeypatch) -> dict:
    calls = {"n": 0}
    monkeypatch.setattr(
        approx, "_yield_gil", lambda *a, **k: calls.__setitem__("n", calls["n"] + 1)
    )
    return calls


def test_missing_long_quote_scan_yields(monkeypatch):
    """부재 장문 인용(k>3 → myers 경로)의 전체 스캔이 주기적으로 양보해야 한다."""
    calls = _count_yields(monkeypatch)
    text = " ".join(
        f"채움 문단 {j}: 앵커와 무관한 서술이 이어지고 숫자 {j * 13}이 등장한다."
        for j in range(2500)
    )
    quote = (
        "근거 문장은 서로 다른 사실을 담고 숫자 3700과 영어 조각 "
        "fragment-100alpha 를 함께 품으며 길이도 상당히 길다"
    )
    result = matcher.match_anchor(
        text, exact=quote, prefix="앞맥락", suffix="뒷맥락",
        position_hint=1000, budget_ms=1000,
    )
    assert result.state in (matcher.MISSING, matcher.UNRESOLVED)
    # 스캔 1024자마다 1회 이상 — 110KB 텍스트 × 코어 2개면 200회를 훌쩍 넘는다.
    assert calls["n"] >= 50, f"양보 {calls['n']}회 — 매칭 루프가 GIL을 독점한다"


def test_bounded_edit_distance_yields(monkeypatch):
    """DP 경로(문맥 후보 비교)도 셀 주기마다 양보해야 한다."""
    calls = _count_yields(monkeypatch)
    a = "가나다라마바사아자차카타파하 abcdefg 0123456789 " * 12  # ~540자
    b = a[:-40] + "완전히 다른 꼬리가 붙는다 완전히 다른 꼬리가 붙는다"
    approx.bounded_edit_distance(a, b, k=64, budget=None)
    assert calls["n"] >= 10, f"양보 {calls['n']}회 — DP 루프가 GIL을 독점한다"


def test_yield_is_gated_by_thread_politeness(monkeypatch):
    """양보 비용(슬립당 84µs)은 배경 워커만 낸다 — 전경 호출(동기 도구·CLI·
    단독 벤치)이 내면 순손실이고, 실제로 최악 사례 벤치의 MISSING 판정을
    UNRESOLVED로 전락시켰다(6/100 → 100/100)."""
    sleeps = {"n": 0}
    monkeypatch.setattr("time.sleep", lambda s: sleeps.__setitem__("n", sleeps["n"] + 1))
    approx._yield_state.last = 0.0
    try:
        with approx.foreground_section():  # 굶는 전경 호출이 있는 상황
            approx.set_thread_yields(False)
            approx._yield_gil()
            assert sleeps["n"] == 0, "전경 스레드가 양보 비용을 냈다"
            approx.set_thread_yields(True)
            approx._yield_gil()
            assert sleeps["n"] == 1, "배경 워커의 양보가 일어나지 않았다"
    finally:
        approx.set_thread_yields(False)


def test_yield_time_is_not_charged_to_the_anchor_budget(monkeypatch):
    """D-196: 배경 워커가 양보로 잠든 시간이 앵커 예산에서 청구되면 같은
    앵커가 동기 경로에서는 MISSING, task 경로에서는 UNRESOLVED가 된다 —
    §7.3이 task를 기본 경로로 정하므로 낮은 한계가 기본이 된다. 양보는
    잠든 만큼 예산을 뒤로 민다(판정은 호출 경로와 무관해야 한다)."""
    real_sleep = time.sleep
    monkeypatch.setattr("time.sleep", lambda s: real_sleep(0.02))  # 양보가 20ms를 잠들었다고 치자
    approx._yield_state.last = 0.0
    approx.set_thread_yields(True)
    try:
        with approx.foreground_section():
            budget = Budget(1000.0)
            before = budget.remaining_seconds()
            approx._yield_gil(budget)
            after = budget.remaining_seconds()
        assert after > before - 0.005, (
            f"양보로 잠든 20ms가 예산에서 빠졌다 (남은 예산 {before*1000:.1f}ms → {after*1000:.1f}ms)"
        )
    finally:
        approx.set_thread_yields(False)


def test_no_sleep_when_no_foreground_call_is_active(monkeypatch):
    """D-196: 굶는 전경 호출이 없으면 배경 워커는 잠들지 않는다.

    잠듦의 참비용(재스케줄·콜드 캐시 재예열)은 측정창 밖에서 새어
    크레딧으로 상환되지 않는다 — 무의미한 양보가 정중 모드 작업을 +12%
    부풀려 경계 문서의 판정을 UNRESOLVED로 밀었다(최악 사례 30/100).
    양보가 잠드는 것은 `foreground_section`이 열려 있는 동안뿐이다.
    """
    sleeps = {"n": 0}
    monkeypatch.setattr("time.sleep", lambda s: sleeps.__setitem__("n", sleeps["n"] + 1))
    approx._yield_state.last = 0.0
    approx.set_thread_yields(True)
    try:
        approx._yield_gil(Budget(1000.0))
        assert sleeps["n"] == 0, "전경 호출이 없는데 배경 워커가 잠들었다"
    finally:
        approx.set_thread_yields(False)
