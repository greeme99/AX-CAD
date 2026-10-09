---
name: grill-me
description: 구현 전에 요구사항, 범위, 제약, 성공 기준과 위험을 집중 질문으로 명확히 한다. 모호하거나 다단계인 작업에 사용한다.
disable-model-invocation: true
---
# Grill Me

사용자의 요청을 즉시 구현하지 말고 다음 순서로 명확히 한다.

1. 목표와 사용자 가치
2. 포함/제외 범위
3. 입력·출력과 핵심 시나리오
4. 기술·호환성·성능 제약
5. 실패 처리와 보안 요구
6. 검증 가능한 완료 조건

`references/question-bank.md`에서 필요한 질문만 선택한다.
이미 답이 있는 질문은 반복하지 않는다.
최종적으로 `scripts/render-spec.ps1` 형식의 간결한 구현 브리프를 만든다.
