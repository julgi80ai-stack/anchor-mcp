# SPDX-License-Identifier: Apache-2.0
"""로컬 마이크로 벤치마크 — SPEC §10 비기능 지표를 네트워크 없이 잰다.

게이트 (초과 시 종료 코드 1):
  - 캐시 히트 응답 p95 < 15 ms (본문 1MB 이하)
  - 앵커 매칭 최악 사례 p99 < 250 ms, 전체 정지 없음
  - 정상 코퍼스 UNRESOLVED 비율 < 1%
  - 재검증 처리량 500 앵커 / 60초 (네트워크 제외)
"""

from __future__ import annotations

import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from anchor.anchoring.matcher import UNRESOLVED, match_anchor  # noqa: E402
from anchor.anchoring.selector import build_selector  # noqa: E402
from anchor.config import Config  # noqa: E402
from anchor.models import utcnow_iso  # noqa: E402
from anchor.normalize.hashing import hash_text  # noqa: E402
from anchor.service import Anchor  # noqa: E402

FAILURES: list[str] = []


def gate(name: str, condition: bool, detail: str) -> None:
    print(f"{'PASS' if condition else 'FAIL'}  {name}: {detail}")
    if not condition:
        FAILURES.append(name)


def judge_cache_hit(p95: float, floor: float, budget: float = 15.0) -> tuple[bool, str]:
    """cache_hit 게이트 판정 — 어떤 기계에서도 회귀는 잡되, 디스크를 코드로
    오인하지 않는다 (D-189).

    커밋 바닥(floor)이 예산 안이면 절대 기준으로 판정한다. 바닥이 이미
    예산을 넘는 기계에서는 **바닥을 뺀 순비용**으로 판정한다 — "판정 불가"로
    통째로 건너뛰면 예산의 7배짜리 회귀도 SKIP 뒤에 숨고, 마지막 줄의
    "모든 게이트 통과"가 근거 없는 문장이 된다.
    """
    if floor < budget:
        return (
            p95 < budget,
            f"p95={p95:.2f}ms (절대 기준, 커밋 바닥 {floor:.2f}ms)",
        )
    net = p95 - floor
    return (
        net < budget,
        f"순비용={net:.2f}ms (p95={p95:.2f}ms − 바닥 {floor:.2f}ms; "
        f"바닥이 예산 {budget:.0f}ms를 넘는 기계라 순비용 기준)",
    )


def commit_floor_ms() -> float:
    """이 기계의 SQLite 커밋 p95. 캐시 히트 경로의 바닥이다.

    `fetch`는 회계 한 줄을 남기며 커밋 한 번을 한다. 그 커밋의 fsync가
    예산을 통째로 먹는 기계에서는 게이트가 코드가 아니라 디스크를 잰다 —
    실측으로 같은 커밋에서 2.5ms와 22ms가 함께 나왔다.
    """
    import sqlite3

    with tempfile.TemporaryDirectory() as tmp:
        connection = sqlite3.connect(Path(tmp) / "floor.db")
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE t (a INTEGER)")
            connection.commit()
            samples = []
            for _ in range(100):
                started = time.perf_counter()
                connection.execute("INSERT INTO t VALUES (1)")
                connection.commit()
                samples.append((time.perf_counter() - started) * 1000)
        finally:
            connection.close()
    return percentile(samples, 0.95)


def percentile(samples: list[float], p: float) -> float:
    ordered = sorted(samples)
    return ordered[min(len(ordered) - 1, int(len(ordered) * p))]


def bench_cache_hit() -> None:
    """네트워크 없이 캐시 히트 경로만 잰다 — 저장소를 직접 시딩한다."""
    text = ("캐시 히트 지연 측정을 위한 본문 문장이다. " * 40 + "\n\n") * 120  # 약 100KB
    with tempfile.TemporaryDirectory() as tmp:
        with Anchor(db_path=Path(tmp) / "bench.db", config=Config(db_path=Path(tmp) / "bench.db")) as ax:
            now = utcnow_iso()
            doc = ax._repository.create_document(
                url="https://bench.invalid/doc", original_url="https://bench.invalid/doc",
                title=None, now=now,
            )
            ax._repository.insert_version(
                document_id=doc.id, text_hash=hash_text(text), raw_hash="b3:raw",
                pipeline_version="bench/1", captured_at=now, byte_size=len(text),
                normalized_text=text, http_status=200,
            )
            samples = []
            for _ in range(100):
                start = time.perf_counter()
                result = ax.fetch("https://bench.invalid/doc")
                samples.append((time.perf_counter() - start) * 1000)
                assert result.outcome == "cache_hit"
    p95 = percentile(samples, 0.95)
    floor = commit_floor_ms()
    ok, detail = judge_cache_hit(p95, floor)
    gate("cache_hit p95 < 15ms", ok, detail + " (n=100, 본문 ~100KB)")


def bench_cache_hit_under_load() -> None:
    """백그라운드 verify의 매칭 루프가 도는 동안의 캐시 히트 — Tasks 확장이
    존재하는 바로 그 상황이다 (D-120). 유휴 벤치만 있으면 매칭 루프의 GIL
    점유 회귀가 CI를 통과한다: 실측 p95 72~100ms(게이트의 6배)가 그렇게
    숨어 있었다."""
    import threading

    text = ("캐시 히트 지연 측정을 위한 본문 문장이다. " * 40 + "\n\n") * 120
    churn_text = " ".join(
        f"채움 문단 {j}: 앵커와 무관한 서술이 이어지고 숫자 {j * 13}이 등장한다."
        for j in range(2500)
    )
    missing_quote = (
        "근거 문장은 서로 다른 사실을 담고 숫자 3700과 영어 조각 "
        "fragment-100alpha 를 함께 품으며 길이도 상당히 길다"
    )
    stop = threading.Event()
    # build_server와 같은 배치 — 서버 프로세스는 스위치 간격을 1ms로 줄인다.
    # 이 벤치는 그 프로세스의 상황을 재는 것이므로 같은 설정에서 잰다.
    previous_interval = sys.getswitchinterval()
    sys.setswitchinterval(0.001)

    churn_rounds = [0]

    def churn() -> None:
        from anchor.anchoring.approx import set_thread_yields

        set_thread_yields(True)  # 서버의 배경 워커와 같은 배치 (D-120)
        while not stop.is_set():
            match_anchor(
                churn_text, exact=missing_quote, prefix="앞", suffix="뒤",
                position_hint=1000, budget_ms=300,
            )
            churn_rounds[0] += 1

    with tempfile.TemporaryDirectory() as tmp:
        with Anchor(db_path=Path(tmp) / "bench.db", config=Config(db_path=Path(tmp) / "bench.db")) as ax:
            now = utcnow_iso()
            doc = ax._repository.create_document(
                url="https://bench.invalid/doc", original_url="https://bench.invalid/doc",
                title=None, now=now,
            )
            ax._repository.insert_version(
                document_id=doc.id, text_hash=hash_text(text), raw_hash="b3:raw",
                pipeline_version="bench/1", captured_at=now, byte_size=len(text),
                normalized_text=text, http_status=200,
            )
            worker = threading.Thread(target=churn)
            worker.start()
            time.sleep(0.1)
            try:
                samples = []
                for _ in range(200):  # 이 머신의 fsync 꼬리 변동을 p95가 견디도록 n을 넉넉히
                    start = time.perf_counter()
                    result = ax.fetch("https://bench.invalid/doc")
                    samples.append((time.perf_counter() - start) * 1000)
                    assert result.outcome == "cache_hit"
            finally:
                died_early = not worker.is_alive()
                stop.set()
                worker.join()
                sys.setswitchinterval(previous_interval)
    # 부하가 실제로 있었는지 게이트가 스스로 확인한다 (D-198). churn이
    # 임포트 오류 등으로 즉사하면 이 벤치는 유휴를 재면서 PASS를 찍는다 —
    # 지키려는 회귀(양보 기제 소실)가 churn 쪽을 깨는 방식이면 무력화된다.
    gate(
        "cache_hit(부하 중) 부하 유효성",
        churn_rounds[0] >= 3 and not died_early,
        f"매칭 {churn_rounds[0]}회, 조기 사망={died_early} — 부하 없이 잰 수치는 판정이 아니다",
    )
    p95 = percentile(samples, 0.95)
    floor = commit_floor_ms()
    ok, detail = judge_cache_hit(p95, floor)
    gate("cache_hit(부하 중) p95 < 15ms", ok, detail + " (n=200, 매칭 루프 동시 실행)")


def bench_matcher_worst_case(polite: bool = False) -> int:
    """polite=True면 배경 워커와 같은 배치로 잰다 (D-196). 판정 게이트가
    전경에서만 돌면 배경 경로의 판정 열화를 못 본다 — D-120이 "유휴만 재면
    숨는다"였던 것과 같은 구조다. 반환: UNRESOLVED 수."""
    from anchor.anchoring.approx import set_thread_yields

    suffix = " (정중 모드)" if polite else ""
    big_text = "채움 문장이 끝없이 이어지는 대폭 개편 문서다. " * 20000  # ~50만 자
    samples = []
    unresolved = 0
    if polite:
        set_thread_yields(True)
    try:
        for index in range(100):
            start = time.perf_counter()
            result = match_anchor(
                big_text,
                exact=f"이 문서 어디에도 없는 인용문 {index}번이다, 확실히.",
                prefix="존재하지 않는 앞 문맥",
                suffix="존재하지 않는 뒤 문맥",
                position_hint=len(big_text) // 2,
                budget_ms=200,
            )
            samples.append((time.perf_counter() - start) * 1000)
            if result.state == UNRESOLVED:
                unresolved += 1
    finally:
        if polite:
            set_thread_yields(False)
    p99 = percentile(samples, 0.99)
    gate(f"최악 사례{suffix} p99 < 250ms", p99 < 250.0, f"p99={p99:.1f}ms, UNRESOLVED {unresolved}/100")
    return unresolved


def bench_normal_corpus(polite: bool = False) -> None:
    from anchor.anchoring.approx import set_thread_yields

    suffix = " (정중 모드)" if polite else ""
    golden = Path(__file__).parent.parent / "tests" / "fixtures" / "golden"
    texts = [p.read_text("utf-8") for p in sorted(golden.glob("*.expected.md"))]
    anchors = []
    for text in texts:
        for start in range(0, max(1, len(text) - 60), max(1, len(text) // 6)):
            quote = text[start : start + 48].strip()
            if len(quote) >= 12:
                try:
                    anchors.append((text, build_selector(text, quote)))
                except Exception:
                    continue
    anchors = (anchors * (500 // len(anchors) + 1))[:500]

    started = time.perf_counter()
    unresolved = 0
    if polite:
        set_thread_yields(True)
    try:
        for text, selector in anchors:
            result = match_anchor(
                text,
                exact=selector.exact,
                prefix=selector.prefix,
                suffix=selector.suffix,
                position_hint=selector.position_hint,
                budget_ms=200,
            )
            if result.state == UNRESOLVED:
                unresolved += 1
    finally:
        if polite:
            set_thread_yields(False)
    elapsed = time.perf_counter() - started
    rate = unresolved / len(anchors)
    gate(f"500 앵커 / 60초{suffix}", elapsed < 60.0, f"{len(anchors)}건 {elapsed:.2f}s")
    gate(f"UNRESOLVED < 1%{suffix}", rate < 0.01, f"{unresolved}/{len(anchors)} ({rate:.2%})")


def main() -> int:
    bench_cache_hit()
    bench_cache_hit_under_load()
    foreground_unresolved = bench_matcher_worst_case()
    polite_unresolved = bench_matcher_worst_case(polite=True)
    # 판정은 호출 경로와 무관해야 한다 (D-196). 양보 시간이 예산에서
    # 청구되면 여기가 0/100 vs 59~60/100으로 갈린다.
    gate(
        "최악 사례 판정의 경로 무관성",
        abs(polite_unresolved - foreground_unresolved) <= 15,
        f"전경 UNRESOLVED {foreground_unresolved}/100 vs 정중 모드 {polite_unresolved}/100",
    )
    bench_normal_corpus()
    bench_normal_corpus(polite=True)
    if FAILURES:
        print(f"\n게이트 실패: {', '.join(FAILURES)}", file=sys.stderr)
        return 1
    print("\n모든 게이트 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
