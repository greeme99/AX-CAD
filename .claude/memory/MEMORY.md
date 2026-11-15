# MEMORY.md — AX-CAD 지속 메모리

확정 결정과 함정만 둔다. 견적 규칙은 `rules/quote-engine.md`, 기하 원칙은 `rules/cad-geometry.md`, 현재 상태는 `docs/HANDOFF.md`.

## ADR (핵심만)
- **01~05**: OCCT(B-Rep·STEP AP242), ezdxf(2D), 웹UI(shadcn), 추적성(`quote_trace`), ERP 격리.
- **06 OCP**: `cadquery-ocp-novtk==8.0.1.1.0` 고정. 파라미터 JSON 원천, BREP sha256 캐시. manylinux_2_28, LGPL 고지.
- **07 견적 불변**: `calculated_*` DB 트리거 고정, 조정은 `override_*`만. 견적은 항상 DRAFT.
- **08 ERP**: 파일(Excel) 수동 업로드 방식(HTTP 연동 폐지).
- **09 FreeCAD**: 알고리즘 이식만(코드 복사·엔진 내장 안 함).
- **10 견적 유형**: 제조 + 공사(`CONSTRUCTION`). 공사 이윤은 재료비 제외.
- **11 PC별 설치**: 서버 없음, `AXCAD_LOCAL_USER`+`TOKEN`, 로그인·결재 없음, Ed25519 서명 패치.
- **12 D′ 하이브리드**: OCCT 직접 + FreeCAD 클린룸 재구현, numpy 솔버. P1~P5 완료.
- **13 R2.0**: 조립(인스턴스 200개), 판금(K-factor·BA), 파라미터 블록(BLOCK description JSON), Copilot(Gemini→K-EXAONE, 기본 꺼짐).

## 주요 함정 (요약)
- OCP: `_s` 접미사 불일치, `GCPnt` 없음→`BRepGProp.LinearProperties_s`, Boolean에 `HasErrors()` 없음, IGES `ReadFile`만(ReadStream 실패), 빈 STEP은 `NbRootsForTransfer()==0`.
- ezdxf: 대칭 블록 속성→직접 배치, `Matrix44.inverse()` 제자리 변경(None 반환), MULTILEADER는 `build(insert=Vec2)`만, `EZDXF` 치수 스타일은 m 기준.
- Next.js: `middlewareClientMaxBodySize`는 `experimental` 아래, standalone 빌드 시 pnpm 심볼릭→`--config.node-linker=hoisted`.
- PS 스크립트: `Set-Content`는 `-Path`·`-Value` 명시, UTF-8 BOM, Windows PS 5.1 호환.
- 조립 솔버: 면 마주봄 잔차 `na×nb`(양쪽), 회전 변수 스케일, 같은 방향 평면은 뒤집기.
- 판금 전개: BA = θ·(R+K·T), 플랜지 L은 직선부, 소재는 전개 BBox.
- 패치: 작업 폴더 `dist\stage` 고정, UPDATE는 런타임 프로브 비교, 설치본의 patcher.py 사용.
- 파라미터 블록: BLOCK description에 JSON(개행→DXF 그룹 주입 BUG-58), 내보내기 값은 검증·정규화 후.
- 렌더 데이터: 블록 확장 조각은 부모 핸들 공유→편집 정의는 첫 조각만, `RENDER_FORMAT` 증가.

## 확정 정책
- 결재 없음, 견적은 항상 수정·출력 가능.
- 검증 오류 422 `REQUEST_INVALID` + `details[{field,type}]`.
- PR은 CI 녹색 후 머지. 실도면은 클라우드로 옮기지 않음.
- 릴리스: `Windows release` Actions 탭에서 실행(클라우드 세션 403).
