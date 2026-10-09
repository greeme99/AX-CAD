---
paths:
  - "core/quote_engine/**"
  - "core/bom/**"
  - "**/*quote*/**"
  - "**/*cost*/**"
---
# Quote Engine & Cost Calculation Rules

## 1. 한국표준 원가 계산 체계 (Korean Standard Cost Framework)
견적 산출은 다음의 표준 원가 구성 체계를 엄격히 준수한다:
1. **순제조원가(Manufacturing Cost)** = 직접재료비 + 직접노무비 + 제조간접비
   - **직접재료비**: 소재 정미중량(Net Weight) × 기준단가 + 스크랩/손실률(Scrap Rate)
   - **직접노무비**: 공정별 표준공수(M/H) × 표준임률(Hourly Rate)
   - **제조간접비**: 기계 사용료(Machine Hour Cost) + 감가상각 + 전력비 등
2. **총원가(Total Cost)** = 순제조원가 + 일반관리비(통상 순제조원가의 5~8%)
3. **견적공급가액(Supply Amount)** = 총원가 + 이윤(통상 7~15%)
4. **최종 견적가(Grand Total)** = 견적공급가액 + 부가가치세(VAT 10%)

## 2. 도면 메트릭 매핑 및 공정 규칙
- **2D DXF 기반 공정 매핑**:
  - 외곽 절단선 총 길이(Cutting Length) → 레이저/NCT 절단 공수 산출
  - 절곡선(Bending Line) 개수 및 길이 → 프레스 브레이크(V-Bending) 공수 산출
  - 구멍(Hole) 개수 및 직경 → 펀칭/드릴/태핑 공정 산출
  - 도면 텍스트/표제란(Title Block) → 품번, 품명, 재질(SUS304, SS400, AL6061 등), 수량 자동 추출
- **3D STEP 기반 공정 매핑**:
  - 바운딩 박스(B-Box) 및 체적(Volume) → 원자재 규격 및 블록 중량 산출
  - 표면적(Surface Area) → 표면처리(아노다이징, 도금, 도장) 비용 산출
  - 밀링/선반 형상 판별 → 3축/5축 CNC 가공 공수 산출

## 3. 데이터 추적성(Traceability) 및 감사 원칙
- **원천 매핑 보존**: 모든 `quote_line`은 반드시 대상 `source_entity_id`(도면 내 엔티티 핸들) 또는 `source_part_id`를 외래키로 참조해야 한다.
- **수동 보정 격리**: 사용자가 견적 단가나 공수를 수동 조정한 경우 원본 계산값(`calculated_value`)을 덮어쓰지 않고, `override_value` 컬럼에 별도 기록하며 수정 사유(`override_reason`)를 남긴다.
- **결과 포맷**: 견적서 출력은 한국표준 양식(공급자 정보, 공급받는자 정보, 품명, 규격, 수량, 단가, 공급가액, 세액)을 지원하는 JSON/PDF로 생성한다.
