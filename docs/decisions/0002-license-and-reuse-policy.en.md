# ADR-0002 — License and Reuse Policy (EN)

| | |
|---|---|
| Status | **Accepted** |
| Decided | 2026-08-16 |
| Supersedes | — |
| Related | THIRD-PARTY.md · NOTICE · SPEC.md §11, §12 · ADR-0001 |

> **Translation note**: This is an English translation of
> `0002-license-and-reuse-policy.md`. The Korean version is normative — if the
> two ever disagree, the Korean text governs. This record is frozen in both
> languages: to revise a judgment, write a new ADR rather than editing this one.
> Neither version is legal advice.

> This document holds the **criteria for judgment**. Which licenses are actually in use is recorded in `THIRD-PARTY.md`. When adding a new dependency or taking in someone else's code, apply the principles in this document and record the outcome in the ledger.
>
> This is not legal advice. The author is not a lawyer, and expert review is recommended before commercial distribution or organizational adoption.

---

## Division of Roles Between Documents

Let us settle this first, to prevent confusion.

| | THIRD-PARTY.md | This document (ADR-0002) |
|---|---|---|
| Kind | Ledger — state | Decision record — rationale |
| Question it answers | "What are we using right now?" | "Why did we decide to do it that way?" |
| Updated | Every release, on every dependency change | **Frozen.** If the judgment changes, a new ADR |
| If it is wrong | License violation | We relitigate the same discussion from scratch |
| Location | Repository root (shipped) | `docs/decisions/` |

Mix the two and both become useless. Once argumentation creeps into the ledger, updates get postponed; once the decision record keeps being edited, "why we decided that at the time" disappears.

---

## Decision (Summary)

1. Anchor is distributed under **Apache-2.0**.
2. **No copyleft component whatsoever** (GPL / AGPL / LGPL / SSPL / BUSL) is included.
3. When taking in someone else's work, one of **three paths** is explicitly chosen.
4. **Credit is given beyond legal obligation.**
5. Compliance is enforced by a **CI gate**, not by human memory.

---

## Principles

### Principle A — A specification and an implementation are entirely different things

**RFC 7089 (Memento), the W3C Web Annotation Data Model, Robust Links, RFC 9110, and RFC 9309 are documents written to be implemented.** Writing code that follows a specification requires no permission whatsoever, no royalties, and no license-attribution obligation. That is the very purpose of a standard's existence.

Anchor is entirely free to serialize a TimeMap, use the TextQuoteSelector structure, and emit the `data-versiondate` attribute.

There is one thing to watch for — **verbatim redistribution of the body text of an RFC** carries IETF Trust conditions (BCP 78). Quotation and reference are fine.

> This is where the rationale for adopting interoperability in ADR-0001 D3 comes from. **Half of the design is secured at zero legal cost.**

### Principle B — The Apache-2.0 outbound license opens in one direction only

| Can be taken in | Cannot be taken in |
|---|---|
| MIT, BSD-2/3-Clause, ISC | **GPL-2.0 / GPL-3.0** |
| Apache-2.0 | **AGPL-3.0** |
| CC0, Unlicense | **LGPL** (when statically linked) |
| MPL-2.0 (subject to the per-file condition) | SSPL, BUSL and other source-available licenses |

As the Apache Software Foundation has stated explicitly, **Apache-2.0 software may be included in a GPLv3 project, but not the reverse.** The incompatibility holds in one direction only. Taking in GPLv3 code would force all of Anchor to become GPLv3, and that would make Apache-2.0 distribution impossible.

### Principle C — Algorithms are not subject to copyright, but code is

Myers's bit-vector approximate string matching is an **algorithm** published in a 1999 paper. Reading the paper and implementing it yourself is free.

Copy **somebody's code** that implements that algorithm, and that code's license applies. If it is BSD/MIT, leaving the copyright notice suffices; if it is GPL, it cannot be used.

**Practical rule: we do not even read GPL code.** Claiming "I wrote it myself" after having read it invites a derivative-work dispute. We consult only papers and permissive implementations. This is a habit more than a rule, which is exactly why it has to be written down to be kept.

---

## The Three Paths of Reuse

There are three ways to take something in, and each carries a different legal burden. **Before taking anything in, decide first which of the three it is.**

```
┌─────────────────────────────────────────────────────────┐
│ Path 1: out-of-process invocation         no obligation │
│   → call MemGator over HTTP                             │
├─────────────────────────────────────────────────────────┤
│ Path 2: algorithm reference only         no obligation* │
│   → implement it yourself from papers / write-ups       │
│   * but: GPL sources are not read                       │
├─────────────────────────────────────────────────────────┤
│ Path 3: code porting             attribution obligation │
│   → translate / adapt permissive code                   │
└─────────────────────────────────────────────────────────┘
```

### Application 1 — MemGator is Path 1

It is MIT, so it could be included, but **we decide not to include it.** It is a finished server written in Go and already provides TimeMap / TimeGate / Memento endpoints. Anchor merely calls it over HTTP. License mixing never arises in the first place.

MemGator provides `/api`, a Time Travel API-compatible fallback endpoint — **it is designed as a drop-in replacement for tools that used the now-shut-down LANL service.** The archive fallback of SPEC.md §5.2 is effectively completed for free.

#### Operational constraints (mandatory)

| Item | Directive | Reason |
|---|---|---|
| `--spoof` | **Never use** | Randomized user-agent spoofing. Directly violates Principle 3 (honest client) |
| `--agent` | Match Anchor's User-Agent | Do not blur where responsibility lies |
| `--arcs` | Specify explicitly | The default is a `git.io` short URL, and that service shut down in 2022 |

**When integrating a tool that ships with evasion features, the fact that those features are not used must be left in documentation, not merely in code.**

### Application 2 — Anchor matching is Path 2

The Hypothesis family of libraries is all JavaScript. Since it has to be rewritten in Python anyway, algorithm reference rather than code porting is the natural fit.

What matters is that the most valuable asset sits on the side with zero legal burden.

| Reference | Nature | Burden |
|---|---|---|
| Hypothesis "Fuzzy Anchoring" blog post | Design write-up | None |
| The four-step procedure in the W3C Workshop report | Public standards document | None |
| Hypothesis client issue #3919 | Public issue tracker | None |
| anchor-quote benchmark figures | Public README | None |
| Myers 1999 bit-vector algorithm | Academic paper | None |

The knowledge that "fuzzy matching blows up on a long document with a short quote" is experience, not code, and it is written down in a public issue.

The Hypothesis client itself is BSD-2-Clause, so porting it would be permitted, but in that case the original copyright notice would be required in the file concerned. Even without porting, adding the following comment is recommended.

```python
# anchoring/matcher.py
#
# The stage structure of the matching strategy was informed by the
# anchoring approach of the Hypothesis client (BSD-2-Clause, Copyright
# (c) 2013-2019 Hypothes.is Project and contributors). The code was
# written anew in Python, and the approximate-matching stage uses an
# edit-distance bound instead of diff-match-patch.
```

There is no legal obligation. The reason for adding it is in §"Credit Beyond Obligation" below.

### Application 3 — Approximate matching is Path 2, self-implemented if needed

Using the fuzzy matching of the `regex` module (`{e<=k}`) removes the need to implement it ourselves. If removal becomes necessary, we write it from the Myers paper. The JS implementation (`approx-string-match`) is used only for behavioral comparison; its code is not transcribed line by line.

---

## A Conflict We Found — `trafilatura`

**This is the only issue that actually tripped us up during the survey. It is worth recording.**

trafilatura **changed its license from GPLv3+ to Apache-2.0** in the v1.8.0 release. The current distribution (2.2.0) is Apache-2.0.

The problem is that this is a **core dependency for body-text extraction**. Without an explicit version floor, a release below 1.8.0 could be installed in a user's environment, and at that moment an Apache-2.0 project would be structured on top of a GPLv3 library.

```toml
"trafilatura>=1.8.0",   # Below v1.8.0 it is GPLv3+. This is a license requirement, not a feature one.
```

### What we learned here

> **Licenses change. Human memory cannot guard against it.**

Every additional dependency adds risk, and transitive dependencies are not even visible. That is why the CI gate in §"Enforcement" was decided.

---

## Two Special Cases

Two packages are not under a single license. The choice and its ripple effects are recorded.

### `regex` — `Apache-2.0 AND CNRI-Python`

CNRI-Python (the Python 1.6 license) is an OSI-approved permissive license, so it poses no problem for Apache-2.0 distribution.

**One ripple effect**: CNRI-Python is classified as incompatible with GPLv2. That is, **the path of relicensing all of Anchor to GPLv2 in the future is blocked by this dependency.** As long as Apache-2.0 is retained this is irrelevant, but it is recorded. If removal becomes necessary, it can be replaced by our own Myers implementation.

### `blake3` — `CC0-1.0 OR Apache-2.0`

A disjunctive dual license. **We choose the Apache-2.0 side.**

Two reasons: matching the distribution license keeps attribution handling simple, and some organizations take issue with CC0's lack of a patent grant. The fact of the choice is stated explicitly in `NOTICE`.

---

## Test Data — A Different Kind of Constraint Than Code

SPEC.md §12 decided to use public annotation data for benchmarking, and this carries a problem distinct from licensing.

| Item | Issue |
|---|---|
| Hypothesis annotation text | It is **user-generated content**, not software. The repository's BSD license does not apply to it |
| The original web pages that were annotated | Each site's copyrighted work. Committing them as fixtures amounts to redistribution |
| API terms of use | Bulk retrieval requires checking the terms of service |

### Decision — three fixture tiers (SPEC.md §12.1)

| Tier | Purpose | Source | Committed |
|---|---|---|---|
| A | Golden (extraction accuracy) | Public domain, CC documents, our own works | ✅ |
| B | Mutation (7-state verdict) | Synthesized by programmatically mutating tier-A material | ✅ |
| C | Anchor benchmark (performance) | Public annotation data collected at runtime | ❌ script only |

Not committing tier C is the crux. Hypothesis's `anchoring-test-tools` operates by taking a URL list as input in exactly this pattern, and we follow the same structure.

That the mutation tests are adequately served by synthesis also matters — the method programmatically applies ad insertion, paragraph moves, and sentence edits to the source text, so it does not matter what the original is.

---

## Enforcement

### NOTICE / THIRD-PARTY.md / SPDX

```
anchor-mcp/
├── LICENSE                    # Full text of Apache-2.0
├── NOTICE                     # Copyright notices, dual-license choice stated
├── THIRD-PARTY.md             # Ledger
└── tools/audit_licenses.py    # Reproducible audit
```

One line of `# SPDX-License-Identifier: Apache-2.0` at the top of every source file. Automated tools can read it, and it saves time at audit later.

### CI gate (SPEC.md §12.2)

| Check | Failure condition |
|---|---|
| Copyleft block | GPL / AGPL / LGPL / SSPL / BUSL in the dependency list |
| `trafilatura` version floor | Installed version < 1.8.0 |
| `NOTICE` sync | A new runtime dependency is missing from `THIRD-PARTY.md` |

Run identically both locally and in CI with `tools/audit_licenses.py --fail-on-copyleft --check-floors`.

---

## Credit Beyond Obligation

Legally, merely referencing BSD-2-Clause code without porting it creates no attribution obligation. Adding it anyway is a matter of strategy.

This field is small. Van de Sompel, Nelson, Alam, Klein, Jones, Knight — these are people who have wrestled with the same problem for twenty years, and they know one another. **A project that quietly reimplements their standards and experience and a project that explicitly declares its lineage receive completely different treatment.**

The former is ignored or provokes a backlash. The latter borrows twenty years' worth of trust. For something purchasable at the cost of a single NOTICE file, that is cheap.

This follows the same logic as ADR-0001 D1; here we state explicitly that it is a voluntary choice, not a legal formality.

---

## Consequences

### Positive

- The risk of copyleft contamination is structurally blocked (CI gate)
- Half of the design (implementing specifications) costs nothing legally
- License changes (such as trafilatura's) are caught by tooling rather than by people

### Negative / what we accept

- Good libraries from the GPL ecosystem cannot be used. So far there has been no case where this was an actual constraint
- The GPLv2 relicensing path is blocked because of `regex`. Irrelevant under the policy of staying on Apache-2.0
- Because tier-C fixtures are not committed, the benchmark depends on the network. Handled as an opt-in run in CI

### When this policy must be revisited

- When a new dependency is copyleft and cannot be replaced
- When a demand arises to redistribute Anchor under a different license
- When a feature that exchanges user data is added, such as a shared cache (a v1.3 candidate) — a new axis, data licensing, comes into existence

In those situations, do not edit this document; **write anew, from ADR-0003 onward.**

---

### Sources

- Apache Software Foundation, "Apache License v2.0 and GPL Compatibility"
- trafilatura README / `pyproject.toml` / v1.8.0 release notes
- oduwsdl/MemGator repository (MIT, `--spoof` option documentation)
- hypothesis/client repository `LICENSE` (BSD-2-Clause, Copyright 2013-2019)
- OSI, full text of the BSD 2-Clause License
- PyPI JSON API, npm registry (retrieved 2026-08-16)
