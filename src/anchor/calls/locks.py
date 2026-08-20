# SPDX-License-Identifier: Apache-2.0
"""URL 단위 직렬화 — 같은 URL의 페치끼리만 줄을 세운다 (D-038·D-128)."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager


class _UrlLockEntry:
    """URL 하나의 락과 그것을 쓰는(보유·대기) 사람 수."""

    __slots__ = ("lock", "refs")

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.refs = 0


class _UrlLocks:
    """URL 하나당 락 하나. 쓰는 사람이 없어지면 사라진다 (D-038·D-128).

    페치의 "조회 → 판단 → 생성"을 같은 URL끼리 직렬화하는 것은 의도다 —
    두 스레드가 동시에 "문서 없음"으로 판단하면 documents.url UNIQUE에
    걸리고, 같은 문서를 두 번 가져와 사이트에 두 배로 부담을 준다.

    문제는 **다른 URL 사이의 거짓 공유**였다. 고정 64개 스트라이프는 URL이
    십수 개만 되어도 충돌하고(생일 문제), 락은 네트워크 왕복 전체(기본 30초
    타임아웃 포함) 동안 잡혀 있다 — 실측으로 무관한 문서가 7.92초를 기다렸다.
    "성능 손해일 뿐"이 아니라 SPEC §10이 요구사항으로 못 박은 격리다.

    등록부 뮤텍스 아래에서 하는 일은 사전 조회와 참조 계수뿐이다 —
    네트워크도 SQLite도 그 밑에서 일어나지 않으므로, 캐시 히트 경로에
    실리는 비용은 경합 없는 잠금 두 번(µs 미만)이다. 참조 수는 보유자와
    대기자를 함께 세므로, 기다리는 사람이 있는 락은 지워지지 않는다.
    """

    def __init__(self) -> None:
        self._mutex = threading.Lock()
        self._entries: dict[str, _UrlLockEntry] = {}

    @contextmanager
    def acquire(self, key: str) -> Iterator[None]:
        with self._mutex:
            entry = self._entries.get(key)
            if entry is None:
                entry = _UrlLockEntry()
                self._entries[key] = entry
            entry.refs += 1
        try:
            entry.lock.acquire()
        except BaseException:
            self._release(key, entry)
            raise
        try:
            yield
        finally:
            entry.lock.release()
            self._release(key, entry)

    def _release(self, key: str, entry: _UrlLockEntry) -> None:
        with self._mutex:
            entry.refs -= 1
            # 같은 키의 새 항목이 이미 들어섰을 수 있다 — 내 것일 때만 지운다.
            if entry.refs == 0 and self._entries.get(key) is entry:
                del self._entries[key]

    # -- 관측 (동시성 계약은 관측 가능해야 회귀를 잡는다) --------------------

    def refcount(self, key: str) -> int:
        """그 URL의 락을 보유·대기 중인 수. 0이면 등록부에 없다."""
        with self._mutex:
            entry = self._entries.get(key)
            return entry.refs if entry is not None else 0

    def size(self) -> int:
        """등록부에 남은 락 수. 정상 상태에서는 진행 중인 페치 수와 같다."""
        with self._mutex:
            return len(self._entries)
