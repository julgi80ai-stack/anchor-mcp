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
    gate("cache_hit p95 < 15ms", p95 < 15.0, f"p95={p95:.2f}ms (n=100, 본문 ~100KB)")


def bench_matcher_worst_case() -> None:
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


def main() -> int:
    bench_cache_hit()
    bench_matcher_worst_case()
    bench_normal_corpus()
    if FAILURES:
        print(f"\n게이트 실패: {', '.join(FAILURES)}", file=sys.stderr)
        return 1
    print("\n모든 게이트 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
