# AX-CAD .NET SDK (C#)

`AxCad` 라이브러리로 AX-CAD를 앱 밖에서 자동화합니다. Python SDK(`sdk/axcad.py`)와 같은 REST API를 호출하므로 화면과 같은 권한 검사·감사 로그·리비전 이력이 그대로 적용됩니다. 앱 안에서 코드를 실행하지 않고, 외부 NuGet 패키지도 쓰지 않습니다.

| 항목 | 값 |
|---|---|
| 플랫폼 | .NET 8 (`net8.0`), Windows·Linux·macOS |
| SDK 버전 | 2.2.0 (`AxCad.csproj`의 `Version`) |
| 대상 API | AX-CAD 2.2 REST (`/api/...`, 응답 봉투 `success`/`data`/`error`) |
| 좌표 | WCS, `double`, 도면 단위 mm (DXF `$INSUNITS`는 서버가 변환) |
| 각도 | 라디안 (`AddArc`, `Rotate`, `Arc.StartAngle`) |
| 허용오차 | 서버 기하 검증 1e-6 mm |

## 빠른 시작

```csharp
using AxCad;

using var cad = AxCadClient.Local();                 // 이 PC 설치본 (설치 토큰 자동)
var dwg = await cad.OpenDrawingAsync(revisionId);     // 2D 리비전을 객체로

using (var tr = dwg.StartTransaction())
{
    dwg.ActiveLayer = dwg.Layers.Add("CUT");
    dwg.ModelSpace.AddLightWeightPolyline([0, 0, 200, 0, 200, 100, 0, 100], closed: true);
    dwg.ModelSpace.AddCircle(new(15, 15), 5).Color = 1;
    tr.Commit();                                      // Commit이 없으면 Dispose에서 되돌림
}
var saved = await dwg.SaveAsync();                    // 다음 리비전 (revision_id, revision_no)
```

서버 모드: `new AxCadClient("https://cad.example", token: await AxCadClient.LoginTokenAsync(url, id, pw))`.

예제 `examples/PlateDrawing`: 빈 A3 도면 → 200×100 판재 + Ø10 구멍 4개 → 저장.
실행: `dotnet run --project sdk/dotnet/examples/PlateDrawing -- <프로젝트 ID>` (PC 설치본이 켜져 있어야 함).

## 트랜잭션 계약

AutoCAD .NET의 Transaction 패턴을 따릅니다.

| 규칙 | 동작 |
|---|---|
| 모든 변경은 트랜잭션 안에서 | 밖에서 `Add*`·속성 쓰기·`Layers.Add` → `NO_TRANSACTION` |
| 한 번에 하나 | 열린 트랜잭션이 있으면 `StartTransaction` → `TRANSACTION_ACTIVE` (중첩 금지) |
| `Commit` | 변경 확정, `Status = Committed` |
| `Abort` / `Commit` 없이 `Dispose` | 시작 시점으로 완전 복원 (새 객체·속성·새 레이어·현재 레이어), `Status = RolledBack` |
| 끝난 트랜잭션 | `Commit` → `TRANSACTION_ENDED` |
| 저장 | 트랜잭션이 열려 있으면 `SaveAsync` → `TRANSACTION_ACTIVE`. 저장은 한 번의 편집 요청으로 서버에서 원자적으로 적용 (실패하면 리비전이 생기지 않음) |
| 저장 후 | 그 객체는 읽기 전용(`IsSaved`) — 같은 편집을 두 번 보내지 않음(`DRAWING_SAVED`). 이어서 편집하려면 반환된 리비전을 다시 엽니다 |

트랜잭션은 이 프로세스 안의 변경 묶음이고, 저장 하나가 화면의 저장 하나(= 리비전 하나, 감사 로그 1건)와 같습니다.

## 객체 모델

| 객체 | 주요 멤버 |
|---|---|
| `Drawing` | `ModelSpace`, `Layers`, `ActiveLayer`, `StartTransaction()`, `Changes()`, `SaveAsync()` |
| `ModelSpace` | `AddLine`, `AddCircle`, `AddArc`, `AddLightWeightPolyline`, `AddText`, `AddPoint`, 열거 |
| `Entity` | `Handle`(불변), `ObjectName`, `Layer`, `Color`(0..256, 256=BYLAYER), `Linetype`, `Move`, `Rotate`, `Scale`, `Mirror`(복사본), `Copy`, `Erase` |
| `Line` / `Circle` / `Arc` | `StartPoint`·`EndPoint`·`Length` / `Center`·`Radius`·`Area` / `StartAngle`·`EndAngle` |
| `Polyline` | `NumberOfVertices`, `GetPointAt`, `GetBulgeAt`, `SetBulgeAt`, `Closed`, `Area`·`Length`(벌지 포함) |
| `DBText` / `DBPoint` | `TextString`·`Height`·`Position` / `Position` |

편집 대상은 화면에서도 편집되는 도형(선·원·호·폴리라인·문자·점)입니다. 치수·해치·블록 등 서버가 그린 도형은 목록에 나오지 않고 그대로 보존됩니다. 핸들은 서버가 정하며 바뀌지 않습니다.

## 오류

모든 오류는 `AxCadException(Status, Code, Message)`입니다. API 오류는 서버 코드(`REVISION_NOT_FOUND`, `EDIT_LAYER_LOCKED` 등)를 그대로 옮기고, SDK가 보내기 전에 막는 오류는 `Status = 0`입니다.

| 코드 | 뜻 | 조치 |
|---|---|---|
| `NO_TRANSACTION` · `TRANSACTION_ACTIVE` · `TRANSACTION_ENDED` | 트랜잭션 규칙 위반 | 위 계약 참조 |
| `DRAWING_SAVED` | 저장한 도면 객체를 다시 씀 | 새 리비전을 엽니다 |
| `LAYER_LOCKED` | 잠긴 레이어의 도형 변경·생성 | 대상에서 빼거나 화면에서 잠금 해제 |
| `NO_LAYER` · `BAD_LAYER` | 없는 레이어 / 쓸 수 없는 이름 | `Layers.Add`로 만들거나 이름 수정 |
| `BAD_GEOMETRY` · `BAD_COLOR` · `TOO_MANY` | 좌표 범위(±1e9)·0 이하 크기·색 번호 / 저장당 새 도형 10,000개 초과 | 입력 수정, 나눠 저장 |
| `ERASED` | 지운 도형을 다시 씀 | — |
| `NO_CHANGES` | 저장할 변경 없음 | — |
| `BAD_URL` · `NO_LOCAL_TOKEN` | 연결 규칙 위반 / 설치 토큰 없음 | 아래 보안 참조 |
| `HTTP_ERROR` · `NOT_JSON` · `NOT_A_FILE` | 봉투가 아닌 응답 (리다이렉트 포함) | 주소·서버 상태 확인 |

## 보안

- 토큰은 https로만 보냅니다. http는 이 PC(127.0.0.1·::1·localhost)일 때만 허용합니다.
- 설치 토큰(`X-AXCAD-Local`)은 이 PC 주소에만 보냅니다.
- 리다이렉트를 따라가지 않습니다. 토큰이 다른 호스트로 새지 않습니다.
- 업로드 파일 이름에서 따옴표와 줄바꿈을 지웁니다. DXF 검사(형식·크기·XREF)는 서버가 수행합니다.
- 권한은 로그인한 사용자의 역할 그대로입니다. SDK가 권한을 넓히지 않습니다.

## 아직 지원하지 않음

- `Idempotency-Key`와 비동기 Job은 아직 없습니다. 저장은 동기 요청 하나이고, 저장 뒤 같은 객체로 다시 보내지 못하게 막는 것으로 중복을 방지합니다.
- `/api/v1` 버전 경로는 아직 없습니다. 호환을 깨는 변경은 SDK 버전과 함께 안내합니다.
- 실시간 이벤트(Reactor)는 앱 안의 AX-LISP에서만 제공합니다. SDK는 일괄 처리용입니다.
- 블록 정의·치수·해치 생성은 지원하지 않습니다. 화면이나 AX-LISP를 사용하세요.

## 테스트

```bash
dotnet run --project sdk/dotnet/AxCad.Tests   # 가짜 API로 연결 규칙·봉투·트랜잭션·기하 검사 (CI)
```
