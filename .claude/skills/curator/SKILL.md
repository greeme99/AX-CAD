---
name: curator
description: 각 프로젝트의 스킬 사용 이력(.usage.json)을 스캔해 오래 쓰지 않은 agent-created 스킬을 stale/archived로 정리하고 리포트를 남긴다.
---
# Curator — 스킬 정리

[Hermes Agent](https://github.com/nousresearch/hermes-agent)의 curator를 참고한 정리 절차다. 대상은 `grow` 스킬이 만든 **agent-created 스킬**뿐이다 — 템플릿이 제공한 스킬(`project-workflow` 등)과 사람이 직접 만든 스킬, `pinned: true`인 스킬은 건드리지 않는다.

## 실행 시점
- Cowork 예약 작업으로 주 1회 자동 실행 (기본).
- 필요하면 사용자가 직접 이 스킬을 호출해 즉시 실행할 수 있다.

## 절차

1. **대상 수집** — `Documents\Claude\Projects\*\.claude\skills\.usage.json`을 모두 읽는다. 각 항목 중 `created_by: agent`이고 `pinned`가 아닌 것만 후보로 삼는다.
2. **결정론적 전이 (항상 실행, 판단 불필요)**
   - `last_used_at` 기준 30일 이상 미사용 → `state: stale`
   - 90일 이상 미사용 → 해당 프로젝트의 `.claude\skills\.archive\<name>\`로 폴더째 이동하고 `state: archived`, `archived_at` 기록. **삭제가 아니라 이동**이므로 되돌릴 수 있어 승인 없이 자동 수행한다.
   - `.usage.json`을 갱신한다.
3. **통합/재작성 제안 (선택, 판단 필요)** — 이름/설명이 겹치는 agent-created 스킬이 여럿 보이면 통합안을 만들 수 있다. 이 경우는 내용을 실제로 고치는 것이므로 **자동 반영하지 않고** `.claude\skills\pending\`에 초안(무엇을 어떻게 합칠지)만 남기고 승인을 요청한다. 판단이 서지 않으면 이 단계는 건너뛴다 — 항상 해야 하는 단계가 아니다.
4. **리포트 작성** — `Documents\Claude\logs\curator\<YYYYMMDD-HHMM>\REPORT.md`에 다음을 적는다: 스캔한 프로젝트 수, stale 전이 목록, archived 전이 목록, 통합 제안(있다면) 목록, 스킵한 이유(대상 없음 등).
5. **통보** — 실행이 대화형 세션 중이면 리포트를 요약해 보여준다. 예약 작업(비대화형)으로 실행됐다면 다음 세션 시작 시 `grow` 절차가 pending과 최근 리포트를 확인해 사용자에게 요약한다.

## 세부 정책
`references/policy.md`의 기본값(30일/90일, consolidate 기본 off)을 따른다. 필요하면 이 파일을 프로젝트별로 조정할 수 있다.

## 복구
잘못 옮겨졌다면 `.claude\skills\.archive\<name>\`를 원래 `.claude\skills\<name>\`로 되돌리고 `.usage.json`의 `state`를 `active`로, `archived_at`을 `null`로 되돌린다.
