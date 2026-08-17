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
        self.status_override: int | None = None
        self.requests: list[str] = []  # 수신한 경로 순서
        # 아카이브 에뮬레이션: 설정 시 CDX·MemGator API·/web/ 재생이 살아난다.
        self.archive_html: str | None = None
        self.archive_timestamp: str = "20260801123456"
        self.response_delay: float = 0.0  # 문서 응답 지연(초) — 타임아웃 테스트용


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler 규약)
        state: FixtureState = self.server.state  # type: ignore[attr-defined]
        state.requests.append(self.path)

        if self.path == "/robots.txt":
            body = state.robots.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # 아카이브 에뮬레이션 — status_override(원본 사망)보다 먼저 처리한다.
        if self.path.startswith(("/cdx/search/cdx", "/web/", "/api/json/")):
            self._serve_archive(state)
            return

        if state.response_delay:
            import time as _time

            _time.sleep(state.response_delay)

        if state.status_override is not None:
            self.send_response(state.status_override)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if state.etag and self.headers.get("If-None-Match") == state.etag:
            self.send_response(304)
            if state.etag:
                self.send_header("ETag", state.etag)
            self.end_headers()
            return

        body = state.html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if state.etag:
            self.send_header("ETag", state.etag)
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
def fixture_server():
    state = FixtureState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.state = state  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    yield base_url, state
    server.shutdown()
    server.server_close()


@pytest.fixture()
def anchor(tmp_path: Path):
    config = Config(
        db_path=tmp_path / "store.db",
        rate_limit_rps=1000.0,
        retry_backoff_base=0.01,
    )
    with Anchor(db_path=config.db_path, config=config) as instance:
        yield instance
