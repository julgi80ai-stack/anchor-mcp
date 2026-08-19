# SPDX-License-Identifier: Apache-2.0
"""인용의 **정체성**이 리다이렉트를 따라 움직이지 않는가 (D-244~D-247).

`documents.url`은 "지금 본문을 가지러 갈 곳"이다. 사용자가 **인용한 곳**은
`documents.original_url`이고, 그 둘은 301 하나로 갈라진다. 갈라진 뒤에도
내보내기가 `url`을 찍으면, 사용자는 자기가 인용한 적 없는 URL을 근거로
배포한다 — 기사가 삭제되고 홈으로 301되는(soft-404) 흔한 경우가 정확히
그 모양이다.

이 파일이 여는 축:
  ① 개명(목적지에 문서 없음) / 병합(양쪽 등록됨) / 신규(첫 페치가 리다이렉트)
  ② 영구(301·308) / 일시(302·307)
  ③ 앵커 있음 / 없음
  ④ 인용 시점이 이사 전 / 후
  ⑤ 기존 DB의 앵커(cited_url NULL)
  ⑥ 내보내기 3종(html·markdown·bibtex_note) · TimeMap 2종(link·json)
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from anchor.config import Config
from anchor.service import Anchor

# 사용자 실 저장소에서 실제로 굳은 모양 (46문서 중 2건): 안정적인 export URL이
# 일회용 세션 토큰 URL로 301된다. 토큰은 곧 만료돼 아무도 열 수 없다.
STABLE_PATH = "/document/d/1aBcD_ExAmPlE/export?format=txt"
TOKEN_PATH = "/export/9f2c7a1e4b/8d3e5f0a7c/"

QUOTE = "일회용 세션 토큰은 몇 분 뒤 만료되므로 인용의 정체성이 될 수 없다."


def _config(tmp_path, **overrides) -> Config:
    return Config(
        db_path=tmp_path / "store.db",
        rate_limit_rps=1000.0,
        retry_backoff_base=0.01,
        **overrides,
    )


def _page(title: str, sentence: str) -> str:
    return (
        f"<html><head><title>{title}</title></head><body><article>"
        f"<p>{sentence} 이 문단은 앵커를 만들 수 있을 만큼 길어야 하므로 문장을 더 둔다. "
        f"조건부 요청과 본문 해시를 함께 쓰면 재페치를 줄일 수 있다는 것이 이 문서의 요지다.</p>"
        f"</article></body></html>"
    )


def _exports(anchor, anchor_id) -> dict[str, str]:
    return {
        fmt: anchor.export_robust_links([anchor_id], fmt=fmt)[0][fmt]
        for fmt in ("html", "markdown", "bibtex_note")
    }


# -- D-244 / D-245: 신규 문서 — 첫 페치가 이미 리다이렉트다 -------------------


def test_export_names_the_url_the_user_asked_for_not_the_session_token(
    tmp_path, fixture_server_factory
):
    """사용자 실 저장소 사례 (D-244·D-245).

    안정적인 export URL을 열었는데 301로 일회용 토큰 URL에 도달한다. 문서의
    정본 URL은 토큰이 되고, 내보내기는 **사용자가 넘긴 적 있는 URL을 한 번도
    출력하지 않는다.** 토큰이 만료되면 그 인용은 아무도 확인할 수 없다.
    """
    docs_url, docs_state = fixture_server_factory()
    text_url, text_state = fixture_server_factory()
    stable = f"{docs_url}{STABLE_PATH}"
    token = f"{text_url}{TOKEN_PATH}"

    docs_state.redirects = {STABLE_PATH: token}
    docs_state.redirect_status = 301
    text_state.bodies = {TOKEN_PATH: _page("내보내기", QUOTE)}
    text_state.etags = {TOKEN_PATH: '"tok-1"'}

    with Anchor(config=_config(tmp_path)) as anchor:
        fetched = anchor.fetch(stable)
        cited = anchor.cite(fetched.document_id, QUOTE)
        items = _exports(anchor, cited.anchor_id)
        timemap_link = anchor.get_timemap(fetched.document_id, fmt="link")["body"]
        timemap_json = anchor.get_timemap(fetched.document_id, fmt="json")["body"]

    assert f'data-originalurl="{stable}"' in items["html"], items["html"]
    assert f'data-originalurl="{stable}"' in items["markdown"], items["markdown"]
    # BibTeX는 `_`를 이스케이프한다 — 그것이 이 형식의 정상 표기다.
    assert stable.replace("_", r"\_") in items["bibtex_note"], items["bibtex_note"]
    assert token not in items["bibtex_note"], items["bibtex_note"]
    assert f'<{stable}>; rel="original"' in timemap_link, timemap_link
    assert timemap_json["original_uri"] == stable
    # "지금 가서 볼 곳"은 여전히 토큰이다 — href는 바뀌지 않는다.
    assert f'href="{token}"' in items["html"], items["html"]


# -- D-244 / D-245: 개명 — 등록된 문서가 뒤늦게 이사한다 ---------------------


def test_a_rename_does_not_move_the_citation_identity(tmp_path, fixture_server_factory):
    """이사 **전에** 인용했다 (축 ①개명 ④인용이 이사 전).

    처음에는 리다이렉트가 없어 정본 URL = 안정적 URL이다. 인용한 뒤 사이트가
    301을 켜면 `rename_document_url`이 정본을 토큰으로 옮긴다 — 인용은 그
    자리에 있는데 인용의 출처만 바뀐다.
    """
    docs_url, docs_state = fixture_server_factory()
    text_url, text_state = fixture_server_factory()
    stable = f"{docs_url}{STABLE_PATH}"
    token = f"{text_url}{TOKEN_PATH}"

    docs_state.bodies = {STABLE_PATH: _page("내보내기", QUOTE)}
    docs_state.etags = {STABLE_PATH: '"st-1"'}
    text_state.bodies = {TOKEN_PATH: _page("내보내기", QUOTE)}
    text_state.etags = {TOKEN_PATH: '"tok-1"'}

    with Anchor(config=_config(tmp_path)) as anchor:
        first = anchor.fetch(stable)
        cited = anchor.cite(first.document_id, QUOTE)

        docs_state.redirects = {STABLE_PATH: token}
        docs_state.redirect_status = 301
        moved = anchor.fetch(stable, max_age=0)
        assert moved.url == token, "픽스처 전제: 정본 URL이 실제로 옮겨졌다"

        items = _exports(anchor, cited.anchor_id)
        timemap_link = anchor.get_timemap(moved.document_id, fmt="link")["body"]

    assert f'data-originalurl="{stable}"' in items["html"], items["html"]
    assert f'<{stable}>; rel="original"' in timemap_link, timemap_link


def test_a_citation_made_after_the_rename_still_names_the_stable_url(
    tmp_path, fixture_server_factory
):
    """이사 **후에** 인용했다 (축 ④인용이 이사 후).

    사용자가 세션에서 넘긴 URL은 여전히 안정적 URL이다 — 토큰 URL은 우리가
    리다이렉트를 따라가서 알게 된 것이지 사용자가 준 것이 아니다.
    """
    docs_url, docs_state = fixture_server_factory()
    text_url, text_state = fixture_server_factory()
    stable = f"{docs_url}{STABLE_PATH}"
    token = f"{text_url}{TOKEN_PATH}"

    docs_state.bodies = {STABLE_PATH: _page("내보내기", QUOTE)}
    docs_state.etags = {STABLE_PATH: '"st-1"'}
    text_state.bodies = {TOKEN_PATH: _page("내보내기", QUOTE)}
    text_state.etags = {TOKEN_PATH: '"tok-1"'}

    with Anchor(config=_config(tmp_path)) as anchor:
        anchor.fetch(stable)
        docs_state.redirects = {STABLE_PATH: token}
        docs_state.redirect_status = 301
        moved = anchor.fetch(stable, max_age=0)
        cited = anchor.cite(moved.document_id, QUOTE)
        items = _exports(anchor, cited.anchor_id)

    assert f'data-originalurl="{stable}"' in items["html"], items["html"]


# -- D-246: 병합 — 앵커가 남의 문서로 옮겨가면서 정체성을 잃는다 -------------


def test_a_merge_does_not_hand_the_anchor_a_url_it_never_cited(
    tmp_path, fixture_server_factory
):
    """양쪽이 각각 등록된 뒤 A→B 301이 생긴다 (축 ①병합).

    `merge_document`는 source의 `url`만 별칭으로 남기고 `original_url`은
    버린다 — 앵커는 target으로 옮겨가 target의 정체성을 물려받는다. 그래서
    A를 인용한 앵커의 내보내기가 **B의 URL**을 찍는다.
    """
    a_url, a_state = fixture_server_factory()
    b_url, b_state = fixture_server_factory()
    a_article = f"{a_url}/2026/report"
    b_article = f"{b_url}/archive/2026-report"

    a_state.bodies = {"/2026/report": _page("보고서", QUOTE)}
    a_state.etags = {"/2026/report": '"a-1"'}
    b_state.bodies = {"/archive/2026-report": _page("보고서", QUOTE)}
    b_state.etags = {"/archive/2026-report": '"b-1"'}

    with Anchor(config=_config(tmp_path)) as anchor:
        source = anchor.fetch(a_article)
        anchor.fetch(b_article)
        cited = anchor.cite(source.document_id, QUOTE)

        a_state.redirects = {"/2026/report": b_article}
        a_state.redirect_status = 301
        landed = anchor.fetch(a_article, max_age=0)
        assert landed.document_id != source.document_id, "픽스처 전제: 병합이 실제로 일어났다"

        items = _exports(anchor, cited.anchor_id)
        timemap_json = anchor.get_timemap(landed.document_id, fmt="json")["body"]

    assert f'data-originalurl="{a_article}"' in items["html"], items["html"]
    assert a_article in items["bibtex_note"], items["bibtex_note"]
    # TimeMap은 문서의 것이다 — 병합 뒤 그 문서의 정체성은 B다. 앵커의
    # 정체성(A)과 다르며, 그것이 사실이다.
    assert timemap_json["original_uri"] == b_article


def test_an_anchor_created_before_v9_falls_back_to_the_documents_identity(
    tmp_path, fixture_server_factory
):
    """v8 이하 DB에서 만들어진 앵커는 `cited_url`을 모른다 (축 ⑤).

    모르는 것을 1.0으로 채우지 않는다 — NULL이면 문서의 `original_url`로
    물러선다. 그것이 그 앵커에 대해 우리가 아는 전부다.
    """
    docs_url, docs_state = fixture_server_factory()
    text_url, text_state = fixture_server_factory()
    stable = f"{docs_url}{STABLE_PATH}"
    token = f"{text_url}{TOKEN_PATH}"

    docs_state.redirects = {STABLE_PATH: token}
    docs_state.redirect_status = 301
    text_state.bodies = {TOKEN_PATH: _page("내보내기", QUOTE)}
    text_state.etags = {TOKEN_PATH: '"tok-1"'}

    config = _config(tmp_path)
    with Anchor(config=config) as anchor:
        fetched = anchor.fetch(stable)
        cited = anchor.cite(fetched.document_id, QUOTE)

    # v9 이전에 만들어진 행을 흉내낸다 — 그 열은 존재한 적이 없다.
    connection = sqlite3.connect(config.db_path)
    try:
        connection.execute(
            "UPDATE anchors SET cited_url = NULL WHERE id = ?", (cited.anchor_id,)
        )
        connection.commit()
    finally:
        connection.close()

    with Anchor(config=config) as anchor:
        items = _exports(anchor, cited.anchor_id)

    assert f'data-originalurl="{stable}"' in items["html"], items["html"]


# -- D-247: 리다이렉트를 사실로 고지한다 (판단 없음) -------------------------


@pytest.mark.parametrize("status,permanent", [(301, True), (308, True), (302, False), (307, False)])
def test_fetch_reports_the_redirect_as_a_fact(
    tmp_path, fixture_server_factory, status, permanent
):
    """축 ②: 영구든 일시든 **리다이렉트가 있었다는 사실**은 같다.

    soft-404를 우리가 판정하지 않기로 했으므로(비싸고, 문턱이 곧 판단이다),
    에이전트가 스스로 이을 수 있게 사실을 건넨다 — "영구 리다이렉트 직후 그
    문서의 앵커가 전부 MISSING"이 soft-404의 모양이고, 우리는 그 두 신호 중
    하나를 지금 말하지 않는다.
    """
    docs_url, docs_state = fixture_server_factory()
    text_url, text_state = fixture_server_factory()
    stable = f"{docs_url}{STABLE_PATH}"
    token = f"{text_url}{TOKEN_PATH}"

    docs_state.redirects = {STABLE_PATH: token}
    docs_state.redirect_status = status
    text_state.bodies = {TOKEN_PATH: _page("내보내기", QUOTE)}
    text_state.etags = {TOKEN_PATH: '"tok-1"'}

    with Anchor(config=_config(tmp_path)) as anchor:
        result = anchor.fetch(stable)

    assert result.redirect is not None, f"{status}를 따라갔는데 고지가 없다"
    assert result.redirect.to == token
    assert result.redirect.permanent is permanent


def test_a_direct_200_discloses_no_redirect(tmp_path, fixture_server):
    """축 ②의 반대편: 리다이렉트가 없으면 고지도 없다 (없는 사실을 만들지 않는다)."""
    base_url, state = fixture_server
    state.bodies = {"/plain": _page("직행", QUOTE)}
    state.etags = {"/plain": '"p-1"'}

    with Anchor(config=_config(tmp_path)) as anchor:
        result = anchor.fetch(f"{base_url}/plain")

    assert result.redirect is None


def test_a_cache_hit_discloses_no_redirect(tmp_path, fixture_server_factory):
    """캐시 히트는 관측이 아니다 — 이번 호출에서 리다이렉트를 본 적이 없다."""
    docs_url, docs_state = fixture_server_factory()
    text_url, text_state = fixture_server_factory()
    stable = f"{docs_url}{STABLE_PATH}"
    token = f"{text_url}{TOKEN_PATH}"

    docs_state.redirects = {STABLE_PATH: token}
    text_state.bodies = {TOKEN_PATH: _page("내보내기", QUOTE)}
    text_state.etags = {TOKEN_PATH: '"tok-1"'}

    with Anchor(config=_config(tmp_path)) as anchor:
        anchor.fetch(stable)
        again = anchor.fetch(stable)

    assert again.outcome == "cache_hit"
    assert again.redirect is None


def test_a_304_still_discloses_the_redirect(tmp_path, fixture_server_factory):
    """조건부 요청이 304로 끝나도 **그 홉은 리다이렉트를 지났다**."""
    docs_url, docs_state = fixture_server_factory()
    text_url, text_state = fixture_server_factory()
    stable = f"{docs_url}{STABLE_PATH}"
    token = f"{text_url}{TOKEN_PATH}"

    docs_state.redirects = {STABLE_PATH: token}
    docs_state.redirect_status = 301
    text_state.bodies = {TOKEN_PATH: _page("내보내기", QUOTE)}
    text_state.etags = {TOKEN_PATH: '"tok-1"'}

    with Anchor(config=_config(tmp_path)) as anchor:
        anchor.fetch(stable)
        again = anchor.fetch(stable, max_age=0)

    assert again.outcome == "not_modified", "픽스처 전제: 조건부 요청이 304로 돌아왔다"
    assert again.redirect is not None
    assert again.redirect.to == token
    assert again.redirect.permanent is True


# -- 다른 진입점: CLI · MCP (재현 통과 ≠ 완료) --------------------------------


def test_cli_export_and_timemap_name_the_cited_url(tmp_path, fixture_server_factory):
    from typer.testing import CliRunner

    from anchor.cli import app

    runner = CliRunner()
    docs_url, docs_state = fixture_server_factory()
    text_url, text_state = fixture_server_factory()
    stable = f"{docs_url}{STABLE_PATH}"
    token = f"{text_url}{TOKEN_PATH}"
    db = str(tmp_path / "cli.db")

    docs_state.redirects = {STABLE_PATH: token}
    docs_state.redirect_status = 301
    text_state.bodies = {TOKEN_PATH: _page("내보내기", QUOTE)}
    text_state.etags = {TOKEN_PATH: '"tok-1"'}

    fetched = runner.invoke(app, ["fetch", stable, "--db", db])
    assert fetched.exit_code == 0, fetched.output
    # 리다이렉트는 사실이다 — 판단하지 않고 알린다 (D-247).
    assert token in fetched.output and "리다이렉트" in fetched.output, fetched.output

    cited = runner.invoke(app, ["cite", stable, QUOTE, "--db", db])
    assert cited.exit_code == 0, cited.output

    exported = runner.invoke(app, ["export", "--robust-links", "--db", db])
    assert exported.exit_code == 0, exported.output
    assert f'data-originalurl="{stable}"' in exported.output, exported.output

    tm = runner.invoke(app, ["timemap", stable, "--db", db])
    assert tm.exit_code == 0, tm.output
    assert f'<{stable}>; rel="original"' in tm.output, tm.output


def test_fetch_json_carries_the_redirect_fact(tmp_path, fixture_server_factory):
    from typer.testing import CliRunner

    from anchor.cli import app

    runner = CliRunner()
    docs_url, docs_state = fixture_server_factory()
    text_url, text_state = fixture_server_factory()
    stable = f"{docs_url}{STABLE_PATH}"
    token = f"{text_url}{TOKEN_PATH}"

    docs_state.redirects = {STABLE_PATH: token}
    docs_state.redirect_status = 302
    text_state.bodies = {TOKEN_PATH: _page("내보내기", QUOTE)}
    text_state.etags = {TOKEN_PATH: '"tok-1"'}

    result = runner.invoke(
        app, ["fetch", stable, "--json", "--db", str(tmp_path / "j.db")]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["redirect"] == {"to": token, "permanent": False}


def test_cite_writes_the_identity_onto_the_anchor_itself(tmp_path, fixture_server_factory):
    """앵커가 자기 정체성을 **직접** 들고 있는가 (D-246).

    지금 내보내기가 옳은 값을 내는 것만으로는 부족하다: 그 값이 문서를 경유해
    나온 것이라면, 문서를 지우는 경로가 하나 더 생기는 날 조용히 틀린다. 기록
    자체를 시험한다 — 폴백이 폴백으로 남는지가 여기서만 관측된다.

    (참고: `documents.original_url`은 생성 뒤 갱신되지 않고 `documents` 행을
    지우는 경로는 병합 하나뿐이므로, **오늘의** 내보내기 결과는 병합 시점
    보정만으로도 같다. 이 시험은 그 두 기제를 갈라 놓기 위한 것이다.)
    """
    docs_url, docs_state = fixture_server_factory()
    text_url, text_state = fixture_server_factory()
    stable = f"{docs_url}{STABLE_PATH}"
    token = f"{text_url}{TOKEN_PATH}"

    docs_state.redirects = {STABLE_PATH: token}
    docs_state.redirect_status = 301
    text_state.bodies = {TOKEN_PATH: _page("내보내기", QUOTE)}
    text_state.etags = {TOKEN_PATH: '"tok-1"'}

    config = _config(tmp_path)
    with Anchor(config=config) as anchor:
        fetched = anchor.fetch(stable)
        cited = anchor.cite(fetched.document_id, QUOTE)

    connection = sqlite3.connect(config.db_path)
    try:
        (recorded,) = connection.execute(
            "SELECT cited_url FROM anchors WHERE id = ?", (cited.anchor_id,)
        ).fetchone()
    finally:
        connection.close()
    assert recorded == stable, recorded


def test_a_pre_v9_anchor_keeps_its_identity_through_a_merge(
    tmp_path, fixture_server_factory
):
    """축 ⑤ × ①: **v9 이전 앵커**가 병합을 지난다 (D-246).

    그 앵커의 `cited_url`은 NULL이라 내보내기는 문서의 `original_url`로
    물러선다. 그런데 병합은 source의 `original_url`을 버리고 그 행을 지우는
    유일한 경로다 — 앵커는 target으로 옮겨가 **target의 정체성**을 물려받고,
    폴백이 조용히 남의 URL을 가리킨다. 병합 직전에 그 값을 앵커에 고정해야
    한다(새로 아는 사실을 만드는 것이 아니라, 지금 쓰고 있는 값을 잃지 않는
    것이다).
    """
    a_url, a_state = fixture_server_factory()
    b_url, b_state = fixture_server_factory()
    a_article = f"{a_url}/2026/report"
    b_article = f"{b_url}/archive/2026-report"

    a_state.bodies = {"/2026/report": _page("보고서", QUOTE)}
    a_state.etags = {"/2026/report": '"a-1"'}
    b_state.bodies = {"/archive/2026-report": _page("보고서", QUOTE)}
    b_state.etags = {"/archive/2026-report": '"b-1"'}

    config = _config(tmp_path)
    with Anchor(config=config) as anchor:
        source = anchor.fetch(a_article)
        anchor.fetch(b_article)
        cited = anchor.cite(source.document_id, QUOTE)

    # v9 이전에 만들어진 앵커 — 그 열은 존재한 적이 없다.
    connection = sqlite3.connect(config.db_path)
    try:
        connection.execute(
            "UPDATE anchors SET cited_url = NULL WHERE id = ?", (cited.anchor_id,)
        )
        connection.commit()
    finally:
        connection.close()

    with Anchor(config=config) as anchor:
        a_state.redirects = {"/2026/report": b_article}
        a_state.redirect_status = 301
        landed = anchor.fetch(a_article, max_age=0)
        assert landed.document_id != source.document_id, "픽스처 전제: 병합이 일어났다"
        items = _exports(anchor, cited.anchor_id)

    assert f'data-originalurl="{a_article}"' in items["html"], items["html"]
