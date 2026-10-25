# 함정 초안 (승인 대기) — Track 8 P1에서 겪은 것

MEMORY.md ADR-02(ezdxf) 아래에 넣을 후보.

- ezdxf `Insert.transform()`·`add_auto_attribs()`는 **대칭(음수 축척) 블록 참조**의 속성(ATTRIB) 위치를 잘못 놓는다 → 속성은 블록 좌표로 되돌린 뒤 새 `matrix44()`로 직접 배치(`writer._place`).
- ezdxf `Matrix44.inverse()`는 **제자리 변경, 반환값 None**.
- ezdxf LEADER는 `extrusion`이 아니라 `normal_vector` 속성을 쓴다(없는 속성 접근 시 `DXFAttributeError`).
- ezdxf `add_cad_spline_control_frame()`은 통과점(fit points)을 저장하지 않는다 → 편집하려면 `fit_points_to_cad_cv` + `apply_construction_tool` 후 `fit_points`를 직접 넣는다.
- ezdxf 새 문서의 `EZDXF` 치수 스타일은 m 기준(문자 높이 0.25, 값 10000) → mm 도면은 `Standard` 스타일 + 재정의.
- 블록 확장 엔티티는 조각마다 부모 핸들을 공유한다 → 편집 정의(`geom`)는 **첫 조각 하나에만** 붙이고 조각의 `type`은 바꾸지 않는다(형상 유형은 `geom.type`).
