# AX-CAD PC 설치·업데이트 가이드 (V1.2, Track 8 P1)

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
   - `data\` 이 PC의 데이터: `pg`(DB), `var`(업로드 도면), `backup`, `logs`, `axcad.env`(DB 비밀번호·토큰 키·설치 토큰, 이 Windows 계정만 읽기), `supplier.json`(견적서 공급자 정보)
4. 견적서의 공급자 정보: `data\supplier.json`을 메모장으로 열어 회사 정보로 바꾼다. 샘플 그대로면 견적서에 "샘플 공급자" 워터마크가 찍힌다.
5. 기준정보(단가): 기준정보 화면 → 새 버전 → **G1 엑셀 가져오기** → 활성화. 여러 PC에 같은 단가를 쓰려면 같은 G1 엑셀을 각 PC에서 가져온다.

## 3. 일상 사용·백업·복구

- **AX-CAD**: DB·API·웹을 켜고 브라우저를 연다(창은 숨김). 브라우저는 이 PC의 설치 토큰을 받아 기억하므로, 이후에는 `http://localhost:13000` 즐겨찾기로 열어도 된다. "시작 메뉴의 AX-CAD로 다시 여세요"가 보이면(다른 브라우저·기록 삭제 후) 시작 메뉴로 한 번 연다.
- **AX-CAD 종료**: 모두 끈다. PC를 끄기 전에 실행하면 안전하다.
- **AX-CAD 백업**: `data\backup\axcad-<시각>.dump`(DB) + `var-<시각>.zip`(도면·설정), 최근 10개 유지. **이 PC가 유일한 사본**이므로 주 1회 이상 다른 매체로 복사한다.
- 복구: PowerShell에서
  `& "$env:LOCALAPPDATA\AX-CAD\app\scripts\restore.ps1" -Dump <dump 파일> -VarZip <var zip>`
- 다른 PC로 옮기기: 새 PC에 같은 버전 설치 → 위 복구 명령으로 백업 파일을 넣는다.

### 3.1 DWG 파일 쓰기 (선택, Track 8 P1-5)

AX-CAD는 DWG 변환기를 포함하지 않는다. DWG를 올리려면 이 PC에 변환기를 한 번 설치한다.

1. **ODA File Converter**(무료, Open Design Alliance 홈페이지에서 Windows용 설치본)를 기본 위치(`C:\Program Files\ODA\...`)에 설치한다.
2. **AX-CAD 종료** → **AX-CAD**로 다시 시작한다. 시작할 때마다 변환기를 찾으므로 별도 설정이 없다.
3. 도면 화면에서 `.dwg` 파일을 올린다. 변환된 DXF로 리비전이 만들어지고 "DWG 변환" 경고가 함께 표시된다. 리비전에는 DXF가 저장되며 원본 DWG는 남지 않는다.

- 다른 위치에 설치했거나 LibreDWG `dwg2dxf.exe`를 쓰려면 `data\axcad.env`에 `DWG_CONVERTER=<실행 파일 전체 경로>`를 한 줄 추가한다. `dwg2dxf.exe`는 PATH에 있으면 자동으로 찾는다.
- 변환기가 없으면 "DWG 변환기가 설치되어 있지 않습니다" 안내가 나온다. 그때는 CAD에서 DXF로 저장해 올려도 된다.
- 변환은 120초 안에 끝나야 하고, 변환된 DXF는 50MB 이하여야 한다. 변환기에는 DB 비밀번호·설치 토큰을 넘기지 않는다.

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
2. GitHub 저장소 → Settings → **Environments** → New environment `release`
   - Deployment branches: `main`만, Required reviewers: 본인(서명 전에 승인 요청이 온다)
   - 이 환경의 **Environment secrets**에 이름 `AXCAD_PATCH_KEY`, 값 = `axcad-patch.key` 파일 내용(64자)
   - 저장소 전체 시크릿(Repository secret)으로 넣지 않는다: 다른 워크플로·브랜치에서 읽을 수 있다

### 4.3 개발자 — 릴리스

1. PR 머지 후 GitHub → Actions → **Windows release** → Run workflow → 버전 입력(예: `1.1.0`, 설치된 버전보다 커야 함).
2. 산출물(아티팩트, 30일 보관):
   - `AX-CAD-<v>-win-x64.zip` 새 PC 설치용
   - `AX-CAD-<v>-patch.zip` 일반 업데이트(서버 코드·웹·스크립트, 수십 MB)
   - `AX-CAD-<v>-full-patch.zip` 런타임까지 포함(수백 MB)
   - `SHA256SUMS.txt`
3. 사내 공유 폴더·메일로 배포한다. 패치 내용은 서명돼 있어 전달 경로에서 바뀌면 적용이 거부된다.
   첫 설치본(`win-x64.zip`)은 서명이 없으므로 `SHA256SUMS.txt`의 해시를 **다른 경로**(사내 공지 등)로도 알려 설치 전에 비교하게 한다.
4. 빌드와 서명은 서로 다른 잡에서 돈다: 서명 잡은 빌드 결과를 데이터로만 읽고, 저장소의 `patcher.py`를 러너 기본 Python(`-I -S`)으로 실행한다(패키지 설치 코드가 서명 키에 닿지 않게).

## 5. 보안 설계 요약

| 위협 | 대응 |
|---|---|
| 다른 웹사이트가 AX-CAD를 조작(CSRF)·내용 읽기 | 모든 API 요청에 이 설치의 무작위 토큰(`X-AXCAD-Local`) 필수 — 다른 사이트는 토큰을 모르고, 폼은 헤더를 못 붙이며, fetch는 CORS 사전 요청이 거부됨 |
| 같은 PC의 다른 Windows 계정 | 토큰은 `axcad.env`(설치한 계정만 읽기)에 있어 다른 계정은 API를 쓸 수 없음 |
| 화면 겹쳐 클릭 유도(클릭재킹) | 웹 화면은 다른 사이트의 프레임 안에서 열리지 않음(`X-Frame-Options: DENY`) |
| DNS 리바인딩·사내망 접근 | 모든 서비스가 127.0.0.1에만 열림, API가 Host·X-Forwarded-Host·Origin이 localhost인지 확인 |
| 변조·위조 패치 | Ed25519 서명(설치된 공개키가 기준), 파일별 SHA-256, 경로 탈출·대소문자만 다른 경로·추가 파일·다운그레이드 거부 |
| 업데이트 실패 | DB 백업 후 교체, 실패 시 DB를 새로 만들어 백업 복구 + 프로그램 자동 원복(교체 도중 정전이어도 원복 가능) |
| 서명 키 유출 | 빌드·서명 잡 분리, `release` 환경(승인·main 전용)의 시크릿 |
| DB 노출 | 127.0.0.1:55432, 무작위 비밀번호(scram-sha-256), 브라우저에는 비밀 환경변수를 넘기지 않음 |
| 백업 파일 조작 | 복구는 `var`·`supplier.json`만 풀고 설정 파일은 건드리지 않음. 단 DB 덤프는 그대로 실행되므로 **이 PC가 만든 백업만** 복구한다 |

남은 위험(수용): **같은 Windows 계정**에서 실행되는 다른 프로그램은 설정 파일을 읽을 수 있으므로 막을 수 없다. 여러 사람이 한 계정을 함께 쓰는 공용 PC에는 설치하지 않는다. PC 분실 대비는 Windows 로그인 암호·BitLocker, USB 백업은 BitLocker To Go로 한다.
