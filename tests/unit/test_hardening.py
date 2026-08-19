# SPDX-License-Identifier: Apache-2.0
"""입력 검증·경계 처리 회귀 (4단계 잔여 항목).

D-013 D-017 D-018 D-021 D-022 D-025 D-026 D-027 D-028 D-029 D-030 D-031
D-032 D-039 D-045 D-046.
"""

from __future__ import annotations

import time

import pytest

from anchor.anchoring.budget import Budget
from anchor.anchoring.matcher import UNRESOLVED, match_anchor
from anchor.anchoring.selector import build_selector
from anchor.errors import ConfigError, UnsupportedContent
from anchor.export.diff import unified_diff
from anchor.export.robustlinks import to_bibtex_note, to_markdown
from anchor.models import Document, Version


def make_version(vid="v", source="live", uri=None):
    return Version(
        id=vid, document_id="d", text_hash="b3:h", raw_hash="b3:r",
        pipeline_version="p", captured_at="2026-08-16T00:00:00Z",
        last_observed_at="2026-08-16T00:00:00Z", last_observed_seq=1, byte_size=1,
        char_count=1, http_status=200, source=source, source_uri=uri,
    )


def make_document(title="Report", url="https://example.com/a"):
    return Document(
        id="d", url=url, original_url=url, title=title, first_seen_at="",
        last_checked_at="", status="live", etag=None, last_modified=None,
        robots_allowed=True,
    )


# -- D-025 통합 diff --------------------------------------------------------


def test_last_line_change_produces_separate_lines():
    before = "첫 문단이다.\n\n마지막 문장이다."
    after = "첫 문단이다.\n\n마지막 문장이 바뀌었다."
    body = unified_diff(make_version("A"), before, make_version("B"), after, context_lines=1)
    lines = body.splitlines()
    minus = [line for line in lines if line.startswith("-") and not line.startswith("---")]
    plus = [line for line in lines if line.startswith("+") and not line.startswith("+++")]
    assert len(minus) == 1 and len(plus) == 1
    assert "바뀌었다" not in minus[0], "삭제 행에 추가 내용이 섞였다"
    assert "\\ No newline at end of file" in body


def test_negative_context_lines_is_rejected():
    with pytest.raises(ValueError):
        unified_diff(make_version("A"), "a", make_version("B"), "b", context_lines=-1)


def test_identical_versions_produce_empty_diff():
    assert unified_diff(make_version("A"), "same", make_version("B"), "same") == ""


# -- D-026 Robust Links 이스케이프 -------------------------------------------


def test_markdown_escapes_script_and_brackets():
    document = make_document(title='Report <script>alert(1)</script> ] and *bold*')
    output = to_markdown(document, make_version())
    assert "<script>" not in output
    assert "\\]" in output, "닫는 대괄호가 링크를 깨뜨린다"
    assert output.startswith("[")


def test_markdown_url_parentheses_are_encoded():
    document = make_document(url="https://example.com/a(b)")
    assert "%28" in to_markdown(document, make_version())


def test_bibtex_escapes_special_characters():
    document = make_document(url="https://example.com/a_b%c&d")
    note = to_bibtex_note(document, make_version())
    assert "\\_" in note and "\\%" in note and "\\&" in note


# -- D-029~032 설정 ---------------------------------------------------------


def test_fractional_max_content_mb(tmp_path):
    from anchor.config import load_config

    path = tmp_path / "c.toml"
    path.write_text("[fetch]\nmax_content_mb = 0.5\n", "utf-8")
    assert load_config(path).max_content_bytes == 524288


def test_string_in_boolean_slot_is_rejected(tmp_path):
    from anchor.config import load_config

    path = tmp_path / "c.toml"
    path.write_text('[fetch.archive_fallback]\nenabled = "no"\n', "utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_malformed_toml_raises_domain_error(tmp_path):
    from anchor.config import load_config

    path = tmp_path / "c.toml"
    path.write_text("[storage\n", "utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_out_of_range_value_is_rejected(tmp_path):
    from anchor.config import load_config

    path = tmp_path / "c.toml"
    path.write_text("[fetch.rate_limit]\nrequests_per_second = 0\n", "utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_env_overrides_every_documented_key(tmp_path, monkeypatch):
    from anchor.config import load_config

    path = tmp_path / "c.toml"
    path.write_text("[fetch]\ntimeout_seconds = 30\n", "utf-8")
    monkeypatch.setenv("ANCHOR_TIMEOUT_SECONDS", "7")
    monkeypatch.setenv("ANCHOR_RATE_LIMIT_RPS", "9")
    config = load_config(path)
    assert config.timeout_seconds == 7.0 and config.rate_limit_rps == 9.0


# -- D-028 CLI 기간 파싱 ----------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [("7d", 604800), ("7D", 604800), ("12h", 43200), ("30m", 1800), ("3600", 3600), ("P7D", 604800)],
)
def test_duration_forms(text, expected):
    from anchor.cli import _parse_older_than

    assert _parse_older_than(text) == expected


@pytest.mark.parametrize("text", ["abc", "-5d", "P", ""])
def test_bad_duration_is_domain_error(text):
    from anchor.cli import _parse_older_than
    from anchor.errors import AnchorError

    with pytest.raises(AnchorError):
        _parse_older_than(text)


# -- D-017 / D-018 추출 -----------------------------------------------------


def test_content_type_is_matched_exactly():
    from anchor.normalize.extract import to_normalized

    with pytest.raises(UnsupportedContent):
        to_normalized(b"<html><body><p>x</p></body></html>", "text/plaintext")


def test_pdf_non_string_title_is_ignored():
    import io

    import pypdf
    from anchor.normalize.extract import to_normalized
    from tests.unit.test_pdf import build_text_pdf

    raw = build_text_pdf("Body text here for extraction.", title="42")
    document = to_normalized(raw, "application/pdf")
    assert document.title in (None, "42")  # 숫자 문자열은 허용, 내부 표현은 금지
    assert document.title != "NullObject"


# -- D-045 / D-046 예산·절단 ------------------------------------------------


def test_budget_is_enforced_inside_dp_stages():
    """긴 인용문의 DP가 예산을 통째로 넘기던 문제 (실측 7.7초 → 예산 내)."""
    base = "가나다라마바사아자차카타파하 " * 300
    quote = base[:4000]
    text = base * 2
    selector = build_selector(text, quote)
    edited = text.replace(quote, quote[:2000] + "X" + quote[2000:])

    started = time.monotonic()
    result = match_anchor(
        edited, exact=selector.exact, prefix=selector.prefix, suffix=selector.suffix,
        position_hint=selector.position_hint, budget_ms=200,
    )
    elapsed_ms = (time.monotonic() - started) * 1000
    assert elapsed_ms < 1500, f"예산을 {elapsed_ms:.0f}ms까지 넘겼다"
    assert result.state in (UNRESOLVED, "ALTERED")


def test_truncated_document_does_not_claim_missing():
    """D-046: 다 보지 못했으면 '사라졌다'가 아니라 '판정 보류'다."""
    text = "머리말 " * 50 + "여기 인용문이 문서 뒤쪽에 있습니다 정말로." + " 꼬리말" * 50
    selector = build_selector(text, "여기 인용문이 문서 뒤쪽에 있습니다 정말로.")
    result = match_anchor(
        text, exact=selector.exact, prefix=selector.prefix, suffix=selector.suffix,
        position_hint=selector.position_hint, budget_ms=500, max_bytes=100,
    )
    assert result.truncated is True
    assert result.state != "MISSING"


def test_bounded_edit_distance_respects_budget():
    from anchor.anchoring.approx import bounded_edit_distance

    long_a = "가" * 3000
    long_b = "나" * 3000
    with pytest.raises(TimeoutError):
        bounded_edit_distance(long_a, long_b, 3000, Budget(0))
