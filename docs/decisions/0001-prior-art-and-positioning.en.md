# ADR-0001 — Inheriting Prior Art and Positioning (EN)

| | |
|---|---|
| Status | **Accepted** |
| Decided | 2026-08-16 |
| Supersedes | — |
| Related | SPEC.md §1.4, §2, §6.2, §14 · MANIFESTO.md §3 |

> **Translation note**: This is an English translation of
> `0001-prior-art-and-positioning.md`. The Korean version is normative — if the
> two ever disagree, the Korean text governs. This record is frozen in both
> languages: to revise a judgment, write a new ADR rather than editing this one.

> This document is a **decision record**. To reverse a decision, do not edit this document — write a new ADR that supersedes it. The reasoning behind an overturned judgment has to survive in the record too.

---

## Decision (Summary)

**Anchor does not define a new layer. It positions itself as a local client implementation of existing standards (Memento, W3C Web Annotation, Robust Links).**

The public narrative is not "something new" but **"something that came back."**

---

## Context

### This problem was already defined

The hypothesis going into the survey was that "the web-evidence integrity layer for AI agents is empty." It was only half right.

```
2009 ─── Memento proposed — "the web has a poor memory"
2013 ─── RFC 7089 — adding a time dimension to HTTP
2014 ─── "One in five articles suffers from reference rot"  (Klein et al., PLOS ONE)
2015 ─── Time Travel service launched (LANL Research Library)
2016 ─── "Three out of four references lead to changed content" (Jones et al., PLOS ONE)
  ⋮      Sustained by institutional funding. Almost unknown among general developers
2025 ─── Time Travel service shut down (management decision)   ◀── infrastructure gone
2026 ─── AI agents consume the web at scale                    ◀── demand explodes
         ▲
         └─ Anchor
```

RFC 7089 defines the original resource (URI-R), past versions (URI-M), the list of versions (TimeMap), and time negotiation (TimeGate). **The `versions` table in the draft design was a local reimplementation of TimeMap.** Had we proceeded without knowing this, we would have rebuilt a 13-year-old standard under a different name.

The same thing happened with anchors. The four-stage matching procedure in draft §6.2 was effectively identical to what Hypothesis already does. That is not a coincidence — it means this problem has only one right answer.

### But the infrastructure is gone

The LANL Time Travel service was shut down at the end of 2025, and mementoweb.org became a static site holding nothing but archived material. The Robust Links service was discontinued along with it. The only survivor is MemGator, which can be self-hosted.

**Memento did not die because it was wrong. It died because it was built as a central service dependent on institutional funding.**

### And demand exploded

| Metric | Value | Source |
|---|---|---|
| Referenced content changed within three years | **~75%** | Jones et al., PLOS ONE 2016 |
| STM articles suffering reference rot | **20%** (1M+ references analyzed) | Klein et al., PLOS ONE 2014 |
| New papers containing fabricated citations | **1 in 277** (1 in 2,828 three years ago) | early 2026 |
| Deep-research agent citation accuracy | OpenAI 78% ~ Claude 94% | 2026 study |
| Re-requests for unchanged pages | **more than half** of AI crawler traffic | Cloudflare |
| Crawl-to-referral ratio | Google 14:1 / OpenAI 1,700:1 / Anthropic 73,000:1 | Cloudflare, mid-2025 |

The paper's title is the problem statement itself — *"Three out of Four URI References Lead to Changed Content."*

### Is the position open?

We checked the adjacent space along three axes. On every axis, Anchor is on the opposite side.

| | Existing tools | Anchor |
|---|---|---|
| Being cited / doing the citing | GEO/AEO marketing tools (Profound, Scrunch, Otterly) | **doing the citing** |
| Static literature / changing web | academic reference validation (CiteCheck, Citely, SwanRef) | **changing web** |
| Watching the future / verifying the past | changedetection.io (32k★) | **verifying the past** |

changedetection.io, the closest adjacent product, asks "has this page changed?" Anchor asks "is the sentence I cited still there?" If the entire page changes but the quote is intact, Anchor stays quiet; if the page barely changes but that one sentence was edited, Anchor warns.

ArchiveBox does full preservation (tens of MB per snapshot); Anchor does content extraction (tens of KB of compressed text). They do not overlap, because ArchiveBox has no quote-level tracking. The relationship is complementary, not competitive.

---

## Decisions

### D1 — Declare the inheritance explicitly

Position the project as **"a client-side revival of a dead standard,"** not as "inventing a new layer."

- Put §1.4 (Prior Art and Inheritance) and §14 (Credits) in SPEC.md
- Make MANIFESTO.md §3 the "Lineage" section and place it early in the document
- Add **"world's first" and "a new standard"** to the list of forbidden phrasings — they are not true, and people in this field will spot it immediately

**Rationale**: This field is small. Van de Sompel, Nelson, Alam, Klein, Jones, Knight — these are people who have worked the same problem for twenty years, and they know each other. A project that quietly reimplements their standards and a project that explicitly declares its inheritance are treated completely differently. The former gets ignored or resented; the latter borrows twenty years of accumulated trust.

### D2 — Local-first is the answer to Memento's failure mode

It was built as a central service, and when the funding stopped, it died. Anchor runs off a single SQLite file. **With no server, there is no server to shut down and no line item to disappear from a budget meeting.**

This is the rationale for Principle 1 (local-first), and the strongest narrative in the manifesto.

### D3 — Adopt interoperability (no reimplementation)

| Adopted | Governing specification | Implementation location |
|---|---|---|
| TimeMap serialization | RFC 7089 §5 | `export/timemap.py` |
| TextQuoteSelector / TextPositionSelector | W3C Web Annotation Data Model | `anchoring/selector.py` |
| Robust Links attributes | Robust Links Spec | `export/robustlinks.py` |
| Archive fallback | Memento aggregator (MemGator) | `fetcher/archive.py` |

The implementation cost is a handful of serialization functions. What we get is interoperability with a 13-year-old RFC, and a point of contact with the community that has held onto this problem for a long time.

### D4 — Anchor matching avoids Hypothesis's failure

Drop the draft's full-document sliding scan with `rapidfuzz.partial_ratio`.

**Rationale**: Hypothesis client issue #3919 — when short, generic quotes are mixed into a long document, fuzzy matching becomes extremely inefficient; anchors with no exact match are processed serially and the page freezes for 10 seconds or more. The cause is that `diff-match-patch` is **slowest when it fails to find anything**.

In the benchmark for the replacement implementation (robertknight/anchor-quote), on a 100,000-character document with 453 quotes:

| Implementation | Anchors resolved | Orphans | Time |
|---|---|---|---|
| dom-anchor-text-quote (diff-match-patch) | 318 | 135 | **13,342 ms** |
| anchor-quote (approximate matching) | 331 | 122 | **936 ms** |

14x faster, with a higher success rate. We therefore adopt approximate search with a bounded edit distance, a per-anchor time budget, and the `UNRESOLVED` state (SPEC.md §6.2, §6.3).

**The most valuable asset from this survey is not code — it is this failure record.** It is written down in a public issue tracker, and it requires no license.

### D5 — The adoption path is a drop-in replacement for `mcp-server-fetch`

Anthropic's official `mcp-server-fetch` is the most widely used fetcher in the MCP ecosystem, but it has **no cache, no versions, and no provenance.** Put those on top of the same interface, and users only have to change one line of configuration.

We set the v0.3 completion criterion as: "replacing `mcp-server-fetch` with Anchor in Claude Desktop causes no inconvenience."

### D6 — The terminology is `reference rot` / `citation drift`

In the developer ecosystem, `content drift` overwhelmingly means machine learning's concept drift. GitHub search results come back entirely as Frouros, alibi-detect, and their kin.

The terminology of the Klein / Van de Sompel lineage is both more precise and free of search collisions.

---

## Consequences

### Positive

- Half the design already exists as public specifications, with no legal barriers (ADR-0002 §Specifications)
- We learned the pitfalls of the anchoring algorithm before stepping into them
- The "came back" narrative is stronger than building one from scratch — because it is true
- Twenty years of empirical data can be used directly as grounds for the problem statement

### Negative / accepted costs

- **We give up the "something new" marketing card.** It does not fit the grammar of investors and media. Given Principle 5 (it does not have to be visible), that was never the goal to begin with
- Standards compatibility has a cost (TimeMap and Robust Links serialization, roughly two modules)
- We inherit the Memento community's expectations — sloppy RFC compliance will draw criticism instead of goodwill. That is why SPEC.md §12 includes interoperability tests

### Follow-up actions

| Item | Status |
|---|---|
| Reflected in SPEC.md v1.1 (Memento terminology, matching replacement, 7 states, TimeMap/Robust Links, Tasks) | ✅ Done |
| MANIFESTO.md 2nd revision (new lineage section, added forbidden phrasings) | ✅ Done |
| License review | ✅ Done → ADR-0002 |
| PyPI `anchor-mcp` availability | ✅ Confirmed (2026-08-16, available) |
| npm `anchor-mcp` | ⚠️ **Taken** (0.1.0). Irrelevant for now since this is a Python project, but a different name will be needed for a future JS port |
| MemGator maintenance status | ⚠️ Unconfirmed. Latest release 1.0-rc9 (2024-05). Archive fallback is an optional feature, so this is not a blocker |

---

## Final Judgment

> **Go. But do not say "something new" — say "something that came back."**

Anchor is not a house built on empty land. The blueprints were drawn 13 years ago, and the building was put up by an institution that walked away from it last year. In the meantime, the reasons that building is needed have grown a hundredfold.

---

---

## Appendix A — Prior Art Classification Map

The original table classifying the entire survey scope by threat level. The body covered only what the decision required, so the full list is kept here.

| Level | Subject | Relationship | Action |
|---|---|---|---|
| 🔴 Must inherit | Memento (RFC 7089) | Problem statement and protocol already complete | Compatible implementation + no reinvention |
| 🔴 Must inherit | W3C Web Annotation Selector | The formal standard for the anchor model | Consistent down to serialization |
| 🔴 Must inherit | Hypothesis anchoring stack | Algorithm + documented performance pitfall | Port the algorithm, avoid the failure case |
| 🟠 Adjacent, needs differentiation | changedetection.io (32k★) | Web change monitoring | Different axis — explicit comparison needed |
| 🟠 Adjacent, needs differentiation | ArchiveBox | Local web archiving | Different preservation scope |
| 🟡 Reference | Perma.cc, Robust Links | Scholarly citation preservation | Adopt the output format |
| 🟡 Reference | mcp-server-fetch (Anthropic) | The standard MCP fetcher | No cache or provenance → replacement target |
| 🟢 Unrelated | AI citation tracking tools | Brand visibility (GEO/AEO) | Different problem |
| 🟢 Unrelated | CiteCheck, Citely, SwanRef | Verifying that academic references exist | Different problem |

## Appendix B — Adjacent Products in Detail

The original comparison that grounds the body's §"Is the position open?" Refer to this table when writing the README comparison section and public-facing copy.

### B.1 changedetection.io — the only subject requiring an explicit comparison in the README

| | changedetection.io | Anchor |
|---|---|---|
| Direction | **push** — register a URL, get notified when it changes | **pull** — register a quote, verify it when needed |
| Unit | the whole page (or a CSS/XPath selected region) | **a single quote** |
| Purpose | restock, price, and policy-change monitoring | validity of evidence |
| Time | looks at the future | **looks at the past** |
| Form | Docker web service + UI | library + MCP tool |
| Noise handling | user manually specifies CSS selectors / LLM filter | content extraction + dual hashing, automatic |
| Consumer | humans | **agents** |

One lesson: this project recently added LLM integration to judge "what counts as an important change" via natural-language rules. Anchor never calls an LLM as a matter of principle, so we wrote the boundary into SPEC.md §1.3 Non-goals: **after an `ALTERED` verdict, whether the meaning changed is handed to the caller (the agent)**.

### B.2 ArchiveBox

- Self-hosted web archiver. Preserves HTML/PDF/PNG/WARC simultaneously via multiple backends — wget, SingleFile, Chrome, yt-dlp, and others
- Storage is SQLite + filesystem — a structure similar to Anchor's
- **Difference 1**: full preservation vs. content extraction. Tens of MB per snapshot against tens of KB of compressed text
- **Difference 2**: no quote-level tracking. It keeps documents, but never asks "is this sentence still there?"
- **Difference 3**: requires Docker, depends on Chrome, exposes no MCP

The relationship is **complementary**, not competitive. ArchiveBox preserves evidence; Anchor verifies it. An `anchor export --to archivebox` integration would be a natural next step.

### B.3 Perma.cc / Robust Links

Perma.cc is a permanent preservation service built by Harvard in response to link rot in legal citations. Robust Links is a spec that attaches three attributes to a link — `data-originalurl`, `data-versiondate`, `data-versionurl` — so that the original, the point in time, and the snapshot are recorded together, and **Anchor has adopted it as an output format** (SPEC.md §7.9).

```html
<a href="https://example.com/report"
   data-versiondate="2026-08-16"
   data-versionurl="https://web.archive.org/web/20260816/...">2026 report</a>
```

It is an interoperability device that lets even readers who do not use Anchor see the moment of citation. The implementation cost is a single serialization function.

### B.4 The AI citation tracking market — why it is unrelated

Searching for "AI citation" turns up two categories, and both sit on a different axis from Anchor. The reason for recording this distinction is that the risk of being confused with them in public copy is real.

**GEO/AEO marketing tools** (Profound, Scrunch, Otterly, Wrodium, etc.)
- Problem solved: "is my brand cited in ChatGPT/Perplexity answers?" — the perspective of **being cited**
- Budget comes from marketing. Pricing $29~$140+/month
- Also a signal that the market is real — Sitecore acquired Scrunch (June 2026)

**Academic reference validation tools** (CiteCheck, Citely, SwanRef, RefCheck, Hallucinator)
- Problem solved: "do this paper's references actually exist?" — hallucination detection
- Targets **static literature**. Things with a DOI that do not change
- Does not address temporal change in web pages

Anchor is a **temporal verification** tool, for the **changing web**, from the perspective of **doing the citing**. It is on the opposite side of all three axes.

## Appendix C — What to Bring In

Things that must not be reimplemented. The method (process separation / algorithm reference / code port) and the license judgment follow ADR-0002.

| Subject | Source | Use | Method |
|---|---|---|---|
| TimeMap serialization | RFC 7089 §5 | Exporting the version list | Spec implementation |
| Selector data model | W3C Web Annotation Data Model | Anchor JSON schema | Spec implementation |
| Robust Links attributes | Robust Links Spec | Exporting citations | Spec implementation |
| Anchor matching strategy order | Hypothesis anchoring architecture | SPEC §6.2 | Algorithm reference |
| Approximate string matching | Myers 1999 / `approx-string-match` family | SPEC §6.2 stage 4 | Algorithm reference |
| Anchoring test data | `hypothesis/anchoring-test-tools` | Benchmark fixtures (level C) | Collected at runtime |
| Archive fallback | MemGator (self-hosted) / Wayback CDX | Rescuing the `GONE` state | Process separation |
| Content extraction | trafilatura + readability-lxml | SPEC §5.3 | Dependency |

**There are only four things Anchor actually builds new.**

1. Assembling the pieces above into a single local tool an agent can use
2. Automatic noise removal via `raw_hash` / `text_hash` dual hashing
3. Seven verification status codes — in particular, separating `ALTERED` from `MISSING`
4. The MCP interface

That assembly and interface are the whole of it is not a weakness. **Linux did not invent POSIX either.**

### Sources

- RFC 7089 — HTTP Framework for Time-Based Access to Resource States (Memento)
- mementoweb.org/about — notice of the Time Travel service's 2025 shutdown
- Klein M, Van de Sompel H, et al. "Scholarly Context Not Found: One in Five Articles Suffers from Reference Rot." PLoS ONE 9(12): e115253 (2014)
- Jones SM, Van de Sompel H, et al. "Scholarly Context Adrift: Three out of Four URI References Lead to Changed Content." PLoS ONE 11(12): e0167475 (2016)
- W3C Web Annotation Data Model / Web Annotations Workshop Report (2014)
- Hypothesis, "Fuzzy Anchoring" and client issue #3919
- robertknight/anchor-quote benchmark
- Robust Links Specification
- MCP Specification 2026-07-28 Release Candidate
- Cloudflare AI crawler policy (announced 2026-07, effective 09-15)
