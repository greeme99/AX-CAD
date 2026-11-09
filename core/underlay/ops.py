"""The calls the API makes in a worker (module level, picklable): one format switch for all."""

from __future__ import annotations

import io
import zipfile
from typing import Any

from . import MAX_ENTRIES_CLASSIFY, UnderlayError, pdf, raster, sniff, xps

CLASSIC = "고전 DWF(.dwf)는 지원하지 않습니다. AutoCAD에서 PDF 또는 DWFx로 내보내 사용하세요"


def classify(data: bytes) -> str:
    """ "pdf" or "dwfx" from the bytes; a classic DWF is refused with the way out."""
    kind = sniff(data)
    if kind == "dwf":
        raise UnderlayError("UNDERLAY_DWF_CLASSIC", CLASSIC)
    if kind == "pdf":
        return "pdf"
    try:
        names = [
            n.lower() for n in zipfile.ZipFile(io.BytesIO(data)).namelist()[:MAX_ENTRIES_CLASSIFY]
        ]
    except zipfile.BadZipFile:
        raise UnderlayError("UNDERLAY_FORMAT", "PDF 또는 DWFx 파일이 아닙니다") from None
    if any(n.endswith((".fpage", ".fdseq")) for n in names):
        return "dwfx"
    if any(n.endswith(".w2d") for n in names) or "manifest.xml" in names:
        raise UnderlayError("UNDERLAY_DWF_CLASSIC", CLASSIC)
    raise UnderlayError("UNDERLAY_FORMAT", "PDF 또는 DWFx 파일이 아닙니다")


def info(kind: str, path: str) -> dict[str, Any]:
    return pdf.info(path) if kind == "pdf" else xps.info(path)


def vectors(kind: str, path: str, page: int) -> dict[str, Any]:
    return pdf.page_vectors(path, page) if kind == "pdf" else xps.page_vectors(path, page)


def render(
    kind: str,
    path: str,
    page: int,
    width_px: int,
    hidden: list[str],
    crop_mm: tuple[float, float, float, float] | None,
) -> bytes:
    if kind == "pdf":
        return pdf.render(path, page, width_px, hidden, crop_mm)
    vec = xps.page_vectors(path, page)
    off = [i for i, n in enumerate(vec["layers"]) if n in hidden]
    return raster.render(vec, width_px, off, crop_mm)
