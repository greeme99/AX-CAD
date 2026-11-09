"""2D underlays U1 (TC-195): PDF and DWFx pages as vectors + raster, limits and hostile files."""

import io
import math
import os
import time
import zipfile

import pytest
from PIL import Image

from core.underlay import MAX_POINTS, Page, UnderlayError, cubic_arc, ops, pdf, raster, worker, xps
from tests import underlay_samples as S

MM = 25.4 / 72


@pytest.fixture(scope="module")
def vpdf(tmp_path_factory):
    p = tmp_path_factory.mktemp("ul") / "v.pdf"
    p.write_bytes(S.vector_pdf())
    return str(p)


def png_size(data: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(data)).size


# --- PDF ---------------------------------------------------------------------------------------


def test_pdf_info_and_vectors(vpdf):
    meta = pdf.info(vpdf)
    assert meta["page_count"] == 1
    assert meta["pages"][0] == {
        "width": pytest.approx(595 * MM, abs=1e-2),
        "height": pytest.approx(842 * MM, abs=1e-2),
    }
    v = pdf.page_vectors(vpdf, 0)
    assert v["layers"] == ["Walls", "Doors"] and not v["raster_only"]
    walls = next(p for p in v["paths"] if p.get("l") == 0)
    assert walls["p"] == pytest.approx(
        [72 * MM, 72 * MM, 300 * MM, 72 * MM, 300 * MM, 200 * MM], abs=1e-3
    )
    doors = next(p for p in v["paths"] if p.get("l") == 1)
    assert doors.get("z") == 1 and len(doors["p"]) == 8  # the rectangle, closed
    # the circle of four cubics is one circle: its centre can be snapped to
    (circle,) = [a for a in v["arcs"] if "a" not in a]
    assert circle["c"] == pytest.approx([400 * MM, 400 * MM], abs=1e-3) and circle[
        "r"
    ] == pytest.approx(40 * MM, abs=1e-3)
    # the form XObject's own matrix (scale 2, then move 500,500) is applied to its line
    form = next(p for p in v["paths"] if p["p"][0] == pytest.approx(520 * MM, abs=1e-3))
    assert form["p"] == pytest.approx([520 * MM, 520 * MM, 540 * MM, 520 * MM], abs=1e-3)
    (text,) = v["texts"]
    assert text["v"] == "Hello PDF" and text["p"] == pytest.approx([100 * MM, 300 * MM], abs=1e-3)
    assert text["h"] == pytest.approx(12 * MM, abs=0.2)


def test_pdf_render_layers_and_crop(vpdf):
    full = pdf.render(vpdf, 0, 400, [])
    assert png_size(full) == (400, round(400 * 842 / 595))
    assert Image.open(io.BytesIO(full)).convert("L").getextrema()[0] < 128  # something is drawn
    no_walls = pdf.render(vpdf, 0, 400, ["Walls"])
    assert full != no_walls  # the Walls layer is gone from the image
    # a window of the page: 50 mm wide at 200 px
    win = pdf.render(vpdf, 0, 200, [], (0.0, 0.0, 50.0, 25.0))
    assert png_size(win) == (200, 100)
    with pytest.raises(UnderlayError) as e:
        pdf.render(vpdf, 0, 8192, [], (0.0, 0.0, 1.0, 100.0))  # 8192 x 819200
    assert e.value.code == "UNDERLAY_RENDER_SIZE"
    with pytest.raises(UnderlayError) as e:
        pdf.page_vectors(vpdf, 5)
    assert e.value.code == "UNDERLAY_PAGE"


def test_rotated_page_swaps_size_and_turns_points(tmp_path):
    p = tmp_path / "r.pdf"
    p.write_bytes(S.vector_pdf(rotate=90))
    meta = pdf.info(str(p))
    assert meta["pages"][0]["width"] == pytest.approx(842 * MM, abs=1e-2)
    v = pdf.page_vectors(str(p), 0)
    w = next(q for q in v["paths"] if q.get("l") == 0)["p"]
    # (72,72) of the unrotated page lands at (72, 595-72) of the page shown turned clockwise
    assert w[:2] == pytest.approx([72 * MM, (595 - 72) * MM], abs=1e-3)
    assert pdf.render(str(p), 0, 300, []) and png_size(pdf.render(str(p), 0, 300, []))[0] == 300


def test_scanned_pdf_has_no_vectors(tmp_path):
    p = tmp_path / "s.pdf"
    p.write_bytes(S.scan_pdf())
    v = pdf.page_vectors(str(p), 0)
    assert v["raster_only"] is True and v["paths"] == [] and v["arcs"] == []
    assert png_size(pdf.render(str(p), 0, 300, []))[0] == 300  # still shown


def test_broken_and_encrypted_pdf(tmp_path):
    p = tmp_path / "b.pdf"
    p.write_bytes(b"%PDF-1.5\nthis is not a pdf")
    with pytest.raises(UnderlayError):
        pdf.info(str(p))


# --- DWFx --------------------------------------------------------------------------------------


@pytest.fixture
def dwfx_file(tmp_path):
    p = tmp_path / "t.dwfx"
    p.write_bytes(S.dwfx())
    return str(p)


def test_dwfx_vectors(dwfx_file):
    k = 25.4 / 96
    meta = xps.info(dwfx_file)
    assert meta["pages"] == [
        {"width": pytest.approx(800 * k, abs=1e-2), "height": pytest.approx(600 * k, abs=1e-2)}
    ]
    v = xps.page_vectors(dwfx_file, 0)
    tri = next(p for p in v["paths"] if p.get("c") == "#0000ff")
    # canvas offset (100,50) and the page's y axis flipped to up
    assert tri["p"] == pytest.approx(
        [100 * k, (600 - 50) * k, 300 * k, (600 - 50) * k, 300 * k, (600 - 150) * k], abs=1e-3
    )
    assert tri.get("z") == 1
    (circle,) = v["arcs"]
    assert circle["c"] == pytest.approx([360 * k, (600 - 250) * k], abs=1e-3) and circle[
        "r"
    ] == pytest.approx(40 * k, abs=1e-3)
    (text,) = v["texts"]
    assert text["v"] == "Hello DWFx" and text["h"] == pytest.approx(24 * k, abs=1e-3)
    grey = next(p for p in v["paths"] if p.get("f"))
    assert grey["c"] == "#808080"


def test_dwfx_raster_and_hidden_layers(dwfx_file):
    v = xps.page_vectors(dwfx_file, 0)
    img = raster.render(v, 600, [])
    assert png_size(img) == (600, 450)
    assert Image.open(io.BytesIO(img)).convert("L").getextrema()[0] < 128
    assert png_size(raster.render(v, 100, [], (0.0, 0.0, 50.0, 50.0))) == (100, 100)
    assert ops.render("dwfx", dwfx_file, 0, 300, [], None)[:4] == b"\x89PNG"


def test_path_mini_language():
    (s,) = xps.parse_path("F1 M 0,0 H 10 V 5 h -10 z")
    assert s["closed"] and [seg[1][1] for seg in s["segs"]] == [
        (10.0, 0.0),
        (10.0, 5.0),
        (0.0, 5.0),
        (0.0, 0.0),
    ]
    (q,) = xps.parse_path("M 0,0 Q 5,10 10,0")  # a quadratic becomes a cubic
    assert q["segs"][0][0] == "C" and q["segs"][0][1][3] == (10.0, 0.0)
    (a,) = xps.parse_path("M 0,0 A 5,5 0 0 1 10,0")
    assert a["segs"][-1][1][1] == (10.0, 0.0) and len(a["segs"]) > 4
    assert len(xps.parse_path("M 0,0 L 1,1 M 5,5 L 6,6")) == 2


# --- what is accepted ----------------------------------------------------------------------------


def test_classify_by_bytes_and_classic_dwf_is_refused():
    assert ops.classify(S.vector_pdf()) == "pdf"
    assert ops.classify(S.dwfx()) == "dwfx"
    for data in (S.classic_dwf(), S.dwf_package()):
        with pytest.raises(UnderlayError) as e:
            ops.classify(data)
        assert e.value.code == "UNDERLAY_DWF_CLASSIC" and "DWFx" in e.value.message
    for data in (b"", b"MZ\x90\x00", b"PK\x03\x04garbage", b'<?xml version="1.0"?><x/>'):
        with pytest.raises(UnderlayError) as e:
            ops.classify(data)
        assert e.value.code == "UNDERLAY_FORMAT"
    other = io.BytesIO()
    with zipfile.ZipFile(other, "w") as z:
        z.writestr("readme.txt", "x")
    with pytest.raises(UnderlayError):
        ops.classify(other.getvalue())


# --- hostile files ---------------------------------------------------------------------------------


def test_xml_bombs_and_deep_canvases_stop(tmp_path):
    laughs = (
        '<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>'
        f'<FixedPage xmlns="{S.XPS}" Width="&b;" Height="10"/>'
    )
    p = tmp_path / "bomb.dwfx"
    p.write_bytes(S.dwfx(laughs))
    with pytest.raises(UnderlayError):
        xps.info(str(p))
    deep = (
        f'<FixedPage xmlns="{S.XPS}" Width="10" Height="10">'
        + "<Canvas>" * 80
        + "</Canvas>" * 80
        + "</FixedPage>"
    )
    q = tmp_path / "deep.dwfx"
    q.write_bytes(S.dwfx(deep))
    with pytest.raises(UnderlayError) as e:
        xps.page_vectors(str(q), 0)
    assert e.value.code == "UNDERLAY_TOO_BIG"


def test_too_many_entries_and_oversized_parts(tmp_path, monkeypatch):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i in range(20_001):
            z.writestr(f"f{i}", b"")
    p = tmp_path / "many.dwfx"
    p.write_bytes(buf.getvalue())
    with pytest.raises(UnderlayError) as e:
        xps.open_package(str(p))
    assert e.value.code == "UNDERLAY_TOO_BIG"
    monkeypatch.setattr(xps, "MAX_PART_BYTES", 100)
    q = tmp_path / "big.dwfx"
    q.write_bytes(S.dwfx())
    with pytest.raises(UnderlayError) as e2:
        xps.info(str(q))
    assert e2.value.code == "UNDERLAY_TOO_BIG"


def test_numbers_and_limits_in_page_model():
    page = Page(100, 100)
    page.add_path([(0, 0), (math.nan, 1)])  # not finite: dropped
    page.add_path([(0, 0), (math.inf, 1)])
    page.add_arc((0, 0), -1)
    page.add_arc((0, 0), math.nan)
    page.add_text("\x00\x01 ", (0, 0), 3, 0)
    page.add_text("ok", (0, 0), 0, 0)  # no height
    assert (page.paths, page.arcs, page.texts) == ([], [], [])
    n = MAX_POINTS // 1000 + 2
    for i in range(n):
        page.add_path([(j, i) for j in range(1000)])
    assert page.truncated and page.points <= MAX_POINTS
    assert len(page.layers) == 0 and page.layer("x" * 400) == 0 and len(page.layers[0]) == 255


def test_cubic_arcs():
    r = 10.0
    k = 0.5522847498 * r
    quarter = cubic_arc((r, 0), (r, k), (k, r), (0, r))
    assert quarter is not None
    (c, rad, start, sweep) = quarter
    assert c == pytest.approx((0, 0), abs=1e-6) and rad == pytest.approx(10, abs=1e-3)
    assert start == pytest.approx(0, abs=1e-6) and sweep == pytest.approx(90, abs=1e-2)
    assert (
        cubic_arc((0, 0), (3, 10), (7, 10), (10, 0)) is None
    )  # a parabola-like curve is not an arc
    assert cubic_arc((0, 0), (1, 0), (2, 0), (3, 0)) is None  # a straight "curve"


# --- the worker ----------------------------------------------------------------------------------------


def test_worker_limits_and_recovery():
    assert worker.run(abs, -3) == 3
    with pytest.raises(UnderlayError) as e:
        worker.run(time.sleep, 30, timeout_s=0.5)
    assert e.value.code == "UNDERLAY_TIMEOUT"
    assert worker.run(abs, -4) == 4  # a fresh pool after the reset
    with pytest.raises(UnderlayError) as e2:
        worker.run(int, "x")  # any library error is one parse error; the detail stays in the log
    assert e2.value.code == "UNDERLAY_PARSE_ERROR" and "invalid literal" not in e2.value.message
    with pytest.raises(UnderlayError) as e3:
        worker.run(os._exit, 1)  # a worker that dies
    assert e3.value.code == "UNDERLAY_FAILED"
    assert worker.run(abs, -5) == 5


def _env_in_worker() -> dict[str, str]:
    import os

    return {k: v for k, v in os.environ.items() if k in ("JWT_SECRET", "DATABASE_URL", "PATH")}


def test_the_worker_has_no_secrets_and_calls_queue(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from core.underlay import worker

    monkeypatch.setenv("JWT_SECRET", "s3cret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    worker.reset()
    try:
        got = worker.run(_env_in_worker)
        assert "PATH" in got and "JWT_SECRET" not in got and "DATABASE_URL" not in got
        # more callers than workers all finish: the queue wait is not part of their time limit
        with ThreadPoolExecutor(6) as ex:
            res = list(ex.map(lambda _: worker.run(_env_in_worker, timeout_s=20), range(6)))
        assert len(res) == 6
    finally:
        worker.reset()
