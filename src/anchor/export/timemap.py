# SPDX-License-Identifier: Apache-2.0
"""RFC 7089 TimeMap 직렬화 (SPEC §7.8).

로컬 버전에는 `anchor://` URI 스킴을 부여하고, 아카이브에서 온 버전은
실제 URI-M을 그대로 노출한다. 외부 Memento 클라이언트가 읽을 수 있는
application/link-format으로 내보낸다.

`rel="original"`(RFC 7089의 URI-R)은 **원 리소스**다 — 우리가 지금 본문을
가지러 가는 곳(`documents.url`)이 아니라 그 문서로서 등록된 곳
(`documents.original_url`)이다 (D-245). 영구 리다이렉트가 정본 URL을 옮긴
뒤에도 TimeMap이 가리키는 원 리소스는 옮겨가지 않는다.
"""

from __future__ import annotations

from email.utils import format_datetime

from anchor.models import Document, Version, parse_iso


def _http_date(iso: str) -> str:
    """ISO 8601 → RFC 1123 HTTP-date (Memento-Datetime 표기)."""
    return format_datetime(parse_iso(iso), usegmt=True)


def _original_uri(document: Document) -> str:
    """이 TimeMap이 말하는 원 리소스 (URI-R). `documents.url`이 아니다 (D-245)."""
    return document.original_url


def _memento_uri(document: Document, version: Version) -> str:
    if version.source == "archive" and version.source_uri:
        return version.source_uri
    return f"anchor:///{document.id}/v/{version.id}"


def _last_memento_index(document: Document, versions: list[Version]) -> int:
    """`rel="last memento"`가 붙을 판본의 자리 (D-236).

    **정렬은 그대로 captured_at이다** — RFC 7089의 시간축은 Memento-Datetime
    이고, SPEC §7.4 주석이 TimeMap과 `latest~N`은 서로 다른 질문에 답한다고
    명시한다. 바꾸는 것은 "last"의 **의미**다.

    되돌림(A→B→A) 뒤 캡처 시각 최대값은 B인데, 원문이 지금 서빙하는 것은
    A다 — 그 A 행의 `captured_at`은 그 본문이 **처음** 캡처된 때이지 마지막
    관측 때가 아니다(SPEC §4.1). 관측 중 Memento-Datetime이 가장 최근인
    것은 A의 재관측이므로, "가장 최근 memento"는 A다. B를 가리키면 Memento
    클라이언트가 **더 이상 서빙되지 않는 본문**을 최신으로 받는다.

    기준은 문서의 현재 포인터(`current_version`)이고, 없으면 관측 순번
    최대값으로 물러선다(v6 이전에 만들어진 행은 순번이 캡처 순이므로 결과가
    같다). link-format은 항목 순서를 규정하지 않으므로, "last"가 목록
    중간에 오는 것은 문법 위반이 아니다 — 시간축은 datetime 속성이 진다.
    """
    if not versions:
        return -1
    for index, version in enumerate(versions):
        if version.id == document.current_version:
            return index
    newest = max(
        range(len(versions)),
        key=lambda i: (
            versions[i].last_observed_seq,
            versions[i].last_observed_at,
            versions[i].captured_at,
            versions[i].id,
        ),
    )
    return newest


def to_link_format(document: Document, versions: list[Version]) -> str:
    """versions는 captured_at 오름차순이어야 한다."""
    lines = [
        f'<{_original_uri(document)}>; rel="original"',
        f'<anchor:///{document.id}/timemap>; rel="self"; type="application/link-format"',
    ]
    last = _last_memento_index(document, versions)
    for index, version in enumerate(versions):
        lines.append(
            f'<{_memento_uri(document, version)}>; rel="{_rel(index, last)}"; '
            f'datetime="{_http_date(version.captured_at)}"'
        )
    return ",\n".join(lines)


def _rel(index: int, last: int) -> str:
    """RFC 7089 §5.1.2 표기 순서: "first last memento" / "first memento" / …"""
    qualifiers = []
    if index == 0:
        qualifiers.append("first")
    if index == last:
        qualifiers.append("last")
    return " ".join(qualifiers + ["memento"])


def to_json_format(document: Document, versions: list[Version]) -> dict:
    last = _last_memento_index(document, versions)
    return {
        "original_uri": _original_uri(document),
        "timemap_uri": f"anchor:///{document.id}/timemap",
        "mementos": [
            {
                "uri": _memento_uri(document, version),
                "datetime": _http_date(version.captured_at),
                # link-format과 같은 사실을 JSON에서도 읽을 수 있어야 한다
                # (D-236) — 어느 것이 지금 서빙되는 본문인지가 여기서만
                # 사라지면 두 형식이 다른 말을 하는 셈이다.
                "rel": _rel(index, last),
                "last_observed_at": version.last_observed_at,
                "version_id": version.id,
                "source": version.source,
                "text_hash": version.text_hash,
            }
            for index, version in enumerate(versions)
        ],
    }
