# SPDX-License-Identifier: Apache-2.0
"""설정 로딩. `~/.anchor/config.toml`을 읽고 환경변수 `ANCHOR_*`가 항상 우선한다.

v0.1은 SPEC §9 중 storage·fetch의 부분집합만 지원한다. 비밀값은 파일에 두지 않는다.
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("~/.anchor/config.toml")
DEFAULT_DB_PATH = Path("~/.anchor/store.db")


@dataclass(frozen=True)
class Config:
    db_path: Path = DEFAULT_DB_PATH
    user_agent: str = "Anchor/0.2 (+https://github.com/julgi80ai-stack/anchor-mcp)"
    respect_robots: bool = True
    timeout_seconds: float = 30.0
    max_redirects: int = 5
    max_content_bytes: int = 8 * 1024 * 1024
    default_max_age: int = 86400
    rate_limit_rps: float = 1.0
    rate_limit_burst: int = 3
    retry_backoff_base: float = 1.0
    robots_ttl_seconds: int = 86400
    # [anchor] (SPEC §9)
    context_chars: int = 48
    max_edit_ratio: float = 0.15
    max_edit_distance: int = 64
    min_quote_chars: int = 12
    short_quote_chars: int = 32
    time_budget_ms: int = 200
    hint_radius: int = 500
    max_match_chars: int = 2_097_152


def load_config(path: Path | None = None) -> Config:
    config = Config()

    config_path = (path or DEFAULT_CONFIG_PATH).expanduser()
    if config_path.is_file():
        with config_path.open("rb") as fp:
            data = tomllib.load(fp)
        storage = data.get("storage", {})
        fetch = data.get("fetch", {})
        rate = fetch.get("rate_limit", {})
        overrides: dict[str, object] = {}
        if "db_path" in storage:
            overrides["db_path"] = Path(storage["db_path"])
        if "user_agent" in fetch:
            overrides["user_agent"] = fetch["user_agent"]
        if "respect_robots" in fetch:
            overrides["respect_robots"] = bool(fetch["respect_robots"])
        if "timeout_seconds" in fetch:
            overrides["timeout_seconds"] = float(fetch["timeout_seconds"])
        if "max_redirects" in fetch:
            overrides["max_redirects"] = int(fetch["max_redirects"])
        if "max_content_mb" in fetch:
            overrides["max_content_bytes"] = int(fetch["max_content_mb"]) * 1024 * 1024
        if "default_max_age" in fetch:
            overrides["default_max_age"] = int(fetch["default_max_age"])
        if "requests_per_second" in rate:
            overrides["rate_limit_rps"] = float(rate["requests_per_second"])
        if "burst" in rate:
            overrides["rate_limit_burst"] = int(rate["burst"])
        anchor_section = data.get("anchor", {})
        if "context_chars" in anchor_section:
            overrides["context_chars"] = int(anchor_section["context_chars"])
        if "max_edit_ratio" in anchor_section:
            overrides["max_edit_ratio"] = float(anchor_section["max_edit_ratio"])
        if "min_quote_chars" in anchor_section:
            overrides["min_quote_chars"] = int(anchor_section["min_quote_chars"])
        if "short_quote_chars" in anchor_section:
            overrides["short_quote_chars"] = int(anchor_section["short_quote_chars"])
        if "time_budget_ms" in anchor_section:
            overrides["time_budget_ms"] = int(anchor_section["time_budget_ms"])
        if "max_document_bytes" in anchor_section:
            overrides["max_match_chars"] = int(anchor_section["max_document_bytes"])
        config = replace(config, **overrides)

    if env_db := os.environ.get("ANCHOR_DB_PATH"):
        config = replace(config, db_path=Path(env_db))
    if env_ua := os.environ.get("ANCHOR_USER_AGENT"):
        config = replace(config, user_agent=env_ua)

    if not config.respect_robots:
        print(
            "경고: respect_robots=false — robots.txt를 무시하도록 설정되어 있습니다 (SPEC §5.4).",
            file=sys.stderr,
        )
    return config
