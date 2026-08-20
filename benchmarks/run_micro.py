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
from anchor.errors import AnchorError  # noqa: E402
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

    커밋 바닥(floor)이 예산의 절반 안이면 절대 기준으로 판정한다. 그보다
    크면 **바닥을 뺀 순비용**으로 판정한다 — "판정 불가"로 통째로 건너뛰면
    예산의 7배짜리 회귀도 SKIP 뒤에 숨고, 마지막 줄의 "모든 게이트 통과"가
    근거 없는 문장이 된다 (D-189).

    경계가 예산 그 자체였을 때의 불연속 (D-209): 바닥 14.91ms에서는 절대
    기준이 비커밋 작업에 0.09ms만 허용해 FAIL, 15.29ms에서는 순비용으로
    넉넉히 PASS였다 — 같은 주변 조건의 A/B(현재 vs 부모 커밋 교차 실행)로
    코드 무회귀를 확인한 그 실행에서다. 바닥이 예산의 절반을 넘으면 이미
    기계 상태가 지배 변수다.
    """
    if floor < budget / 2:
        return (
            p95 < budget,
            f"p95={p95:.2f}ms (절대 기준, 커밋 바닥 {floor:.2f}ms)",
        )
    net = p95 - floor
    return (
        net < budget,
        f"순비용={net:.2f}ms (p95={p95:.2f}ms − 바닥 {floor:.2f}ms; "
        f"바닥이 예산 {budget:.0f}ms의 절반을 넘어 기계 상태가 지배 변수 — 순비용 기준)",
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
                rounds_before_samples = churn_rounds[0]
                for _ in range(200):  # 이 머신의 fsync 꼬리 변동을 p95가 견디도록 n을 넉넉히
                    start = time.perf_counter()
                    result = ax.fetch("https://bench.invalid/doc")
                    samples.append((time.perf_counter() - start) * 1000)
                    assert result.outcome == "cache_hit"
            finally:
                rounds_during_samples = churn_rounds[0] - rounds_before_samples
                died_early = not worker.is_alive()
                stop.set()
                worker.join()
                sys.setswitchinterval(previous_interval)
    # 부하가 실제로 있었는지 게이트가 스스로 확인한다 (D-198). 총 라운드
    # 수가 아니라 **표본 구간과 겹친** 라운드를 센다 (D-208) — 표본 전에만
    # 돌다 죽은 churn은 부하가 아니다.
    gate(
        "cache_hit(부하 중) 부하 유효성",
        rounds_during_samples >= 1 and not died_early,
        f"표본 구간 매칭 {rounds_during_samples}회(총 {churn_rounds[0]}회), "
        f"조기 사망={died_early} — 부하 없이 잰 수치는 판정이 아니다",
    )
    p95 = percentile(samples, 0.95)
    floor = commit_floor_ms()
    ok, detail = judge_cache_hit(p95, floor)
    gate("cache_hit(부하 중) p95 < 15ms", ok, detail + " (n=200, 매칭 루프 동시 실행)")


def bench_matcher_worst_case() -> int:
    """반환: UNRESOLVED 수 — 경합 벤치가 같은 실행의 전경 기준선으로 쓴다."""
    big_text = "채움 문장이 끝없이 이어지는 대폭 개편 문서다. " * 20000  # ~50만 자
    samples = []
    unresolved = 0
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
    p99 = percentile(samples, 0.99)
    gate("최악 사례 p99 < 250ms", p99 < 250.0, f"p99={p99:.1f}ms, UNRESOLVED {unresolved}/100")
    return unresolved


def bench_matcher_under_contention(foreground_unresolved: int) -> None:
    """배경 배치(정중 모드)의 매칭을 **전경 호출이 실제로 진행 중인 동안** 잰다
    (D-202/D-203).

    정중 모드를 켜기만 하고 전경 구간을 열지 않으면 양보는 한 번도 잠들지
    않는다(전경 조건부, D-196) — 그런 "정중 모드 게이트"는 전경 실행과
    바이트 동일한 항진식이었다. 전경은 **GIL 상주 작업**으로 만든다 — 같은
    프로파일의 실경로가 있다: task가 도는 동안 들어온 동기 verify_citations
    (전경 표시 아래의 매칭 루프). fetch 루프는 GIL을 자주 놓아(sqlite·fsync)
    잠듦이 짧게 끝나므로 무상한 크레딧의 회귀를 드러내지 못했다(실측 240ms).

    재는 것: ①경합 중에도 앵커 하나의 벽시계가 §10 상한 안인가 — 크레딧
    상한 (D-203, 무상한이면 실측 p99 306~971ms) ②경합의 판정 비용
    (UNRESOLVED)이 전경 기준선 대비 한도 안인가 — 크레딧 소실 회귀 검출.
    """
    import threading

    from anchor.anchoring import budget as budget_mod
    from anchor.anchoring.approx import foreground_section, set_thread_yields

    big_text = "채움 문장이 끝없이 이어지는 대폭 개편 문서다. " * 20000
    small_text = "전경 재검증을 모사하는 짧은 본문 문장이 이어진다. " * 400
    stop = threading.Event()
    foreground_rounds = [0]
    # 크레딧 **실지급**을 계측한다 (D-210). 크레딧 소실 회귀는 UNRESOLVED
    # 증분으로 잡을 수 없다: 유휴 기계에서는 건강한 코드도 연속 전경에
    # 굶어 100/100이 정직한 값이고(증분 게이트는 통과 불가), 소실되면
    # 데드라인이 안 늘어나 p99는 오히려 내려간다(p99 게이트도 침묵).
    # 갚았는가는 갚은 양으로 잰다.
    credit_granted = [0.0]
    original_credit = budget_mod.Budget.credit

    def counting_credit(self, seconds: float) -> None:
        before = self._credit_left
        original_credit(self, seconds)
        credit_granted[0] += before - self._credit_left

    budget_mod.Budget.credit = counting_credit
    previous_interval = sys.getswitchinterval()
    sys.setswitchinterval(0.001)  # 서빙 배치와 동일 (serve_forever)
    try:

        def foreground() -> None:
            # 동기 verify를 모사: 전경 구간 안의 짧은 매칭 버스트.
            while not stop.is_set():
                with foreground_section():
                    # 인용문이 길어야(k>3) myers 경로 — 순수 파이썬이라 GIL에
                    # 상주한다. 짧으면 regex(C) 경로가 GIL을 놓아 배경의
                    # 잠듦이 즉시 끝나고, 크레딧 회귀가 드러나지 않는다.
                    match_anchor(
                        small_text,
                        exact="전경 경로의 재검증을 모사하기 위한 제법 긴 인용문이며 이 본문 어디에도 존재하지 않는다",
                        prefix="존재하지 않는 앞",
                        suffix="존재하지 않는 뒤",
                        position_hint=0,
                        budget_ms=5,
                    )
                foreground_rounds[0] += 1

        worker = threading.Thread(target=foreground)
        worker.start()
        time.sleep(0.05)
        samples = []
        unresolved = 0
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
            set_thread_yields(False)
            died_early = not worker.is_alive()
            stop.set()
            worker.join()
    finally:
        sys.setswitchinterval(previous_interval)
        budget_mod.Budget.credit = original_credit
    gate(
        "최악 사례(경합 중) 전경 유효성",
        foreground_rounds[0] >= 100 and not died_early,
        f"전경 버스트 {foreground_rounds[0]}회, 조기 사망={died_early} — 전경 없이 "
        "정중 모드만 켠 측정은 항진식이다",
    )
    p99 = percentile(samples, 0.99)
    gate(
        "최악 사례(경합 중) p99 < 250ms",
        p99 < 250.0,
        f"p99={p99:.1f}ms — 크레딧 무상한이면 예산이 경합에 비례해 늘어난다 (D-203)",
    )
    # UNRESOLVED 증분 게이트(≤40)는 걷어냈다 (D-210): 재는 것(경합의 판정
    # 비용)과 잡으려는 회귀(크레딧 소실)가 어긋나 있었다. 연속 전경 아래의
    # 굶주림은 §10이 명문화한 정직한 보고라 유휴 기계에서 건강한 코드가
    # 100/100을 찍고(전경 기준선 0 — 게이트 통과 불가), 부하 기계에서는
    # 기준선까지 포화해 델타가 0이 된다(무엇도 잡지 못하는 통과). 5단계
    # 종결의 PASS는 후자였다. 소실 회귀는 **갚은 양**으로 직접 잰다 —
    # 기제가 살아 있으면 양수, 소실되면 정확히 0. 기계 무관.
    gate(
        "최악 사례(경합 중) 크레딧 실지급",
        credit_granted[0] * 1000 >= 1.0,
        f"실지급 {credit_granted[0] * 1000:.1f}ms — 소실이면 정확히 0이 된다. "
        f"(정보) 경합 UNRESOLVED {unresolved}/100 vs 전경 {foreground_unresolved}/100"
        " — 연속 전경 아래의 굶주림은 정직한 보고다 (SPEC §10)",
    )


def bench_normal_corpus() -> None:
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
    elapsed = time.perf_counter() - started
    rate = unresolved / len(anchors)
    gate("500 앵커 / 60초", elapsed < 60.0, f"{len(anchors)}건 {elapsed:.2f}s")
    gate("UNRESOLVED < 1%", rate < 0.01, f"{unresolved}/{len(anchors)} ({rate:.2%})")


def bench_failure_is_fast() -> None:
    """실패는 그것을 안 순간 보고돼야 한다 (SPEC §10 v1.12).

    이 도구는 AI 에이전트의 도구 호출 경로에 있다 — 우리가 늦으면 그 지연이
    사람에게 그대로 간다. 정확성 회귀를 CI로 막았듯 **채택 가능성의 회귀도**
    같은 방식으로 막는다: 맞는 답을 줘도 느리면 쓰이지 않고, 쓰이지 않는
    무결성 계층은 아무것도 지키지 못한다.

    네트워크 없이 로컬 픽스처 서버로 잰다 — 기계 편차에 강하고, 재시도
    기제가 되살아나면 **그 실패 종류만** 정확히 빨개진다. 조치 전 실측:
    403이 7,010ms(재시도 3회 × 백오프 1+2+4초), `Retry-After: 1`을 준 429가
    7,015ms(우리 백오프가 서버 지시를 덮어씀). 나머지 실패는 전부 20ms 안이라
    이 둘만이 지연의 원인이었다.
    """
    import json as _json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    state = {"status": 404, "robots": "User-agent: *\nAllow: /\n", "retry_after": None}
    hits: list[tuple[str, float]] = []  # (경로, 시각) — 정중함 게이트가 읽는다

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # 벤치 출력에 섞이지 않게
            pass

        def do_GET(self):
            hits.append((self.path, time.perf_counter()))
            if self.path == "/robots.txt":
                body = state["robots"].encode()
                self.send_response(200)
            else:
                body = b"<html><body><p>" + b"x" * 400 + b"</p></body></html>"
                self.send_response(state["status"])
                if state["retry_after"] is not None:
                    self.send_header("Retry-After", state["retry_after"])
                self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    # (이름, 상태, robots, Retry-After, 상한 ms). 상한은 실측 기준선(전부
    # 20ms 안)에 네트워크 없는 루프백과 SQLite 커밋의 여유를 얹은 값이다.
    # 서버가 대기를 지정한 429만 그 지정을 존중하므로 예외로 둔다.
    cases = [
        ("404", 404, "User-agent: *\nAllow: /\n", None, 500.0),
        ("500", 500, "User-agent: *\nAllow: /\n", None, 500.0),
        ("403(맨몸)", 403, "User-agent: *\nAllow: /\n", None, 500.0),
        ("robots 거부", 200, "User-agent: *\nDisallow: /\n", None, 500.0),
        ("429(Retry-After:1)", 429, "User-agent: *\nAllow: /\n", "1", 4000.0),
        # 403은 `Retry-After`가 실렸을 때만 재시도한다 (D-237). 맨몸 403과
        # 갈리는 유일한 축인데 아무도 재지 않았다 — SPEC §10과 코드가 어긋난
        # 자리가 정확히 여기였다 (D-276).
        ("403(Retry-After:1)", 403, "User-agent: *\nAllow: /\n", "1", 4000.0),
        # 상한(60s)보다 긴 대기를 지정하면 재시도하지 않고 즉시 보고한다.
        ("403(Retry-After:3600)", 403, "User-agent: *\nAllow: /\n", "3600", 500.0),
    ]
    try:
        with tempfile.TemporaryDirectory() as tmp:
            for index, (label, status, robots, retry_after, ceiling) in enumerate(cases):
                state.update(status=status, robots=robots, retry_after=retry_after)
                # **케이스마다 새 저장소**다. robots는 오리진 단위로 24시간
                # 캐시되므로(SPEC §5.2) 한 DB를 공유하면 앞 케이스가 캐시해 둔
                # `Allow`가 뒤의 `Disallow` 케이스를 통과시킨다 — 이 게이트를
                # 처음 돌렸을 때 실제로 그렇게 "성공(예상 밖)"이 나왔다.
                db = Path(tmp) / f"fail{index}.db"
                url = f"{base}/case{index}"
                with Anchor(db_path=db, config=Config(db_path=db)) as ax:
                    start = time.perf_counter()
                    try:
                        ax.fetch(url, max_age=0)
                        outcome = "성공(예상 밖)"
                    except AnchorError as error:
                        outcome = type(error).__name__
                    elapsed = (time.perf_counter() - start) * 1000
                gate(
                    f"실패는 빠르다: {label} < {ceiling:.0f}ms",
                    elapsed < ceiling and outcome != "성공(예상 밖)",
                    f"{elapsed:.0f}ms ({outcome}) — 재시도가 되살아나면 이 종류만 빨개진다",
                )
        # --- 정중함: 지정값이 0이어도 바닥은 지킨다 (D-275) ---
        #
        # 위 게이트는 전부 "얼마나 빠른가"만 잰다. 그 반대편 — **얼마나
        # 정중한가** — 은 아무도 재지 않았고, 그래서 D-238이 `Retry-After`를
        # 존중하게 고치며 백오프 바닥을 없앤 것이 6ms 안에 4연발로 나타났는데도
        # 게이트가 침묵했다. 상한만 재는 게이트는 이 방향의 회귀를 원리적으로
        # 못 잡는다 — 더 빨라지는 것이 곧 위반이기 때문이다.
        state.update(status=429, robots="User-agent: *\nAllow: /\n", retry_after="0")
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "polite.db"
            hits.clear()
            with Anchor(db_path=db, config=Config(db_path=db)) as ax:
                try:
                    ax.fetch(f"{base}/polite", max_age=0)
                except AnchorError:
                    pass
            times = [t for path, t in hits if path == "/polite"]
            gaps = [b - a for a, b in zip(times, times[1:])]
            # 기본 설정의 하한 = max(retry_backoff_base 1.0, 1/rate_limit_rps 1.0)
            floor = 1.0
            worst = min(gaps) if gaps else float("inf")
            gate(
                f"정중함: Retry-After:0에도 재시도 간격 >= {floor:.1f}s",
                len(gaps) >= 1 and worst >= floor * 0.9,
                f"요청 {len(times)}회, 최소 간격 "
                f"{'-' if not gaps else format(worst, '.2f') + 's'} — 바닥이 사라지면 "
                f"6ms 안에 4연발이 된다 (D-275)",
            )
    finally:
        server.shutdown()
        server.server_close()


def main() -> int:
    bench_cache_hit()
    bench_cache_hit_under_load()
    foreground_unresolved = bench_matcher_worst_case()
    bench_matcher_under_contention(foreground_unresolved)
    bench_normal_corpus()
    bench_failure_is_fast()
    if FAILURES:
        print(f"\n게이트 실패: {', '.join(FAILURES)}", file=sys.stderr)
        return 1
    print("\n모든 게이트 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
