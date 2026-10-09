---
paths:
  - "core/geometry/**"
  - "core/dxf/**"
  - "**/*cad*/**"
  - "**/*geometry*/**"
---
# CAD & Geometry Engineering Rules

## 1. 2D 제도 및 DXF 파싱 원칙 (ezdxf 기반)
- **레이아웃 구조 접근 준수**: ezdxf 사용 시 반드시 `doc.modelspace()`, `doc.paperspace()`, `doc.blocks` 계층 구조를 명확히 구분하여 엔티티에 접근한다.
- **엔티티 무결성 보장**: 선(Line), 호(Arc), 원(Circle), 폴리라인(LWPolyline)의 좌표계 변환 시 OCS(Object Coordinate System)와 WCS(World Coordinate System) 간 변환 오차(Epsilon: `1e-6`)를 엄격히 검증한다.
- **스냅 성능 최적화**: 캔버스 상의 실시간 스냅(Endpoint, Midpoint, Center) 연산 시 전체 엔티티 순회를 금지하고, R-tree 또는 공간 분할 버킷(Spatial Grid)을 활용한다.

## 2. 3D 형상 모델링 원칙 (OpenCASCADE / OCCT 기반)
- **전처리 검증 의무화**: 폐곡선 스케치를 3D로 돌출(Extrude)하거나 회전(Revolve)하기 전, 와이어가 닫혀 있는지(`wire.IsClosed()`), 자가 교차(`self-intersection`)가 없는지 사전에 검사한다.
- **불리언(Boolean) 연산 안전성**: 합집합(Fuse), 차집합(Cut), 교집합(Common) 연산 시 접촉면 일치(Coincident face)나 퇴화 모서리(Degenerate edge)로 인한 커널 크래시를 방지하기 위해 형상 복구(ShapeFix) 루틴을 배치한다.
- **표준 포맷 교환**: 3D 데이터 저장은 ISO 10303 표준 STEP AP242 및 IGES 포맷을 기본으로 지원하며, OCCT XDE를 통해 조립 구조(Assembly) 및 유효성 속성을 보존한다.

## 3. UI 및 렌더링 분리 원칙
- **뷰어와 기하 엔진의 분리**: Canvas2D/WebGL/Qt 렌더링 코드에 기하 연산 로직을 포함하지 않는다. 화면 그리기 오류와 수학적 모델 오류를 엄격히 분리하여 디버깅한다.
- **도면 단위 고정**: 내부 연산 표준 단위는 `mm`로 단일화하며, 각도는 라디안/도 변환을 명시적으로 처리한다.
