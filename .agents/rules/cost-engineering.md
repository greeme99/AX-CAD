---
trigger: model_decision
description: Apply when implementing features, refactoring, or responding with code to enforce minimal diffs, token-efficient outputs, and full verification quality.
---

# Cost Engineering Guardrails
- **Ponytail / Caveman**: 인사·수식어·반복 설명 배제, 결과 데이터·실행 명령·Unified Diff 중심 출력.
- **YAGNI**: 요청되지 않은 추상화·인터페이스·팩토리 생성 금지, 최소 수정 라인 우선.
- **GRILL-ME**: 요구사항·경계조건이 모호하면 코딩 전 핵심 질문으로 스펙 확정.
- **No Excessive Compaction**: 매 턴·조기 `/compact` 금지. 실제 컨텍스트 70~80% 도달 또는 마일스톤 완료 시만 Handoff 제안.
- **Progressive Disclosure & Context Guard**: 대용량 문서는 범위 지정(`StartLine`/`EndLine`) 읽기, 5개 이상 파일 횡단 탐색은 `research` subagent 격리.
- **Quality 100%**: 토큰 절감을 이유로 보안 검사·타입체크·린트·테스트 검증 게이트를 절대 생략하지 않는다.
