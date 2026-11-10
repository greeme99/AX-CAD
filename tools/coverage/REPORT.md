# AX-LISP vs Autodesk AutoLISP reference

- reference functions: **486**  ·  implemented: **231**  ·  left out on purpose (sandbox): **30**  ·  Express Tools: **50**  ·  not implemented: **175**
- coverage of the in-scope set (reference − sandbox − Express): **56.9%** (231/406)

## Not implemented

`*pop-error-mode*`, `*push-error-using-command*`, `*push-error-using-stack*`, `acad-pop-dbmod`, `acad-push-dbmod`, `acad_colordlg`, `acad_helpdlg`, `acad_truecolorcli`, `acad_truecolordlg`, `acdimenableupdate`, `alloc`, `atoms-family`, `autoarxload`, `boole`, `client_data_tile`, `cvunit`, `defun-q`, `defun-q-list-ref`, `defun-q-list-set`, `dictadd`, `dictnext`, `dictremove`, `dictrename`, `dictsearch`, `dimx_tile`, `dimy_tile`, `dumpallproperties`, `end_image`, `expand`, `fill_image`, `findtrustedfile`, `functions`, `getcfg`, `getcname`, `getenv`, `getfiled`, `getpropertyvalue`, `grclear`, `grdraw`, `grread`, `grtext`, `grvecs`, `initcommandversion`, `initdia`, `ispropertyreadonly`, `layerstate-addlayers`, `layerstate-compare`, `layerstate-delete`, `layerstate-export`, `layerstate-getlastrestored`, `layerstate-getlayers`, `layerstate-getnames`, `layerstate-has`, `layerstate-import`, `layerstate-importfromdb`, `layerstate-removelayers`, `layerstate-rename`, `layerstate-restore`, `layerstate-save`, `layoutlist`, `mem`, `namedobjdict`, `nentsel`, `nentselp`, `osnap`, `regapp`, `setcfg`, `setenv`, `setpropertyvalue`, `setview`, `showhtmlmodalwindow`, `slide_image`, `snvalid`, `ssnamex`, `start_image`, `tablet`, `tblobjname`, `textbox`, `textpage`, `trace`, `untrace`, `vector_image`, `vl-acad-defun`, `vl-acad-undefun`, `vl-arx-import`, `vl-bb-ref`, `vl-bb-set`, `vl-directory-files`, `vl-file-copy`, `vl-file-delete`, `vl-file-directory-p`, `vl-file-rename`, `vl-file-size`, `vl-file-systime`, `vl-filename-mktemp`, `vl-get-resource`, `vl-list*`, `vl-list-exported-functions`, `vl-list-loaded-vlx`, `vl-member-if-not`, `vl-mkdir`, `vl-propagate`, `vl-sort-i`, `vl-string-elt`, `vl-string-mismatch`, `vl-unload-vlx`, `vl-vlx-loaded-p`, `vlax-curve-getclosestpointto`, `vlax-curve-getclosestpointtoprojection`, `vlax-curve-getdistatpoint`, `vlax-curve-getfirstderiv`, `vlax-curve-getparamatdist`, `vlax-curve-getparamatpoint`, `vlax-curve-getpointatdist`, `vlax-curve-getsecondderiv`, `vlax-curve-isperiodic`, `vlax-curve-isplanar`, `vlax-dump-object`, `vlax-ldata-delete`, `vlax-ldata-get`, `vlax-ldata-list`, `vlax-ldata-put`, `vlax-ldata-test`, `vlax-machine-product-key`, `vlax-map-collection`, `vlax-method-applicable-p`, `vlax-product-key`, `vlax-read-enabled-p`, `vlax-remove-cmd`, `vlax-safearray-get-dim`, `vlax-safearray-get-element`, `vlax-safearray-get-l-bound`, `vlax-safearray-get-u-bound`, `vlax-safearray-put-element`, `vlax-safearray-type`, `vlax-tmatrix`, `vlax-user-product-key`, `vlax-variant-change-type`, `vlax-write-enabled-p`, `vlr-acdb-reactor`, `vlr-beep-reaction`, `vlr-current-reaction-name`, `vlr-data-set`, `vlr-deepclone-reactor`, `vlr-docmanager-reactor`, `vlr-dwg-reactor`, `vlr-dxf-reactor`, `vlr-insert-reactor`, `vlr-linker-reactor`, `vlr-lisp-reactor`, `vlr-miscellaneous-reactor`, `vlr-mouse-reactor`, `vlr-notification`, `vlr-owner-add`, `vlr-owner-remove`, `vlr-pers`, `vlr-pers-list`, `vlr-pers-p`, `vlr-pers-release`, `vlr-reaction-name`, `vlr-reaction-set`, `vlr-reactions`, `vlr-set-notification`, `vlr-sysvar-reactor`, `vlr-toolbar-reactor`, `vlr-trace-reaction`, `vlr-type`, `vlr-types`, `vlr-undo-reactor`, `vlr-wblock-reactor`, `vlr-window-reactor`, `vlr-xref-reactor`, `vports`, `xdroom`, `xdsize`

## Left out on purpose (sandbox)

`arx`, `arxload`, `arxunload`, `graphscr`, `help`, `menucmd`, `menugroup`, `setfunhelp`, `startapp`, `textscr`, `vl-doc-export`, `vl-doc-import`, `vl-doc-ref`, `vl-doc-set`, `vl-exit-with-error`, `vl-exit-with-value`, `vl-load-all`, `vl-registry-delete`, `vl-registry-descendents`, `vl-registry-read`, `vl-registry-write`, `vl-vbaload`, `vl-vbarun`, `vlax-add-cmd`, `vlax-create-object`, `vlax-get-object`, `vlax-get-or-create-object`, `vlax-import-type-library`, `vlax-typeinfo-available-p`, `vlisp-compile`


---

# AX-CAD object model (VBA · vla-) vs Autodesk ActiveX reference

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

