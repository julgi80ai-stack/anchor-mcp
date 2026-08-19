# SPDX-License-Identifier: Apache-2.0
"""설정 표면 (SPEC §9) — 6-나 라운드: D-137 D-139~D-146 D-148 D-151 D-152 D-153.

축을 먼저 연다:

① **설정 경로 3종** — TOML 파일 / 환경변수 / 라이브러리 직접(`Config(...)`).
   같은 검증이 세 경로 모두에서 성립해야 한다. 하나만 뚫려 있으면 그 하나가
   실제 배치 경로다 (D-150이 그랬다).
② **값의 형** — 정수·실수·불리언·`inf`·`nan`·거대값·음수·0·빈 문자열·
   공백만·비ASCII·개행 포함.
③ **키 사이 관계** — min > short, enabled인데 aggregator 빈 값.
④ **파일 자체** — 비UTF-8 바이트, 섹션 자리의 스칼라·배열.
⑤ **오류 표면** — 도메인 예외(ConfigError, 이중언어)인가 생 트레이스백인가.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from anchor.config import Config, load_config
from anchor.errors import ConfigError

# 필드 → (TOML 경로, 환경변수 이름). SPEC §9가 문서화한 키 전부.
# 세 경로를 같은 표로 돌리기 위한 유일한 사전이다 — 여기 없는 필드는
# "설정할 수 없는 필드"라는 뜻이고, D-137이 바로 그 목록이었다.
DOCUMENTED_KEYS: dict[str, tuple[str, str]] = {
    "db_path": ("storage.db_path", "ANCHOR_DB_PATH"),
    "keep_versions": ("storage.keep_versions", "ANCHOR_KEEP_VERSIONS"),
    "compression": ("storage.compression", "ANCHOR_COMPRESSION"),
    "user_agent": ("fetch.user_agent", "ANCHOR_USER_AGENT"),
    "respect_robots": ("fetch.respect_robots", "ANCHOR_RESPECT_ROBOTS"),
    "timeout_seconds": ("fetch.timeout_seconds", "ANCHOR_TIMEOUT_SECONDS"),
    "max_redirects": ("fetch.max_redirects", "ANCHOR_MAX_REDIRECTS"),
    "max_content_bytes": ("fetch.max_content_mb", "ANCHOR_MAX_CONTENT_MB"),
    "default_max_age": ("fetch.default_max_age", "ANCHOR_DEFAULT_MAX_AGE"),
    "retry_backoff_base": ("fetch.retry_backoff_base", "ANCHOR_RETRY_BACKOFF_BASE"),
    "robots_ttl_seconds": ("fetch.robots_ttl_seconds", "ANCHOR_ROBOTS_TTL_SECONDS"),
    "rate_limit_rps": (
        "fetch.rate_limit.requests_per_second",
        "ANCHOR_RATE_LIMIT_RPS",
    ),
    "rate_limit_burst": ("fetch.rate_limit.burst", "ANCHOR_RATE_LIMIT_BURST"),
    "archive_fallback_enabled": (
        "fetch.archive_fallback.enabled",
        "ANCHOR_ARCHIVE_FALLBACK_ENABLED",
    ),
    "archive_aggregator": (
        "fetch.archive_fallback.aggregator",
        "ANCHOR_ARCHIVE_AGGREGATOR",
    ),
    "archive_list": (
        "fetch.archive_fallback.archive_list",
        "ANCHOR_ARCHIVE_LIST",
    ),
    "archive_timeout_seconds": (
        "fetch.archive_fallback.timeout_seconds",
        "ANCHOR_ARCHIVE_TIMEOUT_SECONDS",
    ),
    "context_chars": ("anchor.context_chars", "ANCHOR_CONTEXT_CHARS"),
    "max_edit_ratio": ("anchor.max_edit_ratio", "ANCHOR_MAX_EDIT_RATIO"),
    "max_edit_distance": ("anchor.max_edit_distance", "ANCHOR_MAX_EDIT_DISTANCE"),
    "min_quote_chars": ("anchor.min_quote_chars", "ANCHOR_MIN_QUOTE_CHARS"),
    "short_quote_chars": ("anchor.short_quote_chars", "ANCHOR_SHORT_QUOTE_CHARS"),
    "time_budget_ms": ("anchor.time_budget_ms", "ANCHOR_TIME_BUDGET_MS"),
    "hint_radius": ("anchor.hint_radius", "ANCHOR_HINT_RADIUS"),
    "max_document_bytes": ("anchor.max_document_bytes", "ANCHOR_MAX_DOCUMENT_BYTES"),
    "server_transport": ("server.transport", "ANCHOR_SERVER_TRANSPORT"),
}

# TOML의 `fetch.max_content_mb`는 MB로 받아 바이트로 저장한다 — 표에서 값을
# 그대로 비교할 수 없는 유일한 키.
_SCALED = {"max_content_bytes": 1024 * 1024}


def _toml_literal(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return str(value)


def write_toml(tmp_path: Path, pairs: dict[str, object], name: str = "config.toml") -> Path:
    """`{"fetch.timeout_seconds": 5}` 형태를 TOML 파일로 쓴다."""
    sections: dict[str, list[str]] = {}
    for dotted, value in pairs.items():
        section, _, key = dotted.rpartition(".")
        sections.setdefault(section, []).append(f"{key} = {_toml_literal(value)}")
    body = ""
    for section, lines in sections.items():
        body += f"[{section}]\n" if section else ""
        body += "\n".join(lines) + "\n\n"
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def load_via(path_kind: str, tmp_path, monkeypatch, field: str, raw: object):
    """세 경로 중 하나로 값을 넣고 Config를 만든다 — 축 ①."""
    toml_key, env_name = DOCUMENTED_KEYS[field]
    if path_kind == "toml":
        return load_config(write_toml(tmp_path, {toml_key: raw}))
    if path_kind == "env":
        monkeypatch.setenv(env_name, "true" if raw is True else "false" if raw is False else str(raw))
        return load_config(tmp_path / "absent.toml")
    scaled = raw
    if field in _SCALED and isinstance(raw, (int, float)) and not isinstance(raw, bool):
        scaled = int(raw * _SCALED[field]) if raw == raw and abs(raw) != float("inf") else raw
    return Config(**{field: scaled})


PATHS = ("toml", "env", "direct")


# -- D-137: 문서화된 키는 세 경로 모두로 설정된다 ----------------------------


def test_every_documented_key_is_a_config_field():
    names = {f.name for f in dataclasses.fields(Config)}
    assert set(DOCUMENTED_KEYS) <= names, sorted(set(DOCUMENTED_KEYS) - names)


def test_every_documented_key_has_an_env_override():
    from anchor.config import _ENV_OVERRIDES

    covered = {field for field, _ in _ENV_OVERRIDES.values()}
    assert set(DOCUMENTED_KEYS) <= covered, sorted(set(DOCUMENTED_KEYS) - covered)


def test_env_names_match_the_documented_table():
    from anchor.config import _ENV_OVERRIDES

    for field, (_, env_name) in DOCUMENTED_KEYS.items():
        assert env_name in _ENV_OVERRIDES, field
        assert _ENV_OVERRIDES[env_name][0] == field


# 이전에는 TOML로도 환경변수로도 바꿀 수 없던 4개 (D-137).
NEWLY_SETTABLE = [
    ("retry_backoff_base", 2.5, 2.5),
    ("robots_ttl_seconds", 3600, 3600),
    ("max_edit_distance", 32, 32),
    ("hint_radius", 250, 250),
    ("compression", "zstd:12", "zstd:12"),
]


@pytest.mark.parametrize("field,raw,expected", NEWLY_SETTABLE)
@pytest.mark.parametrize("path_kind", ("toml", "env"))
def test_previously_unsettable_fields_are_settable(
    path_kind, field, raw, expected, tmp_path, monkeypatch
):
    config = load_via(path_kind, tmp_path, monkeypatch, field, raw)
    assert getattr(config, field) == expected


# -- D-137: storage.compression ---------------------------------------------


def test_compression_default_is_zstd_6():
    assert Config().compression == "zstd:6"
    assert Config().zstd_level == 6


@pytest.mark.parametrize("path_kind", PATHS)
@pytest.mark.parametrize("value", ["zstd:1", "zstd:22", "ZSTD:6", " zstd:6 "])
def test_compression_accepts_supported_levels(path_kind, value, tmp_path, monkeypatch):
    config = load_via(path_kind, tmp_path, monkeypatch, "compression", value)
    assert 1 <= config.zstd_level <= 22


@pytest.mark.parametrize("path_kind", PATHS)
@pytest.mark.parametrize(
    "value",
    ["none", "gzip:6", "zstd", "zstd:0", "zstd:23", "zstd:-1", "zstd:x", "", "   "],
)
def test_compression_rejects_unsupported_codecs_and_levels(
    path_kind, value, tmp_path, monkeypatch
):
    with pytest.raises(ConfigError):
        load_via(path_kind, tmp_path, monkeypatch, "compression", value)


# -- D-139 / D-145: user_agent는 로드 시점에 검증된다 ------------------------


BAD_USER_AGENTS = [
    "",                       # 빈 값
    "   ",                    # 공백만 (D-139)
    "\t",                     # 탭만
    "Anchor/1.0\nX-Injected: 1",   # 개행 삽입 (D-139)
    "Anchor/1.0\rX: 1",
    "Anchor/1.0\x00",          # 제어문자
    "앵커/1.0",                # 비ASCII (D-145)
    "Anchor/1.0 (+https://예시.invalid)",
    " Anchor/1.0",            # 선두 공백 (RFC 9110 field value)
    "Anchor/1.0 ",            # 말미 공백
]


@pytest.mark.parametrize("path_kind", PATHS)
@pytest.mark.parametrize("value", BAD_USER_AGENTS)
def test_user_agent_is_rejected_at_load_time(path_kind, value, tmp_path, monkeypatch):
    """로컬 설정 오류가 "사이트 소유자의 거부"로 둔갑하지 않게 (D-139)."""
    if path_kind == "env" and "\x00" in value:
        pytest.skip("환경변수에는 NUL을 넣을 수 없다 (OS 제약)")
    with pytest.raises(ConfigError):
        load_via(path_kind, tmp_path, monkeypatch, "user_agent", value)


@pytest.mark.parametrize("path_kind", PATHS)
@pytest.mark.parametrize(
    "value",
    [
        "Anchor/1.1 (+https://github.com/julgi80ai-stack/anchor-mcp)",
        "A",
        "Mozilla/5.0 (compatible; Bot/1.0; +http://e.invalid/bot)",
    ],
)
def test_reasonable_user_agents_still_load(path_kind, value, tmp_path, monkeypatch):
    assert load_via(path_kind, tmp_path, monkeypatch, "user_agent", value).user_agent == value


def test_accepted_user_agent_survives_httpx_header_encoding(tmp_path):
    """검증을 통과한 UA는 실제로 헤더가 되어야 한다 — 검증의 목적이 그것이다."""
    import httpx

    config = load_config(write_toml(tmp_path, {"fetch.user_agent": "Anchor/1.1 (+https://x.invalid)"}))
    request = httpx.Client().build_request(
        "GET", "http://example.invalid/", headers={"User-Agent": config.user_agent}
    )
    assert request.headers["user-agent"] == config.user_agent


# -- D-140: 섹션 자리의 스칼라·배열 -----------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "fetch = 3\n",
        "anchor = true\n",
        'storage = "x"\n',
        "server = [1, 2]\n",
        "[fetch]\nrate_limit = 5\n",
        '[fetch]\narchive_fallback = "on"\n',
    ],
)
def test_scalar_in_a_section_slot_is_a_config_error(body, tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


# -- D-141: 비UTF-8 파일 ------------------------------------------------------


def test_non_utf8_config_file_is_a_config_error(tmp_path):
    path = tmp_path / "config.toml"
    path.write_bytes(b'[fetch]\nuser_agent = "caf\xe9"\n')
    with pytest.raises(ConfigError):
        load_config(path)


# -- D-142 / D-153: anchor.max_document_bytes --------------------------------


@pytest.mark.parametrize("path_kind", PATHS)
@pytest.mark.parametrize("value", [0, 1, -1, -2_097_152, 431])
def test_max_document_bytes_below_the_floor_is_rejected(
    path_kind, value, tmp_path, monkeypatch
):
    """0·1은 모든 앵커를 영구 UNRESOLVED로 만든다 (D-142)."""
    with pytest.raises(ConfigError):
        load_via(path_kind, tmp_path, monkeypatch, "max_document_bytes", value)


@pytest.mark.parametrize("path_kind", PATHS)
def test_max_document_bytes_accepts_a_sane_value(path_kind, tmp_path, monkeypatch):
    config = load_via(path_kind, tmp_path, monkeypatch, "max_document_bytes", 4096)
    assert config.max_document_bytes == 4096


# -- D-143: max_edit_ratio 상한 ----------------------------------------------


@pytest.mark.parametrize("path_kind", PATHS)
@pytest.mark.parametrize("value", [5.0, 1.01, 100.0, float("inf")])
def test_max_edit_ratio_above_one_is_rejected(path_kind, value, tmp_path, monkeypatch):
    """k가 인용문 길이를 넘으면 무관한 문장이 "현재 모습"이 된다 (D-143)."""
    with pytest.raises(ConfigError):
        load_via(path_kind, tmp_path, monkeypatch, "max_edit_ratio", value)


@pytest.mark.parametrize("path_kind", PATHS)
@pytest.mark.parametrize("value", [0.0, -0.15, float("nan")])
def test_max_edit_ratio_not_positive_is_rejected(path_kind, value, tmp_path, monkeypatch):
    with pytest.raises(ConfigError):
        load_via(path_kind, tmp_path, monkeypatch, "max_edit_ratio", value)


# -- D-144: anchor.context_chars ---------------------------------------------


@pytest.mark.parametrize("path_kind", PATHS)
@pytest.mark.parametrize("value", [0, -10, 7])
def test_context_chars_below_the_floor_is_rejected(path_kind, value, tmp_path, monkeypatch):
    """0이면 매처 3단계가 통째로 죽는다 — D-044가 고친 실패 모드의 부활."""
    with pytest.raises(ConfigError):
        load_via(path_kind, tmp_path, monkeypatch, "context_chars", value)


# -- D-146: MB 변환의 inf·nan·거대값 -----------------------------------------


@pytest.mark.parametrize("path_kind", ("toml", "env"))
@pytest.mark.parametrize("value", ["inf", "nan", "-inf", "1e300"])
def test_max_content_mb_non_finite_or_absurd_is_a_config_error(
    path_kind, value, tmp_path, monkeypatch
):
    with pytest.raises(ConfigError):
        if path_kind == "toml":
            path = tmp_path / "config.toml"
            path.write_text(f"[fetch]\nmax_content_mb = {value}\n", encoding="utf-8")
            load_config(path)
        else:
            monkeypatch.setenv("ANCHOR_MAX_CONTENT_MB", value)
            load_config(tmp_path / "absent.toml")


@pytest.mark.parametrize("path_kind", ("toml", "env"))
@pytest.mark.parametrize("value", ["inf", "nan"])
def test_non_finite_floats_are_rejected_everywhere(path_kind, value, tmp_path, monkeypatch):
    """`inf`·`nan`은 TOML의 정식 float 리터럴이다 — 어느 키에서도 통과하면 안 된다."""
    for field in ("timeout_seconds", "rate_limit_rps", "archive_timeout_seconds",
                  "retry_backoff_base"):
        monkeypatch.delenv(DOCUMENTED_KEYS[field][1], raising=False)
        with pytest.raises(ConfigError):
            load_via(path_kind, tmp_path, monkeypatch, field, value)
        monkeypatch.delenv(DOCUMENTED_KEYS[field][1], raising=False)


# -- D-148: 거대 정수 ---------------------------------------------------------


@pytest.mark.parametrize("path_kind", PATHS)
@pytest.mark.parametrize("value", [2**63, 2**70])
def test_keep_versions_beyond_sqlite_range_is_rejected(
    path_kind, value, tmp_path, monkeypatch
):
    with pytest.raises(ConfigError):
        load_via(path_kind, tmp_path, monkeypatch, "keep_versions", value)


# -- D-151: 조용히 통과하던 값들 ---------------------------------------------


SILENTLY_ACCEPTED = [
    ("archive_timeout_seconds", 0.0),
    ("archive_timeout_seconds", -5.0),
    ("max_redirects", 999_999_999),
    ("db_path", ""),
    ("db_path", "   "),
    ("hint_radius", -1),
    ("max_edit_distance", 0),
    ("robots_ttl_seconds", -1),
    ("robots_ttl_seconds", 86_401),   # RFC 9309 §2.4 상한
    ("retry_backoff_base", 0.0),
    ("retry_backoff_base", -1.0),
    ("short_quote_chars", 0),
]


@pytest.mark.parametrize("path_kind", PATHS)
@pytest.mark.parametrize("field,value", SILENTLY_ACCEPTED)
def test_out_of_range_values_are_rejected(path_kind, field, value, tmp_path, monkeypatch):
    with pytest.raises(ConfigError):
        load_via(path_kind, tmp_path, monkeypatch, field, value)


def test_min_quote_chars_above_short_quote_chars_is_rejected(tmp_path):
    """SHORT 품질에 도달할 수 없는 조합 — 축 ③ (D-151)."""
    with pytest.raises(ConfigError):
        load_config(
            write_toml(tmp_path, {"anchor.min_quote_chars": 40, "anchor.short_quote_chars": 10})
        )
    with pytest.raises(ConfigError):
        Config(min_quote_chars=40, short_quote_chars=10)


def test_archive_fallback_without_aggregator_warns_about_the_public_service(tmp_path, capsys):
    """빈 aggregator는 유효하지만(=Wayback CDX) 조용한 외부 의존은 아니다 (D-151)."""
    config = load_config(
        write_toml(
            tmp_path,
            {"fetch.archive_fallback.enabled": True, "fetch.archive_fallback.aggregator": ""},
        )
    )
    assert config.archive_fallback_enabled is True
    assert "Wayback" in capsys.readouterr().err


# -- D-152: 미지 키·미지 섹션은 경고 후 계속 --------------------------------


def test_unknown_key_is_warned_and_ignored(tmp_path, capsys):
    path = tmp_path / "config.toml"
    path.write_text("[fetch]\ntimeuot_seconds = 5\n", encoding="utf-8")
    config = load_config(path)
    captured = capsys.readouterr().err
    assert "timeuot_seconds" in captured
    assert config.timeout_seconds == Config().timeout_seconds  # 기본값이 쓰였다


def test_unknown_section_is_warned_and_ignored(tmp_path, capsys):
    path = tmp_path / "config.toml"
    path.write_text("[bogus]\nx = 1\n", encoding="utf-8")
    load_config(path)
    assert "bogus" in capsys.readouterr().err


def test_known_keys_do_not_warn(tmp_path, capsys):
    path = tmp_path / "config.toml"
    path.write_text(
        "[storage]\nkeep_versions = 5\n\n[fetch.rate_limit]\nburst = 2\n", encoding="utf-8"
    )
    load_config(path)
    assert capsys.readouterr().err == ""


# -- D-153: 키 이름이 약속한 바이트 의미론을 코드가 이행한다 -----------------


def _match(text, quote, **kwargs):
    from anchor.anchoring.matcher import match_anchor
    from anchor.anchoring.selector import build_selector

    selector = build_selector(text, quote)
    return match_anchor(
        text,
        exact=selector.exact,
        prefix=selector.prefix,
        suffix=selector.suffix,
        position_hint=selector.position_hint,
        budget_ms=500,
        **kwargs,
    )


# 한글은 UTF-8에서 문자당 3바이트다 — 문자 수로 재면 상한이 3배로 늘어난다.
KOREAN_LINE = "인용 표류는 링크가 살아 있는 채로 내용만 바뀌는 현상이다. "
LATIN_LINE = "Citation drift happens when the link lives on but the text changes. "


@pytest.mark.parametrize(
    "line,min_bytes_per_char",
    [(KOREAN_LINE, 2.0), (LATIN_LINE, 1.0)],
    ids=["korean", "latin"],
)
def test_document_limit_is_measured_in_bytes(line, min_bytes_per_char):
    """문자 체계 축 — 같은 바이트 상한은 문자 체계와 무관하게 같은 바이트다."""
    text = line * 400
    encoded = len(text.encode("utf-8"))
    assert encoded / len(text) >= min_bytes_per_char

    quote = line.strip()
    # 상한을 실제 바이트 수의 절반으로 두면 어떤 문자 체계에서도 잘려야 한다.
    result = _match(text, quote, max_bytes=encoded // 2)
    assert result.truncated is True
    # 상한이 실제 바이트 수보다 크면 잘리지 않는다.
    assert _match(text, quote, max_bytes=encoded + 1).truncated is False


def test_truncation_does_not_split_a_character():
    """절단이 문자 경계를 깨면 디코딩이 죽거나 대체문자가 본문에 섞인다."""
    from anchor.anchoring.matcher import match_anchor

    text = "가나다라마바사" * 500  # 3바이트/자
    # 3의 배수가 아닌 상한 — 마지막 문자의 중간에서 잘린다.
    result = match_anchor(
        text, exact="가나다라마바사", prefix="", suffix="", position_hint=0,
        budget_ms=500, max_bytes=1000,
    )
    assert result.truncated is True
    assert result.state in ("INTACT", "MOVED")  # 앞쪽에 온전히 남아 있다
    assert "�" not in (result.found_text or "")


def test_byte_limit_fast_path_avoids_encoding(monkeypatch):
    """상한이 넉넉하면 매 호출 전체 인코딩을 하지 않는다 (성능 계약)."""
    import anchor.anchoring.matcher as matcher_module

    calls = []
    original = str.encode

    class Counting(str):
        def encode(self, *args, **kwargs):  # pragma: no cover - 계수용
            calls.append(1)
            return original(self, *args, **kwargs)

    text = Counting("가나다라마바사아자차" * 100)
    matcher_module.match_anchor(
        text, exact="가나다라마바사아자차", prefix="", suffix="", position_hint=0,
        budget_ms=500, max_bytes=2_097_152,
    )
    assert calls == []


# -- D-143: 편집거리 상한 k는 인용문 길이를 넘지 못한다 ----------------------


def test_edit_budget_never_exceeds_the_quote_length():
    """k >= len(quote)이면 "전부 지우고 다른 것 넣기"가 허용 범위에 들어온다.

    실측(조치 전): 삭제된 CJK 인용문에 대해 `ALTERED, edit_distance=18,
    score=0.0, found_text=''` — **빈 문자열**이 "당신 인용문의 현재 모습"으로
    제시됐다. 설정 상한(max_edit_ratio <= 1)만으로는 막히지 않는다. 문자
    체계 계수(최대 2.5)가 곱해지기 때문이다 (D-143).
    """
    from anchor.anchoring.matcher import match_anchor
    from anchor.anchoring.selector import build_selector

    quote = "情報開示請求書は却下された案件です。"  # 남은 본문과 겹치는 글자가 없다
    head = "첫 문단입니다. 여기에는 아무 상관 없는 내용이 들어 있습니다."
    tail = "마지막 문단은 또 다른 이야기를 합니다. 여기도 겹치는 부분이 없습니다."
    selector = build_selector(f"{head}\n\n{quote}\n\n{tail}\n", quote)
    result = match_anchor(
        f"{head}\n\n{tail}\n",
        exact=selector.exact,
        prefix=selector.prefix,
        suffix=selector.suffix,
        position_hint=selector.position_hint,
        budget_ms=500,
        max_edit_ratio=1.0,
        max_edit_distance=1024,
    )
    assert result.state in ("MISSING", "UNRESOLVED"), result
    assert result.found_text is None, result
    # 판별력: 편집거리가 인용문 길이에 도달하면 그것은 "닮음"이 아니다.
    assert result.edit_distance is None or result.edit_distance < len(selector.exact)
