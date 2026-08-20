# SPDX-License-Identifier: Apache-2.0
"""gc와 인용의 경합 (D-251) — 서비스 층위.

D-249가 gc의 보호 집합을 좁히자(검증 로그는 더 이상 버전을 붙잡지 않는다)
잠자던 경합이 깨어났다. `verify`가 대조 중인 판본과 `cite`가 앵커를 달려던
판본이 그 사이 회수되어, 생 `sqlite3.IntegrityError`가 공개 API 밖으로 샜다.
CLI는 트레이스백으로 죽고, MCP는 프로토콜 오류가 되고, Task 경로는 배치
성과를 통째로 잃는다 — SPEC §8("라이브러리도 AnchorError 하나로") 위반이다.

축을 먼저 연다 (조치 절차 6):

① **진입점 3** — `verify`의 대조 판본 조회(`get_version`) / `verify` 루프의
   `insert_verification` / `cite`의 `insert_anchor`. 셋은 창이 서로 다르다.
② **`keep_versions` 1·5·20** — 보호 집합의 크기가 발생률을 결정한다. 기본
   20에서 침묵한다고 없는 결함이 아니다(느린 문서·많은 앵커면 열린다).
③ **경합의 종류 2** — 같은 프로세스의 스레드(하나의 직렬화된 커넥션) /
   별도 프로세스(각자의 SQLite 커넥션과 쓰기 락).
④ **주입 방식 2** — **결정적** 주입과 **자연 발생** 스트레스를 둘 다 둔다.
   결정적 쪽만 두면 기준선에서도 빨개져 회귀 판별력이 없고, 자연 발생 쪽만
   두면 기본 `keep=20`에서 조용히 통과한다.

**인용 복원 불변식**(§1.2)은 어느 시험에서도 깨지지 않아야 한다 —
`anchors.created_version`은 언제나 읽힌다. 이 파일의 조치는 gc의 보호
집합을 다시 넓히는 방향이면 안 된다(그러면 D-249가 되살아난다).
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from anchor.config import Config
from anchor.errors import AnchorError
from anchor.service import Anchor
from anchor.anchoring import matcher

from tests.integration.conftest import article_html
from tests.subprocess_helper import SRC

QUOTE = "링크는 살아 있지만 내용이 바뀌는 인용 표류가 가장 위험하다."

KEEP_AXIS = (1, 5, 20)


def _anchor(tmp_path: Path, *, keep_versions: int, name: str = "race.db") -> Anchor:
    config = Config(
        db_path=tmp_path / name,
        rate_limit_rps=2000.0,
        retry_backoff_base=0.001,
        keep_versions=keep_versions,
    )
    return Anchor(db_path=config.db_path, config=config)


def _push_until_reclaimed(
    anchor: Anchor, url: str, state, target_version_id: str, keep: int, marker: str
) -> int:
    """다른 클라이언트가 하는 일을 그대로 한다 — 새 판본을 만들고 gc를 돌린다.

    대상 판본이 실제로 회수될 때까지 밀어내고, **회수됐다는 사실을 관측해서**
    돌려준다. 몇 번이면 되는지를 적어 두면 gc의 보호 집합이 바뀐 날 이
    픽스처가 조용히 무효가 된다(조치 절차 4).

    인용문이 든 문단은 건드리지 않는다 — 판정을 흔들면 이 시험이 무엇을
    재고 있는지 알 수 없게 된다.
    """
    for turn in range(keep + 4):
        state.html = article_html(
            nonce=f"{marker}{turn}", extra_sentence=f" 밀어내기 {marker}-{turn}."
        )
        state.etag = f'"{marker}{turn}"'
        anchor.fetch(url, max_age=0)
        anchor.collect_garbage(keep=keep)
        if anchor._repository.get_version(target_version_id) is None:
            return turn + 1
    return 0


def _citation_restore_invariant(anchor: Anchor) -> int:
    """모든 앵커의 `created_version`이 여전히 읽히는가 (§1.2). 앵커 수를 돌려준다."""
    rows = anchor._repository._connection.execute(
        "SELECT id, created_version FROM anchors"
    ).fetchall()
    for row in rows:
        assert anchor._repository.get_version(row["created_version"]) is not None, (
            f"인용 복원 불변식이 깨졌다 — 앵커 {row['id']}의 판본이 회수됐다"
        )
        assert anchor._repository.get_version_text(row["created_version"])
    return len(rows)


# -- 축④ 결정적 주입 ---------------------------------------------------------


@pytest.mark.parametrize("keep", KEEP_AXIS)
def test_verify_survives_reclaim_of_the_version_it_is_comparing(
    fixture_server, tmp_path, monkeypatch, keep
):
    """축①-2: 매칭 루프 도중 대조 판본이 회수된다 → `insert_verification`.

    `verify`는 대조 판본을 **한 번** 읽고 그 문서의 모든 앵커를 루프 돈다.
    루프 중(앵커당 최대 200ms) 다른 클라이언트의 페치가 새 판본을 만들면 그
    판본은 더 이상 `current_version`이 아니고 인용된 적도 없어 보호를 못 받는다.
    """
    base, state = fixture_server
    url = f"{base}/article"
    with _anchor(tmp_path, keep_versions=keep) as anchor:
        anchor.fetch(url, max_age=0)
        ids = [anchor.cite(url, QUOTE).anchor_id for _ in range(3)]

        # 원문이 한 번 바뀐다 — 이제 verify가 대조할 판본은 **인용된 적 없는**
        # 판본이고, 그것이 이 결함이 사는 조건이다.
        state.html = article_html(nonce="v2", extra_sentence=" 두 번째 판본의 문장.")
        state.etag = '"v2"'

        observed: dict[str, object] = {}
        real_match = matcher.match_anchor

        def racing_match(*args, **kwargs):
            if "target" not in observed:
                document = anchor.list_documents()[0]
                target = anchor._repository.current_version(document.id)
                observed["target"] = target.id
                observed["pushed"] = _push_until_reclaimed(
                    anchor, url, state, target.id, keep, "m"
                )
            return real_match(*args, **kwargs)

        monkeypatch.setattr(matcher, "match_anchor", racing_match)
        report = anchor.verify(anchor_ids=ids)

        assert observed["pushed"], "대조 판본이 회수되지 않았다 — 픽스처가 무효다"
        assert anchor._repository.get_version(observed["target"]) is None
        # 배치가 죽지 않았고 앵커를 하나도 잃지 않았다.
        assert report.checked == len(ids)

        recorded = anchor._repository._connection.execute(
            f"SELECT checked_version FROM verifications WHERE anchor_id IN "
            f"({','.join('?' * len(ids))})",
            ids,
        ).fetchall()
        assert len(recorded) == len(ids)
        # 회수된 판본을 가리키는 척하지 않는다 — 1밀리초 뒤에 회수됐더라도
        # `ON DELETE SET NULL`이 만들었을 바로 그 값이다.
        assert all(row["checked_version"] is None for row in recorded)

        assert _citation_restore_invariant(anchor) == len(ids)


@pytest.mark.parametrize("keep", KEEP_AXIS)
def test_verify_survives_reclaim_before_it_reads_the_compared_version(
    fixture_server, tmp_path, monkeypatch, keep
):
    """축①-1: 페치와 `get_version` 사이의 더 이른 창 (`assert latest is not None`).

    감사자가 20~90초 스트레스로 맞히지 못한 자리다. 같은 창에 있으므로
    결정적으로 연다 — 여기서 죽으면 `python -O`에서는 `AttributeError`가 된다.
    """
    base, state = fixture_server
    url = f"{base}/article"
    with _anchor(tmp_path, keep_versions=keep) as anchor:
        anchor.fetch(url, max_age=0)
        ids = [anchor.cite(url, QUOTE).anchor_id for _ in range(2)]
        state.html = article_html(nonce="v2", extra_sentence=" 두 번째 판본의 문장.")
        state.etag = '"v2"'

        observed: dict[str, object] = {}
        real_fetch = anchor.fetch

        def racing_fetch(*args, **kwargs):
            result = real_fetch(*args, **kwargs)
            if "target" not in observed:
                # 재진입 가드는 밀어내기 **전에** 세운다 — 밀어내기가 다시
                # `fetch`를 부르므로, 뒤에 세우면 무한 재귀가 된다.
                observed["target"] = result.version_id
                observed["pushed"] = _push_until_reclaimed(
                    anchor, url, state, result.version_id, keep, "f"
                )
            return result

        monkeypatch.setattr(anchor, "fetch", racing_fetch)
        report = anchor.verify(anchor_ids=ids)

        assert observed["pushed"], "대조 판본이 회수되지 않았다 — 픽스처가 무효다"
        assert report.checked == len(ids)
        # 대조할 본문이 없었다 — 판정 불가이지 인용 무효가 아니다.
        assert report.summary[matcher.UNRESOLVED] == len(ids)
        assert report.sources["none"] == len(ids)
        # 아무것도 대조하지 못한 배치가 **빈 attention**으로 보이면 사용자는
        # 그것을 "이상 없음"으로 읽는다 (D-229).
        assert len(report.attention) == len(ids)
        assert {item.state for item in report.attention} == {matcher.UNRESOLVED}

        assert _citation_restore_invariant(anchor) == len(ids)


@pytest.mark.parametrize("keep", KEEP_AXIS)
def test_cite_survives_reclaim_of_the_version_it_is_anchoring(
    fixture_server, tmp_path, monkeypatch, keep
):
    """축①-3: 선택자 생성 도중(§10 예산 최대 200ms) 그 판본이 회수된다.

    여기서 트레이스백이 나면 **사용자의 인용 요청이 거부되고** 방금 만든
    선택자가 버려진다. 인용 요청을 버리지 않는 쪽이 옳다.
    """
    base, state = fixture_server
    url = f"{base}/article"
    with _anchor(tmp_path, keep_versions=keep) as anchor:
        anchor.fetch(url, max_age=0)

        observed: dict[str, object] = {}
        import anchor.service as service_module

        real_build = service_module.build_selector

        def racing_build(*args, **kwargs):
            if "target" not in observed:
                document = anchor.list_documents()[0]
                target = anchor._repository.current_version(document.id)
                observed["target"] = target.id
                observed["pushed"] = _push_until_reclaimed(
                    anchor, url, state, target.id, keep, "c"
                )
            return real_build(*args, **kwargs)

        monkeypatch.setattr(service_module, "build_selector", racing_build)
        result = anchor.cite(url, QUOTE)

        assert observed["pushed"], "앵커 대상 판본이 회수되지 않았다 — 픽스처가 무효다"
        assert anchor._repository.get_version(observed["target"]) is None

        record = anchor._repository.get_anchor(result.anchor_id)
        # 회수된 판본이 아니라 **지금 살아 있는** 판본에 달렸다.
        assert record.created_version != observed["target"]
        assert _citation_restore_invariant(anchor) == 1


# -- 축④ 자연 발생 스트레스 (축③-1: 같은 프로세스 스레드) --------------------


@pytest.mark.parametrize("keep", KEEP_AXIS)
def test_no_raw_exception_under_concurrent_fetch_gc_verify_cite(
    fixture_server, tmp_path, keep
):
    """페치·gc·verify·cite가 동시에 돈다. 결정적 주입 없이도 계약이 성립하는가.

    자연 발생 쪽은 **결함이 실제로 일어나는 조건**을 재는 대신 조치가 만든
    새 코드 경로를 실제 배치에 던져 본다. `keep=20`에서는 침묵할 수 있다 —
    그때도 무엇이 관측됐는지를 픽스처가 스스로 말한다.
    """
    base, state = fixture_server
    url = f"{base}/article"
    duration = 2.5
    raw: list[BaseException] = []
    domain: list[AnchorError] = []
    reclaimed = {"versions": 0}
    lock = threading.Lock()
    stop = threading.Event()

    with _anchor(tmp_path, keep_versions=keep) as anchor:
        anchor.fetch(url, max_age=0)
        # 앵커가 많을수록 verify가 대조 판본을 붙잡고 있는 시간이 길어진다 —
        # 창의 폭 자체가 이 결함의 발생률이다.
        ids = [anchor.cite(url, QUOTE).anchor_id for _ in range(40)]

        def record(error: BaseException) -> None:
            with lock:
                (domain if isinstance(error, AnchorError) else raw).append(error)

        def fetcher() -> None:
            turn = 0
            while not stop.is_set():
                turn += 1
                try:
                    state.html = article_html(
                        nonce=f"s{turn}", extra_sentence=f" 스트레스 문장 {turn}."
                    )
                    state.etag = f'"s{turn}"'
                    anchor.fetch(url, max_age=0)
                except BaseException as error:  # noqa: BLE001 — 무엇이든 기록한다
                    record(error)

        def gcer() -> None:
            while not stop.is_set():
                try:
                    result = anchor.collect_garbage(keep=keep)
                    with lock:
                        reclaimed["versions"] += result["deleted_versions"]
                except BaseException as error:  # noqa: BLE001
                    record(error)

        def verifier() -> None:
            while not stop.is_set():
                try:
                    anchor.verify(anchor_ids=ids)
                except BaseException as error:  # noqa: BLE001
                    record(error)

        def citer() -> None:
            while not stop.is_set():
                try:
                    anchor.cite(url, QUOTE)
                except BaseException as error:  # noqa: BLE001
                    record(error)
                # 쉬지 않고 인용하면 **모든 판본에 앵커가 달려** 회수 대상이
                # 하나도 남지 않는다 — 그러면 경합 창이 열리지 않고 이 시험은
                # 아무것도 재지 못한다(실측: gc 회수 0, 버전 74·앵커 314).
                stop.wait(0.1)

        threads = [
            threading.Thread(target=worker, daemon=True)
            for worker in (fetcher, gcer, gcer, verifier, verifier, citer)
        ]
        for thread in threads:
            thread.start()
        time.sleep(duration)
        stop.set()
        for thread in threads:
            thread.join(timeout=60)
        assert not any(thread.is_alive() for thread in threads), "작업 스레드가 멈추지 않았다"

        assert not raw, (
            f"공개 API 밖으로 샌 생 예외 {len(raw)}건 "
            f"(keep={keep}, gc 회수 {reclaimed['versions']}개): "
            f"{[f'{type(e).__name__}: {e}' for e in raw[:4]]}"
        )
        # 도메인 예외가 났다면 그것은 계약대로다. 다만 무엇이었는지는 남긴다.
        assert all(isinstance(error, AnchorError) for error in domain)
        _citation_restore_invariant(anchor)

    # 픽스처가 스스로 사실을 말한다 — keep이 작을수록 회수가 실제로 일어난다.
    if keep == min(KEEP_AXIS):
        assert reclaimed["versions"] > 0, "gc가 한 개도 회수하지 않았다 — 경합 창이 열리지 않았다"


# -- 축③-2: 별도 프로세스가 gc를 돈다 ----------------------------------------


_GC_CHILD = """
import sys, time
from anchor.config import Config
from anchor.service import Anchor

db, seconds, keep = sys.argv[1], float(sys.argv[2]), int(sys.argv[3])
config = Config(db_path=db, keep_versions=keep)
deleted = 0
with Anchor(db_path=config.db_path, config=config) as anchor:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        deleted += anchor.collect_garbage(keep=keep)["deleted_versions"]
print(deleted)
"""


def test_no_raw_exception_when_another_process_runs_gc(fixture_server, tmp_path):
    """`anchor gc`는 보통 **다른 프로세스**다 — 각자의 커넥션과 쓰기 락을 쥔다.

    같은 프로세스의 스레드는 하나의 직렬화된 커넥션을 나눠 쓰므로, 거기서
    나지 않는 것이 "정말 안 나는 것"인지 "직렬화 덕분"인지 구분되지 않는다.
    지우는 쪽만 별도 프로세스로 옮긴다 — 실제 배치가 그 모습이다(MCP 서버가
    도는 사이 사용자가 다른 터미널에서 `anchor gc`를 친다).

    부모에는 판본을 **미는** 스레드가 있어야 한다. 부모가 단일 스레드면
    verify가 대조하는 판본이 곧 `current_version`이라 어느 프로세스의 gc도
    건드리지 못하고, 이 시험은 아무것도 재지 못한다.
    """
    base, state = fixture_server
    url = f"{base}/article"
    keep = 1
    raw: list[BaseException] = []
    domain: list[AnchorError] = []
    stop = threading.Event()
    lock = threading.Lock()

    with _anchor(tmp_path, keep_versions=keep, name="cross.db") as anchor:
        anchor.fetch(url, max_age=0)
        ids = [anchor.cite(url, QUOTE).anchor_id for _ in range(40)]

        def record(error: BaseException) -> None:
            with lock:
                (domain if isinstance(error, AnchorError) else raw).append(error)

        def pusher() -> None:
            turn = 0
            while not stop.is_set():
                turn += 1
                try:
                    state.html = article_html(
                        nonce=f"x{turn}", extra_sentence=f" 교차 프로세스 문장 {turn}."
                    )
                    state.etag = f'"x{turn}"'
                    anchor.fetch(url, max_age=0)
                except BaseException as error:  # noqa: BLE001
                    record(error)

        child = subprocess.Popen(
            [sys.executable, "-c", _GC_CHILD, str(anchor._repository._db_path), "6", str(keep)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONPATH": SRC, "PYTHONIOENCODING": "utf-8"},
        )
        thread = threading.Thread(target=pusher, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5.0
            turn = 0
            while time.monotonic() < deadline:
                turn += 1
                try:
                    anchor.verify(anchor_ids=ids)
                    if turn % 5 == 0:
                        # 매 회전마다 인용하면 모든 판본이 보호돼 회수 대상이
                        # 남지 않는다 — 다른 프로세스의 gc가 할 일이 없어진다.
                        anchor.cite(url, QUOTE)
                except BaseException as error:  # noqa: BLE001
                    record(error)
        finally:
            stop.set()
            thread.join(timeout=60)
            stdout, stderr = child.communicate(timeout=60)

        assert child.returncode == 0, f"gc 프로세스가 실패했다: {stderr}"
        child_deleted = int(stdout.strip().splitlines()[-1])
        assert child_deleted > 0, (
            "다른 프로세스의 gc가 한 개도 회수하지 않았다 — 경합 창이 열리지 않았다"
        )
        assert not raw, (
            f"공개 API 밖으로 샌 생 예외 {len(raw)}건 (별도 프로세스 gc가 "
            f"{child_deleted}개 회수): {[f'{type(e).__name__}: {e}' for e in raw[:4]]}"
        )
        assert all(isinstance(error, AnchorError) for error in domain)
        _citation_restore_invariant(anchor)
