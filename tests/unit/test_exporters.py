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


def make_version(
    vid: str,
    captured: str,
    source: str = "live",
    source_uri: str | None = None,
    observed: str | None = None,
    seq: int = 0,
):
    return Version(
        id=vid,
        document_id="doc-1",
        text_hash=f"b3:{vid}",
        raw_hash=f"b3:raw-{vid}",
        pipeline_version="trafilatura/2.2.0+norm/1",
        captured_at=captured,
        last_observed_at=observed or captured,
        last_observed_seq=seq,
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


# -- D-236: 되돌림 뒤의 "last memento" ---------------------------------------


def _doc(current: str | None):
    import dataclasses

    return dataclasses.replace(DOC, current_version=current)


def test_last_memento_is_the_body_the_origin_serves_now():
    """A→B→A 되돌림. 캡처 시각 최대값은 B이지만 지금 서빙되는 것은 A다.

    B를 `rel="last"`로 가리키면 Memento 클라이언트가 **더 이상 서빙되지 않는
    본문**을 최신으로 받는다. 정렬은 RFC 7089대로 captured_at 그대로다.
    """
    a = make_version("v-a", "2026-07-15T09:11:00Z", observed="2026-08-20T00:00:00Z", seq=3)
    b = make_version("v-b", "2026-08-16T04:12:00Z", observed="2026-08-16T04:12:00Z", seq=2)
    body = timemap.to_link_format(_doc("v-a"), [a, b])

    assert '<anchor:///doc-1/v/v-a>; rel="first last memento"' in body
    assert '<anchor:///doc-1/v/v-b>; rel="memento"' in body
    # 시간축(datetime)은 그대로 캡처 시각 오름차순이다.
    assert body.index("v-a") < body.index("v-b")


def test_last_memento_falls_back_to_the_observation_axis():
    """포인터가 아직 없어도(구 DB) 관측 순번이 답한다."""
    a = make_version("v-a", "2026-07-15T09:11:00Z", observed="2026-08-20T00:00:00Z", seq=3)
    b = make_version("v-b", "2026-08-16T04:12:00Z", observed="2026-08-16T04:12:00Z", seq=2)
    body = timemap.to_link_format(_doc(None), [a, b])
    assert 'rel="first last memento"' in body


def test_without_a_rollback_last_is_still_the_newest_capture():
    """관측 순번이 캡처 순과 같은 평범한 문서에서는 아무것도 달라지지 않는다."""
    a = make_version("v-a", "2026-07-15T09:11:00Z", seq=1)
    b = make_version("v-b", "2026-08-16T04:12:00Z", seq=2)
    body = timemap.to_link_format(_doc("v-b"), [a, b])
    assert '<anchor:///doc-1/v/v-b>; rel="last memento"' in body


def test_json_format_says_which_memento_is_last():
    a = make_version("v-a", "2026-07-15T09:11:00Z", observed="2026-08-20T00:00:00Z", seq=3)
    b = make_version("v-b", "2026-08-16T04:12:00Z", seq=2)
    payload = timemap.to_json_format(_doc("v-a"), [a, b])
    rels = {item["version_id"]: item["rel"] for item in payload["mementos"]}
    assert rels == {"v-a": "first last memento", "v-b": "memento"}
    assert payload["mementos"][0]["last_observed_at"] == "2026-08-20T00:00:00Z"


# -- D-244·D-245·D-246: 인용의 정체성 우선순위 -------------------------------

MOVED_DOC = Document(
    id="doc-2",
    url="https://cdn.example.net/export/9f2c7a1e/8d3e5f0a/",  # 일회용 세션 토큰
    original_url="https://docs.example.com/document/d/1aBcD/export?format=txt",
    title="2026 Report",
    first_seen_at="2026-07-15T09:11:00Z",
    last_checked_at="2026-08-16T04:12:00Z",
    status="live",
    etag=None,
    last_modified=None,
    robots_allowed=True,
)


def _anchor(cited_url: str | None):
    from anchor.models import AnchorRecord

    return AnchorRecord(
        id="anc-1",
        document_id="doc-2",
        created_version="v-new",
        exact="인용문",
        prefix="앞",
        suffix="뒤",
        position_hint=0,
        exact_hash="b3:x",
        quality="ok",
        note=None,
        created_at="2026-08-16T04:12:00Z",
        cited_url=cited_url,
    )


def test_export_prefers_the_url_recorded_on_the_anchor():
    """앵커가 자기 정체성을 알면 그것이 먼저다 — 병합 뒤에도 살아남는 유일한 값."""
    cited = "https://old.example.org/2026/report"
    html = robustlinks.to_html(MOVED_DOC, V2, _anchor(cited))
    assert f'data-originalurl="{cited}"' in html
    assert f'href="{MOVED_DOC.url}"' in html  # "지금 볼 곳"은 그대로다
    md = robustlinks.to_markdown(MOVED_DOC, V2, _anchor(cited))
    assert f'data-originalurl="{cited}"' in md
    assert f"({MOVED_DOC.url})" in md
    note = robustlinks.to_bibtex_note(MOVED_DOC, V2, _anchor(cited))
    assert cited in note and MOVED_DOC.url not in note


def test_export_falls_back_to_the_documents_identity_when_the_anchor_predates_v9():
    """`cited_url`이 NULL이면 문서의 `original_url`이 우리가 아는 전부다."""
    html = robustlinks.to_html(MOVED_DOC, V2, _anchor(None))
    assert f'data-originalurl="{MOVED_DOC.original_url}"' in html
    md = robustlinks.to_markdown(MOVED_DOC, V2, _anchor(None))
    assert f'data-originalurl="{MOVED_DOC.original_url}"' in md
    note = robustlinks.to_bibtex_note(MOVED_DOC, V2, _anchor(None))
    assert MOVED_DOC.original_url in note


def test_export_without_an_anchor_uses_the_documents_identity():
    """앵커 없이 직렬화하는 호출자(축 ③)도 정본 URL을 찍어서는 안 된다."""
    html = robustlinks.to_html(MOVED_DOC, V2)
    assert f'data-originalurl="{MOVED_DOC.original_url}"' in html


def test_timemap_original_is_the_documents_identity_not_its_fetch_target():
    """RFC 7089의 URI-R는 **원 리소스**다 (D-245)."""
    body = timemap.to_link_format(MOVED_DOC, [V1, V2])
    assert f'<{MOVED_DOC.original_url}>; rel="original"' in body
    assert MOVED_DOC.url not in body
    payload = timemap.to_json_format(MOVED_DOC, [V1, V2])
    assert payload["original_uri"] == MOVED_DOC.original_url


def test_a_titleless_export_labels_the_link_with_the_cited_url():
    """제목이 없을 때의 대체 표기도 출력물이다 (D-244).

    정본 URL을 쓰면 독자가 **보는** 문구가 사용자가 인용한 적 없는 주소가 된다.
    """
    from dataclasses import replace

    titleless = replace(MOVED_DOC, title=None)
    cited = "https://old.example.org/2026/report"
    html_out = robustlinks.to_html(titleless, V2, _anchor(cited))
    assert f">{cited}</a>" in html_out, html_out
    md = robustlinks.to_markdown(titleless, V2, _anchor(cited))
    assert md.startswith(f"[{cited}]"), md
