# THIRD-PARTY.md

**Anchor 제3자 구성요소 라이선스 대장**

| 항목 | 값 |
|---|---|
| 프로젝트 | Anchor (`anchor-mcp`) |
| 배포 라이선스 | **Apache-2.0** |
| 최종 확인일 | **2026-08-17** |
| 확인 방법 | PyPI JSON API, npm 레지스트리, 각 저장소 `LICENSE` 원문 (§5 스크립트로 재현 가능) |
| 미확인 항목 | **없음** |

> 확인은 배포자가 선언한 메타데이터와 저장소의 라이선스 원문에 근거한다. 법률 자문이 아니며, 상업적 배포나 조직 도입 전에는 전문가 검토를 권한다.
>
> **이 문서는 대장이다.** "지금 무엇을 쓰고 있나"만 기록하며, 매 릴리스 갱신한다. "왜 그렇게 하기로 했나"는 `docs/decisions/0002-license-and-reuse-policy.md`에 있다.

---

## 1. 판정

**모든 구성요소가 permissive이며, Apache-2.0 배포와 호환된다. 카피레프트(GPL/AGPL/LGPL) 구성요소는 없다.**

주의가 필요한 세 건은 §4에 따로 정리했다.

---

## 2. 런타임 의존성 (배포물에 포함)

| 패키지 | 확인 버전 | 라이선스 | 표기 위치 | Apache-2.0 호환 |
|---|---|---|---|---|
| `trafilatura` | 2.2.0 | **Apache-2.0** | `license-expression`, `pyproject.toml` | ✅ ⚠️§4.1 |
| `regex` | 2026.7.19 | **Apache-2.0 AND CNRI-Python** | `license-expression` | ✅ ⚠️§4.2 |
| `readability-lxml` | 0.8.4.1 | **Apache-2.0** | `license` 필드 | ✅ |
| `lxml` | 6.1.1 | **BSD-3-Clause** | `license` 필드 | ✅ |
| `protego` | 0.6.2 | **BSD-3-Clause** | `license-expression` | ✅ |
| `markdownify` | 1.2.3 | **MIT** | Trove classifier | ✅ |
| `blake3` | 1.0.9 | **CC0-1.0 OR Apache-2.0** | `license` 필드 | ✅ ⚠️§4.3 |
| `zstandard` | 0.25.0 | **BSD-3-Clause** | `license-expression` | ✅ |
| `charset-normalizer` | 3.5.1 | **MIT** | `license` 필드 | ✅ |
| `pypdf` | 6.16.1 | **BSD-3-Clause** | `license-expression` | ✅ |
| `httpx` | 0.28.1 | **BSD-3-Clause** | `license` 필드 + classifier | ✅ |
| `pydantic` | 2.13.4 | **MIT** | `license-expression` | ✅ (`mcp` 트랜지티브 — 직접 사용 없음) |
| `typer` | 0.27.1 | **MIT** | `license-expression` | ✅ |
| `mcp` (Python SDK) | 2.0.0 | **MIT** | `license` 필드 + classifier | ✅ |
| `beautifulsoup4` | 4.15.0 | **MIT** | `license` 필드 | ✅ (`markdownify` 트랜지티브) |
| `soupsieve` | 2.9.2 | **MIT** | `license-expression` | ✅ (`beautifulsoup4` 트랜지티브) |

라이선스 종류별 집계: MIT 7, BSD-3-Clause 5, Apache-2.0 2, 복합 2 (총 16).

> v1.0 변경: `pydantic-settings` 제거 — 설정 로딩은 표준 라이브러리(tomllib)로 구현되어 실제로 설치되지 않는다. `pydantic`은 `mcp` SDK의 트랜지티브 의존성으로만 배포물에 포함된다.

### 2.1 개발 의존성 (배포물 미포함)

테스트·감사 전용이며 배포 아티팩트에 포함되지 않으므로 Apache-2.0 배포와 무관하다. MPL-2.0은 파일 단위 카피레프트로, ADR-0002 원칙 B의 허용 목록에 있으며 배포물에 안 들어가므로 이중으로 문제없다.

| 패키지 | 확인 버전 | 라이선스 |
|---|---|---|
| `pytest` | 9.1.1 | MIT |
| `packaging` | 26.3 | Apache-2.0 OR BSD-2-Clause |
| `hypothesis` | 6.165.10 | **MPL-2.0** (dev 전용) |
| `pytest-cov` | 7.1.0 | MIT |

---

## 3. 배포물에 포함되지 않는 것

### 3.1 외부 프로세스로 호출

| 대상 | 라이선스 | 관계 |
|---|---|---|
| **MemGator** (`oduwsdl/MemGator`) | **MIT** (`LICENSE` 원문 확인) | 사용자가 자체 호스팅. Anchor는 HTTP로만 호출하며 코드를 포함하지 않는다 |

Anchor 배포물에 MemGator 바이너리나 소스가 들어가지 않으므로 라이선스 혼합이 발생하지 않는다. MIT이므로 포함하더라도 문제는 없으나, 언어가 다르고(Go) 이미 완성된 서버이므로 분리 유지가 합리적이다.

> **운영 지침**: MemGator의 `--spoof` 옵션(무작위 user-agent 위장)은 사용하지 않는다. Anchor의 정직한 클라이언트 원칙에 위배된다. 또한 MemGator의 기본 아카이브 목록이 `git.io` 단축 URL인데 해당 서비스는 2022년 종료됐으므로, 자체 호스팅 시 목록 JSON을 직접 지정해야 한다.

### 3.2 참조만 하는 것 (코드 미포함)

설계·알고리즘·실패 사례를 참조했으나 코드를 가져오지 않은 대상. 법적 고지 의무는 없으나 크레딧을 남긴다.

| 대상 | 라이선스 | 참조 내용 |
|---|---|---|
| **Hypothesis client** (`hypothesis/client`) | **BSD-2-Clause** (`LICENSE` 원문 확인, Copyright 2013-2019 Hypothes.is Project and contributors) | 다단계 앵커 재부착 전략, 성능 실패 사례(issue #3919) |
| **dom-anchor-text-quote** | **MIT** (npm 4.0.2) | TextQuoteSelector 생성 규약 |
| **dom-anchor-text-position** | **MIT** (npm 5.0.0) | TextPositionSelector 규약 |
| **approx-string-match** | **MIT** (npm 2.0.0) | 비트벡터 근사 매칭 접근 |
| **anchor-quote** (`robertknight/anchor-quote`) | **MIT** (`package.json`) | 근사 매칭 대비 성능 벤치마크 수치 |
| **diff-match-patch** (`google/diff-match-patch`) | **Apache-2.0** (`LICENSE` 원문 확인) | Anchor는 **채택하지 않음**. 성능 특성 비교 목적 |
| **apache/incubator-annotator** | **Apache-2.0** | 퍼지 텍스트 인용 매칭 논의 |
| **mcp-server-fetch** (`modelcontextprotocol/servers`) | **MIT** (`LICENSE` 원문 확인) | 드롭인 대체 대상. 인터페이스 호환 참조 |
| **ArchiveBox** | **MIT** (`LICENSE` 원문 확인) | 향후 연동 후보. 현재 코드 미사용 |
| **changedetection.io** | **Apache-2.0** (`LICENSE` 원문 확인) | 포지셔닝 비교 문서용. 코드 미사용 |

### 3.3 명세 (구현 자유)

허락·지불·표기 의무 없이 구현할 수 있다. 예의로 출처를 밝힌다.

| 명세 | 저자/기관 | Anchor 구현 위치 |
|---|---|---|
| **RFC 7089 — Memento** | Van de Sompel, Nelson, Sanderson (IETF) | `export/timemap.py`, 용어 체계 |
| **W3C Web Annotation Data Model** | W3C | `anchoring/selector.py` |
| **Robust Links Specification** | mementoweb.org | `export/robustlinks.py` |
| **RFC 9110 — HTTP Semantics** | IETF | `fetcher/client.py` |
| **RFC 9309 — robots.txt** | IETF | `fetcher/robots.py` |

> RFC 본문 텍스트의 전문 재배포에는 IETF Trust 조건(BCP 78)이 적용된다. 인용과 참조는 자유다.

---

## 4. 주의가 필요한 세 건

### 4.1 `trafilatura` — 버전 하한 필수

**v1.8.0에서 GPLv3+ → Apache-2.0으로 라이선스가 변경됐다.** 현재 배포본(2.2.0)은 Apache-2.0이지만, 하한을 명시하지 않으면 사용자 환경에 GPLv3+ 버전이 설치될 수 있고 그 순간 Apache-2.0 배포와 충돌한다.

```toml
# pyproject.toml
"trafilatura>=1.8.0",   # v1.8.0 미만은 GPLv3+. 낮추지 말 것.
```

이 하한은 **기능 요구가 아니라 라이선스 요구**다. 의존성 정리 중 실수로 낮추는 일을 막기 위해 주석을 반드시 함께 둔다.

### 4.2 `regex` — 복합 라이선스

선언값은 `Apache-2.0 AND CNRI-Python`이다. CNRI-Python(Python 1.6 라이선스)은 OSI 승인 permissive 라이선스이므로 Apache-2.0 배포에 문제가 없다.

다만 한 가지 파급 효과가 있다. **CNRI-Python은 GPLv2와 호환되지 않는 것으로 분류된다.** 즉 Anchor 전체를 훗날 GPLv2로 재라이선스하는 경로는 이 의존성 때문에 막힌다. Apache-2.0을 유지하는 한 무관하지만, 기록해 둔다.

대안이 필요해질 경우: 근사 매칭을 Myers 비트벡터 알고리즘으로 자체 구현하면 `regex` 의존을 제거할 수 있다(사양서 §6.2의 폴백 경로).

### 4.3 `blake3` — 선택형 이중 라이선스

`CC0-1.0 OR Apache-2.0`. 둘 중 하나를 선택할 수 있다. **Anchor는 Apache-2.0 쪽을 선택한다.** 배포 라이선스와 일치시키는 편이 고지 처리가 단순하고, 일부 조직이 CC0의 특허 조항 부재를 문제 삼기 때문이다.

`NOTICE`에 선택 사실을 명시한다.

---

## 5. 재확인 방법

라이선스는 바뀐다. trafilatura가 실제로 그랬다. **이 표는 사람의 기억이 아니라 스크립트로 유지한다.**

```bash
# tools/audit_licenses.py — 릴리스 전 및 분기별 실행
python3 tools/audit_licenses.py --fail-on-copyleft
```

CI 게이트:

```yaml
# .github/workflows/license.yml
name: license-audit
on: [push, pull_request]
jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install . pip-licenses
      - name: 카피레프트 의존성 차단
        run: |
          pip-licenses --format=csv --with-urls > licenses.csv
          cat licenses.csv
          # GPL/AGPL/LGPL이 트랜지티브로라도 유입되면 빌드 실패
          if grep -Ei 'GNU General Public|Affero|LGPL|GPLv[23]' licenses.csv; then
            echo "::error::카피레프트 라이선스가 감지되었습니다. THIRD-PARTY.md를 확인하세요."
            exit 1
          fi
      - name: trafilatura 하한 확인
        run: python -c "
          import trafilatura, sys
          from packaging.version import Version
          v = Version(trafilatura.__version__)
          sys.exit(0 if v >= Version('1.8.0') else
                   print(f'trafilatura {v} < 1.8.0 (GPLv3+). 라이선스 충돌.') or 1)"
```

---

## 6. 감사 스크립트

스크립트는 문서에 인라인하지 않는다. 실행 가능한 파일로 유지해야 실제로 돌아가는지 확인할 수 있다.

**`tools/audit_licenses.py`** — 본 문서 §2의 표는 이 스크립트의 출력에서 생성됐다.

```bash
python3 tools/audit_licenses.py                                  # 조회만
python3 tools/audit_licenses.py --check-floors                   # 버전 하한 포함
python3 tools/audit_licenses.py --fail-on-copyleft --check-floors # CI 게이트
```

스크립트가 확인하는 것:

| 검사 | 방법 |
|---|---|
| 라이선스 종류 | PyPI JSON API의 `license_expression` → `license` → Trove classifier 순으로 조회 |
| 카피레프트 유입 | GPL / AGPL / LGPL / SSPL / BUSL 문자열 탐지 |
| 버전 하한 | 설치된 `trafilatura`가 1.8.0 이상인지 (§4.1) |
| 대장 누락 | `RUNTIME_PACKAGES` 목록에 없는 패키지 경고 |

새 의존성을 추가하면 스크립트의 `RUNTIME_PACKAGES`와 본 문서 §2를 함께 갱신한다. 둘 중 하나만 고치면 다음 감사에서 불일치로 잡힌다.

---

## 7. 변경 이력

| 날짜 | 변경 |
|---|---|
| 2026-08-17 | v1.0: `pydantic-settings` 제거(미설치 — 설정은 stdlib), `pydantic`을 mcp 트랜지티브로 재분류, `beautifulsoup4`·`soupsieve` 추가(markdownify 트랜지티브), 개발 의존성 절(§2.1) 신설 (hypothesis MPL-2.0 dev 전용 포함). 런타임 16건, 미확인 0건 |
| 2026-08-16 | 최초 작성. 런타임 의존성 15건, 참조 대상 10건, 명세 5건 확인 완료. 미확인 0건 |
