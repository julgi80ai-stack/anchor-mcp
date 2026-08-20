# SPDX-License-Identifier: Apache-2.0
"""공개 호출의 수명 — 진행 중 호출을 세고, 전경 구간을 표시한다 (D-129·D-196).

`_foreground`는 두 기제를 겸한다: 종료 안전을 위한 호출 계수(D-129)와
배경 워커의 GIL 양보를 여는 전경 표시(D-196). 겸하는 것이 의도이므로
(정중 스레드의 호출도 close는 기다려야 한다) 여기서도 한 몸으로 둔다.
"""

from __future__ import annotations

import functools
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

from anchor.anchoring import approx
from anchor.errors import StorageError


# close()가 진행 중 호출을 기다리며 잠자코 있는 시간. 넘기면 알리고 계속
# 기다린다 — server.shutdown(D-117)과 같은 판단이다.
_CLOSE_GRACE_SECONDS = 10.0


class _CallGate:
    """진행 중인 공개 API 호출을 세고, 닫는 동안 새 호출을 막는다 (D-129).

    `close()`가 진행 중 작업을 기다리지도 이후 사용을 막지도 않아,
    `Anchor.__exit__`가 워커보다 먼저 끝나면 진행 중이던 페치가
    `Bad file descriptor`로 죽고 이후 호출이 생 `sqlite3.ProgrammingError`로
    샜다. 서버 경로는 "워커 정리 후 저장소 해제"(D-034/D-117)로 막혀 있었지만
    라이브러리 직접 사용 경로(SPEC §8)에는 같은 보장이 없었다.

    `foreground_section`(GIL 양보, D-196~D-204)과는 **별개 기제**다. 양보는
    배경 워커(정중 스레드)의 재진입을 일부러 표시하지 않는데, 종료 안전은
    바로 그 배경 호출까지 세어야 성립하기 때문이다.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition(threading.Lock())
        self._active = 0
        self._closing = False
        self._closed_event = threading.Event()
        self._local = threading.local()

    @property
    def closing(self) -> bool:
        return self._closing

    @contextmanager
    def entered(self) -> Iterator[None]:
        depth = getattr(self._local, "depth", 0)
        with self._condition:
            # 이미 이 스레드에서 시작된 호출의 내부 호출(verify → fetch)은
            # 막지 않는다. 막으면 "진행 중 호출은 끝까지 보장한다"가 깨진다.
            if self._closing and depth == 0:
                raise StorageError(
                    "store is closed — 저장소가 닫혔습니다 (close 이후의 호출)"
                )
            self._active += 1
        self._local.depth = depth + 1
        try:
            yield
        finally:
            self._local.depth = depth
            with self._condition:
                self._active -= 1
                self._condition.notify_all()

    def begin_close(self, grace: float) -> bool:
        """닫기를 시작하고 진행 중 호출이 끝나기를 기다린다.

        이미 닫혔거나 닫는 중이면 False — 이중 close는 무해하다. 유예가
        지나면 stderr로 알리고 **계속 기다린다**: 공개 API 호출은 전부
        유한하고(HTTP 타임아웃·재시도 상한 D-206, 앵커당 시간 예산 §10),
        여기서 포기하고 닫으면 그 호출이 죽어 고치려던 결함으로 되돌아간다.
        """
        own = getattr(self._local, "depth", 0)  # 내 호출 안에서 부른 close
        with self._condition:
            if self._closing:
                already_closing = True
            else:
                already_closing = False
                self._closing = True
        if already_closing:
            # 다른 스레드가 닫는 중이다. 자원 해제 전에 돌려보내면 "close가
            # 반환했으면 닫혔다"가 그 스레드에서만 거짓이 된다 — 기다린다.
            self._closed_event.wait()
            return False
        with self._condition:
            deadline = time.monotonic() + grace
            warned = False
            while self._active > own:
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    self._condition.wait(remaining)
                    continue
                if not warned:
                    print(
                        f"anchor: waiting for {self._active - own} in-flight call(s) to "
                        "finish before closing the store — 저장소를 닫기 전에 진행 중인 "
                        "호출이 끝나기를 기다리는 중",
                        file=sys.stderr,
                    )
                    warned = True
                self._condition.wait(1.0)
        return True

    def finish_close(self) -> None:
        """자원 해제가 끝났음을 알린다 — 같이 기다리던 close들이 돌아간다."""
        self._closed_event.set()


def _foreground(method):
    """전경(지연 민감) 호출 구간을 표시한다 (D-196).

    배경 워커의 매칭 루프는 이 구간이 열려 있는 동안에만 GIL 양보로
    잠든다. 배경 워커(정중 스레드) 자신의 재진입은 표시하지 않는다 —
    자기 자신을 위해 양보하게 만들지 않기 위해서다."""

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        # 종료 안전(D-129)은 양보 의미론과 별개 기제다 — 정중 스레드의
        # 호출도 세어야 close가 그것을 기다린다.
        with self._calls.entered():
            if approx.is_polite_thread():
                return method(self, *args, **kwargs)
            with approx.foreground_section():
                return method(self, *args, **kwargs)

    return wrapper
