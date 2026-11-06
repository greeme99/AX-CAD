target: memory
action: replace
old_text:   - 함정: 거부 메시지의 "apply the full patch"는 UPDATE 등급에서는 해결책이 아니다(full patch도 런타임 변경으로 거부). CI `windows` 잡은 설치본을 복사해 패치를 만들어 이 문제를 잡지 못한다 — 서로 다른 빌드 간 id 비교 테스트는 없음(후속).
---
  - 런타임 프로브(BUG-55, PR #44): CI 빌드마다 pip 메타데이터 때문에 python id가 달라져 작은 패치는 거부되는 게 정상이다 → **full patch**를 쓴다. UPDATE의 런타임 검사(`check_grade`)는 컴포넌트 id가 아니라 `RUNTIME_PROBES`(python.exe·bin/postgres.exe·node.exe) 해시를 비교하므로, 핵심 바이너리가 같으면 full patch가 통과한다. CI `windows` 잡은 서로 다른 빌드 간 비교를 하지 않는다(후속).
  - 함정: PC는 **설치본의** `scripts/patcher.py`로 검사한다(`update.ps1`, `routes_system.py`). patcher 규칙을 고친 릴리스는 구버전 PC에서 옛 규칙으로 거부될 수 있다 → 그 1회는 새 번들의 `app\scripts\patcher.py`를 설치본에 덮어쓴 뒤 full patch 적용(2026-11-06 사용자 PC 1.0.0 → 1.0.2 성공).
- 릴리스 실행(PR #45, 2026-11-06): Windows release 입력은 version + from_version뿐. **from_version 비움 = UPDATE**, 채움 = UPGRADE(직전 major 마지막 버전). build 잡 첫 단계 `Check inputs`가 형식·major 규칙·`installer/release-notes/<v>.txt` 존재를 검사한다(노트 없으면 실패 → 1.0.2/1.2.0 오타 방지). 예전 grade 선택 입력은 같은 major 릴리스를 UPGRADE로 골라 sign 단계에서 반복 실패해 없앴다. 워크플로 dispatch는 세션 GitHub 권한(403)으로 불가 → 사용자가 Actions에서 실행. Rerun은 원래 커밋으로 돌므로 머지 후엔 새로 실행.
---
reason: 1.0.2 릴리스까지 sign 실패 2회(#9 1.2.0 UPGRADE, #10 1.1.0 UPGRADE)와 PC 적용 실패 2종을 겪고 해결함. 기존 52행의 "full patch도 거부" 함정은 PR #44 이후 사실이 아니므로 교체하고, 반복될 운영 규칙(from_version이 등급 결정, 노트 필수, 구버전 PC의 patcher 교체)을 남긴다.
