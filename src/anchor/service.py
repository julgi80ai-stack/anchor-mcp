# SPDX-License-Identifier: Apache-2.0
"""공개 파사드. 페치 파이프라인(SPEC §5)의 판정 로직이 여기에 있다.

v0.1 완료 기준: 같은 URL 두 번 호출 시 두 번째가 네트워크 0바이트.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import TracebackType

import httpx

from anchor.anchoring import matcher
from anchor.anchoring.selector import QUALITY_SHORT, build_selector
from anchor.config import Config, load_config
from anchor.errors import AnchorError, DocumentNotFound, FetchFailed, RobotsDisallowed
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
    utcnow_iso,
)
from anchor.normalize import extract
from anchor.normalize.hashing import hash_bytes, hash_text
from anchor.store.repository import Repository

_STATUS_BY_HTTP = {402: "paywalled", 403: "forbidden", 404: "gone", 410: "gone"}


class Anchor:
    """SQLite 연결과 HTTP 세션을 함께 관리하는 컨텍스트 매니저 (SPEC §8)."""

    def __init__(self, db_path: Path | str | None = None, config: Config | None = None) -> None:
        self._config = config or load_config()
        self._repository = Repository(db_path or self._config.db_path)
        self._client = httpx.Client(
            follow_redirects=True,
            max_redirects=self._config.max_redirects,
            timeout=self._config.timeout_seconds,
        )
        self._fetcher = ConditionalFetcher(
            self._client,
            user_agent=self._config.user_agent,
            max_content_bytes=self._config.max_content_bytes,
            retry_backoff_base=self._config.retry_backoff_base,
        )
        self._robots = RobotsGate(
            self._repository,
            self._client,
            user_agent=self._config.user_agent,
            ttl_seconds=self._config.robots_ttl_seconds,
            respect_robots=self._config.respect_robots,
        )
        self._ratelimit = HostRateLimiter(
            rate=self._config.rate_limit_rps, burst=self._config.rate_limit_burst
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

    def close(self) -> None:
        self._client.close()
        self._repository.close()

    # -- public API --------------------------------------------------------

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
        document = self._repository.get_document_by_url(norm_url)

        # 캐시 조회 — 순수 로컬 경로. 네트워크 요청이 없으므로 robots 판정보다
        # 앞선다 (robots는 "요청해도 되는가"의 규칙이다).
        if document and not force_refresh and max_age > 0:
            latest = self._repository.latest_version(document.id)
            if latest and age_seconds(document.last_checked_at) <= max_age:
                return self._finish(
                    document, latest, "cache_hit", None, 0, started, include_content
                )

        verdict = self._robots.check(norm_url)
        if not verdict.allowed:
            if document:
                self._repository.set_robots_allowed(document.id, False)
                self._log(document.id, "error", None, verdict.bytes_down, started)
            raise RobotsDisallowed(f"robots.txt가 페치를 거부: {norm_url}")

        self._ratelimit.acquire(httpx.URL(norm_url).host or "")
        response = self._fetcher.get(
            norm_url,
            etag=document.etag if document else None,
            last_modified=document.last_modified if document else None,
        )
        bytes_down = verdict.bytes_down + response.bytes_down

        if response.status == 304:
            assert document is not None, "304는 저장된 검증자가 있어야만 온다"
            latest = self._repository.latest_version(document.id)
            if latest is None:
                raise FetchFailed("304를 받았으나 저장된 버전이 없습니다", http_status=304)
            self._repository.update_document_checked(
                document.id,
                now=utcnow_iso(),
                status="live",
                etag=response.etag or document.etag,
                last_modified=response.last_modified or document.last_modified,
            )
            document = self._repository.get_document_by_url(norm_url)
            assert document is not None
            return self._finish(
                document, latest, "not_modified", 304, bytes_down, started, include_content
            )

        if response.status == 200:
            return self._ingest_200(
                norm_url, document, response, bytes_down, started, include_content
            )

        # 실패 경로 — 상태를 그대로 기록하고 그대로 보고한다 (SPEC §5.4).
        status_label = _STATUS_BY_HTTP.get(response.status)
        if document and status_label:
            self._repository.set_document_status(document.id, status_label, utcnow_iso())
        if document:
            self._log(document.id, "error", response.status, bytes_down, started)
        raise FetchFailed(
            f"HTTP {response.status}: {norm_url}", http_status=response.status
        )

    def cite(self, document_ref: str, quote: str, note: str | None = None) -> CiteResult:
        """인용문에 앵커를 부여한다 (SPEC §7.2). document_ref는 문서 id 또는 URL."""
        document = self._resolve_document(document_ref)
        latest = self._repository.latest_version(document.id)
        if latest is None:
            raise DocumentNotFound(f"문서에 저장된 버전이 없습니다: {document.url}")
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
        warnings: tuple[str, ...] = ()
        if selector.quality == QUALITY_SHORT:
            warnings = (
                f"인용문이 {self._config.short_quote_chars}자 미만 — 재검증 정확도가 낮을 수 "
                "있고 시간 예산이 절반으로 적용됩니다. 완결된 문장 하나를 권장합니다.",
            )
        return CiteResult(
            anchor_id=anchor.id,
            document_id=document.id,
            version_id=latest.id,
            offset=selector.position_hint,
            quality=selector.quality,
            warnings=warnings,
            created_at=now,
        )

    def verify(
        self,
        *,
        anchor_ids: list[str] | None = None,
        document_ids: list[str] | None = None,
        older_than_seconds: float | None = None,
        time_budget_ms: float | None = None,
    ) -> VerifyReport:
        """앵커들을 현재 원문 대비 재검증한다 (SPEC §7.3). 조건이 없으면 전체."""
        cutoff = iso_ago(older_than_seconds) if older_than_seconds is not None else None
        anchors = self._repository.select_anchors(
            anchor_ids=anchor_ids, document_ids=document_ids, not_verified_since=cutoff
        )

        summary = {state: 0 for state in matcher.ALL_STATES}
        attention: list[AttentionItem] = []
        requests = 0
        bytes_down = 0

        by_document: dict[str, list[AnchorRecord]] = {}
        for anchor in anchors:
            by_document.setdefault(anchor.document_id, []).append(anchor)

        for document_id, document_anchors in by_document.items():
            document = self._repository.get_document(document_id)
            assert document is not None, "앵커는 문서 없이 존재할 수 없다 (FK)"

            failure_state: str | None = None
            try:
                fetch_result = self.fetch(document.url, max_age=0, include_content=False)
                requests += 1
                bytes_down += fetch_result.network.bytes_down
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

            latest = self._repository.latest_version(document_id)
            assert latest is not None
            text = self._repository.get_version_text(latest.id)

            for anchor in document_anchors:
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
                    max_chars=self._config.max_match_chars,
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
                        )
                    )

        return VerifyReport(
            checked=len(anchors),
            summary=summary,
            attention=tuple(attention),
            requests=requests,
            bytes_down=bytes_down,
        )

    def list_documents(self) -> list[Document]:
        return self._repository.list_documents()

    def get_version_text(self, version_id: str) -> str:
        return self._repository.get_version_text(version_id)

    def _resolve_document(self, document_ref: str) -> Document:
        if document_ref.startswith(("http://", "https://")):
            document = self._repository.get_document_by_url(normalize_url(document_ref))
        else:
            document = self._repository.get_document(document_ref)
        if document is None:
            raise DocumentNotFound(
                f"문서를 찾을 수 없습니다: {document_ref} — 먼저 fetch 하세요"
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

        # 리다이렉트를 따라갔다면 문서는 정규화된 목적지 URL로 귀속된다.
        final_url = normalize_url(response.final_url)
        if final_url != norm_url:
            document = self._repository.get_document_by_url(final_url) or document

        if document is None:
            document = self._repository.create_document(
                url=final_url,
                original_url=norm_url,
                title=normalized.title,
                now=now,
                etag=response.etag,
                last_modified=response.last_modified,
            )
            version = self._insert_version(document, response, raw_hash, text_hash, normalized, now)
            return self._finish(
                document, version, "created", 200, bytes_down, started, include_content
            )

        latest = self._repository.latest_version(document.id)
        self._repository.update_document_checked(
            document.id,
            now=now,
            status="live",
            etag=response.etag,
            last_modified=response.last_modified,
            title=normalized.title,
        )
        refreshed = self._repository.get_document_by_url(document.url)
        assert refreshed is not None
        document = refreshed

        if latest is None:
            outcome = "created"
            version = self._insert_version(document, response, raw_hash, text_hash, normalized, now)
        elif text_hash == latest.text_hash:
            outcome = "unchanged"
            version = latest
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
        # 걸리므로 기존 버전을 재사용한다.
        existing = self._repository.find_version_by_text_hash(document.id, text_hash)
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
            content=self._repository.get_version_text(version.id) if include_content else None,
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
