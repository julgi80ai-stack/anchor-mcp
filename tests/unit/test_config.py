# SPDX-License-Identifier: Apache-2.0
"""설정 로딩 (SPEC §9): config.toml 전 섹션 + 환경변수 우선."""

from __future__ import annotations

from pathlib import Path

from anchor.config import Config, load_config

FULL_TOML = """
[storage]
db_path = "/tmp/anchor-test/store.db"
keep_versions = 7

[fetch]
user_agent = "TestAgent/1.0"
respect_robots = true
timeout_seconds = 11
max_redirects = 3
max_content_mb = 2
default_max_age = 1234

[fetch.rate_limit]
requests_per_second = 2.5
burst = 9

[fetch.archive_fallback]
enabled = true
aggregator = "http://memgator.local:1208"
archive_list = "/etc/anchor/arcs.json"
timeout_seconds = 8

[anchor]
context_chars = 32
max_edit_ratio = 0.2
min_quote_chars = 10
short_quote_chars = 20
time_budget_ms = 100
max_document_bytes = 1000000

[server]
transport = "http"
"""


def test_defaults_without_config_file(tmp_path):
    config = load_config(tmp_path / "absent.toml")
    assert config == Config()
    assert config.archive_fallback_enabled is False  # 조용한 외부 의존 금지


def test_full_toml_round_trip(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(FULL_TOML, "utf-8")
    config = load_config(path)

    assert config.db_path == Path("/tmp/anchor-test/store.db")
    assert config.keep_versions == 7
    assert config.user_agent == "TestAgent/1.0"
    assert config.timeout_seconds == 11.0
    assert config.max_redirects == 3
    assert config.max_content_bytes == 2 * 1024 * 1024
    assert config.default_max_age == 1234
    assert config.rate_limit_rps == 2.5
    assert config.rate_limit_burst == 9
    assert config.archive_fallback_enabled is True
    assert config.archive_aggregator == "http://memgator.local:1208"
    assert config.archive_list == "/etc/anchor/arcs.json"
    assert config.archive_timeout_seconds == 8.0
    assert config.context_chars == 32
    assert config.max_edit_ratio == 0.2
    assert config.min_quote_chars == 10
    assert config.short_quote_chars == 20
    assert config.time_budget_ms == 100
    assert config.max_match_chars == 1_000_000
    assert config.server_transport == "http"


def test_env_always_wins(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(FULL_TOML, "utf-8")
    monkeypatch.setenv("ANCHOR_DB_PATH", "/tmp/anchor-env/override.db")
    monkeypatch.setenv("ANCHOR_USER_AGENT", "EnvAgent/2.0")

    config = load_config(path)
    assert config.db_path == Path("/tmp/anchor-env/override.db")
    assert config.user_agent == "EnvAgent/2.0"


def test_disabling_robots_prints_warning(tmp_path, capsys):
    path = tmp_path / "config.toml"
    path.write_text("[fetch]\nrespect_robots = false\n", "utf-8")

    config = load_config(path)
    assert config.respect_robots is False
    assert "respect_robots=false" in capsys.readouterr().err
