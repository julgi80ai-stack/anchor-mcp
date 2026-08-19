# SPDX-License-Identifier: Apache-2.0
"""하위 프로세스 실행 도우미.

크래시(segfault·프로세스 사망)는 in-process로 잡을 수 없어 별도 프로세스로
돌린다. 이때 `env`를 최소로 넘기면 Windows에서 `SYSTEMROOT` 등이 사라져
asyncio 임포트가 실패한다(WinError 10106) — 부모 환경을 물려받고
`PYTHONPATH`만 덮어써 개발 셸의 오염을 막는다.

입출력 인코딩도 못박는다 (D-226). `text=True`만 주면 파이썬은 **로케일**
인코딩을 쓴다(CPython `subprocess` 문서). 이 저장소의 오류 문장은 한국어이므로
Windows 러너(영문 로케일 → cp1252)에서는 두 방향 다 깨진다: 자식이 한국어를
stdout에 찍으면 `UnicodeEncodeError`로 **죽고**(실측 returncode 1), 부모가
읽는 stderr는 역슬래시 이스케이프로 뭉개져 진단이 사라진다. 그러면 실패의
원인이 시험 대상이 아니라 도우미가 된다 — 측정 수단이 거짓말을 하는 셈이다.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

SRC = str(Path(__file__).resolve().parents[1] / "src")


def run_python(code: str, *, timeout: int = 180) -> subprocess.CompletedProcess:
    environment = {**os.environ, "PYTHONPATH": SRC, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        encoding="utf-8",   # 로케일이 아니라 UTF-8로 읽는다 (D-226)
        errors="replace",   # 진단이 예외로 둔갑하지 않게 — 읽히는 것이 우선이다
        env=environment,
        timeout=timeout,
    )
