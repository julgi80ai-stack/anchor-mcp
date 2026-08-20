# SPDX-License-Identifier: Apache-2.0
"""로컬 HTTP 픽스처 서버 (SPEC §12 통합 층위). 네트워크 없이 재현한다."""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from anchor.config import Config
from anchor.service import Anchor


# 게이트가 열리지 않을 때 픽스처 서버가 영원히 붙잡히지 않게 하는 상한.
_GATE_TIMEOUT_SECONDS = 30.0


def article_html(*, nonce: str = "n0", extra_sentence: str = "") -> str:
    """trafilatura가 본문을 추출할 수 있는 수준의 기사 HTML.

    nonce는 <script> 안에만 존재해 원본 바이트는 바꾸되 추출된 본문은
    바꾸지 않는다 — 이중 해시(raw/text)의 분리를 검증하는 장치.
    """
    return f"""<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="utf-8"><title>재페치 낭비 보고서</title>
<script>var abTestBucket = "{nonce}";</script>
</head><body>
<article>
<h1>재페치 낭비 보고서</h1>
<p>AI 크롤러 트래픽의 절반 이상이 변하지 않은 페이지를 다시 가져오는 데 쓰인다.
이것은 측정된 사실이며, 조건부 요청만으로도 상당 부분을 제거할 수 있다.</p>
<p>학술 문헌이 참조한 웹 콘텐츠의 약 75%가 3년 안에 어느 정도 변경된 것으로
조사되었다. 링크는 살아 있지만 내용이 바뀌는 인용 표류가 가장 위험하다.</p>
<p>조건부 요청과 본문 해시를 함께 쓰면 재페치와 재파싱을 모두 줄일 수 있다.
정규화된 본문을 기준으로 해시하면 광고 노이즈에 속지 않는다.{extra_sentence}</p>
</article>
</body></html>"""


class FixtureState:
    def __init__(self) -> None:
        self.html: str = article_html()
        self.etag: str | None = '"v1"'
        self.robots: str = "User-agent: *\nDisallow: /private\n"
        # robots.txt도 평범하게 200만 주지 않는다 — 3xx·BOM·거대 본문·지연이
        # 실제 사이트의 평범한 모습이고, 거기가 계약이 깨지는 자리다.
        self.robots_status: int = 200
        self.robots_location: str | None = None   # 3xx일 때의 Location
        self.robots_body_override: bytes | None = None  # BOM·크기·인코딩 시험용 원바이트
        self.robots_content_type: str = "text/plain"    # charset 축 (D-190)
        self.robots_delay: float = 0.0
        self.status_override: int | None = None
        # 실패 응답에 실을 추가 헤더. `Retry-After`가 있는 403과 없는 403은
        # 서로 다른 사건이다 — 전자는 "그때 다시 오라"는 초대이고 후자는
        # 항구적 거부다. 이 축이 없으면 재시도 정책을 시험할 수 없다 (D-237).
        self.status_override_headers: dict[str, str] = {}
        self.requests: list[str] = []  # 수신한 경로 순서
        # 요청이 **언제** 왔는가 (경로, monotonic 초). 횟수만으로는 예절을
        # 관측할 수 없다 — "4번 두드렸다"와 "6ms 안에 4번 두드렸다"는 서로
        # 다른 사실이고, 후자가 D-275다. 재시도 간격이라는 축은 시각 없이는
        # 픽스처에 존재하지 않는다.
        self.request_times: list[tuple[str, float]] = []
        # 아카이브 에뮬레이션: 설정 시 CDX·MemGator API·/web/ 재생이 살아난다.
        self.archive_html: str | None = None
        # 재생 응답의 상태코드 축 (D-282). 변환 프록시 뒤에서는 아카이브
        # 재생본도 203으로 온다 — 200만 있는 픽스처에는 그 축이 없다.
        self.archive_replay_status: int = 200
        self.archive_timestamp: str = "20260801123456"
        self.response_delay: float = 0.0  # 문서 응답 지연(초) — 타임아웃 테스트용
        # 리다이렉트 맵: 요청 경로 → Location (상대·절대 모두 가능)
        self.redirects: dict[str, str] = {}
        self.redirect_status: int = 301
        # 경로별 상태코드 — 혼합 사슬(별칭은 301인데 목적지가 302)이 실제
        # 웹의 평범한 모습이고, 전역 단일 값으로는 그 축이 존재하지 않는다
        # (D-183이 살아남은 이유).
        self.redirect_statuses: dict[str, int] = {}
        # 경로마다 다른 본문·검증자. 어느 경로든 같은 HTML을 주면 "남의 문서에
        # 본문이 섞였다"를 관측할 수 없다 — 섞여도 똑같이 보이기 때문이다.
        self.bodies: dict[str, str] = {}
        self.etags: dict[str, str] = {}
        # 리다이렉트 응답의 본문. 실제 서버의 3xx는 대개 짧은 안내 문서를
        # 함께 준다 — 0바이트로 두면 "따라온 홉의 본문 바이트"라는 축이
        # 픽스처에 존재하지 않는다 (D-134가 살아남은 자리).
        self.redirect_body: bytes = b"<html><body>Moved.</body></html>"
        # 실패 응답에도 본문을 실을지 (크기 상한 검증용)
        self.status_override_with_body: bool = False
        # 애그리게이터 응답을 이상한 페이로드로 바꿔치기 (파싱 견고성 검증용)
        self.archive_payload_override: str | None = None
        # CDX 응답의 statuscode 축 (D-092). 우리가 `filter=statuscode:200`을
        # 요청했다는 사실은 응답이 그 필터를 지켰다는 증거가 아니다 — 필터를
        # 무시하는 미러, statuscode 열 자체를 주지 않는 미러가 실재한다.
        # 기본값(None)만 두면 그 축이 픽스처에 존재하지 않는다.
        self.cdx_header: list[str] | None = None   # None이면 표준 7열
        self.cdx_rows: list[list[str]] | None = None  # None이면 200 한 줄
        # 재생 본문을 타임스탬프별로 다르게 준다 — 어느 행을 채택했는지가
        # 본문으로 드러나야 "마지막 행"과 "마지막 200 행"을 구분할 수 있다.
        self.archive_html_by_timestamp: dict[str, str] = {}
        # -- 결정론적 겹침 장치 (D-128/D-129) --------------------------------
        # 동시성 계약은 sleep으로 재현하면 픽스처가 무효다(5단계 교훈). 요청이
        # 핸들러 안에서 **서로 만나게** 하고, 만났는지를 사실로 남긴다.
        self.arrival_barrier: threading.Barrier | None = None  # 문서 요청끼리 만나는 지점
        self.barrier_broken: bool = False       # 만나지 못했다(= 상대가 오지 못했다)
        self.arrived: dict[str, threading.Event] = {}  # 경로별 "도착했다" 신호
        self.gates: dict[str, threading.Event] = {}    # 경로별 "이제 응답해도 된다"
        self.inflight: int = 0                  # 지금 응답 중인 문서 요청 수
        self.max_inflight: int = 0              # 그 최대치 — 겹침의 관측값
        self.inflight_lock = threading.Lock()
        # -- "이미 받은 뒤 끊긴다" 축 (D-212~D-214) ---------------------------
        # 기존 실패 픽스처는 전부 **응답을 끝까지 받은** 실패다(지연·상태코드·
        # 크기 상한). 실제 웹에서 흔한 것은 그 반대다 — 헤더와 본문 일부가
        # 이미 도착한 뒤에 연결이 끊기거나 멈춘다. 그 축이 없으면 "받은
        # 바이트를 계상한다"는 회계 불변식이 어느 계층에서 깨져도 보이지
        # 않는다. 경로 **접두사** → 실제로 흘려보낼 바이트 수.
        self.cut_after: dict[str, int] = {}    # 그만큼 보낸 뒤 연결을 끊는다
        self.stall_after: dict[str, int] = {}  # 그만큼 보낸 뒤 멈춘다(읽기 타임아웃)
        self.stall_seconds: float = 3.0
        # 접두사별 실제 송신량 — 축의 관측값이다. 기대치를 하드코딩하지 않고
        # 여기서 읽어야 픽스처가 스스로 사실을 말한다.
        self.sent_bytes: dict[str, int] = {}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler 규약)
        state: FixtureState = self.server.state  # type: ignore[attr-defined]
        state.requests.append(self.path)
        state.request_times.append((self.path, time.monotonic()))

        if self.path == "/robots.txt" or self.path.startswith("/robots-"):
            if state.robots_delay:
                import time as _time

                _time.sleep(state.robots_delay)
            if state.robots_location is not None and self.path == "/robots.txt":
                self.send_response(state.robots_status)
                self.send_header("Location", state.robots_location)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = (
                state.robots_body_override
                if state.robots_body_override is not None
                else state.robots.encode("utf-8")
            )
            if self._serve_partial(state, body, state.robots_content_type):
                return
            self.send_response(200 if self.path != "/robots.txt" else state.robots_status)
            self.send_header("Content-Type", state.robots_content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # 아카이브 에뮬레이션 — status_override(원본 사망)보다 먼저 처리한다.
        if self.path.startswith(("/cdx/search/cdx", "/web/", "/api/json/")):
            self._serve_archive(state)
            return

        if self.path in state.redirects:
            self.send_response(
                state.redirect_statuses.get(self.path, state.redirect_status)
            )
            self.send_header("Location", state.redirects[self.path])
            self.send_header("Content-Length", str(len(state.redirect_body)))
            self.end_headers()
            if state.redirect_body:
                self.wfile.write(state.redirect_body)
            return

        # 문서 응답 구간. 여기서만 겹침을 세고 만나게 한다 — robots·아카이브·
        # 리다이렉트는 위에서 이미 반환됐다.
        with state.inflight_lock:
            state.inflight += 1
            state.max_inflight = max(state.max_inflight, state.inflight)
        try:
            self._rendezvous(state)
            self._serve_document(state)
        finally:
            with state.inflight_lock:
                state.inflight -= 1

    def _serve_partial(self, state: FixtureState, body: bytes, content_type: str) -> bool:
        """이 경로가 "보내다 끊긴다"로 설정돼 있으면 그렇게 응답하고 True.

        선언한 Content-Length보다 **적게** 준 뒤 끊거나(연결 절단) 멈춘다
        (읽기 타임아웃). 받는 쪽에서는 본문 도중에 실패한 것이고, 그때까지
        도착한 바이트는 이미 우리 손에 있다 — 그 바이트가 회계에 남는가가
        여기서 열리는 축이다 (D-212~D-214).
        """
        import time as _time

        for prefix, count in state.cut_after.items():
            if self.path.startswith(prefix):
                mode, limit, key = "cut", count, prefix
                break
        else:
            for prefix, count in state.stall_after.items():
                if self.path.startswith(prefix):
                    mode, limit, key = "stall", count, prefix
                    break
            else:
                return False

        sent = min(limit, len(body))
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        # 실제로 줄 것보다 크게 선언한다 — 그래야 클라이언트가 "본문 도중에
        # 끝났다"로 읽는다. 0바이트로 끝나면 그냥 짧은 응답일 뿐이다.
        self.send_header("Content-Length", str(len(body) + 1_000_000))
        self.end_headers()
        self.wfile.write(body[:sent])
        self.wfile.flush()
        state.sent_bytes[key] = state.sent_bytes.get(key, 0) + sent
        if mode == "stall":
            _time.sleep(state.stall_seconds)
        self.close_connection = True
        try:
            self.connection.close()
        except OSError:
            pass
        return True

    def _rendezvous(self, state: FixtureState) -> None:
        """요청들이 핸들러 안에서 만나는 지점 (D-128).

        지연만으로는 "두 요청이 실제로 겹쳤다"를 단언할 수 없다 — 느려서
        겹치지 않은 것과 락에 막혀 겹치지 못한 것이 똑같이 보인다. 도착을
        신호하고, 시험이 열어 줄 때까지 붙잡고, 서로 만나야 진행하게 하면
        겹침이 기제로 관측된다. 상대가 오지 못하면 `barrier_broken`이 사실로
        남는다 — 예외로 터뜨리지 않는 이유는 그 실패가 페치 재시도·폴백으로
        번져 관측 대상을 흐리기 때문이다.
        """
        arrived = state.arrived.get(self.path)
        if arrived is not None:
            arrived.set()
        barrier = state.arrival_barrier
        if barrier is not None:
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                state.barrier_broken = True
        gate = state.gates.get(self.path)
        if gate is not None and not gate.wait(timeout=_GATE_TIMEOUT_SECONDS):
            state.barrier_broken = True

    def _serve_document(self, state: FixtureState) -> None:
        if state.response_delay:
            import time as _time

            _time.sleep(state.response_delay)

        if state.status_override is not None:
            body = state.html.encode("utf-8") if state.status_override_with_body else b""
            self.send_response(state.status_override)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            for name, value in state.status_override_headers.items():
                self.send_header(name, value)
            self.end_headers()
            if body:
                self.wfile.write(body)
            return

        etag = state.etags.get(self.path, state.etag)
        if etag and self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.end_headers()
            return

        body = state.bodies.get(self.path, state.html).encode("utf-8")
        if self._serve_partial(state, body, "text/html; charset=utf-8"):
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if etag:
            self.send_header("ETag", etag)
        self.end_headers()
        self.wfile.write(body)

    def _serve_archive(self, state: FixtureState) -> None:
        import json as _json

        host = self.headers.get("Host", "")
        original = f"http://{host}/article"
        uri_m = f"http://{host}/web/{state.archive_timestamp}id_/{original}"

        if self.path.startswith("/web/"):
            stamp = self.path[len("/web/"):].split("id_/", 1)[0]
            if stamp in state.archive_html_by_timestamp:
                body = state.archive_html_by_timestamp[stamp].encode("utf-8")
                if self._serve_partial(state, body, "text/html; charset=utf-8"):
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if state.archive_html is None:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = state.archive_html.encode("utf-8")
            if self._serve_partial(state, body, "text/html; charset=utf-8"):
                return
            self.send_response(state.archive_replay_status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path.startswith("/cdx/search/cdx"):
            header = state.cdx_header or [
                "urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"
            ]
            if state.cdx_rows is not None:
                payload = _json.dumps(
                    [header]
                    + [
                        [
                            (original if cell == "$original" else cell)
                            for cell in row
                        ]
                        for row in state.cdx_rows
                    ]
                ).encode("utf-8")
            elif state.archive_html is None:
                payload = b"[]"
            else:
                payload = _json.dumps(
                    [
                        header,
                        ["key", state.archive_timestamp, original, "text/html", "200", "D", "1"],
                    ]
                ).encode("utf-8")
            if self._serve_partial(state, payload, "application/json"):
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        # MemGator Time Travel 호환 API
        if state.archive_payload_override is not None:
            payload = state.archive_payload_override.encode("utf-8")
            if self._serve_partial(state, payload, "application/json"):
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if state.archive_html is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        payload = _json.dumps(
            {
                "original_uri": original,
                "mementos": {
                    "last": {"datetime": "2026-08-01T12:34:56Z", "uri": uri_m},
                    "list": [{"datetime": "2026-08-01T12:34:56Z", "uri": uri_m}],
                },
            }
        ).encode("utf-8")
        if self._serve_partial(state, payload, "application/json"):
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        pass  # 테스트 출력을 어지럽히지 않는다


@pytest.fixture()
def fixture_server_factory():
    """픽스처 서버를 여러 개 띄운다.

    원본과 아카이브가 **다른 호스트**여야만 성립하는 계약이 있다 — 원본
    호스트의 robots가 판정 불능(5xx)인데 아카이브에서 구제되는 경로가
    그렇다 (D-133). 한 서버로는 그 축이 존재하지 않는다.
    """
    servers: list[ThreadingHTTPServer] = []

    def start() -> tuple[str, FixtureState]:
        state = FixtureState()
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        server.state = state  # type: ignore[attr-defined]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}", state

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.fixture()
def fixture_server(fixture_server_factory):
    return fixture_server_factory()


@pytest.fixture()
def anchor(tmp_path: Path):
    config = Config(
        db_path=tmp_path / "store.db",
        rate_limit_rps=1000.0,
        retry_backoff_base=0.01,
    )
    with Anchor(db_path=config.db_path, config=config) as instance:
        yield instance
