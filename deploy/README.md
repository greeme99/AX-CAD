# AX-CAD 사내 배포 (docker compose, 단일 서버)

> NFR-09: 사내망 단일 노드 운영. 이 폴더만으로 설치·업그레이드한다. UAT 서버도 같은 방법으로 만든다(`docs/UAT_가이드.md`).

## 요약

| 구성 | 이미지 | 역할 | 외부 포트 |
|---|---|---|---|
| `proxy` | nginx | 단일 입구: `/api` → api, 나머지 → web. 업로드 101MB·응답 180초 허용 | **8080** (변경: `AXCAD_PORT`) |
| `web` | `axcad-web` (Next.js standalone) | 화면 | 없음 |
| `api` | `axcad-api` (FastAPI + ezdxf + OCCT) | API·도면 처리 | 없음 |
| `migrate` | `axcad-api` | 기동 전 DB 스키마를 최신(head)으로 올리고 종료 | 없음 |
| `db` | PostgreSQL 16 | 데이터 | 없음 (컨테이너 내부망만) |

데이터는 볼륨 두 개에 남는다: `pgdata`(DB), `axcad-data`(업로드 원본·렌더·3D 캐시). **둘 다 백업 대상**이다.

## 1. 서버 요구사항

- Linux x86_64, Docker Engine 24+ (compose v2 포함), 메모리 8GB 이상 권장(3D 커널·대형 도면)
- 사용자 PC → 서버 `8080` 포트 접근 허용(사내 방화벽)

## 2. 최초 설치

```bash
git clone <저장소> axcad && cd axcad/deploy
cp env.example .env
# .env 편집: POSTGRES_PASSWORD, JWT_SECRET 은 `openssl rand -hex 32` 로 생성(16진수만)
chmod 600 .env

docker compose build                 # 이미지 2개 빌드 (최초 수 분)
docker compose up -d                 # db → migrate → api·web → proxy 순으로 기동
docker compose ps                    # migrate 는 "exited (0)", 나머지는 running

# 첫 관리자 (비밀번호는 두 번 입력, 화면·명령 이력에 남지 않음)
docker compose run --rm api python -m backend.cli create-user --login admin --name "관리자" --admin
```

브라우저에서 `http://<서버>:8080` → 관리자로 로그인 → 사용자·프로젝트·기준정보 준비(UAT 가이드 §1~2).

### 선택 설정

| 항목 | 방법 |
|---|---|
| 정식 견적서 공급자 정보 | `deploy/config/supplier.json`(형식은 `backend/config/supplier.json`, `_sample` 키 없이) → `.env`에 `AXCAD_SUPPLIER_FILE=/config/supplier.json` → `docker compose up -d` |
| ERP 연동 | `.env`의 `ERP_API_URL`(https), `ERP_API_TOKEN` |
| 포트 | `.env`의 `AXCAD_PORT` |

`deploy/.env`와 `deploy/config/`는 git에 올라가지 않는다(`.gitignore`). 빌드 컨텍스트에서도 제외된다(`.dockerignore`).

## 3. 업그레이드

```bash
git pull
# .env 의 AXCAD_VERSION 을 새 값(예: 2027.05.1)으로 바꾸면 이전 이미지가 남아 되돌리기 쉽다
docker compose build && docker compose up -d   # migrate 가 새 마이그레이션을 먼저 적용
```

- 업그레이드 전 **백업**(운영 기본기 문서, Track 3-2)을 먼저 받는다. 증거 데이터(승인·발행 견적서·Revision)가 있으면 DB 마이그레이션 되돌리기(downgrade)는 거부되도록 만들어져 있다.
- 되돌리기: `AXCAD_VERSION`을 이전 값으로 돌리고 `docker compose up -d`. 단, 새 마이그레이션이 적용됐다면 DB는 백업에서 복구한다.

## 4. 사내망·프록시 환경

| 상황 | 방법 |
|---|---|
| Docker Hub 차단, 사내 레지스트리 사용 | `.env`의 `PYTHON_IMAGE`·`NODE_IMAGE`·`POSTGRES_IMAGE`·`NGINX_IMAGE`를 사내 미러 주소로 |
| 빌드 중 패키지 다운로드가 프록시 경유 | `docker compose build --build-arg HTTPS_PROXY=... --build-arg HTTP_PROXY=...` (또는 `~/.docker/config.json`의 `proxies`) |
| 프록시가 TLS를 검사(사내 CA) | 저장소 루트에서 `docker build --secret id=ca,src=<사내CA번들.pem> -f deploy/api.Dockerfile -t axcad-api:<버전> .` (web 은 `web.Dockerfile`·`axcad-web`). CA는 빌드 중에만 쓰이고 이미지에 남지 않는다. 그다음 `docker compose up -d` |
| 완전 폐쇄망 | 인터넷 되는 곳에서 빌드 → `docker save axcad-api:<버전> axcad-web:<버전> postgres:16 nginx:1.28-alpine -o axcad.tar` → 서버에서 `docker load -i axcad.tar` → `docker compose up -d` (build 생략) |

## 5. 보안 메모

- 컨테이너는 root가 아닌 사용자로 실행된다(api: uid 10001, web: node). DB는 외부 포트를 열지 않는다.
- 시크릿은 `.env`(권한 600)에만 둔다. 이미지·저장소·로그에 넣지 않는다.
- HTTPS가 필요하면 사내 인증서로 `nginx.conf`에 `listen 443 ssl`을 추가하거나 사내 리버스 프록시 뒤에 둔다.
- 남은 운영 전 보안 과제(DB 앱 역할 분리, 토큰 저장 방식)는 `docs/HANDOFF.md` §6.
