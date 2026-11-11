---
status: pending (사용자 승인 후 MEMORY.md 반영)
date: 2026-11-11
---

# AI Copilot LLM 2단계: Gemini → K-EXAONE 2.5

MEMORY.md 항목 "13 R2.0 보완 + AI Copilot"의 "LLM은 Gemini 2.5 Flash 접점만 두고…" 문장을 아래로 교체 제안.

- 사용자 결정(2026-11-11): Copilot LLM 순서는 규칙 → K-EXAONE 2.5(`k-exaone_v2.5`, EXAONE API, OpenAI 호환) → 이 PC의 Ollama. Gemini는 제거.
- 키: `EXAONE_API_KEY`(또는 `AXCAD_EXAONE_API_KEY`) 환경변수 > 관리자 화면 저장 키. 개발은 루트 `.env`, PC 설치본은 `axcad.env`.
- 불변: 기본 꺼짐, 관리자 키 설정 시에만, 요약 문맥(화면·문서 유형·피처 개수·코드)만 전송, API 주소 고정·리다이렉트 차단.
- 근거: `plan.md` §18 변경 항목, TC-198.
