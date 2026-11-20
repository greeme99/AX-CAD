"""CAM C1~C4 API routes (plan.md §24)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from backend.api.auth import CurrentUser, Db, get_document, need
from backend.api.common import ApiError, body
from backend.db.models import CamOperation, CamSetup, CamToolRow, Feature, MasterVersion
from core.cam.laser import laser_toolpath
from core.cam.mill25d import ToolpathResult, ToolpathSegment, contour, drill, pocket
from core.cam.post import (
    post_fanuc_mill,
    post_generic_laser,
    sanitize_comment,
    validate_program_name,
)
from core.cam.recognize import recognize_features
from core.cam.tools import DEFAULT_TOOLS, CamError, CamTool
from core.geometry.errors import GeomError
from core.geometry.serialize import from_brep

router = APIRouter(tags=["cam"])


def _var() -> Path:
    from backend.api import main  # late: main imports this module, tests patch main.VAR_DIR

    return main.VAR_DIR


class SetupCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    feature_id: int | None = None
    revision_id: str | None = None
    kind: Literal["MILL_25D", "LASER_2D"] = "MILL_25D"
    post_target: Literal["FANUC_MILL", "GENERIC_LASER"] = "FANUC_MILL"
    wcs_origin: list[float] = Field(
        default_factory=lambda: [0.0, 0.0, 0.0], min_length=3, max_length=3
    )
    safe_z_mm: float = Field(default=20.0, gt=0, le=500.0)
    stock_margin_mm: float = Field(default=0.0, ge=0, le=100.0)


class OperationCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op_type: Literal["CONTOUR", "POCKET", "DRILL", "LASER_CUT"]
    tool_code: str = Field(min_length=1, max_length=32)
    params: dict[str, Any] = Field(default_factory=dict)


def _setup_row(db: Db, setup_id: int) -> CamSetup:
    row = db.get(CamSetup, setup_id)
    if row is None:
        raise ApiError(404, "CAM_SETUP_NOT_FOUND", "CAM 셋업을 찾을 수 없습니다")
    return row


def _resolve_tool(db: Db, tool_code: str) -> CamTool:
    active = db.scalar(select(MasterVersion).where(MasterVersion.status == "ACTIVE"))
    if active is not None:
        row = db.scalar(
            select(CamToolRow).where(
                CamToolRow.version_id == active.version_id,
                CamToolRow.tool_code == tool_code,
            )
        )
        if row is not None:
            return CamTool(
                tool_code=row.tool_code,
                tool_type=row.tool_type,
                diameter_mm=float(row.diameter_mm),
                flutes=row.flutes,
                material_group=row.material_group,
                spindle_rpm=row.spindle_rpm,
                feed_xy_mm_min=float(row.feed_xy_mm_min),
                feed_z_mm_min=float(row.feed_z_mm_min),
                stepdown_mm=float(row.stepdown_mm),
                stepover_ratio=float(row.stepover_ratio),
                laser_power_pct=None if row.laser_power_pct is None else float(row.laser_power_pct),
            )
    for t in DEFAULT_TOOLS:
        if t.tool_code == tool_code:
            return t
    raise ApiError(404, "CAM_TOOL_NOT_FOUND", f"공구 '{tool_code}'를 찾을 수 없습니다")


def _toolpath_file(setup_id: int, op_id: int) -> Path:
    d = _var() / "cam" / str(setup_id)
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{op_id}.json"


def _compute_operation(setup: CamSetup, op: CamOperation, tool: CamTool) -> ToolpathResult:
    p = op.params or {}
    safe_z = float(setup.safe_z_mm)
    try:
        if op.op_type == "CONTOUR":
            loops = [[(float(pt[0]), float(pt[1])) for pt in lp] for lp in p.get("loops", [])]
            return contour(
                loops,
                tool,
                depth=float(p.get("depth_mm", 5.0)),
                stepdown=p.get("stepdown_mm"),
                side=p.get("side", "OUTSIDE"),
                lead_in_r=float(p.get("lead_in_r", 0.0)),
                safe_z=safe_z,
                top_z=float(p.get("top_z", 0.0)),
            )
        if op.op_type == "POCKET":
            outer = [(float(pt[0]), float(pt[1])) for pt in p.get("outer", [])]
            islands = [[(float(pt[0]), float(pt[1])) for pt in isl] for isl in p.get("islands", [])]
            return pocket(
                outer,
                islands,
                tool,
                depth=float(p.get("depth_mm", 5.0)),
                stepdown=p.get("stepdown_mm"),
                stepover=p.get("stepover_ratio"),
                safe_z=safe_z,
                top_z=float(p.get("top_z", 0.0)),
            )
        if op.op_type == "DRILL":
            pts = [(float(pt[0]), float(pt[1])) for pt in p.get("points", [])]
            return drill(
                pts,
                tool,
                depth=float(p.get("depth_mm", 10.0)),
                peck_mm=p.get("peck_mm"),
                safe_z=safe_z,
                top_z=float(p.get("top_z", 0.0)),
            )
        if op.op_type == "LASER_CUT":
            loops = [[(float(pt[0]), float(pt[1])) for pt in lp] for lp in p.get("loops", [])]
            res, _order = laser_toolpath(
                loops,
                kerf_mm=float(p.get("kerf_mm", tool.diameter_mm)),
                lead_in_mm=float(p.get("lead_in_mm", 2.0)),
                power_pct=float(p.get("laser_power_pct", tool.laser_power_pct or 80.0)),
                feed_xy=float(p.get("feed_xy_mm_min", tool.feed_xy_mm_min)),
                common_line=bool(p.get("common_line", False)),
            )
            return res
    except CamError as e:
        raise ApiError(422, e.code, e.message) from e
    except (TypeError, ValueError, KeyError) as e:
        raise ApiError(422, "CAM_INVALID_PARAM", str(e)) from e
    raise ApiError(422, "CAM_INVALID_PARAM", f"unsupported op_type: {op.op_type}")


def _op_dict(op: CamOperation) -> dict[str, Any]:
    return {
        "operation_id": op.operation_id,
        "setup_id": op.setup_id,
        "seq": op.seq,
        "op_type": op.op_type,
        "tool_code": op.tool_code,
        "params": op.params,
        "status": op.status,
        "error_code": op.error_code,
        "cycle_time_s": None if op.cycle_time_s is None else float(op.cycle_time_s),
        "cut_length_mm": None if op.cut_length_mm is None else float(op.cut_length_mm),
        "rapid_length_mm": None if op.rapid_length_mm is None else float(op.rapid_length_mm),
    }


def _setup_dict(setup: CamSetup, ops: list[CamOperation]) -> dict[str, Any]:
    return {
        "setup_id": setup.setup_id,
        "document_id": setup.document_id,
        "feature_id": setup.feature_id,
        "revision_id": setup.revision_id,
        "kind": setup.kind,
        "post_target": setup.post_target,
        "wcs_origin": setup.wcs_origin,
        "safe_z_mm": float(setup.safe_z_mm),
        "stock_margin_mm": float(setup.stock_margin_mm),
        "operations": [_op_dict(o) for o in ops],
    }


@router.get("/api/cam/tools")
def list_cam_tools(db: Db, user: CurrentUser) -> dict[str, Any]:
    active = db.scalar(select(MasterVersion).where(MasterVersion.status == "ACTIVE"))
    tools: list[dict[str, Any]] = []
    if active is not None:
        rows = db.scalars(
            select(CamToolRow)
            .where(CamToolRow.version_id == active.version_id)
            .order_by(CamToolRow.tool_code)
        ).all()
        for r in rows:
            tools.append(
                {
                    "tool_id": r.tool_id,
                    "tool_code": r.tool_code,
                    "tool_type": r.tool_type,
                    "diameter_mm": float(r.diameter_mm),
                    "flutes": r.flutes,
                    "material_group": r.material_group,
                    "spindle_rpm": r.spindle_rpm,
                    "feed_xy_mm_min": float(r.feed_xy_mm_min),
                    "feed_z_mm_min": float(r.feed_z_mm_min),
                    "stepdown_mm": float(r.stepdown_mm),
                    "stepover_ratio": float(r.stepover_ratio),
                    "laser_power_pct": None
                    if r.laser_power_pct is None
                    else float(r.laser_power_pct),
                }
            )
    if not tools:
        tools = [t.to_dict() for t in DEFAULT_TOOLS]
    return body({"tools": tools})


@router.post("/api/documents/{doc_id}/cam/setups", status_code=201)
def create_cam_setup(
    doc_id: int, payload: SetupCreateIn, db: Db, user: CurrentUser
) -> dict[str, Any]:
    doc = get_document(db, user, doc_id, True)
    need(user, "DESIGNER", "ESTIMATOR")
    row = CamSetup(
        document_id=doc.document_id,
        feature_id=payload.feature_id,
        revision_id=payload.revision_id,
        kind=payload.kind,
        post_target=payload.post_target,
        wcs_origin=payload.wcs_origin,
        safe_z_mm=Decimal(str(payload.safe_z_mm)),
        stock_margin_mm=Decimal(str(payload.stock_margin_mm)),
        created_by=user.user_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return body(_setup_dict(row, []))


@router.get("/api/documents/{doc_id}/cam/setups")
def list_cam_setups(doc_id: int, db: Db, user: CurrentUser) -> dict[str, Any]:
    doc = get_document(db, user, doc_id)
    setups = db.scalars(
        select(CamSetup).where(CamSetup.document_id == doc.document_id).order_by(CamSetup.setup_id)
    ).all()
    out = []
    for s in setups:
        ops = list(
            db.scalars(
                select(CamOperation)
                .where(CamOperation.setup_id == s.setup_id)
                .order_by(CamOperation.seq, CamOperation.operation_id)
            ).all()
        )
        out.append(_setup_dict(s, ops))
    return body({"setups": out})


@router.post("/api/cam/setups/{setup_id}/recognize")
def recognize_cam_setup(setup_id: int, db: Db, user: CurrentUser) -> dict[str, Any]:
    setup = _setup_row(db, setup_id)
    get_document(db, user, setup.document_id, True)
    from backend.api.routes_model import _feature_brep  # late: avoid import cycle

    feat = db.get(Feature, setup.feature_id) if setup.feature_id is not None else None
    if feat is None or feat.document_id != setup.document_id or feat.status != "OK":
        raise ApiError(422, "CAM_NO_BREP", "3D 피처 형상(BREP)이 연결되지 않았습니다")
    try:
        shape = from_brep(_feature_brep(db, feat, {}))
        rec = recognize_features(shape)
    except GeomError as e:
        raise ApiError(422, e.code, str(e)) from e
    return body({"setup_id": setup.setup_id, **rec})


@router.post("/api/cam/setups/{setup_id}/operations", status_code=201)
def add_cam_operation(
    setup_id: int, payload: OperationCreateIn, db: Db, user: CurrentUser
) -> dict[str, Any]:
    setup = _setup_row(db, setup_id)
    get_document(db, user, setup.document_id, True)
    tool = _resolve_tool(db, payload.tool_code)
    existing = db.scalars(select(CamOperation).where(CamOperation.setup_id == setup.setup_id)).all()
    next_seq = max((o.seq for o in existing), default=-1) + 1

    op = CamOperation(
        setup_id=setup.setup_id,
        seq=next_seq,
        op_type=payload.op_type,
        tool_code=tool.tool_code,
        params=payload.params,
        status="PENDING",
    )
    db.add(op)
    db.flush()

    res = _compute_operation(setup, op, tool)
    tp_path = _toolpath_file(setup.setup_id, op.operation_id)
    tp_path.write_text(json.dumps(res.to_dict(), allow_nan=False), encoding="utf-8")

    op.status = "OK"
    op.error_code = None
    op.cycle_time_s = Decimal(f"{res.cycle_time_s:.2f}")
    op.cut_length_mm = Decimal(f"{res.cut_length_mm:.2f}")
    op.rapid_length_mm = Decimal(f"{res.rapid_length_mm:.2f}")
    op.toolpath_path = str(tp_path.relative_to(_var()))
    db.commit()
    db.refresh(op)
    return body(_op_dict(op))


def _load_toolpath(op: CamOperation) -> ToolpathResult:
    if not op.toolpath_path:
        return ToolpathResult()
    p = _var() / op.toolpath_path
    if not p.is_file():
        return ToolpathResult()
    raw = json.loads(p.read_text(encoding="utf-8"))
    segs = [
        ToolpathSegment(
            kind=s["kind"],
            start=(float(s["start"][0]), float(s["start"][1]), float(s["start"][2])),
            end=(float(s["end"][0]), float(s["end"][1]), float(s["end"][2])),
            center=None if "center" not in s else (float(s["center"][0]), float(s["center"][1])),
            feed=None if "feed" not in s else float(s["feed"]),
        )
        for s in raw.get("segments", [])
    ]
    return ToolpathResult(
        segments=segs,
        cut_length_mm=float(raw.get("cut_length_mm", 0.0)),
        plunge_length_mm=float(raw.get("plunge_length_mm", 0.0)),
        rapid_length_mm=float(raw.get("rapid_length_mm", 0.0)),
        cycle_time_s=float(raw.get("cycle_time_s", 0.0)),
        z_passes=int(raw.get("z_passes", 0)),
        drill_cycles=list(raw.get("drill_cycles", [])),
    )


@router.get("/api/cam/setups/{setup_id}/toolpath")
def get_cam_toolpath(setup_id: int, db: Db, user: CurrentUser) -> dict[str, Any]:
    setup = _setup_row(db, setup_id)
    get_document(db, user, setup.document_id)
    ops = db.scalars(
        select(CamOperation)
        .where(CamOperation.setup_id == setup.setup_id, CamOperation.status == "OK")
        .order_by(CamOperation.seq, CamOperation.operation_id)
    ).all()
    all_segments: list[dict[str, Any]] = []
    total_cut = 0.0
    total_rapid = 0.0
    total_time = 0.0
    for op in ops:
        tp = _load_toolpath(op)
        all_segments.extend(s.to_dict() for s in tp.segments)
        total_cut += tp.cut_length_mm
        total_rapid += tp.rapid_length_mm
        total_time += tp.cycle_time_s
    return body(
        {
            "setup_id": setup.setup_id,
            "kind": setup.kind,
            "segment_count": len(all_segments),
            "cut_length_mm": round(total_cut, 4),
            "rapid_length_mm": round(total_rapid, 4),
            "cycle_time_s": round(total_time, 4),
            "segments": all_segments,
        }
    )


@router.get("/api/cam/setups/{setup_id}/gcode")
def download_cam_gcode(
    setup_id: int,
    db: Db,
    user: CurrentUser,
    program_name: Annotated[str, Query()] = "O0001",
    comment: Annotated[str, Query()] = "",
) -> PlainTextResponse:
    setup = _setup_row(db, setup_id)
    get_document(db, user, setup.document_id)
    try:
        prog = validate_program_name(program_name)
        safe_comment = sanitize_comment(comment, "comment") if comment else ""
    except CamError as e:
        raise ApiError(422, e.code, e.message) from e

    ops = db.scalars(
        select(CamOperation)
        .where(CamOperation.setup_id == setup.setup_id, CamOperation.status == "OK")
        .order_by(CamOperation.seq, CamOperation.operation_id)
    ).all()
    if not ops:
        raise ApiError(422, "CAM_EMPTY_SETUP", "유효한 CAM 공정이 없습니다")

    loaded: list[tuple[CamTool, ToolpathResult, str]] = []
    for op in ops:
        loaded.append((_resolve_tool(db, op.tool_code), _load_toolpath(op), op.op_type))

    try:
        if setup.post_target == "GENERIC_LASER":
            gcode = post_generic_laser(loaded, program_name=prog, comment=safe_comment)
        else:
            gcode = post_fanuc_mill(loaded, program_name=prog, comment=safe_comment)
    except CamError as e:
        raise ApiError(422, e.code, e.message) from e

    return PlainTextResponse(
        content=gcode,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{prog}.nc"'},
    )
