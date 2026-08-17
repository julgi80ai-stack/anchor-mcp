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
    """`7d`·`12H`·`P7D`·`3600`을 초로. 잘못된 값은 도메인 예외로 (D-028)."""
    from anchor.models import parse_iso_duration

    raw = value.strip()
    if raw.upper().startswith("P"):
        try:
            return parse_iso_duration(raw)
        except ValueError as error:
            raise AnchorError(str(error)) from error

    units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    suffix = raw[-1:].lower()
    number = raw[:-1] if suffix in units else raw
    try:
        seconds = float(number) * (units[suffix] if suffix in units else 1)
    except ValueError as error:
        raise AnchorError(
            f"Cannot parse duration {value!r}; use 7d, 12h, 30m, 3600, or P7D — "
            f"기간을 해석할 수 없습니다: {value!r}"
        ) from error
    if seconds < 0:
        raise AnchorError(
            f"Duration must not be negative, got {value!r} — 기간은 음수일 수 없습니다"
        )
    return seconds


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
                older_than=_parse_older_than(older_than) if older_than else None,
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


@app.command()
def timemap(
    document: str = typer.Argument(..., help="문서 id 또는 URL"),
    format: str = typer.Option("link", "--format", help="link | json"),
    db: Optional[Path] = typer.Option(None, "--db", help="SQLite 경로"),
) -> None:
    """문서의 버전 목록을 RFC 7089 TimeMap으로 내보낸다."""
    try:
        with Anchor(db_path=db) as anchor:
            result = anchor.get_timemap(document, fmt=format)
    except (AnchorError, ValueError) as error:
        typer.secho(f"실패: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    if format == "json":
        typer.echo(json.dumps(result["body"], ensure_ascii=False, indent=2))
    else:
        typer.echo(result["body"])


@app.command()
def export(
    robust_links: bool = typer.Option(
        False, "--robust-links", help="앵커들을 Robust Links 표기로 내보낸다"
    ),
    format: str = typer.Option("html", "--format", help="html | markdown | bibtex_note"),
    anchor_ids: Optional[list[str]] = typer.Option(
        None, "--anchor", help="특정 앵커 id (반복 가능, 생략 시 전체)"
    ),
    db: Optional[Path] = typer.Option(None, "--db", help="SQLite 경로"),
) -> None:
    """앵커들을 상호운용 포맷으로 내보낸다. 현재 지원: --robust-links."""
    if not robust_links:
        typer.secho("내보낼 포맷을 지정하세요: --robust-links", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    try:
        with Anchor(db_path=db) as anchor:
            items = anchor.export_robust_links(anchor_ids or None, fmt=format)
    except (AnchorError, ValueError) as error:
        typer.secho(f"실패: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    if not items:
        typer.echo("내보낼 앵커가 없습니다.")
        return
    for item in items:
        typer.echo(item[format])


@app.command()
def stats(
    json_out: bool = typer.Option(False, "--json", help="결과를 JSON으로 출력"),
    db: Optional[Path] = typer.Option(None, "--db", help="SQLite 경로"),
) -> None:
    """캐시 회계: 문서·버전·앵커 수, 디스크 사용량, 최근 30일 절감 효과."""
    try:
        with Anchor(db_path=db) as anchor:
            payload = anchor.cache_stats()
    except AnchorError as error:
        typer.secho(f"실패: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    except OSError as error:
        typer.secho(f"실패: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    if json_out:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    window = payload["last_30d"]
    typer.echo(
        f"문서 {payload['documents']} · 버전 {payload['versions']} · 앵커 {payload['anchors']}"
        f" · 디스크 {payload['disk_bytes']:,} bytes"
    )
    typer.echo(
        f"최근 30일: 요청 {window['requests']}"
        f" (cache_hit {window['cache_hits']}, not_modified {window['not_modified']},"
        f" unchanged {window['unchanged']}, changed {window['changed']}, error {window['errors']})"
    )
    typer.echo(
        f"다운로드 {window['bytes_down']:,} bytes · 절감 추정 {window['bytes_saved_estimate']:,} bytes"
        f" · hit_rate {window['hit_rate']:.2%}"
    )


@app.command()
def gc(
    keep: Optional[int] = typer.Option(None, "--keep", help="문서당 보존할 최근 버전 수 (기본 20)"),
    db: Optional[Path] = typer.Option(None, "--db", help="SQLite 경로"),
) -> None:
    """고아 버전을 정리한다. 앵커가 가리키는 버전은 절대 삭제하지 않는다."""
    try:
        with Anchor(db_path=db) as anchor:
            result = anchor.collect_garbage(keep=keep)
    except AnchorError as error:
        typer.secho(f"실패: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    except OSError as error:
        typer.secho(f"실패: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    typer.echo(
        f"삭제 {result['deleted_versions']}개 버전, 회수 추정 {result['freed_bytes_estimate']:,} bytes"
        f" (문서당 최근 {result['keep']}개 + 앵커·검증 참조 버전 보존)"
    )


@app.command()
def serve(
    transport: Optional[str] = typer.Option(
        None, "--transport", help="stdio | http (기본: 설정 파일, 없으면 stdio)"
    ),
    db: Optional[Path] = typer.Option(None, "--db", help="SQLite 경로"),
) -> None:  # pragma: no cover — 이벤트 루프를 점유하는 장기 실행 진입점
    """MCP 서버를 시작한다 (도구 9종). MCP 클라이언트 등록은 `anchor-mcp` 참조."""
    from anchor.config import load_config
    from anchor.server import build_server

    config = load_config()
    resolved = transport or config.server_transport
    server, service = build_server(db_path=db, config=config)
    try:
        server.run(transport="streamable-http" if resolved == "http" else "stdio")
    finally:
        service.close()


@app.command("list")
def list_command(
    db: Optional[Path] = typer.Option(None, "--db", help="SQLite 경로"),
) -> None:
    """캐시된 문서 목록을 상태·최종 확인 시각과 함께 출력한다."""
    try:
        with Anchor(db_path=db) as anchor:
            documents = anchor.list_documents()
    except AnchorError as error:
        typer.secho(f"실패: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    except OSError as error:
        typer.secho(f"실패: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
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
