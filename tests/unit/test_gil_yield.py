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

import anchor.anchoring.budget as budget_module
from anchor.anchoring import approx, matcher
from anchor.anchoring.budget import _CREDIT_CAP_FRACTION, Budget
from tests import fake_clock


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
    잠든 만큼 예산을 뒤로 민다(판정은 호출 경로와 무관해야 한다).

    D-224: 예산과 양보가 **같은** 가상 시계를 본다. 예전에는 진짜로 20ms를
    자고 허용오차 5ms로 쟀는데, 그 5ms는 잠듦이 아니라 잠든 전후의 재스케줄
    지연을 덮는 값이라 부하 걸린 러너에서 부족했다(macOS 3.11 실측 5.5ms
    초과 → 빨강). 상환은 시간이 아니라 **뺄셈**이므로 뺄셈으로 잰다 —
    허용오차가 0이 되어 "조금씩 새는" 회귀까지 잡는다.
    """
    clock = fake_clock.install(monkeypatch, approx, budget_module, sleep_floor=0.020)
    approx._yield_state.last = 0.0
    approx.set_thread_yields(True)
    try:
        with approx.foreground_section():
            budget = Budget(1000.0)
            before = budget.remaining_seconds()
            approx._yield_gil(budget)
            after = budget.remaining_seconds()
        assert clock.slept == [0], f"배경 워커가 양보로 잠들지 않았다 ({clock.slept})"
        fake_clock.assert_close(after, before, what="양보 전후의 남은 예산")
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


def test_budget_credit_is_capped(monkeypatch):
    """D-203: 크레딧 무상한은 "앵커 하나의 예산"을 탄력적으로 만든다 —
    경합 실측 앵커당 최대 971ms(예산의 4.9배), 서비스 계층 p99 345·478ms로
    §10 "최악 사례 p99 < 250ms" 위반. D-196이 없앤 판정의 경로 의존이
    시간의 경로 의존으로 옮겨온 것. 크레딧 총량은 **예산의 15%**로 묶는다 —
    그 너머의 잠듦은 예산을 먹고, 경계 문서는 UNRESOLVED로 보류된다(실측된
    경합의 정직한 보고, SPEC §10).

    D-219: 단언을 상수에서 **유도**한다. 예전의 고정 구간(0.010~0.030)은
    15%·20%·25%가 모두 통과해 문서화된 상수를 하나도 고정하지 못했다 —
    상수를 바꿔도 빨개지지 않는 회귀선은 회귀선이 아니다. 그래서 값 자체를
    먼저 못박고(SPEC §10·D-203이 적은 수치), 행동은 그 값에서 유도한다.
    """
    assert _CREDIT_CAP_FRACTION == 0.15, (
        "SPEC §10과 D-203이 적은 상한은 예산의 15%다 — 상수를 바꾸려면 "
        "SPEC·주석·이 시험을 함께 고쳐야 한다"
    )

    budget_ms = 100.0
    cap = budget_ms / 1000.0 * _CREDIT_CAP_FRACTION
    fake_clock.install(monkeypatch, budget_module)  # D-224: 지터 여유를 없앤다
    budget = Budget(budget_ms)
    r0 = budget.remaining_seconds()
    budget.credit(cap * 10)  # 상한의 열 배를 요청해도 상한까지만 인정된다
    r1 = budget.remaining_seconds()
    # 예전에는 벽시계로 재느라 5ms의 지터 여유를 뒀다. 그 여유는 상한이
    # 5% 어긋나도 초록이고(100ms 예산의 5ms), 부하 걸린 러너에서는 반대로
    # 회귀 없이 빨개진다 — 시계를 멈추면 양쪽 다 사라진다.
    fake_clock.assert_close(r1 - r0, cap, what="인정된 크레딧")
    budget.credit(cap * 10)  # 상한 소진 후에는 더 밀리지 않는다
    r2 = budget.remaining_seconds()
    fake_clock.assert_close(r2 - r1, 0.0, what="상한 소진 후의 추가 연장")


def test_all_public_service_methods_are_foreground_marked(tmp_path):
    """D-204·D-207: `@_foreground` 10곳을 전부 제거해도 609 테스트가 통과했다
    — 조치의 서비스 계층 절반에 회귀선이 없었다. 구조(래퍼 존재)와 행동
    (호출 시 전경 구간 개방, 정중 스레드는 제외)을 함께 고정한다."""
    from contextlib import contextmanager

    from anchor.config import Config
    from anchor.service import Anchor

    public_api = [
        "fetch", "cite", "verify", "diff_versions", "get_version",
        "get_version_text", "list_documents", "cache_stats", "get_timemap",
        "export_robust_links", "collect_garbage",
    ]
    for name in public_api:
        assert hasattr(getattr(Anchor, name), "__wrapped__"), (
            f"Anchor.{name}에 @_foreground가 없다 — 배경 워커가 이 호출을 위해 "
            "양보하지 않는다"
        )

    calls = {"n": 0}

    @contextmanager
    def spy():
        calls["n"] += 1
        yield

    import anchor.anchoring.approx as approx_module
    original = approx_module.foreground_section
    approx_module.foreground_section = spy
    try:
        with Anchor(db_path=tmp_path / "fg.db", config=Config(db_path=tmp_path / "fg.db")) as ax:
            ax.list_documents()
            assert calls["n"] == 1, "전경 호출이 전경 구간을 열지 않았다"
            approx_module.set_thread_yields(True)
            try:
                ax.list_documents()
            finally:
                approx_module.set_thread_yields(False)
            assert calls["n"] == 1, "정중(배경) 스레드의 재진입이 전경으로 표시됐다"
    finally:
        approx_module.foreground_section = original
