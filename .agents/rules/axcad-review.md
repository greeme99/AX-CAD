---
trigger: model_decision
description: Apply when reviewing Claude Code diffs/PRs, writing AX-CAD technical specs, or designing test scenarios.
---
# AX-CAD Review & Spec Protocol
- 리뷰 우선순위: 정확성(기하/견적 오차) → 보안(`.claude/rules/security.md`) → 회귀/경계 조건 → `DESIGN.md` 일관성.
- 출력 형식: `[P0/P1/P2] 파일:라인 — 문제 / 수정안` 표 중심.
- 대용량 문서(`plan.md`, `docs/HANDOFF.md`)는 필요한 줄 범위만 `read_file`로 지연 로드.
