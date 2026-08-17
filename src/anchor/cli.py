# SPDX-License-Identifier: Apache-2.0
"""CLI (SPEC §8). v0.1은 fetch와 list만 제공한다."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Optional

import typer

from anchor import __version__
from anchor.errors import AnchorError
from anchor.service import Anchor

app = typer.Typer(
    name="anchor",
    help="AI가 사용한 웹 근거의 시간적·출처적 무결성 계층 (v0.1: fetch + 변경 감지)",
    add_completion=False,
)


@app.callback(invoke_without_command=True)
def _main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", help="버전을 출력하고 종료"),
) -> None:
    if version:
        typer.echo(f"anchor {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


@app.command()
def fetch(
    url: str = typer.Argument(..., help="가져올 URL"),
    max_age: Optional[int] = typer.Option(
        None, help="캐시 허용 나이(초). 기본 86400, 0이면 항상 원본 확인"
    ),
    force_refresh: bool = typer.Option(False, "--force-refresh", help="캐시를 무시하고 다시 확인"),
    show_content: bool = typer.Option(False, "--content", help="정규화된 본문을 출력"),
    json_out: bool = typer.Option(False, "--json", help="결과를 JSON으로 출력"),
    db: Optional[Path] = typer.Option(None, "--db", help="SQLite 경로 (기본 ~/.anchor/store.db)"),
) -> None:
    """문서를 가져오거나 캐시에서 반환한다."""
    try:
        with Anchor(db_path=db) as anchor:
            result = anchor.fetch(
                url,
                max_age=max_age,
                force_refresh=force_refresh,
                include_content=show_content or json_out,
            )
    except AnchorError as error:
        typer.secho(f"실패: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    if json_out:
        payload = dataclasses.asdict(result)
        if not show_content:
            payload.pop("content")
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    typer.secho(f"{result.outcome}", fg=typer.colors.GREEN, bold=True, nl=False)
    typer.echo(f"  {result.url}")
    if result.title:
        typer.echo(f"  제목      {result.title}")
    typer.echo(f"  버전      {result.version_id}  ({result.captured_at}, {result.source})")
    typer.echo(f"  text_hash {result.text_hash}")
    typer.echo(
        f"  네트워크  {result.network.bytes_down:,} bytes down, {result.network.elapsed_ms} ms"
    )
    if show_content and result.content is not None:
        typer.echo("---")
        typer.echo(result.content)


@app.command("list")
def list_command(
    db: Optional[Path] = typer.Option(None, "--db", help="SQLite 경로"),
) -> None:
    """캐시된 문서 목록을 상태·최종 확인 시각과 함께 출력한다."""
    with Anchor(db_path=db) as anchor:
        documents = anchor.list_documents()
    if not documents:
        typer.echo("캐시된 문서가 없습니다.")
        return
    for document in documents:
        typer.echo(
            f"{document.status:10} {document.last_checked_at}  {document.url}"
            + (f"  — {document.title}" if document.title else "")
        )


if __name__ == "__main__":
    app()
