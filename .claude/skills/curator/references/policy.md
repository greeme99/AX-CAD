# Curator 기본 정책

| 항목 | 기본값 | 의미 |
|---|---|---|
| stale_after_days | 30 | 이 기간 미사용 시 stale 표시 |
| archive_after_days | 90 | 이 기간 미사용 시 `.claude\skills\.archive\`로 이동 |
| consolidate | off | 스킬 통합 제안은 기본적으로 만들지 않음. 명시적으로 요청받았을 때만 3단계 수행 |
| prune_scope | agent-created만 | `created_by: agent`이고 `pinned: false`인 스킬만 대상 |

프로젝트마다 다른 기준이 필요하면 해당 프로젝트 CLAUDE.md에 별도로 적고, curator 실행 시 이 문서 대신 그 값을 따른다.
