# Anchor — 문서 세트

**AI가 인터넷에서 얻은 정보의 시간적·출처적 무결성 계층**

> Anchor는 AI가 사용한 웹 근거를 시간에 걸쳐 다시 검증할 수 있게 한다.

사양·철학·판단 근거·라이선스 대장이 먼저 확정됐고, 그 위에 v0.2(fetch + 변경 감지 + 앵커 재검증, CLI)까지 구현된 상태다. 다음 단계는 SPEC.md §13 로드맵의 v0.3(MCP 서버)이다.

```bash
# 설치 (Python 3.11+)
uv venv --python 3.12 && uv pip install -e .

# 사용
anchor fetch https://www.rfc-editor.org/rfc/rfc7089.html   # created
anchor fetch https://www.rfc-editor.org/rfc/rfc7089.html   # cache_hit — 네트워크 0바이트
anchor cite  https://www.rfc-editor.org/rfc/rfc7089.html \
  "The HTTP-based Memento framework bridges the present and past Web."
anchor verify        # INTACT | MOVED | ALTERED | MISSING | GONE | UNREACHABLE | UNRESOLVED
anchor list
```

---

## 문서 지도

```
anchor-mcp/
├── README.md                  ← 지금 이 문서
├── LICENSE                    Apache-2.0 전문
├── NOTICE                     저작권 고지 (배포물)
├── THIRD-PARTY.md             제3자 라이선스 대장 (배포물)
├── docs/
│   ├── SPEC.md                기술사양서 v1.2
│   ├── MANIFESTO.md           설계 철학 / 브랜딩 기준 (개정 2판)
│   └── decisions/
│       ├── 0001-prior-art-and-positioning.md
│       └── 0002-license-and-reuse-policy.md
├── src/anchor/                v0.1 구현 (fetcher / normalize / store / cli)
├── tests/                     단위 + 통합 (로컬 픽스처 서버, 네트워크 없음)
└── tools/
    └── audit_licenses.py      라이선스 감사 (실행 가능)
```

## 어떤 문서를 언제 보는가

| 상황 | 문서 |
|---|---|
| 무엇을 만드는지 알고 싶다 | `docs/SPEC.md` |
| 기능을 넣을지 말지 애매하다 | `docs/MANIFESTO.md` §6 원칙, §7 반대 대상 |
| 왜 이렇게 설계했는지 궁금하다 | `docs/decisions/0001` |
| 경쟁 제품과의 차이를 써야 한다 | `docs/decisions/0001` 부록 B |
| 새 의존성을 추가하려 한다 | `docs/decisions/0002` 원칙 A·B·C → `THIRD-PARTY.md` 갱신 |
| 남의 코드를 가져오려 한다 | `docs/decisions/0002` 재사용 3방식 |
| 지금 무엇을 쓰고 있는지 확인한다 | `THIRD-PARTY.md` |
| 릴리스를 준비한다 | `tools/audit_licenses.py --fail-on-copyleft --check-floors` |
| 대외 문구를 쓴다 | `docs/MANIFESTO.md` §11 메시지 자산 |

**대장과 판단 기록은 성격이 다르다.** `THIRD-PARTY.md`는 "지금 무엇을 쓰고 있나"를 답하며 매 릴리스 갱신한다. `decisions/`는 "왜 그렇게 하기로 했나"를 답하며 **동결**한다 — 판단이 바뀌면 기존 문서를 고치지 않고 새 ADR을 써서 대체한다.

---

## 30초 요약

AI에게 뭔가를 물어보면 출처 링크를 달아준다. 그런데 한 달 뒤 그 페이지의 내용이 바뀌거나 사라져도 아무도 모른다. 학술 논문이 인용한 웹 콘텐츠의 약 4분의 3이 3년 안에 바뀐다는 연구가 있다.

Anchor는 AI가 가져온 웹 문서를 시점별로 보관하고, 인용한 문장이 지금도 원문에 그대로 있는지 언제든 다시 확인해 준다. **검색은 하지 않는다.**

## 계보

이것은 새로 발견한 문제가 아니다.

```
2009  Memento 제안 — "웹은 기억력이 나쁘다"
2013  RFC 7089 — HTTP에 시간 차원을 더하다
2016  "네 개 중 세 개의 참조가 바뀐 콘텐츠로 이어진다"
2025  Time Travel 서비스 종료          ◀── 인프라 소멸
2026  AI 에이전트가 웹을 대량 소비      ◀── 수요 폭증
      ▲
      └─ Anchor
```

Memento는 틀려서 죽은 게 아니라 **중앙 서비스로 지어졌기 때문에** 죽었다. Anchor의 로컬 우선 설계는 그 실패 모드에 대한 답이다. 서버가 없으면 폐쇄될 서버도 없다.

자세한 내용은 `docs/decisions/0001`.

---

## 현재 상태

| 항목 | 상태 |
|---|---|
| 기술사양서 | v1.2 확정 |
| 설계 철학 | 개정 2판 확정 |
| 선행기술 조사 | 완료 (ADR-0001) |
| 라이선스 감사 | 완료 — 런타임 15건 전수 확인, 카피레프트 0건 (ADR-0002) |
| PyPI `anchor-mcp` | 사용 가능 (2026-08-16 확인) |
| 구현 | **v0.2 완료** (2026-08-17) — fetch + 이중 해시 + 변경 감지 + 앵커 재검증(7상태), CLI. 다음은 v0.3 MCP 서버 |

### 알려진 미해결 항목

- **npm `anchor-mcp` 선점됨** — Python 프로젝트이므로 당장 무관하나, 향후 JS 포팅 시 다른 이름 필요
- **MemGator 유지보수 상태 미확인** — 최신 릴리스 1.0-rc9 (2024-05). 아카이브 폴백은 선택 기능이므로 차단 요인 아님
- **`anchor-quote` 저장소 브랜치 구조** — 라이선스는 MIT로 확인(`package.json`)했으나 `LICENSE` 파일 위치 미확인

---

## 라이선스

Apache-2.0. 제3자 구성요소는 `THIRD-PARTY.md`, 고지는 `NOTICE` 참조.
