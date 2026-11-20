# DECISIONS — 협업 및 아키텍처 결정 기록

> **참고**: AX-CAD 제품·기술 아키텍처 핵심 결정(ADR-01~13)의 정본은 [`.claude/memory/MEMORY.md`](../.claude/memory/MEMORY.md)에 유지한다.
> 이 문서는 **협업 거버넌스 운영 결정, 워크플로 변경, 회고 교훈**을 누적 기록한다.

---

## COLLAB-01: Claude Code + Antigravity 이중 에이전트 협업 체계 (2026-11-19, `COLLAB-02`로 대체됨)

- 초기 구성: Claude Code 메인 구현 + Antigravity 탐색·명세·리뷰·검증 파트너, Git worktree 및 고정 포트(`8000/3000`, `8100/3100`, `8200/3200`) 분리 도입.

---

## CAM-CAE-01: `plan.md` §24 CAM·CAE 범위 및 솔버 스택 확정 (2026-11-20)

- **결정** (`/grill-me` 6개 항목 모두 A안 채택):
  1. `DEC-CAM-01`: C1(공구·2.5축 밀링) → C2(3D 인식·시뮬) → C3(후처리·견적) → C4(레이저) 순차 구현.
  2. `DEC-CAM-02`: Fanuc 일반형 밀링 + 일반 레이저(`M3/M5`) 2종 기본 제공.
  3. `DEC-CAM-03`: 기존 G1 엑셀에 `17_공구절삭조건` 시트 추가 + `ACTIVE` 버전 동결.
  4. `DEC-CAM-04`: CAM 경로 존재 시 실경로 가공시간을 견적에 선택 연동(`quote_trace.source_type = 'CAM_TOOLPATH'`), 미존재 시 기존 추정치 유지.
  5. `DEC-CAE-01`: `scipy` (BSD-3, ~35MB, R3.0 `UPGRADE` 1회) 희소 솔버 + 자체 Tet4 선형 정적 솔버 내장 (GPL 감염 차단).
  6. `DEC-CAE-02`: 단품 선형 정적 FEA(변위·von Mises·안전율)만 구현, CFD·비선형·접촉·열·모달 제외.
- **상세 명세**: `docs/specs/cam-cae-spec.md` (`TASK-010`, `TASK-011`, 마이그레이션 `0039`~`0040`).

---

## COLLAB-02: Project Main 및 GitHub 연동 총괄 이관 — Claude Code → Antigravity (2026-11-20)

- **배경**: 프로젝트 관리 총괄·구현·GitHub PR/CI 운영을 Google Antigravity로 단일화하고, Claude Code는 독립 검증자(코드 검수·보안 감사·테스트 검증) 역할로 격리해 역할 충돌과 토큰 소모를 최소화한다.
- **결정 (4계층 무결성)**:
  1. **역할 전환 (`docs/RACI.md`, `docs/AGENT_CONTRACT.md` V2.0)**: `ANTIGRAVITY-PM` = Project Main · 메인 구현 · GitHub/PR/CI 총괄 / `CLAUDE-AUDITOR` = `docs/audit-requests/` 기반 독립 Audit 전용.
  2. **기술적 권한 잠금 (`.claude/settings.json`)**: `CLAUDE-AUDITOR`의 `core/**`, `backend/**`, `frontend/src/**`, `frontend/e2e/**`, `sdk/**`, `installer/**`, `deploy/**`, `.github/workflows/**`, 의존성 락파일 `Edit`/`Write`를 `deny`로 강제.
  3. **증거 파이프라인**: `docs/audit-requests/AUD-<YYYYMMDD>-<NNN>.md` → `docs/audit-reports/AUD-<동일 ID>.md` 1:1 추적, 실제 명령·종료 코드·로그 필수.
  4. **최종 승인 게이트**: `main` 머지·릴리스/배포·의존성·DB 마이그레이션·CI/CD·시크릿은 `HUMAN` 승인 필수. `CLAUDE-AUDITOR`는 `approved`를 단독 확정하지 않는다.
  5. **백로그 이관**: 기존 Claude Code 미결 작업(`TASK-002`, `007`, `008`, `010`, `011`, `012`) 전원 `ANTIGRAVITY-PM`으로 이관 완료 (`docs/TASKS.md`).

## CAM-CAE-NX-01: CAM · CAE NX(Simcenter) 수준 고도화 범위 확정 (2026-11-20)
- **결정**:
  1. **CAM (`TASK-017`, C5~C8, `0041`)**: 3축 자유곡면(황삭·등고선/스캘럽 정삭) + 2축 선반(OD/ID/홈/나사) + 3+2축 인덱싱(`G68.2`/`CYCLE800`/`PLANE SPATIAL`) + 덱셀 스톡 제거/충돌 시뮬레이션 + Siemens 840D/Heidenhain TNC/Fanuc Lathe 후처리기.
  2. **CAE (`TASK-018`, E4~E7, `0042`)**: 2차 Tet10 정밀 요소 + 고유진동수(Modal) + 선형 좌굴(Buckling) + 정상상태 열전달/열응력(Thermal-Structural).
  3. **의존성**: 기존 스택(`OCCT` + `NumPy` + `SciPy` BSD-3) 100% 유지 — 신규 외부 패키지 0, GPL 비감염 유지.
  4. **상세 명세**: `docs/specs/cam-cae-nx-spec.md` (`CAM-T06~T10`, `CAE-T06~T10`).
