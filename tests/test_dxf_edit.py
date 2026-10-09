import ezdxf
import pytest

from core.dxf.reader import DxfError, parse_dxf
from core.dxf.writer import apply_edits

LINE = {"type": "LINE", "start": [0, 0], "end": [5, 5]}


@pytest.fixture
def doc():
    d = ezdxf.new(setup=True)
    d.units = ezdxf.units.MM
    d.layers.add("CUT")
    d.layers.add("LOCK").lock()
    blk = d.blocks.new("B")
    blk.add_circle((0, 0), 1)
    msp = d.modelspace()
    msp.add_line((0, 0), (100, 0), dxfattribs={"layer": "CUT"})
    msp.add_circle((50, 50), 10)
    msp.add_arc((0, 0), 5, 0, 90)
    msp.add_lwpolyline([(0, 0, 0.5), (10, 0), (10, 10), (0, 10)], format="xyb", close=True)
    msp.add_text("PART", height=2.5, dxfattribs={"insert": (1, 2)})
    msp.add_blockref("B", (5, 5))
    msp.add_line((1, 1), (2, 2), dxfattribs={"layer": "LOCK"})
    return d


def handles(p):
    return {e["type"]: e["handle"] for e in p["entities"] if "geom" in e}


def edit(doc, tmp_path, edits):
    src, dst = str(tmp_path / "s.dxf"), str(tmp_path / "d.dxf")
    doc.saveas(src)
    apply_edits(src, dst, edits)
    return src, dst


def test_round_trip(doc, tmp_path):
    src = str(tmp_path / "s.dxf")
    doc.saveas(src)
    p0 = parse_dxf(src)
    h = handles(p0)
    assert set(h) == {"LINE", "CIRCLE", "ARC", "LWPOLYLINE", "TEXT"}
    assert all(
        "geom" not in e
        for e in p0["entities"]
        if e["type"] == "CIRCLE" and e["handle"] not in h.values()
    )
    assert next(la for la in p0["layers"] if la["name"] == "LOCK")["locked"] is True
    circle = {"type": "CIRCLE", "center": [50, 50], "radius": 20}
    edits = {
        "created": [{"layer": "CUT", "geom": LINE}],
        "modified": [{"handle": h["CIRCLE"], "layer": "0", "geom": circle}],
        "deleted": [h["ARC"]],
    }
    p1 = parse_dxf(edit(doc, tmp_path, edits)[1])
    ents = {e["handle"]: e for e in p1["entities"]}
    assert h["ARC"] not in ents and ents[h["CIRCLE"]]["geom"]["radius"] == 20
    for k in ("LINE", "LWPOLYLINE", "TEXT"):
        assert ents[h[k]]["geom"] == next(e for e in p0["entities"] if e["handle"] == h[k])["geom"]
    new = [e for e in p1["entities"] if e["handle"] not in {e0["handle"] for e0 in p0["entities"]}]
    assert [e["geom"] for e in new] == [LINE] and new[0]["layer"] == "CUT"
    assert p1["summary"]["block_count"] == p0["summary"]["block_count"]
    ins = [e for e in p1["entities"] if "geom" not in e and e["type"] == "CIRCLE"]
    assert len(ins) == 1 and ins[0]["paths"]  # block-expanded circle still rendered


def test_inch_scaling(tmp_path):
    d = ezdxf.new()
    d.units = ezdxf.units.IN
    handle = d.modelspace().add_line((0, 0), (1, 0)).dxf.handle
    geom = {"type": "LINE", "start": [0, 0], "end": [254, 0]}
    _, dst = edit(d, tmp_path, {"modified": [{"handle": handle, "layer": "0", "geom": geom}]})
    assert ezdxf.readfile(dst).modelspace()[0].dxf.end.x == pytest.approx(10)
    assert parse_dxf(dst)["entities"][0]["geom"]["end"] == [254, 0]


def test_rejections(doc, tmp_path):
    msp = doc.modelspace()
    h = {e.dxftype() + e.dxf.layer: e.dxf.handle for e in msp}
    block_circle = next(iter(doc.blocks.get("B"))).dxf.handle
    circle = {"type": "CIRCLE", "center": [0, 0], "radius": 1}
    mod = lambda hd, layer, g: {"modified": [{"handle": hd, "layer": layer, "geom": g}]}
    cases = {
        "EDIT_HANDLE_NOT_FOUND": [
            {"deleted": ["FFFF"]},
            {"deleted": [h["INSERT0"]]},
            {"deleted": [block_circle]},
        ],
        "EDIT_INVALID": [mod(h["LINECUT"], "CUT", circle)],
        "EDIT_LAYER_NOT_FOUND": [
            mod(h["LINECUT"], "NOPE", LINE),
            {"created": [{"layer": "NOPE", "geom": LINE}]},
        ],
        "EDIT_LAYER_LOCKED": [
            {"created": [{"layer": "LOCK", "geom": LINE}]},
            mod(h["LINECUT"], "LOCK", LINE),
            mod(h["LINELOCK"], "CUT", LINE),
            {"deleted": [h["LINELOCK"]]},
        ],
    }
    for code, bodies in cases.items():
        for body in bodies:
            with pytest.raises(DxfError) as exc:
                edit(doc, tmp_path, body)
            assert exc.value.code == code, body


def test_api_edit_and_download(client, world, doc, tmp_path):
    h = world.h["designer"]
    doc.saveas(tmp_path / "a.dxf")
    with open(tmp_path / "a.dxf", "rb") as f:
        r = client.post(f"/api/documents/{world.doc}/dxf", files={"file": ("a.dxf", f)}, headers=h)
    rid = r.json()["data"]["revision_id"]
    r = client.post(
        f"/api/revisions/{rid}/edits", json={"created": [{"layer": "CUT", "geom": LINE}]}, headers=h
    )
    data = r.json()["data"]
    assert r.status_code == 200 and data["revision_id"] != rid and data["entity_count"] == 8
    render = client.get(f"/api/revisions/{data['revision_id']}/render", headers=h).json()["data"]
    assert render["parent_revision_id"] == rid
    r = client.get(f"/api/revisions/{data['revision_id']}/dxf", headers=h)
    assert r.headers["content-type"].startswith("application/dxf")
    (tmp_path / "out.dxf").write_bytes(r.content)
    assert len(ezdxf.readfile(tmp_path / "out.dxf").modelspace()) == 8
    assert client.get("/api/revisions/nothex/dxf", headers=h).status_code == 404
    r = client.post(f"/api/revisions/{'0' * 32}/edits", json={"deleted": ["1A"]}, headers=h)
    assert r.status_code == 404
    r = client.post(
        f"/api/revisions/{data['revision_id']}/edits", json={"deleted": ["FFFF"]}, headers=h
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "EDIT_HANDLE_NOT_FOUND"
    assert len(list((tmp_path / "var" / "uploads").iterdir())) == 2  # failed edit left nothing


def test_api_invalid_body(client, world):
    h = world.h["designer"]
    rid = "0" * 32
    bad = [
        {},
        {"created": []},
        {"created": [{"layer": "0", "geom": {"type": "CIRCLE", "center": [0, 0], "radius": 0}}]},
        {
            "created": [
                {"layer": "0", "geom": {"type": "CIRCLE", "center": [0, 1e300], "radius": 1}}
            ]
        },
        {"created": [{"layer": "0", "geom": {"type": "LINE", "start": [1, 1], "end": [1, 1]}}]},
        {"created": [{"layer": "0", "geom": {"type": "LINE", "start": ["a", 1], "end": [1, 2]}}]},
        {
            "created": [
                {
                    "layer": "0",
                    "geom": {"type": "LWPOLYLINE", "points": [[0, 0, 0]], "closed": False},
                }
            ]
        },
        {"deleted": ["../x"]},
        {"deleted": ["1A"] * 10_001},
    ]
    for body in bad:
        r = client.post(f"/api/revisions/{rid}/edits", json=body, headers=h)
        assert r.status_code == 422 and r.json()["success"] is False, body
        assert r.json()["error"]["code"] == "EDIT_INVALID"


def test_api_body_and_vertex_limits(client, world):
    h = world.h["designer"]
    r = client.post(
        f"/api/revisions/{'0' * 32}/edits", content=b"{}", headers={**h, "content-length": ""}
    )
    assert r.status_code == 400
    many = [[i, 0, 0] for i in range(10_001)]
    body = {
        "created": [{"layer": "0", "geom": {"type": "LWPOLYLINE", "points": many, "closed": False}}]
    }
    r = client.post(f"/api/revisions/{'0' * 32}/edits", json=body, headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "EDIT_INVALID"
    big = {**h, "content-length": str(6 * 1024**2), "content-type": "application/json"}
    r = client.post(f"/api/revisions/{'0' * 32}/edits", content=b"{}", headers=big)
    assert r.status_code == 413
