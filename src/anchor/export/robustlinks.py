# SPDX-License-Identifier: Apache-2.0
"""Robust Links 직렬화 (SPEC §7.9).

`data-versionurl`은 아카이브 URI-M이 알려진 경우에만 채운다. 없으면
`data-originalurl`과 `data-versiondate`만 출력한다 — Robust Links 스펙이
허용하는 형태다.
"""

from __future__ import annotations

import html

from anchor.models import AnchorRecord, Document, Version


def _version_date(version: Version) -> str:
    return version.captured_at[:10]  # YYYY-MM-DD


def _version_url(version: Version) -> str | None:
    if version.source == "archive" and version.source_uri:
        return version.source_uri
    return None


def to_html(document: Document, version: Version, anchor: AnchorRecord | None = None) -> str:
    url = html.escape(document.url, quote=True)
    text = html.escape(document.title or document.url)
    attributes = [
        f'href="{url}"',
        f'data-originalurl="{url}"',
        f'data-versiondate="{_version_date(version)}"',
    ]
    if version_url := _version_url(version):
        attributes.append(f'data-versionurl="{html.escape(version_url, quote=True)}"')
    return f"<a {' '.join(attributes)}>{text}</a>"


def to_markdown(document: Document, version: Version, anchor: AnchorRecord | None = None) -> str:
    text = document.title or document.url
    line = f"[{text}]({document.url})"
    comment = f'data-originalurl="{document.url}" data-versiondate="{_version_date(version)}"'
    if version_url := _version_url(version):
        comment += f' data-versionurl="{version_url}"'
    return f"{line} <!-- {comment} -->"


def to_bibtex_note(document: Document, version: Version, anchor: AnchorRecord | None = None) -> str:
    parts = [f"\\url{{{document.url}}}", f"versiondate {_version_date(version)}"]
    if version_url := _version_url(version):
        parts.append(f"versionurl \\url{{{version_url}}}")
    return f"note = {{{', '.join(parts)}}}"


SERIALIZERS = {
    "html": to_html,
    "markdown": to_markdown,
    "bibtex_note": to_bibtex_note,
}
