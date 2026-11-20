"""Photo-to-3D API routes: multi-angle & sequential photo real-scale 3D reconstruction + Antigravity SDK."""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select

from backend.api.auth import CurrentUser, Db, get_document, need
from backend.api.common import ApiError, body
from backend.api.routes_model import _changed, _store_brep
from backend.db.models import Feature
from core.photo3d import (
    MAX_IMAGE_BYTES,
    MAX_IMAGES,
    OutputMode,
    Photo3dError,
    PhotoInput,
    PhotoMode,
    ReferenceScale,
    ViewLabel,
)
from core.photo3d.antigravity_adapter import antigravity_sdk_status
from core.photo3d.reconstruct import reconstruct_from_photos

router = APIRouter(tags=["photo3d"])

_VALID_VIEWS: set[str] = {"FRONT", "BACK", "LEFT", "RIGHT", "SIDE", "TOP", "BOTTOM", "ANGLE"}


class Photo3dMetaIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: PhotoMode = "ORTHO_MULTI"
    output_mode: OutputMode = "HYBRID"
    reference_mm: float = Field(gt=0.0, le=1e6)
    reference_axis: Literal["X", "Y", "Z"] = "X"
    ref_p1_px: list[float] | None = Field(default=None, min_length=2, max_length=2)
    ref_p2_px: list[float] | None = Field(default=None, min_length=2, max_length=2)
    view_labels: list[str] = Field(default_factory=list, max_length=MAX_IMAGES)
    angles_deg: list[float] = Field(default_factory=list, max_length=MAX_IMAGES)
    use_ai: bool = False
    create_feature: bool = True


@router.get("/api/photo3d/status")
def photo3d_status(user: CurrentUser) -> dict[str, Any]:
    """Report Photo-to-3D backend capabilities and Antigravity SDK availability."""
    sdk = antigravity_sdk_status()
    return body(
        {
            **sdk,
            "supported_modes": ["ORTHO_MULTI", "TURNTABLE", "FREE_ANGLE"],
            "supported_outputs": ["HYBRID", "PARAMETRIC", "VISUAL_HULL"],
            "max_images": MAX_IMAGES,
            "max_image_mb": MAX_IMAGE_BYTES // (1024 * 1024),
        }
    )


def _build_primitive_params(plan: dict[str, Any]) -> dict[str, Any]:
    kind = str(plan.get("kind", "BOX")).upper()
    w = max(1.0, float(plan.get("width_mm", 50.0)))
    d = max(1.0, float(plan.get("depth_mm", 50.0)))
    h = max(1.0, float(plan.get("height_mm", 50.0)))
    r = max(0.5, float(plan.get("radius_mm", 0.5 * min(w, d))))
    if kind == "CYLINDER":
        return {
            "kind": "CYLINDER",
            "radius": round(r, 3),
            "height": round(h, 3),
            "origin": [0, 0, 0],
            "axis": "Z",
        }
    if kind == "CONE":
        r2 = max(0.0, min(r * 0.95, float(plan.get("top_radius_mm", r * 0.4))))
        return {
            "kind": "CONE",
            "radius": round(r, 3),
            "radius2": round(r2, 3),
            "height": round(h, 3),
            "origin": [0, 0, 0],
            "axis": "Z",
        }
    if kind == "SPHERE":
        return {"kind": "SPHERE", "radius": round(r, 3), "origin": [0, 0, 0], "axis": "Z"}
    return {
        "kind": "BOX",
        "length": round(w, 3),
        "width": round(d, 3),
        "height": round(h, 3),
        "origin": [round(-0.5 * w, 3), round(-0.5 * d, 3), 0],
        "axis": "Z",
    }


@router.post("/api/documents/{document_id}/photo3d/reconstruct")
def reconstruct_photo3d(
    document_id: int,
    user: CurrentUser,
    db: Db,
    files: Annotated[list[UploadFile], File(...)],
    meta: Annotated[str, Form(...)],
) -> dict[str, Any]:
    """Reconstruct a real-scale 3D CAD solid from multi-angle or sequential photos."""
    doc = get_document(db, user, document_id, lock=True)
    need(user, "DESIGNER")

    if not files or len(files) > MAX_IMAGES:
        raise ApiError(422, "PHOTO3D_COUNT", f"사진은 1~{MAX_IMAGES}장까지 업로드할 수 있습니다")

    try:
        parsed_meta = Photo3dMetaIn.model_validate(json.loads(meta))
    except (ValueError, ValidationError) as exc:
        raise ApiError(422, "PHOTO3D_INVALID_META", f"요청 메타데이터 오류: {exc}") from None

    photo_inputs: list[PhotoInput] = []
    for idx, f in enumerate(files):
        raw = f.file.read(MAX_IMAGE_BYTES + 1)
        if len(raw) > MAX_IMAGE_BYTES:
            raise ApiError(
                413, "PHOTO3D_TOO_LARGE", f"이미지 크기가 15MB를 초과합니다: {f.filename}"
            )
        raw_lbl = (
            parsed_meta.view_labels[idx] if idx < len(parsed_meta.view_labels) else "ANGLE"
        ).upper()
        lbl: ViewLabel = raw_lbl if raw_lbl in _VALID_VIEWS else "ANGLE"  # type: ignore[assignment]
        ang = parsed_meta.angles_deg[idx] if idx < len(parsed_meta.angles_deg) else None
        photo_inputs.append(
            PhotoInput(
                data=raw,
                filename=f.filename or f"photo_{idx + 1}.png",
                view_label=lbl,
                angle_deg=ang,
            )
        )

    scale = ReferenceScale(
        reference_mm=parsed_meta.reference_mm,
        axis=parsed_meta.reference_axis,
        p1_px=(parsed_meta.ref_p1_px[0], parsed_meta.ref_p1_px[1])
        if parsed_meta.ref_p1_px
        else None,
        p2_px=(parsed_meta.ref_p2_px[0], parsed_meta.ref_p2_px[1])
        if parsed_meta.ref_p2_px
        else None,
    )

    try:
        res = reconstruct_from_photos(
            photo_inputs,
            scale,
            mode=parsed_meta.mode,
            output_mode=parsed_meta.output_mode,
            use_ai=parsed_meta.use_ai,
        )
    except Photo3dError as exc:
        raise ApiError(exc.http_status, exc.code, exc.message) from None

    feature_id: int | None = None
    if parsed_meta.create_feature:
        max_seq = db.scalar(
            select(func.coalesce(func.max(Feature.seq), -1)).where(
                Feature.document_id == doc.document_id
            )
        )
        next_seq = int(max_seq if max_seq is not None else -1) + 1
        key = uuid.uuid4().hex
        w_mm, d_mm, h_mm = res.bbox_mm
        metrics = {
            "volume_mm3": round(res.volume_mm3, 3),
            "surface_area_mm2": round(res.surface_area_mm2, 3),
            "bbox": {
                "min": [round(-0.5 * w_mm, 3), round(-0.5 * d_mm, 3), 0.0],
                "max": [round(0.5 * w_mm, 3), round(0.5 * d_mm, 3), round(h_mm, 3)],
            },
            "photo3d": {"mode": res.mode, "ai_used": res.ai_used, "ai_provider": res.ai_provider},
        }
        if res.resolved_output == "PARAMETRIC":
            feat = Feature(
                document_id=doc.document_id,
                seq=next_seq,
                feature_type="PRIMITIVE",
                params=_build_primitive_params(res.feature_plan),
                brep_key=key,
                metrics=metrics,
                created_by=user.user_id,
            )
        else:
            feat = Feature(
                document_id=doc.document_id,
                seq=next_seq,
                feature_type="IMPORT",
                params={
                    "name": f"Photo3D-{res.mode}({len(photo_inputs)}장)",
                    "format": "BREP",
                    "file_key": key,
                },
                brep_key=key,
                metrics=metrics,
                created_by=user.user_id,
            )
        db.add(feat)
        db.flush()
        _store_brep(key, res.brep_bytes)
        _changed(doc)
        db.commit()
        feature_id = feat.feature_id

    return body({"feature_id": feature_id, **res.to_dict()})
