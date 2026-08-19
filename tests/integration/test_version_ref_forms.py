# SPDX-License-Identifier: Apache-2.0
"""버전 참조 문자열의 **형** 축 (D-085 / D-086) + diff 인자의 **부호** 축 (D-211).

축: `latest~N`의 N 자리에 올 수 있는 것들 — 정상(0·1·범위 초과)뿐 아니라
음수·비정수·빈 값·소수·부호·거대값·전각 숫자. 정상값만 두면 D-085가 그대로
산다: 음수는 `back >= len(versions)` 가드를 통과해 파이썬 음수 인덱스로
들어가 **반대쪽 끝**(가장 오래된 판본)을 조용히 돌려준다. "인용 당시 원문을
되살린다"는 이 프로젝트의 핵심 기능이 정확히 반대의 답을 준다.

같은 함수가 `get_version`·`diff_versions` 두 도구의 공통 길목이므로, 두
진입점(MCP 도구·라이브러리)에서 함께 확인한다.
"""

from __future__ import annotations

import pytest
from mcp.client.client import Client

from anchor.config import Config
from anchor.errors import DocumentNotFound
from anchor.server import build_server
from anchor.service import Anchor
from tests.integration.conftest import article_html

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _config(tmp_path) -> Config:
    return Config(db_path=tmp_path / "store.db", rate_limit_rps=1000.0, retry_backoff_base=0.01)


def _seed_three_versions(anchor: Anchor, base_url: str, state) -> tuple[str, list[str]]:
    """관측 순서가 서로 다른 세 판본. 반환은 (문서 id, 오래된 → 최신 순 버전 id)."""
    ids: list[str] = []
    result = anchor.fetch(f"{base_url}/article")
    ids.append(result.version_id)
    for index, tail in enumerate((" 두 번째 판본의 문장이다.", " 세 번째 판본의 문장이다."), start=2):
        state.html = article_html(extra_sentence=tail)
        state.etag = f'"v{index}"'
        ids.append(anchor.fetch(f"{base_url}/article", max_age=0).version_id)
    return result.document_id, ids


# `latest~N`의 N 자리에 올 수 없는 것들. 전부 "해석 불가"로 거부돼야 한다.
MALFORMED_REFS = [
    "latest~-1",     # D-085: 지금은 가장 오래된 판본을 돌려준다
    "latest~-2",     # D-085: 두 번째로 오래된 판본
    "latest~abc",    # D-086: 생 ValueError("invalid literal for int()...")
    "latest~",       # D-086: 빈 값
    "latest~1.0",    # D-086: 소수
    "latest~ 1",     # 공백
    "latest~+1",     # 부호
    "latest~１",     # 전각 숫자 — int()는 받지만 사용자가 쓴 표기가 아니다
]


@pytest.mark.parametrize("ref", MALFORMED_REFS)
def test_malformed_version_ref_is_rejected_not_answered(tmp_path, fixture_server, ref):
    """형이 틀린 참조에 **답을 주어서는 안 된다**.

    특히 음수는 지금 조용히 반대쪽 끝을 돌려준다 — 오류보다 나쁘다.
    """
    base_url, state = fixture_server
    with Anchor(db_path=tmp_path / "s.db", config=_config(tmp_path)) as anchor:
        document_id, versions = _seed_three_versions(anchor, base_url, state)
        with pytest.raises(ValueError) as excinfo:
            anchor.get_version(None, document_id=document_id, ref=ref)
        message = str(excinfo.value)
        assert "latest~" in message, "무엇이 틀렸는지 사용자가 알 수 있어야 한다"
        assert "invalid literal for int" not in message, (
            "파이썬 내부 메시지가 그대로 노출됐다 (D-086)"
        )
        # 이중언어 오류 규약 — 영문과 한국어가 함께 있어야 한다.
        assert any("가" <= character <= "힣" for character in message)


def test_negative_ref_does_not_return_the_oldest_version(tmp_path, fixture_server):
    """D-085의 증상 자체: `latest~-1`이 **가장 오래된 판본**이 되어서는 안 된다."""
    base_url, state = fixture_server
    with Anchor(db_path=tmp_path / "s.db", config=_config(tmp_path)) as anchor:
        document_id, versions = _seed_three_versions(anchor, base_url, state)
        oldest = versions[0]
        try:
            version, _text = anchor.get_version(None, document_id=document_id, ref="latest~-1")
        except ValueError:
            return  # 거부가 정답
        raise AssertionError(
            f"latest~-1이 거부 없이 {version.id}를 돌려줬다 "
            f"(가장 오래된 판본={oldest}) — 되살린 것이 반대쪽 끝이다"
        )


@pytest.mark.parametrize("ref_index", [(0, 2), (1, 1), (2, 0)])
def test_wellformed_refs_still_resolve(tmp_path, fixture_server, ref_index):
    """정상 형은 그대로 동작해야 한다 — 거부가 과하면 그것도 회귀다."""
    back, expected = ref_index
    base_url, state = fixture_server
    with Anchor(db_path=tmp_path / "s.db", config=_config(tmp_path)) as anchor:
        document_id, versions = _seed_three_versions(anchor, base_url, state)
        version, _text = anchor.get_version(None, document_id=document_id, ref=f"latest~{back}")
        assert version.id == versions[expected]


def test_out_of_range_ref_is_still_document_not_found(tmp_path, fixture_server):
    """형은 맞고 범위만 넘은 참조는 종전대로 '해당 버전 없음'이다."""
    base_url, state = fixture_server
    with Anchor(db_path=tmp_path / "s.db", config=_config(tmp_path)) as anchor:
        document_id, _versions = _seed_three_versions(anchor, base_url, state)
        with pytest.raises(DocumentNotFound):
            anchor.get_version(None, document_id=document_id, ref="latest~9")
        with pytest.raises(DocumentNotFound):
            anchor.get_version(None, document_id=document_id, ref="latest~" + "9" * 30)


def test_diff_versions_shares_the_same_guard(tmp_path, fixture_server):
    """같은 함수를 쓰는 두 번째 진입점 — from/to 어느 쪽이든 거부돼야 한다."""
    base_url, state = fixture_server
    with Anchor(db_path=tmp_path / "s.db", config=_config(tmp_path)) as anchor:
        document_id, _versions = _seed_three_versions(anchor, base_url, state)
        with pytest.raises(ValueError):
            anchor.diff_versions(document_id, from_ref="latest~-1", to_ref="latest")
        with pytest.raises(ValueError):
            anchor.diff_versions(document_id, from_ref="latest~1", to_ref="latest~abc")


async def test_mcp_tools_reject_malformed_refs(tmp_path, fixture_server):
    """MCP 도구 두 곳 모두에서 도구 오류로 나와야 한다 — 클라이언트에게
    파이썬 트레이스백 문자열을 내보내지 않는다."""
    base_url, state = fixture_server
    server, service = build_server(db_path=tmp_path / "mcp.db", config=_config(tmp_path))
    try:
        with Anchor(db_path=tmp_path / "mcp.db", config=_config(tmp_path)) as seeder:
            document_id, versions = _seed_three_versions(seeder, base_url, state)
        async with Client(server) as client:
            got = await client.call_tool(
                "get_version", {"document_id": document_id, "ref": "latest~-1"}
            )
            assert got.is_error, (
                "latest~-1이 오류 없이 응답됐다 — 내용은 가장 오래된 판본이다"
            )
            diffed = await client.call_tool(
                "diff_versions", {"document_id": document_id, "from_version": "latest~abc"}
            )
            assert diffed.is_error
            # 정상 경로는 그대로여야 한다.
            fine = await client.call_tool(
                "get_version", {"document_id": document_id, "ref": "latest~2"}
            )
            assert not fine.is_error
            assert fine.structured_content["version_id"] == versions[0]
    finally:
        server.anchor_tasks.shutdown()
        service.close()


# ---------------------------------------------------------------------------
# D-211 — diff_versions(context_lines=음수). 인자의 **부호** 축.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("context_lines", [-1, -3])
def test_negative_context_lines_rejected_before_any_work(tmp_path, fixture_server, context_lines):
    """음수 context_lines는 **인자 오류**로 거부돼야 한다.

    거부가 익스포터 안쪽에만 있으면, 버전 참조가 먼저 실패하는 호출에서는
    사용자가 진짜 원인(음수 인자) 대신 '버전 없음'을 받는다 — D-122의
    선례대로 공통 길목에서 먼저 막는다.
    """
    base_url, state = fixture_server
    with Anchor(db_path=tmp_path / "s.db", config=_config(tmp_path)) as anchor:
        result = anchor.fetch(f"{base_url}/article")  # 버전 1개뿐 — latest~1은 없다
        with pytest.raises(ValueError) as excinfo:
            anchor.diff_versions(result.document_id, context_lines=context_lines)
        assert "context_lines" in str(excinfo.value)


async def test_mcp_diff_rejects_negative_context_lines(tmp_path, fixture_server):
    base_url, state = fixture_server
    server, service = build_server(db_path=tmp_path / "mcp.db", config=_config(tmp_path))
    try:
        async with Client(server) as client:
            fetched = await client.call_tool("fetch_document", {"url": f"{base_url}/article"})
            state.html = article_html(extra_sentence=" 두 번째 판본의 문장이다.")
            state.etag = '"v2"'
            await client.call_tool(
                "fetch_document", {"url": f"{base_url}/article", "max_age": 0}
            )
            result = await client.call_tool(
                "diff_versions",
                {
                    "document_id": fetched.structured_content["document_id"],
                    "context_lines": -1,
                },
            )
            assert result.is_error
    finally:
        server.anchor_tasks.shutdown()
        service.close()
