# SPDX-License-Identifier: Apache-2.0
"""URL 정규화 (SPEC §5.1). 동일 문서의 중복 등록을 막는다.

추적 파라미터 제거 목록은 명백한 것으로 한정한다. `ref`·`s`는 사이트에
따라 실질 경로여서 제거하면 서로 다른 문서가 합쳐지므로 남긴다
(2026-08-17 설계 결정, SPEC v1.3 반영 예정).
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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


def _is_tracking(key: str) -> bool:
    return key in TRACKING_PARAMS or key.startswith(TRACKING_PREFIXES)


def normalize_url(url: str, extra_tracking: frozenset[str] = frozenset()) -> str:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = parts.hostname.lower() if parts.hostname else ""

    # IPv6 리터럴은 대괄호를 유지해야 한다. `parts.hostname`이 벗겨서
    # 돌려주므로 다시 씌우지 않으면 문법상 무효한 URL이 되고, 재정규화가
    # ValueError를 낸다 (D-008).
    netloc = f"[{host}]" if ":" in host else host
    if parts.port is not None and parts.port != _DEFAULT_PORTS.get(scheme):
        netloc = f"{netloc}:{parts.port}"
    if parts.username:
        credentials = parts.username + (f":{parts.password}" if parts.password else "")
        netloc = f"{credentials}@{netloc}"

    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not (_is_tracking(key) or key in extra_tracking)
    ]
    query_pairs.sort(key=lambda pair: pair[0])
    query = urlencode(query_pairs)

    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, query, ""))
