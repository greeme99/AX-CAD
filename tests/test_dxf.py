import math

import ezdxf
import pytest

from core.dxf.reader import DxfError, parse_dxf, parse_dxf_with_timeout, validate_dxf_bytes


def save(doc, tmp_path, name="a.dxf"):
    path = tmp_path / name
    doc.saveas(path)
    return str(path)


def by_type(payload, kind):
    return [e for e in payload["entities"] if e["type"] == kind]


@pytest.fixture
def mm_doc():
    doc = ezdxf.new(setup=True)
    doc.units = ezdxf.units.MM
    doc.layers.add("CUT", color=1)
    msp = doc.modelspace()
    msp.add_line((0, 0), (100, 0), dxfattribs={"layer": "CUT", "color": 1})
    msp.add_circle((50, 50), 10)
    msp.add_arc((0, 0), 5, 0, 90)
    msp.add_lwpolyline([(0, 0, 0.5), (10, 0), (10, 10), (0, 10)], close=True)
    msp.add_text("PART-001", height=2.5, dxfattribs={"insert": (1, 2)})
    msp.add_mtext("hello\\Pworld", dxfattribs={"insert": (3, 4)})
    return doc


def test_mm_drawing(mm_doc, tmp_path):
    p = parse_dxf(save(mm_doc, tmp_path))
    assert p["summary"]["entity_count"] == 6
    assert {"CUT", "0"} <= {lay["name"] for lay in p["layers"]}
    line = by_type(p, "LINE")[0]
    assert line["paths"] == [[[0, 0], [100, 0]]]
    assert line["layer"] == "CUT" and line["color"] == "#ff0000"
    circle = by_type(p, "CIRCLE")[0]["paths"][0]
    assert all(abs(math.dist(pt, (50, 50)) - 10) < 0.05 for pt in circle)
    assert by_type(p, "TEXT")[0]["text"]["value"] == "PART-001"
    assert by_type(p, "MTEXT")[0]["text"]["value"] == "hello\nworld"
    assert p["warnings"] == [] and p["extents"]["min"] == [0, 0]


def test_block_insert_keeps_handle(tmp_path):
    doc = ezdxf.new()
    doc.units = ezdxf.units.MM
    blk = doc.blocks.new("HOLES")
    blk.add_circle((0, 0), 1)
    blk.add_circle((5, 0), 1)
    handles = [doc.modelspace().add_blockref("HOLES", (i * 20, 0)).dxf.handle for i in range(4)]
    p = parse_dxf(save(doc, tmp_path))
    circles = by_type(p, "CIRCLE")
    assert len(circles) == 8
    assert sorted(c["handle"] for c in circles) == sorted(handles * 2)
    assert p["summary"]["block_count"] == 1


def test_inch_units_converted(tmp_path):
    doc = ezdxf.new()
    doc.units = ezdxf.units.IN
    doc.modelspace().add_line((0, 0), (2, 0))
    p = parse_dxf(save(doc, tmp_path))
    assert by_type(p, "LINE")[0]["paths"] == [[[0, 0], [50.8, 0]]]
    assert "UNITS_CONVERTED:in" in p["warnings"]


def test_unitless_assumed_mm(tmp_path):
    doc = ezdxf.new()
    doc.units = 0
    doc.modelspace().add_line((0, 0), (1, 0))
    doc.modelspace().add_point((0, 0))
    p = parse_dxf(save(doc, tmp_path))
    assert "UNITS_ASSUMED_MM" in p["warnings"] and "UNSUPPORTED_ENTITY:POINT x1" in p["warnings"]


def test_nested_block_depth_limit(tmp_path):
    doc = ezdxf.new()
    doc.blocks.new("B0").add_line((0, 0), (1, 0))
    for i in range(1, 20):
        doc.blocks.new(f"B{i}").add_blockref(f"B{i - 1}", (0, 0))
    doc.modelspace().add_blockref("B19", (0, 0))
    with pytest.raises(DxfError) as exc:
        parse_dxf(save(doc, tmp_path))
    assert exc.value.code == "DXF_BLOCK_LIMIT"


def test_validate_dxf_bytes():
    for bad in (b"", b"0\nSECTION\n\x00", b"\x00\x01binary junk", b"hello world", b"0\nEOF\n"):
        with pytest.raises(DxfError) as exc:
            validate_dxf_bytes(bad)
        assert exc.value.code == "DXF_INVALID_FILE"
    validate_dxf_bytes(b"999\ncomment\n  0\nSECTION\n  2\nHEADER\n")
    validate_dxf_bytes(b"\xef\xbb\xbf  0\r\nSECTION\r\n")
    validate_dxf_bytes(b"AutoCAD Binary DXF\r\n\x1a\x00rest")


def test_timeout_and_corrupt(tmp_path):
    bad = tmp_path / "bad.dxf"
    bad.write_text("  0\nSECTION\n  2\nENTITIES\n  0\nLINE\n  8\n")
    with pytest.raises(DxfError) as exc:
        parse_dxf_with_timeout(str(bad))
    assert exc.value.code == "DXF_PARSE_ERROR"
    good = save(ezdxf.new(), tmp_path)
    with pytest.raises(DxfError) as exc:
        parse_dxf_with_timeout(good, timeout_s=0.001)
    assert exc.value.code == "DXF_PARSE_TIMEOUT"


def upload(client, world, content, name="Drawing.DXF", who="designer"):
    return client.post(
        f"/api/documents/{world.doc}/dxf", files={"file": (name, content)}, headers=world.h[who]
    )


def test_api_upload_and_render(client, world, mm_doc, tmp_path):
    save(mm_doc, tmp_path)
    r = upload(client, world, (tmp_path / "a.dxf").read_bytes())
    body = r.json()
    assert r.status_code == 200 and body["success"] and body["error"] is None
    rid = body["data"]["revision_id"]
    r = client.get(f"/api/revisions/{rid}/render", headers=world.h["viewer"])
    assert r.json()["data"]["summary"]["entity_count"] == body["data"]["entity_count"] == 6


def test_api_errors(client, world, tmp_path):
    r = upload(client, world, b"  0\nSECTION\n", "a.txt")
    assert r.status_code == 400 and r.json()["success"] is False
    for rid in ("../etc", "0" * 32, "ABC"):
        r = client.get(f"/api/revisions/{rid}/render", headers=world.h["admin"])
        assert r.status_code == 404 and r.json()["success"] is False
    corrupt = b"  0\nSECTION\n  2\nENTITIES\n  0\nLINE\n  8\n"
    r = upload(client, world, corrupt, "a.dxf")
    assert r.status_code == 422 and r.json()["error"]["code"] == "DXF_PARSE_ERROR"
    assert list((tmp_path / "var" / "uploads").iterdir()) == []


def test_minsert_expanded_and_nan_rejected(tmp_path):
    doc = ezdxf.new()
    doc.blocks.new("B").add_circle((0, 0), 1)
    ref = doc.modelspace().add_blockref("B", (0, 0))
    ref.grid(size=(2, 3), spacing=(10, 10))
    out = parse_dxf(save(doc, tmp_path))
    assert len(out["entities"]) == 6 and {e["handle"] for e in out["entities"]} == {ref.dxf.handle}
    assert not any("MINSERT" in w for w in out["warnings"])  # BUG-05: every array cell is drawn

    doc = ezdxf.new()
    doc.modelspace().add_line((0, 0), (math.nan, 0))
    with pytest.raises(DxfError) as exc:
        parse_dxf(save(doc, tmp_path, "nan.dxf"))
    assert exc.value.code == "DXF_INVALID_GEOMETRY"


def test_api_hides_parser_detail_and_rejects_oversized(client, world):
    corrupt = b"  0\nSECTION\n  2\nENTITIES\n  0\nLINE\n  8\n"
    r = upload(client, world, corrupt, "a.dxf")
    assert "Error" not in r.json()["error"]["message"]  # no exception class/paths leak
    r = client.post(
        f"/api/documents/{world.doc}/dxf",
        content=b"x",
        headers={"content-length": str(60 * 1024 * 1024), "content-type": "multipart/form-data"},
    )
    assert r.status_code == 413 and r.json()["error"]["code"] == "FILE_TOO_LARGE"
