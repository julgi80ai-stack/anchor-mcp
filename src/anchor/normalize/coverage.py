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

문턱 (실측 근거는 `tests/unit/test_coverage_metric.py`가 코퍼스로 고정한다):

  `COVERAGE_WARN_RATIO = 1/3` — "못 본 산문이 포착한 본문의 두 배가 넘는다".
  실제 웹 22건 + 골든 36건에서 이 아래로 내려간 것은 RFC 9110(0.014)과 BBC
  색인(0.051)뿐이고, 뉴스 기사·블로그·문서 사이트는 하나도 없었다. 관측된
  분포의 빈 구간(0.051~0.464)에 놓인 값이며, 골든 36건의 최저치(0.809)에서
  멀다.

  `COVERAGE_BLIND_SPOT_RATIO = 2/3` — `raw_changed`(D-232)와 **함께** 쓸 때의
  문턱. 원본 바이트가 달라졌는데 추출 본문이 같다는 사실 자체가 이미 강한
  신호이므로, 여기서는 "산문의 3분의 1 이상을 못 본다"에서 말한다. 골든 36건은
  전부 0.809 이상이라 이 문턱 위에 있다.
"""

from __future__ import annotations

import re

import lxml.html

from anchor.models import Coverage

# 못 본 산문이 포착한 본문의 두 배를 넘으면 말한다.
COVERAGE_WARN_RATIO = 1.0 / 3.0
# `raw_changed`와 겹칠 때의 문턱 — 산문의 3분의 1 이상을 못 볼 때.
COVERAGE_BLIND_SPOT_RATIO = 2.0 / 3.0

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
# `_SKELETON` 정규화가 만든다(골든 최저 포착률은 두 방식 모두 0.809).
# 길이를 늘리는 것도 답이 아니다 — 24자로 늘리면 추출기가 문자 하나를
# 지우는 문서(ZWJ 소실, D-173)에서 멀쩡한 블록이 죽는다(en-31: 0.933→0.685).
_PROBE_CHARS = 16

_WHITESPACE = re.compile(r"\s+")
# 마크다운 변환이 넣거나 빼는 문자. 양쪽에서 똑같이 지우고 비교한다.
_SKELETON = re.compile(r"[\s*_`~#|\\>\-]+")


def _flat(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def _skeleton(text: str) -> str:
    return _SKELETON.sub("", text)


def _probe(skeleton: str) -> str:
    return skeleton[:_PROBE_CHARS]


def measure_html(html: str, body_text: str) -> Coverage:
    """원본 HTML의 산문 중 저장 본문에 남은 비율을 잰다.

    계측이 페치를 죽이면 안 된다 — 어떤 이유로든 실패하면 "모른다"를
    돌려준다. 모르는 것을 1.0으로 적으면 사각지대가 확신으로 바뀐다.
    """
    try:
        tree = lxml.html.document_fromstring(html)
    except Exception:
        return Coverage(basis="unknown")

    try:
        for tag in _INVISIBLE:
            for element in list(tree.iter(tag)):
                element.drop_tree()

        target = _skeleton(body_text)
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
            prose_chars += len(text)
            if _probe(_skeleton(text)) in target:
                captured_chars += len(text)
            else:
                where = _attribution(element, tag)
                dropped[where] = dropped.get(where, 0) + 1
    except Exception:
        return Coverage(basis="unknown")

    if prose_chars == 0:
        # 색인 페이지처럼 셀 만한 산문이 없다. 0%도 100%도 사실이 아니다.
        return Coverage(basis="no-prose")
    return Coverage(
        basis="html-prose",
        prose_chars=prose_chars,
        captured_chars=captured_chars,
        dropped=tuple(sorted(dropped.items(), key=lambda item: (-item[1], item[0]))),
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
