# pending/

`skill_create`/`skill_patch` 등으로 제안된 스킬 변경 초안이 승인을 기다리는 곳이다.
형식: `<slug>.md` 파일에 `action(create|patch|edit|delete)`, 대상 스킬명, 변경 diff 또는 전체 내용, 사유를 적는다.
승인되면 실제 `.claude\skills\<name>\SKILL.md`에 반영 후 이 파일을 삭제한다. 비어 있는 것이 정상 상태다.
