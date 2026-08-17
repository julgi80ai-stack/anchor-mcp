# ADR-0001 — 선행기술 계승과 포지셔닝 (KR)

| | |
|---|---|
| 상태 | **승인 (Accepted)** |
| 결정일 | 2026-08-16 |
| 대체 | — |
| 관련 | SPEC.md §1.4, §2, §6.2, §14 · MANIFESTO.md §3 |

> 이 문서는 **판단 기록**이다. 결정을 되돌리려면 이 문서를 고치지 말고 새 ADR을 써서 대체한다. 뒤집힌 판단의 이유도 기록으로 남아야 하기 때문이다.

---

## 결정 (요약)

**Anchor는 새로운 계층을 정의하지 않는다. 기존 표준(Memento, W3C Web Annotation, Robust Links)의 로컬 클라이언트 구현으로 자신을 위치시킨다.**

대외 서사는 "새로운 것"이 아니라 **"돌아온 것"** 이다.

---

## 맥락

### 이 문제는 이미 정의되어 있었다

조사 전 가설은 "AI 에이전트의 웹 근거 무결성 계층이 비어 있다"였다. 절반만 맞았다.

```
2009 ─── Memento 제안 — "웹은 기억력이 나쁘다"
2013 ─── RFC 7089 — HTTP에 시간 차원을 더하다
2014 ─── "다섯 편 중 한 편이 참고문헌 부패를 겪는다"  (Klein et al., PLOS ONE)
2015 ─── Time Travel 서비스 개시 (LANL Research Library)
2016 ─── "네 개 중 세 개의 참조가 바뀐 콘텐츠로 이어진다" (Jones et al., PLOS ONE)
  ⋮      기관 자금으로 유지. 일반 개발자 사이에는 거의 알려지지 않음
2025 ─── Time Travel 서비스 종료 (경영상 결정)      ◀── 인프라 소멸
2026 ─── AI 에이전트가 웹을 대량 소비                ◀── 수요 폭증
         ▲
         └─ Anchor
```

RFC 7089은 원본 리소스(URI-R), 과거 버전(URI-M), 버전 목록(TimeMap), 시간 협상(TimeGate)을 정의한다. **초안 설계의 `versions` 테이블은 TimeMap의 로컬 재구현이었다.** 이걸 모른 채 진행했다면 13년 된 표준을 이름만 바꿔 만들 뻔했다.

같은 일이 앵커에서도 있었다. 초안 §6.2의 4단계 매칭 절차는 Hypothesis가 이미 하는 것과 사실상 동일했다. 우연이 아니라, 이 문제의 정답이 하나뿐이라는 뜻이다.

### 그런데 인프라는 사라졌다

LANL Time Travel 서비스는 2025년 말 폐쇄됐고, mementoweb.org는 자료만 남은 정적 사이트가 됐다. Robust Links 서비스도 함께 중단됐다. 생존자는 자체 호스팅 가능한 MemGator뿐이다.

**Memento는 틀려서 죽은 게 아니다. 기관 자금에 의존하는 중앙 서비스로 지어졌기 때문에 죽었다.**

### 그리고 수요는 폭증했다

| 지표 | 값 | 출처 |
|---|---|---|
| 3년 내 변경된 참조 콘텐츠 | **약 75%** | Jones et al., PLOS ONE 2016 |
| 참고문헌 부패를 겪는 STM 논문 | **20%** (100만+ 참조 분석) | Klein et al., PLOS ONE 2014 |
| 조작된 인용을 포함한 신규 논문 | **277편 중 1편** (3년 전 2,828편 중 1편) | 2026년 초 |
| 딥리서치 에이전트 인용 정확도 | OpenAI 78% ~ Claude 94% | 2026년 연구 |
| 변하지 않은 페이지 재요청 비율 | AI 크롤러 트래픽의 **절반 이상** | Cloudflare |
| 크롤 대 참조 비율 | Google 14:1 / OpenAI 1,700:1 / Anthropic 73,000:1 | Cloudflare, 2025 중반 |

논문 제목이 그대로 문제 정의다 — *"Three out of Four URI References Lead to Changed Content."*

### 자리는 비어 있는가

인접 영역을 세 축으로 확인했다. 모든 축에서 Anchor는 반대편에 있다.

| | 기존 도구 | Anchor |
|---|---|---|
| 인용되는 쪽 / 인용하는 쪽 | GEO·AEO 마케팅 도구 (Profound, Scrunch, Otterly) | **인용하는 쪽** |
| 정적 문헌 / 변하는 웹 | 학술 참고문헌 검증 (CiteCheck, Citely, SwanRef) | **변하는 웹** |
| 미래 감시 / 과거 검증 | changedetection.io (32k★) | **과거 검증** |

가장 가까운 인접 제품인 changedetection.io는 "이 페이지가 바뀌었나"를 묻는다. Anchor는 "내가 인용한 문장이 아직 있나"를 묻는다. 페이지 전체가 바뀌어도 인용문이 그대로면 Anchor는 조용하고, 페이지가 거의 안 바뀌었어도 그 문장만 수정됐으면 경고한다.

ArchiveBox는 완전 보존(스냅샷당 수십 MB), Anchor는 본문 추출(압축 텍스트 수십 KB). 인용문 단위 추적이 없다는 점에서 겹치지 않는다. 관계는 경쟁이 아니라 보완이다.

---

## 결정 내용

### D1 — 계승을 명시적으로 선언한다

"새로운 계층 발명"이 아니라 **"죽은 표준의 클라이언트 사이드 부활"** 로 포지셔닝한다.

- SPEC.md에 §1.4(선행기술 및 계승)와 §14(크레딧)를 둔다
- MANIFESTO.md §3을 "계보"로 하고 문서 앞쪽에 배치한다
- 금지 표현에 **"세계 최초", "새로운 표준"** 을 추가한다 — 사실이 아니고, 이 분야 사람들은 바로 알아본다

**근거**: 이 분야는 좁다. Van de Sompel, Nelson, Alam, Klein, Jones, Knight — 20년간 같은 문제를 붙들어온 사람들이고 서로를 안다. 그들의 표준을 조용히 재구현한 프로젝트와 명시적으로 계승을 선언한 프로젝트는 받는 대접이 완전히 다르다. 전자는 무시당하거나 반발을 사고, 후자는 20년 치 신뢰를 빌려온다.

### D2 — 로컬 우선은 Memento의 실패 모드에 대한 답이다

중앙 서비스로 지어져서 자금이 끊기자 죽었다. Anchor는 SQLite 파일 하나로 동작한다. **서버가 없으면 폐쇄될 서버도 없고, 예산 회의에서 사라질 항목도 없다.**

이것은 원칙 1(로컬 우선)의 근거이자, 매니페스토에서 가장 강한 서사다.

### D3 — 상호운용을 채택한다 (재구현 금지)

| 채택 | 근거 명세 | 구현 위치 |
|---|---|---|
| TimeMap 직렬화 | RFC 7089 §5 | `export/timemap.py` |
| TextQuoteSelector / TextPositionSelector | W3C Web Annotation Data Model | `anchoring/selector.py` |
| Robust Links 속성 | Robust Links Spec | `export/robustlinks.py` |
| 아카이브 폴백 | Memento 애그리게이터 (MemGator) | `fetcher/archive.py` |

구현 비용은 직렬화 함수 몇 개다. 얻는 것은 13년 된 RFC와의 상호운용성, 그리고 이 문제를 오래 붙들어온 커뮤니티와의 접점이다.

### D4 — 앵커 매칭은 Hypothesis의 실패를 회피한다

초안의 `rapidfuzz.partial_ratio` 전체 문서 슬라이딩 스캔을 폐기한다.

**근거**: Hypothesis client issue #3919 — 긴 문서에 짧고 일반적인 인용문이 섞이면 퍼지 매칭이 극도로 비효율적이 되어, 정확 일치가 없는 앵커들이 직렬 처리되며 페이지가 10초 이상 멈췄다. 원인은 `diff-match-patch`가 **찾지 못할 때 가장 느리다**는 것이다.

대체 구현 벤치마크(robertknight/anchor-quote)에서 10만 자 문서 / 453 인용 기준:

| 구현 | 앵커 성공 | 고아 | 소요 |
|---|---|---|---|
| dom-anchor-text-quote (diff-match-patch) | 318 | 135 | **13,342 ms** |
| anchor-quote (근사 매칭) | 331 | 122 | **936 ms** |

14배 빠르고 성공률도 높다. 따라서 편집거리 상한을 둔 근사 검색 + 앵커당 시간 예산 + `UNRESOLVED` 상태를 채택한다(SPEC.md §6.2, §6.3).

**이 조사에서 가장 값진 자산은 코드가 아니라 이 실패 기록이다.** 공개 이슈 트래커에 적혀 있고, 라이선스가 필요 없다.

### D5 — 채택 경로는 `mcp-server-fetch` 드롭인 대체다

Anthropic 공식 `mcp-server-fetch`는 MCP 생태계에서 가장 널리 쓰이는 페처지만 **캐시도 버전도 출처도 없다.** 같은 인터페이스에 그것들을 얹으면 사용자는 설정 한 줄만 바꾸면 된다.

v0.3 완료 기준을 "Claude Desktop에서 `mcp-server-fetch`를 Anchor로 교체해도 불편이 없을 것"으로 정한다.

### D6 — 용어는 `reference rot` / `citation drift`를 쓴다

`content drift`는 개발자 생태계에서 머신러닝의 개념 표류(concept drift)를 뜻하는 경우가 압도적이다. GitHub 검색 결과가 전부 Frouros, alibi-detect 계열로 나온다.

Klein / Van de Sompel 계보의 용어가 정확하기도 하고, 검색 충돌도 피한다.

---

## 결과

### 긍정

- 설계의 절반이 공개 명세로 이미 존재하며, 법적 장벽이 없다 (ADR-0002 §명세)
- 앵커 알고리즘의 함정을 밟기 전에 알았다
- "돌아온 것" 서사는 처음부터 만드는 것보다 강하다 — 사실이기 때문이다
- 20년 치 실증 데이터를 문제 정의의 근거로 그대로 쓸 수 있다

### 부정 / 감수하는 것

- **"새로운 것"이라는 마케팅 카드를 버린다.** 투자자·미디어 문법과는 맞지 않는다. 원칙 5(보이지 않아도 된다)를 감안하면 애초에 목표가 아니다
- 표준 호환 비용이 든다 (TimeMap·Robust Links 직렬화, 대략 두 모듈)
- Memento 커뮤니티의 기대치를 상속한다 — RFC 준수가 어설프면 오히려 비판받는다. SPEC.md §12에 상호운용 테스트를 둔 이유다

### 후속 조치

| 항목 | 상태 |
|---|---|
| SPEC.md v1.1 반영 (Memento 용어, 매칭 교체, 상태 7종, TimeMap/Robust Links, Tasks) | ✅ 완료 |
| MANIFESTO.md 개정 2판 (계보 절 신설, 금지 표현 추가) | ✅ 완료 |
| 라이선스 검토 | ✅ 완료 → ADR-0002 |
| PyPI `anchor-mcp` 가용성 | ✅ 확인 (2026-08-16, 사용 가능) |
| npm `anchor-mcp` | ⚠️ **선점됨** (0.1.0). Python 프로젝트이므로 당장 무관하나, 향후 JS 포팅 시 다른 이름이 필요하다 |
| MemGator 유지보수 상태 | ⚠️ 미확인. 최신 릴리스 1.0-rc9 (2024-05). 아카이브 폴백은 선택 기능이므로 차단 요인은 아니다 |

---

## 최종 판단

> **가라. 단, "새로운 것"이라고 말하지 말고 "돌아온 것"이라고 말하라.**

Anchor는 빈 땅에 짓는 집이 아니다. 13년 전에 설계도가 나왔고, 기관이 짓다가 작년에 손을 뗀 건물이다. 그동안 그 건물이 필요한 이유는 100배로 커졌다.

---

---

## 부록 A — 선행기술 분류 지도

조사 대상 전체를 위협 등급으로 분류한 원본 표. 본문은 결정에 필요한 것만 다뤘으므로, 전수 목록은 여기에 남긴다.

| 등급 | 대상 | 관계 | 조치 |
|---|---|---|---|
| 🔴 필수 계승 | Memento (RFC 7089) | 문제 정의와 프로토콜을 이미 완성 | 호환 구현 + 재발명 금지 |
| 🔴 필수 계승 | W3C Web Annotation Selector | 앵커 모델의 정식 표준 | 직렬화까지 정합 |
| 🔴 필수 계승 | Hypothesis 앵커링 스택 | 알고리즘 + 성능 함정 문서화 | 알고리즘 이식, 실패 사례 회피 |
| 🟠 인접·차별화 필요 | changedetection.io (32k★) | 웹 변경 감시 | 축이 다름 — 명시적 비교 필요 |
| 🟠 인접·차별화 필요 | ArchiveBox | 로컬 웹 아카이빙 | 보존 범위가 다름 |
| 🟡 참고 | Perma.cc, Robust Links | 학술 인용 보존 | 출력 포맷 채택 |
| 🟡 참고 | mcp-server-fetch (Anthropic) | MCP 표준 페처 | 캐시·출처 기능 없음 → 대체 대상 |
| 🟢 무관 | AI 인용 추적 도구 | 브랜드 가시성(GEO/AEO) | 다른 문제 |
| 🟢 무관 | CiteCheck, Citely, SwanRef | 학술 참고문헌 존재 검증 | 다른 문제 |

## 부록 B — 인접 제품 상세

본문 §"자리는 비어 있는가"의 근거가 된 원본 비교. README의 비교 섹션과 대외 문구를 쓸 때 이 표를 참조한다.

### B.1 changedetection.io — 유일하게 README에 명시적 비교가 필요한 대상

| | changedetection.io | Anchor |
|---|---|---|
| 방향 | **푸시** — URL을 등록하고 바뀌면 알림 | **풀** — 인용을 등록하고 필요할 때 검증 |
| 단위 | 페이지 전체 (또는 CSS/XPath 선택 영역) | **인용문 하나** |
| 목적 | 재입고, 가격, 정책 변경 감시 | 근거의 유효성 |
| 시간 | 미래를 본다 | **과거를 본다** |
| 형태 | Docker 웹 서비스 + UI | 라이브러리 + MCP 도구 |
| 노이즈 처리 | 사용자가 CSS 선택자 수동 지정 / LLM 필터 | 본문 추출 + 이중 해시로 자동 |
| 소비자 | 사람 | **에이전트** |

배울 점 하나: 이 프로젝트는 최근 LLM 연동으로 "무엇이 중요한 변경인가"를 자연어 규칙으로 판정하는 기능을 넣었다. Anchor는 원칙상 LLM을 부르지 않으므로, **`ALTERED` 판정 후 의미 변화 여부는 호출자(에이전트)에게 넘긴다**는 경계를 SPEC.md §1.3 비목표에 명시했다.

### B.2 ArchiveBox

- 자체 호스팅 웹 아카이버. wget, SingleFile, Chrome, yt-dlp 등 다중 백엔드로 HTML/PDF/PNG/WARC 동시 보존
- 저장소는 SQLite + 파일시스템 — Anchor와 유사한 구조
- **차이 1**: 완전 보존 vs 본문 추출. 스냅샷당 수십 MB 대 압축 텍스트 수십 KB
- **차이 2**: 인용문 단위 추적이 없다. 문서를 보관하지만 "이 문장이 아직 있는지"는 묻지 않는다
- **차이 3**: Docker 필요, Chrome 의존, MCP 노출 없음

관계는 경쟁이 아니라 **보완**이다. ArchiveBox는 증거 보존, Anchor는 근거 검증. 향후 `anchor export --to archivebox` 연동이 자연스럽다.

### B.3 Perma.cc / Robust Links

Perma.cc는 법률 인용의 링크 부패에 대응해 하버드가 만든 영구 보존 서비스다. Robust Links는 링크에 `data-originalurl`, `data-versiondate`, `data-versionurl` 세 속성을 붙여 원본·시점·스냅샷을 함께 표기하는 스펙이며, **Anchor가 출력 포맷으로 채택했다**(SPEC.md §7.9).

```html
<a href="https://example.com/report"
   data-versiondate="2026-08-16"
   data-versionurl="https://web.archive.org/web/20260816/...">2026 보고서</a>
```

Anchor를 쓰지 않는 독자도 인용 시점을 알 수 있게 하는 상호운용 장치다. 구현 비용은 직렬화 함수 하나.

### B.4 AI 인용 추적 시장 — 왜 무관한가

"AI citation" 키워드로 검색하면 두 종류가 나오는데 둘 다 Anchor와 축이 다르다. 이 구분을 기록해 두는 이유는, 대외 문구에서 이들과 혼동될 위험이 실재하기 때문이다.

**GEO/AEO 마케팅 도구** (Profound, Scrunch, Otterly, Wrodium 등)
- 푸는 문제: "내 브랜드가 ChatGPT/Perplexity 답변에 인용되는가" — **인용되는 쪽**의 관점
- 예산 출처가 마케팅. 가격 $29~$140+/월
- 시장이 실재한다는 신호이기도 하다 — Sitecore가 Scrunch를 인수했다 (2026년 6월)

**학술 참고문헌 검증 도구** (CiteCheck, Citely, SwanRef, RefCheck, Hallucinator)
- 푸는 문제: "이 논문 참고문헌이 실존하는가" — 환각 탐지
- **정적 문헌** 대상. DOI가 있고 변하지 않는 것
- 웹 페이지의 시간적 변화는 다루지 않는다

Anchor는 **인용하는 쪽**의, **변하는 웹**에 대한, **시간 검증** 도구다. 세 축 모두에서 반대편이다.

## 부록 C — 가져올 것 목록

재구현하지 말아야 할 것들. 방식(프로세스 분리 / 알고리즘 참조 / 코드 이식)과 라이선스 판단은 ADR-0002를 따른다.

| 대상 | 출처 | 용도 | 방식 |
|---|---|---|---|
| TimeMap 직렬화 | RFC 7089 §5 | 버전 목록 내보내기 | 명세 구현 |
| Selector 데이터 모델 | W3C Web Annotation Data Model | 앵커 JSON 스키마 | 명세 구현 |
| Robust Links 속성 | Robust Links Spec | 인용 내보내기 | 명세 구현 |
| 앵커 매칭 전략 순서 | Hypothesis 앵커링 아키텍처 | SPEC §6.2 | 알고리즘 참조 |
| 근사 문자열 매칭 | Myers 1999 / `approx-string-match` 계열 | SPEC §6.2 4단계 | 알고리즘 참조 |
| 앵커링 테스트 데이터 | `hypothesis/anchoring-test-tools` | 벤치마크 픽스처 (C등급) | 런타임 수집 |
| 아카이브 폴백 | MemGator (자체 호스팅) / Wayback CDX | `GONE` 상태 구제 | 프로세스 분리 |
| 본문 추출 | trafilatura + readability-lxml | SPEC §5.3 | 의존성 |

**Anchor가 실제로 새로 만드는 것은 넷뿐이다.**

1. 위 조각들을 에이전트가 쓸 수 있는 단일 로컬 도구로 조립
2. `raw_hash` / `text_hash` 이중 해시에 의한 자동 노이즈 제거
3. 7가지 검증 상태 코드 — 특히 `ALTERED`와 `MISSING`의 분리
4. MCP 인터페이스

조립과 인터페이스가 전부라는 사실은 약점이 아니다. **리눅스도 POSIX를 발명하지 않았다.**

### 출처

- RFC 7089 — HTTP Framework for Time-Based Access to Resource States (Memento)
- mementoweb.org/about — Time Travel 서비스 2025년 종료 공지
- Klein M, Van de Sompel H, et al. "Scholarly Context Not Found: One in Five Articles Suffers from Reference Rot." PLoS ONE 9(12): e115253 (2014)
- Jones SM, Van de Sompel H, et al. "Scholarly Context Adrift: Three out of Four URI References Lead to Changed Content." PLoS ONE 11(12): e0167475 (2016)
- W3C Web Annotation Data Model / Web Annotations Workshop Report (2014)
- Hypothesis, "Fuzzy Anchoring" 및 client issue #3919
- robertknight/anchor-quote 벤치마크
- Robust Links Specification
- MCP Specification 2026-07-28 Release Candidate
- Cloudflare AI 크롤러 정책 (2026-07 발표, 09-15 시행)
