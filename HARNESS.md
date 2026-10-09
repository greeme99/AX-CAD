# HARNESS.md — AX-CAD Harness Engineering Specification

## 1. 개요 및 계층 구조
AX-CAD 프로젝트의 모든 작업은 Information, Execution, Feedback, Memory의 4계층으로 분리하여 관리한다.
Memory 계층은 [Hermes Agent](https://github.com/nousresearch/hermes-agent)의 폐쇄형 학습 루프(closed learning loop) 원칙을 적용하여, 에이전트가 세션을 넘어 도면 규격, 형상 알고리즘, 한국표준 견적 산출 규칙을 큐레이션된 메모리로 축적하고 자체 성장한다.

| 계층 | 구성 요소 | 역할 |
|---|---|---|
| **Information** | `CLAUDE.md`, `DESIGN.md`, `Research/`, `.claude/rules/` | 지속 지침, CAD 아키텍처, 디자인 토큰, 견적 산출 규칙 |
| **Execution** | `.claude/skills/`, `.claude/agents/`, MCP, 스크립트 | CAD 기하 연산, 파일 파서, 견적 엔진, 빌드/검증 도구 점진 노출 |
| **Feedback** | Sub-agents, pytest, 훅(`PreToolUse`/`PostToolUse`), 리뷰 루프 | 기하 무결성 검증, 회귀 방지, 규격 일치성, 정적 분석 |
| **Memory** | `.claude/memory/MEMORY.md`, `.claude/skills/.usage.json` | 세션을 넘어 지속되는 도면/견적/아키텍처 사실 축적 및 정리 |

---

## 2. 6단계 운영 모델 (Operating Model)

### 1단계: Scope (범위 확정)
- 변경 대상(2D DXF, 3D OCCT, 견적 엔진, UI 뷰어), 제외 범위, 성공 기준을 먼저 확정한다.
- 요구가 모호할 경우 임의로 코딩하지 않고 `/grill-me` 스킬을 사용하여 구체적인 스펙을 명확히 한다.

### 2단계: Context (맥락 정렬)
- 프로젝트 핵심 사실은 `CLAUDE.md`에서 참조한다.
- UI/디자인 작업 시 반드시 `DESIGN.md`를 먼저 읽는다.
- 보안, 기하 연산, 견적 산출 관련 세부 규칙은 `.claude/rules/*.md`를 읽는다.
- 세션 시작 시 `.claude/memory/MEMORY.md`를 읽어 기 확정된 아키텍처 및 도메인 결정을 재확인하지 않는다.

### 3단계: Feedback Loop (점진적 루프)
- 역할 분리: `explorer` (조사) → `planner` (계획) → `implementer` (구현) → `reviewer` (검토) → `security-reviewer` (보안).
- CAD 형상 오류(자체교차, 열린루프) 및 견적 오차 발생 시:
  1. 원인 축소 (최소 재현 샘플 DXF/STEP 도출)
  2. 최소 단위 수정 (외과적 수정)
  3. `pytest` 재검증 루프 반복

### 4단계: Guardrails (안전 가드레일)
- 위험 명령(파괴적 명령, 강제 푸시, DB 강제 삭제 등)은 `.claude/hooks/guard-dangerous-command.sh` (Mac/Linux) 및 `.ps1` (Windows)로 사전 차단.
- 코드 수정 후 `.claude/hooks/post-edit-check.sh`를 통해 문법 오류 및 포맷 자동 검사.
- 업로드된 도면 파일(DXF/STEP)의 경로 탈출 및 버퍼 오버플로우 검증.

### 5단계: Observability (관찰 가능성 및 보고)
- 최종 응답 시 변경 파일 목록, 실행한 검증 결과(테스트 통과 여부), 남은 기술적 위험을 명시하여 보고한다.
- 반복적인 시행착오나 새로운 도면 처리 패턴은 `.claude/memory/`로 승격한다.

### 6단계: Memory & Growth (폐쇄형 학습 루프)
- 작업 종료 시 `grow` 스킬 절차에 따라 "새로 습득한 CAD 커널 특성이나 견적 산출 규칙이 있는가"를 점검한다.
- 신규 사실은 승인 우선 정책에 따라 `.claude/memory/pending/` 또는 `.claude/skills/pending/`에 초안으로 남기고 검토 후 반영한다.
- `curator` 스킬을 통해 미사용 스킬을 아카이빙하고 유지 관리한다.
