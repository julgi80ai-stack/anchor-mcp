# SPDX-License-Identifier: Apache-2.0
"""공개 파사드. 페치 파이프라인(SPEC §5)의 판정 로직이 여기에 있다.

v0.1 완료 기준: 같은 URL 두 번 호출 시 두 번째가 네트워크 0바이트.
"""

from __future__ import annotations

import functools
import math
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import TracebackType

import httpx

from anchor.anchoring import approx, matcher
from anchor.anchoring.selector import QUALITY_SHORT, build_selector
from anchor.config import Config, load_config
from anchor.errors import (
    AnchorError,
    DocumentNotFound,
    FetchFailed,
    RobotsDisallowed,
    StorageError,
)
from anchor.fetcher.archive import ArchiveFallback, ArchiveHit
from anchor.fetcher.client import ConditionalFetcher, FetchResponse
from anchor.fetcher.ratelimit import HostRateLimiter
from anchor.fetcher.robots import RobotsGate
from anchor.fetcher.urlnorm import normalize_url
from anchor.models import (
    AnchorRecord,
    AttentionItem,
    CiteResult,
    Document,
    FetchResult,
    Network,
    VerifyReport,
    Version,
    age_seconds,
    iso_ago,
    parse_iso_duration,
    utcnow_iso,
)
from anchor.export import diff as export_diff
from anchor.export import robustlinks, timemap
from anchor.normalize import extract
from anchor.normalize.hashing import hash_bytes, hash_text
from anchor.store.repository import Repository

_STATUS_BY_HTTP = {402: "paywalled", 403: "forbidden", 404: "gone", 410: "gone"}
_DOCUMENT_STATUSES = ("live", "gone", "forbidden", "paywalled")

# 아직 문서로 등록되지 않은 URL의 실패를 기록할 때 쓰는 자리표시자.
# `fetch_log.document_id`가 NOT NULL이라 빈 값을 넣을 수 없다 (D-014).
_UNREGISTERED = "-"


class _Traffic:
    """한 번의 사용자 호출이 실제로 쓴 네트워크 (D-130·D-134·D-135).

    성공 반환 경로에서만 회계를 만들면, 실패로 끝난 호출의 바이트는 페처와
    폴백의 지역변수에 있다가 예외와 함께 사라진다. 받는 즉시 여기에 쌓아
    두고, 어떻게 끝나든 이 값으로 **한 행**을 남긴다.
    """

    __slots__ = ("bytes_down", "document_id", "http_status")

    def __init__(self) -> None:
        self.bytes_down = 0
        self.document_id: str | None = None
        self.http_status: int | None = None

    def add(self, count: int) -> None:
        self.bytes_down += count


# 아카이브 폴백으로 이어지는 원본 실패 (SPEC §5.2 5→6단계).
_ARCHIVE_FALLBACK_STATUSES = frozenset({402, 403, 404, 410, 429})

# 연결 자체가 안 되는 실패만 "사라졌을 수 있다"로 본다. 타임아웃·리다이렉트
# 이상은 회복 가능한 일시 실패이므로 폴백에 들어갈 자격이 없다 (D-088).
_ARCHIVE_FALLBACK_REASONS = frozenset({"network"})


def _may_consult_archive(error: AnchorError) -> bool:
    """이 실패가 아카이브를 확인할 자격이 있는가 (SPEC §5.2 6단계).

    사이트 소유자가 **명시적으로** 거부한 경우는 어떤 우회도 하지 않는다.
    규칙을 물어보지 못한 경우(호스트 소멸·robots 5xx)와 연결 실패는 다른
    호스트인 공개 아카이브를 확인하는 것까지 막을 이유가 없다 (D-053).
    """
    if isinstance(error, RobotsDisallowed):
        return error.reason != "explicit"
    if isinstance(error, FetchFailed):
        if error.http_status is not None:
            return error.http_status in _ARCHIVE_FALLBACK_STATUSES
        return error.reason in _ARCHIVE_FALLBACK_REASONS
    return False


class _UrlLockEntry:
    """URL 하나의 락과 그것을 쓰는(보유·대기) 사람 수."""

    __slots__ = ("lock", "refs")

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.refs = 0


class _UrlLocks:
    """URL 하나당 락 하나. 쓰는 사람이 없어지면 사라진다 (D-038·D-128).

    페치의 "조회 → 판단 → 생성"을 같은 URL끼리 직렬화하는 것은 의도다 —
    두 스레드가 동시에 "문서 없음"으로 판단하면 documents.url UNIQUE에
    걸리고, 같은 문서를 두 번 가져와 사이트에 두 배로 부담을 준다.

    문제는 **다른 URL 사이의 거짓 공유**였다. 고정 64개 스트라이프는 URL이
    십수 개만 되어도 충돌하고(생일 문제), 락은 네트워크 왕복 전체(기본 30초
    타임아웃 포함) 동안 잡혀 있다 — 실측으로 무관한 문서가 7.92초를 기다렸다.
    "성능 손해일 뿐"이 아니라 SPEC §10이 요구사항으로 못 박은 격리다.

    등록부 뮤텍스 아래에서 하는 일은 사전 조회와 참조 계수뿐이다 —
    네트워크도 SQLite도 그 밑에서 일어나지 않으므로, 캐시 히트 경로에
    실리는 비용은 경합 없는 잠금 두 번(µs 미만)이다. 참조 수는 보유자와
    대기자를 함께 세므로, 기다리는 사람이 있는 락은 지워지지 않는다.
    """

    def __init__(self) -> None:
        self._mutex = threading.Lock()
        self._entries: dict[str, _UrlLockEntry] = {}

    @contextmanager
    def acquire(self, key: str) -> Iterator[None]:
        with self._mutex:
            entry = self._entries.get(key)
            if entry is None:
                entry = _UrlLockEntry()
                self._entries[key] = entry
            entry.refs += 1
        try:
            entry.lock.acquire()
        except BaseException:
            self._release(key, entry)
            raise
        try:
            yield
        finally:
            entry.lock.release()
            self._release(key, entry)

    def _release(self, key: str, entry: _UrlLockEntry) -> None:
        with self._mutex:
            entry.refs -= 1
            # 같은 키의 새 항목이 이미 들어섰을 수 있다 — 내 것일 때만 지운다.
            if entry.refs == 0 and self._entries.get(key) is entry:
                del self._entries[key]

    # -- 관측 (동시성 계약은 관측 가능해야 회귀를 잡는다) --------------------

    def refcount(self, key: str) -> int:
        """그 URL의 락을 보유·대기 중인 수. 0이면 등록부에 없다."""
        with self._mutex:
            entry = self._entries.get(key)
            return entry.refs if entry is not None else 0

    def size(self) -> int:
        """등록부에 남은 락 수. 정상 상태에서는 진행 중인 페치 수와 같다."""
        with self._mutex:
            return len(self._entries)


# close()가 진행 중 호출을 기다리며 잠자코 있는 시간. 넘기면 알리고 계속
# 기다린다 — server.shutdown(D-117)과 같은 판단이다.
_CLOSE_GRACE_SECONDS = 10.0


class _CallGate:
    """진행 중인 공개 API 호출을 세고, 닫는 동안 새 호출을 막는다 (D-129).

    `close()`가 진행 중 작업을 기다리지도 이후 사용을 막지도 않아,
    `Anchor.__exit__`가 워커보다 먼저 끝나면 진행 중이던 페치가
    `Bad file descriptor`로 죽고 이후 호출이 생 `sqlite3.ProgrammingError`로
    샜다. 서버 경로는 "워커 정리 후 저장소 해제"(D-034/D-117)로 막혀 있었지만
    라이브러리 직접 사용 경로(SPEC §8)에는 같은 보장이 없었다.

    `foreground_section`(GIL 양보, D-196~D-204)과는 **별개 기제**다. 양보는
    배경 워커(정중 스레드)의 재진입을 일부러 표시하지 않는데, 종료 안전은
    바로 그 배경 호출까지 세어야 성립하기 때문이다.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition(threading.Lock())
        self._active = 0
        self._closing = False
        self._closed_event = threading.Event()
        self._local = threading.local()

    @property
    def closing(self) -> bool:
        return self._closing

    @contextmanager
    def entered(self) -> Iterator[None]:
        depth = getattr(self._local, "depth", 0)
        with self._condition:
            # 이미 이 스레드에서 시작된 호출의 내부 호출(verify → fetch)은
            # 막지 않는다. 막으면 "진행 중 호출은 끝까지 보장한다"가 깨진다.
            if self._closing and depth == 0:
                raise StorageError(
                    "store is closed — 저장소가 닫혔습니다 (close 이후의 호출)"
                )
            self._active += 1
        self._local.depth = depth + 1
        try:
            yield
        finally:
            self._local.depth = depth
            with self._condition:
                self._active -= 1
                self._condition.notify_all()

    def begin_close(self, grace: float) -> bool:
        """닫기를 시작하고 진행 중 호출이 끝나기를 기다린다.

        이미 닫혔거나 닫는 중이면 False — 이중 close는 무해하다. 유예가
        지나면 stderr로 알리고 **계속 기다린다**: 공개 API 호출은 전부
        유한하고(HTTP 타임아웃·재시도 상한 D-206, 앵커당 시간 예산 §10),
        여기서 포기하고 닫으면 그 호출이 죽어 고치려던 결함으로 되돌아간다.
        """
        own = getattr(self._local, "depth", 0)  # 내 호출 안에서 부른 close
        with self._condition:
            if self._closing:
                already_closing = True
            else:
                already_closing = False
                self._closing = True
        if already_closing:
            # 다른 스레드가 닫는 중이다. 자원 해제 전에 돌려보내면 "close가
            # 반환했으면 닫혔다"가 그 스레드에서만 거짓이 된다 — 기다린다.
            self._closed_event.wait()
            return False
        with self._condition:
            deadline = time.monotonic() + grace
            warned = False
            while self._active > own:
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    self._condition.wait(remaining)
                    continue
                if not warned:
                    print(
                        f"anchor: waiting for {self._active - own} in-flight call(s) to "
                        "finish before closing the store — 저장소를 닫기 전에 진행 중인 "
                        "호출이 끝나기를 기다리는 중",
                        file=sys.stderr,
                    )
                    warned = True
                self._condition.wait(1.0)
        return True

    def finish_close(self) -> None:
        """자원 해제가 끝났음을 알린다 — 같이 기다리던 close들이 돌아간다."""
        self._closed_event.set()


def _foreground(method):
    """전경(지연 민감) 호출 구간을 표시한다 (D-196).

    배경 워커의 매칭 루프는 이 구간이 열려 있는 동안에만 GIL 양보로
    잠든다. 배경 워커(정중 스레드) 자신의 재진입은 표시하지 않는다 —
    자기 자신을 위해 양보하게 만들지 않기 위해서다."""

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        # 종료 안전(D-129)은 양보 의미론과 별개 기제다 — 정중 스레드의
        # 호출도 세어야 close가 그것을 기다린다.
        with self._calls.entered():
            if approx.is_polite_thread():
                return method(self, *args, **kwargs)
            with approx.foreground_section():
                return method(self, *args, **kwargs)

    return wrapper


class Anchor:
    """SQLite 연결과 HTTP 세션을 함께 관리하는 컨텍스트 매니저 (SPEC §8)."""

    def __init__(self, db_path: Path | str | None = None, config: Config | None = None) -> None:
        # 어떤 자원보다 먼저 만든다 — 조립 도중 실패해도 close가 성립해야 한다.
        self._calls = _CallGate()
        self._config = config or load_config()
        self._repository = Repository(
            db_path or self._config.db_path,
            # 압축 레벨은 설정 키다 (D-137). zstd 프레임은 자기서술적이라
            # 레벨을 바꿔도 이미 저장된 버전은 그대로 읽힌다.
            compression_level=self._config.zstd_level,
        )
        # 리다이렉트를 자동으로 따라가지 않는다 — 목적지마다 robots를 다시
        # 판정해야 하므로 페처가 홉을 직접 관리한다 (D-001).
        self._client = httpx.Client(
            follow_redirects=False, timeout=self._config.timeout_seconds
        )
        self._fetcher = ConditionalFetcher(
            self._client,
            user_agent=self._config.user_agent,
            max_content_bytes=self._config.max_content_bytes,
            retry_backoff_base=self._config.retry_backoff_base,
            max_redirects=self._config.max_redirects,
        )
        self._robots = RobotsGate(
            self._repository,
            self._client,
            user_agent=self._config.user_agent,
            ttl_seconds=self._config.robots_ttl_seconds,
            respect_robots=self._config.respect_robots,
            max_content_bytes=self._config.max_content_bytes,
            timeout_seconds=self._config.timeout_seconds,
        )
        self._ratelimit = HostRateLimiter(
            rate=self._config.rate_limit_rps, burst=self._config.rate_limit_burst
        )
        self._url_locks = _UrlLocks()
        self._archive = ArchiveFallback(
            self._client,
            enabled=self._config.archive_fallback_enabled,
            aggregator=self._config.archive_aggregator,
            timeout_seconds=self._config.archive_timeout_seconds,
            user_agent=self._config.user_agent,
            ratelimit=self._ratelimit,
            robots=self._robots,
        )

    def __enter__(self) -> Anchor:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self, grace: float = _CLOSE_GRACE_SECONDS) -> None:
        """진행 중인 공개 API 호출이 끝난 뒤에 자원을 놓는다 (D-129, SPEC §8).

        순서는 서버 종료 경로(D-034/D-117)와 같다: 일하는 쪽을 먼저 비우고
        저장소를 해제한다. 반환 이후의 호출은 `StorageError`이지 닫힌 커넥션
        위의 생 sqlite3 예외가 아니다. 두 번 닫아도 무해하다.
        """
        if not self._calls.begin_close(grace):
            return
        try:
            self._client.close()
            self._repository.close()
        finally:
            # 해제가 실패하더라도 같이 기다리던 close를 붙잡아 두지 않는다.
            self._calls.finish_close()

    # -- public API --------------------------------------------------------

    @_foreground
    def fetch(
        self,
        url: str,
        *,
        max_age: int | None = None,
        force_refresh: bool = False,
        include_content: bool = True,
    ) -> FetchResult:
        if max_age is None:
            max_age = self._config.default_max_age
        started = time.monotonic()
        norm_url = normalize_url(url)
        # 같은 URL의 "조회 → 판단 → 생성"만 직렬화한다. 두 스레드가 동시에
        # "문서 없음"으로 판단하면 documents.url UNIQUE에 걸린다 (D-038).
        # 락은 그 URL 하나에만 걸린다 — 무관한 문서는 막지 않는다 (D-128).
        with self._url_locks.acquire(norm_url):
            return self._fetch_locked(
                norm_url, max_age, force_refresh, include_content, started
            )

    def _fetch_locked(
        self,
        norm_url: str,
        max_age: int,
        force_refresh: bool,
        include_content: bool,
        started: float,
    ) -> FetchResult:
        """사용자 호출 1회 = `fetch_log` 1행, outcome은 **최종 결과** (D-130·D-133).

        성공 경로에만 회계가 있으면 예외로 끝난 실패가 요청 분모에서 통째로
        빠져 hit_rate가 부풀고(실측 2.67배) 그 호출의 트래픽도 함께 사라진다 —
        지표가 실패의 **종류**에 좌우된다. 중간 실패를 그 자리에서 기록하면
        아카이브로 구제된 호출이 error 1행 + archive 1행이 된다. 그래서
        기록은 여기 한 곳에서만 한다.
        """
        traffic = _Traffic()
        try:
            return self._fetch_counted(
                norm_url, max_age, force_refresh, include_content, started, traffic
            )
        except AnchorError:
            self._log(
                traffic.document_id or _UNREGISTERED,
                "error",
                traffic.http_status,
                traffic.bytes_down,
                started,
            )
            raise

    def _fetch_counted(
        self,
        norm_url: str,
        max_age: int,
        force_refresh: bool,
        include_content: bool,
        started: float,
        traffic: _Traffic,
    ) -> FetchResult:
        document = self._repository.get_document_by_any_url(norm_url)
        traffic.document_id = document.id if document else None

        # 캐시 조회 — 순수 로컬 경로. 네트워크 요청이 없으므로 robots 판정보다
        # 앞선다 (robots는 "요청해도 되는가"의 규칙이다).
        if document and not force_refresh and max_age > 0:
            latest = self._repository.current_version(document.id)
            if latest and age_seconds(document.last_checked_at) <= max_age:
                return self._finish(
                    document, latest, "cache_hit", None, 0, started, include_content
                )

        def before_hop(hop_url: str) -> int:
            """홉마다 robots를 판정하고 레이트 제한을 지킨다 (D-001).

            리다이렉트 목적지도 예외가 아니다 — 자동 추종에 맡기면 금지된
            경로나 다른 호스트를 robots 요청조차 없이 가져오게 된다.
            """
            hop_verdict = self._robots.check(hop_url)
            if not hop_verdict.allowed:
                if document:
                    self._repository.set_robots_allowed(document.id, False)
                # 여기서 기록하지 않는다 — 아카이브로 구제되면 이 호출은
                # 결국 성공이고, 중간 실패를 남기면 한 호출이 두 행이 된다
                # (D-133). 다만 이 판정에 쓴 바이트는 페처에게 돌려줄 기회가
                # 없으므로(예외로 나간다) 여기서 직접 계상한다 (D-134).
                traffic.add(hop_verdict.bytes_down)
                raise RobotsDisallowed(
                    "Fetch disallowed by robots.txt — robots.txt가 페치를 거부: "
                    f"{hop_url}",
                    reason=hop_verdict.reason,
                )
            self._ratelimit.acquire(httpx.URL(hop_url).host or "")
            return hop_verdict.bytes_down

        try:
            response = self._fetcher.get(
                norm_url,
                etag=document.etag if document else None,
                last_modified=document.last_modified if document else None,
                # 검증자를 받은 리소스는 **문서의 정본 URL**이다. 사용자가
                # 별칭으로 재확인해도 검증자는 정본 홉에서만 실린다 (D-100).
                validators_for=document.url if document else None,
                before_hop=before_hop,
                # 받는 즉시 계상한다 — 실패로 끝나면 페처의 지역변수에 쌓인
                # 바이트가 예외와 함께 사라진다 (D-134).
                on_bytes=traffic.add,
            )
        except (RobotsDisallowed, FetchFailed) as error:
            # 원본에 닿지 못했다. 사이트 소유자가 **명시적으로** 거부한 경우가
            # 아니라면(호스트 소멸·네트워크 오류·robots 판정 불능), 다른
            # 호스트인 공개 아카이브를 확인하는 것까지 막을 이유는 없다.
            # 호스트가 통째로 사라지는 것은 링크 부패의 가장 흔한 형태이자
            # 아카이브 구제가 가장 필요한 상황이다 (SPEC §5.2 6단계).
            if not _may_consult_archive(error):
                raise
            recovered = self._recover_from_archive(
                norm_url, document, started, include_content, traffic
            )
            if recovered is not None:
                return recovered
            raise
        traffic.http_status = response.status

        if response.status == 304:
            assert document is not None, "304는 저장된 검증자가 있어야만 온다"
            latest = self._repository.current_version(document.id)
            if latest is None:
                raise FetchFailed("Got 304 but no stored version exists — 304를 받았으나 저장된 버전이 없습니다", http_status=304)
            self._repository.update_document_checked(
                document.id,
                now=utcnow_iso(),
                status="live",
                etag=response.etag or document.etag,
                last_modified=response.last_modified or document.last_modified,
            )
            document = self._repository.get_document_by_any_url(norm_url)
            assert document is not None
            return self._finish(
                document, latest, "not_modified", 304, traffic.bytes_down, started, include_content
            )

        if response.status == 200:
            return self._ingest_200(
                norm_url, document, response, traffic.bytes_down, started, include_content
            )

        # 실패 경로 — 상태를 그대로 기록하고 그대로 보고한다 (SPEC §5.4).
        status_label = _STATUS_BY_HTTP.get(response.status)
        if document and status_label:
            self._repository.set_document_status(document.id, status_label, utcnow_iso())

        # 6단계: GONE 확정 전 아카이브 폴백 (SPEC §5.2). 기본 비활성.
        if response.status in _ARCHIVE_FALLBACK_STATUSES and self._archive.enabled:
            # 조회가 무산돼도 받은 바이트는 sink로 이미 들어왔다 (D-135).
            hit = self._archive.lookup(norm_url, on_bytes=traffic.add)
            if hit is not None:
                result = self._ingest_archive(
                    norm_url,
                    document,
                    hit,
                    status_label,
                    traffic.bytes_down,
                    started,
                    include_content,
                )
                if result is not None:
                    return result

        # 문서가 아직 없어도 실패는 회계에 남긴다 (D-014) — 기록은
        # `_fetch_locked`의 한 곳에서 한다 (D-130·D-133).
        raise FetchFailed(
            f"HTTP {response.status}: {norm_url}", http_status=response.status
        )

    @_foreground
    def cite(self, document_ref: str, quote: str, note: str | None = None) -> CiteResult:
        """인용문에 앵커를 부여한다 (SPEC §7.2). document_ref는 문서 id 또는 URL.

        선택자 생성(§10 예산상 최대 200ms) 사이에 다른 프로세스의 페치가
        병합을 일으켜 문서가 옮겨갈 수 있다 (D-185). 별칭이 새 소속을
        가리키므로 한 번 따라가 재시도한다 — 원래부터 없던 문서라면 같은
        DocumentNotFound가 다시 나온다.
        """
        try:
            return self._cite_once(document_ref, quote, note)
        except DocumentNotFound:
            return self._cite_once(document_ref, quote, note)

    def _cite_once(self, document_ref: str, quote: str, note: str | None) -> CiteResult:
        document = self._resolve_document(document_ref)
        latest = self._repository.current_version(document.id)
        if latest is None:
            raise DocumentNotFound(f"Document has no stored version — 저장된 버전이 없습니다: {document.url}")
        text = self._repository.get_version_text(latest.id)

        selector = build_selector(
            text,
            quote,
            context_chars=self._config.context_chars,
            min_quote_chars=self._config.min_quote_chars,
            short_quote_chars=self._config.short_quote_chars,
        )
        now = utcnow_iso()
        anchor = self._repository.insert_anchor(
            document_id=document.id,
            created_version=latest.id,
            exact=selector.exact,
            prefix=selector.prefix,
            suffix=selector.suffix,
            position_hint=selector.position_hint,
            exact_hash=hash_text(selector.exact),
            quality=selector.quality,
            note=note,
            created_at=now,
        )
        warnings_list: list[str] = []
        if selector.quality == QUALITY_SHORT:
            warnings_list.append(
                f"Quote is under {self._config.short_quote_chars} chars: re-verification accuracy "
                "drops and the time budget is halved; one complete sentence is recommended "
                "— 인용문이 짧아 재검증 정확도가 낮을 수 있고 시간 예산이 절반으로 적용됩니다."
            )
        if selector.occurrences > 1:
            # 앵커는 첫 출현에 붙는다. 인용한 인스턴스가 지워져도 다른
            # 인스턴스 때문에 INTACT로 보일 수 있다 (D-047).
            warnings_list.append(
                f"This quote appears {selector.occurrences}+ times in the document; the anchor "
                "binds to the first occurrence and re-verification may match another one "
                f"— 인용문이 원문에 {selector.occurrences}회 이상 나옵니다. 앵커는 첫 출현에 "
                "묶이므로 재검증이 다른 인스턴스를 잡을 수 있습니다."
            )
        warnings = tuple(warnings_list)
        return CiteResult(
            anchor_id=anchor.id,
            document_id=document.id,
            version_id=latest.id,
            offset=selector.position_hint,
            quality=selector.quality,
            warnings=warnings,
            created_at=now,
        )

    @_foreground
    def verify(
        self,
        *,
        anchor_ids: list[str] | None = None,
        document_ids: list[str] | None = None,
        older_than: str | float | None = None,
        time_budget_ms: float | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> VerifyReport:
        """앵커들을 현재 원문 대비 재검증한다 (SPEC §7.3). 조건이 없으면 전체.

        older_than: ISO 8601 기간 문자열("P7D") 또는 초. 그 안에 검증된
        앵커는 건너뛴다.

        should_stop: 문서·앵커 사이마다 확인하는 중단 신호 (D-119). 참을
        돌려주면 남은 작업을 건드리지 않고 지금까지의 결과만 반환한다 —
        취소와 종료가 실제로 작업을 멈추게 하는 유일한 경로다 (D-034/D-035).
        네트워크 대기(fetch) 안에서는 확인하지 않는다 — 반응 상한은 앵커
        하나의 예산 + 진행 중인 한 문서의 페치 전체(robots·홉·재시도·폴백,
        D-206)다 (D-197).
        """
        if time_budget_ms is not None and time_budget_ms <= 0:
            # 0·음수 예산은 매칭 3·4단계를 조용히 건너뛰어 실제 개정 인용문을
            # ALTERED 대신 UNRESOLVED로 만든다 (D-122). 설정 파일 경로는
            # ConfigError로 거부하면서 런타임 인자만 통과시키면 세 경로 중
            # 하나만 검증하는 셈이다 — 공통 길목인 여기서 거부한다.
            raise ValueError(
                f"time_budget_ms must be positive — 앵커당 시간 예산은 양수여야 "
                f"합니다: {time_budget_ms}"
            )
        if isinstance(older_than, str):
            older_than = parse_iso_duration(older_than)
        if older_than is not None and not (
            math.isfinite(older_than) and older_than >= 0
        ):
            # `nan`·`inf`·음수는 `iso_ago`의 `timedelta`에서 ValueError/
            # OverflowError로 죽는다. CLI만 막으면(D-149) MCP 도구와 라이브러리
            # 직접 호출이 그대로 남는다 — 공통 길목인 여기서도 거부한다.
            raise ValueError(
                f"older_than must be a finite, non-negative number of seconds — "
                f"older_than은 유한한 0 이상의 초여야 합니다: {older_than!r}"
            )
        cutoff = iso_ago(older_than) if older_than is not None else None
        anchors = self._repository.select_anchors(
            anchor_ids=anchor_ids, document_ids=document_ids, not_verified_since=cutoff
        )

        summary = {state: 0 for state in matcher.ALL_STATES}
        attention: list[AttentionItem] = []
        requests = 0
        not_modified = 0
        bytes_down = 0

        by_document: dict[str, list[AnchorRecord]] = {}
        for anchor in anchors:
            by_document.setdefault(anchor.document_id, []).append(anchor)

        stopped_early = False
        for document_id, document_anchors in by_document.items():
            if should_stop is not None and should_stop():
                stopped_early = True
                break
            document = self._repository.get_document(document_id)
            if document is None:
                # 다른 프로세스의 페치가 배치 중간에 병합을 일으켰다 (D-185).
                # merge_document는 앵커를 함께 옮기므로, 다시 조회해 새 소속을
                # 따라간다 — AssertionError로 배치 전체를 죽이지 않는다.
                refreshed = self._repository.select_anchors(
                    anchor_ids=[record.id for record in document_anchors]
                )
                homes = {record.document_id for record in refreshed}
                if len(homes) == 1:
                    document = self._repository.get_document(homes.pop())
                    document_anchors = refreshed
            if document is None:
                # 그래도 못 찾으면 이 묶음만 보류하고 배치는 계속 간다.
                for record in document_anchors:
                    summary[matcher.UNRESOLVED] += 1
                continue

            failure_state: str | None = None
            try:
                fetch_result = self.fetch(document.url, max_age=0, include_content=False)
                requests += 1
                bytes_down += fetch_result.network.bytes_down
                if fetch_result.outcome == "not_modified":
                    not_modified += 1
            except FetchFailed as error:
                requests += 1
                failure_state = (
                    matcher.GONE if error.http_status in (404, 410) else matcher.UNREACHABLE
                )
            except AnchorError:
                # robots 거부, 추출 실패 등 — 확인 불가이지 인용 무효가 아니다.
                requests += 1
                failure_state = matcher.UNREACHABLE

            if failure_state is not None:
                for anchor in document_anchors:
                    self._repository.insert_verification(
                        anchor_id=anchor.id,
                        checked_version=None,
                        checked_at=utcnow_iso(),
                        state=failure_state,
                        match_score=None,
                        edit_distance=None,
                        found_offset=None,
                        found_text=None,
                        elapsed_ms=0,
                    )
                    summary[failure_state] += 1
                    if failure_state == matcher.GONE:
                        attention.append(
                            AttentionItem(
                                anchor_id=anchor.id,
                                url=document.url,
                                state=failure_state,
                                before=anchor.exact,
                                after=None,
                                match_score=None,
                                edit_distance=None,
                            )
                        )
                continue

            # 방금 관측한 버전을 그대로 쓴다. 여기서 latest_version()을 다시
            # 조회하면 아카이브 폴백·본문 되돌림 상황에서 앵커 생성에 쓴 옛
            # 본문과 자기 자신을 대조하게 된다 (D-011).
            latest = self._repository.get_version(fetch_result.version_id)
            assert latest is not None
            text = self._repository.get_version_text(latest.id)

            for anchor in document_anchors:
                if should_stop is not None and should_stop():
                    # 문서 사이에서만 확인하면 취소·종료 반응 시간이 한 문서의
                    # 앵커 수에 비례해 무한정 늘어난다 (D-119, 실측 100앵커에
                    # 13.8초) — 앵커 사이에서도 확인한다.
                    stopped_early = True
                    break
                budget_ms = (
                    time_budget_ms if time_budget_ms is not None else self._config.time_budget_ms
                )
                if anchor.quality == QUALITY_SHORT:
                    budget_ms = budget_ms / 2
                match_started = time.monotonic()
                result = matcher.match_anchor(
                    text,
                    exact=anchor.exact,
                    prefix=anchor.prefix,
                    suffix=anchor.suffix,
                    position_hint=anchor.position_hint,
                    budget_ms=budget_ms,
                    max_edit_ratio=self._config.max_edit_ratio,
                    max_edit_distance=self._config.max_edit_distance,
                    hint_radius=self._config.hint_radius,
                    max_bytes=self._config.max_document_bytes,
                )
                elapsed_ms = int((time.monotonic() - match_started) * 1000)
                self._repository.insert_verification(
                    anchor_id=anchor.id,
                    checked_version=latest.id,
                    checked_at=utcnow_iso(),
                    state=result.state,
                    match_score=result.score,
                    edit_distance=result.edit_distance,
                    found_offset=result.found_offset,
                    found_text=result.found_text,
                    elapsed_ms=elapsed_ms,
                )
                summary[result.state] += 1
                if result.state in (matcher.ALTERED, matcher.MISSING, matcher.UNRESOLVED):
                    attention.append(
                        AttentionItem(
                            anchor_id=anchor.id,
                            url=document.url,
                            state=result.state,
                            before=anchor.exact,
                            after=result.found_text if result.state == matcher.ALTERED else None,
                            match_score=result.score,
                            edit_distance=result.edit_distance,
                            truncated=result.truncated,
                            position_hint=anchor.position_hint,
                            found_offset=result.found_offset,
                        )
                    )
            if stopped_early:
                break

        checked = sum(summary.values())
        return VerifyReport(
            checked=checked,
            summary=summary,
            attention=tuple(attention),
            anchor_ids=tuple(anchor.id for anchor in anchors),
            stopped_early=stopped_early,
            requests=requests,
            not_modified=not_modified,
            bytes_down=bytes_down,
        )

    @_foreground
    def list_documents(
        self,
        *,
        status: str | None = None,
        host: str | None = None,
        has_pending_verification: bool | None = None,
    ) -> list[Document]:
        documents = self._repository.list_documents()
        if status is not None:
            # 열거값 밖 문자열에 빈 목록을 돌려주면 호출자는 "캐시가 비었다"로
            # 읽는다 (D-121) — format 인자들과 같은 방식으로 명확히 거부한다.
            # 대소문자는 뜻이 아니므로 접는다 ("LIVE" → live).
            status = status.lower()
            if status not in _DOCUMENT_STATUSES:
                raise ValueError(
                    f"Unknown status — 지원하지 않는 상태: {status} "
                    f"({' | '.join(_DOCUMENT_STATUSES)})"
                )
            documents = [d for d in documents if d.status == status]
        if host is not None:
            documents = [d for d in documents if httpx.URL(d.url).host == host]
        if has_pending_verification is not None:
            documents = [
                d for d in documents if self._has_pending_verification(d) == has_pending_verification
            ]
        return documents

    @_foreground
    def get_version_text(self, version_id: str) -> str:
        return self._repository.get_version_text(version_id)

    @_foreground
    def get_version(
        self,
        version_id: str | None = None,
        *,
        document_id: str | None = None,
        ref: str = "latest",
    ) -> tuple[Version, str]:
        """버전 메타데이터와 본문. version_id 직접 지정 또는 document_id + ref."""
        if version_id is not None:
            version = self._repository.get_version(version_id)
            if version is None:
                raise DocumentNotFound(f"Version not found — 버전을 찾을 수 없습니다: {version_id}")
        else:
            if document_id is None:
                raise DocumentNotFound("Provide version_id or document_id — version_id 또는 document_id를 지정해야 합니다")
            version = self._resolve_version_ref(document_id, ref)
        return version, self._repository.get_version_text(version.id)

    @_foreground
    def diff_versions(
        self,
        document_ref: str,
        *,
        from_ref: str = "latest~1",
        to_ref: str = "latest",
        context_lines: int = 2,
    ) -> str:
        """두 버전의 본문 차이를 통합 diff로 (SPEC §7.4)."""
        document = self._resolve_document(document_ref)
        from_version = self._resolve_version_ref(document.id, from_ref)
        to_version = self._resolve_version_ref(document.id, to_ref)
        return export_diff.unified_diff(
            from_version,
            self._repository.get_version_text(from_version.id),
            to_version,
            self._repository.get_version_text(to_version.id),
            context_lines=context_lines,
        )

    @_foreground
    def cache_stats(self, *, window_seconds: float = 30 * 86400) -> dict:
        """캐시 회계 (SPEC §7.7). 절감 효과를 사용자가 직접 확인하는 지표."""
        since = iso_ago(window_seconds)
        counts = self._repository.count_rows()
        window = self._repository.fetch_stats_since(since)
        requests = window["requests"]
        cache_hits = window.get("cache_hit", 0)
        not_modified = window.get("not_modified", 0)
        return {
            "documents": counts["documents"],
            "versions": counts["versions"],
            "anchors": counts["anchors"],
            "disk_bytes": self._repository.disk_bytes(),
            "last_30d": {
                "requests": requests,
                "cache_hits": cache_hits,
                "not_modified": not_modified,
                "unchanged": window.get("unchanged", 0),
                "changed": window.get("changed", 0)
                + window.get("created", 0)
                + window.get("renormalized", 0),
                # 아카이브 구제도 표시되는 버킷 하나에 속해야 한다 — 빠지면
                # 내역의 합이 총 요청 수와 맞지 않는다 (D-136, SPEC §7.7).
                "archive": window.get("archive", 0),
                "errors": window.get("error", 0),
                "bytes_down": window["bytes_down"],
                "bytes_saved_estimate": self._repository.bytes_saved_estimate_since(since),
                "hit_rate": round((cache_hits + not_modified) / requests, 4) if requests else 0.0,
            },
        }

    @_foreground
    def get_timemap(self, document_ref: str, *, fmt: str = "link") -> dict:
        """RFC 7089 TimeMap 내보내기 (SPEC §7.8)."""
        document = self._resolve_document(document_ref)
        versions = self._repository.list_versions(document.id)
        if fmt == "link":
            return {
                "content_type": "application/link-format",
                "body": timemap.to_link_format(document, versions),
            }
        if fmt == "json":
            return {
                "content_type": "application/json",
                "body": timemap.to_json_format(document, versions),
            }
        raise ValueError(f"Unsupported format — 지원하지 않는 형식: {fmt} (link | json)")

    @_foreground
    def export_robust_links(
        self, anchor_ids: list[str] | None = None, *, fmt: str = "html"
    ) -> list[dict]:
        """Robust Links 내보내기 (SPEC §7.9). anchor_ids가 None이면 전체 앵커."""
        serializer = robustlinks.SERIALIZERS.get(fmt)
        if serializer is None:
            raise ValueError(f"Unsupported format — 지원하지 않는 형식: {fmt} (html | markdown | bibtex_note)")
        if anchor_ids is None:
            anchors = self._repository.select_anchors()
        else:
            anchors = []
            for anchor_id in anchor_ids:
                anchor = self._repository.get_anchor(anchor_id)
                if anchor is None:
                    raise DocumentNotFound(f"Anchor not found — 앵커를 찾을 수 없습니다: {anchor_id}")
                anchors.append(anchor)
        items: list[dict] = []
        for anchor in anchors:
            document = self._repository.get_document(anchor.document_id)
            version = self._repository.get_version(anchor.created_version)
            assert document is not None and version is not None
            items.append({"anchor_id": anchor.id, fmt: serializer(document, version, anchor)})
        return items

    @_foreground
    def collect_garbage(self, *, keep: int | None = None) -> dict:
        """고아 버전 정리 (SPEC §4.2). 앵커가 가리키는 버전은 절대 삭제하지 않는다."""
        if keep is None:
            keep = self._config.keep_versions
        if keep < 1:
            # 0이나 음수는 "모든 버전 삭제"로 동작해 앵커 없는 문서의 최신본까지
            # 지운다. 어떤 해석으로도 유효하지 않으므로 거부한다 (D-021).
            raise ValueError(
                f"keep must be at least 1, got {keep} — "
                "보존 버전 수는 1 이상이어야 합니다 (0은 전체 삭제입니다)"
            )
        deleted, freed = self._repository.collect_garbage_versions(keep=keep)
        return {"deleted_versions": deleted, "freed_bytes_estimate": freed, "keep": keep}

    def _resolve_version_ref(self, document_id: str, ref: str) -> Version:
        """'latest', 'latest~N' 또는 버전 id를 버전으로 해석한다.

        `latest~N`은 **관측 순서**로 N칸 물러난 판본이다 (D-083). 캡처 시각으로
        물러나면 되돌림에서 일어난 적 없는 전이를 보여준다 — A→B→A→C→A의
        직전 판본은 B가 아니라 C다. 본문 해시로 중복을 제거하면 관측의
        시간축이 접히므로, 그 축을 따로 들고 있어야 답할 수 있다.
        """
        if ref == "latest" or ref.startswith("latest~"):
            back = int(ref[7:]) if ref.startswith("latest~") else 0
            if back == 0:
                # "latest" = 원문이 지금 서빙하는 본문 (캡처 시각 최대값이
                # 아니다 — 되돌림·아카이브에서 갈린다, D-012/D-024).
                current = self._repository.current_version(document_id)
                if current is not None:
                    return current
            versions = self._repository.list_versions_by_observation(document_id)
            if not versions or back >= len(versions):
                raise DocumentNotFound(
                    f"Cannot resolve version ref {ref!r} ({len(versions)} versions stored) — 버전 참조 해석 불가"
                )
            return versions[back]
        version = self._repository.get_version(ref)
        if version is None or version.document_id != document_id:
            raise DocumentNotFound(f"Version not found — 버전을 찾을 수 없습니다: {ref}")
        return version

    def _has_pending_verification(self, document: Document) -> bool:
        """현재 본문에 대해 아직 검증되지 않은 앵커가 있는가.

        기준은 **어느 버전을 검증했는가**이지 시각이 아니다 (D-084). 되돌림은
        옛 행을 재사용하고 아카이브는 과거 Memento 시각을 쓰므로, 시각으로
        재면 현재 본문이 방금 바뀌었는데도 "검증할 것 없음"이 나온다 — 그
        필터로 대상을 좁히는 워크플로는 판정이 뒤집힌 문서를 영영 다시
        보지 않는다.
        """
        latest = self._repository.current_version(document.id)
        if latest is None:
            return False
        for anchor in self._repository.select_anchors(document_ids=[document.id]):
            checked = self._repository.latest_verified_version(anchor.id)
            if checked is None or checked[1] != latest.id:
                return True
        return False

    def _resolve_document(self, document_ref: str) -> Document:
        if document_ref.startswith(("http://", "https://")):
            document = self._repository.get_document_by_any_url(normalize_url(document_ref))
        else:
            document = self._repository.get_document(document_ref)
        if document is None:
            raise DocumentNotFound(
                f"Document not found; fetch it first — 문서를 찾을 수 없습니다 (먼저 fetch 필요): {document_ref}"
            )
        return document

    # -- internals ---------------------------------------------------------

    def _ingest_200(
        self,
        norm_url: str,
        document: Document | None,
        response: FetchResponse,
        bytes_down: int,
        started: float,
        include_content: bool,
    ) -> FetchResult:
        now = utcnow_iso()
        normalized = extract.to_normalized(response.content, response.content_type)
        raw_hash = hash_bytes(response.content)
        text_hash = hash_text(normalized.text)

        # 리다이렉트를 따라갔다면 문서는 정규화된 목적지 URL로 귀속된다 —
        # 단 **영구 리다이렉트일 때만**이다. 302·307은 "지금만 다른 곳을
        # 보라"는 뜻이라 정본 URL을 바꿀 근거가 아니다. 동의 장벽·지역 게이트를
        # 거치는 구성에서 인터스티셜 URL이 Memento의 URI-R로 굳고 장벽이
        # 사라진 뒤에도 되돌아오지 않는다 (D-102).
        final_url = normalize_url(response.final_url)
        moved = final_url != norm_url and response.permanent_redirect
        canonical_url = final_url if moved else norm_url

        if document is not None and final_url == norm_url and document.url != norm_url:
            # 옛 별칭이 리다이렉트를 멈추고 **자기 콘텐츠**를 서빙하기
            # 시작했다(리다이렉트 없이 200). 더는 같은 리소스가 아니므로,
            # 목적지 문서의 이력에 다른 리소스의 본문을 꽂아서는 안 된다
            # (D-099). 판정은 `final_url == norm_url`이다 — `not moved`로
            # 재면 **일시** 리다이렉트로 도달한 별칭(여전히 리다이렉트 중)이
            # 여기 걸려, 별칭이 파괴되고 목적지의 본문을 담은 유령 문서가
            # 생긴다 (D-183).
            self._repository.remove_alias(norm_url)
            document = None
        elif moved:
            landed = self._repository.get_document_by_any_url(final_url)
            if landed is not None and document is not None and landed.id != document.id:
                # 각각 등록돼 있던 두 문서가 영구 리다이렉트로 하나가 됐다.
                # 합치지 않으면 A의 앵커가 B의 본문과 대조되면서 검증 기록에는
                # 다른 문서의 버전 id가 남는다 (D-103).
                self._repository.merge_document(document.id, landed.id)
                document = landed
            elif landed is None and document is not None and document.url != final_url:
                # 등록된 문서가 이사했다 — 정본 URL을 옮기고 옛 주소는
                # 별칭으로 남긴다 (D-194). 그대로 두면 documents.url이 옛
                # 주소에 남아 "영구만 정본을 바꾼다"(§5.1)가 신규 문서에서만
                # 성립하고, 자기 자신을 가리키는 별칭이 생긴다.
                old_url = document.url
                if self._repository.rename_document_url(document.id, final_url):
                    self._repository.add_alias(old_url, document.id)
                    document = self._repository.get_document(document.id)
                else:
                    # 그 사이 다른 호출이 목적지 문서를 만들었다 — 병합으로 수렴
                    landed = self._repository.get_document_by_any_url(final_url)
                    if landed is not None and landed.id != document.id:
                        self._repository.merge_document(document.id, landed.id)
                        document = landed
            else:
                document = landed or document

        if document is None:
            document = self._repository.create_document(
                url=canonical_url,
                original_url=norm_url,
                title=normalized.title,
                now=now,
                etag=response.etag,
                last_modified=response.last_modified,
            )
            version = self._insert_version(document, response, raw_hash, text_hash, normalized, now)
            if moved:
                # 사용자가 넘긴 URL로도 이 문서를 찾을 수 있어야 한다 (D-007).
                self._repository.add_alias(norm_url, document.id)
            return self._finish(
                document, version, "created", 200, bytes_down, started, include_content
            )

        # 직전 관측본과 비교한다 — 캡처 시각 최대값이 아니라 "원문이 지금까지
        # 서빙하던 본문"이 비교 기준이다 (되돌림 시 갈린다, D-012/D-024).
        latest = self._repository.current_version(document.id)
        self._repository.update_document_checked(
            document.id,
            now=now,
            status="live",
            etag=response.etag,
            last_modified=response.last_modified,
            title=normalized.title,
        )
        if moved:
            self._repository.add_alias(norm_url, document.id)
        refreshed = self._repository.get_document_by_url(document.url)
        assert refreshed is not None
        document = refreshed

        if latest is None:
            outcome = "created"
            version = self._insert_version(document, response, raw_hash, text_hash, normalized, now)
        elif text_hash == latest.text_hash and latest.source == "live":
            outcome = "unchanged"
            version = latest
        elif text_hash == latest.text_hash:
            # 본문은 같지만 지금 가리키는 것은 **아카이브 판본**이다. 그대로
            # 재사용하면 살아 있는 원문의 인용에 아카이브 URI-M과 과거 날짜가
            # 달린다 — 출처가 사실과 달라진다 (D-087).
            outcome = "changed"
            version = self._insert_or_reuse(
                document, response, raw_hash, text_hash, normalized, now
            )
        elif (
            raw_hash == latest.raw_hash
            and normalized.pipeline_version != latest.pipeline_version
        ):
            # 원문 바이트는 그대로인데 파이프라인이 바뀌어 본문 표현만 달라졌다.
            outcome = "renormalized"
            version = self._insert_or_reuse(document, response, raw_hash, text_hash, normalized, now)
        else:
            outcome = "changed"
            version = self._insert_or_reuse(document, response, raw_hash, text_hash, normalized, now)

        return self._finish(
            document, version, outcome, 200, bytes_down, started, include_content
        )

    def _recover_from_archive(
        self,
        norm_url: str,
        document: Document | None,
        started: float,
        include_content: bool,
        traffic: _Traffic,
    ) -> FetchResult | None:
        """원본에 닿지 못했을 때 아카이브에서 되살린다. 실패하면 None."""
        if not self._archive.enabled:
            return None
        hit = self._archive.lookup(norm_url, on_bytes=traffic.add)
        if hit is None:
            return None
        status_label = document.status if document else "gone"
        return self._ingest_archive(
            norm_url, document, hit, status_label, traffic.bytes_down, started, include_content
        )

    def _ingest_archive(
        self,
        norm_url: str,
        document: Document | None,
        hit: ArchiveHit,
        status_label: str | None,
        bytes_down: int,
        started: float,
        include_content: bool,
    ) -> FetchResult | None:
        """아카이브 본문을 source='archive' 버전으로 저장한다.

        실패하면 None — 폴백의 실패가 원래의 실패 보고를 가리면 안 된다.
        """
        try:
            normalized = extract.to_normalized(hit.content, hit.content_type)
        except AnchorError:
            return None

        now = utcnow_iso()
        text_hash = hash_text(normalized.text)

        if document is None:
            document = self._repository.create_document(
                url=norm_url,
                original_url=norm_url,
                title=normalized.title,
                now=now,
                status=status_label or "gone",
            )
        else:
            self._repository.set_document_status(
                document.id, status_label or document.status, now
            )
            # 원본의 검증자를 버린다 (D-087). 남겨 두면 원본이 되살아났을 때
            # 그 etag로 조건부 GET을 보내고, 원본이 정직하게 준 304를 Anchor가
            # "변한 것 없음"으로 읽어 아카이브 본문을 계속 현재 본문으로
            # 보고한다 — `force_refresh`로도 벗어나지 못한다.
            self._repository.clear_validators(document.id)

        # 본문이 같아도 아카이브 관측은 별개의 memento다 — 시각도 URI-M도
        # 다르다. 기존 live 행을 재사용하면 "아카이브에서 확인됨"이라는
        # 표시가 통째로 사라진다 (D-013). 출처까지 같을 때만 재사용한다.
        # 찾기와 가리키기를 한 트랜잭션으로 묶는다 — 되돌림 재사용과 같은
        # 모양의 틈이 여기에도 있었다 (D-177 → D-182).
        version = self._repository.reuse_and_point(document.id, text_hash, "archive")
        if version is None:
            version = self._repository.insert_version(
                document_id=document.id,
                text_hash=text_hash,
                raw_hash=hash_bytes(hit.content),
                pipeline_version=normalized.pipeline_version,
                captured_at=hit.memento_datetime,  # Memento-Datetime (SPEC §2)
                byte_size=len(hit.content),
                normalized_text=normalized.text,
                http_status=200,
                source="archive",
                source_uri=hit.uri_m,
                observe=True,
            )
        refreshed = self._repository.get_document(document.id)
        assert refreshed is not None
        return self._finish(
            refreshed, version, "archive", 200, bytes_down, started, include_content
        )

    def _insert_or_reuse(
        self,
        document: Document,
        response: FetchResponse,
        raw_hash: str,
        text_hash: str,
        normalized: extract.NormalizedDoc,
        now: str,
    ) -> Version:
        # 과거 버전과 동일한 본문으로 되돌아온 경우 UNIQUE(document_id, text_hash)에
        # 걸리므로 기존 버전을 재사용한다. 찾기와 가리키기를 **한 트랜잭션**으로
        # 묶어, 그 사이 gc가 지워도 맨 예외가 새어 나가지 않게 한다 (D-177).
        existing = self._repository.reuse_and_point(document.id, text_hash)
        if existing is not None:
            return existing
        return self._insert_version(document, response, raw_hash, text_hash, normalized, now)

    def _insert_version(
        self,
        document: Document,
        response: FetchResponse,
        raw_hash: str,
        text_hash: str,
        normalized: extract.NormalizedDoc,
        now: str,
    ) -> Version:
        return self._repository.insert_version(
            document_id=document.id,
            text_hash=text_hash,
            raw_hash=raw_hash,
            pipeline_version=normalized.pipeline_version,
            captured_at=now,
            byte_size=len(response.content),
            normalized_text=normalized.text,
            http_status=response.status,
            observe=True,
        )

    def _finish(
        self,
        document: Document,
        version: Version,
        outcome: str,
        http_status: int | None,
        bytes_down: int,
        started: float,
        include_content: bool,
    ) -> FetchResult:
        # 원문을 실제로 관측한 결과라면 관측을 남긴다 — 포인터와 관측 시각.
        # cache_hit은 관측이 아니므로 건드리지 않는다 (D-011/D-012/D-024).
        # 포인터가 그대로여도(같은 본문 재관측) 시각은 갱신한다: 되돌림에서
        # "직전에 서빙되던 판본"을 이 시간축으로만 알 수 있다 (D-083).
        if outcome != "cache_hit":
            self._repository.observe_version(document.id, version.id, utcnow_iso())
        # 본문을 **기록 전에** 꺼낸다. gc가 방금 그 버전을 지우는 창에서
        # 여기가 `DocumentNotFound`를 던지면, 성공 1행을 남긴 채 실패 경로의
        # 회계가 한 행 더 붙는다 — "호출 1회 = 1행"이 깨진다 (D-130·D-133).
        content = self._repository.get_version_text(version.id) if include_content else None
        elapsed_ms = self._log(document.id, outcome, http_status, bytes_down, started)
        return FetchResult(
            document_id=document.id,
            version_id=version.id,
            url=document.url,
            title=document.title,
            outcome=outcome,
            captured_at=version.captured_at,
            text_hash=version.text_hash,
            char_count=version.char_count,
            source=version.source,
            network=Network(bytes_down=bytes_down, elapsed_ms=elapsed_ms),
            content=content,
        )

    def _log(
        self,
        document_id: str,
        outcome: str,
        http_status: int | None,
        bytes_down: int,
        started: float,
    ) -> int:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        self._repository.log_fetch(
            document_id=document_id,
            requested_at=utcnow_iso(),
            outcome=outcome,
            http_status=http_status,
            bytes_down=bytes_down,
            elapsed_ms=elapsed_ms,
        )
        return elapsed_ms
