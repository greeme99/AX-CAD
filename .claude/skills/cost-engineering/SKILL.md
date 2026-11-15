---
description: "프로젝트의 Claude Code 토큰 사용량을 동일 품질 조건 하에서 최소화한다. CLAUDE.md 압축, 퍼미션 프롬프트 제거, 훅 최적화, 규칙 경로 축소, 메모리 압축을 일괄 진단·실행한다."
trigger: "/cost-engineering, 토큰 최적화, 비용 최적화, cost engineering"
---

# Cost Engineering for Claude Code

프로젝트의 Claude Code 설정을 진단하고 토큰 비용을 최소화한다.
품질·보안·추적성은 유지하되, 매 턴/세션에 불필요하게 로드되는 토큰을 제거한다.

## 실행 절차

### Phase 1: 진단 (읽기 전용)

아래 파일을 순서대로 읽고 진단표를 작성한다. 없는 파일은 건너뛴다.

```
./CLAUDE.md
./.claude/settings.json
./.claude/settings.local.json
./.claude/memory/MEMORY.md
./.claude/rules/*.md
./.claude/hooks/*.sh
./.claude/hooks/*.ps1
./.claude/skills/*/SKILL.md
./.claude/agents/*.md
```

진단 항목 (각 항목별 현재값 → 절감 가능량 추정):

| # | 영역 | 점검 내용 | 절감 원리 |
|---|---|---|---|
| D1 | CLAUDE.md 크기 | 줄 수, 토큰 추정(줄×~20tok) | 매 턴 전량 로드 → 1줄=~20tok/턴 |
| D2 | 상단 인덱스 | 스킬·규칙·에이전트·훅·설정 레지스트리 유무 | 누락 시 Claude가 탐색에 추가 토큰 소모 |
| D3 | 중복·장황 구간 | ASCII 트리, 주석 달린 명령, 장문 설명 | 압축 가능 줄 수 |
| D4 | MEMORY.md 크기 | 줄 수 | 세션 시작 시 전량 로드 |
| D5 | settings.json allow | 항목 수 | 미등록 명령 = 퍼미션 프롬프트 = 추가 라운드트립 |
| D6 | rules 경로 범위 | 각 규칙의 `paths:` 패턴 | 넓은 패턴 = 무관한 편집에도 로드 |
| D7 | hooks 효율 | 파일 타입 필터링 유무 | 무조건 실행 = 불필요 오버헤드 |
| D8 | 불필요 스킬/에이전트 | 미사용·중복 항목 | 디스크만 차지하나 목록 탐색 비용 |

### Phase 2: 제안서 출력

진단 결과를 아래 형식으로 **한 번에** 출력한다:

```
## 토큰 최적화 진단 결과

| # | 영역 | 현재 | 제안 | 추정 절감 |
|---|---|---|---|---|
| D1 | CLAUDE.md | 120줄 (~2400tok/턴) | 75줄 (~1500tok/턴) | ~900tok/턴 |
| ... | ... | ... | ... | ... |

### 실행 항목
1. [ ] CLAUDE.md 압축 (구체적 변경 목록)
2. [ ] MEMORY.md 압축
3. [ ] settings.json allow 확장
4. [ ] rules 경로 축소
5. [ ] hooks 파일타입 가드 추가
6. [ ] 상단 인덱스 추가

### 변경하지 않는 항목 (근거)
- ...
```

사용자에게 **[전체 실행 / 항목 선택 / 수정 요청]** 을 묻는다.

### Phase 3: 실행

승인된 항목만 순서대로 실행한다.

#### 3-1. CLAUDE.md 압축

적용 규칙:
- ASCII 다이어그램 → 2줄 이내 텍스트 요약
- 주석 달린 명령 블록 → 주석 제거, 명령만 유지
- 장문 테이블 → 인라인 또는 3줄 이내 압축
- 중복 설명 → 1회만 기술
- 빈 줄 연속 → 1개로
- 변경 금지: 미션, 스택, 코딩 규칙의 핵심 내용

#### 3-2. 상단 인덱스 추가

`[ACTIVE_RULES_INDEX]` 섹션을 CLAUDE.md 상단(Mission 앞)에 삽입:

```markdown
## [ACTIVE_RULES_INDEX] — 스킬·규칙·에이전트·훅·설정 레지스트리

### Skills (`.claude/skills/`, 온디맨드 호출)
| 스킬 | 트리거 | 역할 |
|---|---|---|
| **name** | trigger | 1줄 설명 |

### Rules (`.claude/rules/`, 경로 매칭 시 자동 로드)
| 규칙 | 대상 경로 | 내용 |
|---|---|---|
| **name.md** | paths | 1줄 설명 |

### Agents (`.claude/agents/`, 서브에이전트 위임)
| 에이전트 | 모델 | 도구 | 역할 |
|---|---|---|---|

### Hooks (`.claude/hooks/`)
| 훅 | 시점 | 동작 |
|---|---|---|

### Settings (`.claude/settings.json`)
- **allow**: 요약
- **deny**: 요약

### Memory
- **MEMORY.md**: 요약
```

#### 3-3. MEMORY.md 압축

적용 규칙:
- ADR → 항목당 1~2줄 (번호: 핵심 결정. 주요 제약)
- Traps/함정 → 카테고리별 1줄 (키워드: 증상→해법)
- 정책 → 불릿 1줄씩
- 변경 금지: 결정 번호, 핵심 제약사항, 정책 내용

#### 3-4. settings.json allow 확장

프로젝트의 Canonical Commands에서 사용하는 CLI를 모두 등록:
- `git`: status, diff, log, add, commit, push -u, fetch, merge, stash, branch, checkout, rebase
- 패키지 매니저: 프로젝트가 사용하는 것만 (uv/pip/poetry/pnpm/npm/yarn 등)
- 테스트·린트: pytest, ruff, mypy, eslint, prettier 등 프로젝트 명령

deny는 보안 항목만 유지:
- `.env`/시크릿 읽기
- `git push --force`
- `git reset --hard`

#### 3-5. rules 경로 축소

각 규칙 파일의 `paths:` 를 실제 해당 코드가 있는 디렉토리로 좁힌다.
- 보안 규칙: 실제 API 경계·입력 처리 경로만
- 도메인 규칙: 해당 도메인 코드 디렉토리만
- `**/*` 와일드카드 → 구체적 경로

#### 3-6. hooks 파일타입 가드

훅이 특정 파일 타입용이면 JSON 페이로드에서 `file_path`를 추출해 해당 확장자만 처리:

**Bash 템플릿:**
```bash
#!/usr/bin/env bash
set -e
raw=$(cat || true)
if [ -z "$raw" ]; then exit 0; fi
file=$(python3 -c "import sys,json; print(json.load(sys.stdin).get('tool_input',{}).get('file_path',''))" <<< "$raw" 2>/dev/null || echo "")
case "$file" in
  *.py) # Python 전용 처리
    ;;
esac
exit 0
```

**PowerShell 템플릿:**
```powershell
$ErrorActionPreference = "Stop"
$raw = $input | Out-String
if (-not $raw) { exit 0 }
try {
    $json = $raw | ConvertFrom-Json
    $file = $json.tool_input.file_path
} catch { exit 0 }
if ($file -and $file.EndsWith(".py")) {
    # Python 전용 처리
}
exit 0
```

### Phase 4: 검증 및 보고

실행 완료 후:
1. 변경된 파일 목록과 줄 수 변화 출력
2. `git diff --stat` 으로 변경량 확인
3. 커밋하지 않음 (사용자가 직접 검토 후 커밋)

보고 형식:
```
## 토큰 최적화 완료

| 파일 | Before | After | 절감 |
|---|---|---|---|
| CLAUDE.md | 120줄 | 75줄 | -45줄 (~900tok/턴) |
| MEMORY.md | 80줄 | 35줄 | -45줄 (~900tok/세션) |
| settings.json | allow 5항목 | allow 22항목 | 퍼미션 프롬프트 17건↓ |
| rules/*.md | 광범위 경로 | 축소 경로 | 불필요 로드↓ |
| hooks/*.sh | 무조건 실행 | 타입 필터 | 비대상 파일 스킵 |

### 미적용 항목
- (근거와 함께)

### PC 전역 권장사항
- ~/.claude/settings.json: 범용 git 명령만 allow
- ~/.claude/CLAUDE.md: User Preferences와 중복 시 생략
```

## 제약 사항

- 보안 deny 규칙은 절대 제거하지 않는다
- 코딩 규칙의 핵심 내용(미션, 아키텍처 결정, 검증 기준)은 의미를 보존한다
- 사용자 승인 없이 파일을 수정하지 않는다
- 스킬·에이전트를 임의로 삭제하지 않는다 (비활성화만 제안)
- 커밋은 사용자에게 맡긴다
