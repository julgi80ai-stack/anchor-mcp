# SPDX-License-Identifier: Apache-2.0
import pytest

from anchor.errors import InvalidURL
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


# -- D-105: RFC 3986 동등성과 요청 불변성 -------------------------------------


def test_unreserved_percent_encoding_is_one_document():
    """`/~user/`와 `/%7Euser/`는 같은 리소스다 (RFC 3986 §6.2.2.2, D-105).

    unreserved 문자의 퍼센트 인코딩을 풀지 않으면 같은 문서가 두 번
    등록된다 — §5.1(중복 등록 방지)의 목적 미달이다.
    """
    assert normalize_url("http://e.test/~user/page") == normalize_url(
        "http://e.test/%7Euser/page"
    )
    assert normalize_url("http://e.test/%41rticle") == normalize_url(
        "http://e.test/Article"
    )


def test_reserved_percent_encoding_is_preserved():
    """예약 문자(%2F 등)는 풀면 **다른 URL**이 된다 — 보존한다."""
    assert "%2F" in normalize_url("http://e.test/a%2Fb").upper()
    assert normalize_url("http://e.test/a%2Fb") != normalize_url("http://e.test/a/b")


def test_percent_hex_case_is_folded():
    """`%2f`와 `%2F`는 같은 옥텟이다 (RFC 3986 §6.2.2.1)."""
    assert normalize_url("http://e.test/a%2fb") == normalize_url("http://e.test/a%2Fb")


def test_valueless_query_parameter_is_not_rewritten():
    """`?novalue`에 `=`를 붙이면 **서버로 나가는 요청이 달라진다** (D-105).

    정규화는 우리 캐시 키를 만드는 일이지 사용자의 URL을 고쳐 쓰는 일이
    아니다 — 정규화된 URL이 곧 요청 URL이기 때문이다.
    """
    assert normalize_url("http://e.test/a?novalue&x=1") == "http://e.test/a?novalue&x=1"


# -- D-107·108: 잘못된 입력은 잘못된 입력이라고 말한다 ------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "http://example.com:99999/a",   # 포트 범위 초과
        "http://example.com:0x50/a",    # 포트가 숫자가 아님
        "example.com/article",          # 스킴 없음 (D-108)
        "/just/a/path",
        "",
        "ftp://example.com/file",       # http(s)가 아님
    ],
)
def test_invalid_input_raises_an_anchor_error(bad):
    """잘못된 URL은 **입력 오류**로 보고한다 (D-107·D-108).

    맨 `ValueError`가 새면 MCP 배치가 죽고(D-107), 스킴 없는 입력이
    robots 거부로 보고되면 **사용자의 오타를 사이트 소유자의 거부로
    뒤집어씌운다**(D-108) — 사실 보고 원칙 위반 중 가장 나쁜 부류다.
    """
    with pytest.raises(InvalidURL):
        normalize_url(bad)
