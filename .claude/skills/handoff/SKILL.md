---
name: handoff
description: >
  마일스톤 완료, 동일 오류 3회 반복, 또는 실제 컨텍스트 70~80% 도달 시
  핵심 상태만 docs/TASKS.md와 docs/HANDOFF.md에 기록해 다음 세션으로 인계한다.
  매 턴이나 세션 초반에 자동 실행하거나 과다 /compact를 유도하지 않는다.
  트리거: /handoff, 세션 정리, 인계, 다음 세션 준비.
---

# Session Handoff Protocol

## 실행 조건 (과다 압축 금지)
- **실행 시점**: (1) 마일스톤/작업 단위 완료, (2) 실제 컨텍스트 **70~80% 도달**, (3) 동일 오류 3회 반복, (4) 사용자 명시 요청.
- **금지**: 매 턴 또는 세션 초반에 자동 `/compact`나 조기 인계를 강제하지 않는다.

## 절차
1. **Sync State**: 완료 항목·미완료 항목·다음 우선순위를 `docs/TASKS.md`에 갱신.
2. **Context Summary**: 결정 사항, 변경 파일, 미해결 리스크, 검증 상태를 최소 분량으로 `docs/HANDOFF.md`에 기록.
3. **Session Transition**: 기록 완료 후 사용자에게 **새 세션 시작** 또는 **`/compact`** 선택을 제안한다 (임의 자동 압축 금지).
