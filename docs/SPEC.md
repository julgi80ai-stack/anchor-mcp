# Anchor — 출처 추적형 페치 캐시 (KR)

**기술사양서 v1.8 (완성 시점 기준)**

| 항목 | 내용 |
|---|---|
| 프로젝트명 | Anchor (`anchor-mcp`) |
| 한 줄 정의 | AI 에이전트가 가져온 웹 문서의 **버전·출처·인용 유효성**을 추적하는 로컬 우선 캐시 계층 |
| 계보 | **Memento (RFC 7089)의 로컬 클라이언트 구현** + W3C Web Annotation Selector |
| 배포 형태 | MCP 서버 (stdio / Streamable HTTP) + Python 라이브러리 |
| 라이선스 | Apache-2.0 (제3자 구성요소 대장: `THIRD-PARTY.md`) |
| 저장소 | SQLite 단일 파일 (외부 서비스 의존 없음) |
| 최소 요구사항 | Python 3.11+ |
| 준거 스펙 | RFC 7089, RFC 9110 (조건부 요청), W3C Web Annotation Data Model, MCP 2026-07-28 |
| 설계 근거 | `docs/decisions/0001` (선행기술·포지셔닝), `docs/decisions/0002` (라이선스·재사용) |

> **v1.7 → v1.8 변경 요약**: 2차 감사 결함 중 **4단계(정직한 클라이언트 계약, 군집 2)** 의 조치를 반영했다. robots.txt의 3xx를 따라가고 선두 BOM을 무시하며 크기 상한·타임아웃을 적용하고(§5.2, §5.4), 빈 User-Agent를 설정 단계에서 거부하고(§5.4), 애그리게이터가 지목한 URI-M에도 robots 판정을 걸었다(§5.2). **리다이렉트가 문서의 정체성을 망가뜨리지 않도록** 영구/일시를 구분하고, 별칭·문서 병합·멱등 생성 규칙을 세우고(§5.1), 검증자를 정본 URL 홉에만 싣고 `https → http` 강등을 거부했다(§5.4). 전체 목록은 §15 참조.
>
> **v1.6 → v1.7 변경 요약**: 2차 감사 결함 중 **3단계(판정 정확성, 군집 5)** 의 조치를 반영했다. 근사 검색이 코어마다 **k 이하인 모든 자리**를 후보로 삼게 하고(§6.2), 3단계 후보 상한에 걸리면 확정하지 않고 4단계로 넘기고(§6.2), 4단계 결과에 **문맥 뒷받침 관문**을 세워 다른 절의 형제 문단이 "현재 모습"으로 제시되지 않게 하고(§6.2), 편집거리 상한의 문자 체계 반영을 연속 함수로 바꾸고(§6.2), 예산 확인 주기를 DP 칸 수로 환산하고(§6.2), 절단 시 `ALTERED`도 보류하게 했다(§6.3). 4단계의 실효 문서 길이 한계를 명시했다(§6.2, §10). **스키마를 v6으로 올려 관측의 시간축을 도입하고**(§4.1) `latest~N`을 그 축 위에서 정의했다(§7.4). 전체 목록은 §16 참조.
>
> **v1.5 → v1.6 변경 요약**: 2차 병렬 감사에서 실증된 결함 101건 중 1단계(정규화) 조치를 반영했다. **정규화 규칙을 재설계해 NORM_VERSION 3으로 올렸다**(§5.3) — 줄과 블록을 먼저 인식하고, 지우는 규칙은 산문 줄 안에서만 적용한다. 인용문 검색을 블록 구분자에 유연하게 바꾸고(§6.1), 골든 코퍼스가 정규화 규칙을 실제로 밟도록 요구하고(§12), 데이터가 든 구버전 DB의 마이그레이션을 테스트 대상으로 명시했다(§12). 전체 목록은 §17 참조.
>
> **v1.4 → v1.5 변경 요약**: 병렬 감사에서 실증된 결함 52건의 조치를 반영했다. 스키마를 v5로 올려 "현재 원문 버전" 포인터·리다이렉트 별칭·출처별 버전 유일성을 도입하고(§4.1), 정규화 규칙을 개정해 화면 복사 인용이 성립하게 하고(§5.3, NORM_VERSION 2), 리다이렉트 홉마다 robots를 판정하고 robots 5xx를 거부로 바꾸고(§5.2), 앵커 임계와 편집거리 비율에 문자 체계를 반영하고(§6.1, §6.2), 예산을 매칭 단계 내부에서 강제하고(§6.2), 전역 락을 URL 단위로 좁혔다(§10). 전체 목록은 §18 참조.
>
> **v1.3 → v1.4 변경 요약**: v1.0 릴리스 시점 정리. 설정 로딩을 표준 라이브러리로 확정하고 `pydantic-settings`를 의존성에서 제거하고(§9, §11), 앵커 벤치마크의 선택 실행 조건을 명문화하고(§12), 개발 의존성 정책을 대장에 위임했다(§11.2). 전체 목록은 §19 참조.
>
> **v1.2 → v1.3 변경 요약**: v0.1~v0.4 구현에서 확정된 사항을 반영했다. `versions.pipeline_version` 컬럼과 `renormalized`·`unchanged` outcome을 정식화하고(§4.1, §5.2, §5.3, §7.1), URL 정규화의 추적 파라미터 제거 목록을 축소하고(§5.1), robots 캐시 영속화와 요청 순서를 명확화하고(§4.1, §5.2), mcp-server-fetch 호환 청크 읽기를 추가하고(§7.1), Tasks 와이어 형식의 SDK 제약을 기록했다(§7.0). 전체 목록은 §20 참조.
>
> **v1.1 → v1.2 변경 요약**: 라이선스 감사 결과를 반영했다. `trafilatura>=1.8.0` 하한을 필수로 지정하고(§11), MemGator 운영 지침을 명문화하고(§5.2, §9), 테스트 픽스처의 출처 정책을 3분류로 나누고(§12), CI에 라이선스 게이트를 추가했다(§12). 전체 목록은 §21 참조.
>
> **v1.0 → v1.1 변경 요약**: 선행기술 조사 결과를 반영해 Memento 호환성을 도입하고(§2, §5.2, §7.8), 앵커 매칭 알고리즘을 성능 안전한 방식으로 교체하고(§6.2), 검증 상태를 7개로 확장하고(§6.3), MCP 최신 스펙의 Tasks 확장을 채택했다(§7.0). 전체 목록은 §22 참조.

---

## 1. 문제 정의

### 1.1 배경

AI 에이전트가 웹을 사용할 때 세 가지 낭비와 한 가지 신뢰 결손이 반복된다.

1. **재페치 낭비** — 변하지 않은 페이지를 매번 다시 가져온다. Cloudflare 추정에 따르면 AI 크롤러 트래픽의 절반 이상이 여기에 쓰인다.
2. **토큰 낭비** — 같은 문서를 세션마다 다시 파싱하고 다시 컨텍스트에 넣는다.
3. **정규화 낭비** — 광고·타임스탬프·A/B 테스트 때문에 바이트는 매번 다른데 본문은 동일하다.
4. **참고문헌 부패 (reference rot)** — 3주 전 보고서에 인용한 문장이 원문에서 사라져도 알 방법이 없다.

`reference rot`은 두 현상의 합이다 (Klein & Van de Sompel의 정의를 따른다).

- **링크 부패 (link rot)** — 리소스가 사라짐
- **인용 표류 (citation drift)** — 리소스는 살아 있으나 내용이 변함

두 번째가 더 위험하다. 깨지지 않기 때문에 탐지되지 않는다. 학술 문헌 대상 조사에서 참조된 웹 콘텐츠의 약 75%가 3년 내 어느 정도 변경된 것으로 측정됐다.

> **용어 주의**: 소프트웨어 생태계에서 "content drift"는 머신러닝의 개념 표류(concept drift)를 뜻하는 경우가 압도적이다. 본 프로젝트의 문서·검색 키워드·태그는 **reference rot / citation drift**를 주 용어로 사용한다.

### 1.2 목표 (Goals)

- 조건부 요청(ETag / Last-Modified)과 본문 해시로 **불필요한 페치와 재파싱을 제거**한다.
- 인용문 단위에 **안정적 앵커(anchor)** 를 부여하고, 임의 시점에 원문 대비 **재검증**한다.
- 문서의 **버전 이력**을 보존해, 인용 당시의 원문을 항상 되살릴 수 있게 한다.
- 위 기능을 **MCP 도구**로 노출하여 모델·프레임워크에 중립적으로 동작한다.
- **기존 표준과 상호운용**한다. TimeMap으로 내보내고, Robust Links로 인용하고, Web Annotation Selector로 앵커를 표현한다.

### 1.3 비목표 (Non-Goals)

명시적으로 하지 않는 것. 범위 확장 요구가 들어올 때 이 절을 근거로 거절한다.

- 크롤링·검색 엔진 (URL은 호출자가 준다)
- 브라우저 자동화, 로그인, 안티봇 우회
- 임베딩·벡터 검색·RAG 파이프라인
- LLM 호출 (Anchor는 모델을 부르지 않는다)
- **의미 변화 판정** — `ALTERED`가 실질적 의미 변화인지 오탈자 수정인지는 호출자(에이전트)가 판단한다. Anchor는 사실만 보고한다
- 분산 서버, 멀티테넌시, 계정 시스템

> **설계 원칙**: 사용자가 1명이어도 이득이 있어야 한다. 네트워크 효과에 의존하는 기능은 v1.0에 넣지 않는다.

### 1.4 선행기술 및 계승

Anchor는 새로운 계층을 발명하지 않는다. 흩어져 있는 기존 성과를 에이전트가 쓸 수 있는 하나의 로컬 도구로 조립한다.

| 물려받는 것 | 출처 | Anchor에서의 위치 |
|---|---|---|
| 시간 차원의 HTTP 접근 모델 | **Memento — RFC 7089** (Van de Sompel, Nelson, Sanderson) | §2 용어, §7.8 TimeMap |
| 인용문 선택자 데이터 모델 | **W3C Web Annotation Data Model** | §6.1 Anchor |
| 다단계 앵커 재부착 전략 | **Hypothesis** 앵커링 스택 | §6.2 매칭 |
| 근사 문자열 매칭 | Myers 비트벡터 알고리즘, `approx-string-match` 계열 | §6.2 4단계 |
| 인용 표기 규약 | **Robust Links Specification** | §7.9 내보내기 |
| 문제의 실증 | Klein et al. (2014), Jones et al. (2016), PLOS ONE | §1.1 |

Memento를 운영하던 LANL Time Travel 서비스는 2025년 말 종료됐고, mementoweb.org는 자료만 남은 정적 사이트가 됐다. **Anchor의 로컬 우선 설계는 그 실패 모드에 대한 직접적인 답이다.** 중앙 서비스가 없으면 폐쇄될 서비스도 없다.

Anchor가 실제로 새로 만드는 것은 네 가지뿐이다.

1. 위 조각들을 에이전트가 쓸 수 있는 단일 로컬 도구로 조립
2. `raw_hash` / `text_hash` 이중 해시에 의한 자동 노이즈 제거
3. 7가지 검증 상태 코드 — 특히 `ALTERED`와 `MISSING`의 분리
4. MCP 인터페이스

---

## 2. 용어 정의

Memento(RFC 7089) 용어를 병기한다. Anchor의 로컬 개념은 Memento의 개념과 1:1 대응하며, 이는 §7.8 TimeMap 내보내기의 근거가 된다.

| Anchor 용어 | Memento 대응 | 정의 |
|---|---|---|
| **Document** | Original Resource (URI-R) | URL 하나에 대응하는 논리적 대상. 여러 Version을 가진다 |
| **Version** | Memento (URI-M) | 특정 시점의 본문 스냅샷. `text_hash`로 식별한다 |
| **버전 목록** | TimeMap (URI-T) | 한 Document의 모든 Version과 캡처 시각의 열거 |
| `captured_at` | Memento-Datetime | 해당 버전을 **처음** 획득한 시각 |
| **Anchor** | — (W3C Annotation Selector) | 인용문 하나를 원문 안에서 다시 찾기 위한 위치 서술자 |
| **Verification** | — | 특정 Anchor를 특정 Version에 대해 재검증한 결과 레코드 |
| **Normalized text** | — | HTML에서 본문만 추출해 마크다운으로 변환하고 공백·유니코드를 정규화한 문자열. 모든 해시와 앵커의 기준 |

Anchor는 TimeGate를 구현하지 않는다. 로컬 저장소이므로 datetime negotiation 대신 직접 조회(§7.6 `get_version`)를 제공한다. 다만 내보내는 TimeMap은 RFC 7089 직렬화를 따르므로 외부 Memento 클라이언트가 읽을 수 있다.

---

## 3. 아키텍처

```
┌─────────────────────────────────────────────┐
│  MCP Client (Claude / GPT / 오픈모델 / CLI)  │
└───────────────────┬─────────────────────────┘
                    │ MCP 2026-07-28 (stdio | streamable-http)
┌───────────────────▼─────────────────────────┐
│                  Anchor                     │
│                                             │
│  ┌───────────┐  ┌────────────┐  ┌────────┐  │
│  │  Fetcher  │→ │ Normalizer │→ │ Anchor │  │
│  │           │  │            │  │ Engine │  │
│  │ · robots  │  │ · 본문추출  │  │        │  │
│  │ · 조건부   │  │ · md 변환  │  │ · 생성  │  │
│  │ · rate제한 │  │ · 정규화   │  │ · 재검증│  │
│  │ · 아카이브 │  │            │  │ · 예산  │  │
│  │   폴백    │  │            │  │        │  │
│  └─────┬─────┘  └──────┬─────┘  └───┬────┘  │
│        └───────────────┴────────────┘       │
│                       │                     │
│              ┌────────▼────────┐            │
│              │  Store (SQLite) │            │
│              │  + blob (zstd)  │            │
│              └─────────────────┘            │
│                       │                     │
│              ┌────────▼────────┐            │
│              │    Exporters    │            │
│              │ TimeMap│Robust  │            │
│              └─────────────────┘            │
└─────────────────────────────────────────────┘
                    │ HTTPS
        ┌───────────┴───────────┐
   ┌────▼────┐          ┌───────▼────────┐
   │ 인터넷   │          │ Memento 애그리 │
   │         │          │ 게이터 (선택)   │
   └─────────┘          └────────────────┘
```

### 3.1 컴포넌트 책임

| 컴포넌트 | 책임 | 하지 않는 것 |
|---|---|---|
| `Fetcher` | HTTP 획득, robots 준수, 레이트 제한, 조건부 요청, 아카이브 폴백 | 파싱 |
| `Normalizer` | 본문 추출, 마크다운 변환, 정규화, 해시 | 네트워크 |
| `AnchorEngine` | 앵커 생성·매칭·상태 판정·시간 예산 관리 | 저장, 의미 판정 |
| `Store` | 영속화, 버전 관리, 압축 | 비즈니스 로직 |
| `Exporters` | TimeMap, Robust Links, 통합 diff 직렬화 | 데이터 생성 |
| `Server` | MCP 도구 노출, 입력 검증, Task 수명주기 | 위 로직의 재구현 |

---

## 4. 데이터 모델

### 4.1 스키마

```sql
-- 논리적 문서 (Memento: Original Resource / URI-R). URL 정규화 후 유일.
CREATE TABLE documents (
    id              TEXT PRIMARY KEY,          -- uuid7
    url             TEXT NOT NULL UNIQUE,      -- 정규화된 URL
    original_url    TEXT NOT NULL,             -- 리다이렉트 이전 원본
    title           TEXT,
    first_seen_at   TEXT NOT NULL,             -- ISO 8601 UTC
    last_checked_at TEXT NOT NULL,
    status          TEXT NOT NULL,             -- live | gone | forbidden | paywalled
    etag            TEXT,
    last_modified   TEXT,
    robots_allowed  INTEGER NOT NULL DEFAULT 1,
    -- 원문이 **지금 서빙하는** 본문의 버전 (v1.5). captured_at 최대값과
    -- 다를 수 있다: 아카이브 폴백은 과거 시각 스냅샷을 넣고, 본문이 이전
    -- 값으로 되돌아오면 옛 버전 행을 재사용한다. 둘을 같은 것으로 보면
    -- 재검증이 "앵커 생성에 쓴 본문"과 자기 자신을 대조하게 된다.
    current_version TEXT REFERENCES versions(id)
);

-- 리다이렉트 이전 URL → 문서 (v1.5). 문서는 최종 URL로 저장되는데 조회는
-- 사용자가 넘긴 URL로 하므로, 이 표가 없으면 리다이렉트되는 모든 URL에서
-- 캐시가 영구히 빗나간다(조건부 요청도 함께 무력화). §5.1 5단계의
-- "리다이렉트 응답을 우선"이 이 표로 구현된다.
CREATE TABLE document_aliases (
    url         TEXT PRIMARY KEY,   -- 정규화된 입력 URL
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE
);

-- 본문 스냅샷 (Memento: URI-M). text_hash가 같으면 새 버전을 만들지 않는다.
CREATE TABLE versions (
    id               TEXT PRIMARY KEY,
    document_id      TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    text_hash        TEXT NOT NULL,               -- blake3(normalized_text)
    raw_hash         TEXT NOT NULL,               -- blake3(원본 바이트)
    pipeline_version TEXT NOT NULL,               -- 추출기+정규화 규칙 버전 (v1.3, §5.3)
    captured_at      TEXT NOT NULL,               -- Memento-Datetime 대응
    byte_size        INTEGER NOT NULL,
    char_count       INTEGER NOT NULL,
    content_blob     BLOB NOT NULL,               -- zstd(normalized_text)
    http_status      INTEGER NOT NULL,
    source           TEXT NOT NULL DEFAULT 'live',-- live | archive
    source_uri       TEXT,                        -- 아카이브에서 온 경우 URI-M
    -- 이 본문이 원문에서 **마지막으로 관측된** 때와 그 순서 (v1.7).
    -- text_hash로 중복을 제거하면 관측의 시간축이 접힌다 — A→B→A에서 A는
    -- 한 행이므로 "직전에 서빙되던 판본"을 captured_at으로는 알 수 없다.
    -- captured_at은 Memento-Datetime이라 바꿀 수 없으므로 따로 둔다.
    -- 시각은 사람에게 답하는 사실이고 순번은 기계에 답하는 순서다: 시각을
    -- 순서로 쓰면 같은 초 안의 두 관측이 갈리지 않고 시계가 뒤로 밀리면
    -- 순서가 뒤집힌다.
    last_observed_at  TEXT NOT NULL,
    last_observed_seq INTEGER NOT NULL,
    -- 본문이 같아도 출처가 다르면 별개의 memento다 (v1.5). 아카이브에서
    -- 되살린 본문이 기존 live 버전과 같다는 이유로 그 행을 재사용하면
    -- source·source_uri·Memento-Datetime이 통째로 사라진다.
    UNIQUE (document_id, text_hash, source)
);

-- 인용 단위 앵커 (W3C Web Annotation Selector).
CREATE TABLE anchors (
    id                TEXT PRIMARY KEY,
    document_id       TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    created_version   TEXT NOT NULL REFERENCES versions(id),
    exact             TEXT NOT NULL,           -- 인용문 원문
    prefix            TEXT NOT NULL,           -- 앞 컨텍스트 48자
    suffix            TEXT NOT NULL,           -- 뒤 컨텍스트 48자
    position_hint     INTEGER NOT NULL,        -- 생성 시점 문자 오프셋
    exact_hash        TEXT NOT NULL,
    quality           TEXT NOT NULL,           -- ok | short (32자 미만)
    note              TEXT,                    -- 사용자 메모 (선택)
    created_at        TEXT NOT NULL
);

-- 재검증 이력.
CREATE TABLE verifications (
    id                TEXT PRIMARY KEY,
    anchor_id         TEXT NOT NULL REFERENCES anchors(id) ON DELETE CASCADE,
    checked_version   TEXT REFERENCES versions(id),
    checked_at        TEXT NOT NULL,
    state             TEXT NOT NULL,           -- 6.3절 참조 (7종)
    match_score       REAL,                    -- 0.0 ~ 1.0
    edit_distance     INTEGER,                 -- 실제 편집거리
    found_offset      INTEGER,
    found_text        TEXT,                    -- 변형되었을 경우 실제 발견된 문자열
    elapsed_ms        INTEGER NOT NULL
);

-- 네트워크 회계. 절감 효과 측정용.
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

-- robots.txt 캐시 (호스트 origin별 24h). CLI는 매 호출이 새 프로세스이므로
-- 영속화하지 않으면 "두 번째 호출 네트워크 0바이트"를 지킬 수 없다 (v1.3).
CREATE TABLE robots_cache (
    origin       TEXT PRIMARY KEY,   -- 예: https://example.com
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

### 4.2 저장 정책

- 버전 blob은 zstd level 6으로 압축. 일반 기사 기준 원문 대비 25~30%.
- 기본 보존 정책: 문서당 최근 20개 버전 + 앵커가 참조하는 모든 버전. **앵커가 가리키는 버전은 절대 삭제하지 않는다.** 검증 이력(`verifications.checked_version`)이 참조하는 버전도 감사 추적을 위해 보존한다 — 최소 보존 규칙보다 넓게 남기는 것은 안전한 방향이다 (v1.3).
- `anchor gc` 명령으로 고아 버전 정리. **`keep`은 1 이상이어야 한다** — 0이나 음수는 앵커 없는 문서의 최신본까지 지워 빈 껍데기 문서만 남기므로 거부한다 (v1.5).

---

## 5. 페치 파이프라인

### 5.1 URL 정규화

동일 문서의 중복 등록을 막기 위해 페치 전 다음 순서로 정규화한다.

1. 스킴·호스트 소문자화, 기본 포트 제거
2. 프래그먼트(`#...`) 제거
3. 추적 파라미터 제거 — `utm_*`, `fbclid`, `gclid`, `dclid`, `msclkid`, `twclid`, `yclid`, `igshid`, `mc_eid` 등 명백한 것만 (설정으로 확장 가능). **`ref`·`s`는 제거하지 않는다** — 사이트에 따라 실질 경로여서 제거하면 서로 다른 문서가 하나로 합쳐진다 (v1.3)
4. 남은 쿼리 파라미터 키 기준 정렬
5. 경로 말미 슬래시는 **정규화하지 않고 리다이렉트 응답에 맡긴다** (v1.5). `/a`와 `/a/`가 같은 문서인지 다른 문서인지는 서버만 알고, 서버는 리다이렉트로 알려준다. 리다이렉트로 도달한 문서는 입력 URL을 `document_aliases`에 등록해 다음 조회가 캐시를 찾도록 한다 — 이것이 이 단계의 실질 구현이다.

> **리다이렉트와 문서의 정체성 (v1.8)**: 이 도구는 리다이렉트를 가장 자주 만난다. 그 경로에서 "이 본문이 어느 문서의 것인가"를 잘못 정하면 사용자는 **자기가 인용한 적 없는 문서의 본문**을 근거로 받는다 — 판정이 틀리는 것보다 나쁘다. 판정 대상 자체가 바뀐 것이라 어떤 재검증으로도 드러나지 않기 때문이다.
>
> - **영구(301·308)만 정본 URL을 바꾼다.** 302·307은 "지금만 다른 곳을 보라"이고 303은 "다른 리소스를 보라"이므로 `documents.url`을 바꿀 근거가 아니다. 동의 장벽·지역 게이트를 거치는 구성에서 인터스티셜 URL이 Memento의 URI-R로 굳는 것을 막는다.
> - **옛 별칭이 자기 콘텐츠를 서빙하기 시작하면 별칭을 버린다.** 리다이렉트를 멈춘 URL은 더는 같은 리소스가 아니다. 그대로 두면 목적지 문서의 이력에 다른 리소스의 본문이 새 버전으로 꽂힌다.
> - **영구 리다이렉트로 하나가 된 두 문서는 합친다.** 각각 등록돼 있던 A·B 사이에 뒤늦게 A→B 301이 생기면, 합치지 않는 한 A의 앵커가 B의 본문과 대조되면서 검증 기록에는 다른 문서의 버전 id가 남는다. 옛 URL은 별칭으로 남겨 잃어버리지 않는다.
> - **문서·버전 생성은 멱등이다.** 락은 입력 URL로 잡히는데 생성은 최종 URL로 하므로, 서로 다른 두 URL이 같은 목적지로 리다이렉트되면 두 호출이 나란히 "없음"으로 판단한다. 락을 겹쳐 잡는 대신 생성을 멱등으로 두어 어떤 순서에서도 한 행으로 수렴시킨다.

> **IPv6 리터럴** (v1.5): 호스트의 대괄호를 보존한다. 벗기면 문법상 무효한 URL이 되고 재정규화가 실패한다 — 정규화는 멱등이어야 한다.

### 5.2 요청 순서

```
1. 캐시 조회 (순수 로컬 — 네트워크 요청이 없으므로 robots 판정보다 앞선다. v1.3)
   └ max_age 이내의 버전 존재 → cache_hit 반환 (네트워크 0)

2. robots.txt 확인 (호스트 origin별 24h 캐시를 SQLite에 영속화, protego 사용)
   └ 거부 → RobotsDisallowed, 네트워크 요청 없음
   └ **5xx·연결 실패 → 거부** (RFC 9309 §2.3.1.4, v1.5). 규칙을 알 수 없는
     상태에서 무제한 접근으로 전환하지 않는다. 이 판정은 5분만 캐시해
     일시 장애가 하루를 막지 않게 한다. 4xx는 종전대로 "제한 없음".
     단, 이 거부는 **원본 요청에만** 적용된다 — 6단계 참조.
   └ **3xx → 따라간다** (RFC 9309 §2.3.1.2, 최소 5홉, v1.8). 상태가 200이
     아니라는 이유로 본문을 버리면 `Disallow: /`조차 사라진다 — http→https,
     CDN 이관, 캐노니컬 정리는 전부 평범한 구성이고 그때마다 사이트 전체를
     가져가게 된다. 한도를 넘기면 "규칙을 알 수 없음"으로 본다.
   └ **선두 BOM은 무시한다** (RFC 9309 §2.3, v1.8). 남으면 파서가
     `User-agent:` 줄을 지시자로 읽지 못해 **규칙 그룹 전체를 버린다**.
   └ robots.txt도 응답이므로 **크기 상한과 타임아웃이 그대로 적용된다**
     (v1.8). 상한을 넘긴 경우 읽은 데까지로 판정한다 — 통째로 버리면
     소유자의 금지가 사라지므로, 부분 적용이 정직에 가깝다.

3. 레이트 제한 대기 (호스트별 토큰 버킷, 기본 1 req/s, burst 3)

4. 조건부 GET
   If-None-Match: <etag>
   If-Modified-Since: <last_modified>
   User-Agent: <설정값, 5.4절>
   Accept: text/html, application/xhtml+xml, text/plain, application/pdf

5. 응답 분기
   304 → not_modified. last_checked_at만 갱신. 본문 전송 없음.
   200 → 정규화 → text_hash 비교
          동일 → unchanged. 새 버전 생성 안 함. (raw_hash가 달라도 해당 —
                 이중 해시의 노이즈 제거가 작동한 경우)
          상이 ┬ raw_hash 동일 + pipeline_version 상이 → renormalized.
               │  원문은 안 바뀌었고 추출·정규화 규칙이 바뀐 것. 새 버전은
               │  삽입하되 "문서가 변경됨"으로 보고하지 않는다. (v1.3)
               └ 그 외 → changed. 새 버전 삽입.
          (첫 획득은 created)
   3xx → 목적지로 추종 (최대 5홉). **홉마다 2·3단계를 다시 거친다**
          (v1.5). 자동 추종에 맡기면 금지된 경로나 다른 호스트를 robots
          요청조차 없이 가져오게 된다 — 대상 호스트는 차단할 방법조차 없다.
          최종 URL이 입력과 다르면 입력 URL을 `document_aliases`에 등록한다.
   402 → PaymentRequired (Cloudflare Pay Per Use 등) → 6단계
   403/429 → 지수 백오프 재시도 (최대 3회) → 실패 시 6단계
   404/410 → status = gone → 6단계

6. 아카이브 폴백 (설정으로 명시적 활성화 — 기본 off. §9, 외부 서비스 무의존)
   └ Memento 애그리게이터 또는 Wayback CDX에 URI-R 조회
     └ URI-M 발견 → 본문 취득 → source='archive'로 버전 삽입
        (source_uri에 URI-M 기록, live 버전과 명확히 구분)
     └ 없음 → 원 상태(gone/forbidden) 확정
```

**폴백에 이르는 경로** (v1.5 보강): 위 5단계의 HTTP 실패(402/403/404/410/429)뿐 아니라 **원본에 닿지 못한 경우**에도 6단계로 간다. 호스트가 통째로 사라져 연결이 실패하거나 robots.txt를 받지 못해 판정을 보류한 경우가 여기 해당한다 — **호스트 소멸은 링크 부패의 가장 흔한 형태이자 아카이브 구제가 가장 필요한 상황**이므로, 여기서 막히면 이 기능의 존재 이유가 사라진다.

> **애그리게이터가 지목한 URI-M에도 판정을 건다** (v1.8): 우리가 가져오는 것에는 전부 robots 판정이 걸린다. 그러지 않으면 직접 페치가 `explicit`으로 막히는 경로를 애그리게이터 한 겹으로 우회할 수 있고, 아래 원칙이 말뿐이 된다. 아카이브가 자체 정책을 가진 다른 호스트라는 사실은 그 호스트의 robots로 확인하면 되는 것이지, 확인을 건너뛸 근거가 아니다.
>
> **명시적 거부와 판정 불능은 다르다**: robots를 읽었고 그 규칙이 막았다면(`explicit`) 사이트 소유자의 의사이므로 아카이브로도 우회하지 않는다. 규칙을 물어보지 못한 것이라면(`unavailable` — 호스트 소멸·5xx) 의사를 확인할 수 없는 상태일 뿐이고, 아카이브는 **자체 정책을 가진 다른 호스트**이므로 확인을 막을 이유가 없다. 이 구분이 §5.4의 정직한 클라이언트 원칙과 6단계를 양립시킨다.

**6단계의 의의**: 문서가 사라져도 인용이 완전히 무효화되지 않는다. `GONE` 판정을 내리기 전에 공개 아카이브를 한 번 확인한다. 이 경로에서 얻은 버전은 `source='archive'`로 표시되어, 사용자가 "원본이 아니라 아카이브에서 확인됨"을 항상 알 수 있다.

**폴백 대상**: 자체 호스팅 MemGator 인스턴스 또는 Wayback CDX API. 애그리게이터 URL은 설정값이며, 기본값은 없다(사용자가 명시해야 활성화). 외부 서비스에 조용히 의존하지 않는다.

#### MemGator 운영 지침 (필수)

MemGator는 MIT 라이선스이며 TimeMap / TimeGate / Memento 엔드포인트를 이미 제공한다. Anchor는 코드를 포함하지 않고 HTTP로만 호출한다.

| 항목 | 지침 | 사유 |
|---|---|---|
| `--spoof` 옵션 | **절대 사용하지 않는다** | 무작위 user-agent 위장. §5.4 정직한 클라이언트 원칙에 정면으로 위배 |
| `--agent` | Anchor의 User-Agent와 동일하게 설정 | 책임 소재를 흐리지 않는다 |
| `--arcs` (아카이브 목록) | **명시적으로 지정한다** | 기본값이 `git.io` 단축 URL인데 해당 서비스는 2022년 종료됐다 |

문서와 설치 가이드에 이 표를 그대로 싣는다. 우회 기능이 딸린 도구를 통합할 때는, 그 기능을 쓰지 않는다는 사실이 코드가 아니라 문서에 남아야 한다.

### 5.3 정규화 및 해시

```
원본 바이트
  → 인코딩 판별 (charset-normalizer)
  → 본문 추출 (trafilatura; 실패 시, 또는 **블록 구분이 무너졌을 때**
     readability-lxml 폴백)
  → 마크다운 변환
  → 줄 구분자 통일 (CR·U+2028·U+2029·U+0085·U+000B·U+000C → 개행)
  → 유니코드 공백을 ASCII 공백으로, **표시에 관여하지 않는** 폭 없는 문자만 제거
  → **줄·블록 분류** (코드 울타리·들여쓴 코드·구조 줄·산문)
  → 산문 줄만: 조판 줄바꿈 접기 → 서식 기호·서식 전용 태그 제거
  → 유니코드 NFC 정규화 (**문자 제거 뒤에**)
  → 연속 공백 1개로 축약, 줄 끝 공백 제거, 3줄 이상 개행을 2줄로
  → normalized_text

raw_hash  = blake3(원본 바이트)
text_hash = blake3(normalized_text.encode("utf-8"))
```

`raw_hash`는 달라도 `text_hash`가 같으면 **변경 없음**으로 판정한다. 광고 슬롯, 조회수 카운터, CSRF 토큰 같은 노이즈를 걸러내는 핵심 장치다.

**`pipeline_version`** (v1.3, v1.5 보강): `text_hash`에는 숨은 입력이 있다 — 추출기 버전, **인코딩 판별기 버전**, 정규화 규칙 버전이다. **경로마다 실제로 쓰인 도구가 다르므로 문자열도 달라야 한다** — `text/plain` 경로가 실행하지도 않은 trafilatura 버전을 기록하면, Content-Type이 흔들릴 때(헤더 누락·CDN 차이·아카이브 응답) 원문이 그대로인데 `changed`로 오보하고 `renormalized` 안전장치가 바로 그 상황에서 열리지 않는다 (v1.5).

| 경로 | 형식 |
|---|---|
| HTML (주) | `trafilatura/<ver>+charset/<ver>+norm/<n>` |
| HTML (폴백) | `readability-lxml/<ver>+markdownify/<ver>+charset/<ver>+norm/<n>` |
| text/plain·markdown | `plain+charset/<ver>+norm/<n>` |
| PDF | `pypdf/<ver>+norm/<n>` |
 `raw_hash` 동일 + `text_hash` 상이 + `pipeline_version` 상이는 원문 변경이 아니라 파이프라인 변경이며, outcome을 `changed`가 아니라 `renormalized`로 구분한다. 원문이 안 바뀌었는데 바뀌었다고 보고하는 것을 막는 장치다. 정규화 규칙을 고치면 반드시 규칙 버전을 올린다.

> 이 이중 해시는 앵커 안정성에도 직결된다. 뉴스 사이트는 페이지 로드마다 다른 광고 텍스트를 삽입하므로, **문서 내용이 바뀌지 않아도 문자 오프셋이 달라진다.** 정규화된 본문을 기준으로 삼지 않으면 위치 기반 앵커가 매번 깨진다.

PDF는 `pypdf` 텍스트 추출 후 줄 끝 분철을 복원하고 동일 경로를 탄다. 스캔 PDF는 범위 밖이며 `UnsupportedContent`를 반환한다.

**콘텐츠 타입은 접두가 아니라 정확히 비교한다** (v1.5). 접두 일치는 `text/plaintext`를 plain 경로로, `application/pdfx`를 PDF 경로로 잘못 보낸다.

### 5.4 네트워크 예절 (compliance)

Anchor는 **정직한 클라이언트**로 동작한다. 이것은 기능이 아니라 전제다.

- **User-Agent**: 기본값 `Anchor/<릴리스 버전> (+https://github.com/julgi80ai-stack/anchor-mcp)`. 위장·스푸핑 옵션은 제공하지 않으며, **빈 값은 설정 단계에서 거부한다** (v1.8) — 규칙을 지키겠다면서 누구인지 말하지 않을 수는 없고, 빈 UA는 robots 매칭도 빈 토큰으로 하게 만든다. 검증은 `Config`를 **만드는 순간** 이뤄진다: 라이브러리를 직접 쓰는 경로(§8)도 같은 보장을 받아야 한다.
- **robots.txt**: 기본 준수. `respect_robots = false` 설정은 존재하나, 활성화 시 서버 시작 로그에 경고를 출력한다.
- **레이트 제한**: 호스트별 토큰 버킷. 동시 호출에서도 설정값을 지킨다 — 락 없이 두면 대기하던 스레드가 한꺼번에 깨어나 설정의 몇 배로 두드린다 (v1.5).
- **`Retry-After` 존중** (v1.5 명확화): 숫자와 HTTP-date를 모두 해석한다. 서버가 상한(기본 60초)보다 긴 대기를 지정하면 **절삭해서 일찍 두드리지 않고 재시도를 멈춘다** — 확인 불가로 보고하는 편이 정직하다. 해석 불가·비정상 값(`nan` 등)은 자체 지수 백오프로 물러선다.
- **크기 상한**: 성공 응답뿐 아니라 **모든 응답**에 적용한다 (v1.5). 거대한 오류 페이지나 차단 인터스티셜을 통째로 버퍼링하지 않는다. **robots.txt도 응답이다** (v1.8) — 여기만 비켜가면 20MB robots.txt가 통째로 캐시 DB에 들어앉는다.
- **조건부 요청**: 항상 사용. 이것이 곧 서버 부하 절감이다. 검증자는 **우리가 그것을 받은 리소스에만** 싣는다 (v1.8) — 문서의 정본 URL에 해당하는 홉에서만이다. 모든 홉에 실으면 목적지가 자기 검증자와 비교해 정직하게 준 304를 "변한 것 없음"으로 읽어 옛 본문을 계속 반환하고(이사한 사실이 영구히 감지되지 않는다), 타 호스트에 ETag가 샌다. 반대로 첫 홉에만 실으면 리다이렉트되는 별칭으로 재확인할 때 조건부 요청이 무력해진다.
- **`https → http` 강등 거부** (v1.8): 무결성 보장이 없는 채널에서 받은 본문을 인용 근거로 삼지 않는다. 정본 URL이 평문으로 기록되는 것도 막는다.
- **안티봇 우회 없음**: 프록시 로테이션, 브라우저 핑거프린트 위장, CAPTCHA 해결을 구현하지 않는다. 403은 403으로 보고한다.

> 2026년 9월 15일부터 Cloudflare는 광고 게재 페이지에서 search/agent/training 혼용 크롤러를 기본 차단한다. Anchor는 사용자 요청에 의해 동작하는 agent 계열 페처이며, 차단 시 우회하지 않고 `Forbidden` 상태로 기록한 뒤 아카이브 폴백만 시도한다. 서명 기반 봇 인증(Web Bot Auth) 지원은 v1.3 후보다.

---

## 6. 앵커 엔진

### 6.1 앵커 생성

인용문 `quote`를 받아 현재 버전의 `normalized_text`에서 위치를 찾고 다음을 저장한다.

```python
@dataclass(frozen=True)
class Anchor:
    """인용문 하나를 원문에서 다시 찾기 위한 위치 서술자.

    W3C Web Annotation Data Model의 TextQuoteSelector +
    TextPositionSelector 조합을 따른다. 오프셋만으로는 문서가
    조금만 바뀌어도 깨지므로, 앞뒤 문맥을 함께 보관해
    위치가 이동해도 재발견할 수 있게 한다.

    position_hint는 탐색 시작점 힌트일 뿐이며, 판정의
    근거가 아니다. 광고 삽입만으로도 오프셋은 변한다.
    """
    exact: str          # 인용문 그 자체
    prefix: str         # 직전 48자
    suffix: str         # 직후 48자
    position_hint: int  # 생성 시점 오프셋 (탐색 시작점으로만 사용)
    quality: Quality    # OK | SHORT
```

`quote`가 원문에 없으면 앵커를 만들지 않고 `QuoteNotFound`를 발생시킨다. **존재하지 않는 인용을 기록하지 않는 것**이 이 도구의 기본 계약이다.

#### 블록 구분자에 유연한 검색 (v1.6)

사람이 화면에서 끌어 복사한 인용문은 저장 본문과 **블록 구분자가 다르다**. 브라우저는 문단 사이를 줄바꿈 하나나 공백으로 주고 목록 표지(`- `)는 아예 주지 않는데, 저장 본문에는 빈 줄과 표지가 있다. 그래서 두 문단·두 목록 항목을 한 번에 인용하는 흔한 행동이 전부 실패했다.

검색은 **공백·줄바꿈의 연속을 하나로 보고, 줄머리의 목록 표지를 건너뛴 형태**로 한다. 다만 앵커의 `exact`는 사용자가 준 형태가 아니라 **저장 본문에 실재하는 문자열**로 잡는다 — §6.2의 1·2단계가 완전 일치 검색이므로 그 전제를 깨면 안 된다.

이 유연함은 **날조를 허용하는 쪽으로 새지 않는다.** 공백 배치만 다를 뿐, 본문에 없는 문장은 여전히 `QuoteNotFound`다. 그리고 그 오류 메시지는 "저장하지 않은 영역일 수 있다"를 함께 알린다 — 화면에 분명히 있는 문장(요약 박스·그림 설명·참고문헌)이 본문으로 추출되지 않았을 때, 사용자가 받는 말이 "원문에 없다"뿐이면 그것은 거짓 진술이다.

#### 짧은 인용문 경고 (v1.1 신규)

`exact`가 **32자 미만**이면 `quality = SHORT`로 표시하고 응답에 경고를 포함한다.

짧고 일반적인 문자열은 퍼지 매칭에서 병리적 케이스를 만든다. 문서 전체에 유사 후보가 다수 존재해 탐색 비용이 폭증하고, 오탐 확률도 높다. Hypothesis는 실제로 이 조합(긴 문서 + 짧은 일반 인용문) 때문에 클라이언트가 10초 이상 정지하는 문제를 겪었다.

- 32자 미만: 경고와 함께 생성 허용. 재검증 시 시간 예산을 절반으로 적용
- 12자 미만: 생성 거부 (`QuoteTooShort`)
- 권장: 완결된 문장 하나

#### 문자 체계 인식 임계 (v1.5)

위 12자·32자는 **라틴 문자를 전제로 잡힌 값**이다. 일본어·중국어는 같은 내용을 훨씬 적은 글자로 적으므로 그대로 적용하면 완결된 문장이 거부되거나(중국어 `气候变化是真实的。`는 9자) 전부 `SHORT`로 분류된다. 한자·가나 비율이 절반을 넘으면 임계를 정보량 비(약 2.5배)로 환산한다. **한국어는 어절을 공백으로 띄우고 글자당 정보량이 중간이라 라틴 기준을 그대로 쓴다.**

#### 중복 출현 경고 (v1.5)

인용문이 문서에 여러 번 나오면 앵커는 **첫 출현**에 묶인다(§6.2 1·2단계가 단순 완전 일치이므로). 사용자가 인용한 인스턴스가 지워져도 다른 인스턴스 때문에 `INTACT`가 될 수 있으므로, 생성 시 출현 횟수를 세어 경고에 담는다. Anchor는 어느 인스턴스가 "그" 인용인지 판정하지 않고 사실만 알린다.

### 6.2 매칭 알고리즘 (v1.1 개정)

새 버전에 대해 다음 순서로 시도한다. 앞 단계가 성공하면 즉시 종료한다.

| 단계 | 방법 | 판정 |
|---|---|---|
| 1 | `position_hint ± 500자` 범위에서 `exact` 완전 일치 | `INTACT` (score 1.0) |
| 2 | 문서 전체에서 `exact` 완전 일치 (표준 문자열 검색) | `MOVED` (score 1.0) |
| 3 | `prefix + suffix` 문맥으로 후보 구간을 **전부** 찾아 가장 가까운 것 선택 | `ALTERED` (score = 유사도) |
| 4 | **편집거리 상한을 둔 근사 문자열 검색** + 문맥 뒷받침 확인 | 뒷받침되는 발견 → `ALTERED`, 아니면 → `MISSING` |

#### 4단계 상세 — v1.0에서 변경된 핵심

**v1.0 사양의 문제**: `rapidfuzz.partial_ratio` 전체 문서 슬라이딩 스캔은 O(n·m)이며, **찾지 못할 때 가장 느리다.** 검증 대상 대부분이 `INTACT`인 정상 상황에서는 드러나지 않다가, 문서가 대폭 개편된 최악의 상황에서 성능이 무너진다. 이는 Hypothesis가 `diff-match-patch`로 겪은 것과 동일한 실패 모드다. 대체 구현 벤치마크에서 10만 자 문서 / 453개 인용 기준 13,342ms 대 936ms로 14배 차이가 보고됐고, 앵커링 성공률도 근사 매칭 쪽이 높았다.

**v1.1 방식**: 편집거리 상한 `k`를 먼저 정하고, 그 상한을 넘으면 즉시 포기하는 비트병렬 근사 검색을 쓴다.

```python
# k = 허용 편집거리. 인용문 길이에 비례하되 상한을 둔다.
# 문자 체계를 반영한다 (v1.5): 같은 성격의 개정(단어 하나 교체)이
# 일본어·중국어에서는 훨씬 적은 글자로 표현되므로, 고정 비율을 그대로
# 곱하면 ALTERED가 MISSING으로 떨어진다.
#
# 반영은 **연속**이어야 한다 (v1.7). "과반이면 2.5배"라는 계단을 두면
# 계단 바로 아래가 항상 틀린다 — 라틴 약어·연도·백분율이 섞인 일본어·
# 중국어 문장은 흔히 밀도 0.5 밑으로 내려가고(`GDPは3.2%増加した。`는
# 0.462), 그 순간 k가 2.5배 작아져 두 글자 교체가 MISSING이 된다.
밀도 = 한자·가나 비율            # 0.0 ~ 1.0 (공백 제외, 한글 제외)
ratio = 0.15 * (1.0 + 1.5 * 밀도)   # 밀도 0 → ×1.0, 밀도 1 → ×2.5
k = max(1, min(int(len(exact) * ratio), 64))
```

구현 우선순위:

1. **주 구현** — `regex` 모듈의 퍼지 매칭. C 구현이며 오류 상한을 네이티브로 지원한다.
   ```python
   pattern = regex.compile(f"({regex.escape(exact)}){{e<={k}}}", regex.BESTMATCH)
   ```
2. **폴백/최적화** — Myers 비트벡터 근사 문자열 검색 직접 구현. `approx-string-match` 계열이 쓰는 알고리즘으로, 패턴 길이 ≤ 64일 때 워드 단위 병렬 처리로 O(n)에 가깝다.

> **경로 선택 (v1.5 개정, v1.7 정정)**: regex 퍼지는 **k가 크고 결과가 없을 때** 지수적으로 느려진다. 실측상 k=4부터 이미 Myers보다 느리고, k=6·7KB 한국어 기사에서 200ms 예산을 소진해 `UNRESOLVED`가 났다(같은 입력을 Myers로 돌리면 56~67배 빠르다). 짧은 인용문(=CJK 완결 문장)이 느린 경로에 고정되던 문제이므로 **k ≤ 3이면 regex, k > 3이면 Myers 경로**를 쓴다.
>
> v1.5는 여기에 "두 경로의 판정이 일치한다"고 적었으나 **그것은 사실이 아니었다**(v1.7 정정). 200회 차분 비교는 디코이가 없는 입력만 밟았다. 아래 창 후보 규칙을 갖춘 뒤에야 두 경로가 같은 답을 낸다.
>
> **창 후보 (v1.5, v1.7 개정)**: 64자 초과 인용문은 앞·뒤 64자 코어로 위치를 좁힌 뒤 전체 인용문을 창 안에서 준전역 DP로 검증한다. 코어 스캔은 **거리 k 이하인 모든 자리**를 후보로 돌려준다 — 코어마다 전역 최소 하나만 남기면, 요약절이 인용문의 앞머리를 옮기고 풀인용이 꼬리를 옮긴 흔한 편집에서 앞·뒤 코어의 최적이 둘 다 그 디코이에 떨어져 진짜 위치의 창이 검사조차 되지 않는다(거짓 `MISSING`). 코어는 인용문의 부분 문자열이므로 그 창에서 전체 인용문의 거리는 **코어 거리 이상**이다 — 이 하한으로 남은 후보를 잘라 전부 훑되 헛일은 하지 않는다. 창의 오른쪽 여유는 `m + 2k`가 필요하다(참 매치의 시작이 ±k, 길이가 m±k까지 벌어지므로).
>
> **문맥 뒷받침 (v1.7 신규)**: 4단계의 발견은 **인용문이 있던 자리**임이 뒷받침될 때만 `ALTERED`로 제시한다. 닮은 정도만으로는 갈리지 않는다 — 약관·릴리스노트·FAQ의 문맥은 템플릿이라 다른 절의 형제 문단도 문맥이 두어 글자밖에 다르지 않다. 갈라 주는 신호는 **문맥이 문서에 그대로 살아 있는데 인용문이 그 옆에 없다**는 것이고, 그때 다른 곳의 근사 일치는 현재 모습이 아니라 닮은 남이므로 `MISSING`이다. 문맥까지 함께 개정돼 표지가 남지 않은 경우에만 문맥의 안쪽 끝으로 근사 비교한다. 후보 선택에서도 **뒷받침되는 쪽이 거리가 조금 더 크더라도 앞선다** — 부록의 표준 문안이 개정된 본문보다 원문에 가까운 것은 약관에서 흔하다.

`score`는 `1 - (edit_distance / len(exact))`로 계산하며, `edit_distance`도 함께 저장한다. 비율만으로는 짧은 인용문에서 오해를 낳기 때문이다.

> **후보 선택 (v1.5, v1.7 개정)**: 3단계는 prefix의 **첫 출현에서 멈추지 않는다**. 템플릿이 반복되는 문서(약관 조항·변경이력·FAQ·표)에서 첫 후보를 확정하면, 인용문과 무관한 형제 문단이 "당신 인용문의 현재 모습"으로 보고된다 — 판정이 아니라 **판정의 근거로 제시되는 텍스트**가 틀리는 것이라 §6.3의 "변경 전후 텍스트를 함께 제시"가 거짓 대조표가 된다.
>
> 후보 수에 상한을 두는 것은 구현의 자유지만, **상한에 걸린 채 그때까지의 최선을 확정해서는 안 된다**(v1.7). 진짜 문단은 아직 보지 않은 뒤쪽에 있을 수 있고, 그러면 실패 모드가 사라지는 것이 아니라 경계만 옮겨간다. 상한에 걸리면 3단계의 답을 버리고 4단계로 넘긴다. 예산이 후보 도중에 소진된 경우도 같다 — 확정하지 않고 `UNRESOLVED`다.

#### 시간 예산 (v1.1 신규, v1.5 강제 범위 확대)

앵커 하나당 매칭 시간 상한을 둔다. 초과 시 판정을 강제하지 않고 `UNRESOLVED`를 반환한다.

**예산은 단계 사이가 아니라 단계 내부에서도 확인한다** (v1.5). 3·4단계의 편집거리 DP는 O(인용문 길이 × 후보 길이)라, 후보 사이에서만 확인하면 단일 호출이 예산을 통째로 넘긴다 — 실측상 4000자 인용문에서 200ms 예산에 7.7초(38배)를 썼다.

확인 주기는 **DP 칸 수로 환산한다** (v1.7). 주기를 행 수로 두면 한 행의 비용이 후보 길이에 비례하므로 초과량이 인용문 길이를 따라 커진다 — 예산 200ms에 8,545자 인용문이 267ms, 68,902자가 1,133ms를 썼다(§10의 앵커당 p99 250ms 위반). 확인 사이의 일을 칸 수로 묶으면 초과량이 길이와 무관한 상수가 된다.

| 조건 | 기본 예산 |
|---|---|
| `quality = OK` | 200 ms |
| `quality = SHORT` | 100 ms |
| 문서 길이 상한 | 2 MB (초과 시 앞 2MB만 탐색, `TRUNCATED` 플래그) |

> **실효 한계 (v1.7 명시)**: 위 2MB는 **저장·탐색 범위의 상한**이지 4단계가 예산 안에 훑을 수 있는 크기가 아니다. Myers 코어 스캔은 순수 파이썬 O(n)이고 실측 처리량이 약 2.0M자/초라, 64자 초과 인용문(코어 2개 × 전문 스캔)은 200ms 예산에서 **약 19만 자**가 실효 한계다. 그보다 큰 문서에서 3단계가 실패하면 4단계는 판정을 내지 못하고 `UNRESOLVED`가 된다. 이것은 결함이 아니라 계약의 귀결이다 — 모르는 것을 모른다고 말하는 쪽을 택한 결과이며, 예산을 늘리면 그만큼 넓어진다. 큰 문서에서 UNRESOLVED가 잦다면 `time_budget_ms`를 올리는 것이 정해진 답이다.

**모르는 것을 모른다고 말하는 것이 틀린 답을 빠르게 주는 것보다 낫다.** 배치 검증에서 앵커 하나가 전체를 멈추게 해서는 안 된다.

### 6.3 검증 상태 코드 (7종)

| 상태 | 의미 | 사용자에게 필요한 조치 |
|---|---|---|
| `INTACT` | 같은 위치에 그대로 있음 | 없음 |
| `MOVED` | 문서 내 다른 위치에 그대로 있음 | 없음 (문단 순서 변경 등) |
| `ALTERED` | 문장이 수정됨 | **확인 필요.** 변경 전후 텍스트를 함께 제시 |
| `MISSING` | 문서는 살아 있으나 인용문이 사라짐 | **인용 철회 또는 대체 검토** |
| `GONE` | 문서 자체가 404/410이며 아카이브에도 없음 | **인용 철회 또는 보존 버전으로 대체** |
| `UNREACHABLE` | 403/402/타임아웃 — 확인 불가 | 재시도 예약 |
| `UNRESOLVED` | 판정 보류 — 시간 예산 초과, 또는 **증거가 갈림** *(v1.1 신규, v1.7 확장)* | 예산을 늘려 재검증하거나, `found_offset`을 보고 사람이 판단 |

`ALTERED`와 `MISSING`은 서로 다른 사건이다. 전자는 원문이 바뀐 것이고, 후자는 인용이 무효가 된 것이다. 이 구분이 보고서 검수에서 가장 실질적인 가치를 낸다.

> **증거가 갈릴 때 (v1.7 신규)**: 4단계가 편집거리 안의 후보를 찾았는데 그것이 **이 인용문인지** 확인할 근거가 없으면 판정을 보류한다. 옛 자리에 다른 것이 들어앉은 채 닮은 문장이 다른 절에 있을 때, "옮겨지며 개정됐다"와 "삭제됐고 닮은 남이 있다"는 이 증거로 갈리지 않는다. `ALTERED`로 내밀면 뜻이 정반대인 문장을 개정문으로 읽게 되고, `MISSING`으로 내밀면 살아 있는 인용을 죽었다고 한다 — **둘 다 단정이다.** 이 경우 `after`를 채우지 않는다: 거짓 대조표를 만들지 않는 것이 이 상태의 목적이다.
>
> 편집거리 안에 **아무것도** 없으면 그때는 `MISSING`이다. 그것은 증거가 갈리는 것이 아니라 증거가 없는 것이고, 없다는 것은 말할 수 있다.
>
> **절단과 판정 (v1.5, v1.7 확장)**: 문서가 상한에서 잘렸다면 "인용문이 사라졌다"고 단정할 수 없다 — `MISSING`은 `UNRESOLVED`로 낮춘다. **`ALTERED`에도 같은 원칙이 적용된다**(v1.7). 인용문이 절단 경계에 걸치면 잘려나간 꼬리가 편집거리로 계산돼, 원문이 무손상인데 잘린 조각이 "현재 모습"으로 제시된다. 발견 구간이 절단 경계에 닿으면 판정을 보류한다. 다 보지 못했으면 단정하지 않는다 — 이것은 `MISSING`만의 규칙이 아니다.

`UNRESOLVED`는 실패가 아니라 **정직한 무응답**이다. 이 상태를 만들지 않으려고 임의 판정을 내리면 도구 전체의 신뢰가 무너진다.

**문서를 다 보지 못했으면 `MISSING`이라고 말하지 않는다** (v1.5). 2MB 상한에서 잘린 문서에서 인용문을 못 찾은 것은 "사라졌다"가 아니라 "확인하지 못했다"이므로 `UNRESOLVED`로 낮추고, 절단 사실(`truncated`)을 결과에 실어 보낸다 — 사용자가 원인을 알 수 없으면 조치할 수도 없다.

**Anchor는 `ALTERED`의 의미를 판정하지 않는다.** 오탈자 수정인지 입장 번복인지는 변경 전후 텍스트를 보고 호출자가 결정한다. 이 경계를 지키는 것이 LLM 비의존 원칙의 실체다.

---

## 7. MCP 도구 인터페이스

### 7.0 프로토콜 준수 (v1.1 신규)

**MCP 2026-07-28 스펙**을 따른다. 실무상 영향은 셋이다.

- **Stateless core** — 서버는 sticky session을 요구하지 않는다. 로컬 SQLite가 유일한 상태이므로 자연히 충족된다.
- **Streamable HTTP** — `Mcp-Method`, `Mcp-Name` 헤더를 붙인다.
- **Tasks 확장** — `verify_citations`는 수십~수백 건의 네트워크 요청을 동반하므로 **Task로 실행할 수 있다.** task 메타데이터를 붙여 호출하면 백그라운드로 실행되고, 클라이언트가 `tasks/get`으로 진행 상태를 조회하고 `tasks/result`로 결과를 회수하고 `tasks/cancel`로 중단할 수 있다. task 메타데이터 없이 부르면 일반 동기 호출이다 (Tasks를 모르는 클라이언트와의 호환 경로). 즉시 응답형 도구(`fetch_document`, `cite`, `get_version` 등)는 일반 tool call을 유지한다.

> **취소와 종료 (v1.5)**: 배치는 별도 스레드에서 돌고 스레드는 강제 취소할 수 없다. 따라서 `tasks/cancel`은 **협조적 중단 신호**로 구현한다 — 워커가 문서 사이마다 확인하고 남은 문서를 건드리지 않는다. 상태만 바꾸고 작업은 계속 도는 것은 "중단할 수 있다"는 계약의 위반이다. 취소된 task도 부분 결과를 남겨 `tasks/result`로 회수할 수 있어야 하며(그러지 않으면 클라이언트가 무한 폴링에 빠진다), 서버 종료 시에는 **워커를 정리한 뒤** 저장소를 닫는다(순서를 어기면 사용 중인 커넥션 해제로 프로세스가 죽는다). 종결된 task는 `ttl` 경과 후 정리한다. task로 실행할 수 없는 도구에 task 메타데이터가 붙으면 조용히 무시하지 않고 거부한다.

> **와이어 형식 주의 (v1.3)**: MCP tasks는 2025-11-25 실험 리비전 전용이라, 현행 프로토콜(2026-07-28)의 와이어 게이트는 `tools/call` 응답으로 `CreateTaskResult`를 허용하지 않는다. 따라서 task 기술자는 `CallToolResult`의 structuredContent에 **인밴드**로 담아 반환하며(`{"task": {taskId, status, …}}`), `tasks/*` 메서드는 확장(SEP-2133) `dev.julgi.anchor/tasks`로 서빙한다. tasks가 코어 스펙에 복귀하면 표준 형식으로 교체한다.

모든 시각은 ISO 8601 UTC 문자열이다.

### 7.1 `fetch_document`

문서를 가져오거나 캐시에서 반환한다. **`mcp-server-fetch`의 드롭인 대체를 목표로 한다** — 같은 일을 하되 캐시와 출처가 붙는다.

```jsonc
// 입력
{
  "url": "https://example.com/report",     // 필수
  "max_age": 3600,                          // 초. 기본 86400. 0이면 항상 확인
  "force_refresh": false,
  "include_content": true,                  // false면 메타데이터만 (토큰 절약)
  "start_index": 0,                         // mcp-server-fetch 호환: 청크 읽기
  "max_length": 5000                        // 청크 길이. 0이면 무제한 (v1.3)
                                            // start_index·max_length는 음수 불가 (v1.5)
}

// 출력
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
  "content": "# 2026 Report\n\n...",         // include_content=true일 때만
  "content_truncated": true,                // 청크가 잘렸으면 true (v1.3)
  "next_start_index": 5000,                 // 이어 읽을 시작점 (잘렸을 때만, v1.3)
  "network": { "bytes_down": 0, "elapsed_ms": 142 }
}
```

### 7.2 `cite`

인용문에 앵커를 부여한다.

```jsonc
// 입력
{ "document_id": "018f...", "quote": "AI 크롤러 트래픽의 절반 이상이 변하지 않은 페이지를 다시 가져오는 데 쓰인다", "note": "3장 근거" }

// 출력
{
  "anchor_id": "018f...",
  "version_id": "018f...",
  "offset": 8214,
  "quality": "ok",
  "warnings": [],                           // quality=short일 때 경고 문자열
  "created_at": "..."
}
```

### 7.3 `verify_citations` *(Task)*

앵커들을 현재 원문 대비 재검증한다. 배치 처리하며 호스트별 레이트 제한을 준수한다. **Task로 반환한다.**

```jsonc
// 입력 — 셋 중 하나 이상
{
  "anchor_ids": ["..."],
  "document_ids": ["..."],
  "older_than": "P7D",
  "time_budget_ms": 200                     // 앵커당 매칭 예산 재정의 (선택)
}

// 최종 결과
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
      "before": "AI 크롤러 트래픽의 절반 이상이 변하지 않은 페이지를 다시 가져오는 데 쓰인다",
      "after":  "AI 크롤러 트래픽의 약 60%가 변하지 않은 페이지를 다시 가져오는 데 쓰인다",
      "match_score": 0.86,
      "edit_distance": 6,
      "position_hint": 4210,                // 앵커를 만든 자리 (v1.7)
      "found_offset": 4198                  // 지금 찾은 자리 (v1.7)
    }
  ],
  "network": { "requests": 12, "not_modified": 9, "bytes_down": 48210 },
  "stopped_early": false                    // 취소·종료로 중도 종료됐는가 (v1.5)
}
```

`stopped_early`가 참이면 `checked`와 `summary`는 **부분 결과**다. 남은 앵커는 검증되지 않았으므로 "이상 없음"으로 읽어서는 안 된다.

`attention` 배열에는 조치가 필요한 항목(`ALTERED`/`MISSING`/`GONE`/`UNRESOLVED`)만 담는다. `INTACT` 36건을 전부 나열해 컨텍스트를 채우지 않는다.

`position_hint`와 `found_offset`을 함께 준다 (v1.7). 편집거리만으로는 그것이 **같은 자리의 개정**인지 **문서의 다른 절에서 온 닮은 문단**인지 호출자가 알 수 없다. 둘이 크게 벌어졌다면 §6.2의 문맥 뒷받침을 통과했더라도 사람이 한 번 볼 값어치가 있다.

### 7.4 `diff_versions`

두 버전의 본문 차이를 통합 diff로 반환한다.

```jsonc
{ "document_id": "018f...", "from_version": "latest~1", "to_version": "latest", "context_lines": 2 }
```

파라미터명이 `from`/`to`가 아닌 이유: Python 예약어라 참조 구현의 도구 시그니처로 쓸 수 없다 (v1.3). 버전 참조는 `latest`, `latest~N`, 또는 버전 id.

> **`latest~N`의 좌표계 (v1.7)**: `latest`가 포인터를 따르므로 `latest~N`도 **관측 순서**를 따른다. 캡처 시각으로 물러나면 두 좌표계가 섞여, 되돌림에서 `latest~1`이 `latest`와 같은 행을 가리켜 기본 diff가 비고(직전 fetch가 `changed`를 보고한 직후에), A→B→A→C→A에서는 **일어난 적 없는 전이(B→A)** 를 근거로 제시하며, 중간 판본 B는 어떤 `latest~N`으로도 도달할 수 없다. TimeMap(§7.8)은 이와 달리 **캡처 시각 순**이다 — RFC 7089의 시간축은 Memento-Datetime이기 때문이다. 둘은 서로 다른 질문에 답한다.

### 7.5 `get_version`

과거 버전의 본문을 그대로 꺼낸다. 원문이 사라진 뒤에도 인용 당시의 텍스트를 확인할 수 있다.

### 7.6 `list_documents`

캐시된 문서 목록을 상태·최종 확인 시각과 함께 반환한다. 필터: `status`, `host`, `has_pending_verification`.

`has_pending_verification`의 기준은 **어느 버전을 검증했는가**이지 시각이 아니다 (v1.7). 되돌림은 옛 행을 재사용하고 아카이브는 과거 Memento 시각을 쓰므로, 시각으로 재면 현재 본문이 방금 바뀌었는데도 "검증할 것 없음"이 나온다 — 이 필터로 대상을 좁히는 워크플로는 판정이 뒤집힌 문서를 영영 다시 보지 않는다.

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

`bytes_saved_estimate`는 절감 효과를 사용자가 직접 확인하는 지표다. 이 숫자가 도구의 존재 이유를 스스로 증명해야 한다.

### 7.8 `get_timemap` *(v1.1 신규)*

한 문서의 버전 목록을 **RFC 7089 TimeMap**으로 내보낸다. 외부 Memento 클라이언트가 읽을 수 있다.

```jsonc
// 입력
{ "document_id": "018f...", "format": "link" }   // link | json

// 출력 (format=link, application/link-format)
{
  "content_type": "application/link-format",
  "body": "<https://example.com/report>; rel=\"original\",\n<anchor:///018f.../timemap>; rel=\"self\"; type=\"application/link-format\",\n<anchor:///018f.../v/018e...>; rel=\"memento\"; datetime=\"Tue, 15 Jul 2026 09:11:00 GMT\",\n<anchor:///018f.../v/018f...>; rel=\"memento\"; datetime=\"Sat, 16 Aug 2026 04:12:00 GMT\""
}
```

로컬 버전에는 `anchor://` URI 스킴을 부여한다. 아카이브에서 온 버전은 실제 URI-M을 그대로 노출한다.

**구현 비용은 직렬화 함수 하나다.** 얻는 것은 20년 된 RFC와의 상호운용성, 그리고 이 문제를 오래 붙들어온 커뮤니티와의 접점이다.

### 7.9 `export_robust_links` *(v1.1 신규)*

앵커들을 **Robust Links** 표기로 내보낸다. Anchor를 쓰지 않는 독자도 인용 시점을 알 수 있게 하는 상호운용 출력이다.

```jsonc
// 입력
{ "anchor_ids": ["..."], "format": "html" }      // html | markdown | bibtex_note

// 출력 (html)
{
  "items": [
    {
      "anchor_id": "018f...",
      "html": "<a href=\"https://example.com/report\" data-originalurl=\"https://example.com/report\" data-versiondate=\"2026-08-16\" data-versionurl=\"https://web.archive.org/web/20260816041200/https://example.com/report\">2026 Report</a>"
    }
  ]
}
```

`data-versionurl`은 아카이브 URI-M이 알려진 경우에만 채운다. 없으면 `data-originalurl`과 `data-versiondate`만 출력한다 — Robust Links 스펙이 허용하는 형태다.

---

## 8. Python API

MCP 없이 직접 쓸 수 있어야 한다.

```python
from anchor import Anchor

# 컨텍스트 매니저가 SQLite 연결과 HTTP 세션을 함께 관리한다.
with Anchor(db_path="~/.anchor/store.db") as ax:
    doc = ax.fetch("https://example.com/report", max_age=3600)
    print(doc.outcome, doc.char_count)

    cit = ax.cite(doc.id, "AI 크롤러 트래픽의 절반 이상이 변하지 않은 페이지를 다시 가져오는 데 쓰인다")
    if cit.quality is Quality.SHORT:
        print("경고: 인용문이 짧아 재검증 정확도가 낮을 수 있습니다")

    # 7일 이상 검증하지 않은 앵커 전체 재검증
    report = ax.verify(older_than="P7D")
    for item in report.attention:
        print(f"[{item.state}] {item.url}\n  이전: {item.before}\n  현재: {item.after}")

    # 보고서 각주용 Robust Links 내보내기
    for link in ax.export_robust_links(report.anchor_ids, fmt="markdown"):
        print(link)
```

CLI도 동일 기능을 제공한다.

```bash
anchor fetch https://example.com/report
anchor cite <doc-id|url> "인용문"
anchor verify --older-than 7d
anchor list                                  # 캐시된 문서 목록 (v1.3)
anchor timemap <doc-id|url> --format link
anchor export --robust-links --format markdown
anchor stats
anchor gc --keep 20
anchor serve --transport stdio               # MCP 서버 (v1.3; anchor-mcp와 동일)
```

MCP 클라이언트 등록용 진입점은 콘솔 스크립트 `anchor-mcp`다 (v1.3).

---

## 9. 설정

`~/.anchor/config.toml`. 환경변수 `ANCHOR_*`가 항상 우선한다. **비밀값은 파일에 두지 않는다.**

**로드 시점에 검증한다** (v1.5). 문법 오류·타입 오류·범위 이탈은 첫 페치 도중이 아니라 설정을 읽는 순간 어느 키가 문제인지 밝히며 실패해야 한다. 특히 세 가지를 주의한다.

| 함정 | 규칙 |
|---|---|
| `enabled = "no"` | 참/거짓 자리의 문자열은 거부한다. `bool("no")`는 참이라, 조용히 통과시키면 §9의 "명시적 활성화 필요"가 무력화된다 |
| `max_content_mb = 0.5` | 소수를 받는다. 정수로 절삭하면 상한이 0바이트가 되어 모든 페치가 실패한다 |
| `requests_per_second = 0` | 0은 "무제한"이 아니라 0으로 나누기다. 양수만 받는다 |

환경변수는 문서화된 설정 키 전반을 덮어쓴다(`ANCHOR_DB_PATH`, `ANCHOR_TIMEOUT_SECONDS`, `ANCHOR_RATE_LIMIT_RPS` 등). 두 개만 구현하고 "항상 우선한다"고 적는 것은 사양과 구현의 불일치다.

```toml
[storage]
db_path        = "~/.anchor/store.db"
keep_versions  = 20
compression    = "zstd:6"

[fetch]
user_agent       = "Anchor/<릴리스 버전> (+https://github.com/julgi80ai-stack/anchor-mcp)"
respect_robots   = true
timeout_seconds  = 30
max_redirects    = 5
max_content_mb   = 8
default_max_age  = 86400

[fetch.rate_limit]
requests_per_second = 1.0
burst               = 3

[fetch.archive_fallback]
enabled         = false        # 명시적 활성화 필요 — 외부 서비스에 조용히 의존하지 않는다
aggregator      = ""           # 예: 자체 호스팅 MemGator 엔드포인트
archive_list    = ""           # 애그리게이터에 넘길 아카이브 목록 JSON (git.io 종료 대응)
timeout_seconds = 20
# 주의: MemGator를 애그리게이터로 쓸 경우 --spoof 를 켜지 말 것.
#       Anchor의 정직한 클라이언트 원칙(§5.4)에 위배된다.

[anchor]
context_chars       = 48
max_edit_ratio      = 0.15     # k = len(exact) * 이 값, 최대 64
min_quote_chars     = 12       # 미만은 생성 거부
short_quote_chars   = 32       # 미만은 SHORT 경고
time_budget_ms      = 200
max_document_bytes  = 2097152

[server]
transport = "stdio"   # stdio | http
```

---

## 10. 비기능 요구사항

| 항목 | 목표치 | 측정 방법 |
|---|---|---|
| 캐시 히트 응답 | p95 < 15 ms (본문 1MB 이하) | 벤치마크 스위트 |
| 조건부 요청 절감 | 변경 없는 문서에서 다운로드 바이트 0 | `fetch_log` 집계 |
| 앵커 재검증 처리량 | 500 앵커 / 60초 (네트워크 제외) | 벤치마크 |
| **앵커 매칭 최악 사례** | **앵커당 p99 < 250 ms, 전체 정지 없음** | 대폭 개편 문서 시나리오 |
| **`UNRESOLVED` 비율** | **정상 코퍼스에서 1% 미만** | 골든 벤치마크 |
| 메모리 | 상주 < 150 MB | 8MB 문서 100건 연속 처리 시 |
| 동시성 | 단일 프로세스 다중 클라이언트 안전 | WAL 모드 + 저장소 계층 직렬화 |
| **동시성 격리** | **한 문서의 작업이 다른 문서·읽기 전용 도구를 막지 않을 것** | **URL 단위 락 (v1.5)** |
| 이식성 | Linux / macOS / Windows | CI 매트릭스 |

최악 사례 지표(4행)가 v1.1에서 추가된 이유는 §6.2에 있다. 평균이 아니라 꼬리를 봐야 한다.

**`UNRESOLVED` 비율의 사거리 (v1.7)**: 5행의 목표치는 §6.2의 실효 문서 길이 한계(약 19만 자, 기본 예산 200ms 기준) 안에서 성립한다. 그보다 큰 문서에서 3단계가 실패하면 4단계는 판정을 내지 못한다 — 예산을 늘리면 그만큼 넓어진다. 게이트가 이 사실을 못 잡는 이유도 함께 적어 둔다: 최악 사례 시나리오의 인용문은 짧아 regex 경로만 밟고, 정상 코퍼스는 개정하지 않은 원문을 자기 자신과 대조해 1단계에서 끝난다.

**동시성 격리 (v1.5)**: 전역 락으로 모든 도구를 감싸면 안전하지만, 백그라운드 배치 검증이 도는 동안 나머지 도구가 전부 멈춘다 — Tasks 확장의 목적과 정면으로 어긋난다. 직렬화는 필요한 최소 범위에만 둔다: 저장소는 자체적으로 직렬화하고, 서비스는 같은 URL의 "조회 → 판단 → 생성" 구간만 URL 단위로 잠근다. **직렬화 책임을 서버가 아니라 라이브러리 계층에 두는 이유**는, 라이브러리를 직접 쓰는 사람도 같은 보장을 받아야 하기 때문이다.

---

## 11. 프로젝트 구조

```
anchor-mcp/
├── pyproject.toml
├── README.md              # 선행기술 크레딧 포함 (§14)
├── LICENSE                # Apache-2.0 전문
├── NOTICE                 # 저작권 고지 및 이중 라이선스 선택 명시
├── THIRD-PARTY.md         # 제3자 구성요소 라이선스 대장
├── src/anchor/
│   ├── __init__.py
│   ├── config.py          # 설정 로딩 및 검증 (stdlib tomllib, v1.4)
│   ├── models.py          # 도메인 데이터클래스
│   ├── errors.py          # 예외 계층
│   ├── fetcher/
│   │   ├── client.py      # httpx 기반 조건부 GET
│   │   ├── robots.py      # robots.txt 캐시 및 판정
│   │   ├── ratelimit.py   # 호스트별 토큰 버킷
│   │   └── archive.py     # Memento 애그리게이터 / CDX 폴백
│   ├── normalize/
│   │   ├── extract.py     # 본문 추출 + 마크다운 변환
│   │   ├── text.py        # 유니코드/공백 정규화
│   │   └── hashing.py     # blake3 래퍼
│   ├── anchoring/
│   │   ├── selector.py    # 앵커 생성, 품질 판정
│   │   ├── matcher.py     # 4단계 매칭
│   │   ├── approx.py      # 근사 문자열 검색 (regex / Myers)
│   │   └── budget.py      # 시간 예산 관리
│   ├── export/
│   │   ├── timemap.py     # RFC 7089 직렬화
│   │   ├── robustlinks.py # Robust Links 직렬화
│   │   └── diff.py
│   ├── store/
│   │   ├── schema.sql     # 신규 DB용 전체 스키마 (현재 v6)
│   │   ├── migrations/    # 증분 SQL. 기존 DB는 이걸로 따라온다
│   │   └── repository.py  # 유일한 SQL 접근 지점 (내부 직렬화 포함)
│   ├── service.py         # 공개 파사드 (Anchor 클래스)
│   ├── server.py          # MCP 도구 등록, Task 수명주기
│   └── cli.py
├── tools/
│   └── audit_licenses.py  # 라이선스 감사 (릴리스 전·분기별 실행)
├── tests/
│   ├── unit/
│   ├── integration/       # 로컬 HTTP 픽스처 서버 사용
│   └── fixtures/          # 실제 HTML 스냅샷 (네트워크 없이 재현)
└── benchmarks/
    ├── anchoring/         # Hypothesis 공개 주석 데이터 기반
    └── fetch/
```

### 11.1 런타임 의존성

라이선스는 2026-08-16 기준 확인값이며, 전체 대장은 `THIRD-PARTY.md`에 있다. **모두 permissive이며 Apache-2.0 배포와 호환된다.**

```toml
[project]
dependencies = [
    # 본문 추출. 라이선스 하한 1.8.0 — 미만은 GPLv3+이므로 Apache-2.0 배포와
    # 충돌한다. 이 하한 아래로 낮추지 말 것. 기능 하한은 1.9.0 —
    # markdown 출력(output_format="markdown")이 이 버전에서 추가됐다 (v1.3).
    "trafilatura>=1.9.0",       # Apache-2.0
    "readability-lxml",         # Apache-2.0  (추출 폴백)
    "lxml",                     # BSD-3-Clause
    "markdownify",              # MIT
    "regex",                    # Apache-2.0 AND CNRI-Python  (§11.2 참조)
    "httpx",                    # BSD-3-Clause
    "protego",                  # BSD-3-Clause  (robots.txt)
    "charset-normalizer",       # MIT
    "blake3",                   # CC0-1.0 OR Apache-2.0 → Apache-2.0 선택
    "zstandard",                # BSD-3-Clause
    "pypdf",                    # BSD-3-Clause
    "typer",                    # MIT
    "mcp",                      # MIT
]
```

설정 로딩은 표준 라이브러리(tomllib + dataclass)로 구현한다 — `pydantic-settings`는 쓰지 않는다 (v1.4). `pydantic`은 `mcp` SDK의 트랜지티브 의존성으로만 배포물에 포함되며, 트랜지티브 목록(beautifulsoup4·soupsieve 등)은 `THIRD-PARTY.md`가 관리한다. 개발 전용 의존성(pytest, hypothesis 등)의 라이선스 정책도 `THIRD-PARTY.md` §2.1에 있다.

### 11.2 의존성 관련 제약

| 항목 | 내용 |
|---|---|
| **`trafilatura` 하한** | v1.8.0에서 GPLv3+ → Apache-2.0 전환. 하한 미지정 시 라이선스 충돌. CI에서 검증한다(§12) |
| **`regex` 복합 라이선스** | `Apache-2.0 AND CNRI-Python`. Apache-2.0 배포에는 무관하나, **Anchor를 GPLv2로 재라이선스하는 경로는 막힌다.** 제거가 필요해지면 §6.2의 Myers 비트벡터 자체 구현으로 대체 가능 |
| **`blake3` 이중 라이선스** | `CC0-1.0 OR Apache-2.0` 중 **Apache-2.0을 선택**한다. `NOTICE`에 명시 |
| **외부 프로세스** | MemGator(MIT)는 의존성이 아니라 사용자가 자체 호스팅하는 별도 서비스다. 배포물에 포함하지 않는다 |

> v1.0 대비 변경: `rapidfuzz` 제거, `regex` 추가. 근사 매칭 방식 변경(§6.2)에 따른 것.

---

## 12. 테스트 전략

| 층위 | 대상 | 방식 |
|---|---|---|
| 단위 | 정규화, 해시, 앵커 매칭 4단계 | 순수 함수, 네트워크 없음 |
| 골든 | 실제 HTML 스냅샷 **36건 이상** → 기대 본문 | 픽스처 비교. 추출기 회귀 방지. **코퍼스가 정규화 규칙을 실제로 밟는지 검사한다** (v1.6) |
| 변형 | 원문에 광고 삽입·문단 이동·문장 수정을 프로그램으로 가하고 상태 판정 검증 | 7상태 각각 최소 5케이스 |
| **앵커 벤치마크** | **Hypothesis 공개 주석 데이터의 실제 인용문 집합** | **성공률 + p99 지연. 회귀 시 CI 실패** |
| **최악 사례** | **긴 문서 + 짧은 일반 인용문 + 대폭 개편** | **정지 없이 `UNRESOLVED` 반환하는지** |
| 통합 | 304, 리다이렉트, 402, 429+Retry-After, 타임아웃, 아카이브 폴백 | 로컬 픽스처 서버 |
| 상호운용 | 생성한 TimeMap을 외부 Memento 파서로 검증 | RFC 7089 준수 확인 |
| 속성 | 임의 텍스트에 대해 `cite → verify(동일 버전) == INTACT` | Hypothesis(라이브러리) |
| **복사-인용** | **화면에서 복사한 문장이 저장 본문에서 발견되는가** (서식·각주·NBSP·조판 줄바꿈·PDF·**여러 블록에 걸친 선택**) | **HTML/PDF 실입력 (v1.5, v1.6)** |
| **언어 형평** | **같은 성격의 개정이 언어에 따라 다른 상태로 갈리지 않는가** | **영·한·일·중 대조 (v1.5)** |
| **리다이렉트** | **홉별 robots 판정, 별칭 캐시 히트, 조건부 요청 유지** | **로컬 픽스처 (v1.5)** |
| **동시성** | **같은 URL 동시 페치의 문서 중복, 서로 다른 URL의 병렬성, 배치 중 읽기 응답성** | **스레드 부하 (v1.5)** |
| **원자성·크래시** | **마이그레이션 중단 후 재개방, 스레드 접근 시 무결성, 데이터가 든 구버전 DB의 상승** | **별도 프로세스 (v1.5) · 행이 든 v1~v4 픽스처 (v1.6)** |

**커버리지 목표**: `anchoring/`, `normalize/` 95% 이상. 나머지 80% 이상.

> **감사에서 배운 것 (v1.5)**: 위 층위를 다 갖추고 커버리지 목표를 채운 상태에서도 실증된 결함 52건이 나왔다. 공통점은 **정상 경로만 밟는 픽스처**였다 — 리다이렉트 없는 서버, 문단이 한 줄인 HTML, 한 줄짜리 PDF, 단일 스레드. 픽스처가 현실의 평범한 조건(리다이렉트·조판·중복 문단·동시 호출)을 포함하지 않으면 어떤 커버리지 수치도 그 구멍을 가리지 못한다.

앵커 벤치마크는 참고용이 아니라 **CI 게이트**다. §6.2의 변경 이유가 성능이므로, 성능 회귀를 코드로 막지 않으면 같은 함정에 다시 빠진다. 단, C등급 데이터는 런타임 수집이라 네트워크가 필요하므로 **선택 실행**(수동 트리거·주기 실행)이며, 실행되면 게이트로 작동한다 (v1.4, ADR-0002 결과 절과 정합). 네트워크가 필요 없는 §10 지표(캐시 히트 지연, 최악 사례, UNRESOLVED 비율)는 `benchmarks/run_micro.py`가 잰다.

### 12.1 픽스처 출처 정책 (v1.2 신규)

테스트 데이터에는 코드와 다른 종류의 제약이 있다. 웹 주석은 사용자 생성 콘텐츠이고, 원본 웹페이지는 각 사이트의 저작물이다. 저장소에 커밋하는 순간 재배포가 된다.

| 등급 | 용도 | 데이터 출처 | 저장소 커밋 |
|---|---|---|---|
| **A** | 골든 (본문 추출 정확도) | 퍼블릭 도메인, CC 라이선스 문서, 본인 소유 콘텐츠 | ✅ 커밋 |
| **B** | 변형 (7상태 판정) | A등급 문서에 프로그램으로 광고 삽입·문단 이동·문장 수정을 가한 합성 데이터 | ✅ 커밋 |
| **C** | 앵커 벤치마크 (성능) | 공개 주석 데이터를 런타임에 수집 | ❌ **스크립트만 커밋** |

C등급을 커밋하지 않는 것이 핵심이다. Hypothesis의 `anchoring-test-tools`가 URL 목록을 입력으로 받아 동작하는 방식이 정확히 이 패턴이며, 같은 구조를 따른다.

A등급 픽스처 30건 중 **최소 10건은 한국어 문서**로 구성한다(§14 리스크 참조).

### 12.2 라이선스 게이트 (v1.2 신규)

라이선스 위반은 사람의 기억으로 막을 수 없다. 트랜지티브 의존성은 눈에 보이지도 않고, 라이선스는 실제로 바뀐다 — trafilatura가 그랬다.

| 검사 | 실패 조건 |
|---|---|
| 카피레프트 차단 | `pip-licenses` 출력에 GPL / AGPL / LGPL / SSPL / BUSL 문자열 존재 |
| `trafilatura` 하한 | 설치된 버전이 1.8.0 미만 |
| `NOTICE` 동기화 | 새 런타임 의존성이 `THIRD-PARTY.md`에 없음 |

CI에서 매 푸시·PR마다 실행하고, `tools/audit_licenses.py`로 로컬에서도 재현 가능하게 한다.

---

## 13. 로드맵

| 버전 | 범위 | 완료 기준 |
|---|---|---|
| **v0.1** | fetch + 해시 + 변경 감지. CLI만 | 같은 URL 두 번 호출 시 두 번째가 네트워크 0바이트 |
| **v0.2** | 앵커 생성·재검증, 버전 보존, 근사 매칭 + 예산 | 변형 테스트 7상태 통과 + 최악 사례에서 정지 없음 |
| **v0.3** | MCP 서버, 도구 9종, Tasks 확장 | **Claude Desktop에서 `mcp-server-fetch`를 Anchor로 교체해도 불편이 없을 것** |
| **v0.4** | diff, gc, stats, PDF, TimeMap/Robust Links 내보내기 | 30일 실사용 후 hit_rate 80% 이상 |
| **v0.5** | 아카이브 폴백 | `GONE` 판정 전 아카이브 확인이 실제로 인용을 구제하는 사례 확보 |
| **v1.0** | 문서화, 마이그레이션, CI 매트릭스, 안정 스키마 | 본 사양서 전 항목 충족 |
| *v1.3 후보* | Web Bot Auth 서명, 공유 캐시 내보내기, 앵커 이식 포맷 | — |

v0.3의 완료 기준이 v1.0에서 바뀌었다. **`mcp-server-fetch` 드롭인 대체**가 가장 현실적인 채택 경로이기 때문이다. 그 서버는 MCP 생태계에서 가장 널리 쓰이는 페처이지만 캐시도 버전도 출처도 없다. 같은 인터페이스에 그것들을 얹으면 사용자는 설정 한 줄만 바꾸면 된다.

**v1.3의 공유 캐시 내보내기가 유일하게 "표준"에 가까워지는 지점이다.** 서명된 버전 스냅샷과 앵커를 다른 사용자와 교환할 수 있게 하는 것. 하지만 이는 v1.0이 실제로 쓰인 다음에만 의미가 있으므로 의도적으로 뒤로 뺀다.

---

## 14. 크레딧 (README 필수 게재)

Anchor는 다음 성과 위에 서 있다. README와 문서에 명시한다.

- **Memento — RFC 7089**, Herbert Van de Sompel, Michael L. Nelson, Robert Sanderson 외. 웹에 시간 차원을 도입한 원저작.
- **W3C Web Annotation Data Model** — TextQuoteSelector / TextPositionSelector.
- **Hypothesis** — 다단계 퍼지 앵커링의 실전 구현과, 그 실패 모드의 공개 문서화.
- **Klein, Van de Sompel, Jones et al.** — reference rot과 content drift의 실증 연구 (PLOS ONE 2014, 2016).
- **Robust Links Specification** — 인용 표기 규약.
- **Sawood Alam, Michael L. Nelson** — MemGator. 중앙 서비스가 사라진 뒤에도 Memento를 쓸 수 있게 한 자체 호스팅 애그리게이터.
- **Robert Knight** — `anchor-quote`. 근사 매칭이 왜 `diff-match-patch`보다 나은지를 벤치마크로 보여준 작업. §6.2의 설계 근거.

결정 근거는 `docs/decisions/0001` D1 및 `docs/decisions/0002`에 있다.

이것은 형식적 예의가 아니다. 이 분야에는 20년간 이 문제를 붙들어온 사람들이 있고, 그들의 용어와 표준을 이름만 바꿔 재구현하는 것은 기술적으로도 전략적으로도 손해다.

---

## 15. v1.7 → v1.8 변경 이력

2차 병렬 감사에서 실증된 결함 중 **4단계(정직한 클라이언트 계약, 군집 2)** 의 robots 묶음을 반영했다. 이 절의 항목은 전부 **선언과 실제가 어긋난** 자리다 — 우회하지 않겠다고 적어 둔 문서가 있으니 아무도 확인하지 않았고, 그래서 흔한 구성에서 규칙이 통째로 사라지는 것을 오래 몰랐다.

| # | 절 | 변경 | 근거가 된 결함 |
|---|---|---|---|
| 1 | **5.2** | robots.txt의 **3xx를 따라간다** (최소 5홉, RFC 9309 §2.3.1.2) | `status != 200`이면 본문을 버려, http→https·CDN 이관·캐노니컬 정리에서 `Disallow: /`조차 무시하고 사이트 전체를 가져갔다. 그 판정이 24시간 캐시됐다 |
| 2 | 5.2 | 선두 **BOM 무시** (RFC 9309 §2.3) | Windows 편집기로 저장된 robots.txt에서 파서가 `User-agent:`를 지시자로 읽지 못해 규칙 그룹 전체를 버렸다 |
| 3 | 5.2, 5.4 | robots.txt에도 **크기 상한·타임아웃 적용** | 20MB robots.txt가 통째로 캐시 DB에 들어앉고, 설정 1초짜리 타임아웃이 10초를 기다렸다. §5.4는 이미 "모든 응답"이라고 적혀 있었다 |
| 4 | **5.4** | 빈 User-Agent를 **설정 단계에서 거부**. 검증을 `Config` 생성 시점으로 | 빈 UA로 요청이 나가고 robots 매칭이 빈 토큰으로 수행됐다. `load_config`를 지나야만 검증돼 라이브러리 직접 사용 경로(§8)가 비어 있었다 |
| 5 | **5.2** | 애그리게이터가 지목한 **URI-M에도 robots 판정** | 직접 페치가 `explicit`으로 막히는 경로를 애그리게이터 한 겹으로 우회할 수 있었다 — "어떤 우회도 하지 않는다"가 말뿐이 됐다 |
| 6 | **5.1** | 영구(301·308)만 정본 URL을 바꾼다 | 302 인터스티셜(동의 장벽·지역 게이트) URL이 Memento의 URI-R로 굳고 장벽이 사라져도 되돌아오지 않았다 |
| 7 | 5.1 | 옛 별칭이 자기 콘텐츠를 서빙하면 별칭을 버린다 | 별칭이 리다이렉트를 멈추면 `final_url == norm_url`이라 교정 분기가 실행되지 않아, 목적지 문서 이력에 **다른 리소스의 본문**이 꽂혔다 |
| 8 | 5.1 | 영구 리다이렉트로 하나가 된 두 문서를 합친다 | A의 앵커가 B의 본문과 대조되면서 검증 기록에는 다른 문서의 버전 id가 남았다 — 감사 추적이 앞뒤가 맞지 않았다 |
| 9 | 5.1 | 문서·버전 생성을 **멱등**으로 | 락 키는 입력 URL인데 생성은 최종 URL이라, 캐노니컬 리다이렉트에서 맨 `sqlite3.IntegrityError`가 호출자에게 올라갔다(재현율 25/30) |
| 10 | **5.4** | 검증자는 **정본 URL 홉에만** 싣는다 | 목적지가 정직하게 준 304를 "변한 것 없음"으로 읽어 옛 본문을 계속 반환했고, 이사한 사실이 영구히 감지되지 않았다. 타 호스트에 ETag가 샜다 |
| 11 | 5.4 | `https → http` 강등 리다이렉트 거부 | 무결성 보장이 없는 채널에서 받은 본문이 인용 근거가 되고 정본 URL이 평문으로 기록됐다 |

## 16. v1.6 → v1.7 변경 이력

2차 병렬 감사(Opus 10기, 2026-08-18)에서 실증된 결함 중 **3단계(판정 정확성, 군집 5)** 의 조치를 반영했다. 이 절의 항목은 전부 "판정이 조용히 틀리는" 부류다 — 거짓 MISSING은 멀쩡한 인용을 죽었다고 하고, 거짓 ALTERED는 다른 절의 닮은 남을 "당신 인용문의 현재 모습"으로 내민다. 둘 다 사용자가 확인할 방법이 없다.

| # | 절 | 변경 | 근거가 된 결함 |
|---|---|---|---|
| 1 | **6.2** | 코어 스캔이 **거리 k 이하인 모든 자리**를 후보로 돌려준다 | 코어마다 전역 최소 하나만 보면, 요약절이 앞머리를 옮기고 풀인용이 꼬리를 옮긴 흔한 편집에서 앞·뒤 코어의 최적이 둘 다 디코이에 떨어져 진짜 위치가 검사조차 되지 않았다(디코이 캠페인 4,000건 중 거짓 MISSING 3,856건) |
| 2 | 6.2 | 3단계 후보 **상한에 걸리면 확정하지 않고 4단계로 이양** | 32개 중 최선을 확정해, 진짜 개정문이 38번째면 앞쪽 형제 문단이 `found_text`로 보고됐다 — D-044가 고친 실패 모드의 경계가 1에서 32로 옮겨졌을 뿐이었다 |
| 3 | 6.2 | 3단계 예산 소진 시에도 확정하지 않는다 (`UNRESOLVED`) | 같은 이유다. 다 보지 못한 채 고른 최선은 근거가 아니다 |
| 4 | **6.2** | 4단계 결과에 **문맥 뒷받침 관문** 신설 | 삭제된 v2.0.0 항목에 v1.9.0 절의 템플릿 문장(뜻이 정반대)이 "현재 모습"으로 붙었다. 닮은 정도로는 갈리지 않는다 — 문맥이 살아 있는데 인용문이 그 옆에 없으면 삭제다 |
| 5 | 6.2 | 후보 선택에서 **뒷받침되는 쪽을 앞세운다** | 1번 조치로 후보가 넓어지면서 새로 생긴 위험이다. 부록의 표준 문안이 개정된 본문보다 원문에 가까운 것은 약관에서 흔하다 |
| 6 | 6.2 | 편집거리 상한의 문자 체계 반영을 **연속 함수**로 | "과반이면 2.5배"라는 계단 바로 아래가 항상 틀렸다. 라틴 약어·연도·백분율이 섞인 일본어·중국어 문장은 흔히 밀도 0.5 밑으로 내려간다(`GDPは3.2%増加した。`는 0.462) |
| 7 | 6.2 | 예산 확인 주기를 **DP 칸 수**로 환산 | 주기가 행 수라 초과량이 인용문 길이에 비례했다 — 예산 200ms에 68,902자 인용문이 1,133ms(§10 위반). `cite`에 길이 상한이 없어 공개 API로 도달 가능했다 |
| 8 | 6.2 | v1.5의 "두 경로의 판정이 일치한다"를 **정정** | 그 차분 비교는 디코이가 없는 입력만 밟았다. 1번 조치 뒤에야 참이 된다 |
| 9 | 6.2, 10 | 4단계의 **실효 문서 길이 한계(약 19만 자)** 명시 | 2MB는 탐색 범위의 상한이지 예산 안에 훑을 수 있는 크기가 아니다. 사양이 허용하는 크기와 예산이 허용하는 크기가 다르다는 사실 자체가 적혀 있지 않았다 |
| 10 | **6.3** | 절단 시 `ALTERED`도 보류한다 | "다 보지 못했으면 단정하지 않는다"가 `MISSING`에만 적용돼 있었다. 인용문이 경계에 걸치면 잘린 꼬리가 편집거리로 계산돼, 원문 무손상인데 잘린 조각이 "현재 모습"으로 제시됐다 |
| 11 | 7.3 | `attention`에 `position_hint`·`found_offset` 추가 | 편집거리만으로는 같은 자리의 개정인지 다른 절에서 온 문단인지 호출자가 알 수 없다 |
| 12 | **4.1** | **스키마 v6** — `versions.last_observed_at`·`last_observed_seq` | 본문 해시로 중복을 제거하면서 관측의 시간축이 접혔다. `latest`(포인터)와 `latest~N`(캡처 시각)이 갈려, 되돌림에서 기본 diff가 비고 일어난 적 없는 전이를 보여주고 중간 판본에 도달할 수 없었다 |
| 13 | 7.4 | `latest~N`을 **관측 순서**로 정의. TimeMap은 캡처 시각 순 유지 | 위와 같다. 둘은 서로 다른 질문에 답한다 — RFC 7089의 시간축은 Memento-Datetime이다 |
| 14 | 7.6 | `has_pending_verification`의 기준을 **검증한 버전**으로 | 시각 기준이라 되돌림·아카이브에서 현재 본문이 방금 바뀌었는데 False를 줬다 |
| 15 | **6.2** | 코어 스캔의 덩이를 **패턴 길이마다 끊는다** | 덩이를 `score ≤ k`인 이어진 자리로만 묶었더니, k가 코어 길이에 근접하는 긴 인용문(라틴 340자~)에서 문서 전체가 덩이 하나가 되어 #1의 조치가 무효가 됐다. 그 구간의 88.5%가 거짓 MISSING |
| 16 | **6.3** | `UNRESOLVED`의 뜻을 **증거가 갈리는 경우**까지 넓힘 | #4의 관문이 "표지가 살아 있는데 옆에 없다 = 삭제"로 읽어, **자리를 옮기며 개정된** 인용문을 MISSING으로 단정했다. 절 재배치는 평범한 편집이다. 두 상황은 증거로 갈리지 않으므로 어느 쪽으로도 단정하지 않는다 |
| 17 | 6.2 | 4단계 판정 경계를 "일치 없음 → MISSING / 뒷받침 없음 → UNRESOLVED"로 분리 | 위와 같다. 없다는 것은 말할 수 있고, 무엇인지 모르는 것은 말할 수 없다 |

이 절의 조치는 앞선 조치가 **절반만 고친 것**을 마저 고친 경우가 많다(1은 D-042, 2·4는 D-044, 7은 D-045, 6은 D-049의 뒤를 잇는다). 재현 스크립트 하나가 통과하는 것은 완료가 아니라는 §12의 교훈이 그대로 되풀이됐다.

## 17. v1.5 → v1.6 변경 이력

2차 병렬 감사(Opus 10기, 2026-08-18)에서 실증된 결함 101건 중 **1단계(정규화, 군집 1)** 의 조치를 반영했다. 1차 조치가 만든 코드에서 나온 것들이라, 각 항목은 "고치면서 무엇을 부쉈는가"의 기록이기도 하다.

| # | 절 | 변경 | 근거가 된 결함 |
|---|---|---|---|
| 1 | **5.3** | **NORM_VERSION 3** — 줄과 블록을 먼저 인식하고, 지우는 규칙은 산문 줄 안에서만 적용한다 | 전역 치환이 줄·울타리 인식보다 앞서 있던 것이 군집 1 결함 22건의 공통 뿌리였다 |
| 2 | 5.3 | 태그 제거를 **서식 전용 인라인 태그**로 한정 | 태그 모양을 전부 지워 산문의 `<updated>`·`List<String>`이 사라졌고, `<updated>`→`<published>` 개정이 같은 문자열로 붕괴해 verify가 INTACT를 보고했다 |
| 3 | 5.3 | 강조 기호를 **어절 내부에서 벗기지 않는다** | `2*3*4`가 `234`가 되어 원문에 없던 수치가 만들어졌다 |
| 4 | 5.3 | NFC를 문자 제거 **뒤로** 이동 | 폭 없는 문자를 지우면 결합 문자가 비로소 인접하는데 NFC는 이미 끝나 있었다 |
| 5 | 5.3 | 코드 울타리·들여쓴 코드·인라인 코드 격리 | 코드 블록이 비고 들여쓰기가 뭉개져 돌려준 코드가 문법적으로 무효였다 |
| 6 | 5.3 | 줄 접기에 **레코드 감지** 도입, 구조 표지 경계를 좁힘 | 로그·CSV·표·설정이 접혀 인접하지 않던 값이 이웃이 됐고, 반대로 `2026. 3. 15.`가 번호 목록으로 오인돼 접히지 않았다 |
| 7 | 5.3 | 접합 공백을 **문서 전체의 문자 체계**로 결정, CJK 사이 공백 제거 | 경계 문자 하나만 보면 일본어 문서에 없던 공백이 들어갔다. 추출기가 CJK 줄바꿈을 공백으로 바꾸는 경로도 함께 닫았다 |
| 8 | 5.3 | 줄 구분자 6종 통일, 폭 없는 문자 제거 범위 축소 | U+2028 등이 본문에 남아 복사 인용이 실패했고, ZWJ·ZWNJ·LRM·RLM 제거가 이모지·정서법·문단 방향을 훼손했다 |
| 9 | 5.3 | 정규화 후 빈 본문은 **저장하지 않는다** | `char_count: 0` 판본이 저장되면 그 문서의 앵커가 전부 MISSING으로 뒤집혔다 |
| 10 | 5.3 | 블록 구분이 무너진 추출 결과는 폴백 | 제목이 뒤 문단에 낱말째 붙어 화면 복사 인용이 성립하지 않았다 |
| 11 | **6.1** | 인용문 검색을 **블록 구분자에 유연하게** — 공백·줄바꿈의 연속은 하나로 보고, 줄머리 목록 표지는 건너뛴다. 앵커의 `exact`는 저장 본문에 실재하는 문자열로 잡는다 | 두 문단·두 목록 항목을 한 번에 인용하는 흔한 행동이 전부 `QuoteNotFound`였다(10건 중 2건만 성공) |
| 12 | 6.1 | `QuoteNotFound`가 "저장하지 않은 영역일 수 있다"를 구분해 말한다 | 날조 거부와 글자 하나 다르지 않은 문구여서, 사용자가 화면에서 복사한 문장에 대해 "원문에 없다"는 거짓 진술을 받았다 |
| 13 | **12** | 골든 코퍼스가 정규화 규칙을 **실제로 밟도록** 요구(코드 블록·표·목록·CJK·결합 문자·산문 속 별표·유니코드 공백), 그 커버리지를 테스트로 고정 | 골든 30건이 NORM_VERSION 2의 새 규칙을 하나도 시험하지 않아, 정규화가 본문을 삭제하는데도 전부 통과했다 |
| 14 | 12 | **데이터가 든** 구버전 DB의 마이그레이션을 시험 대상으로 명시 | 회귀 테스트가 행이 0인 DB만 올려, 실사용 저장소가 영구히 열리지 않는 결함을 놓쳤다 |

---

## 18. v1.4 → v1.5 변경 이력

병렬 감사(Opus 10기, 2026-08-17)에서 **재현 스크립트로 실증된 결함 52건**의 조치를 반영했다. 기존 테스트 198개가 전부 통과하는 상태에서 나온 것들이라, 각 항목은 사양의 사각지대이기도 하다.

| # | 절 | 변경 | 근거가 된 결함 |
|---|---|---|---|
| 1 | **4.1** | `documents.current_version` 신설 — "현재 원문 버전"과 "캡처 시각 최대값"을 분리 | 아카이브 폴백 시 재검증이 앵커 생성에 쓴 본문과 자기 자신을 대조해 죽은 문서가 영구 `INTACT`가 됐다 |
| 2 | **4.1** | `document_aliases` 신설 | 리다이렉트되는 URL은 조회키와 저장키가 갈려 캐시가 영구히 빗나갔다 (v0.1 완료 기준 위반) |
| 3 | **4.1** | `UNIQUE`에 `source` 추가 | 아카이브 본문이 기존 live와 같으면 그 행을 재사용하며 출처·URI-M·Memento 시각이 소실됐다 |
| 4 | 4.2 | `gc keep >= 1` 명시 | `--keep 0`이 앵커 없는 문서의 최신본까지 삭제 |
| 5 | **5.1** | 말미 슬래시를 리다이렉트에 위임, IPv6 대괄호 보존 | 정규화 멱등성 위반은 IPv6가 유일했다 |
| 6 | **5.2** | 리다이렉트 **홉마다** robots 재판정 | 리다이렉터를 거치면 금지 경로·타 호스트를 robots 요청조차 없이 취득 |
| 7 | **5.2** | robots 5xx·연결 실패를 거부로 (RFC 9309 §2.3.1.4), 5분 캐시 | 서버가 과부하로 규칙을 못 주는 순간에 무제한 접근으로 전환됐다 |
| 8 | **5.3** | **NORM_VERSION 2** — 유니코드 공백 통일, 폭 없는 문자·마크다운 강조·잔존 태그 제거, 조판 줄바꿈 접기(문자 체계 인식), PDF 분철 복원 | "화면에서 복사 → cite"라는 유일한 진입 경로가 자주 깨졌다 |
| 9 | **5.3** | `pipeline_version`을 경로별로 분리 + 인코딩 판별기 포함 | `text/plain`이 실행하지도 않은 trafilatura 버전을 기록해 `renormalized` 안전장치가 무력화 |
| 10 | 5.3 | 콘텐츠 타입 정확 일치 | `text/plaintext`가 plain 경로로 오분류 |
| 11 | **5.4** | `Retry-After` HTTP-date 해석, 상한 초과 시 절삭 대신 재시도 중단, 크기 상한을 모든 응답에 적용, 레이트 제한 동시성 | 서버 지정 대기를 60초로 잘라 일찍 두드렸고, 거대한 오류 페이지를 통째로 버퍼링했다 |
| 12 | **6.1** | 문자 체계 인식 임계 (한자·가나는 정보량 2.5배로 환산, 한글은 라틴 기준) | 완결된 중국어 문장이 인용 거부됐다 |
| 13 | 6.1 | 중복 출현 경고 | 앵커는 첫 출현에 묶이므로 인용한 인스턴스가 지워져도 `INTACT`가 될 수 있다 |
| 14 | **6.2** | regex/Myers 경로 임계 6 → 3, 편집거리 비율에 문자 체계 반영 | CJK 완결 문장이 느린 경로에 고정돼 7KB 기사에서 판정 실패 (한국어 140ms → 2.2ms) |
| 15 | **6.2** | 코어별 창을 전부 정제, 창 오른쪽 여유 `m+2k` | 인용문 한쪽 끝이 제목·리드에 더 잘 정렬되면 거짓 `MISSING` |
| 16 | **6.2** | 3단계가 후보 전부를 훑어 최선 선택 | 템플릿 반복 문서에서 무관한 형제 문단이 "인용문의 현재 모습"으로 보고됐다 |
| 17 | **6.2** | 예산을 단계 **내부**에서 강제 | 4000자 인용문이 200ms 예산에 7.7초를 썼다 |
| 18 | **6.3** | 절단된 문서의 미발견은 `MISSING`이 아니라 `UNRESOLVED`, `truncated` 전달 | 다 보지 못하고 "사라졌다"고 단정했다 |
| 19 | **7.0** | Tasks의 협조적 취소·종료 순서·ttl 정리·비-taskable 거부 | 취소가 상태만 바꾸고 작업은 계속 돌았고, 종료 시 프로세스가 죽었다 |
| 20 | 7.1 | 청크 인자 음수 거부 | 음수 `start_index`가 문서 끝부분을 앞부분인 양 반환 |
| 21 | 7.3 | `stopped_early` 추가 | 부분 결과를 "이상 없음"으로 오독할 수 있었다 |
| 22 | **9** | 로드 시점 검증(타입·범위), 환경변수 전면 지원, `max_content_mb` 소수 | `enabled = "no"`가 참으로 해석되고, `0.5`가 0바이트가 됐다 |
| 23 | **10** | 동시성 격리 지표 신설 — 전역 락 대신 URL 단위 락 | 백그라운드 검증이 나머지 8개 도구를 전부 멈췄다 |
| 24 | **5.2** | 원본에 닿지 못한 경우(호스트 소멸·robots 판정 불능)에도 아카이브 폴백으로 진입. 명시적 robots 거부는 종전대로 우회하지 않는다 | 파이널라이즈 중 실증: 폐쇄된 서비스의 인용을 구제하려 했더니 robots를 못 받아 폴백에 도달조차 못 했다 — 대표 시나리오가 막혀 있었다 |
| 25 | 12 | 회귀 스위트 확대 (198 → 340) | 위 결함 전부가 기존 스위트를 통과했다 |

---

## 19. v1.3 → v1.4 변경 이력

v1.0 릴리스(2026-08-17) 시점 정리.

| # | 절 | 변경 | 근거 |
|---|---|---|---|
| 1 | **11.1, 11 구조, 9** | 설정 로딩을 stdlib(tomllib)로 확정, `pydantic-settings` 의존성 제거, `pydantic`은 mcp 트랜지티브로 재분류 | 실제로 설치·사용되지 않는 의존성을 사양이 요구하고 있었다 |
| 2 | 12 | 앵커 벤치마크 선택 실행 조건 명문화 + `benchmarks/run_micro.py`(§10 지표) 명시 | C등급 데이터는 네트워크 필요 — ADR-0002 결과 절과 정합 |
| 3 | 11.2 | 개발 의존성 라이선스 정책을 `THIRD-PARTY.md` §2.1로 위임 (hypothesis MPL-2.0 dev 전용 포함) | 배포물 미포함 의존성의 관리 위치 명확화 |
| 4 | **6.2** | **근사 검색 경로 선택 확정** — k ≤ 6 regex, k > 6 Myers 비트벡터(코어 축소 + 준전역 DP 검증) | regex 퍼지는 큰 k에서 미발견 시 지수적 — 영어 인용문(글자 수↑ → k↑)에서 MISSING 판정이 무력화되던 실측 결함 수정 |

---

## 20. v1.2 → v1.3 변경 이력

v0.1~v0.4 구현(2026-08-17)에서 확정된 사항 반영. 구현이 사양을 앞서며 발견한 것들이므로, 각 항목의 근거는 코드와 테스트에 있다.

| # | 절 | 변경 | 근거 |
|---|---|---|---|
| 1 | **4.1, 5.3** | **`versions.pipeline_version` 컬럼 신설** — 추출기+정규화 규칙 버전. text_hash의 숨은 입력 | 파이프라인 변경을 원문 변경으로 오보하는 것 방지 |
| 2 | **5.2, 4.1, 7.1** | **outcome 어휘 정식화** — `unchanged`(200인데 text_hash 동일)·`renormalized`(raw 동일+파이프라인 상이)·`created` 추가 | §5.2 본문에만 있던 `unchanged`가 열거에 빠져 있었다 |
| 3 | **5.1** | 추적 파라미터 제거 목록 축소, **`ref`·`s` 제거 금지** | 사이트에 따라 실질 경로 — 서로 다른 문서가 합쳐진다 |
| 4 | 5.2 | 요청 순서: 캐시 조회를 robots 확인 앞으로 | 캐시 반환은 네트워크 요청이 아니다. robots는 요청 직전에 판정 |
| 5 | 4.1 | `robots_cache` 테이블 신설 (origin별 24h 영속화) | CLI는 매 호출이 새 프로세스 — 영속화 없이는 v0.1 완료 기준 불충족 |
| 6 | 4.2 | 검증 이력 참조 버전도 gc에서 보존 | FK 무결성 + 감사 추적 |
| 7 | 7.1 | `max_length`·`content_truncated`·`next_start_index` (mcp-server-fetch 호환 청크 읽기) | 드롭인 대체 경로(§13 v0.3 완료 기준) |
| 8 | 7.0 | Tasks 와이어 형식 주의 — task 기술자를 CallToolResult에 인밴드 반환, tasks/*는 확장으로 서빙 | 현행 프로토콜(2026-07-28) 와이어 게이트가 CreateTaskResult 불허 |
| 9 | 7.4 | `from`/`to` → `from_version`/`to_version` | Python 예약어 |
| 10 | 8 | CLI `list`·`serve` 추가, `anchor-mcp` 진입점 명시 | 구현 반영 |
| 11 | 11.1 | `trafilatura>=1.9.0` — 라이선스 하한(1.8.0)과 기능 하한(markdown 출력) 구분 명시 | 1.8.x에는 markdown 출력이 없다 |

---

## 21. v1.1 → v1.2 변경 이력

라이선스 감사(2026-08-16) 결과 반영. 미확인 항목 16건을 전부 확인해 0건으로 만들었고, 그 과정에서 한 건의 실질적 충돌을 발견했다.

| # | 절 | 변경 | 근거 |
|---|---|---|---|
| 1 | **11.1** | **`trafilatura>=1.8.0` 하한 명시 + 사유 주석** | **v1.8.0 미만은 GPLv3+. Apache-2.0 배포와 충돌** |
| 2 | 11.1 | 전 의존성에 확인된 라이선스 주석 부착 | 추측 제거 |
| 3 | 11.2 신설 | 의존성 제약 3건 (`trafilatura` 하한, `regex` 복합, `blake3` 이중) | 감사 결과 |
| 4 | 5.2 | MemGator 운영 지침 표 (`--spoof` 금지, `--agent`, `--arcs`) | 원칙 위배 방지 + git.io 종료 대응 |
| 5 | 9 | `archive_list` 설정값 추가, spoof 금지 주석 | 동일 |
| 6 | **12.1 신설** | 픽스처 출처 정책 3등급 (A 커밋 / B 커밋 / C 미커밋) | 주석 데이터는 UGC, 원본 페이지는 타인 저작물 |
| 7 | **12.2 신설** | CI 라이선스 게이트 3종 | 사람이 기억으로 막을 수 없다 |
| 8 | 11 구조 | `NOTICE`, `THIRD-PARTY.md`, `tools/audit_licenses.py` 추가 | Apache-2.0 §4(d) 및 재현 가능한 감사 |
| 9 | 14 | Sawood Alam·Michael L. Nelson(MemGator), Robert Knight(anchor-quote) 크레딧 추가 | 설계 근거 제공자 |
| 10 | 로드맵 | 기존 "v1.2 후보" → **v1.3 후보**로 이동 | 사양서 버전과 충돌 회피 |

### 확인된 라이선스 요약

| 구분 | 건수 | 결과 |
|---|---|---|
| 런타임 의존성 | 15 | MIT 6, BSD-3-Clause 5, Apache-2.0 3, 복합 2 — **전부 permissive** |
| 참조 대상 (코드 미포함) | 10 | MIT 6, Apache-2.0 3, BSD-2-Clause 1 |
| 명세 (구현 자유) | 5 | RFC 7089 / 9110 / 9309, W3C Web Annotation, Robust Links |
| **카피레프트** | **0** | GPL / AGPL / LGPL 없음 |

상세는 `THIRD-PARTY.md` 참조.

---

## 22. v1.0 → v1.1 변경 이력

| # | 절 | 변경 | 근거 |
|---|---|---|---|
| 1 | 1.1 | 용어를 `reference rot` / `citation drift`로 정정 | "content drift"는 ML 용어와 충돌 |
| 2 | 1.3 | 의미 변화 판정을 비목표에 명시 추가 | 경계 명확화 |
| 3 | **1.4 신설** | 선행기술 및 계승 | Memento/W3C/Hypothesis 계보 |
| 4 | 2 | Memento 용어 대응표 | RFC 7089 상호운용 |
| 5 | 4.1 | `versions.source`, `versions.source_uri`, `anchors.quality`, `verifications.edit_distance`, `verifications.elapsed_ms` 추가 | 아카이브 폴백 및 진단 |
| 6 | 5.2 | 6단계 아카이브 폴백 추가 | `GONE` 구제 |
| 7 | **6.1** | 짧은 인용문 경고·거부 | 병리적 매칭 케이스 차단 |
| 8 | **6.2** | `rapidfuzz.partial_ratio` 전체 스캔 → 편집거리 상한 근사 검색 + 시간 예산 | **Hypothesis #3919 실패 모드 회피** |
| 9 | 6.3 | `UNRESOLVED` 상태 추가 (6종 → 7종) | 예산 초과 시 정직한 무응답 |
| 10 | **7.0 신설** | MCP 2026-07-28 준수, Tasks 확장 채택 | 장시간 배치 검증 |
| 11 | 7.1 | `start_index` 파라미터 (mcp-server-fetch 호환) | 드롭인 대체 경로 |
| 12 | **7.8 신설** | `get_timemap` — RFC 7089 내보내기 | 상호운용 |
| 13 | **7.9 신설** | `export_robust_links` | 상호운용 |
| 14 | 10 | 최악 사례 지연 및 `UNRESOLVED` 비율 지표 추가 | 꼬리 성능 관리 |
| 15 | 11 | `rapidfuzz` → `regex`, `export/`·`archive.py`·`approx.py`·`budget.py` 모듈 추가 | 위 변경 반영 |
| 16 | 12 | 앵커 벤치마크를 CI 게이트로 승격, 상호운용 테스트 추가 | 성능 회귀 방지 |
| 17 | 13 | v0.3 완료 기준을 "mcp-server-fetch 드롭인 대체"로 구체화, v0.5 아카이브 폴백 신설 | 채택 경로 명확화 |
| 18 | **14 신설** | 크레딧 | 커뮤니티 관계 |

---

*본 사양서는 v1.0 완성 시점을 기준으로 기술한 것이며, 구현은 13절 로드맵에 따라 v0.1부터 단계적으로 진행한다.*
