# AX-LISP vs Autodesk AutoLISP reference

- reference functions: **486**  ·  implemented: **426**  ·  left out on purpose (sandbox): **58**  ·  Express Tools: **1**  ·  not implemented: **1**
- coverage of the in-scope set (reference − sandbox; Express Tools included): **99.5%** (426/428)

## Not implemented

`layerstate-importfromdb`

## Express Tools (in scope, not implemented)

`acet-laytrans`

## Left out on purpose (sandbox)

`acad_helpdlg`, `acet-file-chdir`, `acet-file-cwd`, `acet-file-rmdir`, `acet-help`, `acet-help-trap`, `acet-ini-get`, `acet-ini-set`, `acet-reg-del`, `acet-reg-get`, `acet-reg-machine-prodkey`, `acet-reg-prodkey`, `acet-reg-put`, `acet-reg-user-prodkey`, `acet-sys-command`, `acet-sys-foreground`, `acet-sys-procid`, `acet-sys-spawn`, `acet-sys-term`, `acet-sys-wait`, `arx`, `arxload`, `arxunload`, `autoarxload`, `functions`, `graphscr`, `help`, `menucmd`, `menugroup`, `setfunhelp`, `showhtmlmodalwindow`, `startapp`, `textpage`, `textscr`, `vl-arx-import`, `vl-doc-export`, `vl-doc-import`, `vl-doc-ref`, `vl-doc-set`, `vl-exit-with-error`, `vl-exit-with-value`, `vl-load-all`, `vl-registry-delete`, `vl-registry-descendents`, `vl-registry-read`, `vl-registry-write`, `vl-vbaload`, `vl-vbarun`, `vlax-add-cmd`, `vlax-create-object`, `vlax-get-object`, `vlax-get-or-create-object`, `vlax-import-type-library`, `vlax-machine-product-key`, `vlax-product-key`, `vlax-typeinfo-available-p`, `vlax-user-product-key`, `vlisp-compile`


---

# AX-CAD object model (VBA · vla-) vs Autodesk ActiveX reference

- **plan §22.2 scope** (members of the 50 objects AX-CAD has): **1935/2271 = 85.2%** (target 85%)
- of these, **limited** (AX-CAD keeps no such data: the default is read and only it is taken): 352; full: 1583 = 69.7%

| object | members done / total | limited |
|---|---|---|
| Application | 19/25 | 1 |
| Arc | 48/48 | 4 |
| Attribute | 64/64 | 34 |
| AttributeReference | 60/60 | 42 |
| Block | 48/59 | 6 |
| BlockReference | 58/58 | 7 |
| Blocks | 13/13 | 0 |
| Circle | 44/44 | 4 |
| Dictionaries | 14/14 | 0 |
| Dictionary | 22/22 | 1 |
| DimAligned | 126/131 | 13 |
| DimAngular | 104/105 | 13 |
| DimDiametric | 114/115 | 14 |
| DimRadial | 111/112 | 14 |
| DimRotated | 125/129 | 13 |
| DimStyle | 14/14 | 1 |
| DimStyles | 14/14 | 0 |
| Document | 57/66 | 13 |
| DwfUnderlay | 0/52 | 0 |
| Ellipse | 51/51 | 4 |
| Group | 27/27 | 2 |
| Groups | 14/14 | 0 |
| Hatch | 63/63 | 21 |
| Layer | 25/25 | 3 |
| Layers | 15/15 | 0 |
| Layout | 46/46 | 25 |
| Layouts | 12/13 | 0 |
| Leader | 51/51 | 13 |
| LightweightPolyline | 53/53 | 9 |
| Line | 44/44 | 4 |
| Linetype | 14/14 | 0 |
| Linetypes | 14/15 | 0 |
| MLeader | 88/88 | 44 |
| MText | 50/50 | 9 |
| ModelSpace Entities | 41/51 | 6 |
| PViewport | 0/70 | 0 |
| PaperSpace Entities | 0/49 | 0 |
| PdfUnderlay | 0/52 | 0 |
| Point | 39/39 | 4 |
| Polyline | 0/54 | 0 |
| RegisteredApplication | 13/13 | 0 |
| RegisteredApplications | 14/14 | 0 |
| SelectionSet | 15/15 | 1 |
| SelectionSets | 4/4 | 0 |
| Spline | 59/68 | 12 |
| Text | 51/51 | 12 |
| TextStyle | 22/22 | 2 |
| TextStyles | 14/14 | 0 |
| Utility | 25/25 | 0 |
| XRecord | 16/16 | 1 |

## Whole reference (for scale)

| kind | reference | implemented | left out on purpose | not implemented | coverage of in-scope |
|---|---|---|---|---|---|
| Objects | 154 | 13 | 25 | 116 | **10.1%** |
| Methods | 485 | 44 | 25 | 416 | **9.6%** |
| Properties | 853 | 50 | 0 | 803 | **5.9%** |
| Events | 41 | 0 | 0 | 41 | **0.0%** |

Members match by name only (see the module docstring).

## Objects: implemented

`Application`, `Arc`, `BlockReference`, `Circle`, `Document`, `Ellipse`, `Layer`, `Layers`, `Line`, `MText`, `Point`, `Text`, `Utility`

## Methods: implemented

`Add`, `AddArc`, `AddCircle`, `AddEllipse`, `AddLightWeightPolyline`, `AddLine`, `AddMText`, `AddPoint`, `AddPolyline`, `AddText`, `AngleFromXAxis`, `AngleToReal`, `AngleToString`, `Copy`, `CreateTypedArray`, `Delete`, `DistanceToReal`, `Erase`, `GetAngle`, `GetCorner`, `GetDistance`, `GetEntity`, `GetInput`, `GetInteger`, `GetKeyword`, `GetObjectIdString`, `GetOrientation`, `GetPoint`, `GetReal`, `GetString`, `GetSubEntity`, `InitializeUserInput`, `Item`, `Mirror`, `Move`, `PolarPoint`, `Prompt`, `RealToString`, `Rotate`, `ScaleEntity`, `SendModelessOperationEnded`, `SendModelessOperationStart`, `TranslateCoordinates`, `Update`

## Properties: implemented

`ActiveDocument`, `ActiveLayer`, `Angle`, `Application`, `ArcLength`, `Area`, `AttachmentPoint`, `Center`, `Circumference`, `Closed`, `Color`, `Coordinates`, `Count`, `Delta`, `Description`, `Diameter`, `EffectiveName`, `EndAngle`, `EndPoint`, `EntityTransparency`, `FontFile`, `Freeze`, `Handle`, `Height`, `InsertionPoint`, `Layer`, `LayerOn`, `Layers`, `Length`, `Linetype`, `LinetypeScale`, `Lineweight`, `Lock`, `MajorAxis`, `ModelSpace`, `Name`, `ObjectName`, `ObliqueAngle`, `Plottable`, `Radius`, `RadiusRatio`, `Rotation`, `StartAngle`, `StartPoint`, `TextString`, `TotalAngle`, `Visible`, `Width`, `XScaleFactor`, `YScaleFactor`

## Events: implemented

—


---

# AX-CAD C# SDK vs AutoCAD .NET API

- core set: **10/58 = 17.2%**

| namespace | classes | structures | enums | in the SDK |
|---|---|---|---|---|
| DatabaseServices | 404 | 38 | 181 | 10 |
| Geometry | 65 | 22 | 8 | 0 |
| EditorInput | 91 | 4 | 14 | 0 |
| ApplicationServices | 45 | 7 | 2 | 0 |
| Runtime | 40 | 6 | 7 | 0 |

## Core: in the SDK

`Transaction`, `Entity`, `Line`, `Circle`, `Arc`, `Polyline`, `DBPoint`, `DBText`, `BlockTableRecord`, `LayerTable`

## Core: missing

`Database`, `TransactionManager`, `ObjectId`, `ObjectIdCollection`, `DBObject`, `Curve`, `MText`, `Ellipse`, `Spline`, `Hatch`, `Leader`, `MLeader`, `BlockTable`, `BlockReference`, `AttributeDefinition`, `AttributeReference`, `LayerTableRecord`, `LinetypeTable`, `LinetypeTableRecord`, `TextStyleTable`, `TextStyleTableRecord`, `DimStyleTable`, `DimStyleTableRecord`, `RegAppTable`, `RegAppTableRecord`, `SymbolTable`, `SymbolTableRecord`, `DBDictionary`, `Xrecord`, `ResultBuffer`, `TypedValue`, `Dimension`, `AlignedDimension`, `RotatedDimension`, `RadialDimension`, `DiametricDimension`, `LineAngularDimension2`, `OpenMode`, `Point2d`, `Point3d`, `Vector2d`, `Vector3d`, `Matrix3d`, `Extents3d`, `Tolerance`, `Scale3d`, `SelectionFilter`, `PromptSelectionResult`

