# SPDX-License-Identifier: Apache-2.0
from anchor.normalize.text import normalize_text


def test_collapses_horizontal_whitespace():
    assert normalize_text("가  나\t\t다") == "가 나 다"


def test_strips_trailing_whitespace_per_line():
    assert normalize_text("가나  \n다라\t") == "가나\n다라"


def test_collapses_three_plus_newlines_to_two():
    assert normalize_text("가\n\n\n\n나") == "가\n\n나"
    assert normalize_text("가\n\n나") == "가\n\n나"


def test_nfc_normalization():
    decomposed = "e\u0301cole"  # e + 결합 악센트 (NFD)
    composed = "\u00e9cole"  # é 단일 코드포인트 (NFC)
    assert normalize_text(decomposed) == composed


def test_strips_outer_whitespace():
    assert normalize_text("\n\n 가나 \n\n") == "가나"
