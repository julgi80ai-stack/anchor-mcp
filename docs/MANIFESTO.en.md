# Anchor (EN)

**A temporal and provenance integrity layer for the information AI takes from the internet**

*Technical introduction and design philosophy · 2026 · Second edition*

> **Translation note**: This is an English translation of `MANIFESTO.md`. The
> Korean version is normative — if the two ever disagree, the Korean text governs.
> Section numbers match the Korean version so cross-references work in both.

---

## 0. One sentence

> **Anchor makes the web evidence used by AI re-verifiable over time.**

That one sentence is the whole project. A feature request that falls outside it belongs to a different project.

---

## 1. How the internet works right now

```
AI → Web → information → answer
```

The flow looks like it works. But once AI starts using the internet **continuously rather than once**, four questions appear between those arrows, and nobody answers them.

1. **When was that information fetched?**
2. **Was the sentence the AI quoted actually in the source?**
3. **Does that page still say the same thing?**
4. **Can the evidence used last month be checked again?**

All four are questions about **time**. And the current stack has no layer responsible for time.

Search engines answer "what exists now." Models answer "what it means." **"Since when has it been so, and is it still so" — nobody answers that.**

Anchor sits in that spot.

---

## 2. The name

Links on the web were originally called **anchors**. The `a` in the `<a>` tag is anchor. It meant dropping anchor at a particular point in a document.

But the web's anchor is **only a coordinate in space; it guarantees nothing about time.** A link points to the same place yesterday and today, but it does not tell you the contents there are the same. For thirty years we did not treat that as a problem. A person opens the link and sees the change.

AI does not see it.

**Anchor is a project that gives links their time axis back.** An anchor does not stop the ship. It only keeps the ship from losing its position while it moves.

---

## 3. Lineage — we are not the first

This is not a newly discovered problem. It was defined long ago, measured, and standardized.

```
2009 ─── Memento proposed — "the web has a bad memory"
2013 ─── RFC 7089 — adding a time dimension to HTTP
2014 ─── "one in five articles suffers reference rot"
2015 ─── Time Travel service launched (LANL)
2016 ─── "three out of four references lead to changed content"
  ⋮
2025 ─── Time Travel service shut down     ◀── infrastructure gone
2026 ─── AI agents consume the web at scale ◀── demand surges
         ▲
         └─ Anchor
```

**RFC 7089 (Memento)** is the standard that added a time dimension to HTTP. Original resource, past versions, version lists, datetime negotiation — the grammar for reaching the web's past was already complete thirteen years ago.

The service that actually ran that standard was **Time Travel**, at Los Alamos National Laboratory. Official infrastructure that, from 2015, found past pages across many web archives. **In late 2025 it was shut down as a management decision.** mementoweb.org became a static site with nothing but documents left on it.

Look at that order again. **The year after the infrastructure that guarded the web's time closed its doors, AI agents began reading the web more heavily than ever.**

Memento did not die because it was wrong. **It died because it was built as a central service dependent on institutional funding.** Anchor's local-first design is a direct answer to that failure mode. With no server, there is no server to shut down, and no line item to vanish in a budget meeting.

> **Anchor is not something new. It is something returned.
> Only this time it runs on your disk, not on a server.**

Anchor uses Memento's vocabulary, exports version lists in RFC 7089 form, expresses quotes with the W3C Web Annotation selector model, and writes footnotes by the Robust Links convention. It does not create a new standard. **It assembles what already exists so that agents can use it.**

---

## 4. What it does not compete with

This is Anchor's most important strategic asset.

```
   Agent A ─┐
   Agent B ─┤
   Agent C ─┼──▶  Anchor  ──▶  Web
   Agent D ─┤
   Agent E ─┘
```

**Anchor does not search.**

- It does not compete with Google.
- It does not compete with Naver.
- It does not compete with Perplexity.
- It is not bound to OpenAI, Anthropic, or any open model.

They **find** information. Anchor takes the problem one layer below: **how to trust and remember** the information once it has been found.

Not searching is not a deficiency; it is a design decision. The moment Anchor searches, it steps into the same ring as dozens of companies, and as a project built by one person it dies on the spot. Because it does not search, Anchor **can sit underneath every search.**

> Fight up top and one wins. Lie underneath and you are under all of them.

For the same reason, Anchor does not build a model. Because it does not build a model, every model can use it.

### Telling it apart from its neighbors

Three things are easy to confuse with Anchor. Each sits on a different axis.

| | What they ask | What Anchor asks |
|---|---|---|
| **Change monitoring tools** | "Has this page changed?" (future) | "Is the sentence I quoted still there?" (past) |
| **AI citation tracking tools** | "Does my brand show up in AI answers?" (the cited side) | "Is the evidence I used still valid?" (the citing side) |
| **Reference checking tools** | "Does this paper exist?" (static literature) | "Does this web page still say that?" (the changing web) |

On all three axes Anchor is on the opposite side. That combination is empty.

---

## 5. Small product, large protocol philosophy

Anchor v1.0 is one SQLite file and nine tools. No server cluster, no account system, no dashboard.

This is not small ambition. **It is what the things that become infrastructure have in common.**

| Project | Size at the start | Where it is now |
|---|---|---|
| SQLite | A single-file embedded DB | The most widely deployed database on earth |
| curl | A CLI that sends one HTTP request | Inside nearly every device |
| Linux | A kernel someone made as a hobby | The floor of the cloud |

None of them started with a white paper announcing a new layer. **Someone built it because they wanted to use it, built it well, and gave it away.**

And Linux did not invent POSIX. The specification already existed, and Linux was a free implementation of it. That is exactly the relationship between Anchor and Memento.

---

## 6. Five principles

### Principle 1 — Local First

Anchor runs on the user's machine, on the user's disk, with the user's data. It does not ask a server. It sends no telemetry. Cached evidence still reads when the internet is down.

If the tool that verifies evidence is itself an unverifiable remote service, it has only moved the problem. **And a remote service can be shut down. We just watched it happen.**

### Principle 2 — Value at N=1

Features that depend on network effects do not go into v1.0. Even used alone, it cuts fetches, cuts tokens, and verifies quotes. If a second user never appears, the first user has already gained.

This is the **only** path by which one person can build infrastructure. A design that requires both sides to adopt at once requires an organization.

### Principle 3 — Honest Client

Anchor does not run proxies, does not spoof browser fingerprints, does not ignore robots.txt, and does not solve CAPTCHAs. **A 403 is reported as a 403.**

The moment a single evasion feature goes in, Anchor becomes a scraper and loses its legitimacy as infrastructure. Trust in infrastructure comes from restraint, not from capability.

### Principle 4 — Fetch Less, Cite Better

By Cloudflare's mid-2025 measurements, Google's search crawler took about 14 pages for every one human visit. On the same basis OpenAI's crawler was at 1,700:1 and Anthropic's at 73,000:1. And more than half of AI crawler traffic goes to **re-fetching pages that have not changed**.

That asymmetry is why the web is closing its doors to AI. From September 2026, Cloudflare blocks agent crawlers by default on ad-serving pages.

Anchor removes re-fetches with conditional requests and body hashes. For the user this is a cost saving, but **for the web as a whole it turns the relationship between AI and publishers from extraction into accounting.** An agent that uses Anchor takes less and cites what it took accurately.

This principle is not moral decoration. In a world where doors are closing, **only the honest client survives.**

### Principle 5 — Invisible by Design

Just as nobody thinks about Linux running inside an AWS server, Anchor's name does not need to appear on screen when an agent verifies a quote.

**If it works well, people need not know the name.** That is not a failure; that is the goal.

---

## 7. What it stands against

A brand is defined not by what it supports but by **what it stands against**.

Anchor stands against one thing.

> **Plausibility replacing fact.**

The most dangerous output AI produces is not a wrong answer. It is an answer that is wrong but carries a source, where the source is a real URL and the prose is smooth enough that **nobody checks it**.

Three kinds of rot overlap here.

- **hallucination** — a sentence that was never in the source gets quoted.
- **link rot** — the source disappears.
- **citation drift** — the link is alive but the content has changed.

The third is the most dangerous. Because nothing breaks, nobody notices.

And this is not conjecture; it is measured. **About 75% of the web content referenced by scholarly articles changed to some degree within three years**, and **one in five** STM articles suffers reference rot — findings from an analysis of over a million references. The paper's title is our problem statement verbatim — *"Three out of four references lead to changed content."*

Anchor's `ALTERED` state exists precisely to catch this.

Anchor does not say to distrust AI. It says **make it checkable**. And the judgment is made by a person — Anchor reports only the fact that a sentence changed; whether that was a typo fix or a reversal of position, it does not determine.

---

## 8. Who it is for

| Audience | The problem they have | What Anchor gives them |
|---|---|---|
| People writing papers and reports | No way to know whether a quote from three weeks ago still holds | Bulk re-verification of every citation before submission |
| Agent developers | Scraping the same page again every session | Cache and provenance through one MCP tool |
| Research teams | Cannot answer "where did this number come from" | A snapshot of the source as it stood when quoted |
| Regulatory and audit response | Demands to trace the evidence behind AI output | Fetch time, hashes, version history |
| Publishers | AI traffic that leaves nothing but load | A client that honors conditional requests |

The first user is the person who built it. That fact is not hidden.

---

## 9. Why now

By mid-2026 the agent protocol stack has hardened from the bottom up.

- **MCP** — between agent and tool. Effectively settled.
- **A2A** — between agent and agent. 1.0 under neutral governance.
- **WebMCP** — between agent and website. W3C draft, browser preview.

All three layers answer the question **"how do I get access."**

**"How do I trust what I got, over time"** is answered by none of them.

Three things converged at once.

1. **The infrastructure is gone** — Memento Time Travel, which handled this problem for twenty years, shut down at the end of 2025.
2. **The web is closing** — Cloudflare blocking by default, pay per use. Keeping what you already fetched becomes the advantage.
3. **Demand has surged** — agents read the web, and demands for evidence behind their output are rising from regulation, audit, and academia alike.

The vacancy and the demand met in the same year. If not now, this spot too becomes somebody's proprietary service. And proprietary services close again.

---

## 10. The definition of success

Not user counts, not stars, not funding.

> **When someone reimplements Anchor in another language.**

At that moment Anchor stops being software and becomes a specification. That is why the spec document must be short and clear, why the status codes must not exceed seven, and why the storage format must be readable by others. Making reimplementation easy is itself the strategy.

The indicators before that stage are these.

1. The person who built it uses it daily on real report work (v0.3)
2. A 30-day cache hit rate above 80% — the tool proves its own reason to exist (v0.4)
3. Someone other than the person who built it opens an issue
4. Someone other than the person who built it adds a feature
5. A reimplementation appears

---

## 11. Message assets

### Taglines

**Primary**
> Anchor — evidence should still be evidence later.
> *(KR: 근거는 시간이 지나도 근거여야 한다.)*

**Alternates**
> Puts the time axis back into links.
> *(KR: 링크에 시간 축을 되돌려준다.)*
>
> The infrastructure that guarded the web's time shut down. This time it runs on your disk.
> *(KR: 웹의 시간을 지키던 인프라는 문을 닫았다. 이번엔 당신의 디스크 위에서.)*
>
> Finding is theirs. Remembering is here.
> *(KR: 찾는 건 그들에게. 기억하는 건 여기에.)*
>
> An AI's memory of the internet, and verification of where it came from.
> *(KR: AI의 인터넷 기억, 그리고 출처 검증.)*

### 30 seconds (non-technical)

Ask an AI something and it gives you a source link. But a month later, if that page changes or disappears, nobody notices. There is research showing that three quarters of the web pages cited by scholarly articles change within three years. Anchor keeps the web documents an AI fetched, version by version, and lets you check at any time whether the sentence it quoted is still in the source. It does not search. Plenty of places already do search well.

### 2 minutes (technical)

Anchor is a local-first fetch cache that runs as an MCP server. It combines three things. First, a cache built on conditional requests and body hashes — it hashes the normalized body rather than the raw bytes, so ad slots and timestamp noise do not fool it. Second, version preservation — when a document changes it keeps a new snapshot, and a version referenced by a quote is never deleted. It can be exported as an RFC 7089 TimeMap. Third, anchors — using the W3C Web Annotation Selector model it stores the text surrounding a quote, so it can find that sentence again after the document changes. Results come back as one of seven states: intact, moved, altered, missing, the document itself gone, unreachable, and undetermined. Of these, the separation of `ALTERED` from `MISSING` is where most of the practical value lies. Storage is a single SQLite file; it calls no model and implements no anti-bot evasion. The only thing newly invented here is the way the pieces are assembled — every standard underneath belongs to someone else.

### Terminology rules

| Use | Do not use | Reason |
|---|---|---|
| reference rot | — | The precise term in the Klein/Van de Sompel lineage |
| citation drift | content drift | Among developers, "content drift" means ML concept drift |
| 인용 표류 / 참고문헌 부패 | — | The Korean renderings |

### Expressions we will not use

For brand consistency, the expressions below are not used in documents, READMEs, or talks.

| Forbidden | Reason |
|---|---|
| "AI search", "next-generation browser" | Sets up the wrong competitive frame. Anchor does not search |
| "revolution", "paradigm shift", "game changer" | Infrastructure does not talk like this |
| "world's first", "a new standard" | **Not true.** RFC 7089 came out thirteen years ago |
| "perfect fact-checking", "hallucination-free" | Overstated and untrue. Anchor gives **verifiability**; it does not determine truth |
| "enterprise-grade", "unlimited scale" | Does not match what v1.0 actually is |
| "Don't trust AI" | Fear marketing. Anchor's posture is verification, not suspicion |

### One-page summary

| | |
|---|---|
| **What** | A temporal and provenance integrity layer for the web evidence AI uses |
| **How** | Conditional cache + version preservation + quote anchor re-verification |
| **Lineage** | A local client implementation of Memento (RFC 7089) |
| **Form** | MCP server + Python library, a single SQLite file |
| **Does not do** | Search, models, crawling, anti-bot evasion, telemetry, semantic judgment |
| **Competitors** | None (deliberately) |
| **License** | Apache-2.0 |
| **Condition for success** | Someone else reimplements it |

---

## 12. Acknowledgments

Anchor stands on the following people and projects. Putting this near the front of the document is this project's posture.

- **Herbert Van de Sompel, Michael L. Nelson, Robert Sanderson** and others — Memento / RFC 7089. The original work that brought a time dimension to the web
- **Martin Klein, Shawn M. Jones** and others — empirical research on reference rot and content drift
- **W3C Web Annotation Working Group** — the data model for quote selectors
- **Hypothesis** — the working implementation of fuzzy anchoring, and the honesty to publish its failures too
- **Robust Links** — the citation notation convention

What we do is assemble this so that agents can use it. We claim nothing beyond that.

---

*This document is not Anchor's technical specification but its criterion for judgment. When it is unclear whether a new feature belongs, decide by holding it against the five principles in §6 and what §7 stands against.*

*The technical specification is in `docs/SPEC.md`; the grounds for design decisions are in `docs/decisions/`.*
