# SPDX-License-Identifier: Apache-2.0
"""RFC 7089 TimeMap 직렬화 (SPEC §7.8).

로컬 버전에는 `anchor://` URI 스킴을 부여하고, 아카이브에서 온 버전은
실제 URI-M을 그대로 노출한다. 외부 Memento 클라이언트가 읽을 수 있는
application/link-format으로 내보낸다.
"""

from __future__ import annotations

from email.utils import format_datetime

from anchor.models import Document, Version, parse_iso


def _http_date(iso: str) -> str:
    """ISO 8601 → RFC 1123 HTTP-date (Memento-Datetime 표기)."""
    return format_datetime(parse_iso(iso), usegmt=True)


def _memento_uri(document: Document, version: Version) -> str:
    if version.source == "archive" and version.source_uri:
        return version.source_uri
    return f"anchor:///{document.id}/v/{version.id}"


def to_link_format(document: Document, versions: list[Version]) -> str:
    """versions는 captured_at 오름차순이어야 한다."""
    lines = [
        f'<{document.url}>; rel="original"',
        f'<anchor:///{document.id}/timemap>; rel="self"; type="application/link-format"',
    ]
    for index, version in enumerate(versions):
        # RFC 7089 §5.1.2 표기 순서: "first last memento" / "first memento" / "last memento"
        qualifiers = []
        if index == 0:
            qualifiers.append("first")
        if index == len(versions) - 1:
            qualifiers.append("last")
        rel = " ".join(qualifiers + ["memento"])
        lines.append(
            f'<{_memento_uri(document, version)}>; rel="{rel}"; '
            f'datetime="{_http_date(version.captured_at)}"'
        )
    return ",\n".join(lines)


def to_json_format(document: Document, versions: list[Version]) -> dict:
    return {
        "original_uri": document.url,
        "timemap_uri": f"anchor:///{document.id}/timemap",
        "mementos": [
            {
                "uri": _memento_uri(document, version),
                "datetime": _http_date(version.captured_at),
                "version_id": version.id,
                "source": version.source,
                "text_hash": version.text_hash,
            }
            for version in versions
        ],
    }
