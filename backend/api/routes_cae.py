"""CAE E1~E3 Linear Static FEA API routes & PDF report (plan.md §24)."""

from __future__ import annotations

import io
import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from backend.api.auth import CurrentUser, Db, get_document, need
from backend.api.common import ApiError, body
from backend.db.models import CaeStudy, Feature
from core.cae.fea import solve_linear_static
from core.cae.mesh import CaeError, generate_tet4_mesh
from core.geometry.errors import GeomError
from core.geometry.serialize import from_brep

router = APIRouter(tags=["cae"])
DISCLAIMER_KO = "선형 정적 근사 해석 참고용 (Linear Static Approximation - Reference Only)"


def _var() -> Path:
    from backend.api import main  # late: tests patch main.VAR_DIR

    return main.VAR_DIR


class CaeStudyCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    feature_id: int | None = None
    material_code: str = Field(default="STEEL", min_length=1, max_length=32)
    youngs_modulus_mpa: float = Field(default=210_000.0, gt=0, le=1e7)
    poissons_ratio: float = Field(default=0.3, gt=0.0, lt=0.495)
    yield_strength_mpa: float = Field(default=250.0, gt=0, le=1e5)
    mesh_size_mm: float = Field(default=5.0, gt=0, le=500.0)
    restraints: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    loads: list[dict[str, Any]] = Field(default_factory=list, max_length=50)


class CaeSolveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    box_dims: list[float] | None = Field(default=None, min_length=3, max_length=3)


def _study_row(db: Db, study_id: int) -> CaeStudy:
    row = db.get(CaeStudy, study_id)
    if row is None:
        raise ApiError(404, "CAE_STUDY_NOT_FOUND", "CAE 해석 스터디를 찾을 수 없습니다")
    return row


def _study_dict(row: CaeStudy) -> dict[str, Any]:
    return {
        "study_id": row.study_id,
        "document_id": row.document_id,
        "feature_id": row.feature_id,
        "material_code": row.material_code,
        "youngs_modulus_mpa": float(row.youngs_modulus_mpa),
        "poissons_ratio": float(row.poissons_ratio),
        "yield_strength_mpa": float(row.yield_strength_mpa),
        "mesh_size_mm": float(row.mesh_size_mm),
        "restraints": row.restraints,
        "loads": row.loads,
        "status": row.status,
        "error_code": row.error_code,
        "node_count": row.node_count,
        "element_count": row.element_count,
        "max_disp_mm": None if row.max_disp_mm is None else float(row.max_disp_mm),
        "max_von_mises_mpa": None
        if row.max_von_mises_mpa is None
        else float(row.max_von_mises_mpa),
        "min_safety_factor": None
        if row.min_safety_factor is None
        else float(row.min_safety_factor),
    }


@router.post("/api/documents/{doc_id}/cae/studies", status_code=201)
def create_cae_study(
    doc_id: int, payload: CaeStudyCreateIn, db: Db, user: CurrentUser
) -> dict[str, Any]:
    doc = get_document(db, user, doc_id, True)
    need(user, "DESIGNER", "ESTIMATOR")
    row = CaeStudy(
        document_id=doc.document_id,
        feature_id=payload.feature_id,
        material_code=payload.material_code,
        youngs_modulus_mpa=Decimal(str(payload.youngs_modulus_mpa)),
        poissons_ratio=Decimal(str(payload.poissons_ratio)),
        yield_strength_mpa=Decimal(str(payload.yield_strength_mpa)),
        mesh_size_mm=Decimal(str(payload.mesh_size_mm)),
        restraints=payload.restraints,
        loads=payload.loads,
        status="PENDING",
        created_by=user.user_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return body(_study_dict(row))


@router.post("/api/cae/studies/{study_id}/solve")
def solve_cae_study(
    study_id: int,
    db: Db,
    user: CurrentUser,
    payload: CaeSolveIn | None = None,
) -> dict[str, Any]:
    from backend.api.routes_model import _feature_brep  # late: avoid import cycle

    row = _study_row(db, study_id)
    get_document(db, user, row.document_id, True)
    need(user, "DESIGNER", "ESTIMATOR")

    shape = None
    box_dims: tuple[float, float, float] | None = None
    if payload and payload.box_dims:
        box_dims = (
            float(payload.box_dims[0]),
            float(payload.box_dims[1]),
            float(payload.box_dims[2]),
        )
    elif row.feature_id is not None:
        feat = db.get(Feature, row.feature_id)
        if feat is None or feat.document_id != row.document_id or feat.status != "OK":
            raise ApiError(422, "CAE_NO_BREP", "해석 대상 3D 피처 형상을 찾을 수 없습니다")
        try:
            shape = from_brep(_feature_brep(db, feat, {}))
        except GeomError as e:
            raise ApiError(422, e.code, str(e)) from e
    else:
        raise ApiError(422, "CAE_NO_BREP", "feature_id 또는 box_dims가 필요합니다")

    try:
        mesh = generate_tet4_mesh(
            shape,
            float(row.mesh_size_mm),
            box_dims=box_dims,
        )
        res = solve_linear_static(
            mesh,
            youngs_modulus_mpa=float(row.youngs_modulus_mpa),
            poissons_ratio=float(row.poissons_ratio),
            yield_strength_mpa=float(row.yield_strength_mpa),
            restraints=row.restraints,
            loads=row.loads,
        )
    except CaeError as e:
        row.status = "ERROR"
        row.error_code = e.code
        db.commit()
        raise ApiError(422, e.code, e.message) from e

    out_dir = _var() / "cae" / str(row.study_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    res_path = out_dir / "result.json"
    res_path.write_text(json.dumps(res.to_dict(mesh), allow_nan=False), encoding="utf-8")

    row.status = "SOLVED"
    row.error_code = None
    row.node_count = res.node_count
    row.element_count = res.element_count
    row.max_disp_mm = Decimal(f"{res.max_disp_mm:.6f}")
    row.max_von_mises_mpa = Decimal(f"{res.max_von_mises_mpa:.4f}")
    row.min_safety_factor = Decimal(f"{res.min_safety_factor:.4f}")
    row.result_path = str(res_path.relative_to(_var()))
    db.commit()
    db.refresh(row)
    return body(_study_dict(row))


@router.get("/api/cae/studies/{study_id}")
def get_cae_study(study_id: int, db: Db, user: CurrentUser) -> dict[str, Any]:
    row = _study_row(db, study_id)
    get_document(db, user, row.document_id)
    return body(_study_dict(row))


@router.get("/api/cae/studies/{study_id}/result")
def get_cae_result(study_id: int, db: Db, user: CurrentUser) -> dict[str, Any]:
    row = _study_row(db, study_id)
    get_document(db, user, row.document_id)
    if row.status != "SOLVED" or not row.result_path:
        raise ApiError(422, "CAE_NOT_SOLVED", "아직 풀이되지 않은 해석 스터디입니다")
    p = _var() / row.result_path
    if not p.is_file():
        raise ApiError(404, "CAE_RESULT_NOT_FOUND", "해석 결과 파일을 찾을 수 없습니다")
    data = json.loads(p.read_text(encoding="utf-8"))
    return body({"study_id": row.study_id, **data})


def _register_korean_font() -> str:
    font_path = Path(__file__).resolve().parents[1] / "assets" / "fonts" / "NanumGothic.ttf"
    if font_path.is_file():
        try:
            pdfmetrics.registerFont(TTFont("NanumGothic", str(font_path)))
            return "NanumGothic"
        except Exception:  # noqa: BLE001
            return "Helvetica"
    return "Helvetica"


@router.get("/api/cae/studies/{study_id}/report")
def download_cae_report(
    study_id: int,
    db: Db,
    user: CurrentUser,
    format: Literal["pdf"] = "pdf",
) -> Response:
    row = _study_row(db, study_id)
    doc = get_document(db, user, row.document_id)
    if row.status != "SOLVED":
        raise ApiError(422, "CAE_NOT_SOLVED", "해석 완료 후 보고서를 생성할 수 있습니다")

    buf = io.BytesIO()
    font_name = _register_korean_font()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"AX-CAD CAE Report #{row.study_id} - {DISCLAIMER_KO}")
    c.setFont(font_name, 14)
    c.drawString(40, 800, f"AX-CAD 선형 정적 FEA 해석 보고서 (Study #{row.study_id})")
    c.setFont(font_name, 10)
    c.drawString(40, 778, f"주의: {DISCLAIMER_KO}")
    c.drawString(40, 755, f"문서 번호: {doc.doc_no} / 제목: {doc.title}")
    c.drawString(
        40,
        735,
        f"재질: {row.material_code} (E={row.youngs_modulus_mpa} MPa, v={row.poissons_ratio}, Sy={row.yield_strength_mpa} MPa)",
    )
    c.drawString(
        40,
        715,
        f"메시 통계: 절점 {row.node_count}개 / 4절점 사면체(Tet4) 요소 {row.element_count}개 (크기 {row.mesh_size_mm} mm)",
    )
    c.drawString(40, 690, f"최대 변위: {row.max_disp_mm} mm")
    c.drawString(40, 670, f"최대 von Mises 응력: {row.max_von_mises_mpa} MPa")
    c.drawString(40, 650, f"최소 안전율 (Safety Factor): {row.min_safety_factor}")
    c.showPage()
    c.save()

    return Response(
        content=buf.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="cae-study-{row.study_id}.pdf"'},
    )
