# DECISIONS — 협업 및 아키텍처 결정 기록

> **참고**: AX-CAD 제품·기술 아키텍처 핵심 결정(ADR-01~13)의 정본은 [`.claude/memory/MEMORY.md`](../.claude/memory/MEMORY.md)에 유지한다.
> 이 문서는 **Claude Code ↔ Antigravity 협업 운영 결정, 워크플로 변경, 회고 교훈**을 누적 기록한다.

---

## COLLAB-01: Claude Code + Antigravity 이중 에이전트 협업 체계 (2026-11-19)

- **배경**: 로컬 Windows PC(`D:\Workspace\AX-CAD`)에서 메인 개발 에이전트인 Claude Code와 탐색·설계·문서·리뷰에 강한 Google Antigravity를 함께 운영할 때, 파일 덮어쓰기·포트 충돌·컨텍스트 단절을 방지할 필요가 있음.
- **결정**:
  1. **역할 분리**: Claude Code = 메인 구현·리팩터링·단위 테스트·PR 생성 / Antigravity = 탐색·명세·리뷰·문서화·독립 검증.
  2. **물리 격리**: 수정 작업은 각자 Git worktree(Claude `--worktree` / Antigravity `New Worktree Mode`)에서 수행하고 `main` 직접 push 금지.
  3. **공유 허브**: 계약(`docs/AGENT_CONTRACT.md`), 작업 대기열(`docs/TASKS.md`), 인계(`docs/HANDOFF.md`), 프로젝트 메모리(`.claude/memory/MEMORY.md`)를 단일 진실 원천으로 공유.
  4. **규칙 동기화**: Claude Code는 `CLAUDE.md`, Antigravity는 루트 `GEMINI.md`를 자동 로드하며 양쪽 모두 `docs/AGENT_CONTRACT.md`를 참조.
  5. **포트 분리**: 단독(`8000/3000`), Claude 병렬(`8100/3100`), Antigravity 병렬(`8200/3200`) + `tools/agent-dev.ps1`로 고정 포트 실행.
- **트레이드오프**: worktree마다 의존성/캐시 디스크 사용이 늘 수 있으나, 동시 편집 충돌 및 롤백 비용을 원천 차단하는 이점이 더 큼. 초기 병렬 에이전트 수는 2개로 제한한다.

---

## CAM-CAE-01: `plan.md` §24 CAM·CAE 범위 및 솔버 스택 확정 (2026-11-20)

- **결정** (`/grill-me` 6개 항목 모두 A안 채택):
  1. `DEC-CAM-01`: C1(공구·2.5축 밀링) → C2(3D 인식·시뮬) → C3(후처리·견적) → C4(레이저) 순차 구현.
  2. `DEC-CAM-02`: Fanuc 일반형 밀링 + 일반 레이저(`M3/M5`) 2종 기본 제공.
  3. `DEC-CAM-03`: 기존 G1 엑셀에 `17_공구절삭조건` 시트 추가 + `ACTIVE` 버전 동결.
  4. `DEC-CAM-04`: CAM 경로 존재 시 실경로 가공시간을 견적에 선택 연동(`quote_trace.source_type = 'CAM_TOOLPATH'`), 미존재 시 기존 추정치 유지.
  5. `DEC-CAE-01`: `scipy` (BSD-3, ~35MB, R3.0 `UPGRADE` 1회) 희소 솔버 + 자체 Tet4 선형 정적 솔버 내장 (GPL 감염 차단).
  6. `DEC-CAE-02`: 단품 선형 정적 FEA(변위·von Mises·안전율)만 구현, CFD·비선형·접촉·열·모달 제외.
- **상세 명세**: `docs/specs/cam-cae-spec.md` (`TASK-010`, `TASK-011`).
