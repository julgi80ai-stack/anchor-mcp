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


@app.command()
def cite(
    document: str = typer.Argument(..., help="문서 id 또는 URL (먼저 fetch 되어 있어야 함)"),
    quote: str = typer.Argument(..., help="인용문 (완결된 문장 하나 권장, 최소 12자)"),
    note: Optional[str] = typer.Option(None, "--note", help="사용자 메모"),
    json_out: bool = typer.Option(False, "--json", help="결과를 JSON으로 출력"),
    db: Optional[Path] = typer.Option(None, "--db", help="SQLite 경로"),
) -> None:
    """인용문에 앵커를 부여한다. 원문에 없는 인용은 기록하지 않는다."""
    try:
        with Anchor(db_path=db) as anchor:
            result = anchor.cite(document, quote, note=note)
    except AnchorError as error:
        typer.secho(f"실패: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    if json_out:
        typer.echo(json.dumps(dataclasses.asdict(result), ensure_ascii=False, indent=2))
        return
    typer.secho("앵커 생성", fg=typer.colors.GREEN, bold=True, nl=False)
    typer.echo(f"  {result.anchor_id}")
    typer.echo(f"  버전   {result.version_id}  오프셋 {result.offset}  품질 {result.quality}")
    for warning in result.warnings:
        typer.secho(f"  경고: {warning}", fg=typer.colors.YELLOW)


def _parse_older_than(value: str) -> float:
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if value and value[-1] in units:
        return float(value[:-1]) * units[value[-1]]
    return float(value)


@app.command()
def verify(
    anchor_ids: Optional[list[str]] = typer.Option(None, "--anchor", help="특정 앵커 id (반복 가능)"),
    older_than: Optional[str] = typer.Option(
        None, "--older-than", help="이 기간 내 검증된 앵커는 건너뜀 (예: 7d, 12h, 3600s)"
    ),
    budget_ms: Optional[int] = typer.Option(None, "--budget-ms", help="앵커당 매칭 시간 예산(ms)"),
    json_out: bool = typer.Option(False, "--json", help="결과를 JSON으로 출력"),
    db: Optional[Path] = typer.Option(None, "--db", help="SQLite 경로"),
) -> None:
    """앵커들을 현재 원문 대비 재검증한다. 조건이 없으면 전체."""
    try:
        with Anchor(db_path=db) as anchor:
            report = anchor.verify(
                anchor_ids=anchor_ids or None,
                older_than_seconds=_parse_older_than(older_than) if older_than else None,
                time_budget_ms=budget_ms,
            )
    except AnchorError as error:
        typer.secho(f"실패: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    if json_out:
        typer.echo(json.dumps(dataclasses.asdict(report), ensure_ascii=False, indent=2))
        return

    summary_line = " · ".join(f"{state} {count}" for state, count in report.summary.items())
    typer.echo(f"검증 {report.checked}건: {summary_line}")
    typer.echo(f"네트워크: 요청 {report.requests}건, {report.bytes_down:,} bytes down")
    if not report.attention:
        return
    typer.secho("\n주의 필요:", bold=True)
    for item in report.attention:
        typer.secho(f"[{item.state}] ", fg=typer.colors.YELLOW, bold=True, nl=False)
        typer.echo(item.url)
        typer.echo(f"  이전: {item.before}")
        if item.after is not None:
            typer.echo(f"  현재: {item.after}")
        if item.match_score is not None:
            typer.echo(f"  (score {item.match_score:.2f}, 편집거리 {item.edit_distance})")


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
