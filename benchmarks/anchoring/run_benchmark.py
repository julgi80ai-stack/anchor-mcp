# SPDX-License-Identifier: Apache-2.0
"""앵커링 벤치마크 — Hypothesis 공개 주석 데이터 기반 (SPEC §12).

C등급 픽스처 정책(SPEC §12.1): 주석은 사용자 생성 콘텐츠, 원본 페이지는
각 사이트의 저작물이므로 **데이터를 저장소에 커밋하지 않고** 이 스크립트가
런타임에 수집한다. Hypothesis의 anchoring-test-tools와 같은 구조다.

절차: 공개 주석 API에서 TextQuoteSelector가 달린 주석을 모으고, 대상
문서를 Anchor의 정직한 페처(robots 준수, 레이트 제한)로 가져온 뒤, 각
인용문을 현재 본문에 재부착해 성공률과 지연을 잰다.

게이트 (SPEC §10/§12): p99 ≤ 250ms, 정지 없음, 측정 표본 ≥ 20건.
성공률(INTACT/MOVED/ALTERED 합)은 문서가 실제로 변하므로 정보로 보고한다.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from anchor.anchoring.matcher import match_anchor  # noqa: E402
from anchor.config import Config  # noqa: E402
from anchor.errors import AnchorError  # noqa: E402
from anchor.service import Anchor  # noqa: E402

API = "https://api.hypothes.is/api/search"
P99_GATE_MS = 250.0
MIN_SAMPLES = 20


def collect_annotations(pages: int) -> dict[str, list[dict]]:
    """공개 주석에서 (uri → TextQuoteSelector 목록)을 모은다. API 상한은
    요청당 200건이라 offset 페이지네이션으로 모은다."""
    rows: list[dict] = []
    for page in range(pages):
        request = urllib.request.Request(
            f"{API}?limit=200&offset={page * 200}&sort=updated&order=desc",
            headers={
                "User-Agent": "anchor-benchmark (+https://github.com/julgi80ai-stack/anchor-mcp)"
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            batch = json.load(response)["rows"]
        rows.extend(batch)
        if len(batch) < 200:
            break

    by_uri: dict[str, list[dict]] = {}
    for row in rows:
        uri = row.get("uri", "")
        if not uri.startswith(("http://", "https://")):
            continue
        for target in row.get("target", []):
            for selector in target.get("selector", []):
                if selector.get("type") == "TextQuoteSelector" and len(selector.get("exact", "")) >= 12:
                    by_uri.setdefault(uri, []).append(selector)
    return by_uri


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-documents", type=int, default=8)
    parser.add_argument("--pages", type=int, default=3, help="주석 API 페이지 수 (페이지당 200건)")
    args = parser.parse_args()

    by_uri = collect_annotations(args.pages)
    print(f"주석 수집: 문서 {len(by_uri)}개, 인용 {sum(len(v) for v in by_uri.values())}건")

    latencies: list[float] = []
    states: Counter[str] = Counter()
    fetched_docs = 0

    with tempfile.TemporaryDirectory() as tmp:
        config = Config(db_path=Path(tmp) / "bench.db")
        with Anchor(db_path=config.db_path, config=config) as ax:
            for uri, selectors in by_uri.items():
                if fetched_docs >= args.max_documents:
                    break
                try:
                    doc = ax.fetch(uri, include_content=True)
                except AnchorError as error:
                    print(f"  skip {uri[:70]} — {error}")
                    continue
                text = doc.content or ""
                if len(text) < 200:
                    continue
                fetched_docs += 1
                print(f"  {uri[:70]} ({len(selectors)}건)")
                for selector in selectors:
                    start = time.perf_counter()
                    result = match_anchor(
                        text,
                        exact=selector["exact"],
                        prefix=selector.get("prefix", ""),
                        suffix=selector.get("suffix", ""),
                        position_hint=0,
                        budget_ms=200,
                    )
                    latencies.append((time.perf_counter() - start) * 1000)
                    states[result.state] += 1

    total = sum(states.values())
    if total < MIN_SAMPLES:
        print(f"표본 부족: {total} < {MIN_SAMPLES} — 게이트 판정 불가", file=sys.stderr)
        return 1

    ordered = sorted(latencies)
    p50 = ordered[len(ordered) // 2]
    p99 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.99))]
    success = states["INTACT"] + states["MOVED"] + states["ALTERED"]
    print(f"\n측정 {total}건 / 문서 {fetched_docs}개")
    print(f"상태 분포: {dict(states)}")
    print(f"앵커링 성공률(참고): {success}/{total} ({success / total:.1%})")
    print(f"지연: p50={p50:.1f}ms p99={p99:.1f}ms (게이트 {P99_GATE_MS}ms)")

    if p99 > P99_GATE_MS:
        print(f"게이트 실패: p99 {p99:.1f}ms > {P99_GATE_MS}ms", file=sys.stderr)
        return 1
    print("게이트 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
