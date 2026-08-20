# SPDX-License-Identifier: Apache-2.0
"""포착 범위 계측 — 우리가 이 문서의 얼마를 보았는가 (D-239).

본문 추출기는 문서의 일부만 본문으로 인정한다. 그 밖에서 일어난 개정은
`normalized_text`에 나타나지 않으므로 `text_hash`가 같고, 판정은 `unchanged`가
되며, 그 문서의 모든 앵커가 `INTACT`로 남는다. 실측: RFC 9110은 가시 텍스트
446,095자 중 5,590자(1.3%)만 저장되고, `<aside>` 32개가 **전부** 사라진다 —
그 안의 정정 고지는 사람이 화면에서 볼 수 있는데 저장 본문에는 없다.

이 모듈은 그 사각지대를 **없애지 않는다**. 추출 범위를 넓히는 것은 반대편
문제(보일러플레이트 유입)를 만들고 별건 설계 과제로 미뤄져 있다(D-073·D-162).
여기서 고치는 것은 **침묵**이다 — 도구가 자기가 문서의 얼마를 보고 그 말을
하는지 함께 밝히게 한다. 판정(outcome·INTACT/ALTERED)은 이 모듈이 건드리지
않는다.

무엇을 재는가 (후보 비교는 SPEC §5.5):

  **기각 — 문자 보존율** (저장 문자수 / 가시 텍스트 문자수). 계산은 싸지만
  정상 문서와 사각지대를 가르지 못한다. 실측(실제 웹 22건)에서 RFC 9110은
  0.013, AP 뉴스 기사는 0.107, BBC 색인은 0.038이었다 — 앞의 것을 잡는
  어떤 문턱도 뒤의 둘을 함께 잡는다. 내비게이션·쿠키 배너·푸터가 본문보다
  큰 것은 정상적이고 올바른 추출이며, 거기에 경보를 띄우면 22건 중 9건에서
  울린다(뉴스 기사·허브·블로그 색인 포함).

  **채택 — 산문 블록 포착률**. 원본에서 **산문 단위**(잎 블록 요소 중 40자
  이상, 링크 밀도 30% 이하)를 세고, 그중 저장 본문에서 발견되는 분량의 비율을
  낸다. 링크 밀도 조건이 내비게이션·관련기사·사이트맵을 걸러 내므로 정상
  뉴스 페이지에서 값이 높게 유지된다(실측 AP 기사 0.768, 사내 픽스처
  news-boilerplate 0.452 — 문자 보존율로는 각각 0.107·0.163). 같은 22건에서
  이 지표가 문턱 아래로 내려간 것은 2건이고 둘 다 참값이다.

어떻게 대조하는가 (D-271):

  16자 골격 지문은 **유일하지 않다**. 지문이 본문 어딘가에 있기만 하면
  포착으로 세던 초안에서, 같은 서두로 시작하는 정정 고지 30개가 본문에 남은
  문단 하나를 근거로 전부 살아났다 — 저장 본문 169자, 포착 5,028자, 경고
  0건. 침묵을 고치려던 기능이 **모르는 것을 안다고 말하는** 자리가 됐다.
  그래서 앞머리와 꼬리가 한 자리에서 맞아야 하고, 그 자리는 한 블록만
  보증하며, 신용은 그 자리가 저장 본문에서 차지한 길이를 넘지 못한다. 자세한
  것은 `_measure`.

언제 비율을 말하지 않는가 (D-273):

  분모가 붕괴하면 100%는 침묵보다 나쁘다. 실측 blog.rust-lang.org — 가시
  텍스트 15,661자 중 저장 본문은 162자인데, 링크 밀도·40자 조건이 분모를 거의
  다 걷어내 산문 단위가 105자만 남았고 그 105자가 포착되어 **포착률 1.0**이
  나왔다. 셀 만한 산문이 페이지에 비해 한 줌이면 `no-prose`와 같은 판단으로
  물러선다(비율 `None`).

문턱 (실측 근거는 `tests/unit/test_coverage_metric.py`가 코퍼스로 고정한다):

  `COVERAGE_MIN_PROSE_SHARE = 1/8` — 산문 단위가 가시 텍스트의 이 몫에 못
  미치면 비율을 말하지 않는다. 관측 53건(골든 36 + 구조 9 + 실웹 8)에서
  허브·색인은 0.007~0.018, 비율을 재는 문서는 0.358(내비게이션이 본문보다 큰
  뉴스) 이상, 골든은 0.821 이상이었다. 빈 구간 0.018~0.358 안에 있고 양쪽에서
  각각 7배·2.9배 떨어져 있다.

  `COVERAGE_WARN_RATIO = 1/3` — "못 본 산문이 포착한 본문의 두 배가 넘는다".
  실제 웹 22건 + 골든 36건에서 이 아래로 내려간 것은 RFC 9110(0.013)과 BBC
  색인(0.051)뿐이고, 뉴스 기사·블로그·문서 사이트는 하나도 없었다. 관측된
  분포의 빈 구간(0.051~0.464)에 놓인 값이며, 골든 36건의 최저치(0.805)에서
  멀다.

  `COVERAGE_BLIND_SPOT_RATIO = 2/3` — `raw_changed`(D-232)와 **함께** 쓸 때의
  문턱. 원본 바이트가 달라졌는데 추출 본문이 같다는 사실 자체가 이미 강한
  신호이므로, 여기서는 "산문의 3분의 1 이상을 못 본다"에서 말한다. 골든 36건은
  전부 0.805 이상이라 이 문턱 위에 있다.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from typing import NamedTuple

import lxml.html

from anchor.models import Coverage

# 못 본 산문이 포착한 본문의 두 배를 넘으면 말한다.
COVERAGE_WARN_RATIO = 1.0 / 3.0
# `raw_changed`와 겹칠 때의 문턱 — 산문의 3분의 1 이상을 못 볼 때.
COVERAGE_BLIND_SPOT_RATIO = 2.0 / 3.0
# 산문 단위가 가시 텍스트의 이 비율에 못 미치면 **비율을 말하지 않는다**
# (D-273). 근거는 위 모듈 설명의 관측 표.
COVERAGE_MIN_PROSE_SHARE = 1.0 / 8.0

# 화면에 글자를 내지 않는 요소. 여기 텍스트는 가시 텍스트가 아니다.
_INVISIBLE = ("script", "style", "noscript", "template", "head", "svg", "math")

# 세는 단위가 되는 의미 블록. `div`·`section`은 여기 없다 — 뉴스레터 홍보
# 문구처럼 컨테이너에 직접 담긴 보일러플레이트가 산문으로 계상되면 정상
# 기사에서 값이 무너진다 (실측: AP 기사 0.482 → 0.777).
_COUNTED = frozenset(
    {
        "p", "li", "td", "th", "dd", "dt", "blockquote", "figcaption",
        "caption", "pre", "summary", "h1", "h2", "h3", "h4", "h5", "h6",
    }
)
# 잎 판정용 — 이 중 하나라도 후손에 있으면 그 요소는 잎이 아니다(중복 계산 방지).
_CONTAINER = _COUNTED | frozenset(
    {
        "div", "section", "article", "figure", "main", "body", "nav", "footer",
        "header", "ul", "ol", "dl", "table", "tr", "tbody", "thead", "tfoot",
        "form", "aside", "details",
    }
)
# 정정·주석·부연이 실리는 구조. 미포착 블록을 이 조상 이름으로 귀속시켜
# "무엇을 못 봤는가"를 사용자가 행동 가능한 말로 받게 한다.
_NOTICE = frozenset({"aside", "figcaption", "details", "blockquote", "dd", "caption"})

# 산문 단위의 최소 길이. 이보다 짧은 것은 메뉴 항목·라벨·표 머리글이며,
# 세면 정상 문서에서 값이 요동친다.
_MIN_UNIT_CHARS = 40
# 링크가 이 비율을 넘으면 내비게이션·목록이지 산문이 아니다.
_MAX_LINK_DENSITY = 0.30
# 대조용 지문 길이. **한 곳(앞머리)에서만** 뽑는다.
#
# 처음에는 앞·중간·끝 세 곳에서 뽑아 하나라도 맞으면 살아남은 것으로 셌다.
# 그것이 과대계상을 만든다: 16자 골격은 긴 규격 문서 안에서 쉽게 충돌해
# (`…stepsare:` 같은 꼬리), **저장 본문에 없는 블록이 살아남은 것으로 계상**
# 된다. 실측에서 RFC 9110의 포착 문자수가 14,178자로 나왔는데 저장 본문
# 자체가 5,590자였다 — 담을 수 없는 양을 담았다고 말한 것이다(포착률 1.4%를
# 3.6%로 부풀림). 마크다운 변환에 대한 내성은 세 지문이 아니라 아래
# `_SKELETON` 정규화가 만든다(골든 최저 포착률 0.805). 지문 하나로는 충돌을
# 막지 못하므로 꼬리 지문을 **같은 자리에서** 함께 요구한다 — `_measure` 참조.
# 길이를 늘리는 것도 답이 아니다 — 24자로 늘리면 추출기가 문자 하나를
# 지우는 문서(ZWJ 소실, D-173)에서 멀쩡한 블록이 죽는다(en-31: 0.933→0.685).
_PROBE_CHARS = 16
# 한 블록이 살펴보는 후보 자리의 상한. 사실을 정하는 문턱이 아니라 **비용
# 상한**이다 — 같은 앞머리가 본문에 이만큼 되풀이되는 문서라면 그 지문에
# 변별력이 없고, 더 뒤져도 사실을 더 알게 되지 않는다. 코퍼스 53건(골든 36 +
# 구조 9 + 실웹 8)의 실측 최대는 29(`repeated-heads`)·22(docs.python.org)이며
# 상한은 그 두 배 위다. `test_candidate_scan_stays_far_below_its_cost_ceiling`이
# 코퍼스로 고정한다.
_MAX_CANDIDATES = 64

# 문서 첫머리의 XML 선언. lxml은 인코딩 선언이 붙은 **문자열** 입력을 거부하고
# (`ValueError: Unicode strings with encoding declaration are not supported`),
# 그 예외를 삼키면 `application/xhtml+xml` 문서 전체가 조용히 `unknown`이 된다
# (D-272). 바이트로 되돌려 파싱하지 않는 이유: 여기 오는 `html`은 이미 HTTP
# charset·meta·BOM을 종합해 정한 인코딩으로 디코드된 문자열이고(`decode_bytes`),
# 저장 본문도 같은 디코드에서 나왔다. 다시 인코드해 lxml에게 선언을 믿고 재해석
# 하게 하면 **분자와 분모가 서로 다른 디코드**로 갈라진다. 선언은 화면에 글자를
# 내지 않으므로 떼어 내도 잴 것이 달라지지 않는다.
_XML_DECLARATION = re.compile(r"^[\s\ufeff]*<\?xml[^>]*\?>")

_WHITESPACE = re.compile(r"\s+")
# 마크다운 변환이 넣거나 빼는 문자. 양쪽에서 똑같이 지우고 비교한다.
_SKELETON = re.compile(r"[\s*_`~#|\\>\-]+")


def _flat(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def _skeleton(text: str) -> str:
    return _SKELETON.sub("", text)


def _probe(skeleton: str) -> str:
    return skeleton[:_PROBE_CHARS]


def _skeleton_index(text: str) -> tuple[str, list[int], list[int]]:
    """저장 본문의 골격과, 골격 위치를 본문 위치로 되돌리는 구간 표.

    신용을 **저장 본문에서 실제로 차지한 자리**로 환산하기 위해 필요하다
    (D-271). 골격만으로는 "이 블록이 본문의 몇 자를 썼는가"를 말할 수 없다.
    """
    pieces: list[str] = []
    skeleton_starts: list[int] = []
    body_starts: list[int] = []
    total = 0
    position = 0
    for match in _SKELETON.finditer(text):
        if match.start() > position:
            chunk = text[position:match.start()]
            pieces.append(chunk)
            skeleton_starts.append(total)
            body_starts.append(position)
            total += len(chunk)
        position = match.end()
    if position < len(text):
        chunk = text[position:]
        pieces.append(chunk)
        skeleton_starts.append(total)
        body_starts.append(position)
    return "".join(pieces), skeleton_starts, body_starts


def _body_position(
    skeleton_starts: list[int], body_starts: list[int], position: int, empty: int
) -> int:
    if not skeleton_starts:
        return empty
    index = bisect_right(skeleton_starts, position) - 1
    if index < 0:
        return body_starts[0]
    return body_starts[index] + (position - skeleton_starts[index])


def _is_free(starts: list[int], ends: list[int], start: int, end: int) -> bool:
    """이 구간이 아직 어느 블록의 근거로도 쓰이지 않았는가.

    구간들은 서로 겹치지 않고 정렬돼 있으므로 양옆 하나씩만 보면 된다.
    """
    index = bisect_right(starts, start) - 1
    if index >= 0 and ends[index] > start:
        return False
    following = index + 1
    return not (following < len(starts) and starts[following] < end)


class _Measured(NamedTuple):
    """계측의 날것. 무엇을 **말할지**는 `measure_html`이 정한다."""

    prose_chars: int
    captured_chars: int
    dropped: tuple[tuple[str, int], ...]
    visible_chars: int


def _measure(html: str, body_text: str) -> _Measured | None:
    """산문 단위를 세고, 저장 본문이 그중 어디를 담고 있는지 확인한다.

    **한 자리는 한 블록만 보증한다** (D-271). 16자 지문은 유일하지 않다 —
    같은 서두로 시작하는 정정 고지 30개가 있으면 본문에 남은 문단 하나가
    30개 전부를 '포착'으로 만들어, **저장 본문에 없는 글을 다 봤다**고 말하게
    된다(실측: 저장 본문 169자, 포착 5,028자, 경고 0건). 그래서 세 가지를
    함께 요구한다.

      1. **앞머리와 꼬리가 같은 자리에서 맞는다.** 꼬리는 앞머리가 걸린 곳
         에서 그 블록 길이 안에서만 찾는다 — 창을 블록 길이로 두면 상수를
         새로 들여올 일이 없다. 앞머리만 보면 머리 충돌이, 꼬리만 보면 꼬리
         충돌(`repeated-tails`)이 그대로 산다.
      2. **이미 쓰인 자리는 다시 쓰지 않는다.** 지문이 가리킨 구간이 다른
         블록의 근거로 이미 쓰였으면 그 블록은 포착이 아니다.
      3. **신용은 그 자리가 저장 본문에서 차지한 길이를 넘지 못한다.**
         42만 자짜리 블록이 85자짜리 본문에서 앞머리를 맞혀도 신용은 85자다.

    이 셋이 불변식을 코드에서 성립시킨다 — 신용 구간은 서로 겹치지 않고 각
    신용은 자기 구간의 본문 길이 이하이므로 **포착 문자수 ≤ 저장 본문 길이**
    다. 넘친 값을 잘라 맞추는 것(클램프)이 아니라 판정을 조인 것이다.

    문서 순서는 가정하지 않는다. 실측에서 추출 본문의 블록 순서는 원본 트리
    순서와 달랐다(RFC 9110: 트리에서 먼저 나오는 문단이 본문 끝에 있다).
    같은 앞머리를 가진 블록끼리만 앞으로 나아가는 탐색 위치를 공유한다.

    실패하면 `None`이다 — 계측이 페치를 죽이면 안 된다.
    """
    try:
        tree = lxml.html.document_fromstring(_XML_DECLARATION.sub("", html, count=1))
    except Exception:
        return None

    try:
        for tag in _INVISIBLE:
            for element in list(tree.iter(tag)):
                element.drop_tree()

        visible_chars = len(_flat(tree.text_content()))
        target, skeleton_starts, body_starts = _skeleton_index(body_text)
        searched_from: dict[str, int] = {}
        claim_starts: list[int] = []
        claim_ends: list[int] = []
        prose_chars = 0
        captured_chars = 0
        dropped: dict[str, int] = {}

        for element in tree.iter():
            tag = element.tag
            if not isinstance(tag, str) or tag not in _COUNTED:
                continue
            if any(
                isinstance(child.tag, str) and child.tag in _CONTAINER
                for child in element.iterdescendants()
            ):
                continue  # 잎이 아니다 — 후손에서 센다
            text = _flat(element.text_content())
            if len(text) < _MIN_UNIT_CHARS:
                continue
            link_chars = sum(len(_flat(a.text_content())) for a in element.iter("a"))
            if link_chars / len(text) > _MAX_LINK_DENSITY:
                continue
            unit = _skeleton(text)
            if len(unit) < _PROBE_CHARS:
                # 골격이 지문보다 짧다 — 낱말이 없는 구분선(`* * *`)이다.
                # 세면 지문이 빈 문자열이 되어 **어느 본문에나** 들어 있다.
                continue
            prose_chars += len(text)

            head = _probe(unit)
            tail = unit[-_PROBE_CHARS:]
            scan = searched_from.get(head, 0)
            start = end = -1
            attempts = 0
            while attempts < _MAX_CANDIDATES:
                position = target.find(head, scan)
                if position < 0:
                    break
                attempts += 1
                scan = position + 1
                window = min(position + len(unit), len(target))
                stop = target.find(tail, position, window)
                if stop >= 0 and _is_free(
                    claim_starts, claim_ends, position, stop + _PROBE_CHARS
                ):
                    start, end = position, stop + _PROBE_CHARS
                    # 다음 같은-앞머리 블록은 **성공한 자리 뒤**에서 찾는다.
                    # 실패한 탐색이 이 자리를 태우면, 앞의 블록 하나가 못
                    # 찾았다는 이유로 뒤의 멀쩡한 블록이 전부 죽는다.
                    searched_from[head] = scan
                    break
            if end < 0:
                where = _attribution(element, tag)
                dropped[where] = dropped.get(where, 0) + 1
                continue
            index = bisect_right(claim_starts, start)
            claim_starts.insert(index, start)
            claim_ends.insert(index, end)
            span = _body_position(
                skeleton_starts, body_starts, end, len(body_text)
            ) - _body_position(skeleton_starts, body_starts, start, 0)
            captured_chars += min(len(text), max(span, 0))
    except Exception:
        return None

    return _Measured(
        prose_chars=prose_chars,
        captured_chars=captured_chars,
        dropped=tuple(sorted(dropped.items(), key=lambda item: (-item[1], item[0]))),
        visible_chars=visible_chars,
    )


def measure_html(html: str, body_text: str) -> Coverage:
    """원본 HTML의 산문 중 저장 본문에 남은 비율을 잰다.

    계측이 페치를 죽이면 안 된다 — 어떤 이유로든 실패하면 "모른다"를
    돌려준다. 모르는 것을 1.0으로 적으면 사각지대가 확신으로 바뀐다.
    """
    measured = _measure(html, body_text)
    if measured is None:
        return Coverage(basis="unknown")
    if measured.prose_chars == 0:
        # 색인 페이지처럼 셀 만한 산문이 없다. 0%도 100%도 사실이 아니다.
        return Coverage(basis="no-prose")
    if measured.prose_chars < measured.visible_chars * COVERAGE_MIN_PROSE_SHARE:
        # 분모가 페이지에 비해 한 줌이다 — 그 한 줌이 전부 포착돼도 그것은
        # 이 페이지를 얼마나 봤는지에 대한 답이 아니다 (D-273).
        return Coverage(basis="no-prose")
    return Coverage(
        basis="html-prose",
        prose_chars=measured.prose_chars,
        captured_chars=measured.captured_chars,
        dropped=measured.dropped,
    )


def _attribution(element, tag: str) -> str:
    """미포착 블록을 정정이 실릴 법한 조상 구조 이름으로 귀속시킨다."""
    node = element
    while node is not None:
        node_tag = node.tag
        if isinstance(node_tag, str) and node_tag in _NOTICE:
            return node_tag
        node = node.getparent()
    return tag


def whole_document() -> Coverage:
    """text/plain — 고른 것이 없으므로 버린 영역도 없다."""
    return Coverage(basis="whole-document")


def not_measurable() -> Coverage:
    """PDF — 가시 텍스트를 독립적으로 잴 수단이 없다.

    pypdf가 뽑은 텍스트를 분모로 삼으면 비율이 항상 1.0이 되어 **거짓
    확신**을 만든다. 2단 조판 PDF는 문자를 잃지 않고 순서만 뒤섞으므로
    (D-075) 그 1.0은 "다 봤다"는 뜻이 아니다. 재지 않은 것은 재지 않았다고
    적는다.
    """
    return Coverage(basis="not-measurable")


def format_ratio(ratio: float) -> str:
    """비율을 사람이 읽는 백분율로. 1% 미만은 `0%`로 붕괴시키지 않는다 —
    RFC 9110은 3.6%이고, 그것을 `4%`로 적는 것과 `0%`로 적는 것은 다르다."""
    if ratio >= 0.01:
        return f"{ratio * 100:.0f}%"
    return f"{ratio * 100:.2g}%"


def _dropped_phrase(coverage: Coverage) -> str:
    if not coverage.dropped:
        return ""
    return ", ".join(f"{tag}×{count}" for tag, count in coverage.dropped[:4])


def coverage_notes(coverage: Coverage | None, *, raw_changed: bool = False) -> tuple[str, ...]:
    """포착 범위에 대해 할 말이 있으면 문장으로 돌려준다 (없으면 빈 튜플).

    **판정을 바꾸는 말은 하지 않는다.** `unchanged`는 여전히 `unchanged`이며,
    여기 문장은 "그 말을 얼마나 보고 하는가"만 덧붙인다.
    """
    if coverage is None:
        return ()
    ratio = coverage.ratio
    if ratio is None:
        return ()
    notes: list[str] = []
    percent = format_ratio(ratio)
    dropped = _dropped_phrase(coverage)
    dropped_en = f" Blocks not captured: {dropped}." if dropped else ""
    dropped_kr = f" 미포착 블록: {dropped}." if dropped else ""
    if ratio < COVERAGE_WARN_RATIO:
        notes.append(
            f"Coverage: only {percent} of this page's prose "
            f"({coverage.captured_chars}/{coverage.prose_chars} chars) is in the stored "
            f"body, so a revision outside it would not change the verdict above."
            f"{dropped_en}"
            f" — 포착 범위: 이 페이지 산문의 {percent}만"
            f"({coverage.captured_chars}/{coverage.prose_chars}자) 저장 본문에 있습니다. "
            f"그 밖에서 일어난 개정은 위 판정에 나타나지 않습니다.{dropped_kr}"
        )
    if raw_changed and ratio < COVERAGE_BLIND_SPOT_RATIO:
        notes.append(
            f"The raw bytes differed while the extracted body did not, and only "
            f"{percent} of this page's prose is captured — the change may lie outside "
            f"what is looked at."
            f" — 원본 바이트는 달라졌는데 추출 본문은 같습니다. 이 페이지 산문의 "
            f"{percent}만 포착하므로, 달라진 곳이 보는 범위 밖일 수 있습니다."
        )
    return tuple(notes)
