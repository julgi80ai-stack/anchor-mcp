# SPDX-License-Identifier: Apache-2.0
"""설정 로딩. `~/.anchor/config.toml`을 읽고 환경변수 `ANCHOR_*`가 항상 우선한다.

SPEC §9의 storage / fetch(+rate_limit, archive_fallback) / anchor / server
섹션을 지원한다. 비밀값은 파일에 두지 않는다.
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

from anchor.errors import ConfigError

DEFAULT_CONFIG_PATH = Path("~/.anchor/config.toml")
DEFAULT_DB_PATH = Path("~/.anchor/store.db")


@dataclass(frozen=True)
class Config:
    db_path: Path = DEFAULT_DB_PATH
    keep_versions: int = 20
    user_agent: str = "Anchor/1.1 (+https://github.com/julgi80ai-stack/anchor-mcp)"
    respect_robots: bool = True
    timeout_seconds: float = 30.0
    max_redirects: int = 5
    max_content_bytes: int = 8 * 1024 * 1024
    default_max_age: int = 86400
    rate_limit_rps: float = 1.0
    rate_limit_burst: int = 3
    retry_backoff_base: float = 1.0
    robots_ttl_seconds: int = 86400
    # [fetch.archive_fallback] (SPEC §5.2 6단계, §9). 기본 비활성 —
    # 외부 서비스에 조용히 의존하지 않는다.
    archive_fallback_enabled: bool = False
    archive_aggregator: str = ""  # 자체 호스팅 MemGator 엔드포인트. 비우면 Wayback CDX
    archive_list: str = ""  # MemGator 기동 시 --arcs로 넘길 목록(운영값). Anchor가 직접 쓰진 않는다
    archive_timeout_seconds: float = 20.0
    # [anchor] (SPEC §9)
    context_chars: int = 48
    max_edit_ratio: float = 0.15
    max_edit_distance: int = 64
    min_quote_chars: int = 12
    short_quote_chars: int = 32
    time_budget_ms: int = 200
    hint_radius: int = 500
    max_match_chars: int = 2_097_152
    # [server] (SPEC §9)
    server_transport: str = "stdio"  # stdio | http

    def __post_init__(self) -> None:
        """만드는 순간 검증한다 (D-098).

        `load_config`를 지나야만 검증되면, SPEC §8이 약속한 라이브러리 직접
        사용 경로가 검증 없이 통과한다 — 같은 보장을 받아야 한다.
        """
        _validate(self)


def _require(value: object, kind: type, key: str) -> object:
    """TOML 값의 타입을 확인한다. `bool("no")`가 True가 되는 식의 조용한
    오해석을 막는다 (D-031)."""
    if kind is bool:
        if not isinstance(value, bool):
            raise ConfigError(
                f"[{key}] must be a boolean (true/false), got {value!r} — "
                f"[{key}]는 참/거짓이어야 합니다 (따옴표 없는 true/false)"
            )
        return value
    if kind is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(
                f"[{key}] must be a number, got {value!r} — [{key}]는 숫자여야 합니다"
            )
        return float(value)
    if kind is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(
                f"[{key}] must be an integer, got {value!r} — [{key}]는 정수여야 합니다"
            )
        return value
    if kind is str:
        if not isinstance(value, str):
            raise ConfigError(
                f"[{key}] must be a string, got {value!r} — [{key}]는 문자열이어야 합니다"
            )
        return value
    return value


# 환경변수 이름 → Config 필드. SPEC §9 "환경변수가 항상 우선한다" (D-032).
_ENV_OVERRIDES: dict[str, tuple[str, type]] = {
    "ANCHOR_DB_PATH": ("db_path", Path),
    "ANCHOR_KEEP_VERSIONS": ("keep_versions", int),
    "ANCHOR_USER_AGENT": ("user_agent", str),
    "ANCHOR_RESPECT_ROBOTS": ("respect_robots", bool),
    "ANCHOR_TIMEOUT_SECONDS": ("timeout_seconds", float),
    "ANCHOR_MAX_REDIRECTS": ("max_redirects", int),
    "ANCHOR_MAX_CONTENT_MB": ("max_content_bytes", float),
    "ANCHOR_DEFAULT_MAX_AGE": ("default_max_age", int),
    "ANCHOR_RATE_LIMIT_RPS": ("rate_limit_rps", float),
    "ANCHOR_RATE_LIMIT_BURST": ("rate_limit_burst", int),
    "ANCHOR_ARCHIVE_FALLBACK_ENABLED": ("archive_fallback_enabled", bool),
    "ANCHOR_ARCHIVE_AGGREGATOR": ("archive_aggregator", str),
    "ANCHOR_TIME_BUDGET_MS": ("time_budget_ms", int),
    "ANCHOR_SERVER_TRANSPORT": ("server_transport", str),
}


def _coerce_env(raw: str, kind: type, name: str) -> object:
    try:
        if kind is bool:
            lowered = raw.strip().lower()
            if lowered in ("1", "true", "yes", "on"):
                return True
            if lowered in ("0", "false", "no", "off"):
                return False
            raise ValueError(raw)
        if kind is Path:
            return Path(raw)
        return kind(raw)
    except (TypeError, ValueError) as error:
        raise ConfigError(
            f"{name}={raw!r} is not a valid {kind.__name__} — "
            f"{name} 값을 해석할 수 없습니다"
        ) from error


def load_config(path: Path | None = None) -> Config:
    # 겹침(TOML → 환경변수)을 **딕셔너리에서** 끝내고 Config는 한 번만 만든다.
    # 중간 상태의 Config를 만들면 생성자 검증(D-098)이 거기서 터진다 — 파일의
    # 잘못된 값을 환경변수가 덮도록 배포한 구성에서 서버가 아예 뜨지 않고,
    # 오류는 사용자가 이미 덮어 놓은 값을 가리킨다 (D-188, SPEC §9 "환경변수가
    # 항상 우선"). 검증은 최종 상태에 대해서만 의미가 있다.
    merged: dict[str, object] = {}

    config_path = (path or DEFAULT_CONFIG_PATH).expanduser()
    if config_path.is_file():
        try:
            with config_path.open("rb") as fp:
                data = tomllib.load(fp)
        except tomllib.TOMLDecodeError as error:
            raise ConfigError(
                f"{config_path} is not valid TOML: {error} — 설정 파일 문법 오류"
            ) from error
        except OSError as error:
            raise ConfigError(
                f"cannot read {config_path}: {error} — 설정 파일을 읽을 수 없습니다"
            ) from error

        storage = data.get("storage", {})
        fetch = data.get("fetch", {})
        rate = fetch.get("rate_limit", {})
        fallback = fetch.get("archive_fallback", {})
        anchor_section = data.get("anchor", {})
        server_section = data.get("server", {})
        overrides: dict[str, object] = {}

        simple: list[tuple[dict, str, str, type]] = [
            (storage, "keep_versions", "storage.keep_versions", int),
            (fetch, "user_agent", "fetch.user_agent", str),
            (fetch, "respect_robots", "fetch.respect_robots", bool),
            (fetch, "timeout_seconds", "fetch.timeout_seconds", float),
            (fetch, "max_redirects", "fetch.max_redirects", int),
            (fetch, "default_max_age", "fetch.default_max_age", int),
            (rate, "requests_per_second", "fetch.rate_limit.requests_per_second", float),
            (rate, "burst", "fetch.rate_limit.burst", int),
            (fallback, "enabled", "fetch.archive_fallback.enabled", bool),
            (fallback, "aggregator", "fetch.archive_fallback.aggregator", str),
            (fallback, "archive_list", "fetch.archive_fallback.archive_list", str),
            (fallback, "timeout_seconds", "fetch.archive_fallback.timeout_seconds", float),
            (anchor_section, "context_chars", "anchor.context_chars", int),
            (anchor_section, "max_edit_ratio", "anchor.max_edit_ratio", float),
            (anchor_section, "min_quote_chars", "anchor.min_quote_chars", int),
            (anchor_section, "short_quote_chars", "anchor.short_quote_chars", int),
            (anchor_section, "time_budget_ms", "anchor.time_budget_ms", int),
            (anchor_section, "max_document_bytes", "anchor.max_document_bytes", int),
            (server_section, "transport", "server.transport", str),
        ]
        field_names = {
            "keep_versions": "keep_versions",
            "user_agent": "user_agent",
            "respect_robots": "respect_robots",
            "timeout_seconds": "timeout_seconds",
            "max_redirects": "max_redirects",
            "default_max_age": "default_max_age",
            "requests_per_second": "rate_limit_rps",
            "burst": "rate_limit_burst",
            "enabled": "archive_fallback_enabled",
            "aggregator": "archive_aggregator",
            "archive_list": "archive_list",
            "context_chars": "context_chars",
            "max_edit_ratio": "max_edit_ratio",
            "min_quote_chars": "min_quote_chars",
            "short_quote_chars": "short_quote_chars",
            "time_budget_ms": "time_budget_ms",
            "max_document_bytes": "max_match_chars",
            "transport": "server_transport",
        }
        for section, key, label, kind in simple:
            if key not in section:
                continue
            value = _require(section[key], kind, label)
            if section is fallback and key == "timeout_seconds":
                overrides["archive_timeout_seconds"] = value
            else:
                overrides[field_names[key]] = value

        if "db_path" in storage:
            overrides["db_path"] = Path(_require(storage["db_path"], str, "storage.db_path"))
        if "max_content_mb" in fetch:
            # 소수를 받을 수 있어야 한다 — int()로 절삭하면 0.5가 0바이트가 된다 (D-029).
            megabytes = _require(fetch["max_content_mb"], float, "fetch.max_content_mb")
            overrides["max_content_bytes"] = int(float(megabytes) * 1024 * 1024)
        merged.update(overrides)

    for name, (field_name, kind) in _ENV_OVERRIDES.items():
        raw = os.environ.get(name)
        if raw is None:
            continue
        value = _coerce_env(raw, kind, name)
        if field_name == "max_content_bytes":
            value = int(float(value) * 1024 * 1024)
        merged[field_name] = value

    config = Config(**merged)  # 생성 = 최종 상태 검증 (__post_init__, D-098)

    if not config.respect_robots:
        print(
            "경고: respect_robots=false — robots.txt를 무시하도록 설정되어 있습니다 (SPEC §5.4).",
            file=sys.stderr,
        )
    return config


def _validate(config: Config) -> None:
    """값의 범위를 로드 시점에 확인한다 — 첫 페치 도중 죽지 않도록 (D-010/D-029)."""
    checks: list[tuple[bool, str]] = [
        (config.rate_limit_rps > 0, "fetch.rate_limit.requests_per_second must be > 0"),
        (config.rate_limit_burst >= 1, "fetch.rate_limit.burst must be >= 1"),
        (config.max_content_bytes > 0, "fetch.max_content_mb must be > 0"),
        (config.timeout_seconds > 0, "fetch.timeout_seconds must be > 0"),
        (config.max_redirects >= 0, "fetch.max_redirects must be >= 0"),
        (config.default_max_age >= 0, "fetch.default_max_age must be >= 0"),
        (config.keep_versions >= 1, "storage.keep_versions must be >= 1"),
        (config.min_quote_chars >= 1, "anchor.min_quote_chars must be >= 1"),
        (config.time_budget_ms > 0, "anchor.time_budget_ms must be > 0"),
        (config.max_edit_ratio > 0, "anchor.max_edit_ratio must be > 0"),
        # 자기를 밝히지 않는 요청은 보내지 않는다 (D-098, SPEC §5.4). 빈 UA는
        # robots 매칭도 빈 토큰으로 하게 만든다 — 규칙을 지키겠다면서 누구인지
        # 말하지 않는 것이다.
        (bool(config.user_agent.strip()), "fetch.user_agent must not be blank"),
        (
            config.server_transport in ("stdio", "http"),
            "server.transport must be 'stdio' or 'http'",
        ),
    ]
    for ok, message in checks:
        if not ok:
            raise ConfigError(f"{message} — 설정값 범위 오류")
