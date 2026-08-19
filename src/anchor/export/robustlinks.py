# SPDX-License-Identifier: Apache-2.0
"""Robust Links 직렬화 (SPEC §7.9).

`data-versionurl`은 아카이브 URI-M이 알려진 경우에만 채운다. 없으면
`data-originalurl`과 `data-versiondate`만 출력한다 — Robust Links 스펙이
허용하는 형태다.

**`href`와 `data-originalurl`은 서로 다른 질문에 답한다 (D-244).** `href`는
"지금 가서 볼 곳"이므로 문서의 정본 URL(`documents.url`)이고, 영구
리다이렉트를 따라 움직이는 것이 맞다. `data-originalurl`은 "인용한 곳"이므로
움직여서는 안 된다 — 기사가 삭제되고 홈으로 301되면(soft-404) 그 둘은
갈라지고, 그때 정본 URL을 찍으면 사용자가 인용한 적 없는 URL이 인용의 출처로
배포된다. 실측: 일회용 세션 토큰 URL이 정본으로 굳은 문서가 실재한다.
"""

from __future__ import annotations

import html
import re

from anchor.models import AnchorRecord, Document, Version

# 마크다운 링크 텍스트에서 구조를 깨는 문자들.
_MD_TEXT_SPECIALS = re.compile(r"([\\\[\]`*_])")
# BibTeX에서 특별한 의미를 갖는 문자들.
_BIBTEX_SPECIALS = {
    "\\": "\\textbackslash{}", "{": "\\{", "}": "\\}", "$": "\\$", "&": "\\&",
    "%": "\\%", "#": "\\#", "_": "\\_", "^": "\\textasciicircum{}",
    "~": "\\textasciitilde{}",
}


def _escape_markdown_text(text: str) -> str:
    """링크 텍스트를 이스케이프한다. 제목의 `]` 하나로 링크가 통째로
    사라지거나, `<script>`가 그대로 렌더되던 문제를 막는다 (D-026)."""
    return _MD_TEXT_SPECIALS.sub(r"\\\1", text).replace("<", "&lt;").replace(">", "&gt;")


def _escape_markdown_url(url: str) -> str:
    return url.replace("(", "%28").replace(")", "%29").replace(" ", "%20")


def _escape_bibtex(text: str) -> str:
    return "".join(_BIBTEX_SPECIALS.get(char, char) for char in text)


def _version_date(version: Version) -> str:
    return version.captured_at[:10]  # YYYY-MM-DD


def _version_url(version: Version) -> str | None:
    if version.source == "archive" and version.source_uri:
        return version.source_uri
    return None


def _cited_url(document: Document, anchor: AnchorRecord | None) -> str:
    """이 인용의 **정체성** — 사용자가 인용한 그 URL (D-244·D-246).

    앵커가 자기 값을 들고 있으면 그것이 먼저다: 문서는 병합으로 사라질 수
    있고, 그러면 앵커는 남의 `original_url`을 물려받는다. 앵커가 모르면
    (v9 이전에 만들어진 행) 문서의 `original_url`로 물러선다 — 그것이 그
    앵커에 대해 우리가 아는 전부이고, 없는 것을 지어내지 않는다.
    """
    if anchor is not None and anchor.cited_url:
        return anchor.cited_url
    return document.original_url


def to_html(document: Document, version: Version, anchor: AnchorRecord | None = None) -> str:
    url = html.escape(document.url, quote=True)          # 지금 가서 볼 곳
    identity = _cited_url(document, anchor)              # 인용한 곳
    cited = html.escape(identity, quote=True)
    # 제목이 없을 때의 대체 표기도 **출력물**이다 — 정본 URL을 쓰면 독자가
    # 보는 링크 문구가 사용자가 인용한 적 없는 주소가 된다 (D-244).
    text = html.escape(document.title or identity)
    attributes = [
        f'href="{url}"',
        f'data-originalurl="{cited}"',
        f'data-versiondate="{_version_date(version)}"',
    ]
    if version_url := _version_url(version):
        attributes.append(f'data-versionurl="{html.escape(version_url, quote=True)}"')
    return f"<a {' '.join(attributes)}>{text}</a>"


def to_markdown(document: Document, version: Version, anchor: AnchorRecord | None = None) -> str:
    identity = _cited_url(document, anchor)
    text = _escape_markdown_text(document.title or identity)
    line = f"[{text}]({_escape_markdown_url(document.url)})"
    comment = (
        f'data-originalurl="{html.escape(identity, quote=True)}" '
        f'data-versiondate="{_version_date(version)}"'
    )
    if version_url := _version_url(version):
        comment += f' data-versionurl="{html.escape(version_url, quote=True)}"'
    # 주석을 조기 종료시키는 `--`를 막는다.
    return f"{line} <!-- {comment.replace('--', '- -')} -->"


def to_bibtex_note(document: Document, version: Version, anchor: AnchorRecord | None = None) -> str:
    parts = [
        # BibTeX note에는 "지금 볼 곳" 슬롯이 없다 — Robust Links의 note 표기에서
        # 첫 `\url{}`은 URI-R, 즉 **인용한 리소스**다 (D-244).
        f"\\url{{{_escape_bibtex(_cited_url(document, anchor))}}}",
        f"versiondate {_version_date(version)}",
    ]
    if version_url := _version_url(version):
        parts.append(f"versionurl \\url{{{_escape_bibtex(version_url)}}}")
    return f"note = {{{', '.join(parts)}}}"


SERIALIZERS = {
    "html": to_html,
    "markdown": to_markdown,
    "bibtex_note": to_bibtex_note,
}
