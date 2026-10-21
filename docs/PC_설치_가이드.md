# AX-CAD PC 설치·업데이트 가이드 (V1.0, Track 7)

> **운영 방식(2026-10-21 사용자 결정)**: 서버 없이 **사용자 PC마다 설치**해 쓰고, 기능 개선은 **서명된 패치 파일**로 업데이트한다. 각 PC의 데이터(도면·견적·기준정보)는 그 PC에만 있다. 로그인·결재·ERP 실연동은 없다.

## 요약

| 할 일 | 누가 | 방법 |
|---|---|---|
| 처음 설치 | 사용자 | `AX-CAD-<버전>-win-x64.zip` 압축 해제 → `install.cmd` 실행 (관리자 권한 불필요) |
| 실행·종료 | 사용자 | 시작 메뉴 **AX-CAD** / **AX-CAD 종료** |
| 백업 | 사용자 | 시작 메뉴 **AX-CAD 백업** → `data\backup` 폴더를 USB·다른 디스크로 복사 |
| 업데이트 | 사용자 | 시작 메뉴 **AX-CAD 업데이트** → 받은 `AX-CAD-<버전>-patch.zip` 선택 |
| 설치본·패치 만들기 | 개발자 | GitHub Actions **Windows release** 실행(§4) |

## 1. 요구 사항

- Windows 10/11 64비트, 디스크 여유 5GB 이상, 메모리 8GB 이상 권장
- 관리자 권한·Docker·인터넷 연결 **불필요**(설치 후)
- 사용 포트(이 PC 안에서만): 웹 13000, API 18000, DB 55432 — 다른 프로그램이 쓰면 시작 실패 메시지가 나온다

## 2. 설치

1. 받은 `AX-CAD-<버전>-win-x64.zip`의 SHA-256을 함께 받은 `SHA256SUMS.txt`와 비교한다(PowerShell: `Get-FileHash 파일.zip`).
2. 압축을 풀고 `install.cmd`를 더블클릭한다. 약 1~3분 뒤 브라우저에 AX-CAD가 열린다.
3. 설치 위치: `%LOCALAPPDATA%\AX-CAD`
   - `app\` 프로그램(업데이트 시 교체)
   - `data\` 이 PC의 데이터: `pg`(DB), `var`(업로드 도면), `backup`, `logs`, `axcad.env`(DB 비밀번호·토큰 키, 이 사용자만 읽기), `supplier.json`(견적서 공급자 정보)
4. 견적서의 공급자 정보: `data\supplier.json`을 메모장으로 열어 회사 정보로 바꾼다. 샘플 그대로면 견적서에 "샘플 공급자" 워터마크가 찍힌다.
5. 기준정보(단가): 기준정보 화면 → 새 버전 → **G1 엑셀 가져오기** → 활성화. 여러 PC에 같은 단가를 쓰려면 같은 G1 엑셀을 각 PC에서 가져온다.

## 3. 일상 사용·백업·복구

- **AX-CAD**: DB·API·웹을 켜고 브라우저를 연다(창은 숨김). 이미 켜져 있으면 브라우저만 연다.
- **AX-CAD 종료**: 모두 끈다. PC를 끄기 전에 실행하면 안전하다.
- **AX-CAD 백업**: `data\backup\axcad-<시각>.dump`(DB) + `var-<시각>.zip`(도면·설정), 최근 10개 유지. **이 PC가 유일한 사본**이므로 주 1회 이상 다른 매체로 복사한다.
- 복구: PowerShell에서
  `& "$env:LOCALAPPDATA\AX-CAD\app\scripts\restore.ps1" -Dump <dump 파일> -VarZip <var zip>`
- 다른 PC로 옮기기: 새 PC에 같은 버전 설치 → 위 복구 명령으로 백업 파일을 넣는다.

## 4. 업데이트(패치)

### 4.1 사용자

1. **AX-CAD 업데이트** → 패치 파일 선택. 진행 순서:
   종료 → DB 백업 → 패치 **서명 검증**·파일 해시 검증 → 프로그램 교체 → DB 마이그레이션 → 시작·상태 확인
2. 어느 단계든 실패하면 **이전 프로그램과 DB로 자동 복원**하고 이전 버전으로 다시 켠다.
3. 메시지별 조치

| 메시지 | 뜻 | 조치 |
|---|---|---|
| signature does not match | 우리 회사 키로 서명되지 않은 파일 | 사용 금지, 담당자에게 알림 |
| not newer than installed | 같은·이전 버전 | 최신 패치를 받는다 |
| apply the full patch | 런타임(Python·PostgreSQL·Node)이 바뀐 버전 | `…-full-patch.zip`을 적용 |
| PostgreSQL 주 버전이 다릅니다 | DB 엔진 주 버전 변경 | 백업 → 새 설치본 설치 → 복구 |

### 4.2 개발자 — 서명 키 만들기(최초 1회, 집 PC)

1. 저장소 폴더에서 `uv run python installer\patcher.py keygen %USERPROFILE%\axcad-patch.key`
   - 출력된 64자 공개키를 `installer\patch_pub.hex`로 저장해 커밋한다(공개키는 공유해도 안전).
   - `axcad-patch.key`(개인키)는 **절대 저장소·채팅에 올리지 않는다**. USB 등에 별도 보관(분실 시 기존 설치본은 새 키로 서명된 패치를 받을 수 없어 재설치 필요).
2. GitHub 저장소 → Settings → Secrets and variables → Actions → **New repository secret**
   이름 `AXCAD_PATCH_KEY`, 값 = `axcad-patch.key` 파일 내용(64자).

### 4.3 개발자 — 릴리스

1. PR 머지 후 GitHub → Actions → **Windows release** → Run workflow → 버전 입력(예: `1.1.0`, 설치된 버전보다 커야 함).
2. 산출물(아티팩트, 30일 보관):
   - `AX-CAD-<v>-win-x64.zip` 새 PC 설치용
   - `AX-CAD-<v>-patch.zip` 일반 업데이트(서버 코드·웹·스크립트, 수십 MB)
   - `AX-CAD-<v>-full-patch.zip` 런타임까지 포함(수백 MB)
   - `SHA256SUMS.txt`
3. 사내 공유 폴더·메일로 배포한다. 패치 내용은 서명돼 있어 전달 경로에서 바뀌면 적용이 거부된다.

## 5. 보안 설계 요약

| 위협 | 대응 |
|---|---|
| 같은 브라우저의 다른 웹사이트가 AX-CAD를 조작(CSRF) | 상태 변경 요청은 전용 헤더 `X-AXCAD-Local` 필수(다른 사이트 폼은 붙일 수 없고, fetch는 CORS 사전 요청이 거부됨) |
| DNS 리바인딩·사내망 접근 | 모든 서비스가 127.0.0.1에만 열림, API가 Host·X-Forwarded-Host·Origin이 localhost인지 확인 |
| 변조·위조 패치 | Ed25519 서명(설치된 공개키가 기준), 파일별 SHA-256, 경로 탈출·추가 파일·다운그레이드 거부 |
| 업데이트 실패 | DB 백업 후 교체, 실패 시 프로그램·DB 자동 원복 |
| DB 노출 | 127.0.0.1:55432, 무작위 비밀번호(scram-sha-256), 설정 파일은 사용자 본인만 읽기 |

남은 위험(수용): 로그인이 없으므로 **이 PC에서 실행되는 프로그램**(다른 Windows 계정 포함)은 localhost로 AX-CAD에 접근할 수 있다. 여러 사람이 쓰는 공용 PC에는 설치하지 않는다. PC 분실 대비는 Windows 로그인 암호·BitLocker로 한다.
