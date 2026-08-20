# SPDX-License-Identifier: Apache-2.0
"""한 호출이 실제로 쓴 네트워크의 회계 (D-130·D-134·D-135)."""

from __future__ import annotations


class _Traffic:
    """한 번의 사용자 호출이 실제로 쓴 네트워크 (D-130·D-134·D-135).

    성공 반환 경로에서만 회계를 만들면, 실패로 끝난 호출의 바이트는 페처와
    폴백의 지역변수에 있다가 예외와 함께 사라진다. 받는 즉시 여기에 쌓아
    두고, 어떻게 끝나든 이 값으로 **한 행**을 남긴다.
    """

    __slots__ = ("bytes_down", "document_id", "http_status")

    def __init__(self) -> None:
        self.bytes_down = 0
        self.document_id: str | None = None
        self.http_status: int | None = None

    def add(self, count: int) -> None:
        self.bytes_down += count
