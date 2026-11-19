# `ponytail:` 기술부채 전수 감사 보고서

> 작성일: 2026-11-20 | 감사자: Antigravity | 기준 커밋: `origin/main` `d32f572` (PR #82)
> 총 **60건** (`core/`·`backend/` 31건 + `frontend/src/` 29건) 전수 스캔·분류

---

## 1. 분류 요약

| 등급 | Backend / Core | Frontend | 합계 | 처리 권고 |
|---|---:|---:|---:|---|
| **`DEFECT_RISK`** (실결함·보안·업그레이드 파손 위험) | 4 | 4 | **8** | 우선 해소 또는 서버 전환·메이저 업그레이드 전 필수 점검 |
| **`REFACTOR`** (성능·UX 확장 시 리팩터 대상) | 4 | 15 | **19** | 대용량 도면·장시간 세션 이슈 발생 시 순차 개선 |
| **`KEEP`** (의도적 단순화·설계 범위 내 수용) | 23 | 10 | **33** | 현행 유지 |
| **합계** | **31** | **29** | **60** | — |

---

## 2. 최우선 주의 항목 (`DEFECT_RISK` 8건)

| # | 파일:라인 | 현재 단순화 내용 | 리스크 / 권고 조치 |
|---|---|---|---|
| 1 | `backend/api/routes_admin.py:38` | 계정 잠금 외 IP별 로그인 속도 제한 없음 | 인트라넷 외부 노출 시 패스워드 스프레이 취약 → 서버 재도입 시 리버스 프록시/미들웨어 IP 제한 필수 |
| 2 | `backend/api/routes_model.py:1845` | STEP/IGES 빌드(≤120초) 동안 문서 행 잠금 + DB 커넥션 점유 | 다중 사용자 동시 가져오기 시 DB 풀 고갈 → 서버 모드 전환 시 메모리 잠금 + 커밋 후 커널 호출 |
| 3 | `core/dxf/dwg_builtin.py:13` | `ezdwg==0.20.0` 비공개 헬퍼(`ezdwg.convert._*`) 직접 의존 | `ezdwg` 마이너 업그레이드 시 DWG 가져오기 침묵 파손 가능 → 버전 고정 유지 + 업그레이드 시 `test_dwg_builtin.py` 필수 실행 |
| 4 | `core/geometry/worker.py:73` | `ProcessPoolExecutor._processes` 비공개 속성으로 타임아웃 워커 강제 종료 | Python 3.13/3.14 업그레이드 시 속성명 변경되면 행 걸린 OCCT 워커 미종료 → 업그레이드 회귀 테스트 추가 |
| 5 | `frontend/src/lib/api.ts:2` | 토큰을 `sessionStorage`/`localStorage`에 보관 | XSS 발생 시 토큰 탈취 가능 (현재 CSP로 완화 중) → 외부 노출 시 BFF `httpOnly` 쿠키 전환 |
| 6 | `frontend/src/app/model/[documentId]/page.tsx:1357` | `meshes` 캐시 무제한 누적 (eviction 없음) | 장시간 3D 모델링 세션에서 브라우저 메모리 증가 → 최근 N개(예: 50개) LRU 정리 추가 권장 |
| 7 | `frontend/src/lib/cad/features.ts:405` | 모듈 전역 단일 변수로 현재 워크벤치 상태 공유 | 동시 마운트/StrictMode/다중 탭 확장 시 상태 오염 위험 → 인자 전달 또는 Context로 전환 |
| 8 | `frontend/src/lib/cad/edits.ts:87` | 키 순서에 민감한 JSON 문자열 비교 | 외부 경로·키 순서가 다른 객체 유입 시 Undo 비교 오탐 → 정규화 비교 함수 도입 권장 |

---

## 3. 중기 개선 대상 (`REFACTOR` 19건)

### Backend / Core (4건)
- `backend/api/main.py:99`: 리비전별 락 딕셔너리 프로세스 수명 유지 → `WeakValueDictionary`/LRU 전환
- `backend/api/main.py:468`: 리비전 목록에서 렌더 JSON 매번 읽기 → DB 엔티티 수 컬럼 캐시
- `backend/api/routes_model.py:140`: 피처 작업마다 문서 전체 피처 스캔 → 피처 수 증가 시 `consumed_by` FK 검토
- `backend/api/routes_quote.py:95`: 견적/QTO 캐시 파일 미정리 → TTL/용량 기반 주기적 정리

### Frontend (15건)
- `frontend/src/app/globals.css:78`: 시스템 폰트 폴백 (오프라인 Pretendard/JetBrains Mono 셀프호스팅 검토)
- `frontend/src/app/page.tsx:12`: 최근 문서 기준이 `created_at` → 서버 측 `last_opened_at` 추적
- `frontend/src/app/documents/[documentId]/page.tsx:45`: 업로드 진행률 바 없음 (`fetch` → XHR)
- `frontend/src/app/viewer/[revisionId]/page.tsx:80`: 저장 알림을 모듈 변수로 전달 → 쿼리/토스트 전환
- `frontend/src/components/cad/ToolPalette.tsx:108`: 그룹 접힘 상태 `localStorage` 미저장
- `frontend/src/components/shell/AppShell.tsx:15`: 고정 224px 사이드바 (64px 접이식 레일 미지원)
- `frontend/src/lib/cad/features.ts:235`: `holes_std.py` 피치 표 수동 미러링 (API 단일화 가능)
- `frontend/src/lib/cad/geom.ts:79, 333`: 고정 톨러런스 원 평탄화, `0.6*height` 근사 문자 폭
- `frontend/src/lib/cad/history.ts:7`: 명령 diff 대신 전체 스냅샷 저장 (초대형 도면 메모리 주의)
- `frontend/src/lib/cad/lisp.ts:1247, 1988`: 팽창 호 현 길이 근사, 평균 글리프 폭 근사
- `frontend/src/lib/cad/snap.ts:22, 50, 111`: 16셀 쿼리 반경 상한, 호 세그먼트 MID 미지원, 읽기 전용 엔티티 끝점만 스냅

---

## 4. 현행 유지 (`KEEP` 33건)
- **Backend / Core (23건)**: `main.py:77,78`, `routes_bom.py:144`, `routes_master.py:149`, `routes_model.py:2192`, `routes_quote.py:349,754`, `0001_initial.py:121`, `0003_revolve_boolean.py:19`, `core/bom/dxf.py:120,173`, `core/dxf/reader.py:749,1017`, `core/dxf/writer.py:90,849`, `core/geometry/features.py:86`, `core/geometry/sheetmetal.py:886,1152`, `core/geometry/sketch.py:34`, `core/geometry/surface_edit.py:96`, `core/geometry/worker.py:48`, `core/quote_engine/cost.py:86`, `core/quote_engine/qto.py:282`.
- **Frontend (10건)**: `viewer/[revisionId]/page.tsx:120`, `CommandPrompt.tsx:8`, `ThreeViewport.tsx:351`, `axobj.ts:296`, `edit.ts:144`, `geom.ts:136,158`, `lisp.ts:2821,3069`, `objmodel.ts:522`.
