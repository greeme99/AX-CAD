---
name: cost-engineering
description: >
  Claude Code와 Antigravity 프로젝트의 토큰 비용을 동일 최고 품질 조건 하에서 최소화한다.
  과다 compact를 금지하고, grill-me / Ponytail / Caveman / Handoff 중심으로 지침을 재정비한다.
  추가로 Antigravity(agy) CLI 및 GEMINI.md/.agents/를 교차 검증하고 필요한 스킬 세팅을 실행한다.
  트리거: /cost-engineering, 토큰 최적화, cost engineering, 스킬 정리, Antigravity 검증, agy로 스킬 세팅.
  절대 매 턴 자동 실행하지 않는다. 사용자가 명시적으로 호출할 때만 동작한다.
---

# Cost Engineering for Claude Code + Antigravity Verification (v2.1)

## 핵심 원칙 (절대 위반 금지)

1. **과다 압축 금지**
   `/compact` 또는 `MEMORY.md`/`CLAUDE.md` 자동 압축을 매 턴·세션 시작 시 실행하지 않는다.
   압축은 사용자가 요청하거나 컨텍스트가 실제로 **70~80%**에 도달했을 때만 제안한다.

2. **온디맨드 로딩 & 캐시 프리픽스 보존**
   Skills body, MCP 스키마, 상세 규칙은 호출/경로 매칭 시에만 로드한다.
   `CLAUDE.md`·`GEMINI.md` 상단에는 동적 상태(날짜·이슈)를 넣지 않아 Prompt Cache 히트를 유지한다.

3. **승인 게이트**
   파일 수정, 스킬 설치/OFF, settings 변경, Antigravity 설정 변경은 **반드시 사용자 승인 후** 진행한다.

4. **필수 4대 스킬 중심 + 품질 100% 무손실**
   - `grill-me` (스펙 모호 시 선행)
   - `ponytail` (YAGNI·최소 Diff)
   - `caveman` (불필요 서술 제거)
   - `handoff` (임계 도달/마일스톤 완료 시 세션 인계)
   위 4개를 기본 권장하며, 테스트·타입체크·보안 검증 게이트는 절대 생략하지 않는다.

5. **Antigravity 교차 검증**
   Claude Code 설정만으로 판단하지 말고, `agy` CLI 및 `GEMINI.md`/`.agents/`를 독립적으로 교차 검증한다.

## 실행 절차

### Phase 0: 트리거 확인
- 사용자가 `/cost-engineering` 또는 명시적 요청("토큰 최적화", "Antigravity로 검증해", "스킬 세팅해")을 했는가?
- 아니면 즉시 종료하고 "필요하면 `/cost-engineering` 호출하세요"라고만 답한다.

### Phase 1: 진단 (읽기 전용)

#### 1-1. Claude Code 측
```text
./CLAUDE.md
./.claude/settings.json
./.claude/settings.local.json
./.claude/memory/MEMORY.md
./.claude/rules/*.md
./.claude/hooks/*
./.claude/skills/*/SKILL.md
./.claude/agents/*.md
~/.claude/CLAUDE.md
~/.claude/settings.json
~/.claude/skills/
```

#### 1-2. Antigravity 측 (존재 시)
```text
./GEMINI.md
./AGENTS.md
./.agents/skills/*/SKILL.md
./.agents/rules/*.md
```

진단표(D1~D9):

| # | 영역 | 점검 내용 | 절감/검증 원리 |
|---|---|---|---|
| D1 | CLAUDE.md 크기 & `@include` | 줄 수 / `@파일` 강제 주입 여부 | 매 턴 전량 로드 제거 |
| D2 | 상단 인덱스 & `STABLE_PREFIX` | 인덱스·정적 프리픽스 유무 | 탐색 토큰 제거 + 캐시 히트↑ |
| D3 | 중복·장황 구간 | `rules/*.md` 중복, 주석 장문 | 압축 가능 줄 수 |
| D4 | MEMORY.md | 줄 수 | 세션 시작 로드 최소화 |
| D5 | settings allow/deny | allow 항목 수, deny 보존 | 퍼미션 라운드트립 감소 |
| D6 | rules paths | 광범위 와일드카드(`**/*cost*/**` 등) | 무관한 규칙 로드 차단 |
| D7 | hooks 효율 | 파일타입 가드 유무 | 비대상 파일 스킵 |
| D8 | 불필요 스킬/MCP | 전역/로컬 미사용 항목 | 목록·스키마 토큰 제거 |
| D9 | Antigravity 교차 상태 | `GEMINI.md`/`.agents/`/`agy` 일치 여부 | Claude ↔ Antigravity 일관성 |

### Phase 2: Antigravity 교차 검증 (가능한 경우)

`agy`가 PATH에 있는 경우 아래 읽기 전용 명령으로 독립 검증한다:

```bash
agy --version 2>/dev/null || echo "agy not found"
agy mcp list 2>/dev/null || true
agy plugin list 2>/dev/null || true
```

검증 포인트:
- `.claude/skills/`와 `.agents/skills/`의 필수 4스킬(`grill-me`, `ponytail`, `caveman`, `handoff`) 존재 및 원칙 일치 여부
- `GEMINI.md` ↔ `CLAUDE.md` 중복/충돌 여부 (`AGENTS.md` 불필요 중복 없음 확인)
- 프로젝트와 무관하게 활성화된 전역 플러그인/MCP 존재 여부
- `agy` 미설치 시 "Antigravity 미설치 또는 PATH 없음"으로 기록하고 Phase 3으로 진행.

### Phase 3: 제안서 출력

진단표(D1~D9), 실행 항목 체크리스트, 변경하지 않는 항목(보안 `deny`, 핵심 아키텍처·검증 게이트)을 한 번에 출력하고 승인을 요청한다:
`[전체 일괄 실행(All) / 항목 번호 선택 / 수정 요청 / Antigravity만 재검증]`

### Phase 4: 승인 후 실행

1. **백업**: 수정 전 대상 파일 원본을 타임스탬프 백업 폴더에 보존한다.
2. **CLAUDE.md / GEMINI.md 패치**:
   - 최상단 `STABLE_PREFIX` + `[ACTIVE_RULES_INDEX]` 유지
   - `[COST_ENGINEERING_RULES]` (과다 compact 금지, Ponytail+Caveman, grill-me 선행, Handoff 임계 시만, goal DENY, Antigravity 교차 검증, 품질 게이트 무손실) 주입
3. **스킬/규칙/설정 동기화**:
   - 필수 4대 스킬 양쪽 존재·정합성 확보
   - 불필요 스킬은 삭제하지 않고 온디맨드/OFF 처리
   - `.claude/rules/*.md` 경로를 실제 디렉토리로 축소
   - `.claude/settings.json` 안전 읽기/검증 명령 `allow` 확장 (`deny`/`ask` 보존)
4. **검증 보고**: Before/After 줄 수·절감량 표, JSON 유효성 검증, Antigravity 교차 검증 요약 출력.

## 제약 사항
- 사용자 승인 없이 파일 덮어쓰기·패키지 설치·전역 설정 변경 금지
- 보안 `deny` 규칙 및 테스트/린트/타입체크 검증 게이트 제거 금지
- 스킬 임의 삭제 금지 (OFF/온디맨드만)
- 매 턴 자동 압축 금지
- 커밋은 사용자에게 맡김
