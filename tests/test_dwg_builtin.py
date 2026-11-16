"""plan §25: the built-in DWG translator — no converter installed. Core only (no API fixtures),
so the CI windows job runs it too."""

import io
from pathlib import Path

import ezdxf
import pytest

from core.dxf import dwg
from core.dxf.reader import DxfError

SAMPLE = Path(__file__).parent / "samples" / "dwg" / "insert_2004.dwg"  # ezdwg example (MIT)

pytestmark = pytest.mark.skipif(not dwg.builtin_available(), reason="ezdwg is not installed")


@pytest.fixture(autouse=True)
def no_external_converter(monkeypatch):
    monkeypatch.delenv("AXCAD_DWG_CONVERTER", raising=False)


def drawing() -> ezdxf.document.Drawing:
    d = ezdxf.new("R2018", setup=True)
    d.units = 4  # mm
    d.layers.add("OUTLINE", color=1)
    d.layers.add("CENTER", color=3, linetype="CENTER")
    m = d.modelspace()
    m.add_line((0, 0), (100, 0), dxfattribs={"layer": "OUTLINE"})
    m.add_line((0, 30), (100, 30), dxfattribs={"layer": "CENTER"})
    m.add_circle((50, 50), 20, dxfattribs={"layer": "OUTLINE"})
    m.add_arc((0, 0), 30, 10, 95)
    m.add_lwpolyline([(0, 0, 0.5), (100, 0, 0), (100, 60, 0)], format="xyb", close=True)
    m.add_text("한글 품번 AX-100", height=5, dxfattribs={"insert": (10, 10)})
    m.add_mtext("재질: SUS304", dxfattribs={"insert": (10, 40), "char_height": 3})
    hatch = m.add_hatch(color=2)
    hatch.paths.add_polyline_path([(0, 0), (10, 0), (10, 10), (0, 10)], is_closed=True)
    bolt = d.blocks.new("BOLT")
    for i in range(5):
        bolt.add_line((i, 0), (i, 4))
    bolt.add_attdef("SIZE", (0, -6), dxfattribs={"height": 2})
    for at in ((70, 20), (90, 20)):
        m.add_blockref("BOLT", at, dxfattribs={"rotation": 30}).add_auto_attribs({"SIZE": "M8"})
    m.add_linear_dim(base=(0, -20), p1=(0, 0), p2=(100, 0)).render()
    return d


def dxf_bytes(d: ezdxf.document.Drawing) -> bytes:
    buf = io.StringIO()
    d.write(buf)
    return buf.getvalue().encode()


def back(data: bytes) -> ezdxf.document.Drawing:
    return ezdxf.read(io.StringIO(data.decode("utf-8", "replace")))


def pts(*points) -> list[tuple[float, float]]:
    return [(round(p[0], 6), round(p[1], 6)) for p in points]


def test_save_as_dwg_2000_and_open_again():
    saved = dwg.export_drawing(dxf_bytes(drawing()), "DWG", "2000")
    assert dwg.dwg_release(saved) == "2000"
    dxf, kind, clean, skipped = dwg.dwg_import(saved)
    assert kind == "builtin" and clean and skipped == {}
    d = back(dxf)
    m = d.modelspace()
    assert d.units == 4  # the drawing unit survives (a wrong unit scales every length)
    lines = sorted(pts(e.dxf.start, e.dxf.end) for e in m.query("LINE[layer!='0']"))
    assert lines == [pts((0, 0), (100, 0)), pts((0, 30), (100, 30))]
    circle = m.query("CIRCLE")[0]
    assert pts(circle.dxf.center) == [(50, 50)] and circle.dxf.radius == pytest.approx(20)
    arc = m.query("ARC")[0]
    assert (arc.dxf.start_angle, arc.dxf.end_angle) == pytest.approx((10, 95))
    poly = m.query("LWPOLYLINE")[0]
    assert poly.closed and [tuple(p) for p in poly.get_points("xyb")] == [
        pytest.approx(p) for p in ((0, 0, 0.5), (100, 0, 0), (100, 60, 0))
    ]
    assert [t.dxf.text for t in m.query("TEXT")] == ["한글 품번 AX-100"]
    assert any("SUS304" in t.text for t in m.query("MTEXT"))
    assert len(m.query("HATCH")) == 1
    # layers keep colour and linetype (centre lines stay centre lines)
    center = d.layers.get("CENTER")
    assert center.dxf.color == 3 and center.dxf.linetype.upper() == "CENTER"
    assert d.layers.get("OUTLINE").dxf.color == 1


def test_block_contents_stay_in_the_block():
    """A block's lines must not be copied into model space: that would add cut length."""
    d = back(dwg.dwg_import(dwg.export_drawing(dxf_bytes(drawing()), "DWG", "2000"))[0])
    m = d.modelspace()
    refs = [e for e in m.query("INSERT") if e.dxf.name.upper() == "BOLT"]
    assert sorted(pts(e.dxf.insert)[0] for e in refs) == [(70, 20), (90, 20)]
    assert all(e.dxf.rotation == pytest.approx(30) for e in refs)
    assert [a.dxf.text for a in refs[0].attribs] == ["M8"]
    bolt = d.blocks.get("BOLT")
    assert len(bolt.query("LINE")) == 5 and len(bolt.query("ATTDEF")) == 1
    assert len(m.query("LINE[layer!='0']")) == 2  # only the two drawn in model space


def test_dimension_is_saved_as_its_geometry():
    d = back(dwg.dwg_import(dwg.export_drawing(dxf_bytes(drawing()), "DWG", "2000"))[0])
    m = d.modelspace()
    assert len(m.query("DIMENSION")) == 0
    lengths = [e.dxf.start.distance(e.dxf.end) for e in m.query("LINE[layer=='0']")]
    assert any(n == pytest.approx(100, abs=1) for n in lengths)  # the dimension line


def test_reads_a_2004_drawing_with_a_block():
    dxf, kind, clean, skipped = dwg.dwg_import(SAMPLE.read_bytes())
    assert kind == "builtin" and clean and skipped == {}
    d = back(dxf)
    assert [e.dxftype() for e in d.modelspace()] == ["INSERT"]
    name = d.modelspace()[0].dxf.name
    assert [e.dxftype() for e in d.blocks.get(name)] == ["LINE"]


def test_names_the_dwg_format_refuses_are_replaced():
    d = ezdxf.new("R2018")
    d.layers.add("A|B", color=2)
    d.layers.add("*HIDDEN")
    d.modelspace().add_line((0, 0), (1, 0), dxfattribs={"layer": "A|B"})
    d.modelspace().add_line((0, 1), (1, 1), dxfattribs={"layer": "*HIDDEN"})
    out = back(dwg.dwg_import(dwg.export_drawing(dxf_bytes(d), "DWG", "2000"))[0])
    assert sorted(e.dxf.layer for e in out.modelspace()) == ["A_B", "_HIDDEN"]


def test_what_the_builtin_translator_does_not_do():
    data = dxf_bytes(drawing())
    for fmt, version in (("DWG", "2018"), ("DXF", "2000"), ("DXF", "R12")):
        with pytest.raises(DxfError) as e:
            dwg.export_drawing(data, fmt, version)
        assert e.value.code == "DWG_WRITE_UNSUPPORTED"
    i = dwg.info()
    assert i["available"] and i["kind"] == "builtin" and i["program"] == "ezdwg"
    assert i["can_read"] and i["can_write"] and i["versions"] == ["2000"]
    assert i["formats"] == ["DWG"] and i["version"]


@pytest.mark.parametrize("cut", [200, 3000])
def test_a_damaged_dwg_is_an_error_not_a_drawing(cut):
    good = dwg.export_drawing(dxf_bytes(drawing()), "DWG", "2000")
    with pytest.raises(DxfError) as e:
        dwg.dwg_import(good[:cut])
    assert e.value.code == "DWG_CONVERT_FAILED"
    with pytest.raises(DxfError) as e:
        dwg.dwg_import(b"AC1032" + b"\x00" * 64)
    assert e.value.code == "DWG_CONVERT_FAILED"


def test_selftest_runs_on_the_builtin_translator():
    r = dwg.selftest()
    assert r["ok"] and r["dwg_release"] == "2000"
    assert r["entities"] == ["CIRCLE", "LINE", "LWPOLYLINE"]


def test_report_counts_are_checked():
    assert dwg._counts({"3DSOLID": 2, "X": "9", "Y": -1, "Z": True, 5: 1}) == {"3DSOLID": 2, "5": 1}
    assert dwg._counts(["3DSOLID"]) == {} and dwg._counts(None) == {}
    assert dwg._counts({"A": 10**30, "b\nad": 1}) == {"A": 10**9}


def test_converter_output_must_be_a_plain_file(tmp_path):
    """A converter taken over by a hostile drawing cannot point its result at another file."""
    secret = tmp_path / "secret.txt"
    secret.write_text("JWT_SECRET=...")
    link = tmp_path / "drawing.dxf"
    try:
        link.symlink_to(secret)
    except OSError:
        pytest.skip("symbolic links need a privilege on this Windows account")
    assert dwg._plain_file(link) == -1 and dwg._plain_file(secret) > 0
    with pytest.raises(DxfError) as e:
        dwg._read_result(link)
    assert e.value.code == "DWG_CONVERT_FAILED"


def test_without_ezdwg_there_is_no_converter(monkeypatch):
    monkeypatch.setattr(dwg, "builtin_available", lambda: False)
    assert dwg.info()["available"] is False
    with pytest.raises(DxfError) as e:
        dwg.dwg_import(SAMPLE.read_bytes())
    assert e.value.code == "DWG_CONVERTER_MISSING"
