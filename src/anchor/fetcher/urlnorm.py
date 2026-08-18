# SPDX-License-Identifier: Apache-2.0
"""URL 정규화 (SPEC §5.1). 동일 문서의 중복 등록을 막는다.

추적 파라미터 제거 목록은 명백한 것으로 한정한다. `ref`·`s`는 사이트에
따라 실질 경로여서 제거하면 서로 다른 문서가 합쳐지므로 남긴다
(2026-08-17 설계 결정, SPEC v1.3 §5.1에 반영됨).
"""

from __future__ import annotations

import string
from urllib.parse import quote_plus, unquote_plus, urlsplit, urlunsplit

from anchor.errors import InvalidURL

TRACKING_PARAMS: frozenset[str] = frozenset(
    {
        "fbclid",
        "gclid",
        "dclid",
        "msclkid",
        "twclid",
        "yclid",
        "igshid",
        "mc_eid",
    }
)
TRACKING_PREFIXES: tuple[str, ...] = ("utm_",)

_DEFAULT_PORTS = {"http": 80, "https": 443}

# RFC 3986 §2.3 unreserved — 퍼센트 인코딩 여부와 무관하게 같은 문자다.
# `/~user/`와 `/%7Euser/`가 서로 다른 문서로 등록되면 §5.1의 목적 미달이다
# (D-105). 예약 문자(%2F 등)는 풀면 다른 URL이 되므로 보존하되, 16진수의
# 대소문자만 통일한다 (§6.2.2.1).
_UNRESERVED = frozenset(string.ascii_letters + string.digits + "-._~")
_HEX = frozenset(string.hexdigits)


def _fold_percent_encoding(text: str) -> str:
    pieces: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if (
            char == "%"
            and index + 2 < len(text) + 1
            and len(text) - index >= 3
            and text[index + 1] in _HEX
            and text[index + 2] in _HEX
        ):
            decoded = chr(int(text[index + 1 : index + 3], 16))
            pieces.append(decoded if decoded in _UNRESERVED
                          else "%" + text[index + 1 : index + 3].upper())
            index += 3
        else:
            pieces.append(char)
            index += 1
    return "".join(pieces)


def _is_tracking(key: str) -> bool:
    return key in TRACKING_PARAMS or key.startswith(TRACKING_PREFIXES)


def normalize_url(url: str, extra_tracking: frozenset[str] = frozenset()) -> str:
    # 잘못된 입력은 여기서 잘못된 입력이라고 말한다 (D-107·D-108). 흘려보내면
    # 포트 오류는 맨 ValueError로 새고, 스킴 없는 입력은 robots 판정까지
    # 흘러가 "사이트 소유자가 거부했다"로 보고된다.
    try:
        parts = urlsplit(url.strip())
        port = parts.port  # 범위 초과·비숫자 포트는 여기서 ValueError
        host = parts.hostname.lower() if parts.hostname else ""
    except ValueError as error:
        raise InvalidURL(f"Not a valid URL — URL이 아닙니다: {url!r} ({error})") from error
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not host:
        raise InvalidURL(
            f"Not an http(s) URL — http(s) URL이 아닙니다: {url!r}"
            + (" (스킴 누락 — 'https://'로 시작해야 합니다)" if not parts.scheme else "")
        )

    # IPv6 리터럴은 대괄호를 유지해야 한다. `parts.hostname`이 벗겨서
    # 돌려주므로 다시 씌우지 않으면 문법상 무효한 URL이 되고, 재정규화가
    # ValueError를 낸다 (D-008).
    netloc = f"[{host}]" if ":" in host else host
    if port is not None and port != _DEFAULT_PORTS.get(scheme):
        netloc = f"{netloc}:{port}"
    if parts.username:
        credentials = parts.username + (f":{parts.password}" if parts.password else "")
        netloc = f"{credentials}@{netloc}"

    # 값 없는 파라미터에 `=`를 붙이지 않는다 (D-105). 정규화된 URL이 곧
    # 요청 URL이므로, `?novalue` → `?novalue=`는 캐시 키 정리가 아니라
    # **사용자가 준 것과 다른 요청을 서버에 보내는 것**이다. parse_qsl은
    # `=` 유무를 잃어버리므로 직접 가른다.
    query_pairs: list[tuple[str, str, bool]] = []
    for piece in parts.query.split("&"):
        if not piece:
            continue
        key, equals, value = piece.partition("=")
        key, value = unquote_plus(key), unquote_plus(value)
        if _is_tracking(key) or key in extra_tracking:
            continue
        query_pairs.append((key, value, bool(equals)))
    query_pairs.sort(key=lambda pair: pair[0])
    query = "&".join(
        quote_plus(key) + (f"={quote_plus(value)}" if has_value else "")
        for key, value, has_value in query_pairs
    )

    path = _fold_percent_encoding(parts.path) or "/"
    return urlunsplit((scheme, netloc, path, query, ""))
