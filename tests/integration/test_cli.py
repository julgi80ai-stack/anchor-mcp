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


def test_cli_export_requires_format_flag(tmp_path):
    result = runner.invoke(app, ["export", "--db", str(tmp_path / "empty.db")])
    assert result.exit_code == 1
