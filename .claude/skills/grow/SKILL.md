---
name: grow
description: 작업을 마칠 때 기록할 가치가 있는 사실이나 절차가 있는지 점검해 메모리·스킬 초안을 pending에 남기고 승인을 요청한다. 폐쇄형 학습루프의 핵심 절차.
---
# Grow — 폐쇄형 학습루프

[Hermes Agent](https://github.com/nousresearch/hermes-agent)의 "성장하는 에이전트" 개념을 이 Harness에 맞게 적용한다.
자동으로 파일을 즉시 고치지 않는다 — 이 스킬의 산출물은 항상 **pending 초안 + 승인 요청**이다.

## 언제 실행하는가
- 비단순 작업(Default Workflow 1~7단계)을 끝낸 직후.
- 사용자가 접근 방식을 정정했을 때.
- 오류·막다른 길을 만났다가 해결 경로를 찾았을 때.
- 5회 이상 도구 호출이 들어간 절차를 성공적으로 마쳤을 때.
- 같은 유형의 실수나 질문이 반복된다고 느껴질 때.

해당하지 않으면(단순 조회, 1~2단계짜리 작업) 아무것도 하지 않는다 — 매 턴 실행하는 절차가 아니다.

## 절차

1. **메모리 후보 판단** — `references/format-guide.md`의 "저장할 것 / 넘어갈 것" 기준으로 이번 작업에서 남길 사실이 있는지 확인한다.
   - 사용자 개인 선호·소통 스타일 → `target: user`
   - 환경 사실, 프로젝트 컨벤션, 얻은 교훈, 완료한 작업 기록 → `target: memory`
   - 프로젝트 고유 사실이면 해당 프로젝트의 `.claude\memory\pending\`, 워크스페이스 공통 사실이면 전역 `memory\pending\`에 초안 파일을 만든다 (형식은 `pending\README.md` 참고).
2. **스킬 후보 판단** — 이번에 사용한 절차가 재사용 가치가 있는지 확인한다 (기준은 "When the Agent Creates Skills" 참고).
   - 기존 스킬을 다듬은 것이면 `action: patch`, 새 절차면 `action: create`로 `.claude\skills\pending\`에 SKILL.md 초안을 작성한다.
   - `references/format-guide.md`의 SKILL.md 형식(frontmatter, When to Use/Procedure/Pitfalls/Verification)을 따른다.
3. **승인 요청** — pending에 쓴 초안을 한두 문장으로 요약해 사용자에게 반영해도 될지 묻는다. 여러 건이면 한 번에 모아 묻는다.
4. **반영 또는 폐기**
   - 승인 → 대상 파일(`MEMORY.md`/`USER.md`/`SKILL.md`)에 반영한다. 용량 한도(전역 memory 약 2,000자, user 약 1,200자, 프로젝트 memory 약 2,000자)를 넘으면 겹치는 항목을 통합하거나 오래된 항목을 제거한 뒤 추가한다. 반영 후 pending 초안 파일을 삭제한다.
   - 거절 → pending 초안 파일만 삭제하고 넘어간다.
5. **사용 이력 갱신** — 이번 작업에서 사용하거나 수정한 스킬이 있으면 해당 `.claude\skills\.usage.json`에 항목을 추가/갱신한다 (`use_count`, `last_used_at`, 수정했다면 `patch_count`, `last_patched_at`). 형식은 `references/format-guide.md` 참고.

## 세션 시작 시 확인
새 세션을 시작할 때 `memory\pending\`과 현재 프로젝트 `.claude\skills\pending\`에 남아 있는 파일이 있으면(예: 예약된 curator 실행 중 생성됨) 먼저 사용자에게 요약해 승인 여부를 묻는다.
