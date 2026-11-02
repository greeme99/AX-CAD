# 메모리 초안 — 패치 빌드 폴더 고정·화면 업로드 한도 (2026-11-02, 승인 대기)

반영 위치: `.claude/memory/MEMORY.md` ADR-12 아래 P5 항목 뒤에 이어 붙인다.

- 설치본과 작은 패치의 짝(BUG-53, PR #31): 패치는 설치본의 Python·PostgreSQL·Node 컴포넌트 id(파일 해시 묶음)와 같을 때만 적용된다(`patcher._admissible`, UPDATE 등급은 런타임이 바뀌는 패치를 거부). `build.ps1`의 작업 폴더가 `dist\AX-CAD-<버전>\`이면 pip가 `Scripts\*.exe`·`*.dist-info/RECORD`에 `python.exe` 절대 경로를 새겨 릴리스마다 python id가 달라진다 → 작업 폴더는 `dist\stage`로 **고정**한다. 이 이전에 만든 설치본은 패치를 받을 수 없어 재설치. Python 버전·`uv.lock`을 바꾸는 릴리스는 `UPGRADE`.
  - 함정: 거부 메시지의 "apply the full patch"는 UPDATE 등급에서는 해결책이 아니다(full patch도 런타임 변경으로 거부). CI `windows` 잡은 설치본을 복사해 패치를 만들어 이 문제를 잡지 못한다 — 서로 다른 빌드 간 id 비교 테스트는 없음(후속).
  - 진단: 설치본 `app\components.json`의 `components.python.id`와 패치 `manifest.json`의 `requires.python` 비교, 다르면 두 파일 목록의 해시 차이를 비교(이번엔 24개 = exe 14 + RECORD 10).
- Next.js 15.5 `/api` 재작성 프록시는 본문 10MB 제한(BUG-54, Open). 한도 옵션 `middlewareClientMaxBodySize`는 **`experimental` 아래**에 둔다(최상위는 조용히 무시). 수정 전까지 CI의 패치 업로드는 FastAPI(`:18000`) 직접이라 화면 경로는 미검증.
- 운영: 클라우드 세션의 GitHub 연결은 Actions 실행 권한이 없다(403) → 릴리스는 사용자가 Actions 탭에서. `Windows release` 서명 잡은 `main`에서만 돌고 `release` 환경(승인자)을 쓴다.
