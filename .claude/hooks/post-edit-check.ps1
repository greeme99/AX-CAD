$ErrorActionPreference = "Stop"
# 기본 템플릿은 안전하게 no-op이다.
# 프로젝트에 맞는 빠른 formatter/linter 명령으로 교체한다.
# 예:
# if (Test-Path "package.json") { pnpm lint }
exit 0
