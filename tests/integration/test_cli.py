# SPDX-License-Identifier: Apache-2.0
"""CLI 명령 스모크 (SPEC §8): fetch → cite → timemap / export / stats / gc."""

from __future__ import annotations

from typer.testing import CliRunner

from anchor.cli import app

runner = CliRunner()

QUOTE = "링크는 살아 있지만 내용이 바뀌는 인용 표류가 가장 위험하다."


def test_cli_full_workflow(fixture_server, tmp_path):
    base_url, state = fixture_server
    db = str(tmp_path / "cli.db")
    url = f"{base_url}/article"

    fetched = runner.invoke(app, ["fetch", url, "--db", db])
    assert fetched.exit_code == 0, fetched.output
    assert "created" in fetched.output

    cited = runner.invoke(app, ["cite", url, QUOTE, "--db", db])
    assert cited.exit_code == 0, cited.output
    assert "앵커 생성" in cited.output

    tm = runner.invoke(app, ["timemap", url, "--db", db])
    assert tm.exit_code == 0, tm.output
    assert 'rel="original"' in tm.output
    assert 'rel="first last memento"' in tm.output

    exported = runner.invoke(
        app, ["export", "--robust-links", "--format", "markdown", "--db", db]
    )
    assert exported.exit_code == 0, exported.output
    assert "data-versiondate=" in exported.output

    stats = runner.invoke(app, ["stats", "--db", db])
    assert stats.exit_code == 0, stats.output
    assert "문서 1" in stats.output
    assert "hit_rate" in stats.output

    gc = runner.invoke(app, ["gc", "--db", db])
    assert gc.exit_code == 0, gc.output
    assert "삭제 0개 버전" in gc.output


def test_cli_cite_says_when_the_anchor_landed_on_an_archive_snapshot(
    fixture_server, tmp_path
):
    """`cite`의 사람이 읽는 출력도 출처를 말한다 (D-218, SPEC §5.2·§7.2).

    `verify`는 아카이브 대조를 노란 줄로 알리고 `fetch`는 출처를 한 줄로
    적는데, `cite`만 `--json`·MCP에만 담고 사람에게는 말하지 않았다. 원본이
    죽어 아카이브 스냅샷에 앵커를 달아도 CLI 사용자는 그 사실을 알 수 없다 —
    D-093이 세운 "**항상** 알 수 있다"가 여기서만 비대칭이었다.
    """
    from anchor.config import Config
    from anchor.service import Anchor

    from .conftest import article_html

    base_url, state = fixture_server
    db = tmp_path / "cli_archive.db"
    state.status_override = 404  # 원본 소멸
    state.archive_html = article_html(nonce="arch")
    config = Config(
        db_path=db,
        rate_limit_rps=1000.0,
        archive_fallback_enabled=True,
        archive_aggregator=base_url,
    )
    with Anchor(db_path=db, config=config) as anchor:
        assert anchor.fetch(f"{base_url}/article").source == "archive"

    cited = runner.invoke(app, ["cite", f"{base_url}/article", QUOTE, "--db", str(db)])
    assert cited.exit_code == 0, cited.output
    assert "archive" in cited.output, cited.output


def test_cli_cite_stays_quiet_when_the_anchor_is_on_the_original(
    fixture_server, tmp_path
):
    """원본에 단 앵커에는 그 줄이 없다 — `verify`와 같은 대칭이다 (D-218)."""
    base_url, _state = fixture_server
    db = str(tmp_path / "cli_live.db")
    url = f"{base_url}/article"
    assert runner.invoke(app, ["fetch", url, "--db", db]).exit_code == 0
    cited = runner.invoke(app, ["cite", url, QUOTE, "--db", db])
    assert cited.exit_code == 0, cited.output
    assert "archive" not in cited.output, cited.output


def test_cli_export_requires_format_flag(tmp_path):
    result = runner.invoke(app, ["export", "--db", str(tmp_path / "empty.db")])
    assert result.exit_code == 1


def test_cli_json_outputs_and_errors(fixture_server, tmp_path):
    base_url, state = fixture_server
    db = str(tmp_path / "cli2.db")
    url = f"{base_url}/article"

    fetched = runner.invoke(app, ["fetch", url, "--db", db, "--json"])
    assert fetched.exit_code == 0
    assert '"outcome": "created"' in fetched.output

    cached = runner.invoke(app, ["fetch", url, "--db", db, "--content"])
    assert cached.exit_code == 0
    assert "재페치" in cached.output  # --content는 본문을 함께 출력

    cited = runner.invoke(app, ["cite", url, QUOTE, "--json", "--db", db])
    assert cited.exit_code == 0
    assert '"quality": "ok"' in cited.output

    verified = runner.invoke(app, ["verify", "--older-than", "0s", "--json", "--db", db])
    assert verified.exit_code == 0
    assert '"INTACT": 1' in verified.output

    listed = runner.invoke(app, ["list", "--db", db])
    assert listed.exit_code == 0 and "live" in listed.output

    tm_json = runner.invoke(app, ["timemap", url, "--format", "json", "--db", db])
    assert tm_json.exit_code == 0 and '"mementos"' in tm_json.output

    stats_json = runner.invoke(app, ["stats", "--json", "--db", db])
    assert stats_json.exit_code == 0 and '"hit_rate"' in stats_json.output

    verify_human = runner.invoke(app, ["verify", "--db", db])
    assert verify_human.exit_code == 0 and "검증 1건" in verify_human.output


def test_cli_error_paths(fixture_server, tmp_path):
    base_url, state = fixture_server
    db = str(tmp_path / "cli3.db")

    missing_doc = runner.invoke(app, ["cite", "없는-문서-id", "인용문이 충분히 길다고 치자.", "--db", db])
    assert missing_doc.exit_code == 1

    bad_timemap = runner.invoke(app, ["timemap", "없는-문서-id", "--db", db])
    assert bad_timemap.exit_code == 1

    empty_list = runner.invoke(app, ["list", "--db", db])
    assert "캐시된 문서가 없습니다" in empty_list.output

    empty_export = runner.invoke(app, ["export", "--robust-links", "--db", db])
    assert "내보낼 앵커가 없습니다" in empty_export.output

    version = runner.invoke(app, ["--version"])
    assert version.exit_code == 0 and "anchor" in version.output

    state.status_override = 404
    dead = runner.invoke(app, ["fetch", f"{base_url}/article", "--db", db])
    assert dead.exit_code == 1 and "404" in dead.output
    state.status_override = None
