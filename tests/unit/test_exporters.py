# SPDX-License-Identifier: Apache-2.0
from anchor.export import robustlinks, timemap
from anchor.models import Document, Version, parse_iso_duration

DOC = Document(
    id="doc-1",
    url="https://example.com/report",
    original_url="https://example.com/report",
    title="2026 Report",
    first_seen_at="2026-07-15T09:11:00Z",
    last_checked_at="2026-08-16T04:12:00Z",
    status="live",
    etag=None,
    last_modified=None,
    robots_allowed=True,
)


def make_version(vid: str, captured: str, source: str = "live", source_uri: str | None = None):
    return Version(
        id=vid,
        document_id="doc-1",
        text_hash=f"b3:{vid}",
        raw_hash=f"b3:raw-{vid}",
        pipeline_version="trafilatura/2.2.0+norm/1",
        captured_at=captured,
        byte_size=1000,
        char_count=500,
        http_status=200,
        source=source,
        source_uri=source_uri,
    )


V1 = make_version("v-old", "2026-07-15T09:11:00Z")
V2 = make_version("v-new", "2026-08-16T04:12:00Z")
V_ARCHIVE = make_version(
    "v-arc",
    "2026-08-01T00:00:00Z",
    source="archive",
    source_uri="https://web.archive.org/web/20260801000000/https://example.com/report",
)


def test_timemap_link_format_follows_rfc7089():
    body = timemap.to_link_format(DOC, [V1, V2])
    assert '<https://example.com/report>; rel="original"' in body
    assert 'rel="self"; type="application/link-format"' in body
    assert '<anchor:///doc-1/v/v-old>; rel="first memento"' in body
    assert '<anchor:///doc-1/v/v-new>; rel="last memento"' in body
    assert 'datetime="Wed, 15 Jul 2026 09:11:00 GMT"' in body


def test_timemap_single_version_is_first_and_last():
    body = timemap.to_link_format(DOC, [V1])
    assert 'rel="first last memento"' in body


def test_timemap_archive_version_exposes_real_uri_m():
    body = timemap.to_link_format(DOC, [V_ARCHIVE, V2])
    assert "<https://web.archive.org/web/20260801000000/https://example.com/report>" in body


def test_timemap_json_format():
    payload = timemap.to_json_format(DOC, [V1, V2])
    assert payload["original_uri"] == DOC.url
    assert len(payload["mementos"]) == 2
    assert payload["mementos"][0]["version_id"] == "v-old"


def test_robust_links_html_without_archive():
    html = robustlinks.to_html(DOC, V2)
    assert 'data-originalurl="https://example.com/report"' in html
    assert 'data-versiondate="2026-08-16"' in html
    assert "data-versionurl" not in html  # 아카이브 URI-M 미확보 시 생략
    assert ">2026 Report</a>" in html


def test_robust_links_html_with_archive():
    html = robustlinks.to_html(DOC, V_ARCHIVE)
    assert 'data-versionurl="https://web.archive.org/' in html


def test_robust_links_markdown_and_bibtex():
    md = robustlinks.to_markdown(DOC, V2)
    assert md.startswith("[2026 Report](https://example.com/report)")
    assert 'data-versiondate="2026-08-16"' in md
    note = robustlinks.to_bibtex_note(DOC, V2)
    assert note.startswith("note = {")
    assert "versiondate 2026-08-16" in note


def test_parse_iso_duration():
    assert parse_iso_duration("P7D") == 7 * 86400
    assert parse_iso_duration("PT12H") == 12 * 3600
    assert parse_iso_duration("P1DT6H30M") == 86400 + 6 * 3600 + 30 * 60
    assert parse_iso_duration("P2W") == 14 * 86400
    import pytest

    with pytest.raises(ValueError):
        parse_iso_duration("7d")
