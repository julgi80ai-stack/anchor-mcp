# SPDX-License-Identifier: Apache-2.0
"""설정 로딩. `~/.anchor/config.toml`을 읽고 환경변수 `ANCHOR_*`가 항상 우선한다.

SPEC §9의 storage / fetch(+rate_limit, archive_fallback) / anchor / server
섹션을 지원한다. 비밀값은 파일에 두지 않는다.

문서화된 키는 **세 경로 모두**로 설정된다: TOML 파일 / 환경변수 / 라이브러리
직접(`Config(...)`). 한 경로만 검증하면 나머지 둘이 실제 배치 경로가 된다
(D-137·D-150). 값 검증은 겹침이 끝난 **최종 상태**에 대해 `Config` 생성
시점에 한 번 한다 (D-188).
"""

from __future__ import annotations

import math
import os
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from anchor import __version__
from anchor.errors import ConfigError

DEFAULT_CONFIG_PATH = Path("~/.anchor/config.toml")
DEFAULT_DB_PATH = Path("~/.anchor/store.db")

# MCP 전송 방식. 세 진입점(설정 파일 `_validate`, `anchor serve`, `anchor-mcp`)이
# **같은 사전**을 본다 — 따로 적어 두면 한 곳만 뚫린다 (D-150).
SERVER_TRANSPORTS = ("stdio", "http")

# SQLite의 정수 범위. 이 밖의 값은 로드는 통과하고 첫 쿼리에서 OverflowError로
# 죽는다 (D-148).
_SQLITE_MAX_INT = 2**63 - 1
# 응답 본문 상한의 상한. 1e300 MB 같은 값이 통과하면 "상한이 있다"는 말이
# 사실이 아니게 된다 (D-146).
_MAX_CONTENT_CEILING = 2**40  # 1 TiB
# RFC 9309 §2.4: robots.txt 캐시는 24시간을 넘기지 않는다.
_ROBOTS_TTL_CEILING = 86_400
# 매처 3단계가 문맥을 표지로 쓸 때의 최소 폭. matcher._context_supports의
# `slack = max(8, …)`과 같은 바닥이다 — 이보다 짧은 문맥은 표지가 못 된다.
_MIN_CONTEXT_CHARS = 8
# 인용문 하나(min 12자)와 그 앞뒤 문맥(48자×2)조차 담지 못하는 상한은 어떤
# 앵커도 해소할 수 없게 만든다 — 최악(UTF-8 4바이트/자) 기준 432바이트 (D-142).
_MIN_DOCUMENT_BYTES = 1024
# httpx가 헤더를 ASCII로 인코딩한다. RFC 9110 field-value의 가시 ASCII만
# 허용하고 선두·말미 공백과 제어문자를 막는다 (D-139·D-145).
_FIELD_VALUE_RE = re.compile(r"^[\x21-\x7e](?:[\x20-\x7e\t]*[\x21-\x7e])?$")

# 기본 UA는 **릴리스 버전을 따라간다** (SPEC §5.4: `Anchor/<릴리스 버전>`).
# 손으로 적어 두면 버전을 올릴 때마다 어긋나고, 사양이 약속한 것과 우리가
# 실제로 밝히는 신원이 달라진다 — 정직한 클라이언트의 첫 조건은 자기를
# 사실대로 말하는 것이다 (D-221).
_DEFAULT_USER_AGENT = f"Anchor/{__version__} (+https://github.com/julgi80ai-stack/anchor-mcp)"

_COMPRESSION_CODEC = "zstd"
_ZSTD_MIN_LEVEL, _ZSTD_MAX_LEVEL = 1, 22


@dataclass(frozen=True)
class Config:
    db_path: Path = DEFAULT_DB_PATH
    keep_versions: int = 20
    # 본문 압축 코덱과 레벨. zstd 프레임은 자기서술적이라 레벨을 바꿔도 이미
    # 저장된 버전은 그대로 읽힌다 — 마이그레이션이 필요 없다 (D-137).
    compression: str = "zstd:6"
    user_agent: str = _DEFAULT_USER_AGENT
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
    # 키 이름이 바이트라고 말하므로 코드도 바이트로 잰다 (D-153). 문자 수로
    # 재면 UTF-8 한글(3바이트/자)에서 문서화된 상한의 3배까지 통과한다.
    max_document_bytes: int = 2_097_152
    # [server] (SPEC §9)
    server_transport: str = "stdio"  # stdio | http

    def __post_init__(self) -> None:
        """만드는 순간 검증한다 (D-098).

        `load_config`를 지나야만 검증되면, SPEC §8이 약속한 라이브러리 직접
        사용 경로가 검증 없이 통과한다 — 같은 보장을 받아야 한다.
        """
        _validate(self)

    @property
    def zstd_level(self) -> int:
        """`compression`이 지시하는 zstd 레벨. 검증을 통과한 값만 여기 온다."""
        return _parse_compression(self.compression)


def _parse_compression(value: object) -> int:
    """`"zstd:N"`을 레벨로 해석한다 (D-137).

    `none`이나 다른 코덱은 지원하지 않는다 — 저장 포맷이 하나여야 어떤 버전을
    읽든 같은 해제 경로를 쓴다. 지원하지 않는 값은 조용히 무시하지 않고 거절한다.
    """
    if not isinstance(value, str):
        raise ConfigError(
            f"storage.compression must be a string, got {value!r} — "
            "storage.compression은 문자열이어야 합니다"
        )
    codec, _, level = value.strip().lower().partition(":")
    message = (
        f"storage.compression must be 'zstd:N' with N in "
        f"{_ZSTD_MIN_LEVEL}..{_ZSTD_MAX_LEVEL}, got {value!r} — "
        f"storage.compression은 'zstd:N'만 지원합니다 "
        f"(none·다른 코덱 미지원, N은 {_ZSTD_MIN_LEVEL}~{_ZSTD_MAX_LEVEL})"
    )
    if codec != _COMPRESSION_CODEC or not level:
        raise ConfigError(message)
    try:
        parsed = int(level)
    except ValueError as error:
        raise ConfigError(message) from error
    if not _ZSTD_MIN_LEVEL <= parsed <= _ZSTD_MAX_LEVEL:
        raise ConfigError(message)
    return parsed


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


def _require_table(value: object, label: str) -> dict:
    """섹션 자리에 스칼라·배열이 오면 도메인 예외로 (D-140).

    `AttributeError`/`TypeError`가 생으로 새면 CLI·MCP 서버가 트레이스백으로
    죽고, 문자열이면 `in`이 부분문자열 검사로 성립해 섹션이 **조용히** 무시된다.
    """
    if not isinstance(value, dict):
        raise ConfigError(
            f"[{label}] must be a table (section), got {value!r} — "
            f"[{label}]는 섹션이어야 합니다 (`[{label}]` 아래에 키를 적으세요)"
        )
    return value


# (섹션 경로) → {TOML 키: (Config 필드, 형)}. 파싱과 미지 키 경고(D-152)가
# **같은 표**를 본다 — 표가 갈라지면 새 키를 더할 때 한쪽만 갱신돼 경고가
# 거짓말을 한다.
_TOML_SCHEMA: dict[str, dict[str, tuple[str, type]]] = {
    "storage": {
        "db_path": ("db_path", str),
        "keep_versions": ("keep_versions", int),
        "compression": ("compression", str),
    },
    "fetch": {
        "user_agent": ("user_agent", str),
        "respect_robots": ("respect_robots", bool),
        "timeout_seconds": ("timeout_seconds", float),
        "max_redirects": ("max_redirects", int),
        "max_content_mb": ("max_content_bytes", float),
        "default_max_age": ("default_max_age", int),
        "retry_backoff_base": ("retry_backoff_base", float),
        "robots_ttl_seconds": ("robots_ttl_seconds", int),
    },
    "fetch.rate_limit": {
        "requests_per_second": ("rate_limit_rps", float),
        "burst": ("rate_limit_burst", int),
    },
    "fetch.archive_fallback": {
        "enabled": ("archive_fallback_enabled", bool),
        "aggregator": ("archive_aggregator", str),
        "archive_list": ("archive_list", str),
        "timeout_seconds": ("archive_timeout_seconds", float),
    },
    "anchor": {
        "context_chars": ("context_chars", int),
        "max_edit_ratio": ("max_edit_ratio", float),
        "max_edit_distance": ("max_edit_distance", int),
        "min_quote_chars": ("min_quote_chars", int),
        "short_quote_chars": ("short_quote_chars", int),
        "time_budget_ms": ("time_budget_ms", int),
        "hint_radius": ("hint_radius", int),
        "max_document_bytes": ("max_document_bytes", int),
    },
    "server": {
        "transport": ("server_transport", str),
    },
}
_TOP_SECTIONS = ("storage", "fetch", "anchor", "server")
_SUBSECTIONS: dict[str, tuple[str, ...]] = {"fetch": ("rate_limit", "archive_fallback")}


# 환경변수 이름 → Config 필드. SPEC §9 "환경변수가 항상 우선한다" (D-032).
# 문서화된 키는 **빠짐없이** 여기에 있어야 한다 — 7개가 빠져 있던 것이
# D-137이다.
_ENV_OVERRIDES: dict[str, tuple[str, type]] = {
    "ANCHOR_DB_PATH": ("db_path", Path),
    "ANCHOR_KEEP_VERSIONS": ("keep_versions", int),
    "ANCHOR_COMPRESSION": ("compression", str),
    "ANCHOR_USER_AGENT": ("user_agent", str),
    "ANCHOR_RESPECT_ROBOTS": ("respect_robots", bool),
    "ANCHOR_TIMEOUT_SECONDS": ("timeout_seconds", float),
    "ANCHOR_MAX_REDIRECTS": ("max_redirects", int),
    "ANCHOR_MAX_CONTENT_MB": ("max_content_bytes", float),
    "ANCHOR_DEFAULT_MAX_AGE": ("default_max_age", int),
    "ANCHOR_RETRY_BACKOFF_BASE": ("retry_backoff_base", float),
    "ANCHOR_ROBOTS_TTL_SECONDS": ("robots_ttl_seconds", int),
    "ANCHOR_RATE_LIMIT_RPS": ("rate_limit_rps", float),
    "ANCHOR_RATE_LIMIT_BURST": ("rate_limit_burst", int),
    "ANCHOR_ARCHIVE_FALLBACK_ENABLED": ("archive_fallback_enabled", bool),
    "ANCHOR_ARCHIVE_AGGREGATOR": ("archive_aggregator", str),
    "ANCHOR_ARCHIVE_LIST": ("archive_list", str),
    "ANCHOR_ARCHIVE_TIMEOUT_SECONDS": ("archive_timeout_seconds", float),
    "ANCHOR_CONTEXT_CHARS": ("context_chars", int),
    "ANCHOR_MAX_EDIT_RATIO": ("max_edit_ratio", float),
    "ANCHOR_MAX_EDIT_DISTANCE": ("max_edit_distance", int),
    "ANCHOR_MIN_QUOTE_CHARS": ("min_quote_chars", int),
    "ANCHOR_SHORT_QUOTE_CHARS": ("short_quote_chars", int),
    "ANCHOR_TIME_BUDGET_MS": ("time_budget_ms", int),
    "ANCHOR_HINT_RADIUS": ("hint_radius", int),
    "ANCHOR_MAX_DOCUMENT_BYTES": ("max_document_bytes", int),
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


def _megabytes_to_bytes(value: float, label: str) -> int:
    """MB → 바이트. 변환 자체가 실패할 수 있다 (D-146).

    `inf`·`nan`은 TOML의 정식 float 리터럴이라 파싱을 통과한다. 변환을
    try 밖에 두면 `OverflowError`/`ValueError`가 생으로 새어 전 CLI·MCP
    서버가 트레이스백으로 죽는다.
    """
    if not math.isfinite(value):
        raise ConfigError(
            f"{label} must be a finite number, got {value!r} — "
            f"{label}에는 inf·nan을 쓸 수 없습니다"
        )
    try:
        return int(value * 1024 * 1024)
    except (OverflowError, ValueError) as error:  # pragma: no cover — 방어
        raise ConfigError(
            f"{label}={value!r} cannot be converted to bytes — "
            f"{label} 값을 바이트로 환산할 수 없습니다"
        ) from error


def _read_section(
    table: object, path: str, overrides: dict[str, object], unknown: list[str]
) -> None:
    values = _require_table(table, path)
    schema = _TOML_SCHEMA[path]
    children = _SUBSECTIONS.get(path, ())
    for key, value in values.items():
        if key in children:
            _read_section(value, f"{path}.{key}", overrides, unknown)
            continue
        if key not in schema:
            unknown.append(f"{path}.{key}")
            continue
        field, kind = schema[key]
        label = f"{path}.{key}"
        checked = _require(value, kind, label)
        if field == "max_content_bytes":
            # 소수를 받을 수 있어야 한다 — int()로 절삭하면 0.5가 0바이트가 된다 (D-029).
            checked = _megabytes_to_bytes(float(checked), label)
        elif field == "db_path":
            checked = Path(str(checked))
        overrides[field] = checked


def load_config(path: Path | None = None) -> Config:
    # 겹침(TOML → 환경변수)을 **딕셔너리에서** 끝내고 Config는 한 번만 만든다.
    # 중간 상태의 Config를 만들면 생성자 검증(D-098)이 거기서 터진다 — 파일의
    # 잘못된 값을 환경변수가 덮도록 배포한 구성에서 서버가 아예 뜨지 않고,
    # 오류는 사용자가 이미 덮어 놓은 값을 가리킨다 (D-188, SPEC §9 "환경변수가
    # 항상 우선"). 검증은 최종 상태에 대해서만 의미가 있다.
    merged: dict[str, object] = {}
    unknown: list[str] = []

    config_path = (path or DEFAULT_CONFIG_PATH).expanduser()
    if config_path.is_file():
        try:
            with config_path.open("rb") as fp:
                data = tomllib.load(fp)
        except tomllib.TOMLDecodeError as error:
            raise ConfigError(
                f"{config_path} is not valid TOML: {error} — 설정 파일 문법 오류"
            ) from error
        except UnicodeDecodeError as error:
            # TOML은 UTF-8이다(toml.io). 비UTF-8 바이트는 문법 오류처럼
            # 사실대로 알린다 — 생 UnicodeDecodeError로 죽지 않는다 (D-141).
            raise ConfigError(
                f"{config_path} is not valid UTF-8: {error} — "
                "설정 파일은 UTF-8이어야 합니다"
            ) from error
        except OSError as error:
            raise ConfigError(
                f"cannot read {config_path}: {error} — 설정 파일을 읽을 수 없습니다"
            ) from error

        for name, table in data.items():
            if name not in _TOP_SECTIONS:
                unknown.append(name)
                continue
            _read_section(table, name, merged, unknown)

    for name, (field_name, kind) in _ENV_OVERRIDES.items():
        raw = os.environ.get(name)
        if raw is None:
            continue
        value = _coerce_env(raw, kind, name)
        if field_name == "max_content_bytes":
            value = _megabytes_to_bytes(float(value), name)
        merged[field_name] = value

    for key in unknown:
        # 오류가 아니라 경고다 — 앞으로 생길 키를 쓰는 설정 파일이 구버전에서
        # 아예 뜨지 않으면 전방 호환이 깨진다. 다만 조용히 무시하면 오타 하나가
        # 기본값을 쓰게 만들고 그 사실을 아무도 모른다 (D-152).
        print(
            f"경고: 알 수 없는 설정 키 `{key}` — 무시했습니다 "
            f"(unknown config key, ignored)",
            file=sys.stderr,
        )

    config = Config(**merged)  # 생성 = 최종 상태 검증 (__post_init__, D-098)

    if not config.respect_robots:
        print(
            "경고: respect_robots=false — robots.txt를 무시하도록 설정되어 있습니다 (SPEC §5.4).",
            file=sys.stderr,
        )
    if config.archive_fallback_enabled and not config.archive_aggregator.strip():
        # 빈 aggregator는 유효한 설정이지만(공개 Wayback CDX 사용) 그 사실을
        # 말하지 않으면 조용한 외부 의존이 된다 (D-151, SPEC §5.2 6단계).
        print(
            "경고: archive_fallback.aggregator가 비어 있어 공개 Wayback CDX를 사용합니다 "
            "(falling back to the public Wayback CDX API).",
            file=sys.stderr,
        )
    return config


def _positive_finite(value: float) -> bool:
    return math.isfinite(value) and value > 0


def _validate(config: Config) -> None:
    """값의 범위를 로드 시점에 확인한다 — 첫 페치 도중 죽지 않도록 (D-010/D-029).

    검증은 **세 경로가 공유한다**(TOML·환경변수·`Config(...)`). 여기 없는
    규칙은 첫 페치·첫 매칭·첫 gc에서 트레이스백으로 나타난다.
    """
    _parse_compression(config.compression)  # D-137: 형식·범위를 여기서 확정한다
    _validate_user_agent(config.user_agent)
    checks: list[tuple[bool, str]] = [
        # 빈 경로는 `Path('.')`가 되어 저장소 계층에서 디렉터리를 연다 (D-151·D-138).
        (
            str(config.db_path).strip() not in ("", "."),
            "storage.db_path must not be empty",
        ),
        (_positive_finite(config.rate_limit_rps), "fetch.rate_limit.requests_per_second must be a finite number > 0"),
        (config.rate_limit_burst >= 1, "fetch.rate_limit.burst must be >= 1"),
        (
            0 < config.max_content_bytes <= _MAX_CONTENT_CEILING,
            f"fetch.max_content_mb must be > 0 and <= {_MAX_CONTENT_CEILING // (1024 * 1024)} MB",
        ),
        (_positive_finite(config.timeout_seconds), "fetch.timeout_seconds must be a finite number > 0"),
        # 상한이 없으면 리다이렉트 사슬 하나가 사실상 무한 루프가 된다 (D-151).
        (0 <= config.max_redirects <= 20, "fetch.max_redirects must be between 0 and 20"),
        (0 <= config.default_max_age <= _SQLITE_MAX_INT, "fetch.default_max_age must be >= 0"),
        # 0·음수 백오프는 실패한 호스트를 즉시 다시 때린다 (SPEC §5.4 정중함).
        (
            _positive_finite(config.retry_backoff_base) and config.retry_backoff_base <= 60,
            "fetch.retry_backoff_base must be a finite number in (0, 60]",
        ),
        # RFC 9309 §2.4 — 24시간을 넘겨 캐시하지 않는다.
        (
            0 <= config.robots_ttl_seconds <= _ROBOTS_TTL_CEILING,
            f"fetch.robots_ttl_seconds must be between 0 and {_ROBOTS_TTL_CEILING} (RFC 9309 §2.4)",
        ),
        (
            _positive_finite(config.archive_timeout_seconds),
            "fetch.archive_fallback.timeout_seconds must be a finite number > 0",
        ),
        # gc가 LIMIT/OFFSET으로 쓴다 — SQLite 정수 범위를 넘으면 그때 죽는다 (D-148).
        (
            1 <= config.keep_versions <= _SQLITE_MAX_INT,
            f"storage.keep_versions must be between 1 and {_SQLITE_MAX_INT}",
        ),
        (config.min_quote_chars >= 1, "anchor.min_quote_chars must be >= 1"),
        (config.short_quote_chars >= 1, "anchor.short_quote_chars must be >= 1"),
        # min > short면 어떤 인용문도 SHORT 품질에 도달하지 못한다 (D-151).
        (
            config.min_quote_chars <= config.short_quote_chars,
            "anchor.min_quote_chars must be <= anchor.short_quote_chars",
        ),
        (config.time_budget_ms > 0, "anchor.time_budget_ms must be > 0"),
        # k가 인용문 길이를 넘으면 무관한 문장이 "개정된 인용문"으로 제시된다 (D-143).
        (
            _positive_finite(config.max_edit_ratio) and config.max_edit_ratio <= 1.0,
            "anchor.max_edit_ratio must be in (0, 1]",
        ),
        (config.max_edit_distance >= 1, "anchor.max_edit_distance must be >= 1"),
        (config.hint_radius >= 0, "anchor.hint_radius must be >= 0"),
        # 0이면 prefix/suffix가 비어 매처 3단계가 통째로 죽는다 (D-144).
        (
            config.context_chars >= _MIN_CONTEXT_CHARS,
            f"anchor.context_chars must be >= {_MIN_CONTEXT_CHARS}",
        ),
        # 0·1은 모든 앵커를 영구 UNRESOLVED로 만든다 (D-142).
        (
            config.max_document_bytes >= _MIN_DOCUMENT_BYTES,
            f"anchor.max_document_bytes must be >= {_MIN_DOCUMENT_BYTES}",
        ),
        (
            config.server_transport in SERVER_TRANSPORTS,
            f"server.transport must be one of {SERVER_TRANSPORTS}",
        ),
    ]
    for ok, message in checks:
        if not ok:
            raise ConfigError(f"{message} — 설정값 범위 오류")


def _validate_user_agent(value: object) -> None:
    """UA는 **로드 시점에** 검증한다 (D-139·D-145, SPEC §5.4·§9).

    자기를 밝히지 않는 요청은 보내지 않는다(빈 UA는 robots 매칭도 빈 토큰으로
    하게 만든다 — 규칙을 지키겠다면서 누구인지 말하지 않는 것이다). 그 위에,
    개행·제어문자가 들어가면 httpx가 `LocalProtocolError`를 던지고 `RobotsGate`가
    그것을 "robots 판정 불능"으로 삼켜 **모든 URL이 사이트 소유자의 거부로
    보고된다** — 로컬 설정 오타를 남의 의사로 오귀속하는 것이다. 비ASCII는
    첫 페치 도중 `UnicodeEncodeError`로 죽는다.

    허용 기준은 RFC 9110 field-value의 가시 ASCII(+안쪽 SP/HTAB)다.
    """
    if not isinstance(value, str):
        raise ConfigError(
            f"fetch.user_agent must be a string, got {value!r} — "
            "fetch.user_agent는 문자열이어야 합니다"
        )
    if not value.strip():
        raise ConfigError(
            "fetch.user_agent must not be blank — 설정값 범위 오류 "
            "(자기를 밝히지 않는 요청은 보내지 않습니다)"
        )
    if not _FIELD_VALUE_RE.match(value):
        raise ConfigError(
            f"fetch.user_agent must be printable ASCII without leading/trailing "
            f"whitespace or control characters (RFC 9110 field-value), got {value!r} — "
            "fetch.user_agent는 제어문자·개행·비ASCII 없이, 앞뒤 공백 없는 "
            "출력 가능 ASCII여야 합니다"
        )
