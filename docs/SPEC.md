# Anchor — 출처 추적형 페치 캐시

**기술사양서 v1.2 (완성 시점 기준)**

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

> **v1.1 → v1.2 변경 요약**: 라이선스 감사 결과를 반영했다. `trafilatura>=1.8.0` 하한을 필수로 지정하고(§11), MemGator 운영 지침을 명문화하고(§5.2, §9), 테스트 픽스처의 출처 정책을 3분류로 나누고(§12), CI에 라이선스 게이트를 추가했다(§12). 전체 목록은 §16 참조.
>
> **v1.0 → v1.1 변경 요약**: 선행기술 조사 결과를 반영해 Memento 호환성을 도입하고(§2, §5.2, §7.8), 앵커 매칭 알고리즘을 성능 안전한 방식으로 교체하고(§6.2), 검증 상태를 7개로 확장하고(§6.3), MCP 최신 스펙의 Tasks 확장을 채택했다(§7.0). 전체 목록은 §16 참조.

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
| `captured_at` | Memento-Datetime | 해당 버전을 획득한 시각 |
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
    robots_allowed  INTEGER NOT NULL DEFAULT 1
);

-- 본문 스냅샷 (Memento: URI-M). text_hash가 같으면 새 버전을 만들지 않는다.
CREATE TABLE versions (
    id            TEXT PRIMARY KEY,
    document_id   TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    text_hash     TEXT NOT NULL,               -- blake3(normalized_text)
    raw_hash      TEXT NOT NULL,               -- blake3(원본 바이트)
    captured_at   TEXT NOT NULL,               -- Memento-Datetime 대응
    byte_size     INTEGER NOT NULL,
    char_count    INTEGER NOT NULL,
    content_blob  BLOB NOT NULL,               -- zstd(normalized_text)
    http_status   INTEGER NOT NULL,
    source        TEXT NOT NULL DEFAULT 'live',-- live | archive
    source_uri    TEXT,                        -- 아카이브에서 온 경우 URI-M
    UNIQUE (document_id, text_hash)
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
    outcome       TEXT NOT NULL,               -- cache_hit | not_modified | changed | archive | error
    http_status   INTEGER,
    bytes_down    INTEGER NOT NULL DEFAULT 0,
    elapsed_ms    INTEGER NOT NULL
);

CREATE INDEX idx_versions_doc      ON versions(document_id, captured_at DESC);
CREATE INDEX idx_anchors_doc       ON anchors(document_id);
CREATE INDEX idx_verif_anchor      ON verifications(anchor_id, checked_at DESC);
CREATE INDEX idx_fetchlog_time     ON fetch_log(requested_at DESC);
```

### 4.2 저장 정책

- 버전 blob은 zstd level 6으로 압축. 일반 기사 기준 원문 대비 25~30%.
- 기본 보존 정책: 문서당 최근 20개 버전 + 앵커가 참조하는 모든 버전. **앵커가 가리키는 버전은 절대 삭제하지 않는다.**
- `anchor gc` 명령으로 고아 버전 정리.

---

## 5. 페치 파이프라인

### 5.1 URL 정규화

동일 문서의 중복 등록을 막기 위해 페치 전 다음 순서로 정규화한다.

1. 스킴·호스트 소문자화, 기본 포트 제거
2. 프래그먼트(`#...`) 제거
3. 추적 파라미터 제거 — `utm_*`, `fbclid`, `gclid`, `ref`, `s`, `igshid` 등 (설정으로 확장 가능)
4. 남은 쿼리 파라미터 키 기준 정렬
5. 경로 말미 슬래시 정규화 (단, 리다이렉트 응답이 있으면 그것을 우선)

### 5.2 요청 순서

```
1. robots.txt 확인 (호스트별 24h 캐시, protego 사용)
   └ 거부 → RobotsDisallowed, 네트워크 요청 없음

2. 캐시 조회
   └ max_age 이내의 버전 존재 → cache_hit 반환 (네트워크 0)

3. 레이트 제한 대기 (호스트별 토큰 버킷, 기본 1 req/s, burst 3)

4. 조건부 GET
   If-None-Match: <etag>
   If-Modified-Since: <last_modified>
   User-Agent: <설정값, 5.4절>
   Accept: text/html, application/xhtml+xml, text/plain, application/pdf

5. 응답 분기
   304 → not_modified. last_checked_at만 갱신. 본문 전송 없음.
   200 → 정규화 → text_hash 비교
          동일 → unchanged. 새 버전 생성 안 함.
          상이 → changed. 새 버전 삽입.
   3xx → 정규화된 목적지로 1회 추종 (최대 5홉)
   402 → PaymentRequired (Cloudflare Pay Per Use 등) → 6단계
   403/429 → 지수 백오프 재시도 (최대 3회) → 실패 시 6단계
   404/410 → status = gone → 6단계

6. 아카이브 폴백 (설정으로 활성화, 기본 on)
   └ Memento 애그리게이터 또는 Wayback CDX에 URI-R 조회
     └ URI-M 발견 → 본문 취득 → source='archive'로 버전 삽입
        (source_uri에 URI-M 기록, live 버전과 명확히 구분)
     └ 없음 → 원 상태(gone/forbidden) 확정
```

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
  → 본문 추출 (trafilatura; 실패 시 readability-lxml 폴백)
  → 마크다운 변환
  → 유니코드 NFC 정규화
  → 연속 공백 1개로 축약, 줄 끝 공백 제거, 3줄 이상 개행을 2줄로
  → normalized_text

raw_hash  = blake3(원본 바이트)
text_hash = blake3(normalized_text.encode("utf-8"))
```

`raw_hash`는 달라도 `text_hash`가 같으면 **변경 없음**으로 판정한다. 광고 슬롯, 조회수 카운터, CSRF 토큰 같은 노이즈를 걸러내는 핵심 장치다.

> 이 이중 해시는 앵커 안정성에도 직결된다. 뉴스 사이트는 페이지 로드마다 다른 광고 텍스트를 삽입하므로, **문서 내용이 바뀌지 않아도 문자 오프셋이 달라진다.** 정규화된 본문을 기준으로 삼지 않으면 위치 기반 앵커가 매번 깨진다.

PDF는 `pypdf` 텍스트 추출 후 동일 경로를 탄다. 스캔 PDF는 v1.0 범위 밖이며 `UnsupportedContent`를 반환한다.

### 5.4 네트워크 예절 (compliance)

Anchor는 **정직한 클라이언트**로 동작한다. 이것은 기능이 아니라 전제다.

- **User-Agent**: 기본값 `Anchor/1.2 (+https://github.com/<org>/anchor-mcp)`. 위장·스푸핑 옵션은 제공하지 않는다.
- **robots.txt**: 기본 준수. `respect_robots = false` 설정은 존재하나, 활성화 시 서버 시작 로그에 경고를 출력한다.
- **레이트 제한**: 호스트별 토큰 버킷. `Retry-After` 헤더를 항상 존중한다.
- **조건부 요청**: 항상 사용. 이것이 곧 서버 부하 절감이다.
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

#### 짧은 인용문 경고 (v1.1 신규)

`exact`가 **32자 미만**이면 `quality = SHORT`로 표시하고 응답에 경고를 포함한다.

짧고 일반적인 문자열은 퍼지 매칭에서 병리적 케이스를 만든다. 문서 전체에 유사 후보가 다수 존재해 탐색 비용이 폭증하고, 오탐 확률도 높다. Hypothesis는 실제로 이 조합(긴 문서 + 짧은 일반 인용문) 때문에 클라이언트가 10초 이상 정지하는 문제를 겪었다.

- 32자 미만: 경고와 함께 생성 허용. 재검증 시 시간 예산을 절반으로 적용
- 12자 미만: 생성 거부 (`QuoteTooShort`)
- 권장: 완결된 문장 하나

### 6.2 매칭 알고리즘 (v1.1 개정)

새 버전에 대해 다음 순서로 시도한다. 앞 단계가 성공하면 즉시 종료한다.

| 단계 | 방법 | 판정 |
|---|---|---|
| 1 | `position_hint ± 500자` 범위에서 `exact` 완전 일치 | `INTACT` (score 1.0) |
| 2 | 문서 전체에서 `exact` 완전 일치 (표준 문자열 검색) | `MOVED` (score 1.0) |
| 3 | `prefix + suffix` 문맥으로 후보 구간 특정 후, 그 사이 문자열과 비교 | `ALTERED` (score = 유사도) |
| 4 | **편집거리 상한을 둔 근사 문자열 검색** | 상한 내 발견 → `ALTERED`, 미발견 → `MISSING` |

#### 4단계 상세 — v1.0에서 변경된 핵심

**v1.0 사양의 문제**: `rapidfuzz.partial_ratio` 전체 문서 슬라이딩 스캔은 O(n·m)이며, **찾지 못할 때 가장 느리다.** 검증 대상 대부분이 `INTACT`인 정상 상황에서는 드러나지 않다가, 문서가 대폭 개편된 최악의 상황에서 성능이 무너진다. 이는 Hypothesis가 `diff-match-patch`로 겪은 것과 동일한 실패 모드다. 대체 구현 벤치마크에서 10만 자 문서 / 453개 인용 기준 13,342ms 대 936ms로 14배 차이가 보고됐고, 앵커링 성공률도 근사 매칭 쪽이 높았다.

**v1.1 방식**: 편집거리 상한 `k`를 먼저 정하고, 그 상한을 넘으면 즉시 포기하는 비트병렬 근사 검색을 쓴다.

```python
# k = 허용 편집거리. 인용문 길이에 비례하되 상한을 둔다.
k = min(int(len(exact) * 0.15), 64)
```

구현 우선순위:

1. **주 구현** — `regex` 모듈의 퍼지 매칭. C 구현이며 오류 상한을 네이티브로 지원한다.
   ```python
   pattern = regex.compile(f"({regex.escape(exact)}){{e<={k}}}", regex.BESTMATCH)
   ```
2. **폴백/최적화** — Myers 비트벡터 근사 문자열 검색 직접 구현. `approx-string-match` 계열이 쓰는 알고리즘으로, 패턴 길이 ≤ 64일 때 워드 단위 병렬 처리로 O(n)에 가깝다.

`score`는 `1 - (edit_distance / len(exact))`로 계산하며, `edit_distance`도 함께 저장한다. 비율만으로는 짧은 인용문에서 오해를 낳기 때문이다.

#### 시간 예산 (v1.1 신규)

앵커 하나당 매칭 시간 상한을 둔다. 초과 시 판정을 강제하지 않고 `UNRESOLVED`를 반환한다.

| 조건 | 기본 예산 |
|---|---|
| `quality = OK` | 200 ms |
| `quality = SHORT` | 100 ms |
| 문서 길이 상한 | 2 MB (초과 시 앞 2MB만 탐색, `TRUNCATED` 플래그) |

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
| `UNRESOLVED` | 시간 예산 초과로 판정 보류 *(v1.1 신규)* | 예산을 늘려 재검증하거나 인용문을 길게 재설정 |

`ALTERED`와 `MISSING`은 서로 다른 사건이다. 전자는 원문이 바뀐 것이고, 후자는 인용이 무효가 된 것이다. 이 구분이 보고서 검수에서 가장 실질적인 가치를 낸다.

`UNRESOLVED`는 실패가 아니라 **정직한 무응답**이다. 이 상태를 만들지 않으려고 임의 판정을 내리면 도구 전체의 신뢰가 무너진다.

**Anchor는 `ALTERED`의 의미를 판정하지 않는다.** 오탈자 수정인지 입장 번복인지는 변경 전후 텍스트를 보고 호출자가 결정한다. 이 경계를 지키는 것이 LLM 비의존 원칙의 실체다.

---

## 7. MCP 도구 인터페이스

### 7.0 프로토콜 준수 (v1.1 신규)

**MCP 2026-07-28 스펙**을 따른다. 실무상 영향은 셋이다.

- **Stateless core** — 서버는 sticky session을 요구하지 않는다. 로컬 SQLite가 유일한 상태이므로 자연히 충족된다.
- **Streamable HTTP** — `Mcp-Method`, `Mcp-Name` 헤더를 붙인다.
- **Tasks 확장** — `verify_citations`와 `refresh_all`은 수십~수백 건의 네트워크 요청을 동반하므로 **Task로 반환한다.** 클라이언트가 `tasks/get`으로 진행률을 조회하고 `tasks/cancel`로 중단할 수 있다. 즉시 응답형 도구(`fetch_document`, `cite`, `get_version` 등)는 일반 tool call을 유지한다.

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
  "start_index": 0                          // mcp-server-fetch 호환: 청크 읽기
}

// 출력
{
  "document_id": "018f...",
  "version_id": "018f...",
  "url": "https://example.com/report",
  "title": "2026 Report",
  "outcome": "not_modified",                // cache_hit|not_modified|changed|created|archive
  "captured_at": "2026-08-16T04:12:00Z",
  "text_hash": "b3:9a4f...",
  "char_count": 18432,
  "source": "live",                         // live | archive
  "content": "# 2026 Report\n\n...",         // include_content=true일 때만
  "network": { "bytes_down": 0, "elapsed_ms": 142 }
}
```

### 7.2 `cite`

인용문에 앵커를 부여한다.

```jsonc
// 입력
{ "document_id": "018f...", "quote": "재페치의 절반은 낭비다", "note": "3장 근거" }

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
      "before": "재페치의 절반은 낭비다",
      "after":  "재페치의 약 60%는 낭비다",
      "match_score": 0.88,
      "edit_distance": 4
    }
  ],
  "network": { "requests": 12, "not_modified": 9, "bytes_down": 48210 }
}
```

`attention` 배열에는 조치가 필요한 항목(`ALTERED`/`MISSING`/`GONE`/`UNRESOLVED`)만 담는다. `INTACT` 36건을 전부 나열해 컨텍스트를 채우지 않는다.

### 7.4 `diff_versions`

두 버전의 본문 차이를 통합 diff로 반환한다.

```jsonc
{ "document_id": "018f...", "from": "latest~1", "to": "latest", "context_lines": 2 }
```

### 7.5 `get_version`

과거 버전의 본문을 그대로 꺼낸다. 원문이 사라진 뒤에도 인용 당시의 텍스트를 확인할 수 있다.

### 7.6 `list_documents`

캐시된 문서 목록을 상태·최종 확인 시각과 함께 반환한다. 필터: `status`, `host`, `has_pending_verification`.

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

    cit = ax.cite(doc.id, "재페치의 절반은 낭비다")
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
anchor cite <doc-id> "인용문"
anchor verify --older-than 7d --format table
anchor timemap <doc-id> --format link
anchor export --robust-links --format markdown
anchor stats
anchor gc --keep 20
```

---

## 9. 설정

`~/.anchor/config.toml`. 환경변수 `ANCHOR_*`가 항상 우선한다. **비밀값은 파일에 두지 않는다.**

```toml
[storage]
db_path        = "~/.anchor/store.db"
keep_versions  = 20
compression    = "zstd:6"

[fetch]
user_agent       = "Anchor/1.2 (+https://github.com/<org>/anchor-mcp)"
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
| 동시성 | 단일 프로세스 다중 클라이언트 안전 | WAL 모드 + 쓰기 직렬화 |
| 이식성 | Linux / macOS / Windows | CI 매트릭스 |

최악 사례 지표(4행)가 v1.1에서 추가된 이유는 §6.2에 있다. 평균이 아니라 꼬리를 봐야 한다.

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
│   ├── config.py          # 설정 로딩 및 검증 (pydantic-settings)
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
│   │   ├── schema.sql
│   │   ├── migrations/
│   │   └── repository.py  # 유일한 SQL 접근 지점
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
    # 본문 추출. v1.8.0 미만은 GPLv3+이므로 Apache-2.0 배포와 충돌한다.
    # 이 하한은 기능 요구가 아니라 라이선스 요구다. 낮추지 말 것.
    "trafilatura>=1.8.0",       # Apache-2.0
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
    "pydantic",                 # MIT
    "pydantic-settings",        # MIT
    "typer",                    # MIT
    "mcp",                      # MIT
]
```

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
| 골든 | 실제 HTML 스냅샷 30건 → 기대 본문 | 픽스처 비교. 추출기 회귀 방지 |
| 변형 | 원문에 광고 삽입·문단 이동·문장 수정을 프로그램으로 가하고 상태 판정 검증 | 7상태 각각 최소 5케이스 |
| **앵커 벤치마크** | **Hypothesis 공개 주석 데이터의 실제 인용문 집합** | **성공률 + p99 지연. 회귀 시 CI 실패** |
| **최악 사례** | **긴 문서 + 짧은 일반 인용문 + 대폭 개편** | **정지 없이 `UNRESOLVED` 반환하는지** |
| 통합 | 304, 리다이렉트, 402, 429+Retry-After, 타임아웃, 아카이브 폴백 | 로컬 픽스처 서버 |
| 상호운용 | 생성한 TimeMap을 외부 Memento 파서로 검증 | RFC 7089 준수 확인 |
| 속성 | 임의 텍스트에 대해 `cite → verify(동일 버전) == INTACT` | Hypothesis(라이브러리) |

**커버리지 목표**: `anchoring/`, `normalize/` 95% 이상. 나머지 80% 이상.

앵커 벤치마크는 참고용이 아니라 **CI 게이트**다. §6.2의 변경 이유가 성능이므로, 성능 회귀를 코드로 막지 않으면 같은 함정에 다시 빠진다.

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

## 15. v1.1 → v1.2 변경 이력

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

## 16. v1.0 → v1.1 변경 이력

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
