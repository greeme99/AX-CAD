# AX-LISP vs Autodesk AutoLISP reference

- reference functions: **486**  ·  implemented: **231**  ·  left out on purpose (sandbox): **41**  ·  Express Tools: **39**  ·  not implemented: **175**
- coverage of the in-scope set (reference − sandbox; Express Tools included): **51.9%** (231/445)

## Not implemented

`*pop-error-mode*`, `*push-error-using-command*`, `*push-error-using-stack*`, `acad-pop-dbmod`, `acad-push-dbmod`, `acad_colordlg`, `acad_helpdlg`, `acad_truecolorcli`, `acad_truecolordlg`, `acdimenableupdate`, `alloc`, `atoms-family`, `autoarxload`, `boole`, `client_data_tile`, `cvunit`, `defun-q`, `defun-q-list-ref`, `defun-q-list-set`, `dictadd`, `dictnext`, `dictremove`, `dictrename`, `dictsearch`, `dimx_tile`, `dimy_tile`, `dumpallproperties`, `end_image`, `expand`, `fill_image`, `findtrustedfile`, `functions`, `getcfg`, `getcname`, `getenv`, `getfiled`, `getpropertyvalue`, `grclear`, `grdraw`, `grread`, `grtext`, `grvecs`, `initcommandversion`, `initdia`, `ispropertyreadonly`, `layerstate-addlayers`, `layerstate-compare`, `layerstate-delete`, `layerstate-export`, `layerstate-getlastrestored`, `layerstate-getlayers`, `layerstate-getnames`, `layerstate-has`, `layerstate-import`, `layerstate-importfromdb`, `layerstate-removelayers`, `layerstate-rename`, `layerstate-restore`, `layerstate-save`, `layoutlist`, `mem`, `namedobjdict`, `nentsel`, `nentselp`, `osnap`, `regapp`, `setcfg`, `setenv`, `setpropertyvalue`, `setview`, `showhtmlmodalwindow`, `slide_image`, `snvalid`, `ssnamex`, `start_image`, `tablet`, `tblobjname`, `textbox`, `textpage`, `trace`, `untrace`, `vector_image`, `vl-acad-defun`, `vl-acad-undefun`, `vl-arx-import`, `vl-bb-ref`, `vl-bb-set`, `vl-directory-files`, `vl-file-copy`, `vl-file-delete`, `vl-file-directory-p`, `vl-file-rename`, `vl-file-size`, `vl-file-systime`, `vl-filename-mktemp`, `vl-get-resource`, `vl-list*`, `vl-list-exported-functions`, `vl-list-loaded-vlx`, `vl-member-if-not`, `vl-mkdir`, `vl-propagate`, `vl-sort-i`, `vl-string-elt`, `vl-string-mismatch`, `vl-unload-vlx`, `vl-vlx-loaded-p`, `vlax-curve-getclosestpointto`, `vlax-curve-getclosestpointtoprojection`, `vlax-curve-getdistatpoint`, `vlax-curve-getfirstderiv`, `vlax-curve-getparamatdist`, `vlax-curve-getparamatpoint`, `vlax-curve-getpointatdist`, `vlax-curve-getsecondderiv`, `vlax-curve-isperiodic`, `vlax-curve-isplanar`, `vlax-dump-object`, `vlax-ldata-delete`, `vlax-ldata-get`, `vlax-ldata-list`, `vlax-ldata-put`, `vlax-ldata-test`, `vlax-machine-product-key`, `vlax-map-collection`, `vlax-method-applicable-p`, `vlax-product-key`, `vlax-read-enabled-p`, `vlax-remove-cmd`, `vlax-safearray-get-dim`, `vlax-safearray-get-element`, `vlax-safearray-get-l-bound`, `vlax-safearray-get-u-bound`, `vlax-safearray-put-element`, `vlax-safearray-type`, `vlax-tmatrix`, `vlax-user-product-key`, `vlax-variant-change-type`, `vlax-write-enabled-p`, `vlr-acdb-reactor`, `vlr-beep-reaction`, `vlr-current-reaction-name`, `vlr-data-set`, `vlr-deepclone-reactor`, `vlr-docmanager-reactor`, `vlr-dwg-reactor`, `vlr-dxf-reactor`, `vlr-insert-reactor`, `vlr-linker-reactor`, `vlr-lisp-reactor`, `vlr-miscellaneous-reactor`, `vlr-mouse-reactor`, `vlr-notification`, `vlr-owner-add`, `vlr-owner-remove`, `vlr-pers`, `vlr-pers-list`, `vlr-pers-p`, `vlr-pers-release`, `vlr-reaction-name`, `vlr-reaction-set`, `vlr-reactions`, `vlr-set-notification`, `vlr-sysvar-reactor`, `vlr-toolbar-reactor`, `vlr-trace-reaction`, `vlr-type`, `vlr-types`, `vlr-undo-reactor`, `vlr-wblock-reactor`, `vlr-window-reactor`, `vlr-xref-reactor`, `vports`, `xdroom`, `xdsize`

## Express Tools (in scope, not implemented)

`acet-ent-geomextents`, `acet-file-attr`, `acet-file-copy`, `acet-file-dir`, `acet-file-mkdir`, `acet-file-move`, `acet-file-remove`, `acet-help`, `acet-help-trap`, `acet-layerp-mark`, `acet-layerp-mode`, `acet-laytrans`, `acet-load-expresstools`, `acet-ms-to-ps`, `acet-ps-to-ms`, `acet-ss-drag-move`, `acet-ss-drag-rotate`, `acet-ss-drag-scale`, `acet-str-collate`, `acet-str-equal`, `acet-str-find`, `acet-str-format`, `acet-str-replace`, `acet-str-wcmatch`, `acet-sys-beep`, `acet-sys-command`, `acet-sys-foreground`, `acet-sys-keystate`, `acet-sys-lasterr`, `acet-sys-procid`, `acet-sys-sleep`, `acet-sys-spawn`, `acet-sys-term`, `acet-sys-wait`, `acet-ui-message`, `acet-ui-pickdir`, `acet-ui-progress`, `acet-ui-status`, `acet-ui-txted`

## Left out on purpose (sandbox)

`acet-file-chdir`, `acet-file-cwd`, `acet-file-rmdir`, `acet-ini-get`, `acet-ini-set`, `acet-reg-del`, `acet-reg-get`, `acet-reg-machine-prodkey`, `acet-reg-prodkey`, `acet-reg-put`, `acet-reg-user-prodkey`, `arx`, `arxload`, `arxunload`, `graphscr`, `help`, `menucmd`, `menugroup`, `setfunhelp`, `startapp`, `textscr`, `vl-doc-export`, `vl-doc-import`, `vl-doc-ref`, `vl-doc-set`, `vl-exit-with-error`, `vl-exit-with-value`, `vl-load-all`, `vl-registry-delete`, `vl-registry-descendents`, `vl-registry-read`, `vl-registry-write`, `vl-vbaload`, `vl-vbarun`, `vlax-add-cmd`, `vlax-create-object`, `vlax-get-object`, `vlax-get-or-create-object`, `vlax-import-type-library`, `vlax-typeinfo-available-p`, `vlisp-compile`


---

# AX-CAD object model (VBA · vla-) vs Autodesk ActiveX reference

- **plan §22.2 scope** (members of the 50 objects AX-CAD has): **156/2414 = 6.5%** (target 85%)

| object | members done / total |
|---|---|
| Application | 5/62 |
| Arc | 21/48 |
| Attribute | 0/64 |
| AttributeReference | 0/60 |
| Block | 0/84 |
| BlockReference | 18/58 |
| Blocks | 0/13 |
| Circle | 17/44 |
| Dictionaries | 0/14 |
| Dictionary | 0/22 |
| DimAligned | 0/131 |
| DimAngular | 0/105 |
| DimDiametric | 0/115 |
| DimRadial | 0/112 |
| DimRotated | 0/129 |
| DimStyle | 0/14 |
| DimStyles | 0/14 |
| Document | 6/102 |
| DwfUnderlay | 0/52 |
| Ellipse | 20/51 |
| Group | 0/27 |
| Groups | 0/14 |
| Hatch | 0/63 |
| Layer | 7/25 |
| Layers | 6/15 |
| Layout | 0/46 |
| Layouts | 0/13 |
| Leader | 0/51 |
| LightweightPolyline | 0/53 |
| Line | 17/44 |
| Linetype | 0/14 |
| Linetypes | 0/15 |
| MLeader | 0/88 |
| MText | 0/50 |
| ModelSpace Entities | 0/71 |
| PViewport | 0/70 |
| PaperSpace Entities | 0/69 |
| PdfUnderlay | 0/52 |
| Point | 13/39 |
| Polyline | 0/54 |
| RegisteredApplication | 0/13 |
| RegisteredApplications | 0/14 |
| SelectionSet | 0/15 |
| SelectionSets | 0/4 |
| Spline | 0/68 |
| Text | 16/51 |
| TextStyle | 0/22 |
| TextStyles | 0/14 |
| Utility | 10/30 |
| XRecord | 0/16 |

## Whole reference (for scale)

| kind | reference | implemented | left out on purpose | not implemented | coverage of in-scope |
|---|---|---|---|---|---|
| Objects | 154 | 12 | 25 | 117 | **9.3%** |
| Methods | 485 | 28 | 25 | 432 | **6.1%** |
| Properties | 853 | 38 | 0 | 815 | **4.5%** |
| Events | 41 | 0 | 0 | 41 | **0.0%** |

Members match by name only (see the module docstring).

## Objects: implemented

`Application`, `Arc`, `BlockReference`, `Circle`, `Document`, `Ellipse`, `Layer`, `Layers`, `Line`, `Point`, `Text`, `Utility`

## Methods: implemented

`Add`, `AddArc`, `AddCircle`, `AddEllipse`, `AddLightWeightPolyline`, `AddLine`, `AddPoint`, `AddPolyline`, `AddText`, `AngleToReal`, `Copy`, `Delete`, `Erase`, `GetAngle`, `GetDistance`, `GetInteger`, `GetOrientation`, `GetPoint`, `GetReal`, `GetString`, `Item`, `Mirror`, `Move`, `PolarPoint`, `Prompt`, `Rotate`, `ScaleEntity`, `Update`

## Properties: implemented

`ActiveDocument`, `ActiveLayer`, `Angle`, `Application`, `ArcLength`, `Area`, `Center`, `Circumference`, `Closed`, `Color`, `Coordinates`, `Count`, `Delta`, `Diameter`, `EffectiveName`, `EndAngle`, `EndPoint`, `Handle`, `Height`, `InsertionPoint`, `Layer`, `Layers`, `Length`, `Linetype`, `Lock`, `MajorAxis`, `ModelSpace`, `Name`, `ObjectName`, `Radius`, `RadiusRatio`, `Rotation`, `StartAngle`, `StartPoint`, `TextString`, `TotalAngle`, `XScaleFactor`, `YScaleFactor`

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

