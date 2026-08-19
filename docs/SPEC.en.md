# Anchor — A Provenance-Tracking Fetch Cache (EN)

**Technical Specification v1.10 (as of completion)**

> **Translation note**: This is an English translation of `SPEC.md`. The Korean
> version is normative — if the two ever disagree, the Korean text governs.
> Section numbers match the Korean version so cross-references work in both.

| Item | Content |
|---|---|
| Project name | Anchor (`anchor-mcp`) |
| One-line definition | A local-first cache layer that tracks the **version, provenance, and citation validity** of web documents retrieved by AI agents |
| Lineage | **A local client implementation of Memento (RFC 7089)** + W3C Web Annotation Selector |
| Distribution form | MCP server (stdio / Streamable HTTP) + Python library |
| License | Apache-2.0 (third-party component ledger: `THIRD-PARTY.md`) |
| Storage | A single SQLite file (no dependency on external services) |
| Minimum requirement | Python 3.11+ |
| Governing specs | RFC 7089, RFC 9110 (conditional requests), W3C Web Annotation Data Model, MCP 2026-07-28 |
| Design rationale | `docs/decisions/0001` (prior art and positioning), `docs/decisions/0002` (licensing and reuse) |

> **v1.9 → v1.10 change summary**: Reflects stage 6 (accounting, configuration, CLI — clusters 9–11) of the remediation. Two accounting invariants were established — one user call = one `fetch_log` row whose outcome is the final result, and every byte actually downloaded is counted even when the call ends in failure (§7.7). `bytes_saved_estimate` was corrected to measure against the currently served body, `disk_bytes` was defined as actual occupancy after WAL reclamation, and an `archive` bucket was added so the breakdown sums to the total again (§7.7, §13). The contention gate's verdict was replaced: instead of the UNRESOLVED increment over the foreground baseline it now gates **credit actually granted** (§10). Configuration opens all 26 documented keys through all three paths (TOML, environment, library) with an authoritative validation table, and unknown keys are warned about and ignored (§9). `max_document_bytes` is measured in UTF-8 bytes as its name says, and k is clamped below the quote length (§6.2). Every CLI command carries a traceback-free error surface, and store-open failures are domain-typed as `StorageError` (§8). The per-URL lock became genuinely per-URL rather than an approximation, and a `close()` contract was established (§10, §8). The archive CDX `statuscode` is re-checked and the attribution, reason, and recovery of robots records were corrected (§5.2); `cite` and `verify_citations` disclose provenance (§7.2, §7.3); `tasks/list` gained cursor pagination (§7.0); and the forms of version references and diff arguments were settled (§7.4). See §15 for the full list.
>
> **v1.8 → v1.9 change summary**: Reflects stage 5 of the remediation (Tasks and the server, cluster 8). A Task **ttl policy** was established (a call that omits ttl gets a server default of 30 minutes; zero and negative values are rejected; oversized requests are clamped to 24 hours with the actual value reported; the ttl key is never omitted from any response — §7.0); the retention period is counted from creation as the protocol defines, but is updated at termination to the actual retention so a long-running task's result does not vanish the moment it finishes (§7.0); server shutdown now **actually guarantees worker termination** before the store is closed (§7.0); and cancellation reacts at anchor granularity (§7.0). The cache-hit gate was strengthened to hold **while background matching is running**, with a loaded scenario added to the benchmark (§10). `list_documents`' status and `verify_citations`' time_budget_ms are validated once for all three call paths (§7.6, §7.3). See §16 for the full list.
>
> **v1.7 → v1.8 change summary**: Reflects stage 4 of the remediation (the honest-client contract, cluster 2) plus the audit of that stage's own remediation code. robots.txt redirects are now followed, a leading BOM is ignored, and the size cap, timeout, and declared charset apply to robots.txt as well (§5.2, §5.4); a blank User-Agent is rejected at configuration time and validation moved to `Config` construction over the final merged state (§5.4, §9); the URI-M an aggregator points at also gets a robots verdict (§5.2). **So that redirects cannot corrupt a document's identity**, permanent and temporary redirects are distinguished, rules were established for alias retirement, document merging (earliest-capture keeper, observation renumbering, accounting transfer), registered-document moves, and idempotent creation (§5.1); validators ride only on the canonical hop, compared in normalized form, and `https → http` downgrades and unfollowable `Location` values are refused (§5.4, §5.2). URL normalization gained RFC 3986 equivalence and input validation with a domain exception (§5.1). See §17 for the full list.
>
> **v1.6 → v1.7 change summary**: Reflects stage 3 of the remediation (judgment accuracy, cluster 5). The approximate search now takes **every position within distance k** as a candidate per core (§6.2); when stage 3 hits its candidate cap it defers to stage 4 instead of committing (§6.2); stage 4 results pass a **context-corroboration gate** so a sibling paragraph from another section is never presented as "the current form of your quote" (§6.2); the writing-system factor on the edit-distance cap became a continuous function (§6.2); the budget check period is measured in DP cells (§6.2); and truncation now holds `ALTERED` back too (§6.3). The effective document-length limit of stage 4 was made explicit (§6.2, §10). **The schema was raised to v6 to introduce the observation timeline** (§4.1) and `latest~N` was defined on that axis (§7.4). See §18 for the full list.
>
> **v1.5 → v1.6 change summary**: Reflects stage 1 (normalization) of the remediation for the 101 defects demonstrated in the second parallel audit. **The normalization rules were redesigned and NORM_VERSION was raised to 3** (§5.3) — lines and blocks are recognized first, and rules that delete anything apply only inside prose lines. Quote lookup was made tolerant of block separators (§6.1), the golden corpus is now required to actually exercise the normalization rules (§12), and migrating an older database *that contains rows* was made an explicit test target (§12). See §19 for the full list.
>
> **v1.4 → v1.5 change summary**: Reflects the remediation of 52 defects demonstrated in a parallel audit. The schema was raised to v5 to introduce a "current live version" pointer, redirect aliases, and per-source version uniqueness (§4.1); the normalization rules were revised so that quotes copied off the screen actually resolve (§5.3, NORM_VERSION 2); robots is now evaluated at every redirect hop and a robots 5xx became a denial (§5.2); anchor thresholds and the edit-distance ratio now account for the writing system (§6.1, §6.2); the budget is enforced inside the matching stages (§6.2); and the global lock was narrowed to per-URL scope (§10). See §20 for the full list.
>
> **v1.3 → v1.4 change summary**: Cleanup at the v1.0 release point. Configuration loading was settled on the standard library and `pydantic-settings` was removed from the dependencies (§9, §11); the conditions for optionally running the anchor benchmark were made explicit (§12); and the development-dependency policy was delegated to the ledger (§11.2). See §21 for the full list.
>
> **v1.2 → v1.3 change summary**: Reflects what was settled during the v0.1–v0.4 implementation. The `versions.pipeline_version` column and the `renormalized` and `unchanged` outcomes were formalized (§4.1, §5.2, §5.3, §7.1); the tracking-parameter removal list in URL normalization was narrowed (§5.1); robots cache persistence and the request order were clarified (§4.1, §5.2); `mcp-server-fetch`-compatible chunked reading was added (§7.1); and the SDK constraint on the Tasks wire format was recorded (§7.0). See §22 for the full list.
>
> **v1.1 → v1.2 change summary**: Reflects the results of the license audit. The `trafilatura>=1.8.0` lower bound was made mandatory (§11); MemGator operating guidance was made explicit (§5.2, §9); the provenance policy for test fixtures was split into three grades (§12); and a license gate was added to CI (§12). See §23 for the full list.
>
> **v1.0 → v1.1 change summary**: Reflects the results of the prior-art survey by introducing Memento compatibility (§2, §5.2, §7.8), replacing the anchor matching algorithm with a performance-safe approach (§6.2), expanding the verification states to seven (§6.3), and adopting the Tasks extension of the latest MCP spec (§7.0). See §24 for the full list.

---

## 1. Problem Definition

### 1.1 Background

When AI agents use the web, three kinds of waste and one trust deficit recur.

1. **Re-fetch waste** — Unchanged pages are fetched again every time. By Cloudflare's estimate, more than half of AI crawler traffic goes here.
2. **Token waste** — The same document is re-parsed and re-inserted into the context every session.
3. **Normalization waste** — Ads, timestamps, and A/B tests make the bytes differ every time while the content is identical.
4. **reference rot** — There is no way to know when a sentence cited in a report three weeks ago has disappeared from the source.

`reference rot` is the sum of two phenomena (following Klein & Van de Sompel's definition).

- **link rot** — the resource disappears
- **citation drift** — the resource is still alive but its content has changed

The second is more dangerous. Because nothing breaks, it goes undetected. A survey of scholarly literature measured that roughly 75% of referenced web content had changed to some degree within three years.

> **Terminology caution**: In the software ecosystem, "content drift" overwhelmingly means concept drift in machine learning. This project's documentation, search keywords, and tags use **reference rot / citation drift** as the primary terms.

### 1.2 Goals

- **Eliminate unnecessary fetching and re-parsing** through conditional requests (ETag / Last-Modified) and content hashes.
- Assign a **stable anchor** at the granularity of a quote, and **re-verify** it against the source at any point in time.
- Preserve the **version history** of a document so the source as it stood at citation time can always be recovered.
- Expose the above as **MCP tools** so they work neutrally across models and frameworks.
- **Interoperate with existing standards.** Export as TimeMap, cite with Robust Links, express anchors with Web Annotation Selectors.

### 1.3 Non-Goals

What is explicitly not done. When requests to expand scope arrive, this section is the grounds for declining.

- Crawling and search engines (the caller supplies the URL)
- Browser automation, login, anti-bot evasion
- Embeddings, vector search, RAG pipelines
- LLM calls (Anchor does not call a model)
- **Determining semantic change** — whether an `ALTERED` is a substantive change of meaning or a typo fix is judged by the caller (the agent). Anchor reports only facts
- Distributed servers, multi-tenancy, account systems

> **Design principle**: There must be a benefit even with a single user. Features that depend on network effects are not put into v1.0.

### 1.4 Prior Art and Inheritance

Anchor does not invent a new layer. It assembles existing, scattered results into a single local tool an agent can use.

| What is inherited | Source | Where it lives in Anchor |
|---|---|---|
| A time-dimensional HTTP access model | **Memento — RFC 7089** (Van de Sompel, Nelson, Sanderson) | §2 Terminology, §7.8 TimeMap |
| A data model for quote selectors | **W3C Web Annotation Data Model** | §6.1 Anchor |
| A multi-stage anchor re-attachment strategy | **Hypothesis** anchoring stack | §6.2 Matching |
| Approximate string matching | Myers bit-vector algorithm, the `approx-string-match` family | §6.2 stage 4 |
| A citation notation convention | **Robust Links Specification** | §7.9 Export |
| Empirical demonstration of the problem | Klein et al. (2014), Jones et al. (2016), PLOS ONE | §1.1 |

The LANL Time Travel service that operated Memento shut down at the end of 2025, and mementoweb.org became a static site holding only archived material. **Anchor's local-first design is a direct answer to that failure mode.** With no central service, there is no service to be shut down.

There are only four things Anchor actually builds new.

1. Assembling the pieces above into a single local tool an agent can use
2. Automatic noise removal via the `raw_hash` / `text_hash` dual hash
3. Seven verification state codes — in particular, separating `ALTERED` from `MISSING`
4. The MCP interface

---

## 2. Terminology

Memento (RFC 7089) terms are given alongside. Anchor's local concepts map 1:1 onto Memento's, which is the basis for the §7.8 TimeMap export.

| Anchor term | Memento counterpart | Definition |
|---|---|---|
| **Document** | Original Resource (URI-R) | The logical object corresponding to one URL. It has multiple Versions |
| **Version** | Memento (URI-M) | A content snapshot at a particular point in time. Identified by `text_hash` |
| **Version list** | TimeMap (URI-T) | An enumeration of all Versions of one Document together with their capture times |
| `captured_at` | Memento-Datetime | The time at which that version was **first** obtained |
| **Anchor** | — (W3C Annotation Selector) | A position descriptor for finding one quote again inside the source |
| **Verification** | — | A record of the result of re-verifying a particular Anchor against a particular Version |
| **Normalized text** | — | The string obtained by extracting only the main content from HTML, converting it to markdown, and normalizing whitespace and Unicode. The basis of every hash and anchor |

Anchor does not implement a TimeGate. Because it is a local store, it offers direct lookup (§7.6 `get_version`) instead of datetime negotiation. The TimeMap it exports, however, follows RFC 7089 serialization, so external Memento clients can read it.

---

## 3. Architecture

```
┌────────────────────────────────────────────────────────────┐
│ MCP Client (Claude / GPT / open models / CLI)              │
└───────────────────┬────────────────────────────────────────┘
                    │ MCP 2026-07-28 (stdio | streamable-http)
┌───────────────────▼────────────────────────────────────────┐
│                          Anchor                            │
│                                                            │
│  ┌────────────────┐  ┌────────────────┐  ┌──────────────┐  │
│  │    Fetcher     │→ │   Normalizer   │→ │    Anchor    │  │
│  │                │  │                │  │    Engine    │  │
│  │ · robots       │  │ · extraction   │  │              │  │
│  │ · conditional  │  │ · md convert   │  │ · create     │  │
│  │ · rate limit   │  │ · normalize    │  │ · re-verify  │  │
│  │ · archive      │  │                │  │ · budget     │  │
│  │   fallback     │  │                │  │              │  │
│  └───────┬────────┘  └───────┬────────┘  └──────┬───────┘  │
│          └───────────────────┴──────────────────┘          │
│                              │                             │
│                     ┌────────▼────────┐                    │
│                     │ Store (SQLite)  │                    │
│                     │ + blob (zstd)   │                    │
│                     └─────────────────┘                    │
│                              │                             │
│                     ┌────────▼────────┐                    │
│                     │   Exporters     │                    │
│                     │ TimeMap│Robust  │                    │
│                     └─────────────────┘                    │
└────────────────────────────────────────────────────────────┘
                              │ HTTPS
              ┌───────────────┴───────────────┐
      ┌───────▼────┐                ┌─────────▼──────────┐
      │  Internet  │                │ Memento aggregator │
      │            │                │ (optional)         │
      └────────────┘                └────────────────────┘
```

### 3.1 Component Responsibilities

| Component | Responsibility | What it does not do |
|---|---|---|
| `Fetcher` | HTTP acquisition, robots compliance, rate limiting, conditional requests, archive fallback | Parsing |
| `Normalizer` | Content extraction, markdown conversion, normalization, hashing | Network |
| `AnchorEngine` | Anchor creation, matching, state determination, time budget management | Storage, semantic determination |
| `Store` | Persistence, version management, compression | Business logic |
| `Exporters` | TimeMap, Robust Links, unified diff serialization | Data generation |
| `Server` | MCP tool exposure, input validation, Task lifecycle | Reimplementing the logic above |

---

## 4. Data Model

### 4.1 Schema

```sql
-- Logical document (Memento: Original Resource / URI-R). Unique after URL normalization.
CREATE TABLE documents (
    id              TEXT PRIMARY KEY,          -- uuid7
    url             TEXT NOT NULL UNIQUE,      -- normalized URL
    original_url    TEXT NOT NULL,             -- the original, before redirects
    title           TEXT,
    first_seen_at   TEXT NOT NULL,             -- ISO 8601 UTC
    last_checked_at TEXT NOT NULL,
    status          TEXT NOT NULL,             -- live | gone | forbidden | paywalled
    etag            TEXT,
    last_modified   TEXT,
    robots_allowed  INTEGER NOT NULL DEFAULT 1,
    -- The version of the content the source is serving **right now** (v1.5). It may
    -- differ from the maximum captured_at: an archive fallback inserts a snapshot
    -- with a past timestamp, and when the content reverts to an earlier value the
    -- old version row is reused. Treating the two as the same makes re-verification
    -- compare "the content the anchor was created from" against itself.
    current_version TEXT REFERENCES versions(id)
);

-- Pre-redirect URL → document (v1.5). A document is stored under its final URL while
-- lookups use the URL the user passed, so without this table the cache misses forever
-- on every URL that redirects (which also defeats conditional requests). The
-- "defer to the redirect response" of §5.1 step 5 is implemented by this table.
CREATE TABLE document_aliases (
    url         TEXT PRIMARY KEY,   -- normalized input URL
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE
);

-- Content snapshot (Memento: URI-M). If text_hash is the same, no new version is created.
CREATE TABLE versions (
    id               TEXT PRIMARY KEY,
    document_id      TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    text_hash        TEXT NOT NULL,               -- blake3(normalized_text)
    raw_hash         TEXT NOT NULL,               -- blake3(raw bytes)
    pipeline_version TEXT NOT NULL,               -- extractor + normalization rule version (v1.3, §5.3)
    captured_at      TEXT NOT NULL,               -- corresponds to Memento-Datetime
    byte_size        INTEGER NOT NULL,
    char_count       INTEGER NOT NULL,
    content_blob     BLOB NOT NULL,               -- zstd(normalized_text)
    http_status      INTEGER NOT NULL,
    source           TEXT NOT NULL DEFAULT 'live',-- live | archive
    source_uri       TEXT,                        -- the URI-M, if it came from an archive
    -- When this body was **last observed** at the origin, and in what order (v1.7).
    -- Deduplicating by text_hash folds the observation timeline — in A→B→A, A is
    -- one row, so "the edition served just before this one" cannot be answered
    -- from captured_at. captured_at is the Memento-Datetime and must not change,
    -- so the last observation is kept separately. The timestamp is a fact for
    -- humans; the sequence number is an ordering for machines: using the clock
    -- as the order fails to separate two observations within the same second,
    -- and a clock that steps backwards would invert the order.
    last_observed_at  TEXT NOT NULL,
    last_observed_seq INTEGER NOT NULL,
    -- Even with identical content, a different source is a separate memento (v1.5).
    -- Reusing an existing live row just because content recovered from an archive
    -- matches it wipes out source, source_uri, and Memento-Datetime entirely.
    UNIQUE (document_id, text_hash, source)
);

-- Per-quote anchor (W3C Web Annotation Selector).
CREATE TABLE anchors (
    id                TEXT PRIMARY KEY,
    document_id       TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    created_version   TEXT NOT NULL REFERENCES versions(id),
    exact             TEXT NOT NULL,           -- the quote itself
    prefix            TEXT NOT NULL,           -- 48 chars of leading context
    suffix            TEXT NOT NULL,           -- 48 chars of trailing context
    position_hint     INTEGER NOT NULL,        -- character offset at creation time
    exact_hash        TEXT NOT NULL,
    quality           TEXT NOT NULL,           -- ok | short (under 32 chars)
    note              TEXT,                    -- user memo (optional)
    created_at        TEXT NOT NULL
);

-- Re-verification history.
CREATE TABLE verifications (
    id                TEXT PRIMARY KEY,
    anchor_id         TEXT NOT NULL REFERENCES anchors(id) ON DELETE CASCADE,
    checked_version   TEXT REFERENCES versions(id),
    checked_at        TEXT NOT NULL,
    state             TEXT NOT NULL,           -- see §6.3 (7 states)
    match_score       REAL,                    -- 0.0 ~ 1.0
    edit_distance     INTEGER,                 -- the actual edit distance
    found_offset      INTEGER,
    found_text        TEXT,                    -- the string actually found, if altered
    elapsed_ms        INTEGER NOT NULL
);

-- Network accounting. For measuring the savings.
CREATE TABLE fetch_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id   TEXT NOT NULL,
    requested_at  TEXT NOT NULL,
    outcome       TEXT NOT NULL,  -- cache_hit | not_modified | unchanged | changed
                                  -- | renormalized | created | archive | error (v1.3, §5.2)
    http_status   INTEGER,
    bytes_down    INTEGER NOT NULL DEFAULT 0,
    elapsed_ms    INTEGER NOT NULL
);

-- robots.txt cache (24h per host origin). Every CLI invocation is a new process, so
-- without persistence "zero network bytes on the second call" cannot hold (v1.3).
CREATE TABLE robots_cache (
    origin       TEXT PRIMARY KEY,   -- e.g. https://example.com
    body         TEXT NOT NULL,
    fetch_status INTEGER NOT NULL,
    fetched_at   TEXT NOT NULL
);

CREATE INDEX idx_versions_doc      ON versions(document_id, captured_at DESC);
CREATE INDEX idx_versions_observed ON versions(document_id, last_observed_seq DESC);
CREATE INDEX idx_anchors_doc       ON anchors(document_id);
CREATE INDEX idx_verif_anchor      ON verifications(anchor_id, checked_at DESC);
CREATE INDEX idx_fetchlog_time     ON fetch_log(requested_at DESC);
```

### 4.2 Storage Policy

- Version blobs are compressed with zstd level 6. For a typical article, 25–30% of the original.
- Default retention policy: the most recent 20 versions per document plus every version referenced by an anchor. **A version an anchor points at is never deleted.** Versions referenced by verification history (`verifications.checked_version`) are also retained for the audit trail — retaining more broadly than the minimum retention rule is the safe direction (v1.3).
- The `anchor gc` command cleans up orphaned versions. **`keep` must be at least 1** — zero or negative would delete even the latest content of documents with no anchors, leaving nothing but empty shells, so it is rejected (v1.5).

---

## 5. Fetch Pipeline

### 5.1 URL Normalization

To prevent duplicate registration of the same document, normalize in the following order before fetching.

1. Lowercase the scheme and host, drop the default port
2. Remove the fragment (`#...`)
3. Remove tracking parameters — only the unambiguous ones such as `utm_*`, `fbclid`, `gclid`, `dclid`, `msclkid`, `twclid`, `yclid`, `igshid`, `mc_eid` (extensible by configuration). **`ref` and `s` are not removed** — depending on the site they are part of the real path, and removing them merges distinct documents into one (v1.3)
4. Sort the remaining query parameters by key
4-1. **RFC 3986 equivalence** (v1.8): percent-encodings of unreserved characters are decoded (`/%7Euser/` = `/~user/` — undecoded, the same document registers twice, §6.2.2.2). Remaining percent triplets have their hex digits uppercased (§6.2.2.1). Reserved characters (`%2F` etc.) are preserved, since decoding them yields a different URL. **No `=` is appended to valueless query parameters** — the normalized URL is also the request URL, so appending one is not cache-key tidying but sending the server a request different from what the user gave.

4-2. **Input validation** (v1.8): input without an http(s) scheme and host, or with an invalid port, is rejected with the domain exception `InvalidURL`. Let through, a port error leaks as a bare `ValueError`, and scheme-less input flows all the way to the robots verdict where **the user's typo is reported as "the site owner refused"** — the worst kind of violation of the fact-reporting principle.

5. The trailing slash of a path is **not normalized; it is left to the redirect response** (v1.5). Only the server knows whether `/a` and `/a/` are the same document or different ones, and the server says so with a redirect. For a document reached via a redirect, the input URL is registered in `document_aliases` so the next lookup finds the cache — that is the practical implementation of this step.

> **Redirects and document identity (v1.8)**: this tool meets redirects more often than anything else. If that path answers "whose document does this body belong to?" wrongly, the user receives, as evidence, **the body of a document they never cited** — worse than a wrong verdict, because the object of the verdict itself has changed, and no re-verification can reveal it.
>
> - **Only permanent redirects (301·308) change the canonical URL.** 302 and 307 mean "look elsewhere for now" and 303 means "look at a different resource" — neither is grounds to rewrite `documents.url`. This keeps a consent-wall or region-gate interstitial URL from hardening into the Memento URI-R.
> - **An old alias that starts serving its own content retires the alias.** A URL that stopped redirecting is no longer the same resource; kept, it pushes another resource's body into the target document's history as a new version. The test is **"it answered 200 with no redirect"** (corrected in v1.8) — testing "the redirect was not permanent" destroys an alias reached through a *temporary* redirect, which is still redirecting. That a body received over a 302 lands in the canonical document's observation history is not contested: per RFC 9110 it is the current representation of the requested resource.
> - **Two documents unified by a permanent redirect are merged.** If A and B were registered separately and an A→B 301 appears later, then unless they are merged, A's anchors get checked against B's body while the verification record holds another document's version id. The old URL survives as an alias. Merge details (v1.8): ① identical (body, source) on both sides keeps the **earliest capture** — 301 declares "same resource", so that body's Memento-Datetime is its first observation across both URLs, and deleting either side changes the citation's `data-versiondate` to a different snapshot ② observation sequence numbers are **renumbered** — each document numbered from 1, so merely moving rows makes `latest~N` show transitions that never happened ③ accounting (fetch_log) moves along ④ the merge is the **only** path that deletes a `documents` row, so access to a vanished document answers with a domain error and consumers (verify, cite) follow the relocated anchors.
> - **A registered document that moves via 301 has its canonical URL moved** (v1.8). Even with no document at the destination, `documents.url` follows and the old address becomes an alias — so the rule above does not hold for new documents only.
> - **Document and version creation are idempotent.** The lock is keyed on the input URL while creation uses the final URL, so two different URLs redirecting to one target both decide "no document". Instead of stacking locks, creation is idempotent so any ordering converges on one row.

> **IPv6 literals** (v1.5): Preserve the brackets in the host. Stripping them produces a syntactically invalid URL and re-normalization fails — normalization must be idempotent.

### 5.2 Request Order

```
1. Cache lookup (purely local — since there is no network request, it comes before the
   robots determination. v1.3)
   └ a version within max_age exists → return cache_hit (zero network)

2. robots.txt check (24h cache per host origin, persisted in SQLite, using protego)
   └ disallowed → RobotsDisallowed, no network request
   └ **5xx / connection failure → deny** (RFC 9309 §2.3.1.4, v1.5). Do not switch to
     unrestricted access while the rules are unknown. This determination is cached for
     only 5 minutes so a transient outage does not block a whole day. 4xx remains
     "no restrictions" as before. Note that this denial applies **only to the original
     request** — see step 6.
   └ **3xx → follow it** (RFC 9309 §2.3.1.2, at least 5 hops, v1.8). Discarding the
     body because the status is not 200 makes even `Disallow: /` vanish — http→https,
     CDN migrations, and canonical cleanups are all ordinary configurations, and each
     of them would hand over the whole site. Past the hop limit the state is
     "rules unavailable".
   └ **A leading BOM is ignored** (RFC 9309 §2.3, v1.8). Left in place, the parser
     fails to read the `User-agent:` line as a directive and **discards the whole
     rule group**.
   └ robots.txt is a response too, so **the size cap and timeout apply to it as-is**
     (v1.8). Past the cap, the verdict uses what was read — discarding everything
     erases the owner's prohibitions, so partial application is closer to honesty.
     The chunk straddling the boundary is truncated and **kept** (dropped, a cap
     smaller than one transfer chunk discards everything and becomes a fully-open
     allow). The declared **charset is honoured** — hardcoded utf-8 annihilates the
     rules of a UTF-16 file. **An unfollowable 3xx** (no Location, 300·305) means
     "we could not ask for the rules" — it is not cached as unrestricted.

3. Rate-limit wait (per-host token bucket, default 1 req/s, burst 3)

4. Conditional GET
   If-None-Match: <etag>
   If-Modified-Since: <last_modified>
   User-Agent: <configured value, §5.4>
   Accept: text/html, application/xhtml+xml, text/plain, application/pdf

5. Response branching
   304 → not_modified. Only last_checked_at is updated. No body transferred.
   200 → normalize → compare text_hash
          same → unchanged. No new version created. (This also applies when raw_hash
                 differs — the case where the dual hash's noise removal worked)
          different ┬ raw_hash same + pipeline_version different → renormalized.
                    │  The source did not change; the extraction and normalization
                    │  rules did. A new version is inserted, but it is not reported
                    │  as "the document changed". (v1.3)
                    └ otherwise → changed. A new version is inserted.
          (the first acquisition is created)
   3xx → follow to the destination (up to 5 hops). **Steps 2 and 3 are repeated at
          every hop** (v1.5). Leaving it to automatic following would fetch forbidden
          paths or other hosts without even a robots request — the target host would
          have no way to block it. If the final URL differs from the input, the input
          URL is registered in `document_aliases`.
   402 → PaymentRequired (Cloudflare Pay Per Use, etc.) → step 6
   403/429 → exponential backoff retry (up to 3 times) → on failure, step 6
   404/410 → status = gone → step 6

6. Archive fallback (enabled by configuration, on by default)
   └ query a Memento aggregator or the Wayback CDX for the URI-R
     └ URI-M found → retrieve the content → insert a version with source='archive'
        (record the URI-M in source_uri, clearly distinguished from live versions)
     └ not found → the original state (gone/forbidden) is confirmed
```

**Paths that lead to the fallback** (reinforced in v1.5): step 6 is reached not only on the HTTP failures of step 5 (402/403/404/410/429) but also **when the original could not be reached at all**. That covers the cases where the host has disappeared entirely and the connection fails, or where robots.txt could not be retrieved and the determination was withheld — **host disappearance is the most common form of link rot and precisely the situation where archive rescue is most needed**, so if it is blocked here the entire reason this feature exists disappears.

> **The URI-M an aggregator points at also gets a robots verdict** (v1.8): everything we fetch as content gets a robots verdict. Otherwise a path that direct fetching blocks as `explicit` can be reached through one layer of aggregator, and the principle below becomes words only. That an archive is a different host with its own policy is something to confirm through that host's robots — not a reason to skip confirming. (Aggregator/CDX *lookup* calls are API endpoints the user explicitly enabled; they carry the rate limit and User-Agent, under that service's terms of use.)
>
> **The `statuscode` in a CDX response is re-checked (v1.10)**: the fact that we *requested* `filter=statuscode:200` is not evidence that the response honoured it. On a mirror or proxy that ignores the filter, an archived 404 notice gets stored as "the rescued body", and a quote on top of it is then **asserted** to be `MISSING`. We take the most recent row whose `statuscode` is 200; if that column is absent altogether we cannot know which row is a 200, so we abandon the rescue and report the original state (gone) truthfully — we do not treat what we do not know as known (§5.4). The aggregator path (MemGator/Time Travel) does not expose HTTP status, so this re-check is impossible in principle there; in that case we trust the memento the aggregator selected.

> **Recording the robots verdict (v1.10)**: `documents.robots_allowed` holds only "**do the owner's rules block this URL**". ① The verdict is attributed to **the document the blocked hop belongs to** — recording a different host's refusal (at a redirect destination) against the input URL's document leaves a site that forbade nothing marked as forbidding. ② `unavailable` (5xx, undeterminable) is not a value of this column — recording what we could not ask as a refusal freezes a transient outage into a permanent ban. ③ When the rules allow again, it returns to `true`.

> **An explicit denial and an undeterminable state are different things**: If robots was read and its rules blocked us (`explicit`), that is the site owner's intent, so we do not route around it via an archive either. If we simply could not ask about the rules (`unavailable` — host disappearance, 5xx), that is merely a state in which the intent cannot be confirmed, and an archive is **a different host with its own policy**, so there is no reason to block the check. This distinction is what reconciles §5.4's honest client principle with step 6.

**The significance of step 6**: Even when a document disappears, the citation is not completely invalidated. Before rendering a `GONE` determination, public archives are checked once. A version obtained via this path is marked `source='archive'`, so the user always knows it was "confirmed from an archive, not the original."

**Fallback targets**: A self-hosted MemGator instance or the Wayback CDX API. The aggregator URL is a configuration value with no default (the user must specify it to enable it). We do not quietly depend on external services.

#### MemGator Operating Guidance (mandatory)

MemGator is MIT-licensed and already provides TimeMap / TimeGate / Memento endpoints. Anchor does not include its code and calls it only over HTTP.

| Item | Guidance | Reason |
|---|---|---|
| `--spoof` option | **Never use it** | Random user-agent disguise. Directly violates the honest client principle of §5.4 |
| `--agent` | Set it identical to Anchor's User-Agent | Do not blur accountability |
| `--arcs` (archive list) | **Specify it explicitly** | The default is a `git.io` shortened URL, and that service shut down in 2022 |

Carry this table verbatim into the documentation and the installation guide. When integrating a tool that ships an evasion feature, the fact that we do not use that feature must be recorded in the documentation, not just in the code.

### 5.3 Normalization and Hashing

```
raw bytes
  → encoding detection (charset-normalizer)
  → content extraction (trafilatura; falls back to readability-lxml on failure,
     **or when block structure has collapsed**)
  → markdown conversion
  → unify line separators (CR, U+2028, U+2029, U+0085, U+000B, U+000C → newline)
  → Unicode spaces → ASCII space; remove only zero-width characters that do **not**
     participate in rendering
  → **classify lines and blocks** (code fences, indented code, structural lines, prose)
  → prose lines only: fold typographic line breaks → strip formatting markers and
     formatting-only tags
  → Unicode NFC normalization (**after** character removal)
  → collapse runs of whitespace to one, strip trailing whitespace, reduce 3+ newlines to 2
  → normalized_text

raw_hash  = blake3(raw bytes)
text_hash = blake3(normalized_text.encode("utf-8"))
```

If `text_hash` is the same even when `raw_hash` differs, the determination is **no change**. This is the core mechanism that filters out noise such as ad slots, view counters, and CSRF tokens.

**`pipeline_version`** (v1.3, reinforced in v1.5): `text_hash` has hidden inputs — the extractor version, **the encoding detector version**, and the normalization rule version. **Because each path actually uses different tools, the strings must differ as well** — if the `text/plain` path records a trafilatura version it never even ran, then whenever the Content-Type wobbles (a missing header, a CDN difference, an archive response) it misreports `changed` although the source is unchanged, and the `renormalized` safeguard fails to open in exactly that situation (v1.5).

| Path | Format |
|---|---|
| HTML (primary) | `trafilatura/<ver>+charset/<ver>+norm/<n>` |
| HTML (fallback) | `readability-lxml/<ver>+markdownify/<ver>+charset/<ver>+norm/<n>` |
| text/plain · markdown | `plain+charset/<ver>+norm/<n>` |
| PDF | `pypdf/<ver>+norm/<n>` |
 The combination of identical `raw_hash` + differing `text_hash` + differing `pipeline_version` is a pipeline change, not a source change, and its outcome is distinguished as `renormalized` rather than `changed`. It is the mechanism that prevents reporting a change when the source did not change. Whenever the normalization rules are modified, the rule version must be incremented.

> This dual hash bears directly on anchor stability as well. News sites insert different ad text on every page load, so **character offsets shift even when the document content has not changed.** Without normalized text as the reference, position-based anchors break every time.

PDFs go through `pypdf` text extraction, then end-of-line hyphenation is restored and they follow the same path. Scanned PDFs are out of scope and return `UnsupportedContent`.

**Content types are compared exactly, not by prefix** (v1.5). Prefix matching would wrongly route `text/plaintext` to the plain path and `application/pdfx` to the PDF path.

### 5.4 Network Etiquette (compliance)

Anchor operates as an **honest client**. This is not a feature; it is a premise.

- **User-Agent**: default `Anchor/<release version> (+https://github.com/julgi80ai-stack/anchor-mcp)`. No disguise or spoofing option is provided, and **a blank value is rejected at configuration time** (v1.8) — you cannot promise to honour the rules while refusing to say who you are, and an empty UA makes robots matching run on an empty token. Validation happens **the moment a `Config` is constructed**: the direct-library path (§8) must receive the same guarantee.
- **robots.txt**: respected by default. The `respect_robots = false` setting exists, but enabling it prints a warning in the server startup log.
- **Rate limiting**: a per-host token bucket. The configured value is honored even under concurrent calls — left unlocked, waiting threads all wake at once and hammer the host at several times the configured rate (v1.5).
- **Honoring `Retry-After`** (clarified in v1.5): both numeric and HTTP-date forms are parsed. If the server specifies a wait longer than the ceiling (default 60 seconds), we **stop retrying rather than truncating it and knocking early** — reporting "could not confirm" is the honest answer. Unparseable or abnormal values (`nan`, etc.) fall back to our own exponential backoff.
- **Size ceiling**: applied to **every response**, not only successful ones (v1.5). We do not buffer an enormous error page or blocking interstitial in full. **robots.txt is a response too** (v1.8) — exempting it alone lets a 20MB robots.txt settle wholesale into the cache DB.
- **Conditional requests**: always used. This is exactly what reduces server load. Validators ride **only on the hop of the resource we received them from** (v1.8) — the document's canonical URL. Sent on every hop, a destination honestly comparing them against its own validators returns 304, which Anchor reads as "nothing changed", serving the old body as current forever (the move is never detected) while leaking the ETag to other hosts. Sent only on the first hop, re-checking through a redirecting alias loses conditional requests entirely. The hop-to-canonical comparison uses the **normalized form** (§5.1, v1.8) — raw strings never match once query ordering, tracking parameters, or a fragment differ.
- **`https → http` downgrade refused** (v1.8): a body received over a channel with no integrity guarantee does not become citation evidence, and the canonical URL is not recorded as plaintext.
- **Unfollowable Location** (v1.8): a non-http(s) Location (`mailto:`, `about:blank`, …) is that document's `FetchFailed` — it does not escape the exception hierarchy (`httpx.InvalidURL`) and kill the whole re-verification batch.
- **No anti-bot evasion**: proxy rotation, browser fingerprint spoofing, and CAPTCHA solving are not implemented. A 403 is reported as a 403.

> From 15 September 2026, Cloudflare blocks by default those crawlers that mix search/agent/training purposes on ad-serving pages. Anchor is an agent-class fetcher that operates on user request; when blocked, it does not evade — it records a `Forbidden` state and then attempts only the archive fallback. Support for signature-based bot authentication (Web Bot Auth) is a v1.3 candidate.

---

## 6. Anchor Engine

### 6.1 Anchor Creation

Given a quote `quote`, find its position in the current version's `normalized_text` and store the following.

```python
@dataclass(frozen=True)
class Anchor:
    """A position descriptor for finding one quote again in the source.

    Follows the combination of TextQuoteSelector and TextPositionSelector
    from the W3C Web Annotation Data Model. Offsets alone break as soon as
    the document changes even slightly, so the surrounding context is kept
    as well, allowing rediscovery after the position has moved.

    position_hint is only a hint for where to start searching; it is not
    grounds for a determination. An ad insertion alone changes the offset.
    """
    exact: str          # the quote itself
    prefix: str         # the preceding 48 chars
    suffix: str         # the following 48 chars
    position_hint: int  # offset at creation time (used only as a search start point)
    quality: Quality    # OK | SHORT
```

If `quote` is not present in the source, no anchor is created and `QuoteNotFound` is raised. **Not recording a citation that does not exist** is the basic contract of this tool.

#### Lookup tolerant of block separators (v1.6)

A quote a person dragged off the screen carries **different block separators** from the stored body. The browser hands over a single newline or a space between paragraphs and no list marker (`- `) at all, whereas the stored body has blank lines and markers. As a result, quoting two paragraphs or two list items at once — a common action — always failed.

Lookup therefore runs on a form where **runs of spaces and newlines count as one and leading list markers are skipped**. The anchor's `exact`, however, is bound to **a string that actually exists in the stored body** rather than to the form the user supplied — stages 1 and 2 of §6.2 are exact-match searches, and that premise must not be broken.

This tolerance **does not leak toward permitting fabrication.** Only whitespace placement differs; a sentence that is not in the body still yields `QuoteNotFound`. That error also states that the region may not have been stored — when a sentence plainly visible on the page (a summary box, a figure caption, a reference list) was not extracted as body text, telling the user only "it is not in the source" is a false statement.

#### Short Quote Warning (new in v1.1)

If `exact` is **under 32 characters**, it is marked `quality = SHORT` and a warning is included in the response.

Short, common strings create pathological cases in fuzzy matching. Many similar candidates exist throughout the document, so search cost explodes and the false-positive probability is high as well. Hypothesis actually suffered from clients freezing for more than 10 seconds because of this combination (long document + short common quote).

- Under 32 chars: creation allowed with a warning. On re-verification, half the time budget applies
- Under 12 chars: creation refused (`QuoteTooShort`)
- Recommended: one complete sentence

#### Writing-System-Aware Thresholds (v1.5)

The 12- and 32-character figures above are **values set on the assumption of Latin script**. Japanese and Chinese write the same content in far fewer characters, so applying them as-is either refuses a complete sentence (the Chinese `气候变化是真实的。` is 9 characters) or classifies everything as `SHORT`. When the proportion of Han characters and kana exceeds half, the thresholds are converted by the information-density ratio (roughly 2.5×). **Korean separates words with spaces and has intermediate information density per character, so the Latin baseline is used as-is.**

#### Duplicate-Occurrence Warning (v1.5)

If a quote occurs multiple times in a document, the anchor binds to the **first occurrence** (because stages 1 and 2 of §6.2 are plain exact matching). Since the state could be `INTACT` because of another instance even after the instance the user cited was deleted, the occurrence count is measured at creation time and carried in the warning. Anchor does not determine which instance is "the" citation; it reports only the fact.

### 6.2 Matching Algorithm (revised in v1.1)

For a new version, the following are attempted in order. As soon as a stage succeeds, it terminates.

| Stage | Method | Determination |
|---|---|---|
| 1 | Exact match of `exact` within `position_hint ± 500 chars` | `INTACT` (score 1.0) |
| 2 | Exact match of `exact` across the whole document (standard string search) | `MOVED` (score 1.0) |
| 3 | Find **all** candidate spans by `prefix + suffix` context and select the closest | `ALTERED` (score = similarity) |
| 4 | **Approximate string search with an edit-distance ceiling** + context corroboration | corroborated find → `ALTERED`, otherwise → `MISSING` |

#### Stage 4 in Detail — the Core Change from v1.0

**The problem with the v1.0 specification**: A whole-document sliding scan with `rapidfuzz.partial_ratio` is O(n·m), and **it is slowest exactly when it fails to find anything.** It stays hidden in the normal situation where most verification targets are `INTACT`, and then performance collapses in the worst situation, where the document has been heavily reworked. This is the same failure mode Hypothesis hit with `diff-match-patch`. A benchmark of an alternative implementation reported a 14× difference — 13,342 ms versus 936 ms on a 100,000-character document with 453 quotes — and the approximate-matching side also had a higher anchoring success rate.

**The v1.1 approach**: Fix an edit-distance ceiling `k` up front and use a bit-parallel approximate search that gives up immediately once that ceiling is exceeded.

```python
# k = the allowed edit distance. Proportional to quote length, but capped.
# Accounts for the writing system (v1.5): a revision of the same character
# (replacing one word) is expressed in far fewer characters in Japanese and
# Chinese, so multiplying by a fixed ratio drops ALTERED down to MISSING.
#
# The adjustment must be **continuous** (v1.7). A step like "2.5x if majority"
# is always wrong just below the step — Japanese and Chinese sentences mixing
# Latin abbreviations, years, or percentages routinely fall below density 0.5
# (`GDPは3.2%増加した。` is 0.462), at which point k shrinks 2.5-fold and a
# two-character replacement becomes MISSING.
density = share_of_han_and_kana          # 0.0-1.0 (whitespace excluded, Hangul excluded)
ratio = 0.15 * (1.0 + 1.5 * density)     # density 0 -> x1.0, density 1 -> x2.5
k = max(1, min(int(len(exact) * ratio), 64, len(exact) - 1))
# k must stay below the quote length (v1.10, D-143): with k >= len(exact),
# "delete everything and insert something else" falls inside the allowance and
# any sentence at all becomes an approximate match. The configuration cap
# (max_edit_ratio <= 1) alone does not hold this boundary — the writing-system
# factor (up to 2.5) is multiplied on top.
```

Implementation priority:

1. **Primary implementation** — the fuzzy matching of the `regex` module. It is a C implementation and supports an error ceiling natively.
   ```python
   pattern = regex.compile(f"({regex.escape(exact)}){{e<={k}}}", regex.BESTMATCH)
   ```
2. **Fallback / optimization** — a direct implementation of Myers bit-vector approximate string search. It is the algorithm the `approx-string-match` family uses; for pattern lengths ≤ 64 it is close to O(n) through word-level parallelism.

> **Path selection (revised in v1.5, corrected in v1.7)**: regex fuzzy matching becomes exponentially slow **when k is large and there is no result**. Measurements show it is already slower than Myers from k=4, and at k=6 on a 7 KB Korean article it exhausted the 200 ms budget and produced `UNRESOLVED` (running the same input through Myers is 56–67× faster). Since the problem was that short quotes (= complete CJK sentences) were pinned to the slow path, **regex is used when k ≤ 3 and the Myers path when k > 3**.
>
> v1.5 additionally claimed here that "the two paths agree in their determinations" — **that was not a fact** (corrected in v1.7). The differential comparison only exercised decoy-free inputs. The two paths agree only once the window-candidate rule below is in place.
>
> **Window candidates (v1.5, revised in v1.7)**: For quotes longer than 64 characters, the position is narrowed using 64-character cores from the front and the back, and then the whole quote is verified inside the window with semi-global DP. The core scan returns **every position within distance k** as a candidate — keeping only the global minimum per core means that in the ordinary edit where a summary carries the quote's head and a pull-quote its tail, both cores' optima land on those decoys and the true position's window is never even examined (a false `MISSING`). Since a core is a substring of the quote, the whole quote's distance inside a window is **at least the core's distance** — that lower bound prunes the remaining candidates: everything is swept, nothing is wasted. Contiguous qualifying positions are grouped into one run keeping only its minimum, and runs are **split at the pattern length** — when k approaches the core length (Latin quotes from ~340 chars up), ordinary prose qualifies almost everywhere and the whole document would otherwise collapse into a single run, reviving the original defect. The window needs `m + 2k` of slack on the right (because the true match's start can shift by ±k and its length can stretch to m±k).
>
> **Context corroboration (new in v1.7)**: a stage 4 find is presented as `ALTERED` only when corroborated as **the place the quote used to live**. Similarity alone cannot decide this — in contracts, release notes, and FAQs the context is boilerplate, and a sibling paragraph from another section differs by only a couple of characters. The deciding signal is what remains at the old location: if the old neighbours now **sit adjacent**, the quote left that spot (a section move) and a match elsewhere is that quote; if something else occupies the slot, "moved and edited" and "deleted, with a lookalike elsewhere" **cannot be told apart by this evidence**, so neither is asserted (see §6.3). Candidate ranking also **prefers the corroborated side** even at slightly larger edit distance — an appendix's boilerplate is often closer to the original than the revised body is.

`score` is computed as `1 - (edit_distance / len(exact))`, and `edit_distance` is stored alongside it. A ratio alone is misleading for short quotes.

> **Candidate selection (v1.5)**: Stage 3 **does not stop at the first occurrence** of the prefix. In documents with repeating templates (contract clauses, changelogs, FAQs, tables), fixing on the first candidate reports a sibling paragraph unrelated to the quote as "the current form of your quote" — what is wrong is not the determination but **the text presented as grounds for the determination**, which turns §6.3's "present the text before and after the change together" into a false comparison table.

#### Time Budget (new in v1.1, enforcement scope widened in v1.5)

There is a ceiling on matching time per anchor. On exceeding it, no determination is forced; `UNRESOLVED` is returned.

**The budget is checked not only between stages but inside them** (v1.5). The edit-distance DP of stages 3 and 4 is O(quote length × candidate length), so checking only between candidates lets a single call blow through the whole budget — measurements showed a 4,000-character quote spending 7.7 seconds against a 200 ms budget (38×).

The check period is **converted to DP cells** (v1.7). With a per-row period, one row costs O(candidate length), so the overshoot grows with the quote — against a 200 ms budget an 8,545-char quote spent 267 ms and a 68,902-char quote 1,133 ms (violating §10's per-anchor p99 of 250 ms). Bounding the work between checks in cells makes the overshoot a constant independent of length.

| Condition | Default budget |
|---|---|
| `quality = OK` | 200 ms |
| `quality = SHORT` | 100 ms |
| Document length ceiling | 2 MB (beyond that, only the first 2 MB is searched, with a `TRUNCATED` flag) |

> **Unit of measure (v1.10)**: `anchor.max_document_bytes` is measured in **UTF-8 bytes**, as the name says. Measured in characters, Hangul (3 bytes per character) passes at up to 3× the documented cap. On overflow the truncation lands on a character boundary, and §6.3's rule of withholding MISSING/ALTERED verdicts for truncated documents is unchanged.
>
> **Effective limit (made explicit in v1.7)**: the 2 MB above caps the **stored and searched range**, not what stage 4 can sweep within the budget. The Myers core scan is pure Python at roughly 2.0M chars/s, so for quotes over 64 characters (two cores × a full scan) the effective limit under the default 200 ms budget is **about 190K characters**. On larger documents, when stage 3 fails, stage 4 cannot reach a verdict and returns `UNRESOLVED`. That is not a defect but a consequence of the contract — we chose to say we do not know, and a larger budget widens the reach accordingly. If UNRESOLVED is frequent on large documents, raising `time_budget_ms` is the intended answer.

**Saying you do not know what you do not know is better than giving a wrong answer quickly.** In batch verification, one anchor must not be allowed to stop the whole run.

### 6.3 Verification State Codes (7 states)

| State | Meaning | Action required from the user |
|---|---|---|
| `INTACT` | Present unchanged in the same position | None |
| `MOVED` | Present unchanged at a different position in the document | None (paragraph reordering, etc.) |
| `ALTERED` | The sentence was modified | **Review needed.** Present the text before and after the change together |
| `MISSING` | The document is alive but the quote has disappeared | **Consider withdrawing or replacing the citation** |
| `GONE` | The document itself is 404/410 and is not in any archive | **Withdraw the citation or replace it with a preserved version** |
| `UNREACHABLE` | 403/402/timeout — cannot be confirmed | Schedule a retry |
| `UNRESOLVED` | Determination withheld — budget exceeded, or **the evidence splits** *(new in v1.1, widened in v1.7)* | Re-verify with a larger budget, or judge by eye using `found_offset` |

`ALTERED` and `MISSING` are different events. The former means the source changed; the latter means the citation became invalid. This distinction produces the most practical value in report review.

> **When the evidence splits (new in v1.7)**: if stage 4 finds a candidate within the edit-distance ceiling but has no grounds to confirm it is **this quote**, the determination is withheld. When something else occupies the quote's old slot and a similar sentence exists in another section, "it moved and was edited" and "it was deleted and a lookalike exists" cannot be told apart by this evidence. Presenting `ALTERED` makes the user read a sentence of opposite meaning as the revision; presenting `MISSING` declares a living quote dead — **both are assertions.** In this case `after` stays empty: not fabricating a false comparison table is the point of this state.
>
> If **nothing** lies within the edit-distance ceiling, that is `MISSING`. That is not split evidence but absent evidence — absence can be stated.
>
> **Truncation and verdicts (v1.5, extended in v1.7)**: if the document was cut at the ceiling, "the quote has disappeared" cannot be asserted — `MISSING` is lowered to `UNRESOLVED`. **The same applies to `ALTERED`** (v1.7): a quote straddling the cut has its severed tail counted as edit distance, so the original is intact yet the severed fragment would be presented as its "current form". A find touching the cut boundary withholds the verdict. Not asserting what you could not fully see is not a rule for `MISSING` alone.

`UNRESOLVED` is not a failure but **an honest non-answer**. Making an arbitrary determination in order to avoid producing this state destroys trust in the whole tool.

**If the document was not read in full, do not say `MISSING`** (v1.5). Failing to find a quote in a document truncated at the 2 MB ceiling is not "it disappeared" but "it could not be confirmed," so it is downgraded to `UNRESOLVED` and the fact of truncation (`truncated`) is carried in the result — a user who cannot know the cause cannot act on it either.

**Anchor does not determine the meaning of `ALTERED`.** Whether it is a typo fix or a reversal of position is decided by the caller, looking at the text before and after the change. Holding this boundary is the substance of the LLM-independence principle.

---

## 7. MCP Tool Interface

### 7.0 Protocol Compliance (new in v1.1)

Follows the **MCP 2026-07-28 spec**. There are three practical consequences.

- **Stateless core** — the server does not require a sticky session. Local SQLite is the only state, so this is satisfied naturally.
- **Streamable HTTP** — attach the `Mcp-Method` and `Mcp-Name` headers.
- **Tasks extension** — `verify_citations` entails dozens to hundreds of network requests, so **it can be run as a Task.** Called with task metadata attached, it runs in the background and the client can poll progress with `tasks/get`, collect results with `tasks/result`, and abort with `tasks/cancel`. Called without task metadata, it is an ordinary synchronous call (the compatibility path for clients that do not know Tasks). Immediate-response tools (`fetch_document`, `cite`, `get_version`, etc.) remain ordinary tool calls.

> **Pagination for `tasks/list` (v1.10)**: MCP's cursor pagination is honoured — 50 entries per page, an opaque cursor, ordered by task id (uuid7, so lexical order is creation order). The cursor points at an **item**, not a position ("after this id"), so pruning between pages never skips what remains. A cursor we did not issue is rejected with `-32602` — silently starting over would let a client read the same items twice without knowing. Returning everything makes the response grow with the list on a long-running server.

> **Cancellation and shutdown (v1.5)**: A batch runs on a separate thread, and a thread cannot be forcibly cancelled. `tasks/cancel` is therefore implemented as **a cooperative abort signal** — the worker checks between documents (and, since v1.9, between anchors) and does not touch the remaining work. Changing only the status while the work keeps running is a violation of the contract that says "it can be aborted." A cancelled task must also leave partial results that can be collected via `tasks/result` (otherwise the client falls into infinite polling), and on server shutdown the store is closed **after the workers have been cleaned up** (getting the order wrong kills the process by releasing a connection still in use). Terminated tasks are cleaned up after their `ttl` elapses. If task metadata is attached to a tool that cannot be run as a task, it is rejected rather than silently ignored.

> **ttl policy and shutdown guarantee (v1.9)**: `Task.ttl` is **always a finite, actual retention period** — a call that omits ttl gets the server default (30 minutes), zero and negative values are rejected (-32602: a retention that makes result retrieval impossible from the start), and oversized requests are clamped to the cap (24 hours) with the actual value reported. **The ttl key is never omitted from any response** — a required-nullable field loses its key under exclude_none serialization, and a standard client's schema validation then rejects the entire response. Retention is counted from creation as the protocol defines, but at termination it is updated to the actual retention (elapsed + requested) so that a long-running task's result does not vanish the moment it finishes — the cap therefore applies to the **post-termination retention (the requested ttl)**, and for a long run the total retention from creation may exceed 24 hours. Shutdown releases the store **only after every worker has finished** — past the grace period it says so and keeps waiting (the interpreter waits for the workers anyway; this merely performs the same wait with the store still open). The cancel/shutdown signal is checked **between anchors** as well as between documents. The bound on reaction time is not the batch size but **one anchor's budget plus one document's entire fetch in flight** (robots, redirect hops, retries, and archive fallback — potentially several requests): the signal is not checked while waiting on the network, so a `tasks/cancel` overlapping that window **may answer with a non-terminal state** — the signal is already set, and termination is observed by polling `tasks/get`.

> **Wire format caution (v1.3)**: MCP tasks are exclusive to the 2025-11-25 experimental revision, so the wire gate of the current protocol (2026-07-28) does not permit a `CreateTaskResult` as a `tools/call` response. The task descriptor is therefore returned **in-band** in the structuredContent of `CallToolResult` (`{"task": {taskId, status, …}}`), and the `tasks/*` methods are served under the extension (SEP-2133) `dev.julgi.anchor/tasks`. When tasks return to the core spec, this is replaced with the standard format.

All times are ISO 8601 UTC strings.

### 7.1 `fetch_document`

Fetches a document or returns it from the cache. **It aims to be a drop-in replacement for `mcp-server-fetch`** — it does the same job, with a cache and provenance attached.

```jsonc
// input
{
  "url": "https://example.com/report",     // required
  "max_age": 3600,                          // seconds. Default 86400. 0 means always check
  "force_refresh": false,
  "include_content": true,                  // false gives metadata only (saves tokens)
  "start_index": 0,                         // mcp-server-fetch compatible: chunked reading
  "max_length": 5000                        // chunk length. 0 means unlimited (v1.3)
                                            // start_index and max_length cannot be negative (v1.5)
}

// output
{
  "document_id": "018f...",
  "version_id": "018f...",
  "url": "https://example.com/report",
  "title": "2026 Report",
  "outcome": "not_modified",                // cache_hit|not_modified|unchanged|changed
                                            // |renormalized|created|archive (v1.3, §5.2)
  "captured_at": "2026-08-16T04:12:00Z",
  "text_hash": "b3:9a4f...",
  "char_count": 18432,
  "source": "live",                         // live | archive
  "content": "# 2026 Report\n\n...",         // only when include_content=true
  "content_truncated": true,                // true if the chunk was truncated (v1.3)
  "next_start_index": 5000,                 // where to resume (only when truncated, v1.3)
  "network": { "bytes_down": 0, "elapsed_ms": 142 }
}
```

### 7.2 `cite`

Assigns an anchor to a quote.

```jsonc
// input
{ "document_id": "018f...", "quote": "More than half of AI crawler traffic goes to re-fetching pages that have not changed", "note": "grounds for ch. 3" }

// output
{
  "anchor_id": "018f...",
  "version_id": "018f...",
  "source": "live",                         // live | archive (v1.10) — what the anchor was placed on
  "offset": 8214,
  "quality": "ok",
  "warnings": [],                           // warning strings when quality=short
  "created_at": "..."
}
```

### 7.3 `verify_citations` *(Task)*

Re-verifies anchors against the current source. Processes in batch and observes per-host rate limits. **Returns as a Task.** `time_budget_ms` accepts **positive values only** (v1.9) — zero and negative values silently skip matching stages 3–4 and demote genuinely revised quotes from `ALTERED` to `UNRESOLVED`, so they are rejected at the common choke point shared by the sync tool, the task path, and the CLI.

```jsonc
// input — at least one of the three
{
  "anchor_ids": ["..."],
  "document_ids": ["..."],
  "older_than": "P7D",
  "time_budget_ms": 200                     // override the per-anchor matching budget (optional)
}

// final result
{
  "checked": 42,
  "summary": {
    "INTACT": 36, "MOVED": 2, "ALTERED": 2,
    "MISSING": 1, "GONE": 0, "UNREACHABLE": 0, "UNRESOLVED": 1
  },
  "sources": { "live": 40, "archive": 2, "none": 0 },   // what each check compared against (v1.10; sums to checked)
  "attention": [
    {
      "anchor_id": "018f...",
      "state": "ALTERED",
      "url": "https://example.com/report",
      "before": "More than half of AI crawler traffic goes to re-fetching pages that have not changed",
      "after":  "About 60% of AI crawler traffic goes to re-fetching pages that have not changed",
      "match_score": 0.86,
      "edit_distance": 12,
      "position_hint": 4210,                // where the anchor was created (v1.7)
      "found_offset": 4198,                 // where it was found now (v1.7)
      "source": "live"                      // what this item compared against (v1.10; null if nothing was)
    }
  ],
  "network": { "requests": 12, "not_modified": 9, "bytes_down": 48210 },
  "stopped_early": false                    // did it end early due to cancellation or shutdown (v1.5)
}
```

`sources` always records **what each check compared against** (v1.10). Attaching provenance only to `attention` makes that fact vanish entirely from a report where everything is `INTACT` — "the origin is 404 and only an archived snapshot was examined" reads as "nothing wrong." Since §5.2 requires that the user can **always** tell when something was confirmed from an archive, that knowledge must not arrive only when something is wrong. Items with no comparison at all (`GONE`, `UNREACHABLE`) are `none`; provenance is never invented.

If `stopped_early` is true, `checked` and `summary` are **partial results**. The remaining anchors were not verified, so they must not be read as "nothing wrong."

The `attention` array carries only the items that require action (`ALTERED`/`MISSING`/`GONE`/`UNRESOLVED`). It does not fill the context by listing all 36 `INTACT` entries.

`position_hint` and `found_offset` are provided together (v1.7). From edit distance alone the caller cannot tell whether this is **a revision in the same place** or **a lookalike paragraph from another section of the document**. If the two are far apart, the item is worth a human look even though it passed §6.2's context corroboration.

### 7.4 `diff_versions`

Returns the content difference between two versions as a unified diff.

```jsonc
{ "document_id": "018f...", "from_version": "latest~1", "to_version": "latest", "context_lines": 2 }
```

Why the parameters are not named `from`/`to`: they are Python keywords and cannot be used in the tool signature of the reference implementation (v1.3). Version references are `latest`, `latest~N`, or a version id — **N is a non-negative decimal integer only**, and any other form (negative, non-integer, empty) is an argument error (v1.10). Letting a negative through passes the range guard untouched and lands on Python's negative indexing, so `latest~-1` returns the **oldest** version — the feature that "brings back the source as it was when you cited it" hands back precisely the opposite end. `context_lines` must be ≥ 0, and both checks run at the common gate **before** version references are resolved (otherwise, on a document with only one version, the user gets "no such version" instead of the real cause).

> **The coordinate system of `latest~N` (v1.7)**: since `latest` follows the pointer, `latest~N` also follows **observation order**. Stepping back by capture time mixes two coordinate systems: after a revert, `latest~1` points at the same row as `latest`, so the default diff comes out empty (right after a fetch reported `changed`); in A→B→A→C→A it presents **a transition that never happened (B→A)** as evidence; and the intermediate edition B is unreachable by any `latest~N`. The TimeMap (§7.8) stays in **capture order**, by contrast — RFC 7089's time axis is the Memento-Datetime. The two answer different questions.

### 7.5 `get_version`

Retrieves the content of a past version verbatim. Even after the source is gone, the text as it stood at citation time can be inspected.

### 7.6 `list_documents`

Returns the list of cached documents together with their status and last-checked time. Filters: `status`, `host`, `has_pending_verification`.

`status` is case-folded and then validated against the enumeration (`live | gone | forbidden | paywalled`); anything else is rejected with the list of allowed values (v1.9). Returning an error-free empty list for a string outside the enumeration reads to the caller as "the cache is empty."

The criterion for `has_pending_verification` is **which version was verified**, not when (v1.7). Reverts reuse an old row and archive rescues carry a past Memento time, so measuring by time answers "nothing to verify" right after the current body changed — a workflow narrowing its targets with this filter would never re-examine documents whose verdicts flipped.

### 7.7 `cache_stats`

```jsonc
{
  "documents": 312, "versions": 489, "anchors": 1204,
  "disk_bytes": 24117248,
  "last_30d": {
    "requests": 1840,
    "cache_hits": 1102,
    "not_modified": 498,
    "unchanged": 96,
    "changed": 130,
    "archive": 4,
    "errors": 10,
    "bytes_down": 4821023,
    "bytes_saved_estimate": 71303168,
    "hit_rate": 0.87
  }
}
```

**Accounting invariants** (v1.10) — if these two break, every number below them is a lie:

1. **One user call = one `fetch_log` row, and its outcome is the final result.** A failure that ends in an exception (timeout, network error, redirect limit, size cap, extraction failure, robots refusal) is still one `error` row — if failures drop out of the denominator, `hit_rate` depends on the *kind* of failure (measured 2.67× inflation). A call rescued from an archive is one `archive` row, not an `error` row plus an `archive` row.
2. **`bytes_down` is all the traffic the call actually downloaded — even when it ends in failure.** Not just the body: redirect-hop interstitial bodies, robots.txt, and futile archive lookups all count. Bytes that vanish with an exception undermine the credibility of every savings claim.

It follows that **the breakdown (cache_hits + not_modified + unchanged + changed + archive + errors) sums to requests**, and `hit_rate = (cache_hits + not_modified) / requests`.

Since the exact size at event time is not retained, `bytes_saved_estimate` approximates with the byte_size of **the body the origin is serving now** (`documents.current_version`) (v1.10) — measuring by the version with the latest capture time evaluates every saving against a version that is no longer being served for reverted and archive-rescued documents, and that inflation factor has no bound (measured 1220×). A document whose pointer is not yet set falls back to the last *observed* version.

`disk_bytes` is what the store actually occupies on disk (main file + `-wal` + `-shm`). A WAL checkpoint is attempted right before measuring; if the store is in concurrent use, the file sizes of that moment are reported as they are (v1.10) — the WAL never shrinks after a checkpoint, so summing without reclaiming makes the same store look several times larger in a long-running process.

`bytes_saved_estimate` is the metric by which the user directly confirms the savings. This number has to prove the tool's reason for existing on its own.

### 7.8 `get_timemap` *(new in v1.1)*

Exports the version list of one document as an **RFC 7089 TimeMap**. External Memento clients can read it.

```jsonc
// input
{ "document_id": "018f...", "format": "link" }   // link | json

// output (format=link, application/link-format)
{
  "content_type": "application/link-format",
  "body": "<https://example.com/report>; rel=\"original\",\n<anchor:///018f.../timemap>; rel=\"self\"; type=\"application/link-format\",\n<anchor:///018f.../v/018e...>; rel=\"memento\"; datetime=\"Tue, 15 Jul 2026 09:11:00 GMT\",\n<anchor:///018f.../v/018f...>; rel=\"memento\"; datetime=\"Sat, 16 Aug 2026 04:12:00 GMT\""
}
```

Local versions are given the `anchor://` URI scheme. Versions that came from an archive expose their actual URI-M as-is.

**The implementation cost is a single serialization function.** What it buys is interoperability with a 20-year-old RFC, and a point of contact with the community that has held on to this problem for that long.

### 7.9 `export_robust_links` *(new in v1.1)*

Exports anchors in **Robust Links** notation. This is the interoperability output that lets readers who do not use Anchor still know the citation time.

```jsonc
// input
{ "anchor_ids": ["..."], "format": "html" }      // html | markdown | bibtex_note

// output (html)
{
  "items": [
    {
      "anchor_id": "018f...",
      "html": "<a href=\"https://example.com/report\" data-originalurl=\"https://example.com/report\" data-versiondate=\"2026-08-16\" data-versionurl=\"https://web.archive.org/web/20260816041200/https://example.com/report\">2026 Report</a>"
    }
  ]
}
```

`data-versionurl` is filled in only when the archive URI-M is known. Otherwise only `data-originalurl` and `data-versiondate` are emitted — a form the Robust Links spec permits.

---

## 8. Python API

It must be usable directly, without MCP.

```python
from anchor import Anchor

# The context manager manages the SQLite connection and the HTTP session together.
with Anchor(db_path="~/.anchor/store.db") as ax:
    doc = ax.fetch("https://example.com/report", max_age=3600)
    print(doc.outcome, doc.char_count)

    cit = ax.cite(doc.id, "More than half of AI crawler traffic goes to re-fetching pages that have not changed")
    if cit.quality is Quality.SHORT:
        print("Warning: the quote is short, so re-verification accuracy may be low")

    # Re-verify every anchor not verified in the last 7 days
    report = ax.verify(older_than="P7D")
    for item in report.attention:
        print(f"[{item.state}] {item.url}\n  before: {item.before}\n  now: {item.after}")

    # Export Robust Links for report footnotes
    for link in ax.export_robust_links(report.anchor_ids, fmt="markdown"):
        print(link)
```

The CLI provides the same functionality.

```bash
anchor fetch https://example.com/report
anchor cite <doc-id|url> "quote"
anchor verify --older-than 7d
anchor list                                  # list of cached documents (v1.3)
anchor timemap <doc-id|url> --format link
anchor export --robust-links --format markdown
anchor stats
anchor gc --keep 20
anchor serve --transport stdio               # MCP server (v1.3; same as anchor-mcp)
```

**Error surface** (v1.10): a failure to open the store (corrupt DB, directory path, permissions, empty path) is wrapped at the store layer into the domain exception `anchor.errors.StorageError` (a subclass of `AnchorError`) — direct library use receives the same guarantee. Every CLI command carries the same error surface: `AnchorError`, `ValueError`, and `OSError` become one `실패: …` line and exit code 1, with no traceback — a traceback means "the tool is broken", and a user's typo does not mean that. `anchor serve --transport` accepts only `stdio|http` (all three entry points share one list), and `--older-than ""` is an error, not "no filter" — omitting an option and passing it empty are different things. The server entry points (`anchor-mcp`, `anchor serve`) report configuration and storage errors as a single stderr line and exit nonzero — spitting a traceback onto stdio makes an MCP client read it as a protocol error.

**The `close()` contract** (v1.10): `close()` releases resources **only after in-flight public API calls have finished** — the same order as the server shutdown path (§7.0: drain the workers, then release the store). Without that wait, a fetch in progress dies on a closed connection. When the grace period (10 seconds by default) elapses, it says so on stderr and **keeps waiting**: every public API call is finite (HTTP timeout and retry bounds §7.0, the per-anchor time budget §10), so giving up is the worse option. A call after `close()` is a `StorageError`, not a bare `sqlite3` exception on a closed connection, and closing twice is harmless (idempotent). If `close()` has returned, the store is closed — when another thread is closing, it waits for that release to finish before returning.

The entry point for registration with MCP clients is the console script `anchor-mcp` (v1.3).

---

## 9. Configuration

`~/.anchor/config.toml`. The environment variables `ANCHOR_*` always take precedence. **Secrets are not kept in the file.**

**Validation happens at load time** (v1.5). Syntax errors, type errors, and out-of-range values must fail at the moment the configuration is read — naming which key is at fault — not partway through the first fetch. Three cases in particular deserve attention.

| Trap | Rule |
|---|---|
| `enabled = "no"` | Reject a string where a boolean belongs. `bool("no")` is true, so letting it pass silently defeats §9's "explicit activation required" |
| `max_content_mb = 0.5` | Accept fractional values. Truncating to an integer makes the ceiling 0 bytes and every fetch fails |
| `requests_per_second = 0` | Zero is not "unlimited"; it is a division by zero. Accept only positive values |

All 26 documented keys are settable through **all three paths** — the TOML file, environment variables, and direct library use (`Config(...)`) — and receive the same validation (v1.10). Implementing only two of them while writing "always take precedence" is a mismatch between specification and implementation. The table below is the authority on keys, environment variables, and allowed ranges.

Values are validated **only against the final merged state** (v1.8). Validating the intermediate state after the file layer is applied means that, in a deployment where an environment variable is meant to override a bad file value, the server refuses to start at all — "always take precedence" collapses at the validation point. The validation itself runs at `Config` construction (including §5.4's UA rule), so direct library use receives the same guarantee.

**Unknown keys and unknown sections are warned about on stderr and ignored** (v1.10). Rejecting them would keep a configuration file that uses future keys from loading at all on an older version — breaking forward compatibility — while ignoring them silently lets a single typo (`timeuot_seconds`) fall back to the default without anyone knowing. A scalar or array where a section belongs (`fetch = 3`) and a non-UTF-8 file are a `ConfigError`. When `archive_fallback.enabled = true` with an empty `aggregator`, the fact that the public Wayback CDX will be used is announced on stderr — no quiet external dependency (§5.2).

```toml
[storage]
db_path        = "~/.anchor/store.db"
keep_versions  = 20
compression    = "zstd:6"

[fetch]
user_agent       = "Anchor/<release version> (+https://github.com/julgi80ai-stack/anchor-mcp)"
respect_robots   = true
timeout_seconds  = 30
max_redirects    = 5
max_content_mb   = 8
default_max_age  = 86400
retry_backoff_base = 1.0       # base of the retry exponential backoff (exposed in v1.10)
robots_ttl_seconds = 86400     # robots.txt cache lifetime — RFC 9309 §2.4 ceiling (exposed in v1.10)

[fetch.rate_limit]
requests_per_second = 1.0
burst               = 3

[fetch.archive_fallback]
enabled         = false        # explicit activation required — we do not quietly depend on external services
aggregator      = ""           # e.g. a self-hosted MemGator endpoint
archive_list    = ""           # JSON archive list to pass to the aggregator (git.io shutdown workaround)
timeout_seconds = 20
# Caution: when using MemGator as the aggregator, do not turn on --spoof.
#          It violates Anchor's honest client principle (§5.4).

[anchor]
context_chars       = 48
max_edit_ratio      = 0.15     # k = len(exact) * this value, max max_edit_distance
max_edit_distance   = 64       # absolute cap on k (exposed in v1.10)
hint_radius         = 500      # stage-1 hint search radius (exposed in v1.10)
min_quote_chars     = 12       # below this, creation is refused
short_quote_chars   = 32       # below this, a SHORT warning
time_budget_ms      = 200
max_document_bytes  = 2097152

[server]
transport = "stdio"   # stdio | http
```

**Keys, environment variables, allowed ranges** (v1.10 — this table is the authority; "the documented keys" means these 26):

| Key | Environment variable | Allowed range |
|---|---|---|
| `storage.db_path` | `ANCHOR_DB_PATH` | non-empty file path (`""` and `.` refused) |
| `storage.keep_versions` | `ANCHOR_KEEP_VERSIONS` | 1 – 2⁶³−1 (SQLite integer range) |
| `storage.compression` | `ANCHOR_COMPRESSION` | `zstd:N`, N in 1–22 (`none` and other codecs unsupported — one storage format) |
| `fetch.user_agent` | `ANCHOR_USER_AGENT` | RFC 9110 field-value: visible ASCII (+ inner SP/HTAB); leading/trailing whitespace, control characters, newlines, non-ASCII refused |
| `fetch.respect_robots` | `ANCHOR_RESPECT_ROBOTS` | bool |
| `fetch.timeout_seconds` | `ANCHOR_TIMEOUT_SECONDS` | finite > 0 |
| `fetch.max_redirects` | `ANCHOR_MAX_REDIRECTS` | 0 – 20 |
| `fetch.max_content_mb` | `ANCHOR_MAX_CONTENT_MB` | finite, over 0 up to 1,048,576 MB (1 TiB) |
| `fetch.default_max_age` | `ANCHOR_DEFAULT_MAX_AGE` | ≥ 0 |
| `fetch.retry_backoff_base` | `ANCHOR_RETRY_BACKOFF_BASE` | finite, (0, 60] — at 0 a failed host is hit again immediately (§5.4) |
| `fetch.robots_ttl_seconds` | `ANCHOR_ROBOTS_TTL_SECONDS` | 0 – 86400 (RFC 9309 §2.4 — never cached past 24 hours) |
| `fetch.rate_limit.requests_per_second` | `ANCHOR_RATE_LIMIT_RPS` | finite > 0 |
| `fetch.rate_limit.burst` | `ANCHOR_RATE_LIMIT_BURST` | ≥ 1 |
| `fetch.archive_fallback.enabled` | `ANCHOR_ARCHIVE_FALLBACK_ENABLED` | bool |
| `fetch.archive_fallback.aggregator` | `ANCHOR_ARCHIVE_AGGREGATOR` | URL or empty (public Wayback CDX — warned when enabled with it empty) |
| `fetch.archive_fallback.archive_list` | `ANCHOR_ARCHIVE_LIST` | string |
| `fetch.archive_fallback.timeout_seconds` | `ANCHOR_ARCHIVE_TIMEOUT_SECONDS` | finite > 0 |
| `anchor.context_chars` | `ANCHOR_CONTEXT_CHARS` | ≥ 8 (the minimum width of a stage-3 context marker — at 0, stage 3 dies wholesale) |
| `anchor.max_edit_ratio` | `ANCHOR_MAX_EDIT_RATIO` | (0, 1] — above 1, k exceeds the quote length and unrelated sentences become approximate matches |
| `anchor.max_edit_distance` | `ANCHOR_MAX_EDIT_DISTANCE` | ≥ 1 |
| `anchor.min_quote_chars` | `ANCHOR_MIN_QUOTE_CHARS` | ≥ 1, at most `short_quote_chars` |
| `anchor.short_quote_chars` | `ANCHOR_SHORT_QUOTE_CHARS` | ≥ 1 |
| `anchor.time_budget_ms` | `ANCHOR_TIME_BUDGET_MS` | > 0 |
| `anchor.hint_radius` | `ANCHOR_HINT_RADIUS` | ≥ 0 |
| `anchor.max_document_bytes` | `ANCHOR_MAX_DOCUMENT_BYTES` | ≥ 1024 (UTF-8 bytes — §6.2; a cap that cannot even hold a quote plus its context, 432 bytes worst case, makes every anchor permanently UNRESOLVED) |
| `server.transport` | `ANCHOR_SERVER_TRANSPORT` | `stdio` \| `http` |

---

## 10. Non-Functional Requirements

| Item | Target | Measurement method |
|---|---|---|
| Cache hit response | p95 < 15 ms (content ≤ 1 MB, **including under background matching load**) | Benchmark suite (idle + under load, v1.9) |
| Conditional request savings | 0 **body** bytes downloaded for unchanged documents — measured on `cache_hit` and direct 304. Re-confirming through a redirect alias may be nonzero because the 3xx hop's interstitial body is honestly counted (v1.10) | `fetch_log` aggregation |
| Anchor re-verification throughput | 500 anchors / 60 s (excluding network) | Benchmark |
| **Anchor matching worst case** | **p99 < 250 ms per anchor, no stalls** | Heavily reworked document scenario |
| **`UNRESOLVED` rate** | **under 1% on a normal corpus** | Golden benchmark |
| Memory | resident < 150 MB | Processing 100 consecutive 8 MB documents |
| Concurrency | Safe for multiple clients in a single process | WAL mode + serialization in the store layer |
| **Concurrency isolation** | **Work on one document must not block other documents or read-only tools** | **Per-URL lock (v1.5)** |
| Portability | Linux / macOS / Windows | CI matrix |

The reason the worst-case metric (row 4) was added in v1.1 is in §6.2. You have to look at the tail, not the average.

**The reach of the `UNRESOLVED` ratio (v1.7)**: the target in row 5 holds within §6.2's effective document-length limit (~190K chars at the default 200 ms budget). On larger documents, when stage 3 fails, stage 4 cannot reach a verdict — a larger budget widens the reach. Why the gate does not catch this is also recorded: the worst-case scenario's quote is short and takes the regex path only, and the normal corpus compares unrevised text against itself, ending at stage 1.

**Cache hits under load (v1.9)**: the target in row 1 must hold while a background task's matching loop is running — that is precisely the situation the Tasks extension exists for. The matching loop is pure Python and holds its whole GIL slice, while the cache-hit path must acquire the GIL dozens of times, so the waiting accumulates per acquisition (measured at 6–14× over the gate). The background worker's matching loop yields the GIL at its budget checkpoints, and the actual sleeping is bounded three times over: it sleeps **only while a foreground call is actually in flight** (a yield with nobody starving is pure loss — the true cost of sleeping, cold-cache rewarming, cannot be refunded); **the slept time is not charged to the anchor budget** (charging it makes the same anchor's verdict differ between the sync path and the task path — the effective document-length limit of §6.2 must not depend on the call path); and that refund is **capped at 15% of the budget** — an uncapped refund makes "one anchor's budget" elastic in proportion to contention and breaks this table's p99 bound (measured up to 971 ms per anchor). Sleeping past the cap eats the budget, and borderline verdicts are held back as `UNRESOLVED` — an honest report of measured contention, recoverable by re-verification. The benchmark measures three conditions: idle; cache hits under matching load (verifying the load itself survived — rounds overlapping the sample window — since a dead churn thread would measure idle and print PASS); and **background matching under foreground contention** (verifying foreground validity, and gating the contention p99 plus **credit actually granted** — how much of the slept time was actually repaid, exactly 0 if the refund is lost. A measurement that merely switches polite mode on without opening a foreground section is byte-identical to the foreground run: a tautology. The contention's verdict cost — the `UNRESOLVED` increment — is reported as information, not gated (v1.10): starvation under a continuous foreground is the honest reporting mandated above, so healthy code saturates it on an idle machine, while on a loaded machine the foreground baseline saturates too and the increment collapses to 0. A lost credit does not extend the deadline, so it *lowers* p99 — that regression is caught only by the granted amount).

**Concurrency isolation (v1.5)**: Wrapping every tool in a global lock is safe, but it stops all the other tools while a background batch verification runs — directly at odds with the purpose of the Tasks extension. Serialization is confined to the minimum necessary scope: the store serializes internally, and the service locks only the "look up → decide → create" section for the same URL, at per-URL granularity. **The reason the serialization responsibility sits in the library layer rather than the server** is that someone using the library directly must get the same guarantee.

"Per-URL" here means **one lock per URL, not a hash approximation** (v1.10). Once no holder or waiter remains, that lock is removed from the registry, so locks do not accumulate without bound. Approximating with a fixed set of stripes collides once there are barely a dozen URLs (the birthday problem), and since the lock is held across the entire network round trip (timeout included), **an unrelated document is blocked for exactly that long** — measured at 7.92 seconds. The isolation in this table is a requirement, not a performance preference.

---

## 11. Project Structure

```
anchor-mcp/
├── pyproject.toml
├── README.md              # includes prior-art credits (§14)
├── LICENSE                # full text of Apache-2.0
├── NOTICE                 # copyright notices and the stated dual-license election
├── THIRD-PARTY.md         # license ledger for third-party components
├── src/anchor/
│   ├── __init__.py
│   ├── config.py          # configuration loading and validation (stdlib tomllib, v1.4)
│   ├── models.py          # domain dataclasses
│   ├── errors.py          # exception hierarchy
│   ├── fetcher/
│   │   ├── client.py      # httpx-based conditional GET
│   │   ├── robots.py      # robots.txt cache and determination
│   │   ├── ratelimit.py   # per-host token bucket
│   │   └── archive.py     # Memento aggregator / CDX fallback
│   ├── normalize/
│   │   ├── extract.py     # content extraction + markdown conversion
│   │   ├── text.py        # Unicode/whitespace normalization
│   │   └── hashing.py     # blake3 wrapper
│   ├── anchoring/
│   │   ├── selector.py    # anchor creation, quality determination
│   │   ├── matcher.py     # 4-stage matching
│   │   ├── approx.py      # approximate string search (regex / Myers)
│   │   └── budget.py      # time budget management
│   ├── export/
│   │   ├── timemap.py     # RFC 7089 serialization
│   │   ├── robustlinks.py # Robust Links serialization
│   │   └── diff.py
│   ├── store/
│   │   ├── schema.sql     # full schema for new DBs (currently v6)
│   │   ├── migrations/    # incremental SQL. Existing DBs catch up through these
│   │   └── repository.py  # the sole SQL access point (includes internal serialization)
│   ├── service.py         # public facade (the Anchor class)
│   ├── server.py          # MCP tool registration, Task lifecycle
│   └── cli.py
├── tools/
│   └── audit_licenses.py  # license audit (run before releases and quarterly)
├── tests/
│   ├── unit/
│   ├── integration/       # uses a local HTTP fixture server
│   └── fixtures/          # real HTML snapshots (reproducible without network)
└── benchmarks/
    ├── anchoring/         # based on Hypothesis public annotation data
    └── fetch/
```

### 11.1 Runtime Dependencies

Licenses are as verified on 2026-08-16; the full ledger is in `THIRD-PARTY.md`. **All are permissive and compatible with Apache-2.0 distribution.**

```toml
[project]
dependencies = [
    # Content extraction. License lower bound 1.8.0 — below that it is GPLv3+, which
    # conflicts with Apache-2.0 distribution. Do not lower this bound. The functional
    # lower bound is 1.9.0 — markdown output (output_format="markdown") was added in
    # that version (v1.3).
    "trafilatura>=1.9.0",       # Apache-2.0
    "readability-lxml",         # Apache-2.0  (extraction fallback)
    "lxml",                     # BSD-3-Clause
    "markdownify",              # MIT
    "regex",                    # Apache-2.0 AND CNRI-Python  (see §11.2)
    "httpx",                    # BSD-3-Clause
    "protego",                  # BSD-3-Clause  (robots.txt)
    "charset-normalizer",       # MIT
    "blake3",                   # CC0-1.0 OR Apache-2.0 → Apache-2.0 elected
    "zstandard",                # BSD-3-Clause
    "pypdf",                    # BSD-3-Clause
    "typer",                    # MIT
    "mcp",                      # MIT
]
```

Configuration loading is implemented with the standard library (tomllib + dataclass) — `pydantic-settings` is not used (v1.4). `pydantic` is included in the distribution only as a transitive dependency of the `mcp` SDK, and the transitive list (beautifulsoup4, soupsieve, etc.) is managed by `THIRD-PARTY.md`. The license policy for development-only dependencies (pytest, hypothesis, etc.) is also in `THIRD-PARTY.md` §2.1.

### 11.2 Dependency-Related Constraints

| Item | Content |
|---|---|
| **`trafilatura` lower bound** | Switched from GPLv3+ to Apache-2.0 in v1.8.0. Without a lower bound there is a license conflict. Verified in CI (§12) |
| **`regex` compound license** | `Apache-2.0 AND CNRI-Python`. Irrelevant to Apache-2.0 distribution, but **it closes off the path of relicensing Anchor under GPLv2.** If removal becomes necessary, it can be replaced by the in-house Myers bit-vector implementation of §6.2 |
| **`blake3` dual license** | Of `CC0-1.0 OR Apache-2.0`, **Apache-2.0 is elected**. Stated in `NOTICE` |
| **External processes** | MemGator (MIT) is not a dependency but a separate service the user self-hosts. It is not included in the distribution |

> Changes from v1.0: `rapidfuzz` removed, `regex` added. A consequence of the change in the approximate matching approach (§6.2).

---

## 12. Test Strategy

| Layer | Target | Method |
|---|---|---|
| Unit | Normalization, hashing, the 4 stages of anchor matching | Pure functions, no network |
| Golden | **36+** real HTML snapshots → expected content | Fixture comparison. Prevents extractor regressions. **A test asserts the corpus actually exercises the normalization rules** (v1.6) |
| Mutation | Programmatically apply ad insertion, paragraph moves, and sentence edits to a source and verify the state determination | At least 5 cases for each of the 7 states |
| **Anchor benchmark** | **The real quote set from Hypothesis public annotation data** | **Success rate + p99 latency. CI fails on regression** |
| **Worst case** | **Long document + short common quote + heavy rework** | **Whether it returns `UNRESOLVED` without stalling** |
| Integration | 304, redirects, 402, 429+Retry-After, timeouts, archive fallback | Local fixture server |
| Interoperability | Validate a generated TimeMap with an external Memento parser | Confirm RFC 7089 compliance |
| Property | For arbitrary text, `cite → verify(same version) == INTACT` | Hypothesis (the library) |
| **Copy-and-cite** | **Whether a sentence copied off the screen is found in the stored content** (formatting, footnotes, NBSP, typesetting line breaks, PDF, **selections spanning several blocks**) | **Real HTML/PDF input (v1.5, v1.6)** |
| **Language equity** | **Whether a revision of the same character resolves to different states depending on the language** | **English/Korean/Japanese/Chinese comparison (v1.5)** |
| **Redirects** | **Per-hop robots determination, alias cache hits, conditional requests preserved** | **Local fixtures (v1.5)** |
| **Concurrency** | **Document duplication under concurrent fetches of the same URL, parallelism across different URLs, read responsiveness during a batch** | **Thread load (v1.5)** |
| **Atomicity and crashes** | **Reopening after an interrupted migration, integrity under threaded access, upgrading an older database that contains rows** | **Separate process (v1.5) · v1–v4 fixtures with rows (v1.6)** |

**Coverage targets**: 95% or above for `anchoring/` and `normalize/`. 80% or above for the rest.

> **What the audit taught us (v1.5)**: Even with all of the layers above in place and the coverage targets met, 52 demonstrated defects came out. What they had in common was **fixtures that only walk the happy path** — servers without redirects, HTML whose paragraphs are a single line, single-line PDFs, a single thread. If the fixtures do not include the ordinary conditions of reality (redirects, typesetting, duplicate paragraphs, concurrent calls), no coverage figure can cover that hole.

The anchor benchmark is not a reference; it is a **CI gate**. Since the reason for the change in §6.2 was performance, failing to block performance regressions in code means falling into the same trap again. However, grade-C data is collected at runtime and therefore needs the network, so it runs **optionally** (manual trigger or scheduled run), and when it runs it acts as a gate (v1.4, consistent with the outcome section of ADR-0002). The §10 metrics that need no network (cache hit latency, worst case, `UNRESOLVED` rate) are measured by `benchmarks/run_micro.py`.

### 12.1 Fixture Provenance Policy (new in v1.2)

Test data carries a different kind of constraint than code. Web annotations are user-generated content, and the original web pages are the copyrighted work of each site. The moment they are committed to the repository, that is redistribution.

| Grade | Use | Data source | Committed to the repository |
|---|---|---|---|
| **A** | Golden (content extraction accuracy) | Public domain, CC-licensed documents, self-owned content | ✅ committed |
| **B** | Mutation (7-state determination) | Synthetic data produced by programmatically applying ad insertion, paragraph moves, and sentence edits to grade-A documents | ✅ committed |
| **C** | Anchor benchmark (performance) | Public annotation data collected at runtime | ❌ **script only is committed** |

Not committing grade C is the crux. The way Hypothesis's `anchoring-test-tools` works — taking a URL list as input — is exactly this pattern, and we follow the same structure.

Of the 30 grade-A fixtures, **at least 10 are Korean documents** (see the §14 risk note).

### 12.2 License Gate (new in v1.2)

License violations cannot be prevented by human memory. Transitive dependencies are not even visible, and licenses do actually change — trafilatura did.

| Check | Failure condition |
|---|---|
| Copyleft block | The strings GPL / AGPL / LGPL / SSPL / BUSL appear in the `pip-licenses` output |
| `trafilatura` lower bound | The installed version is below 1.8.0 |
| `NOTICE` synchronization | A new runtime dependency is absent from `THIRD-PARTY.md` |

It runs in CI on every push and PR, and is made locally reproducible through `tools/audit_licenses.py`.

---

## 13. Roadmap

| Version | Scope | Completion criteria |
|---|---|---|
| **v0.1** | fetch + hashing + change detection. CLI only | Calling the same URL twice, the second call downloads zero network bytes |
| **v0.2** | Anchor creation and re-verification, version retention, approximate matching + budget | Mutation tests pass for all 7 states + no stall in the worst case |
| **v0.3** | MCP server, 9 tools, Tasks extension | **Replacing `mcp-server-fetch` with Anchor in Claude Desktop causes no inconvenience** |
| **v0.4** | diff, gc, stats, PDF, TimeMap/Robust Links export | hit_rate of 80% or above after 30 days of real use |
| **v0.5** | Archive fallback | Obtain a case where checking an archive before a `GONE` determination actually rescues a citation |
| **v1.0** | Documentation, migrations, CI matrix, stable schema | Every item of this specification satisfied |
| *v1.3 candidates* | Web Bot Auth signatures, shared cache export, an anchor portability format | — |

The completion criteria for v0.3 changed in v1.0. This is because a **drop-in replacement for `mcp-server-fetch`** is the most realistic adoption path. That server is the most widely used fetcher in the MCP ecosystem, but it has no cache, no versions, and no provenance. Put those on top of the same interface and the user only has to change one line of configuration.

**The shared cache export in v1.3 is the one point where this comes close to a "standard."** It would let signed version snapshots and anchors be exchanged with other users. But it only means anything after v1.0 is actually in use, so it is deliberately deferred.

---

## 14. Credits (mandatory in the README)

Anchor stands on the following work. It is stated in the README and in the documentation.

- **Memento — RFC 7089**, Herbert Van de Sompel, Michael L. Nelson, Robert Sanderson, et al. The original work that introduced a time dimension to the web.
- **W3C Web Annotation Data Model** — TextQuoteSelector / TextPositionSelector.
- **Hypothesis** — the practical implementation of multi-stage fuzzy anchoring, and the public documentation of its failure modes.
- **Klein, Van de Sompel, Jones et al.** — empirical research on reference rot and content drift (PLOS ONE 2014, 2016).
- **Robust Links Specification** — the citation notation convention.
- **Sawood Alam, Michael L. Nelson** — MemGator. The self-hostable aggregator that keeps Memento usable after the central service disappeared.
- **Robert Knight** — `anchor-quote`. The work that showed with benchmarks why approximate matching beats `diff-match-patch`. The design rationale for §6.2.

The grounds for these decisions are in `docs/decisions/0001` D1 and `docs/decisions/0002`.

This is not a formality. There are people in this field who have held on to this problem for 20 years, and reimplementing their terminology and standards under a different name is a loss both technically and strategically.

---

## 15. v1.9 → v1.10 Change History

Reflects stage 6 (accounting, configuration, CLI — clusters 9–11) of the remediation for the defects demonstrated in the second parallel audit, plus what was found along the way. Most items in this section are places where **the numbers the tool reports about itself** had drifted from the truth.

| # | Section | Change | Underlying defect |
|---|---|---|---|
| 1 | **10** | The contention gate's verdict was replaced with **credit actually granted** (how much of the slept time was actually repaid) — the UNRESOLVED-increment gate is gone. Background starvation under a continuous foreground (UNRESOLVED saturation) is not a defect but the honest reporting §10 itself mandates | The increment gate measured one thing and hunted another: on an idle machine healthy code scores 100/100 (foreground baseline 0), so the gate could never pass; on a loaded machine the baseline saturates too, so the delta collapses to 0 and the gate passes while catching nothing — the stage-5 close PASS was the latter. A lost credit does not extend the deadline, so it *lowers* p99 and slips past the p99 gate as well (measured on a neutered copy: p99 202 ms PASS, 0 granted). Whether the debt was repaid is measured by the amount repaid (D-210) |
| 2 | **7.7** | New accounting invariant: **one user call = one `fetch_log` row** (outcome is the final result), and the displayed breakdown sums to requests. A failure that ends in an exception is still one `error` row; an archive rescue is one `archive` row | Failures ending in exceptions (timeout, network, redirect limit, size cap, extraction failure, robots refusal) left no row at all, so hit_rate depended on the kind of failure (measured 2.67× inflation — poisoning the very instrument of the v0.4 observation window), and a call rescued after an unavailable robots verdict left error+archive rows, inflating the denominator and the error count at once (D-130, D-133) |
| 3 | 7.7 | `bytes_down` counts **all traffic the call actually downloaded** even when it ends in failure (redirect-hop interstitial bodies, robots.txt, futile archive lookups included) | When robots refused mid-redirect, the body bytes of the hops already followed were lost wholesale with the exception, and the bytes of an archive lookup that ended in "no memento" were recorded nowhere (D-134, D-135) |
| 4 | 7.7 | `bytes_saved_estimate` measures against **the body the origin is serving now** (`documents.current_version`); falls back to the last observed version when the pointer is absent | Measuring by the max-captured_at version evaluated every saving against a version no longer served for reverted and archive-rescued documents — with no bound on the inflation (measured 1220×). The "current body" pointer §4.1 introduced was used everywhere except accounting (D-131) |
| 5 | 7.7 | New `disk_bytes` definition: actual occupancy (main+wal+shm), with a WAL checkpoint attempted right before measuring (reported as-is when in concurrent use) | The WAL never shrinks after a checkpoint, so a long-running process reported 13.5× the real load, and VACUUM rewrites the DB into the WAL so disk_bytes *grew* right after gc (D-132) |
| 6 | 7.7 | Added the `archive` bucket to the displayed breakdown | outcome='archive' entered the denominator but no displayed bucket, so the breakdown did not sum to the request count (D-136) |
| 7 | 13 | The completion criterion "0 bytes downloaded" is narrowed to **0 body bytes** (measured on cache_hit and direct 304) | Re-confirmation through a redirect alias honestly counts the 3xx hop's interstitial body, so it is nonzero — a corollary of accounting invariant 2 (fallout of D-134) |
| 8 | **9** | **All 26** documented keys settable through all three paths (TOML, environment, `Config(...)`), with an authoritative key/env/range table. `storage.compression` (`zstd:N`) implemented; the four previously unsettable fields (`retry_backoff_base`, `robots_ttl_seconds`, `max_edit_distance`, `hint_radius`) exposed | Seven environment variables were missing; `compression` existed only in the example without even a Config field (compression itself always ran at hardcoded level 6 — only the knob was fake); four fields could be changed through no path at all. §9 itself had written "implementing only two while writing 'always take precedence' is a mismatch" (D-137) |
| 9 | 9 | Unknown keys and sections are **warned about on stderr and ignored** (forward compatibility); `enabled=true` with an empty `aggregator` warns that the public Wayback CDX will be used | A typo (`timeuot_seconds`) silently fell back to the default, and a quiet external dependency existed (D-152, D-151) — policy approved by the user on 2026-08-19 |
| 10 | 9, 5.4 | The UA is validated at load time as an **RFC 9110 field-value** (visible ASCII; leading/trailing whitespace, control characters, newlines, non-ASCII refused) | A UA containing a newline made httpx's `LocalProtocolError` get swallowed as "robots unavailable", reporting **every URL as refused by the site owner** (misattributing a local typo — with archive fallback on, every fetch quietly became an archive bypass); non-ASCII died mid-first-fetch with a traceback (D-139, D-145) |
| 11 | 9 | Validation extended: section types, non-UTF-8 files, finiteness (`inf`/`nan`), ceilings (`max_content_mb` 1 TiB, `max_redirects` 20, `keep_versions` 2⁶³−1), relations (`min_quote_chars ≤ short_quote_chars`), empty `db_path`, `max_edit_ratio (0,1]`, `context_chars ≥ 8`, `max_document_bytes ≥ 1024` | `fetch = 3` raised a bare `AttributeError`, non-UTF-8 a bare `UnicodeDecodeError`, `inf` a bare `OverflowError` killing every CLI and the server; `1e300` MB, a ratio of `5.0`, a context of 0, and a retention of 2⁶³ all passed silently and surfaced as first-fetch deaths, unrelated sentences presented as ALTERED, wholesale stage-3 loss, and gc tracebacks (D-140, D-141, D-142, D-143, D-144, D-146, D-148, D-151) |
| 12 | **6.2** | `max_document_bytes` is measured in **UTF-8 bytes** as its name says (truncation on a character boundary; §6.3's withholding rule unchanged), and **k is clamped below the quote length** | Character-count comparison let Hangul pass at up to 3× the documented cap (D-153), and with k ≥ len a deleted CJK quote was presented with an empty string as its "current form" (`ALTERED, found_text=''`) — the configuration cap alone cannot hold the boundary once the writing-system factor of 2.5 is multiplied on (D-143) |
| 13 | **8** | Every CLI command carries the same error surface (`AnchorError`, `ValueError`, `OSError` → one line + exit 1); store-open failures are the new public exception **`StorageError`**; `serve --transport` validates against the list all three entry points share; `--older-than ""` is refused; `older_than` finiteness is also validated at the service-layer common gate | Corrupt DB, directory, or empty path × 8 commands = all 40 combinations tracebacks (D-138); only `gc --keep 0` diverged into a traceback (D-147); `nan`/`inf` durations died in `timedelta` (D-149); a serve typo quietly started stdio (D-150); an empty `--older-than` became a full re-verification (D-154) |
| 14 | **10** | "Per-URL lock" means **one lock per URL, not a hash approximation**; a lock with no holder or waiter is removed from the registry | Fixed 64 stripes collide with barely a dozen URLs (the birthday problem), and the lock is held across the whole network round trip — an **unrelated document waited 7.92 seconds**. The specification had written this isolation down as a requirement while the implementation approximated it and the comment claimed it was "only a performance cost" (D-128) |
| 15 | **8** | New `close()` contract: wait for in-flight public calls before releasing (after a 10-second grace, say so and keep waiting); later calls are `StorageError`; double and concurrent close are idempotent, and a returned close guarantees the store is closed | `close()` neither waited nor blocked, so when `__exit__` finished ahead of a worker the in-flight fetch died with `Bad file descriptor` and every later call leaked a bare `sqlite3.ProgrammingError` (1,594 of them). D-034 had covered only the server path, leaving the direct-library path (§8) unguarded (D-129). `StorageError`'s reach widens from "cannot open" to "access after close" |
| 16 | **5.2** | The `statuscode` in a CDX response is **re-checked** — take the most recent 200 row; if the column is absent, abandon the rescue and report gone truthfully | Requesting a filter and the response honouring it are different things. On a mirror that ignores the filter, **an archived 404 notice became "the rescued body"** and a quote on top of it was asserted MISSING (D-092) |
| 17 | **5.2, 4.1** | The meaning of `documents.robots_allowed` is settled: it holds only whether the owner's rules block the URL. The verdict is attributed to **the document of the blocked hop**, `unavailable` is not recorded, and it returns to `true` once allowed | A destination host's refusal was recorded against the input URL's document, leaving a site that forbade nothing marked as forbidding; a transient 5xx froze in like an explicit denial; and no call site anywhere set it back to `true`, so it stayed False forever (D-097) |
| 18 | **7.2, 7.3** | `source` in the `cite` response; `sources` (summing to checked) and `attention[].source` in `verify_citations` | §5.2 requires that archive confirmation is **always** knowable, yet it existed only in `fetch_document` and `get_version` — a 404 origin compared against an archive **reported nothing but `INTACT`**. Attaching it only to attention loses the fact in an all-INTACT report, hence the report-level tally as well (D-093) |
| 19 | **7.0** | Cursor pagination for `tasks/list` (50 per page, opaque cursor, uuid7 ordering, item-based cursor, `-32602` for a bad cursor) | The cursor was never read and everything was returned with no `nextCursor` — a violation of MCP's pagination contract, and on a long-running server the response grows with the list (D-054) |
| 20 | **7.4** | In `latest~N`, N is a **non-negative decimal integer** only (anything else is an argument error), and `context_lines` must be ≥ 0 — both checked at the common gate **before** version references are resolved | `latest~-1` returned the **oldest** version without objection (a negative passed the range guard into Python's negative indexing, D-085), and `latest~abc` leaked a bare `ValueError` with Python's internal message to the MCP client (D-086). A negative `context_lines` was caught only deep inside the exporter, so the user got "no such version" instead of the real cause (D-211) |

## 16. v1.8 → v1.9 Change History

Reflects stage 5 (Tasks and the server, cluster 8) of the remediation for the defects demonstrated in the second parallel audit. Every item in this section is a place where **what the protocol promises and what the server actually does** had drifted apart — the default call broke, shutdown lost data, or cancellation did not cancel.

| # | Section | Change | Underlying defect |
|---|---|---|---|
| 1 | **7.0** | New Task **ttl policy**: an omitted ttl gets the server default of 30 minutes; zero and negative values are rejected (-32602); oversized requests are clamped to 24 hours with the actual value reported. The ttl key is never omitted from any response | `Task.ttl` is required-nullable, and exclude_none serialization drops the key when it is None — so for the **protocol-default call** that omits ttl, every response (the in-band descriptor, tasks/get, tasks/list, tasks/cancel) failed schema validation in a standard client, and a ttl-less task was skipped by prune and accumulated forever (D-116) |
| 2 | 7.0 | Retention is counted **from creation** as the protocol defines, but is updated at termination to the actual retention (elapsed + requested) | The implementation used last_updated_at (D-123). The naive correction would make a task whose run outlasts its ttl lose its result the moment it terminates — since Task.ttl is the "actual retention duration," updating and reporting it is exactly the server discretion the protocol anticipates |
| 3 | **7.0** | Shutdown guarantee: shutdown returns **only after every worker has finished**, and the store is closed after that. Both entry points (`anchor-mcp`, `anchor serve`) share one shutdown path | When the grace period ran out, shutdown returned silently with workers alive → the workers died under a closed store and **partial results were lost wholesale** (D-117). `anchor serve` never called worker cleanup at all (D-118) |
| 4 | 7.0 | The cancel/shutdown signal is checked **between anchors** as well as between documents — the reaction bound is one anchor's budget plus one document's **entire fetch** in flight (robots, hops, retries, fallback), and a cancel overlapping that window may answer non-terminal | Reaction time grew without bound in proportion to one document's anchor count (13.8 s after the signal at 100 anchors) (D-119). Both drafts — "one anchor's budget" and "one request" — were untrue: one document's fetch measured 7 requests (D-206), and a cancel overlapping a wait past the 5-second join threshold returned working (D-197, D-205) |
| 5 | **10** | The cache-hit p95 gate must hold **while background matching is running**. The benchmark measures the loaded scenario as well, and the background worker's matching loop periodically yields the GIL (foreground calls do not pay that cost) | During a background verify, p95 exceeded the gate 6–14×, but the benchmark only measured the idle state, so CI passed — it broke precisely in the situation the Tasks extension exists for (D-120) |
| 6 | 7.6 | `list_documents`' status is case-folded, then validated against the enumeration; anything else is rejected with the list of allowed values | `LIVE` returned 0 rows and a typo returned an error-free empty list — which a caller reads as "the cache is empty" (D-121) |
| 7 | 7.3 | `time_budget_ms` accepts **positive values only** — rejected at the common choke point shared by the sync tool, the task path, and the CLI | Zero and negative values silently skipped matching stages 3–4, demoting genuinely revised quotes from `ALTERED` to `UNRESOLVED`. Only the config-file path was validated (D-122) |
| 8 | **10** | A background worker's yield sleeps **only while a foreground call is in flight**, and slept time is not charged to the anchor budget | The yield cost was charged inside the budget, so the same anchor was MISSING on the sync path and UNRESOLVED on the task path (worst case 0→59–60/100; crediting alone still left 30/100 — the cold-cache cost of sleeping cannot be refunded). Since §7.3 makes the task the default path, the lower limit became the default, and the verdict gates ran foreground-only so they never saw it (D-196) |
| 9 | 10 | The under-load gate verifies the load itself survived — counting rounds **overlapping the sample window**, not the total | A churn thread dying instantly on an import error still produced PASS and exit 0 — measuring idle while claiming load; a regression that breaks the churn side would disarm the very gate meant to catch it (D-198). A total count mistakes a churn that ran only before the samples for load (D-208) |
| 10 | 7.0 | The ttl cap (24 hours) is stated to apply to the **post-termination retention** — a long run's total retention from creation may exceed it | Combining the cap sentence with the termination update made the cap not a cap (24 h requested, 6 h run → 30 h reported) (D-200) |
| 11 | **10** | The credit (refund of slept time) is **capped at 15% of the budget** | An uncapped refund makes "one anchor's budget" elastic in proportion to contention: measured up to 971 ms per anchor (4.9×), service-layer p99 345/478 ms — violating the 250 ms p99 bound. The trade — verdict path-independence (D-196) bought by giving up bounded time — was written down nowhere (D-203) |
| 12 | 10 | New **foreground-contention bench** for the matching gates: foreground validity, contention p99, and the UNRESOLVED increment over the foreground baseline | The "polite-mode gates" never opened a foreground section, making them byte-identical to the foreground run (0 sleeps across 52,800 checkpoints) — a tautology. The failure mode of D-120 ("measuring in a batch that is not the real one") recurred inside the gate itself (D-202) |

## 17. v1.7 → v1.8 Change History

Reflects the robots and redirect-identity portion of **stage 4 (the honest-client contract, cluster 2)** of the second parallel audit's remediation, plus the audit of that stage's own remediation code. Every item in this section is a place where **the declaration and the reality had drifted apart** — because a document said we never bypass anything, nobody checked, and so nobody noticed for a long time that ordinary configurations made the rules vanish wholesale.

| # | § | Change | Underlying defect |
|---|---|---|---|
| 1 | **5.2** | robots.txt **3xx is followed** (at least 5 hops, RFC 9309 §2.3.1.2) | Any `status != 200` discarded the body, so http→https moves, CDN migrations, and canonical cleanups ignored even `Disallow: /` and took the whole site. That verdict was cached for 24 hours |
| 2 | 5.2 | Leading **BOM ignored** (RFC 9309 §2.3) | In robots.txt saved by Windows editors the parser failed to read `User-agent:` as a directive and discarded the entire rule group |
| 3 | 5.2, 5.4 | **Size cap and timeout apply to robots.txt** too | A 20MB robots.txt landed wholesale in the cache DB, and a configured 1-second timeout waited 10 seconds. §5.4 already said "every response" |
| 4 | **5.4** | Blank User-Agent **rejected at configuration time**; validation moved to `Config` construction | Requests went out with an empty UA and robots matching ran on an empty token. Validation only happened via `load_config`, leaving the direct-library path (§8) uncovered |
| 5 | **5.2** | The **URI-M an aggregator points at also gets a robots verdict** | A path that direct fetching blocks as `explicit` could be reached through one layer of aggregator — "no bypass of any kind" was words only |
| 6 | **5.1** | Only permanent redirects (301·308) change the canonical URL | A 302 interstitial URL (consent wall, region gate) hardened into the Memento URI-R and never came back after the wall lifted |
| 7 | 5.1 | An old alias that starts serving its own content retires the alias | When an alias stopped redirecting, `final_url == norm_url` meant the correction branch never ran, and **another resource's body** was pushed into the target document's history |
| 8 | 5.1 | Two documents unified by a permanent redirect are merged | A's anchors were checked against B's body while the verification record held another document's version id — the audit trail contradicted itself |
| 9 | 5.1 | Document and version creation made **idempotent** | The lock key is the input URL but creation uses the final URL, so canonical redirects raised bare `sqlite3.IntegrityError` to the caller (reproduced 25/30) |
| 10 | **5.4** | Validators ride **only on the canonical-URL hop** | A destination honestly returning 304 was read as "nothing changed", the old body kept being served as current, and the move was never detected. ETags leaked to other hosts |
| 11 | 5.4 | `https → http` downgrade redirects refused | A body received over a channel with no integrity guarantee became citation evidence and the canonical URL was recorded as plaintext |
| 12 | **5.1** | The "serving its own content" test corrected to **`final_url == norm_url`** (a 200 with no redirect) | Testing "not permanent" instead meant an alias reached through a *temporary* redirect was destroyed, a ghost document holding the destination's body was created, and the recovery-phase merge planted that body in the target's history (D-183, the mirror image of #7) |
| 13 | 5.1 | Merge rules strengthened: identical (body, source) keeps the **earliest capture**, observation sequence is **renumbered**, and accounting (fetch_log) moves too | Deleting either side changed a citation's Memento-Datetime and URI-M (D-186), duplicate sequence numbers made `latest~N` show transitions that never happened (D-184), and accounting shrank (D-195) |
| 14 | 5.1 | Access after a merge removed the document row answers with a **domain error / re-tracking** | `merge_document` is the only path that deletes from documents — another process's verify died with AssertionError and cite with a bare IntegrityError (D-185) |
| 15 | 5.1 | A registered document that moves: **the canonical URL moves and the old address becomes an alias** | With no document at the destination, documents.url stayed at the old address, so #6 held only for new documents (D-194) |
| 16 | 5.4 | Validator hop comparison in **normalized form** | Raw string comparison meant query ordering, tracking parameters, or a fragment made conditional requests permanently inert (D-187) |
| 17 | 5.2 | robots: **declared charset honoured**, boundary chunk **kept**, unfollowable 3xx is **rules-unavailable** | Hardcoded utf-8 wiped out UTF-16 rules (D-190), discarding the boundary chunk turned cap < chunk into a fully-open allow (D-191), and a Location-less 3xx was cached as unlimited allow for 24h (D-192) |
| 18 | **5.1** | URL normalization: RFC 3986 **unreserved percent-decoding**, uppercase hex, **valueless parameters preserved** | `/~user/` and `/%7Euser/` registered as duplicate documents, and `?novalue`→`?novalue=` sent the server **a request different from what the user gave** (D-105) |
| 19 | 5.1 | Invalid input raises `InvalidURL` (a domain exception) | Port errors leaked as bare ValueError (D-107), and scheme-less input was reported as "robots.txt refused" — **pinning the user's typo on the site owner** (D-108) |
| 20 | 5.2 | An unfollowable Location (`mailto:` etc.) is that document's `FetchFailed` | `httpx.InvalidURL` is not a subclass of HTTPError, so it escaped the hierarchy and killed the whole re-verification batch (D-106) |
| 21 | **9** | Configuration is validated **only in its final state** — one construction after layering | Mid-state validation broke "environment variables always win": with a bad file value that env was deployed to override, the server refused to start (D-188) |

## 18. v1.6 → v1.7 Change History

Reflects **stage 3 (judgment accuracy, cluster 5)** of the remediation for the defects demonstrated in the second parallel audit (10 Opus auditors, 2026-08-18). Every item in this section belongs to the "silently wrong judgment" family — a false MISSING declares a living quote dead, and a false ALTERED presents a lookalike from another section as "the current form of your quote". The user has no way to check either.

| # | § | Change | Underlying defect |
|---|---|---|---|
| 1 | **6.2** | The core scan returns **every position within distance k** as a candidate | Keeping only the global minimum per core meant that in the ordinary edit where a summary carries the quote's head and a pull-quote its tail, both cores' optima landed on decoys and the true position was never even examined (3,856 false MISSING out of a 4,000-case decoy campaign) |
| 2 | 6.2 | When stage 3 **hits its candidate cap it defers to stage 4** instead of committing | Committing to the best of 32 meant that if the real revision was 38th, an earlier sibling paragraph was reported as `found_text` — the failure mode D-044 fixed, with its boundary merely moved from 1 to 32 |
| 3 | 6.2 | Stage 3 also refuses to commit when the budget runs out (`UNRESOLVED`) | Same reason. A best chosen without seeing everything is not evidence |
| 4 | **6.2** | A **context-corroboration gate** on stage 4 results | A template sentence from the v1.9.0 section (opposite in meaning) was attached to a deleted v2.0.0 entry as its "current form". Similarity cannot decide this — if the context is alive in the document and the quote is not beside it, that is deletion |
| 5 | 6.2 | Candidate selection **prefers the corroborated side** | A risk newly created by #1's wider candidates: in terms-of-service documents, an appendix's boilerplate is often closer to the original than the revised body |
| 6 | 6.2 | The writing-system factor on the edit-distance cap became a **continuous function** | Just below the "2.5× if majority" step was always wrong. Japanese and Chinese sentences with Latin abbreviations, years, or percentages routinely fall under density 0.5 (`GDPは3.2%増加した。` is 0.462) |
| 7 | 6.2 | The budget check period is measured in **DP cells** | With a per-row period the overshoot grew with quote length — a 68,902-char quote spent 1,133ms against a 200ms budget (§10 violation), reachable through the public API since `cite` has no length cap |
| 8 | 6.2 | v1.5's "the two paths agree" claim **corrected** | That differential comparison only exercised decoy-free inputs. It becomes true only after #1 |
| 9 | 6.2, 10 | Stage 4's **effective document-length limit (~190K chars)** made explicit | 2MB is the cap on the search range, not what the budget can sweep. The spec never stated that the size it permits and the size the budget permits are different |
| 10 | **6.3** | Truncation holds `ALTERED` back too | "Do not assert what you could not fully see" applied only to `MISSING`. A quote straddling the cut had its severed tail counted as edit distance — the original intact, yet the severed fragment presented as its "current form" |
| 11 | 7.3 | `position_hint` and `found_offset` added to `attention` | From edit distance alone the caller cannot tell an in-place revision from a paragraph that came from another section |
| 12 | **4.1** | **Schema v6** — `versions.last_observed_at` and `last_observed_seq` | Deduplicating by body hash folded the observation timeline. `latest` (the pointer) and `latest~N` (capture time) diverged: after a revert the default diff came out empty, transitions that never happened were shown, and intermediate editions were unreachable |
| 13 | 7.4 | `latest~N` defined on **observation order**; the TimeMap keeps capture order | Same as above. The two answer different questions — RFC 7089's time axis is the Memento-Datetime |
| 14 | 7.6 | `has_pending_verification` now keyed on **which version was verified** | Keyed on time, reverts and archive rescues returned False right after the current body changed |
| 15 | **6.2** | Core-scan runs are **split at pattern length** | Grouping runs only by `score ≤ k` meant that for long quotes (Latin ~340 chars up), where k approaches the core length, the whole document became one run and #1 was nullified. 88.5% of that range was false MISSING |
| 16 | **6.3** | `UNRESOLVED` widened to **evidence-split cases** | #4's gate read "the context is alive but the quote is not beside it" as deletion, asserting MISSING for a quote that had **moved sections while being edited**. Section reshuffles are ordinary editing. The two situations cannot be told apart by this evidence, so neither is asserted |
| 17 | 6.2 | Stage 4's verdict boundary split into "no match → MISSING / no corroboration → UNRESOLVED" | Same as above. Absence can be stated; unidentifiability cannot |

Many of this section's fixes complete what an earlier fix did **only by half** (1 follows D-042, 2 and 4 follow D-044, 7 follows D-045, 6 follows D-049). The §12 lesson — one passing reproduction script is not completion — repeated itself verbatim.

## 19. v1.5 → v1.6 Change History

Reflects **stage 1 (normalization, cluster 1)** of the remediation for the 101 defects demonstrated in the second parallel audit (10 Opus auditors, 2026-08-18). These came out of code written during the first round of remediation, so each entry also records what was broken while fixing something else.

| # | Section | Change | Defect behind it |
|---|---|---|---|
| 1 | **5.3** | **NORM_VERSION 3** — recognize lines and blocks first; rules that delete anything apply only inside prose lines | Global substitution running ahead of line and fence recognition was the common root of all 22 cluster-1 defects |
| 2 | 5.3 | Tag removal narrowed to **formatting-only inline tags** | Deleting anything tag-shaped erased `<updated>` and `List<String>` from prose, so an `<updated>` → `<published>` revision collapsed to the same string and verify reported INTACT |
| 3 | 5.3 | Emphasis markers are **not stripped inside a word** | `2*3*4` became `234`, inventing a number that was never written |
| 4 | 5.3 | NFC moved **after** character removal | Removing zero-width characters is what makes a base and a combining mark adjacent, and NFC had already run |
| 5 | 5.3 | Code fences, indented code, and inline code are isolated | Code blocks were emptied and indentation flattened, so returned code was syntactically invalid |
| 6 | 5.3 | **Record detection** for line folding; tighter structural markers | Logs, CSV, tables, and config files were folded so unrelated values became neighbours; conversely `2026. 3. 15.` was mistaken for a numbered list and was not folded |
| 7 | 5.3 | The join separator is decided by the **document's writing system**; spaces between CJK characters are removed | Looking at a single boundary character inserted a space that does not exist in Japanese text. The path where the extractor turns a CJK line break into a space was closed as well |
| 8 | 5.3 | Six line separators unified; the set of removed zero-width characters narrowed | U+2028 and friends survived in the body and broke copied quotes, while removing ZWJ, ZWNJ, LRM, and RLM damaged emoji, orthography, and paragraph direction |
| 9 | 5.3 | A body that normalizes to empty is **not stored** | A `char_count: 0` version flipped every anchor on that document to MISSING |
| 10 | 5.3 | Fall back when the extraction's block structure has collapsed | Headings were glued onto the following paragraph, so quotes copied off the screen did not resolve |
| 11 | **6.1** | Quote lookup is **tolerant of block separators** — runs of spaces and newlines count as one, and leading list markers are skipped. The anchor's `exact` is bound to a string that actually exists in the stored body | Quoting two paragraphs or two list items at once — a common action — always produced `QuoteNotFound` (only 2 of 10 succeeded) |
| 12 | 6.1 | `QuoteNotFound` distinguishes "this region may not have been stored" | The wording was identical to the refusal of a fabricated quote, so a user who copied from the page was told their sentence "is not in the source" |
| 13 | **12** | The golden corpus must **actually exercise** the normalization rules (code blocks, tables, lists, CJK, combining marks, prose asterisks, Unicode spaces), and that coverage is pinned by a test | The 30 golden fixtures exercised none of the rules NORM_VERSION 2 introduced, so all 30 passed while normalization was deleting body text |
| 14 | 12 | Migrating an older database **that contains rows** is an explicit test target | The regression test only migrated a database with zero rows and missed a defect that made real stores permanently unopenable |

---

## 20. v1.4 → v1.5 Change History

Reflects the remediation of **52 defects demonstrated with reproduction scripts** in a parallel audit (10 Opus instances, 2026-08-17). They emerged in a state where all 198 existing tests passed, so each item is also a blind spot in the specification.

| # | Section | Change | The defect that motivated it |
|---|---|---|---|
| 1 | **4.1** | Introduced `documents.current_version` — separating "the current source version" from "the maximum capture time" | On an archive fallback, re-verification compared the content used to create the anchor against itself, so a dead document stayed permanently `INTACT` |
| 2 | **4.1** | Introduced `document_aliases` | For URLs that redirect, the lookup key and the storage key diverged and the cache missed forever (a violation of the v0.1 completion criteria) |
| 3 | **4.1** | Added `source` to the `UNIQUE` constraint | When archived content matched an existing live version, that row was reused and the source, URI-M, and Memento datetime were lost |
| 4 | 4.2 | Made `gc keep >= 1` explicit | `--keep 0` deleted even the latest content of documents with no anchors |
| 5 | **5.1** | Delegated the trailing slash to redirects, preserved IPv6 brackets | IPv6 was the only violation of normalization idempotence |
| 6 | **5.2** | Re-evaluate robots at **every** redirect hop | Going through a redirector fetched forbidden paths and other hosts without even a robots request |
| 7 | **5.2** | robots 5xx and connection failure became denials (RFC 9309 §2.3.1.4), cached for 5 minutes | At the very moment a server was too overloaded to serve its rules, we switched to unrestricted access |
| 8 | **5.3** | **NORM_VERSION 2** — unify Unicode whitespace, remove zero-width characters, markdown emphasis, and residual tags, fold typesetting line breaks (writing-system aware), restore PDF end-of-line hyphenation | The one entry path that exists — "copy off the screen → cite" — broke frequently |
| 9 | **5.3** | Split `pipeline_version` per path and included the encoding detector | `text/plain` recorded a trafilatura version it never ran, defeating the `renormalized` safeguard |
| 10 | 5.3 | Exact content-type matching | `text/plaintext` was misclassified onto the plain path |
| 11 | **5.4** | Parse `Retry-After` HTTP-date, stop retrying instead of truncating when the ceiling is exceeded, apply the size ceiling to every response, rate-limit concurrency | We cut the server-specified wait to 60 seconds and knocked early, and buffered enormous error pages in full |
| 12 | **6.1** | Writing-system-aware thresholds (Han and kana converted at 2.5× information density, Hangul on the Latin baseline) | A complete Chinese sentence was refused as a citation |
| 13 | 6.1 | Duplicate-occurrence warning | Because the anchor binds to the first occurrence, the state can be `INTACT` even after the cited instance is deleted |
| 14 | **6.2** | regex/Myers path threshold 6 → 3, writing system reflected in the edit-distance ratio | Complete CJK sentences were pinned to the slow path and the determination failed on a 7 KB article (Korean 140 ms → 2.2 ms) |
| 15 | **6.2** | Refine the windows from every core, `m+2k` of slack on the right of the window | When one end of the quote aligns better with a heading or lede, a false `MISSING` results |
| 16 | **6.2** | Stage 3 sweeps all candidates and selects the best | In documents with repeating templates, an unrelated sibling paragraph was reported as "the current form of the quote" |
| 17 | **6.2** | Enforce the budget **inside** the stages | A 4,000-character quote spent 7.7 seconds against a 200 ms budget |
| 18 | **6.3** | A miss in a truncated document is `UNRESOLVED`, not `MISSING`, and `truncated` is conveyed | We declared "it disappeared" without having read the whole thing |
| 19 | **7.0** | Cooperative cancellation of Tasks, shutdown ordering, ttl cleanup, rejection of non-taskable calls | Cancellation changed only the status while the work kept running, and the process died on shutdown |
| 20 | 7.1 | Reject negative chunk arguments | A negative `start_index` returned the tail of the document as though it were the head |
| 21 | 7.3 | Added `stopped_early` | Partial results could be misread as "nothing wrong" |
| 22 | **9** | Load-time validation (types and ranges), full environment-variable support, fractional `max_content_mb` | `enabled = "no"` was interpreted as true, and `0.5` became 0 bytes |
| 23 | **10** | Introduced a concurrency isolation metric — per-URL locks instead of a global lock | Background verification stopped all 8 of the other tools |
| 24 | **5.2** | Enter the archive fallback even when the original could not be reached (host disappearance, undeterminable robots). An explicit robots denial is still not routed around | Demonstrated during finalization: an attempt to rescue a citation from a shut-down service could not even reach the fallback because robots could not be retrieved — the representative scenario was blocked |
| 25 | 12 | Expanded the regression suite (198 → 340) | Every one of the defects above passed the existing suite |

---

## 21. v1.3 → v1.4 Change History

Cleanup at the v1.0 release (2026-08-17).

| # | Section | Change | Rationale |
|---|---|---|---|
| 1 | **11.1, 11 structure, 9** | Settled configuration loading on the stdlib (tomllib), removed the `pydantic-settings` dependency, reclassified `pydantic` as an mcp transitive | The specification was requiring a dependency that was not actually installed or used |
| 2 | 12 | Made the optional-run conditions for the anchor benchmark explicit + specified `benchmarks/run_micro.py` (§10 metrics) | Grade-C data needs the network — consistent with the outcome section of ADR-0002 |
| 3 | 11.2 | Delegated the development-dependency license policy to `THIRD-PARTY.md` §2.1 (including hypothesis MPL-2.0, dev-only) | Clarifies where dependencies not included in the distribution are managed |
| 4 | **6.2** | **Settled the approximate search path selection** — regex for k ≤ 6, Myers bit-vector for k > 6 (core reduction + semi-global DP verification) | regex fuzzy matching is exponential at large k when nothing is found — fixes a measured defect where the `MISSING` determination was defeated on English quotes (more characters → larger k) |

---

## 22. v1.2 → v1.3 Change History

Reflects what was settled during the v0.1–v0.4 implementation (2026-08-17). These were found with the implementation running ahead of the specification, so the grounds for each item are in the code and the tests.

| # | Section | Change | Rationale |
|---|---|---|---|
| 1 | **4.1, 5.3** | **Introduced the `versions.pipeline_version` column** — extractor + normalization rule version. A hidden input to text_hash | Prevents misreporting a pipeline change as a source change |
| 2 | **5.2, 4.1, 7.1** | **Formalized the outcome vocabulary** — added `unchanged` (a 200 with an identical text_hash), `renormalized` (identical raw, differing pipeline), and `created` | `unchanged`, which appeared only in the body of §5.2, was missing from the enumeration |
| 3 | **5.1** | Narrowed the tracking-parameter removal list, **prohibited removing `ref` and `s`** | Depending on the site they are part of the real path — distinct documents get merged |
| 4 | 5.2 | Request order: moved the cache lookup ahead of the robots check | Returning from the cache is not a network request. robots is evaluated immediately before a request |
| 5 | 4.1 | Introduced the `robots_cache` table (24h persistence per origin) | Every CLI invocation is a new process — without persistence the v0.1 completion criteria are not met |
| 6 | 4.2 | Versions referenced by verification history are also retained by gc | FK integrity + audit trail |
| 7 | 7.1 | `max_length`, `content_truncated`, `next_start_index` (mcp-server-fetch compatible chunked reading) | The drop-in replacement path (§13, v0.3 completion criteria) |
| 8 | 7.0 | Tasks wire format caution — return the task descriptor in-band in CallToolResult, serve tasks/* as an extension | The wire gate of the current protocol (2026-07-28) disallows CreateTaskResult |
| 9 | 7.4 | `from`/`to` → `from_version`/`to_version` | Python keywords |
| 10 | 8 | Added the CLI `list` and `serve` commands, specified the `anchor-mcp` entry point | Reflects the implementation |
| 11 | 11.1 | `trafilatura>=1.9.0` — made the distinction between the license lower bound (1.8.0) and the functional lower bound (markdown output) explicit | 1.8.x has no markdown output |

---

## 23. v1.1 → v1.2 Change History

Reflects the results of the license audit (2026-08-16). All 16 unverified items were checked and reduced to zero, and in the process one substantive conflict was found.

| # | Section | Change | Rationale |
|---|---|---|---|
| 1 | **11.1** | **Specified the `trafilatura>=1.8.0` lower bound + a comment giving the reason** | **Below v1.8.0 it is GPLv3+. It conflicts with Apache-2.0 distribution** |
| 2 | 11.1 | Attached a verified license comment to every dependency | Removes guesswork |
| 3 | 11.2 introduced | Three dependency constraints (`trafilatura` lower bound, `regex` compound, `blake3` dual) | Audit results |
| 4 | 5.2 | MemGator operating guidance table (`--spoof` prohibited, `--agent`, `--arcs`) | Prevents a violation of principle + workaround for the git.io shutdown |
| 5 | 9 | Added the `archive_list` configuration value, a comment prohibiting spoof | Same |
| 6 | **12.1 introduced** | Three-grade fixture provenance policy (A committed / B committed / C not committed) | Annotation data is UGC, and original pages are other people's copyrighted work |
| 7 | **12.2 introduced** | Three CI license gates | People cannot prevent this from memory |
| 8 | 11 structure | Added `NOTICE`, `THIRD-PARTY.md`, `tools/audit_licenses.py` | Apache-2.0 §4(d) and a reproducible audit |
| 9 | 14 | Added credits for Sawood Alam and Michael L. Nelson (MemGator) and Robert Knight (anchor-quote) | They supplied the design rationale |
| 10 | Roadmap | Moved the former "v1.2 candidates" to **v1.3 candidates** | Avoids a collision with the specification version |

### Summary of Verified Licenses

| Category | Count | Result |
|---|---|---|
| Runtime dependencies | 15 | MIT 6, BSD-3-Clause 5, Apache-2.0 3, compound 2 — **all permissive** |
| Referenced (code not included) | 10 | MIT 6, Apache-2.0 3, BSD-2-Clause 1 |
| Specifications (free to implement) | 5 | RFC 7089 / 9110 / 9309, W3C Web Annotation, Robust Links |
| **Copyleft** | **0** | No GPL / AGPL / LGPL |

See `THIRD-PARTY.md` for details.

---

## 24. v1.0 → v1.1 Change History

| # | Section | Change | Rationale |
|---|---|---|---|
| 1 | 1.1 | Corrected the terminology to `reference rot` / `citation drift` | "content drift" collides with the ML term |
| 2 | 1.3 | Explicitly added determining semantic change to the non-goals | Boundary clarification |
| 3 | **1.4 introduced** | Prior art and inheritance | The Memento/W3C/Hypothesis lineage |
| 4 | 2 | Memento terminology mapping table | RFC 7089 interoperability |
| 5 | 4.1 | Added `versions.source`, `versions.source_uri`, `anchors.quality`, `verifications.edit_distance`, `verifications.elapsed_ms` | Archive fallback and diagnostics |
| 6 | 5.2 | Added step 6, the archive fallback | Rescuing `GONE` |
| 7 | **6.1** | Short quote warning and refusal | Blocks pathological matching cases |
| 8 | **6.2** | `rapidfuzz.partial_ratio` full scan → approximate search with an edit-distance ceiling + a time budget | **Avoids the Hypothesis #3919 failure mode** |
| 9 | 6.3 | Added the `UNRESOLVED` state (6 → 7) | An honest non-answer when the budget is exceeded |
| 10 | **7.0 introduced** | MCP 2026-07-28 compliance, adoption of the Tasks extension | Long-running batch verification |
| 11 | 7.1 | The `start_index` parameter (mcp-server-fetch compatible) | The drop-in replacement path |
| 12 | **7.8 introduced** | `get_timemap` — RFC 7089 export | Interoperability |
| 13 | **7.9 introduced** | `export_robust_links` | Interoperability |
| 14 | 10 | Added worst-case latency and `UNRESOLVED` rate metrics | Tail performance management |
| 15 | 11 | `rapidfuzz` → `regex`, added the `export/`, `archive.py`, `approx.py`, and `budget.py` modules | Reflects the changes above |
| 16 | 12 | Promoted the anchor benchmark to a CI gate, added interoperability tests | Prevents performance regressions |
| 17 | 13 | Made the v0.3 completion criteria concrete as "a drop-in replacement for mcp-server-fetch", introduced the v0.5 archive fallback | Clarifies the adoption path |
| 18 | **14 introduced** | Credits | Community relations |

---

*This specification describes the state as of the completion of v1.0; implementation proceeds in stages from v0.1 according to the roadmap in §13.*
