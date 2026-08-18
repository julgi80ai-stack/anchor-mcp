# Anchor — A Provenance-Tracking Fetch Cache (EN)

**Technical Specification v1.6 (as of completion)**

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

> **v1.5 → v1.6 change summary**: Reflects stage 1 (normalization) of the remediation for the 101 defects demonstrated in the second parallel audit. **The normalization rules were redesigned and NORM_VERSION was raised to 3** (§5.3) — lines and blocks are recognized first, and rules that delete anything apply only inside prose lines. Quote lookup was made tolerant of block separators (§6.1), the golden corpus is now required to actually exercise the normalization rules (§12), and migrating an older database *that contains rows* was made an explicit test target (§12). See §15 for the full list.
>
> **v1.4 → v1.5 change summary**: Reflects the remediation of 52 defects demonstrated in a parallel audit. The schema was raised to v5 to introduce a "current live version" pointer, redirect aliases, and per-source version uniqueness (§4.1); the normalization rules were revised so that quotes copied off the screen actually resolve (§5.3, NORM_VERSION 2); robots is now evaluated at every redirect hop and a robots 5xx became a denial (§5.2); anchor thresholds and the edit-distance ratio now account for the writing system (§6.1, §6.2); the budget is enforced inside the matching stages (§6.2); and the global lock was narrowed to per-URL scope (§10). See §16 for the full list.
>
> **v1.3 → v1.4 change summary**: Cleanup at the v1.0 release point. Configuration loading was settled on the standard library and `pydantic-settings` was removed from the dependencies (§9, §11); the conditions for optionally running the anchor benchmark were made explicit (§12); and the development-dependency policy was delegated to the ledger (§11.2). See §17 for the full list.
>
> **v1.2 → v1.3 change summary**: Reflects what was settled during the v0.1–v0.4 implementation. The `versions.pipeline_version` column and the `renormalized` and `unchanged` outcomes were formalized (§4.1, §5.2, §5.3, §7.1); the tracking-parameter removal list in URL normalization was narrowed (§5.1); robots cache persistence and the request order were clarified (§4.1, §5.2); `mcp-server-fetch`-compatible chunked reading was added (§7.1); and the SDK constraint on the Tasks wire format was recorded (§7.0). See §18 for the full list.
>
> **v1.1 → v1.2 change summary**: Reflects the results of the license audit. The `trafilatura>=1.8.0` lower bound was made mandatory (§11); MemGator operating guidance was made explicit (§5.2, §9); the provenance policy for test fixtures was split into three grades (§12); and a license gate was added to CI (§12). See §19 for the full list.
>
> **v1.0 → v1.1 change summary**: Reflects the results of the prior-art survey by introducing Memento compatibility (§2, §5.2, §7.8), replacing the anchor matching algorithm with a performance-safe approach (§6.2), expanding the verification states to seven (§6.3), and adopting the Tasks extension of the latest MCP spec (§7.0). See §20 for the full list.

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
| `captured_at` | Memento-Datetime | The time at which that version was obtained |
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
5. The trailing slash of a path is **not normalized; it is left to the redirect response** (v1.5). Only the server knows whether `/a` and `/a/` are the same document or different ones, and the server says so with a redirect. For a document reached via a redirect, the input URL is registered in `document_aliases` so the next lookup finds the cache — that is the practical implementation of this step.

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

- **User-Agent**: default `Anchor/<release version> (+https://github.com/julgi80ai-stack/anchor-mcp)`. No disguise or spoofing option is provided.
- **robots.txt**: respected by default. The `respect_robots = false` setting exists, but enabling it prints a warning in the server startup log.
- **Rate limiting**: a per-host token bucket. The configured value is honored even under concurrent calls — left unlocked, waiting threads all wake at once and hammer the host at several times the configured rate (v1.5).
- **Honoring `Retry-After`** (clarified in v1.5): both numeric and HTTP-date forms are parsed. If the server specifies a wait longer than the ceiling (default 60 seconds), we **stop retrying rather than truncating it and knocking early** — reporting "could not confirm" is the honest answer. Unparseable or abnormal values (`nan`, etc.) fall back to our own exponential backoff.
- **Size ceiling**: applied to **every response**, not only successful ones (v1.5). We do not buffer an enormous error page or blocking interstitial in full.
- **Conditional requests**: always used. This is exactly what reduces server load.
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
| 4 | **Approximate string search with an edit-distance ceiling** | found within the ceiling → `ALTERED`, not found → `MISSING` |

#### Stage 4 in Detail — the Core Change from v1.0

**The problem with the v1.0 specification**: A whole-document sliding scan with `rapidfuzz.partial_ratio` is O(n·m), and **it is slowest exactly when it fails to find anything.** It stays hidden in the normal situation where most verification targets are `INTACT`, and then performance collapses in the worst situation, where the document has been heavily reworked. This is the same failure mode Hypothesis hit with `diff-match-patch`. A benchmark of an alternative implementation reported a 14× difference — 13,342 ms versus 936 ms on a 100,000-character document with 453 quotes — and the approximate-matching side also had a higher anchoring success rate.

**The v1.1 approach**: Fix an edit-distance ceiling `k` up front and use a bit-parallel approximate search that gives up immediately once that ceiling is exceeded.

```python
# k = the allowed edit distance. Proportional to quote length, but capped.
# Accounts for the writing system (v1.5): a revision of the same character
# (replacing one word) is expressed in far fewer characters in Japanese and
# Chinese, so multiplying by a fixed ratio drops ALTERED down to MISSING.
ratio = 0.15 * (2.5 if han_and_kana_are_the_majority else 1.0)
k = max(1, min(int(len(exact) * ratio), 64))
```

Implementation priority:

1. **Primary implementation** — the fuzzy matching of the `regex` module. It is a C implementation and supports an error ceiling natively.
   ```python
   pattern = regex.compile(f"({regex.escape(exact)}){{e<={k}}}", regex.BESTMATCH)
   ```
2. **Fallback / optimization** — a direct implementation of Myers bit-vector approximate string search. It is the algorithm the `approx-string-match` family uses; for pattern lengths ≤ 64 it is close to O(n) through word-level parallelism.

> **Path selection (revised in v1.5)**: regex fuzzy matching becomes exponentially slow **when k is large and there is no result**. Measurements show it is already slower than Myers from k=4, and at k=6 on a 7 KB Korean article it exhausted the 200 ms budget and produced `UNRESOLVED` (running the same input through Myers is 56–67× faster). Since the problem was that short quotes (= complete CJK sentences) were pinned to the slow path, **regex is used when k ≤ 3 and the Myers path when k > 3**. That the two paths agree in their determinations was confirmed by differential comparison.
>
> **Window candidates (v1.5)**: For quotes longer than 64 characters, the position is narrowed using 64-character cores from the front and the back, and then the whole quote is verified inside the window with semi-global DP. Here **the windows from every core are refined and the best one is chosen** — keeping only the single lowest-scoring core means that when one end of the quote aligns better somewhere else in the document (a heading, a lede, a pull quote), only that decoy window is examined, the true position is missed, and a false `MISSING` results. The window needs `m + 2k` of slack on the right (because the true match's start can shift by ±k and its length can stretch to m±k).

`score` is computed as `1 - (edit_distance / len(exact))`, and `edit_distance` is stored alongside it. A ratio alone is misleading for short quotes.

> **Candidate selection (v1.5)**: Stage 3 **does not stop at the first occurrence** of the prefix. In documents with repeating templates (contract clauses, changelogs, FAQs, tables), fixing on the first candidate reports a sibling paragraph unrelated to the quote as "the current form of your quote" — what is wrong is not the determination but **the text presented as grounds for the determination**, which turns §6.3's "present the text before and after the change together" into a false comparison table.

#### Time Budget (new in v1.1, enforcement scope widened in v1.5)

There is a ceiling on matching time per anchor. On exceeding it, no determination is forced; `UNRESOLVED` is returned.

**The budget is checked not only between stages but inside them** (v1.5). The edit-distance DP of stages 3 and 4 is O(quote length × candidate length), so checking only between candidates lets a single call blow through the whole budget — measurements showed a 4,000-character quote spending 7.7 seconds against a 200 ms budget (38×). Checking on every DP row upholds the contract that "one anchor does not stop the whole run."

| Condition | Default budget |
|---|---|
| `quality = OK` | 200 ms |
| `quality = SHORT` | 100 ms |
| Document length ceiling | 2 MB (beyond that, only the first 2 MB is searched, with a `TRUNCATED` flag) |

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
| `UNRESOLVED` | Determination withheld because the time budget was exceeded *(new in v1.1)* | Re-verify with a larger budget, or reset the quote to a longer one |

`ALTERED` and `MISSING` are different events. The former means the source changed; the latter means the citation became invalid. This distinction produces the most practical value in report review.

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

> **Cancellation and shutdown (v1.5)**: A batch runs on a separate thread, and a thread cannot be forcibly cancelled. `tasks/cancel` is therefore implemented as **a cooperative abort signal** — the worker checks between documents and does not touch the remaining ones. Changing only the status while the work keeps running is a violation of the contract that says "it can be aborted." A cancelled task must also leave partial results that can be collected via `tasks/result` (otherwise the client falls into infinite polling), and on server shutdown the store is closed **after the workers have been cleaned up** (getting the order wrong kills the process by releasing a connection still in use). Terminated tasks are cleaned up after their `ttl` elapses. If task metadata is attached to a tool that cannot be run as a task, it is rejected rather than silently ignored.

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
  "offset": 8214,
  "quality": "ok",
  "warnings": [],                           // warning strings when quality=short
  "created_at": "..."
}
```

### 7.3 `verify_citations` *(Task)*

Re-verifies anchors against the current source. Processes in batch and observes per-host rate limits. **Returns as a Task.**

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
  "attention": [
    {
      "anchor_id": "018f...",
      "state": "ALTERED",
      "url": "https://example.com/report",
      "before": "More than half of AI crawler traffic goes to re-fetching pages that have not changed",
      "after":  "About 60% of AI crawler traffic goes to re-fetching pages that have not changed",
      "match_score": 0.86,
      "edit_distance": 12
    }
  ],
  "network": { "requests": 12, "not_modified": 9, "bytes_down": 48210 },
  "stopped_early": false                    // did it end early due to cancellation or shutdown (v1.5)
}
```

If `stopped_early` is true, `checked` and `summary` are **partial results**. The remaining anchors were not verified, so they must not be read as "nothing wrong."

The `attention` array carries only the items that require action (`ALTERED`/`MISSING`/`GONE`/`UNRESOLVED`). It does not fill the context by listing all 36 `INTACT` entries.

### 7.4 `diff_versions`

Returns the content difference between two versions as a unified diff.

```jsonc
{ "document_id": "018f...", "from_version": "latest~1", "to_version": "latest", "context_lines": 2 }
```

Why the parameters are not named `from`/`to`: they are Python keywords and cannot be used in the tool signature of the reference implementation (v1.3). Version references are `latest`, `latest~N`, or a version id.

### 7.5 `get_version`

Retrieves the content of a past version verbatim. Even after the source is gone, the text as it stood at citation time can be inspected.

### 7.6 `list_documents`

Returns the list of cached documents together with their status and last-checked time. Filters: `status`, `host`, `has_pending_verification`.

### 7.7 `cache_stats`

```jsonc
{
  "documents": 312, "versions": 489, "anchors": 1204,
  "disk_bytes": 24117248,
  "last_30d": {
    "requests": 1840,
    "cache_hits": 1102,
    "not_modified": 498,
    "changed": 240,
    "bytes_saved_estimate": 71303168,
    "hit_rate": 0.87
  }
}
```

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

Environment variables override the documented configuration keys across the board (`ANCHOR_DB_PATH`, `ANCHOR_TIMEOUT_SECONDS`, `ANCHOR_RATE_LIMIT_RPS`, etc.). Implementing only two of them while writing "always take precedence" is a mismatch between specification and implementation.

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
max_edit_ratio      = 0.15     # k = len(exact) * this value, max 64
min_quote_chars     = 12       # below this, creation is refused
short_quote_chars   = 32       # below this, a SHORT warning
time_budget_ms      = 200
max_document_bytes  = 2097152

[server]
transport = "stdio"   # stdio | http
```

---

## 10. Non-Functional Requirements

| Item | Target | Measurement method |
|---|---|---|
| Cache hit response | p95 < 15 ms (content ≤ 1 MB) | Benchmark suite |
| Conditional request savings | 0 bytes downloaded for unchanged documents | `fetch_log` aggregation |
| Anchor re-verification throughput | 500 anchors / 60 s (excluding network) | Benchmark |
| **Anchor matching worst case** | **p99 < 250 ms per anchor, no stalls** | Heavily reworked document scenario |
| **`UNRESOLVED` rate** | **under 1% on a normal corpus** | Golden benchmark |
| Memory | resident < 150 MB | Processing 100 consecutive 8 MB documents |
| Concurrency | Safe for multiple clients in a single process | WAL mode + serialization in the store layer |
| **Concurrency isolation** | **Work on one document must not block other documents or read-only tools** | **Per-URL lock (v1.5)** |
| Portability | Linux / macOS / Windows | CI matrix |

The reason the worst-case metric (row 4) was added in v1.1 is in §6.2. You have to look at the tail, not the average.

**Concurrency isolation (v1.5)**: Wrapping every tool in a global lock is safe, but it stops all the other tools while a background batch verification runs — directly at odds with the purpose of the Tasks extension. Serialization is confined to the minimum necessary scope: the store serializes internally, and the service locks only the "look up → decide → create" section for the same URL, at per-URL granularity. **The reason the serialization responsibility sits in the library layer rather than the server** is that someone using the library directly must get the same guarantee.

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
│   │   ├── schema.sql     # full schema for new DBs (currently v5)
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

## 15. v1.5 → v1.6 Change History

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

## 16. v1.4 → v1.5 Change History

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

## 17. v1.3 → v1.4 Change History

Cleanup at the v1.0 release (2026-08-17).

| # | Section | Change | Rationale |
|---|---|---|---|
| 1 | **11.1, 11 structure, 9** | Settled configuration loading on the stdlib (tomllib), removed the `pydantic-settings` dependency, reclassified `pydantic` as an mcp transitive | The specification was requiring a dependency that was not actually installed or used |
| 2 | 12 | Made the optional-run conditions for the anchor benchmark explicit + specified `benchmarks/run_micro.py` (§10 metrics) | Grade-C data needs the network — consistent with the outcome section of ADR-0002 |
| 3 | 11.2 | Delegated the development-dependency license policy to `THIRD-PARTY.md` §2.1 (including hypothesis MPL-2.0, dev-only) | Clarifies where dependencies not included in the distribution are managed |
| 4 | **6.2** | **Settled the approximate search path selection** — regex for k ≤ 6, Myers bit-vector for k > 6 (core reduction + semi-global DP verification) | regex fuzzy matching is exponential at large k when nothing is found — fixes a measured defect where the `MISSING` determination was defeated on English quotes (more characters → larger k) |

---

## 18. v1.2 → v1.3 Change History

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

## 19. v1.1 → v1.2 Change History

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

## 20. v1.0 → v1.1 Change History

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
