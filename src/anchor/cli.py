# SPDX-License-Identifier: Apache-2.0
"""CLI (SPEC §8). fetch·cite·verify·timemap·export·stats·gc·list·serve."""

from __future__ import annotations

import dataclasses
import json
import math
from pathlib import Path
from typing import Optional

import typer

from anchor import __version__
from anchor.errors import AnchorError
from anchor.normalize.coverage import COVERAGE_WARN_RATIO, format_ratio
from anchor.service import Anchor

# 모든 명령이 같은 오류 표면을 진다 (D-138·D-147). 명령마다 다른 예외를 잡으면
# `gc --keep 0`은 트레이스백, `timemap`은 문장이 되는 식으로 갈린다 — 트레이스백은
# "도구가 깨졌다"는 뜻인데 사용자의 오타는 그런 뜻이 아니다. `StorageError`는
# 저장소 계층에서 도메인화되므로(D-138) `AnchorError`로 함께 잡힌다.
_USER_ERRORS = (AnchorError, ValueError, OSError)

app = typer.Typer(
    name="anchor",
    help="AI가 사용한 웹 근거의 시간적·출처적 무결성 계층 (Memento RFC 7089 로컬 클라이언트)",
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
    except _USER_ERRORS as error:
        typer.secho(f"실패: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    if json_out:
        payload = dataclasses.asdict(result)
        if not show_content:
            payload.pop("content")
        # `ratio`는 파생값이라 asdict에 담기지 않는다 (D-239).
        payload["coverage"] = result.coverage.as_payload()
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    typer.secho(f"{result.outcome}", fg=typer.colors.GREEN, bold=True, nl=False)
    typer.echo(f"  {result.url}")
    if result.title:
        typer.echo(f"  제목      {result.title}")
    typer.echo(f"  버전      {result.version_id}  ({result.captured_at}, {result.source})")
    typer.echo(f"  text_hash {result.text_hash}")
    if result.redirect is not None:
        # 사실이지 경고가 아니다 (D-247) — 판정은 하지 않는다.
        kind = "영구" if result.redirect.permanent else "일시"
        typer.echo(f"  리다이렉트 {kind} → {result.redirect.to}")
    if result.raw_changed:
        # 판정은 unchanged다. 달라진 것은 우리가 보는 영역 **밖**이다 (D-232).
        typer.secho(
            "  원본 바이트가 달라졌습니다 — 추출된 본문은 같습니다"
            " (본문으로 뽑히지 않는 영역의 변경일 수 있습니다)",
            fg=typer.colors.YELLOW,
        )
    # 우리가 이 문서의 얼마를 보고 위 판정을 했는가 (D-239). 경고가 아니라
    # 사실이므로 색을 쓰지 않는다 — 사용자가 매번 보아야 할 값이다.
    _echo_coverage(result.coverage)
    for note in result.notes:
        typer.secho(f"  {note}", fg=typer.colors.YELLOW)
    typer.echo(
        f"  네트워크  {result.network.bytes_down:,} bytes down, {result.network.elapsed_ms} ms"
    )
    if show_content and result.content is not None:
        typer.echo("---")
        typer.echo(result.content)


def _echo_coverage(coverage) -> None:
    """포착 범위 한 줄. 재지 못했으면 그렇게 적는다 (D-239·D-243)."""
    ratio = coverage.ratio
    if ratio is None:
        reason = {
            "no-prose": "셀 만한 산문 단위가 없습니다",
            "not-measurable": "이 형식은 가시 텍스트를 잴 수단이 없습니다",
        }.get(coverage.basis, "재지 않았습니다")
        typer.echo(f"  포착 범위  측정 불가 — {reason}")
        return
    if coverage.basis == "whole-document":
        typer.echo("  포착 범위  문서 전체 (고른 것이 없습니다)")
        return
    dropped = " ".join(f"{tag}×{count}" for tag, count in coverage.dropped[:4])
    line = (
        f"  포착 범위  산문 {coverage.captured_chars:,}/{coverage.prose_chars:,}자 "
        f"({format_ratio(ratio)})"
    )
    if dropped:
        line += f"  미포착 {dropped}"
    typer.echo(line)


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
    except _USER_ERRORS as error:
        typer.secho(f"실패: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    if json_out:
        typer.echo(json.dumps(dataclasses.asdict(result), ensure_ascii=False, indent=2))
        return
    typer.secho("앵커 생성", fg=typer.colors.GREEN, bold=True, nl=False)
    typer.echo(f"  {result.anchor_id}")
    typer.echo(f"  버전   {result.version_id}  오프셋 {result.offset}  품질 {result.quality}")
    # 어느 시점 판본에 닻을 내렸는가 (D-230). cite는 네트워크에 나가지
    # 않으므로, 이 줄이 없으면 사용자는 오래된 스냅샷에 인용을 걸면서 모른다.
    typer.echo(f"  캡처   {result.captured_at}  (원본 대조 {result.last_checked_at})")
    _echo_coverage(result.coverage)
    if result.source == "archive":
        # `verify`와 같은 대칭이다 — 아카이브 스냅샷에 앵커를 달았다는 사실은
        # `--json`·MCP에만 있으면 안 된다. 사람이 읽는 출력에서만 빠지면
        # "**항상** 알 수 있다"(D-093, SPEC §5.2)가 CLI에서 깨진다 (D-218).
        typer.secho(
            "  출처: archive — 원본이 아니라 아카이브 스냅샷에 앵커를 달았습니다",
            fg=typer.colors.YELLOW,
        )
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
    if not math.isfinite(seconds):
        # `nan`·`inf`·`1e400`은 float()를 통과하고 `timedelta`에서 죽는다 (D-149).
        raise AnchorError(
            f"Duration must be a finite number, got {value!r} — "
            f"기간은 유한한 값이어야 합니다: {value!r}"
        )
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
                # `--older-than ""`(셸 변수가 빈 경우)를 "필터 없음"으로 읽으면
                # 전체 앵커가 재검증된다 — 지정하지 않은 것과 빈 값은 다르다 (D-154).
                older_than=(
                    _parse_older_than(older_than) if older_than is not None else None
                ),
                time_budget_ms=budget_ms,
            )
    except _USER_ERRORS as error:
        typer.secho(f"실패: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    if json_out:
        typer.echo(json.dumps(dataclasses.asdict(report), ensure_ascii=False, indent=2))
        return

    summary_line = " · ".join(f"{state} {count}" for state, count in report.summary.items())
    typer.echo(f"검증 {report.checked}건: {summary_line}")
    typer.echo(f"네트워크: 요청 {report.requests}건, {report.bytes_down:,} bytes down")
    if report.sources.get("archive"):
        # 전부 INTACT여도 이 사실은 남아야 한다 — 원본이 아니라 아카이브
        # 스냅샷과 대조한 것이다 (D-093, SPEC §5.2).
        typer.secho(
            f"출처: archive {report.sources['archive']}건 — 원본이 아니라 "
            "아카이브 스냅샷과 대조했습니다",
            fg=typer.colors.YELLOW,
        )
    if report.pipeline_changed:
        # 원문이 한 글자도 안 바뀌었는데 경보가 쏟아지는 유일한 이유다 (D-235).
        typer.secho(
            f"추출 파이프라인 변경 {report.pipeline_changed}건 — 원문 변경이 "
            "아닐 수 있습니다",
            fg=typer.colors.YELLOW,
        )
    if report.low_coverage:
        # 포착 범위가 좁은 문서에서 INTACT는 "본 범위 안에서 이상 없음"이라는
        # 뜻으로 좁아진다 (D-241). 전부 INTACT인 보고서에서도 남아야 한다.
        typer.secho(
            f"포착 범위 좁음 {report.low_coverage}건 — 저장 본문이 그 문서 산문의 "
            "3분의 1 미만입니다. 그 밖에서 일어난 개정은 판정에 나타나지 않습니다",
            fg=typer.colors.YELLOW,
        )
    if report.ambiguous:
        # 모호한 채 INTACT가 된 앵커는 attention에 없다 (D-231).
        typer.secho(
            f"중복 출현 {report.ambiguous}건 — 인용문이 원문에 여러 번 나와 "
            "앵커가 어느 인스턴스를 가리키는지 모호합니다",
            fg=typer.colors.YELLOW,
        )
    if not report.attention:
        return
    typer.secho("\n주의 필요:", bold=True)
    for item in report.attention:
        typer.secho(f"[{item.state}] ", fg=typer.colors.YELLOW, bold=True, nl=False)
        typer.echo(item.url)
        if item.source == "archive":
            # 집계 줄만으로는 혼합 배치에서 **어느 항목이** 아카이브 대조인지
            # 알 수 없다 (D-234, D-218의 나머지 절반).
            typer.secho(
                "  출처: archive — 원본이 아니라 아카이브 스냅샷과 대조했습니다",
                fg=typer.colors.YELLOW,
            )
        if item.pipeline_changed:
            typer.secho(
                "  추출 파이프라인이 달라졌습니다 — 원문 변경이 아닐 수 있습니다",
                fg=typer.colors.YELLOW,
            )
        if item.coverage_ratio is not None and item.coverage_ratio < COVERAGE_WARN_RATIO:
            typer.secho(
                f"  대조 판본이 그 문서 산문의 {format_ratio(item.coverage_ratio)}만 "
                "담고 있습니다 — 나머지는 판정에 나타나지 않습니다",
                fg=typer.colors.YELLOW,
            )
        if item.occurrences is not None and item.occurrences > 1:
            typer.secho(
                f"  인용문이 원문에 {item.occurrences}회 이상 나왔습니다 — "
                "앵커가 어느 인스턴스를 가리키는지 모호합니다",
                fg=typer.colors.YELLOW,
            )
        typer.echo(f"  이전: {item.before}")
        if item.after is not None:
            typer.echo(f"  현재: {item.after}")
        if item.match_score is not None:
            typer.echo(f"  (score {item.match_score:.2f}, 편집거리 {item.edit_distance})")
        if item.found_offset is not None and item.position_hint is not None:
            moved = item.found_offset - item.position_hint
            if abs(moved) > 500:
                typer.echo(f"  (원래 자리에서 {moved:+,}자 — 다른 절일 수 있습니다)")


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
    except _USER_ERRORS as error:
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
    except _USER_ERRORS as error:
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
    except _USER_ERRORS as error:
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
        # created·renormalized를 changed와 나눠 적는다 (D-227). 한 이름으로
        # 묶으면 첫 페치가 "바뀌었다"로 읽힌다.
        f"최근 30일: 요청 {window['requests']}"
        f" (cache_hit {window['cache_hits']}, not_modified {window['not_modified']},"
        f" unchanged {window['unchanged']}, created {window['created']},"
        f" changed {window['changed']}, renormalized {window['renormalized']},"
        f" archive {window['archive']}, error {window['errors']})"
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
    """저장소를 정리한다. **인용된 버전은 절대 삭제하지 않는다.**"""
    try:
        with Anchor(db_path=db) as anchor:
            result = anchor.collect_garbage(keep=keep)
    except _USER_ERRORS as error:
        typer.secho(f"실패: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    typer.echo(
        f"삭제 {result['deleted_versions']}개 버전, 회수 추정 {result['freed_bytes_estimate']:,} bytes"
        f" (문서당 최근 {result['keep']}개 + 인용된 버전 + 서빙 중인 버전 보존)"
    )
    typer.echo(
        f"정리: 검증 이력 {result['pruned_verifications']}건"
        f" (앵커당 최신 1건은 남긴다), 회계 {result['pruned_fetch_log']}건,"
        f" robots 캐시 {result['pruned_robots_cache']}건"
    )


@app.command()
def serve(
    transport: Optional[str] = typer.Option(
        None, "--transport", help="stdio | http (기본: 설정 파일, 없으면 stdio)"
    ),
    db: Optional[Path] = typer.Option(None, "--db", help="SQLite 경로"),
) -> None:  # pragma: no cover — 이벤트 루프를 점유하는 장기 실행 진입점
    """MCP 서버를 시작한다 (도구 9종). MCP 클라이언트 등록은 `anchor-mcp` 참조."""
    from anchor.config import SERVER_TRANSPORTS, load_config
    from anchor.server import build_server, serve_forever

    if transport is not None and transport not in SERVER_TRANSPORTS:
        # 설정 파일은 `_validate`가, `anchor-mcp`는 argparse choices가 거부하는데
        # 여기만 뚫려 있었다 — 오타가 오류 없이 stdio로 실행됐다 (D-150).
        typer.secho(
            f"실패: --transport must be one of {SERVER_TRANSPORTS}, got {transport!r} — "
            f"전송 방식은 {' | '.join(SERVER_TRANSPORTS)} 중 하나여야 합니다",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    try:
        config = load_config()
        resolved = transport or config.server_transport
        server, service = build_server(db_path=db, config=config)
    except _USER_ERRORS as error:
        typer.secho(f"실패: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    serve_forever(server, service, resolved)


@app.command("list")
def list_command(
    db: Optional[Path] = typer.Option(None, "--db", help="SQLite 경로"),
) -> None:
    """캐시된 문서 목록을 상태·최종 확인 시각과 함께 출력한다."""
    try:
        with Anchor(db_path=db) as anchor:
            documents = anchor.list_documents()
    except _USER_ERRORS as error:
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
