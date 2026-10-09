# 메모리·스킬 형식 가이드

## 메모리에 저장할 것 (예)
- 사용자 선호: "TypeScript보다 concise한 응답 선호" → `user`
- 환경 사실: "스테이징 서버는 SSH 2222 포트 사용" → `memory`
- 정정 사항: "이 프로젝트는 pnpm만 쓴다, npm 금지" → `memory`
- 컨벤션: "커밋 메시지는 Conventional Commits 형식" → `memory`
- 완료한 작업: "2026-07-13 인증 모듈 JWT로 마이그레이션 완료" → `memory`

## 넘어갈 것
- 너무 사소하거나 모호한 정보 ("사용자가 로그인에 대해 물어봄")
- 검색하면 바로 나오는 일반 지식
- 코드/로그 원문 같은 대용량 덤프
- 이번 세션에서만 의미 있는 임시 정보 (파일 경로, 디버깅 중간값)
- 이미 CLAUDE.md/프로젝트 문서에 있는 정보

## 좋은 항목 예시
```
Bad:  사용자가 프로젝트를 가지고 있다.
Good: ~/Projects/Prod.Plan는 Node 22 + pnpm + NestJS + PostgreSQL, 테스트는 `pnpm test`.
```
한 줄에 여러 관련 사실을 압축하는 편이 항목을 여러 개로 쪼개는 것보다 낫다.

## pending 초안 파일 형식 (메모리)
```markdown
target: memory | user
action: add | replace | remove
old_text: (replace/remove일 때만, 대상을 특정하는 부분 문자열)
---
(추가/교체할 내용)
---
reason: 이유
```

## SKILL.md 새로 만들 때 형식
```markdown
---
name: skill-slug
description: 60자 내외, 언제 쓰는지가 드러나게
---
# 제목

## When to Use
트리거 조건

## Procedure
1. 단계

## Pitfalls
- 알려진 실패 패턴과 대처

## Verification
어떻게 확인하는지
```
`patch`(기존 스킬의 일부만 고치기)가 `edit`(전체 재작성)보다 검토하기 쉬우므로 우선한다.

## .usage.json 항목 형식
```json
{
  "skill-name": {
    "use_count": 1,
    "last_used_at": "2026-07-13T00:00:00Z",
    "patch_count": 0,
    "last_patched_at": null,
    "created_at": "2026-07-13T00:00:00Z",
    "state": "active",
    "pinned": false,
    "created_by": "agent"
  }
}
```
`created_by`는 `agent`(이 스킬이 grow 절차로 생성됨, curator 관리 대상) 또는 `template`/`user`(템플릿 제공·사람이 직접 작성, curator가 건드리지 않음)다. 값이 없으면 `template`로 취급한다.
