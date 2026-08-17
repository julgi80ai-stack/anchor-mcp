# SPDX-License-Identifier: Apache-2.0
"""하위 프로세스 실행 도우미.

크래시(segfault·프로세스 사망)는 in-process로 잡을 수 없어 별도 프로세스로
돌린다. 이때 `env`를 최소로 넘기면 Windows에서 `SYSTEMROOT` 등이 사라져
asyncio 임포트가 실패한다(WinError 10106) — 부모 환경을 물려받고
`PYTHONPATH`만 덮어써 개발 셸의 오염을 막는다.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

SRC = str(Path(__file__).resolve().parents[1] / "src")


def run_python(code: str, *, timeout: int = 180) -> subprocess.CompletedProcess:
    environment = {**os.environ, "PYTHONPATH": SRC}
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        env=environment,
        timeout=timeout,
    )
