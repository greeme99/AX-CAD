# AX-LISP vs Autodesk AutoLISP reference

- reference functions: **486**  ·  implemented: **420**  ·  left out on purpose (sandbox): **58**  ·  Express Tools: **1**  ·  not implemented: **7**
- coverage of the in-scope set (reference − sandbox; Express Tools included): **98.1%** (420/428)

## Not implemented

`dumpallproperties`, `getpropertyvalue`, `ispropertyreadonly`, `layerstate-importfromdb`, `setpropertyvalue`, `vlax-dump-object`, `vlax-method-applicable-p`

## Express Tools (in scope, not implemented)

`acet-laytrans`

## Left out on purpose (sandbox)

`acad_helpdlg`, `acet-file-chdir`, `acet-file-cwd`, `acet-file-rmdir`, `acet-help`, `acet-help-trap`, `acet-ini-get`, `acet-ini-set`, `acet-reg-del`, `acet-reg-get`, `acet-reg-machine-prodkey`, `acet-reg-prodkey`, `acet-reg-put`, `acet-reg-user-prodkey`, `acet-sys-command`, `acet-sys-foreground`, `acet-sys-procid`, `acet-sys-spawn`, `acet-sys-term`, `acet-sys-wait`, `arx`, `arxload`, `arxunload`, `autoarxload`, `functions`, `graphscr`, `help`, `menucmd`, `menugroup`, `setfunhelp`, `showhtmlmodalwindow`, `startapp`, `textpage`, `textscr`, `vl-arx-import`, `vl-doc-export`, `vl-doc-import`, `vl-doc-ref`, `vl-doc-set`, `vl-exit-with-error`, `vl-exit-with-value`, `vl-load-all`, `vl-registry-delete`, `vl-registry-descendents`, `vl-registry-read`, `vl-registry-write`, `vl-vbaload`, `vl-vbarun`, `vlax-add-cmd`, `vlax-create-object`, `vlax-get-object`, `vlax-get-or-create-object`, `vlax-import-type-library`, `vlax-machine-product-key`, `vlax-product-key`, `vlax-typeinfo-available-p`, `vlax-user-product-key`, `vlisp-compile`


---

# AX-CAD object model (VBA · vla-) vs Autodesk ActiveX reference

- **plan §22.2 scope** (members of the 50 objects AX-CAD has): **215/2414 = 8.9%** (target 85%)

| object | members done / total |
|---|---|
| Application | 7/62 |
| Arc | 25/48 |
| Attribute | 0/64 |
| AttributeReference | 0/60 |
| Block | 0/84 |
| BlockReference | 22/58 |
| Blocks | 0/13 |
| Circle | 21/44 |
| Dictionaries | 0/14 |
| Dictionary | 0/22 |
| DimAligned | 0/131 |
| DimAngular | 0/105 |
| DimDiametric | 0/115 |
| DimRadial | 0/112 |
| DimRotated | 0/129 |
| DimStyle | 0/14 |
| DimStyles | 0/14 |
| Document | 7/102 |
| DwfUnderlay | 0/52 |
| Ellipse | 24/51 |
| Group | 0/27 |
| Groups | 0/14 |
| Hatch | 0/63 |
| Layer | 12/25 |
| Layers | 6/15 |
| Layout | 0/46 |
| Layouts | 0/13 |
| Leader | 0/51 |
| LightweightPolyline | 0/53 |
| Line | 21/44 |
| Linetype | 0/14 |
| Linetypes | 0/15 |
| MLeader | 0/88 |
| MText | 22/50 |
| ModelSpace Entities | 0/71 |
| PViewport | 0/70 |
| PaperSpace Entities | 0/69 |
| PdfUnderlay | 0/52 |
| Point | 17/39 |
| Polyline | 0/54 |
| RegisteredApplication | 0/13 |
| RegisteredApplications | 0/14 |
| SelectionSet | 0/15 |
| SelectionSets | 0/4 |
| Spline | 0/68 |
| Text | 21/51 |
| TextStyle | 0/22 |
| TextStyles | 0/14 |
| Utility | 10/30 |
| XRecord | 0/16 |

## Whole reference (for scale)

| kind | reference | implemented | left out on purpose | not implemented | coverage of in-scope |
|---|---|---|---|---|---|
| Objects | 154 | 13 | 25 | 116 | **10.1%** |
| Methods | 485 | 29 | 25 | 431 | **6.3%** |
| Properties | 853 | 50 | 0 | 803 | **5.9%** |
| Events | 41 | 0 | 0 | 41 | **0.0%** |

Members match by name only (see the module docstring).

## Objects: implemented

`Application`, `Arc`, `BlockReference`, `Circle`, `Document`, `Ellipse`, `Layer`, `Layers`, `Line`, `MText`, `Point`, `Text`, `Utility`

## Methods: implemented

`Add`, `AddArc`, `AddCircle`, `AddEllipse`, `AddLightWeightPolyline`, `AddLine`, `AddMText`, `AddPoint`, `AddPolyline`, `AddText`, `AngleToReal`, `Copy`, `Delete`, `Erase`, `GetAngle`, `GetDistance`, `GetInteger`, `GetOrientation`, `GetPoint`, `GetReal`, `GetString`, `Item`, `Mirror`, `Move`, `PolarPoint`, `Prompt`, `Rotate`, `ScaleEntity`, `Update`

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

