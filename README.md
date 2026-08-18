# Anchor

**AI가 인터넷에서 얻은 정보의 시간적·출처적 무결성 계층**
**A temporal & provenance integrity layer for the web evidence AI relies on**

> Anchor는 AI가 사용한 웹 근거를 시간에 걸쳐 다시 검증할 수 있게 한다.
> Anchor lets you re-verify, at any later time, the web evidence an AI used.

MCP 서버 + Python 라이브러리 + CLI. 저장소는 SQLite 파일 하나, 모델 호출 없음, 검색 없음, 안티봇 우회 없음 — 계보는 **Memento(RFC 7089)의 로컬 클라이언트**다.
An MCP server + Python library + CLI. One SQLite file, no LLM calls, no search, no anti-bot evasion — in lineage, **a local client of Memento (RFC 7089)**.

---

## 왜 필요한가 · Why

AI에게 뭔가를 물어보면 출처 링크를 달아준다. 그런데 한 달 뒤 그 페이지의 내용이 바뀌거나 사라져도 아무도 모른다. 학술 논문이 인용한 웹 콘텐츠의 약 4분의 3이 3년 안에 바뀐다는 연구가 있다. Anchor는 AI가 가져온 웹 문서를 시점별로 보관하고, 인용한 문장이 지금도 원문에 그대로 있는지 언제든 다시 확인해 준다. **검색은 하지 않는다.**

Ask an AI something and it hands you source links. A month later the page may have changed or vanished — and nothing tells you. Studies show roughly three quarters of web content cited by scholarly papers changes within three years. Anchor snapshots the web documents an AI fetches and can re-check, at any time, whether a quoted sentence still exists in the source. **It does not search the web.**

## 설치와 사용 · Install & Use

```bash
# Python 3.11+
uv venv --python 3.12 && uv pip install -e .

anchor fetch https://www.rfc-editor.org/rfc/rfc7089.html   # created
anchor fetch https://www.rfc-editor.org/rfc/rfc7089.html   # cache_hit — 0 bytes over the network
anchor cite  https://www.rfc-editor.org/rfc/rfc7089.html \
  "The HTTP-based Memento framework bridges the present and past Web."
anchor verify        # INTACT | MOVED | ALTERED | MISSING | GONE | UNREACHABLE | UNRESOLVED
anchor list
anchor timemap https://www.rfc-editor.org/rfc/rfc7089.html   # RFC 7089 TimeMap
anchor export --robust-links --format markdown
anchor stats         # hit_rate, bytes saved · 절감 바이트
anchor gc --keep 20
```

## MCP 서버로 쓰기 · As an MCP server

`mcp-server-fetch` 자리에 그대로 교체할 수 있다 — 같은 일을 하되 캐시·버전·출처가 붙는다.
A drop-in replacement for `mcp-server-fetch` — same job, plus caching, version history, and provenance.

```bash
# Claude Code CLI
claude mcp add -s user anchor -- /absolute/path/.venv/bin/anchor-mcp
```

```jsonc
// Claude Desktop 등 · or any MCP client config
{ "mcpServers": { "anchor": { "command": "/absolute/path/.venv/bin/anchor-mcp" } } }
```

도구 9종 · 9 tools: `fetch_document` `cite` `verify_citations` `diff_versions` `get_version` `list_documents` `cache_stats` `get_timemap` `export_robust_links`

### 모델이 anchor를 우선 쓰게 하려면 · Make your model prefer anchor

서버가 도구 선택을 강제할 수는 없다 — 모델은 습관적으로 내장 웹페치를 먼저 집는다. 확실히 하려면 아래 규칙을 프로젝트 규칙 파일(`CLAUDE.md`, `.cursorrules`, 시스템 프롬프트 등)에 복사한다.
A server cannot force tool choice — models habitually reach for the built-in web fetch first. To make the preference stick, copy this rule into your rules file (`CLAUDE.md`, `.cursorrules`, system prompt, …):

```markdown
- URL 본문을 열람할 때는 내장 WebFetch 대신 anchor의 `fetch_document`를 사용한다
  (캐시·버전·출처 자동). 웹 검색은 기존 도구를 그대로 쓴다 — anchor는 검색하지 않는다.
- When reading the content of a URL, use anchor's `fetch_document` instead of the
  built-in web fetch (automatic caching, versioning, provenance). Keep using your
  search tool for searching — anchor does not search.
```

## 아카이브 폴백 · Archive fallback

원본이 죽었을 때 공개 아카이브를 확인하는 폴백은 **기본 꺼져 있다** — 외부 서비스에 조용히 의존하지 않는다. `~/.anchor/config.toml`에서 명시적으로 켠다.
Checking public archives when a source dies is **off by default** — no silent dependence on external services. Enable it explicitly:

```toml
[fetch.archive_fallback]
enabled    = true
aggregator = ""   # self-hosted MemGator endpoint; empty = Wayback CDX
```

MemGator를 자체 호스팅하는 경우의 운영 지침 (SPEC §5.2 — 우회 기능이 딸린 도구를 통합할 때는, 그 기능을 쓰지 않는다는 사실이 문서에 남아야 한다):
Operational rules when self-hosting MemGator (SPEC §5.2 — when integrating a tool that ships an evasion feature, the fact that you don't use it must be on record):

| 항목 · Item | 지침 · Rule | 사유 · Why |
|---|---|---|
| `--spoof` | **절대 사용하지 않는다 · never use** | 무작위 UA 위장 — 정직한 클라이언트 원칙 위배 · random user-agent spoofing violates the honest-client principle |
| `--agent` | Anchor의 User-Agent와 동일하게 · match Anchor's UA | 책임 소재를 흐리지 않는다 · keeps accountability clear |
| `--arcs` | 명시적으로 지정 · set explicitly | 기본값 `git.io`는 2022년 종료 · the default `git.io` shortener died in 2022 |

## 계보 · Lineage

이것은 새로 발견한 문제가 아니다. This is not a newly discovered problem.

```
2009  Memento proposed — "the web has a poor memory"
2013  RFC 7089 — a time dimension for HTTP
2016  "Three out of four URI references lead to changed content"
2025  Time Travel service shut down        ◀── infrastructure gone
2026  AI agents consume the web at scale   ◀── demand explodes
      ▲
      └─ Anchor
```

Memento는 틀려서 죽은 게 아니라 **중앙 서비스로 지어졌기 때문에** 죽었다. Anchor의 로컬 우선 설계는 그 실패 모드에 대한 답이다. 서버가 없으면 폐쇄될 서버도 없다.
Memento didn't die because it was wrong — it died because it was **built as a centrally funded service**. Anchor's local-first design is the direct answer to that failure mode: no server, nothing to shut down.

자세한 내용은 · details: `docs/decisions/0001`.

## 문서 지도 · Document map

설계 문서(사양서·철학·판단 기록)는 한국어로 쓰여 있다. The design documents below are written in Korean.

```
anchor-mcp/
├── README.md                  ← 지금 이 문서 · this file
├── LICENSE                    Apache-2.0
├── NOTICE                     저작권 고지 · attribution (distributed)
├── THIRD-PARTY.md             제3자 라이선스 대장 · third-party license ledger
├── docs/
│   ├── SPEC.md                기술사양서 v1.5 · technical spec (Korean)
│   ├── MANIFESTO.md           설계 철학 · design philosophy (Korean)
│   └── decisions/             ADR — 판단 기록 (동결) · frozen decision records
├── src/anchor/                구현 · implementation (fetcher / normalize / anchoring / export / store / server / cli)
├── tests/                     단위 + 통합 · unit + integration (local fixture server, no network)
├── benchmarks/                §10 지표 게이트 · perf gates + anchoring benchmark (runtime-collected data)
├── .github/workflows/         CI: test matrix · license-audit · benchmark
└── tools/audit_licenses.py    라이선스 감사 · reproducible license audit
```

| 상황 · When you want to… | 문서 · See |
|---|---|
| 무엇을 만드는지 알고 싶다 · know what this is, precisely | `docs/SPEC.md` |
| 기능을 넣을지 말지 애매하다 · decide whether a feature belongs | `docs/MANIFESTO.md` §6·§7 |
| 왜 이렇게 설계했는지 궁금하다 · understand the design rationale | `docs/decisions/0001` |
| 새 의존성을 추가하려 한다 · add a dependency | `docs/decisions/0002` → update `THIRD-PARTY.md` |
| 릴리스를 준비한다 · prepare a release | `tools/audit_licenses.py --all-installed --fail-on-copyleft --check-floors` |

**대장과 판단 기록은 성격이 다르다.** `THIRD-PARTY.md`는 "지금 무엇을 쓰고 있나"를 답하며 매 릴리스 갱신한다. `decisions/`는 "왜 그렇게 하기로 했나"를 답하며 **동결**한다 — 판단이 바뀌면 새 ADR로 대체한다.
The ledger and the decision records serve different purposes: `THIRD-PARTY.md` answers "what are we using now" and is updated every release; `decisions/` answers "why we chose this," is **frozen**, and is superseded by new ADRs, never edited.

## 현재 상태 · Status

| 항목 · Item | 상태 · State |
|---|---|
| 기술사양서 · Spec | v1.5 (2026-08-18) |
| 구현 · Implementation | **v1.1.1** (2026-08-18) — 데이터가 든 구버전 DB의 마이그레이션 수정 · fixes migration of pre-existing databases that contain rows. 테스트 362개 · 362 tests. CI: 3 OS × Python 3.11–3.13, license gate, benchmark gate |
| 실사용 관찰 · Field observation | 30일 hit_rate 관찰 창 진행 중 · 30-day hit-rate window in progress (`anchor stats`) |
| 라이선스 감사 · License audit | 카피레프트 강제 0건 · zero forced copyleft (선택형 `tld`는 MPL-1.1 선택 · disjunctive `tld` elected as MPL-1.1, THIRD-PARTY §4.4) |

### 알려진 미해결 항목 · Known open items

- **npm `anchor-mcp` 선점됨** — Python 프로젝트라 당장 무관 · npm name taken; irrelevant until a JS port
- **MemGator 유지보수 상태 미확인** · maintenance status unverified — 아카이브 폴백은 선택 기능 · fallback is optional

## 크레딧 · Credits

Anchor는 다음 성과 위에 서 있다 (SPEC §14). 이것은 형식적 예의가 아니다 — 이 분야에는 20년간 이 문제를 붙들어온 사람들이 있고, Anchor는 그들의 표준을 조립한 것이다.
Anchor stands on the following work (SPEC §14). This is not a courtesy — people have worked on this problem for twenty years, and Anchor is an assembly of their standards.

- **Memento — RFC 7089**, Herbert Van de Sompel, Michael L. Nelson, Robert Sanderson et al. — the time dimension for the web
- **W3C Web Annotation Data Model** — TextQuoteSelector / TextPositionSelector
- **Hypothesis** — production fuzzy anchoring, and the honesty of documenting its failure modes in public
- **Klein, Van de Sompel, Jones et al.** — the empirical studies of reference rot (PLOS ONE 2014, 2016)
- **Robust Links Specification** — citation markup convention
- **Sawood Alam, Michael L. Nelson** — MemGator, the self-hostable aggregator that outlived the central service
- **Robert Knight** — `anchor-quote`, the benchmark showing why bounded approximate matching beats diff-match-patch

## 라이선스 · License

Apache-2.0. 제3자 구성요소는 `THIRD-PARTY.md`, 고지는 `NOTICE` 참조.
Apache-2.0. See `THIRD-PARTY.md` for third-party components and `NOTICE` for attributions.
