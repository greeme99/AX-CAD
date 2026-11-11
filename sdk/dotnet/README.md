# AX-CAD .NET SDK (C#)

`AxCad` 라이브러리로 AX-CAD를 앱 밖에서 자동화합니다. Python SDK(`sdk/axcad.py`)와 같은 REST API를 호출하므로 화면과 같은 권한 검사·감사 로그·리비전 이력이 그대로 적용됩니다. 앱 안에서 코드를 실행하지 않고, 외부 NuGet 패키지도 쓰지 않습니다.

**3.0 (W8, plan §22.4)부터 AutoCAD .NET API(`Autodesk.AutoCAD.DatabaseServices`·`Geometry`·`EditorInput`)와 같은 구조**입니다: `Database` · `Transaction`/`TransactionManager` · `ObjectId` · `BlockTable`/`BlockTableRecord` · 심볼 테이블 · `DBDictionary`/`Xrecord` · `ResultBuffer`/`TypedValue` · 엔티티 하위 클래스 · `Point3d`/`Vector3d`/`Matrix3d` · `SelectionFilter`. 핵심 58종(`tools/coverage/dotnet_coverage.py` `CORE`) **100%**.

| 항목 | 값 |
|---|---|
| 플랫폼 | .NET 8 (`net8.0`), Windows·Linux·macOS |
| 패키지 | `AxCad.Sdk` 3.0.0 (`dotnet pack sdk/dotnet/AxCad`) |
| 대상 API | AX-CAD 2.2 REST (`/api/...`, 응답 봉투 `success`/`data`/`error`) |
| 좌표 | WCS, `double`, 도면 단위 mm. 2D 도면이라 Z는 0, 법선은 (0,0,1)만 |
| 각도 | 라디안 (`Arc.StartAngle`, `Matrix3d.Rotation`, `DBText.Rotation`) |
| 허용오차 | `Tolerance.Global` 1e-10, 서버 기하 검증 1e-6 mm |

## 빠른 시작

```csharp
using AxCad;

using var cad = AxCadClient.Local();                        // 이 PC 설치본 (설치 토큰 자동)
var db = await cad.OpenDatabaseAsync(revisionId);            // 2D 리비전을 Database로

using (var tr = db.TransactionManager.StartTransaction())
{
    var bt = (BlockTable)tr.GetObject(db.BlockTableId, OpenMode.ForRead);
    var ms = (BlockTableRecord)tr.GetObject(bt[BlockTableRecord.ModelSpace], OpenMode.ForWrite);
    var lt = (LayerTable)tr.GetObject(db.LayerTableId, OpenMode.ForWrite);
    if (!lt.Has("CUT")) lt.Add(new LayerTableRecord { Name = "CUT", ColorIndex = 1 });

    var hole = new Circle(new Point3d(15, 15, 0), Vector3d.ZAxis, 5) { Layer = "CUT" };
    ms.AppendEntity(hole);
    tr.AddNewlyCreatedDBObject(hole, true);
    tr.Commit();                                             // Commit이 없으면 Dispose에서 되돌림
}
var saved = await db.SaveAsync();                            // 다음 리비전 (revision_id, revision_no)
```

서버 모드: `new AxCadClient("https://cad.example", token: await AxCadClient.LoginTokenAsync(url, id, pw))`.

예제 `examples/PlateDrawing`: 빈 A3 도면 → 200×100 판재 + Ø10 구멍 4개 → 저장.
실행: `dotnet run --project sdk/dotnet/examples/PlateDrawing -- <프로젝트 ID>` (PC 설치본이 켜져 있어야 함).

## 트랜잭션 계약

| 규칙 | 동작 |
|---|---|
| 읽기·쓰기는 트랜잭션 안에서 | `tr.GetObject(id, OpenMode.ForRead / ForWrite)`. 밖에서 → `NO_TRANSACTION` |
| 쓰기는 `ForWrite`로 연 객체만 | 아니면 `NOT_OPEN_FOR_WRITE` (`UpgradeOpen()` 가능). `new`로 만든 객체는 추가 전까지 자유롭게 설정 |
| 잠긴 레이어 | 그 위 엔티티를 `ForWrite`로 열거나 그 레이어에 추가 → `LAYER_LOCKED` |
| 한 번에 하나 | 열린 트랜잭션이 있으면 `StartTransaction` → `TRANSACTION_ACTIVE` (중첩 금지) |
| `Commit` / `Abort`·`Dispose` | 확정 / 시작 시점으로 완전 복원(새 객체·속성·레코드·사전) |
| 저장 | 트랜잭션이 열려 있으면 `SaveAsync` → `TRANSACTION_ACTIVE`. 서버가 한 요청으로 원자 적용 |
| 저장 후 | `IsSaved` — 같은 편집을 두 번 보내지 않음(`DRAWING_SAVED`). 이어서 편집하려면 새 리비전을 엽니다 |

## 객체 모델 (AutoCAD .NET 대응)

| 영역 | 타입과 주요 멤버 |
|---|---|
| 데이터베이스 | `Database`(`BlockTableId`·`LayerTableId`·`LinetypeTableId`·`TextStyleTableId`·`DimStyleTableId`·`RegAppTableId`·`NamedObjectsDictionaryId`·`Clayer`·`Changes()`·`SaveAsync()`), `TransactionManager`, `Transaction`(`GetObject`·`AddNewlyCreatedDBObject`·`Commit`·`Abort`), `ObjectId`(`Handle`·`IsErased`·`ObjectClassName`·`GetObject`), `ObjectIdCollection`, `OpenMode`, `Handle` |
| 공통 | `DBObject`(`ObjectId`·`OwnerId`·`Handle`·`IsErased`·`Erase`·`XData`·`GetXDataForApplication`·`ExtensionDictionary`·`CreateExtensionDictionary`·`UpgradeOpen`) |
| 심볼 테이블 | `SymbolTable`(`Has`·`this[name]`·`Add`·열거) / `SymbolTableRecord`(`Name` — 바꾸면 저장 때 이름 변경), `LayerTable(Record)`(`ColorIndex`·`IsLocked`·`IsOff`·`IsFrozen`·`IsPlottable`·`Linetype`·`LineWeight`·`Description`), `LinetypeTable(Record)`(`NumDashes`·`DashLengthAt`), `TextStyleTable(Record)`(`FileName`·`TextSize`·`XScale`·`ObliquingAngle`), `DimStyleTable(Record)`(`Dimtxt`·`Dimasz`·`Dimexe`·`Dimexo`·`Dimgap`·`Dimdec`·`Dimscale`), `RegAppTable(Record)` |
| 블록 | `BlockTable`, `BlockTableRecord`(`AppendEntity`·`Origin`·열거·`ModelSpace`), `BlockReference`(`Position`·`ScaleFactors`·`Rotation`·`BlockTransform`·`AttributeCollection`), `AttributeDefinition`, `AttributeReference`(`Tag`·`TextString`·`SetAttributeFromBlock`) |
| 엔티티 | `Entity`(`Layer`·`ColorIndex`·`Linetype`·`LinetypeScale`·`LineWeight`·`Transparency`·`Visible`·`GeometricExtents`·`TransformBy`·`GetTransformedCopy`·`Clone`), `Curve`, `Line`, `Circle`, `Arc`, `Polyline`(`AddVertexAt`·벌지·`Area`·`Length`), `DBPoint`, `DBText`, `MText`, `Ellipse`, `Spline`(맞춤점), `Hatch`(`SetHatchPattern`·`AppendLoop`), `Leader`, `MLeader` |
| 치수 | `Dimension`(`DimensionText`·`Measurement`·`Dimasz`·`Dimtxt`·… 재정의), `AlignedDimension`, `RotatedDimension`, `RadialDimension`, `DiametricDimension`, `LineAngularDimension2` |
| 사전·데이터 | `DBDictionary`(`GetAt`·`SetAt`·`Remove`·`Contains`·열거), `Xrecord`(`Data`), `ResultBuffer`, `TypedValue` |
| 기하 | `Point2d`, `Point3d`, `Vector2d`, `Vector3d`, `Matrix3d`(`Displacement`·`Rotation`·`Scaling`·`Mirroring`·`Inverse`), `Extents3d`, `Scale3d`, `Tolerance` |
| 선택 | `db.Editor.SelectAll/SelectWindow/SelectCrossingWindow(SelectionFilter)` → `PromptSelectionResult`(`Status`·`Value.GetObjectIds()`). 필터 그룹 0·2·6·8·62, `*`·`?`·쉼표 목록 |

### AutoCAD와 다른 점 (2D·REST)

- `TransformBy`는 XY 평면의 이동·회전·균일 축척·대칭만 받습니다(그 밖은 `NOT_SUPPORTED`).
- 도면의 기존 블록은 내용이 서버에 있어 이름 변경·삭제만 됩니다(내용 재정의는 2D 화면). 이 세션에서 만든 블록에는 엔티티·속성 정의를 넣습니다. 블록이 자기 자신을 품으면 `SELF_REFERENCE`.
- 레이어 삭제(`Erase`), 중첩 트랜잭션, 폭 있는 폴리라인, 다중 루프 해치, 다중 지시선 여러 개는 지원하지 않습니다.
- 서버가 그리기만 하는 엔티티(문자 없는 치수 블록 등 `geom`이 없는 것)는 모형 공간 목록에 나오지 않고 그대로 보존됩니다.
- 사전이 많아 렌더 응답에 실리지 않은 경우 `NOT_LOADED` — `GET /api/revisions/{id}/dictionaries`를 쓰세요.

## 오류

모든 오류는 `AxCadException(Status, Code, Message)`입니다. API 오류는 서버 코드를 그대로 옮기고, SDK가 보내기 전에 막는 오류는 `Status = 0`입니다.

| 코드 | 뜻 |
|---|---|
| `NO_TRANSACTION` · `TRANSACTION_ACTIVE` · `TRANSACTION_ENDED` · `NOT_OPEN_FOR_WRITE` | 트랜잭션·열기 규칙 위반 |
| `DRAWING_SAVED` · `NO_CHANGES` | 저장한 Database를 다시 씀 / 저장할 변경 없음 |
| `LAYER_LOCKED` · `NO_LAYER` · `BAD_NAME` · `DUPLICATE_RECORD` · `KEY_NOT_FOUND` | 레이어·레코드 규칙 |
| `BAD_GEOMETRY` · `BAD_COLOR` · `BAD_TEXT` · `BAD_XDATA` · `NO_REGAPP` · `SYSTEM_APP` · `BAD_XRECORD` · `TOO_MANY` | 입력 범위 / 저장당 새 객체 10,000개 |
| `NOT_SUPPORTED` · `NOT_LOADED` · `SELF_REFERENCE` · `BLOCK_IN_USE` | 위 "AutoCAD와 다른 점" |
| `BAD_URL` · `NO_LOCAL_TOKEN` · `HTTP_ERROR` · `NOT_JSON` · `NOT_A_FILE` | 연결 규칙·응답 형식 |

## 보안

- 토큰은 https로만 보냅니다. http는 이 PC(127.0.0.1·::1·localhost)일 때만 허용합니다.
- 설치 토큰(`X-AXCAD-Local`)은 이 PC 주소에만 보냅니다. 리다이렉트를 따라가지 않습니다.
- 업로드 파일 이름에서 따옴표와 줄바꿈을 지웁니다. 글꼴은 파일 이름만 보냅니다(경로 제거). DXF 검사(형식·크기·XREF)와 모든 편집 값의 재검증은 서버가 수행합니다.
- 권한은 로그인한 사용자의 역할 그대로입니다. SDK가 권한을 넓히지 않습니다.

## 2.x에서 옮기기

`OpenDrawingAsync`/`Drawing`/`ModelSpace.AddLine(...)`은 3.0에서 `OpenDatabaseAsync`/`Database`/`new Line(...)` + `BlockTableRecord.AppendEntity`로 바뀌었습니다. `Entity.Color` → `ColorIndex`, `Move/Rotate/Scale/Mirror` → `TransformBy(Matrix3d.Displacement/Rotation/Scaling/Mirroring)`, `Layers.Add("X")` → `LayerTable.Add(new LayerTableRecord { Name = "X" })`.

## 테스트

```bash
dotnet run --project sdk/dotnet/AxCad.Tests   # 가짜 API로 연결 규칙·봉투·트랜잭션·객체 모델·기하 검사 (CI)
dotnet pack sdk/dotnet/AxCad -c Release        # NuGet 패키지 (CI)
```
