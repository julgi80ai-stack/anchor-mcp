# SPDX-License-Identifier: Apache-2.0
"""CLI 인자 표면 (SPEC §8) — 6-나 라운드: D-138 D-147 D-149 D-150 D-154.

축을 먼저 연다:

④ **CLI 인자** — 빈 문자열·오타·손상 DB·디렉터리 경로·쓰기 불가 경로.
⑤ **오류 표면** — 사용자에게 문장으로 나가는가(exit 1 + 메시지), 아니면
   생 트레이스백인가. 트레이스백은 "도구가 깨졌다"는 뜻이고, 사용자의 오타는
   그런 뜻이 아니다.

CLI만이 아니라 **라이브러리 직접 경로**도 같은 보장을 받아야 한다 —
저장소 계층에서 도메인화한다 (D-129·D-138의 공통 원리).
"""

from __future__ import annotations

import os
import random

import pytest
from typer.testing import CliRunner

from anchor.cli import _parse_older_than, app
from anchor.errors import AnchorError, StorageError
from anchor.store.repository import Repository

runner = CliRunner()

# `--db`가 받을 수 있는 잘못된 값들. 정상값을 섞어 두어 판별력을 확인한다.
BROKEN_DB_KINDS = ("corrupt", "directory", "unwritable-parent", "empty", "parent-is-a-file")


@pytest.fixture
def broken_db(request, tmp_path):
    kind = request.param
    if kind == "corrupt":
        path = tmp_path / "corrupt.db"
        path.write_bytes(b"SQLite format 3 -- not really" * 40)
        return str(path)
    if kind == "directory":
        path = tmp_path / "a-directory"
        path.mkdir()
        return str(path)
    if kind == "unwritable-parent":
        parent = tmp_path / "readonly"
        parent.mkdir()
        parent.chmod(0o500)
        request.addfinalizer(lambda: parent.chmod(0o700))
        return str(parent / "store.db")
    if kind == "empty":
        return ""
    if kind == "parent-is-a-file":
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        return str(blocker / "store.db")
    raise AssertionError(kind)


def _no_traceback(result) -> bool:
    """typer.Exit은 SystemExit으로 나온다 — 그 밖의 예외는 전부 트레이스백이다."""
    return result.exception is None or isinstance(result.exception, SystemExit)


# 모든 명령이 같은 계약을 진다 — 하나만 고치면 다음 사람이 다른 명령에서 만난다.
COMMANDS = [
    ["list"],
    ["stats"],
    ["stats", "--json"],
    ["gc"],
    ["export", "--robust-links"],
    ["timemap", "https://example.invalid/a"],
    ["verify"],
    ["cite", "https://example.invalid/a", "인용문이 충분히 길다고 치자 정말로."],
]


@pytest.mark.parametrize("broken_db", BROKEN_DB_KINDS, indirect=True)
@pytest.mark.parametrize("argv", COMMANDS, ids=lambda a: a[0] + ("-" + a[1].strip("-") if len(a) > 1 and a[1].startswith("--") else ""))
def test_broken_db_path_is_reported_not_raised(argv, broken_db, tmp_path):
    """D-138 — 손상·디렉터리·쓰기 불가·빈 경로에서 트레이스백이 나오면 안 된다."""
    if os.geteuid() == 0 and "readonly" in broken_db:
        pytest.skip("root는 읽기 전용 디렉터리에도 쓸 수 있다")
    result = runner.invoke(app, argv + ["--db", broken_db])
    assert _no_traceback(result), result.exception
    assert result.exit_code == 1
    assert "실패" in result.output


@pytest.mark.parametrize("broken_db", BROKEN_DB_KINDS, indirect=True)
def test_repository_wraps_storage_failures_for_library_callers(broken_db):
    """라이브러리 직접 경로도 같은 보장을 받는다 — CLI에서만 잡으면 반쪽이다."""
    if os.geteuid() == 0 and "readonly" in broken_db:
        pytest.skip("root는 읽기 전용 디렉터리에도 쓸 수 있다")
    with pytest.raises(StorageError) as caught:
        Repository(broken_db)
    assert isinstance(caught.value, AnchorError)


def test_an_empty_db_path_is_reported_as_empty_not_as_a_directory():
    """빈 `--db`는 "빈 경로"로 보고된다 (D-220).

    "실패"만 재는 시험은 원인이 서로 뒤바뀌어도 초록이다. typer는 `--db ''`를
    `Path('')`로 넘기고 `str(Path(''))`는 `"."`가 되므로, 저장소의 빈 경로
    가드가 CLI에서는 영영 도달하지 않고 원인이 "디렉터리"로 오귀속됐다 —
    환경변수가 비어 있는 흔한 호출(`--db "$ANCHOR_DB"`)이 정확히 이 모습이다.
    """
    result = runner.invoke(app, ["stats", "--db", ""])
    assert result.exit_code == 1, result.output
    assert "비어 있습니다" in result.output, result.output
    assert "디렉터리" not in result.output, result.output


def test_a_real_directory_is_still_reported_as_a_directory(tmp_path):
    """판별력: 원인 구분이 살아 있어야 한다 — 둘 다 같은 문장이면 고친 게 아니다."""
    directory = tmp_path / "a-directory"
    directory.mkdir()
    result = runner.invoke(app, ["stats", "--db", str(directory)])
    assert result.exit_code == 1, result.output
    assert "디렉터리" in result.output, result.output


def test_healthy_db_path_still_opens(tmp_path):
    """판별력: 정상 경로는 그대로 열려야 한다."""
    repository = Repository(tmp_path / "nested" / "store.db")
    repository.close()
    result = runner.invoke(app, ["list", "--db", str(tmp_path / "nested" / "store.db")])
    assert result.exit_code == 0, result.output


def test_corrupt_db_is_not_silently_recreated(tmp_path):
    """손상 DB를 지우고 새로 만들면 사용자의 인용이 조용히 사라진다."""
    path = tmp_path / "corrupt.db"
    payload = b"SQLite format 3 -- not really" * 40
    path.write_bytes(payload)
    with pytest.raises(StorageError):
        Repository(path)
    assert path.read_bytes() == payload


# -- D-147: gc --keep 0 -------------------------------------------------------


@pytest.mark.parametrize("keep", ["0", "-1"])
def test_gc_keep_below_one_is_reported(keep, tmp_path):
    result = runner.invoke(app, ["gc", "--keep", keep, "--db", str(tmp_path / "gc.db")])
    assert _no_traceback(result), result.exception
    assert result.exit_code == 1
    assert "실패" in result.output


def test_gc_keep_one_still_works(tmp_path):
    result = runner.invoke(app, ["gc", "--keep", "1", "--db", str(tmp_path / "gc.db")])
    assert result.exit_code == 0, result.output


# -- D-149 / D-154: --older-than ---------------------------------------------


@pytest.mark.parametrize("value", ["nan", "inf", "1e400", "-inf", "NaN", "Infinity"])
def test_non_finite_durations_are_domain_errors(value):
    with pytest.raises(AnchorError):
        _parse_older_than(value)


@pytest.mark.parametrize("value", ["", "   ", "nan", "inf", "1e400", "7x", "d", "--"])
def test_cli_verify_rejects_unusable_older_than(value, tmp_path):
    """D-154 — 빈 문자열이 "필터 없음"이 되면 전체 앵커가 재검증된다."""
    result = runner.invoke(
        app, ["verify", "--older-than", value, "--db", str(tmp_path / "v.db")]
    )
    assert _no_traceback(result), result.exception
    assert result.exit_code == 1, result.output


# `７d`(전각 숫자)는 Python의 float()가 받아들여 7일로 읽힌다 — 사용자가 적은
# 뜻 그대로이므로 거절할 이유가 없다.
@pytest.mark.parametrize("value", ["7d", "12H", "3600", "P7D", "0s", "0", "７d"])
def test_cli_verify_accepts_valid_durations(value, tmp_path):
    """판별력: 문서화된 형식은 그대로 통과해야 한다."""
    result = runner.invoke(
        app, ["verify", "--older-than", value, "--db", str(tmp_path / "v.db")]
    )
    assert result.exit_code == 0, result.output


def test_omitting_older_than_still_means_no_filter(tmp_path):
    result = runner.invoke(app, ["verify", "--db", str(tmp_path / "v.db")])
    assert result.exit_code == 0, result.output


# -- D-150: serve --transport -------------------------------------------------


@pytest.mark.parametrize("value", ["studio", "STDIO", "", "http ", "sse"])
def test_serve_rejects_unknown_transport(value, tmp_path):
    """세 경로(설정 파일·`anchor serve`·`anchor-mcp`)가 같은 값을 받아야 한다."""
    result = runner.invoke(app, ["serve", "--transport", value, "--db", str(tmp_path / "s.db")])
    assert _no_traceback(result), result.exception
    assert result.exit_code == 1, result.output


def test_anchor_mcp_entrypoint_rejects_unknown_transport(monkeypatch):
    from anchor import server as server_module

    monkeypatch.setattr("sys.argv", ["anchor-mcp", "--transport", "studio"])
    with pytest.raises(SystemExit) as caught:
        server_module.main()
    assert caught.value.code != 0


def test_transport_choices_are_shared(tmp_path):
    """세 경로가 **같은 사전**을 본다는 것을 코드로 고정한다."""
    from anchor.config import SERVER_TRANSPORTS

    assert tuple(SERVER_TRANSPORTS) == ("stdio", "http")


# -- 같은 결함의 다른 진입점 (조치 절차 3) -----------------------------------


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0, float("-inf")])
def test_service_verify_rejects_non_finite_older_than(bad, tmp_path):
    """D-149 — CLI만 막으면 MCP 도구와 라이브러리 직접 호출이 남는다."""
    from anchor.config import Config
    from anchor.service import Anchor

    config = Config(db_path=tmp_path / "s.db")
    with Anchor(db_path=config.db_path, config=config) as anchor:
        with pytest.raises(ValueError):
            anchor.verify(older_than=bad)
        # 판별력: 유한한 값은 그대로 통과한다.
        assert anchor.verify(older_than=0.0).checked == 0


def test_mcp_entrypoint_reports_storage_errors_instead_of_tracebacks(monkeypatch, tmp_path):
    """D-138 — MCP 서버 기동도 같은 계약을 진다. 트레이스백을 stdio로
    토해내면 클라이언트가 프로토콜 오류로 읽는다."""
    from anchor import server as server_module
    from anchor.config import Config

    a_directory = tmp_path / "dir"
    a_directory.mkdir()
    monkeypatch.setattr(server_module, "load_config", lambda: Config(db_path=tmp_path / "x.db"))
    monkeypatch.setattr("sys.argv", ["anchor-mcp", "--db", str(a_directory)])
    with pytest.raises(SystemExit) as caught:
        server_module.main()
    assert caught.value.code == 1


def test_mcp_entrypoint_reports_config_errors_instead_of_tracebacks(monkeypatch, tmp_path):
    """D-139·D-141 — 설정 오류로 서버가 못 뜨는 것도 정상적인 실패다."""
    from anchor import server as server_module
    from anchor.errors import ConfigError

    def _boom():
        raise ConfigError("fetch.user_agent must be printable ASCII — 설정값 범위 오류")

    monkeypatch.setattr(server_module, "load_config", _boom)
    monkeypatch.setattr("sys.argv", ["anchor-mcp"])
    with pytest.raises(SystemExit) as caught:
        server_module.main()
    assert caught.value.code == 1


# -- D-137: storage.compression이 실제 저장 경로에 닿는가 --------------------


def test_compression_level_reaches_the_store_and_stays_readable(tmp_path):
    """레벨은 설정에서 오고, 해제는 레벨을 모른다 (zstd 프레임 자기서술)."""
    from anchor.store.repository import ZSTD_LEVEL

    # 완전 반복 문자열은 레벨과 무관하게 같은 크기로 줄어 판별력이 없다.
    random.seed(7)
    words = ["인용", "표류", "링크", "부패", "아카이브", "메멘토", "해시", "앵커",
             "citation", "drift", "archive", "memento", "hash", "version"]
    text = " ".join(
        random.choice(words) + str(random.randint(0, 9999)) for _ in range(3000)
    )
    sizes = {}
    for level in (1, 22):
        path = tmp_path / f"c{level}.db"
        repository = Repository(path, compression_level=level)
        document = repository.create_document(
            url="https://example.invalid/a",
            original_url="https://example.invalid/a",
            title="t",
            now="2026-08-19T00:00:00Z",
        )
        version = repository.insert_version(
            document_id=document.id, text_hash=f"b3:{level}", raw_hash="b3:r",
            pipeline_version="p", captured_at="2026-08-19T00:00:00Z",
            byte_size=len(text.encode("utf-8")), normalized_text=text, http_status=200,
        )
        sizes[level] = len(
            repository._connection.execute(
                "SELECT content_blob FROM versions WHERE id = ?", (version.id,)
            ).fetchone()["content_blob"]
        )
        repository.close()
        # 다른 레벨로 연 저장소가 그 본문을 그대로 읽는다 — 마이그레이션 불필요.
        other = Repository(path, compression_level=ZSTD_LEVEL)
        assert other.get_version_text(version.id) == text
        other.close()
    assert sizes[22] < sizes[1], sizes  # 레벨이 실제로 전달됐다


def test_service_uses_the_configured_compression_level(tmp_path):
    from anchor.config import Config
    from anchor.service import Anchor

    config = Config(db_path=tmp_path / "s.db", compression="zstd:3")
    with Anchor(db_path=config.db_path, config=config) as anchor:
        assert anchor._repository._compression_level == 3
