# SPDX-License-Identifier: Apache-2.0
"""httpx 기반 조건부 GET (SPEC §5.2 4단계, RFC 9110).

정직한 클라이언트로 동작한다: 위장 없음, Retry-After 존중,
429는 지수 백오프 재시도 후 그대로 보고 (SPEC §5.4).

리다이렉트는 클라이언트에 맡기지 않고 직접 따라간다. 목적지마다 robots를
다시 판정해야 하기 때문이다 — 자동 추종에 맡기면 금지된 경로·호스트를
그대로 가져오게 된다 (D-001).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace

import httpx

from anchor.errors import ContentTooLarge, FetchFailed, InvalidURL, RobotsDisallowed
from anchor.fetcher.urlnorm import normalize_url

ACCEPT_HEADER = "text/html, application/xhtml+xml, text/plain, application/pdf"
# 재시도가 **규약인** 상태. 429는 레이트 제한이고, 물러났다 다시 오는 것이
# 그 상태코드가 요구하는 행동이다.
RETRYABLE_STATUSES = frozenset({429})
# 재시도가 **초대받았을 때만** 성립하는 상태 (D-237). 403은 안티봇의 항구적
# 거부가 대부분이라 3회 재시도 + 지수 백오프가 매번 7초를 쓰고 같은 403으로
# 끝난다(사용자 실 저장소의 오류 17건 중 7건). SPEC §5.4는 "403은 403으로
# 보고한다"이지 "7초 뒤에 보고한다"가 아니다. 다만 서버가 `Retry-After`로
# 대기 시간을 **명시**했다면 그것은 "지금은 안 되지만 그때 다시 오라"는
# 초대이고, 그 말을 무시하는 것도 정직한 클라이언트가 아니다 — 해석 가능한
# 값이 실렸을 때만 재시도한다(값이 없거나 못 읽으면 초대가 아니다).
INVITED_RETRY_STATUSES = frozenset({403})
# 요청한 리소스의 **표현**을 담고 오는 응답 (RFC 9110 §15.3, D-282). 200만이
# 아니다 — `203 Non-Authoritative Information`의 페이로드는 원 서버의 200
# 응답을 변환 프록시가 변형한 표현 그 자체이며 캐시 가능하다(§15.3.4).
# 회사 게이트웨이·통신사 압축 프록시·일부 CDN 미들박스 뒤에서는 그것이 그
# 문서의 평범한 응답이다.
#
# 201·202·204·206은 여기 없다. 그 본문은 요청한 리소스가 아니다 — 생성 결과·
# 처리 상태 모니터·본문 없음·요청하지도 않은 부분 바이트다.
REPRESENTATION_STATUSES = frozenset({200, 203})
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
# 301·308만 "영구히 옮겼다"이다. 302·307은 지금만 다른 곳을 보라는 뜻이고,
# 303은 다른 리소스를 보라는 뜻이라 정본 URL을 바꿀 근거가 아니다 (D-102).
PERMANENT_REDIRECTS = frozenset({301, 308})
MAX_RETRIES = 3
# 이보다 오래 기다리라는 응답은 재시도하지 않고 그대로 보고한다. 몰래
# 일찍 두드리는 것보다 "확인 불가"가 정직하다 (SPEC §5.4).
MAX_RETRY_AFTER_SECONDS = 60.0


@dataclass(frozen=True)
class FetchResponse:
    status: int
    content: bytes
    content_type: str
    etag: str | None
    last_modified: str | None
    retry_after: str | None
    final_url: str
    bytes_down: int
    elapsed_ms: int
    location: str | None = None  # 3xx의 Location 헤더
    # 리다이렉트 사슬이 **전부** 영구였는가. 하나라도 일시가 섞이면 거짓이다.
    permanent_redirect: bool = False


class ConditionalFetcher:
    def __init__(
        self,
        client: httpx.Client,
        *,
        user_agent: str,
        max_content_bytes: int,
        retry_backoff_base: float = 1.0,
        max_redirects: int = 5,
        min_retry_delay: float = 0.0,
    ) -> None:
        self._client = client
        self._user_agent = user_agent
        self._max_content_bytes = max_content_bytes
        self._retry_backoff_base = retry_backoff_base
        self._max_redirects = max_redirects
        # 재시도 사이의 **바닥** (D-275). 호출자가 그 호스트에 스스로 약속한
        # 간격(1/rate_limit_rps)을 넘겨준다 — 재시도도 그 호스트로 나가는
        # 요청이기 때문이다. 레이트 제한은 홉 단위로만 걸려서 재시도 홉은
        # 통째로 빠져 있었다.
        self._min_retry_delay = min_retry_delay

    def get(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        validators_for: str | None = None,
        before_hop: Callable[[str], int] | None = None,
        on_bytes: Callable[[int], None] | None = None,
    ) -> FetchResponse:
        """조건부 GET. `before_hop`은 매 홉 직전에 호출되어 robots 판정과
        레이트 제한을 수행하고, 그 과정에서 내려받은 바이트를 돌려준다.

        `on_bytes`는 바이트를 **받는 즉시** 호출자에게 알린다 (D-134).
        여기 지역변수에만 쌓으면 리다이렉트 도중의 robots 거부처럼 예외로
        끝나는 경로에서 이미 나간 트래픽이 회계에서 통째로 사라진다."""
        base_headers = {"User-Agent": self._user_agent, "Accept": ACCEPT_HEADER}
        # 검증자는 **우리가 그것을 받은 리소스에만** 유효하다 (D-100).
        #
        # 모든 홉에 실어 보내면, 목적지가 RFC 9110 §13.1.3대로 자기 검증자와
        # 비교해 정직하게 304를 줬을 때 Anchor가 "변한 것 없음"으로 읽어 옛
        # 본문을 현재 내용으로 계속 반환한다 — 이사한 사실이 영구히 감지되지
        # 않고, 부수로 타 호스트에 ETag가 샌다.
        #
        # 반대로 첫 홉에만 싣는 것도 틀리다. 리다이렉트되는 별칭으로 재확인할
        # 때 검증자가 붙어야 하는 곳은 별칭이 아니라 **정본**이다 (D-007).
        # 그래서 "어느 리소스의 검증자인가"를 받아 그 홉에서만 싣는다.
        # 비교는 **정규화 형태**로 한다 (D-187). 날것 문자열로 재면 질의
        # 정렬·추적 파라미터·프래그먼트가 붙는 순간 영영 일치하지 않아
        # 조건부 요청이 조용히 무력해진다 — 같은 리소스인가라는 질문에는
        # 같은 리소스 판별 규칙(§5.1)으로 답해야 한다.
        try:
            validator_target = normalize_url(validators_for or url)
        except InvalidURL:
            validator_target = None
        conditional = {}
        if etag:
            conditional["If-None-Match"] = etag
        if last_modified:
            conditional["If-Modified-Since"] = last_modified

        def headers_for(hop_url: str) -> dict[str, str]:
            merged = dict(base_headers)
            try:
                same = (
                    validator_target is not None
                    and normalize_url(hop_url) == validator_target
                )
            except InvalidURL:
                same = False
            if same:
                merged.update(conditional)
            return merged

        def account(count: int) -> int:
            """받은 바이트를 즉시 호출자에게도 흘린다 (D-134)."""
            if on_bytes is not None and count:
                on_bytes(count)
            return count

        started = time.monotonic()
        overhead_bytes = 0
        attempted_bytes = 0
        current = url
        permanent = True

        for hop in range(self._max_redirects + 1):
            if before_hop is not None:
                overhead_bytes += account(before_hop(current))

            headers = headers_for(current)
            response = self._request(current, headers, account)
            attempted_bytes += account(response.bytes_down)
            for attempt in range(MAX_RETRIES):
                if not _may_retry(response):
                    break
                delay = self._retry_delay(response, attempt)
                if delay is None:  # 서버가 지정한 대기가 상한을 넘는다
                    break
                time.sleep(delay)
                response = self._request(current, headers, account)
                attempted_bytes += account(response.bytes_down)

            if response.status not in REDIRECT_STATUSES:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                return replace(
                    response,
                    elapsed_ms=elapsed_ms,
                    bytes_down=attempted_bytes + overhead_bytes,
                    permanent_redirect=permanent and current != url,
                )

            location = response.location
            if not location:
                raise FetchFailed(
                    f"Redirect without Location — 목적지 없는 리다이렉트: {current}",
                    http_status=response.status,
                    reason="redirect",
                )
            try:
                destination = httpx.URL(current).join(location)
            except httpx.InvalidURL as error:
                # `httpx.InvalidURL`은 `httpx.HTTPError`의 하위가 **아니다**.
                # 여기서 계층 안으로 접지 않으면 mailto:·about:blank Location
                # 하나가 재검증 배치 전체를 죽인다 (D-106).
                raise FetchFailed(
                    f"Unfollowable Location — 따라갈 수 없는 Location: "
                    f"{location!r} ({current})",
                    http_status=response.status,
                    reason="redirect",
                ) from error
            if destination.scheme not in ("http", "https"):
                # join이 성공해도 http(s)가 아니면 웹 자원이 아니다.
                raise FetchFailed(
                    f"Non-http Location — http(s)가 아닌 Location: {location!r}",
                    http_status=response.status,
                    reason="redirect",
                )
            if httpx.URL(current).scheme == "https" and destination.scheme == "http":
                # https로 요청했는데 평문으로 내려간다. 조용히 따라가면
                # 무결성 보장이 없는 채널에서 받은 본문이 인용 근거가 되고
                # 정본 URL이 평문으로 기록된다 (D-104).
                raise FetchFailed(
                    f"Refusing https → http downgrade — 평문으로의 강등 거부: "
                    f"{current} → {destination}",
                    http_status=response.status,
                    reason="redirect",
                )
            permanent = permanent and response.status in PERMANENT_REDIRECTS
            current = str(destination)

        raise FetchFailed(
            f"Too many redirects — 리다이렉트 한도 초과: {url}", reason="redirect"
        )

    def _retry_delay(self, response: FetchResponse, attempt: int) -> float | None:
        """다음 재시도까지의 대기. 대기가 상한을 넘으면 None(재시도 없음).

        규칙은 둘이다.

        1. **서버가 지정했으면 그 값을 쓴다** (D-238, SPEC §10 "실패는 빨라야
           한다"). `max(requested, backoff)`는 더 정중한 방향이지만 서버의
           지시를 덮어쓰는 것이고, 그만큼 호출자를 붙잡는다 — `Retry-After: 1`을
           준 서버에게 우리 백오프(1·2·4초)가 총 7초를 기다리게 했다.
        2. **바닥은 있다** (D-275). 1만 남기면 `Retry-After: 0`·음수·과거
           HTTP-date에서 대기가 0이 되어, 429를 준 서버를 6ms 안에 네 번
           두드린다(실측). 그건 지시를 존중한 것이 아니라 지시를 핑계로 예절을
           버린 것이다. 바닥은 우리가 그 호스트에 이미 약속한 간격
           (`1/rate_limit_rps`)과 백오프 기준값 중 큰 쪽이다 — 새 상수를
           만들지 않는 이유는 예절의 크기를 정하는 곳이 이미 설정에 있기
           때문이다.

        바닥이 상한을 넘으면 재시도하지 않는다. 몰래 일찍 두드리는 것보다
        "확인 불가"가 정직하다 (SPEC §5.4).
        """
        floor = max(self._retry_backoff_base, self._min_retry_delay)
        backoff = self._retry_backoff_base * (2**attempt)
        raw = response.retry_after
        requested = None if raw is None else _parse_retry_after(raw)
        # 해석 불가(None)는 지정이 없는 것과 같다 — 우리 백오프로 물러선다.
        delay = backoff if requested is None else requested
        delay = max(delay, floor)
        if delay > MAX_RETRY_AFTER_SECONDS:
            # 지정 시각보다 일찍 두드리지 않는다 (SPEC §5.4 "항상 존중").
            return None
        return delay

    def _request(
        self,
        url: str,
        headers: dict[str, str],
        account: Callable[[int], int] | None = None,
    ) -> FetchResponse:
        # 수신량은 `with` 밖에서 센다. 안에만 두면 전송 중단 예외와 함께
        # 사라져, 이미 우리 손에 온 바이트가 회계에서 증발한다 (D-212).
        total = 0
        try:
            with self._client.stream("GET", url, headers=headers) as response:
                declared = response.headers.get("Content-Length")
                if declared and declared.isdigit() and int(declared) > self._max_content_bytes:
                    raise ContentTooLarge(
                        f"Content-Length {declared} exceeds the "
                        f"{self._max_content_bytes}-byte limit — 본문 크기 상한 초과"
                    )
                # 크기 상한은 실패 응답에도 적용한다. 거대한 오류 페이지나
                # 차단 인터스티셜을 통째로 버퍼링하지 않는다 (D-002).
                chunks: list[bytes] = []
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > self._max_content_bytes:
                        if account is not None:
                            account(total)  # 이미 받은 것은 받은 것이다 (D-134)
                        raise ContentTooLarge(
                            f"Body exceeds the {self._max_content_bytes}-byte limit — "
                            "본문 크기 상한 초과"
                        )
                    chunks.append(chunk)
                content = b"".join(chunks)

                fetched = FetchResponse(
                    status=response.status_code,
                    content=content,
                    content_type=response.headers.get("Content-Type", ""),
                    etag=response.headers.get("ETag"),
                    last_modified=response.headers.get("Last-Modified"),
                    retry_after=response.headers.get("Retry-After"),
                    final_url=str(response.url),
                    bytes_down=len(content),
                    elapsed_ms=0,
                    location=response.headers.get("Location"),
                )
                return fetched
        except httpx.InvalidURL as error:
            # `httpx.InvalidURL`은 `httpx.HTTPError`의 하위가 **아니다**. 게다가
            # httpx는 `follow_redirects=False`여도 `next_request`를 만들기 위해
            # Location을 미리 해석하므로, `mailto:`·`about:blank` Location이면
            # **첫 응답을 받는 자리에서** 이 예외가 난다 — 우리 홉 루프의 방어가
            # 닿기 전이다. 여기서 계층 안으로 접지 않으면 문서 하나가 재검증
            # 배치 전체를 죽인다 (D-106).
            _spill(account, total)
            raise FetchFailed(
                f"Server sent an unfollowable Location — 따라갈 수 없는 Location: {url}",
                reason="redirect",
            ) from error
        except httpx.ConnectTimeout as error:
            # 연결이 아예 성립하지 않는 것은 **호스트 소멸의 흔한 모습**이다
            # (방화벽 DROP·블랙홀 IP·주차된 도메인). 살아 있지만 느린 서버의
            # 읽기 타임아웃과 같은 칸에 넣으면 아카이브 구제가 막힌다 (D-181).
            _spill(account, total)
            raise FetchFailed(
                f"Connection timed out — 연결 타임아웃: {url}", reason="network"
            ) from error
        except httpx.TimeoutException as error:
            _spill(account, total)
            raise FetchFailed(f"Timeout — 타임아웃: {url}", reason="timeout") from error
        except httpx.TooManyRedirects as error:
            _spill(account, total)
            raise FetchFailed(
                f"Too many redirects — 리다이렉트 한도 초과: {url}", reason="redirect"
            ) from error
        except httpx.HTTPError as error:
            _spill(account, total)
            raise FetchFailed(
                f"Network error — 네트워크 오류: {url} ({error})", reason="network"
            ) from error


def _may_retry(response: FetchResponse) -> bool:
    """이 응답을 다시 두드려도 되는가 (D-237).

    429는 무조건, 403은 서버가 `Retry-After`로 초대했을 때만이다.
    """
    if response.status in RETRYABLE_STATUSES:
        return True
    if response.status not in INVITED_RETRY_STATUSES:
        return False
    return response.retry_after is not None and _parse_retry_after(response.retry_after) is not None


def _spill(account: Callable[[int], int] | None, total: int) -> None:
    """전송이 중단돼도 이미 받은 바이트는 받은 것이다 (D-212, SPEC §7.7 불변식 2).

    성공 반환 경로는 호출자가 `response.bytes_down`으로 계상하므로 여기서
    흘리지 않는다 — 이 함수는 **예외로 나가는 출구에서만** 부른다. 그래서
    이중 계상이 생기지 않는다.
    """
    if account is not None and total:
        account(total)


def _parse_retry_after(raw: str) -> float | None:
    """`Retry-After`를 초로. 숫자와 HTTP-date를 모두 받는다 (D-004/D-005)."""
    raw = raw.strip()
    try:
        seconds = float(raw)
    except ValueError:
        pass
    else:
        # NaN·무한대는 float()가 통과시키므로 여기서 걸러야 한다 (D-005).
        if seconds != seconds or seconds in (float("inf"), float("-inf")):
            return None
        return max(0.0, seconds)

    from email.utils import parsedate_to_datetime

    try:
        moment = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max(0.0, (moment - now).total_seconds())
