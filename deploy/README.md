# AX-CAD 사내 배포 (docker compose, 단일 서버)

> NFR-09: 사내망 단일 노드 운영. 이 폴더만으로 설치·업그레이드·백업한다. UAT 서버도 같은 방법으로 만든다(`docs/UAT_가이드.md`).

## 요약

| 구성 | 이미지 | 역할 | 외부 포트 |
|---|---|---|---|
| `proxy` | nginx | 단일 입구: `/api` → api, 나머지 → web. HTTPS·보안 헤더·로그인 속도 제한 | **443**(HTTPS), 8080(→443 이동) |
| `web` | `axcad-web` (Next.js standalone) | 화면 | 없음 |
| `api` | `axcad-api` (FastAPI + ezdxf + OCCT) | API·도면 처리, `/api/health` | 없음 |
| `migrate` | `axcad-api` | 기동 전 DB 스키마를 최신(head)으로 올리고 종료 | 없음 |
| `db` | PostgreSQL 16 | 데이터 | 없음 (컨테이너 내부망만) |

데이터는 볼륨 두 개에 남는다: `pgdata`(DB), `axcad-data`(업로드 원본·렌더·3D 캐시). **둘 다 백업 대상**이다(§4).

## 1. 서버 요구사항

- Linux x86_64, Docker Engine 24+ (compose v2 포함), 메모리 8GB 이상 권장(3D 커널·대형 도면)
- 사용자 PC → 서버 443 포트 접근 허용(사내 방화벽)
- 사내 CA로 발급한 서버 인증서(운영). 시험·UAT는 HTTP 모드 가능(§2 선택 설정)

## 2. 최초 설치

```bash
git clone <저장소> axcad && cd axcad/deploy
cp env.example .env && chmod 600 .env
# .env 편집: POSTGRES_PASSWORD, APP_DB_PASSWORD, JWT_SECRET 은 `openssl rand -hex 32` 로 생성(16진수만)

# HTTPS 인증서 (사내 CA 발급본). 파일명 고정: server.crt(체인 포함), server.key
mkdir -p config/tls && cp <인증서> config/tls/server.crt && cp <키> config/tls/server.key
chmod 755 config config/tls && chmod 644 config/tls/server.crt && chmod 640 config/tls/server.key

docker compose build                 # 이미지 2개 빌드 (최초 수 분)
docker compose up -d                 # db → migrate → api(healthy) → web·proxy 순으로 기동
docker compose ps                    # migrate 는 exited (0), api 는 (healthy)

# 첫 관리자: 비밀번호를 화면에서 두 번 입력 (-T 를 붙이거나 파이프로 넣지 않는다)
docker compose run --rm api python -m backend.cli create-user --login admin --name "관리자" --admin
```

브라우저에서 `https://<서버>` → 관리자로 로그인 → 사용자·프로젝트·기준정보 준비(UAT 가이드 §1~2).

### 선택 설정 (`.env`)

| 항목 | 방법 |
|---|---|
| HTTP 모드(시험·UAT 전용) | `AXCAD_PROXY=http` → `http://<서버>:8080`. 로그인·토큰이 평문으로 오가므로 운영에 쓰지 않는다 |
| 바인딩 주소 | `AXCAD_BIND=<서버 사내 IP>` — Docker는 호스트 방화벽(ufw 등)을 우회해 포트를 연다 |
| 정식 견적서 공급자 정보 | `config/supplier.json`(형식은 `backend/config/supplier.json`, `_sample` 키 없이, 권한 644) → `AXCAD_SUPPLIER_FILE=/config/supplier.json` |
| ERP 연동 | `ERP_API_URL`(https), `ERP_API_TOKEN` |
| API 메모리 상한 | `AXCAD_API_MEM` (기본 6g) |

`deploy/.env`·`deploy/config/`·`deploy/backups/`는 git에 올라가지 않고 빌드 컨텍스트에서도 제외된다.

## 3. 업그레이드·되돌리기

```bash
./backup.sh                                   # 먼저 백업 (§4)
git pull
# .env 의 AXCAD_VERSION 을 새 값(예: 2027.05.1)으로 바꾸면 이전 이미지가 남아 되돌리기 쉽다
docker compose build && docker compose up -d  # migrate 가 새 마이그레이션을 먼저 적용
```

- 되돌리기: `AXCAD_VERSION`을 이전 값으로 → `docker compose up -d`. 새 마이그레이션이 이미 적용됐다면 DB는 백업에서 복구한다(증거 데이터가 있으면 downgrade는 거부되도록 만들어져 있다).
- 프록시는 api·web 컨테이너가 새로 만들어져도 다시 찾아간다(Docker DNS 재조회) — 프록시 재시작 불필요.

## 4. 운영

### 상태 확인

| 확인 | 방법 |
|---|---|
| 전체 | `docker compose ps` — api `(healthy)`, db `(healthy)` |
| API | `curl -k https://<서버>/api/health` → `{"status":"ok"}` (로그인 불필요, DB 연결·파일 저장소 쓰기 가능 여부만) |
| 로그 | `docker compose logs --tail 200 api` — 서비스별 10MB × 5개로 자동 순환 |

### 백업 (매일 권장)

```bash
./backup.sh                     # deploy/backups/axcad-<시각>.dump + data-<시각>.tar, 최근 14쌍 보관
BACKUP_KEEP=30 ./backup.sh /mnt/nas/axcad   # 보관 개수·위치 지정
# cron 예: 매일 02:30
# 30 2 * * * cd /opt/axcad/deploy && ./backup.sh /mnt/nas/axcad >> /var/log/axcad-backup.log 2>&1
```

- 백업 파일에는 단가·견적·계정 정보가 들어 있다: 권한 600(폴더 700)으로 만들어지며, **서버 밖(NAS 등)에도 복사**한다. 같은 디스크의 백업은 디스크 장애를 막지 못한다.
- 백업에 **들어가지 않는 것**: `deploy/.env`(비밀번호·서명 키), `deploy/config/`(TLS 인증서·공급자 정보). 새 서버로 복구하려면 이 둘을 별도로 안전하게 보관한다.
- 보관 개수는 파일명의 시각 기준으로 센다(복사로 수정 시각이 바뀌어도 최신본을 지우지 않는다). 수동으로 만든 다른 이름의 파일은 건드리지 않는다.
- DB 덤프와 파일 묶음은 같은 순간의 스냅샷이 아니다. 업무 시간 외에 받는다.
- 백업 직후 덤프·묶음을 다시 읽어 깨지지 않았는지 확인한다(실패하면 스크립트가 오류로 끝난다).

### 복구

```bash
./restore.sh backups/axcad-<시각>.dump backups/data-<시각>.tar   # "RESTORE" 입력해야 진행
```

현재 DB와 파일을 **모두 백업 시점으로 바꾼다**. 순서: 대상 프로젝트 표시·`RESTORE` 확인 → **현재 상태 스냅샷**(`backups/pre-restore-<시각>/`) → api·web·proxy 정지 → DB 재생성·**단일 트랜잭션** 적재(오류 시 즉시 중단) → 파일 교체 → `up -d`(오래된 백업이면 migrate가 최신 스키마로 올리고 API 역할 권한을 다시 부여). 복구가 잘못되면 스냅샷으로 같은 스크립트를 다시 돌린다. 오래된 백업은 그때의 사용자·비밀번호도 되살린다.

복구·업그레이드 뒤 `./check-app-role.sh`로 API 역할 권한을 확인한다. **분기마다 UAT 서버에서 복구 훈련**을 해서 백업이 실제로 쓸 수 있는지 확인한다.

다른 compose 프로젝트(예: UAT)는 `COMPOSE_PROJECT_NAME=axcad-uat ./backup.sh`.

### 기타

- DB 비밀번호 교체: API 역할은 `.env`의 `APP_DB_PASSWORD`만 바꾸고 `docker compose up -d`. 소유자 계정은 `docker compose exec db psql -U axcad -c "ALTER USER axcad PASSWORD '<새값>'"` → `.env`의 `POSTGRES_PASSWORD` 수정 → `docker compose up -d` (`POSTGRES_PASSWORD`는 DB를 처음 만들 때만 적용된다)
- API 워커 수를 늘리지 않는다: 도면 파싱·견적서 렌더 동시 실행 상한이 프로세스 단위라 메모리가 배로 는다.
- 로그인은 IP당 분당 10회(순간 5회 추가)로 제한된다. 여러 사용자가 한 IP(사내 프록시 등)로 들어오면 `nginx/http.conf`·`https.conf`의 `rate`를 올린다.

## 5. 사내망·프록시 환경

| 상황 | 방법 |
|---|---|
| Docker Hub 차단, 사내 레지스트리 사용 | `.env`의 `PYTHON_IMAGE`·`NODE_IMAGE`·`POSTGRES_IMAGE`·`NGINX_IMAGE`를 사내 미러 주소로(재현성이 필요하면 `@sha256:` 고정) |
| 빌드 중 패키지 다운로드가 프록시 경유 | `docker compose build --build-arg HTTPS_PROXY=... --build-arg HTTP_PROXY=...` (또는 `~/.docker/config.json`의 `proxies`) |
| 프록시가 TLS를 검사(사내 CA) | 저장소 루트에서 `docker build --secret id=ca,src=<사내CA번들.pem> -f deploy/api.Dockerfile -t axcad-api:<버전> .` (web 은 `web.Dockerfile`·`axcad-web`). CA는 빌드 중에만 쓰이고 이미지에 남지 않는다. 그다음 `docker compose up -d` |
| 완전 폐쇄망 | 인터넷 되는 곳에서 빌드 → `docker save axcad-api:<버전> axcad-web:<버전> postgres:16 nginx:1.28-alpine -o axcad.tar` → 서버에서 `docker load -i axcad.tar` → `docker compose up -d` (build 생략) |

## 6. 보안 메모

- api·web·proxy는 Linux 권한(capability)을 모두 버리고(proxy는 워커 전환에 필요한 3개만), 권한 상승을 막는다. api·web은 루트 파일시스템이 읽기 전용이고 root가 아닌 사용자로 실행된다(api uid 10001, web node). DB는 외부 포트를 열지 않는다.
- 응답 보안 헤더(nosniff, 프레임 금지, Referrer 차단, HSTS), API 응답 캐시 금지, FastAPI 문서(`/docs`)는 외부로 노출되지 않는다.
- **CSP**: 화면의 스크립트는 같은 출처(이 서버)로만 통신·이미지 로드·폼 제출을 할 수 있다(`connect-src 'self'` 등). 주입 스크립트가 데이터를 외부로 보내는 일반 경로를 막지만, XSS 자체나 페이지 이동을 이용한 유출까지 막지는 못한다(Next.js 구조상 인라인 스크립트 허용). API 응답에는 실행 불가 CSP(`sandbox`)를 붙인다.
- **DB 역할 분리**: API는 `axcad_app` 역할로 접속한다. 데이터 읽기·쓰기만 가능하고, 삭제는 API가 실제로 지우는 테이블(구성원·3D Feature·초안 기준정보)만 허용한다. 트리거 해제·DDL·TRUNCATE·시퀀스 되돌리기·감사 로그와 발행 견적서 등 증거의 수정·삭제는 불가하다. 비밀번호는 SCRAM 해시로만 DB에 전달된다. DB 소유자(`axcad`) 계정은 `migrate` 단계와 백업만 쓴다. 역할과 권한은 배포(`up -d`)마다 `migrate`가 다시 맞추므로 `APP_DB_PASSWORD`를 바꾸고 `up -d`하면 비밀번호가 교체된다.
- 시크릿은 `.env`(권한 600)에만 둔다. `docker inspect`로 보이므로 서버의 docker 그룹 구성원을 최소로 유지한다.
- 로그인 토큰은 브라우저 sessionStorage에 있다(탭을 닫으면 삭제). httpOnly 쿠키 전환은 CSP로 유출 경로를 막은 뒤 필요 시 진행(`docs/HANDOFF.md` §6).
