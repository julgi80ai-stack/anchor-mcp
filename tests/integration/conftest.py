# SPDX-License-Identifier: Apache-2.0
"""로컬 HTTP 픽스처 서버 (SPEC §12 통합 층위). 네트워크 없이 재현한다."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from anchor.config import Config
from anchor.service import Anchor


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
        self.requests: list[str] = []  # 수신한 경로 순서
        # 아카이브 에뮬레이션: 설정 시 CDX·MemGator API·/web/ 재생이 살아난다.
        self.archive_html: str | None = None
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


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler 규약)
        state: FixtureState = self.server.state  # type: ignore[attr-defined]
        state.requests.append(self.path)

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

        if state.response_delay:
            import time as _time

            _time.sleep(state.response_delay)

        if state.status_override is not None:
            body = state.html.encode("utf-8") if state.status_override_with_body else b""
            self.send_response(state.status_override)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
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
            if state.archive_html is None:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = state.archive_html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path.startswith("/cdx/search/cdx"):
            if state.archive_html is None:
                payload = b"[]"
            else:
                payload = _json.dumps(
                    [
                        ["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"],
                        ["key", state.archive_timestamp, original, "text/html", "200", "D", "1"],
                    ]
                ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        # MemGator Time Travel 호환 API
        if state.archive_payload_override is not None:
            payload = state.archive_payload_override.encode("utf-8")
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
