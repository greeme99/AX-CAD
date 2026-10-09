---
paths:
  - "**/*.{ts,tsx,js,jsx,py,go,rs,java,cs}"
---
# Security Rules (AX-CAD)

- **CAD 파일 업로드 및 파싱 보안**:
  - DXF, STEP 파일 업로드 시 파일 헤더(Magic bytes) 및 확장자 화이트리스트를 검증한다.
  - 외부 참조(XREF) 또는 순환 참조를 통한 경로 순회(Path Traversal) 공격을 엄격히 차단한다.
  - 대용량 도면 또는 고의적 재귀 블록(Decompression Bomb) 파싱 시 메모리 상한 및 타임아웃(Timeout)을 설정한다.
- **API 및 기업 연동 보안**:
  - ERP/MES/PLM API 연동 엔드포인트는 인증 토큰(JWT / OAuth2 / API Key)을 신뢰 경계에서 분리 검증한다.
  - SQL 쿼리, ORM 호출 및 쉘 명령 실행 시 파라미터 바인딩을 강제하여 주입 공격을 차단한다.
- **시크릿 관리**:
  - DB 접속 정보, ERP 연동 시크릿은 `.env`로 관리하며 Git에 커밋하거나 로그/클라이언트에 노출하지 않는다.
- **보안 검토 절차**:
  - 파일 파서 변경이나 외부 API 연동 변경은 `security-reviewer` 검토를 거친다.
