# AUD-<YYYYMMDD>-<NNN>

## 작업 메타데이터
- 요청자: ANTIGRAVITY-PM
- 요청일: <YYYY-MM-DD>
- 상태: pending | in-review | changes-requested | approved | blocked
- 대상 브랜치: feat/antigravity-<task-id>
- 대상 PR 또는 diff 범위: `origin/main...HEAD`
- 관련 TASK ID: TASK-XXX
- Audit 유형: code-review | security-audit | test-validation | release-readiness

## 변경 요약
- 목적:
- 주요 변경:
- 영향 파일:
- 의도하지 않은 변경: 없음

## 검증 요청
- 반드시 확인할 항목:
  1.
- 반드시 실행할 테스트:
  - `uv run ruff check . && uv run ruff format --check .`
  - `uv run mypy --platform linux core backend`
  - `uv run pytest -q tests/`
- 금지 행위:
  - 구현 브랜치 소스 코드 직접 수정 금지 (`core/**`, `backend/**`, `frontend/src/**`, `frontend/e2e/**`, `sdk/**`)
  - 의존성·마이그레이션·CI/CD·배포 설정 변경 금지
  - `main` commit/push 및 `approved` 단독 확정 금지
- 산출물 위치: `docs/audit-reports/AUD-<YYYYMMDD>-<NNN>.md`

## 완료 기준
- 심각도별 이슈 분류: `blocker` | `high` | `medium` | `low` | `informational`
- 각 이슈에 파일·라인·재현 방법·권고 조치 포함
- 테스트 결과의 실제 명령·종료 코드·핵심 로그 포함
- 권고 판정(`approve` | `approve-with-conditions` | `changes-requested` | `reject`)과 근거 명시
