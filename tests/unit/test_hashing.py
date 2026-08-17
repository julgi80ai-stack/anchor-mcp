# SPDX-License-Identifier: Apache-2.0
from anchor.normalize.hashing import hash_bytes, hash_text


def test_prefix_and_determinism():
    first = hash_text("재페치의 절반은 낭비다")
    second = hash_text("재페치의 절반은 낭비다")
    assert first == second
    assert first.startswith("b3:")


def test_text_and_bytes_agree():
    assert hash_text("abc") == hash_bytes(b"abc")


def test_different_input_different_hash():
    assert hash_text("가") != hash_text("나")
