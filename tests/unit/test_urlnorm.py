# SPDX-License-Identifier: Apache-2.0
from anchor.fetcher.urlnorm import normalize_url


def test_lowercases_scheme_host_and_strips_default_port():
    assert normalize_url("HTTP://Example.COM:80/Path") == "http://example.com/Path"
    assert normalize_url("https://example.com:443/a") == "https://example.com/a"


def test_keeps_non_default_port():
    assert normalize_url("http://example.com:8080/a") == "http://example.com:8080/a"


def test_strips_fragment():
    assert normalize_url("https://example.com/a#section-2") == "https://example.com/a"


def test_removes_tracking_params_only():
    url = "https://example.com/a?utm_source=x&utm_medium=y&fbclid=z&gclid=1&igshid=2&q=검색"
    assert normalize_url(url) == "https://example.com/a?q=%EA%B2%80%EC%83%89"


def test_preserves_ref_and_s_params():
    # `ref`·`s`는 사이트에 따라 실질 경로다 — 제거하면 다른 문서가 합쳐진다.
    url = "https://example.com/a?s=twitter&ref=home&utm_source=x"
    assert normalize_url(url) == "https://example.com/a?ref=home&s=twitter"


def test_sorts_query_params_by_key():
    assert normalize_url("https://example.com/a?b=2&a=1") == "https://example.com/a?a=1&b=2"


def test_empty_path_becomes_slash():
    assert normalize_url("https://example.com") == "https://example.com/"
