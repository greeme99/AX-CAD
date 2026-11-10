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
    return {e["geom"]["type"]: e["handle"] for e in p["entities"] if "geom" in e}


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
    assert set(h) == {"LINE", "CIRCLE", "ARC", "LWPOLYLINE", "TEXT", "INSERT"}
    pieces = [e for e in p0["entities"] if e["handle"] == h["INSERT"]]
    assert [e["geom"]["type"] for e in pieces if "geom" in e] == ["INSERT"]  # one piece carries it
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
    ins = [e for e in p1["entities"] if e["handle"] == h["INSERT"] and e["type"] == "CIRCLE"]
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


# --- W7a (plan §22.8): entity properties, MTEXT, paper space -------------------------------------

MT = {
    "type": "MTEXT",
    "insert": [10, 20],
    "height": 3.5,
    "width": 40,
    "value": "첫 줄\n둘째 줄",
    "rotation": 30,
    "attachment": 7,
}


def test_w7a_properties_round_trip(doc, tmp_path):
    p0_src = str(tmp_path / "s0.dxf")
    doc.saveas(p0_src)
    h = handles(parse_dxf(p0_src))
    props = {
        "lineweight": 50,
        "true_color": 0x336699,
        "transparency": 40,
        "ltscale": 2.0,
        "visible": False,
    }
    edits = {
        "created": [{"layer": "CUT", "geom": MT, "color": 1, **props}],
        "modified": [
            {
                "handle": h["CIRCLE"],
                "layer": "0",
                "geom": {"type": "CIRCLE", "center": [50, 50], "radius": 10},
                "lineweight": 25,
            }
        ],
    }
    dst = edit(doc, tmp_path, edits)[1]
    d = ezdxf.readfile(dst)
    mt = d.modelspace().query("MTEXT").first
    assert mt.dxf.lineweight == 50 and mt.dxf.true_color == 0x336699 and mt.dxf.color == 1
    assert round(mt.transparency, 2) == 0.4 and mt.dxf.ltscale == 2.0 and mt.dxf.invisible == 1
    assert mt.text == "첫 줄\\P둘째 줄" and mt.dxf.attachment_point == 7 and mt.dxf.width == 40
    p = parse_dxf(dst)
    rec = next(e for e in p["entities"] if e.get("geom", {}).get("type") == "MTEXT")
    assert rec["geom"]["value"] == "첫 줄\\P둘째 줄" and rec["geom"]["rotation"] == 30
    assert rec["lwt"] == 50 and rec["tc"] == 0x336699 and rec["tr"] == 40 and rec["inv"] is True
    assert next(e for e in p["entities"] if e["handle"] == h["CIRCLE"])["lwt"] == 25
    # an MTEXT is edited like any other: modify it, then read it back
    m = {**MT, "value": "바뀐 글", "insert": [0, 0]}
    d.saveas(str(tmp_path / "s1.dxf"))
    apply_edits(
        str(tmp_path / "s1.dxf"),
        str(tmp_path / "d1.dxf"),
        {"modified": [{"handle": rec["handle"], "layer": "CUT", "geom": m}]},
    )
    rec2 = next(
        e for e in parse_dxf(str(tmp_path / "d1.dxf"))["entities"] if e["handle"] == rec["handle"]
    )
    assert (
        rec2["geom"]["value"] == "바뀐 글"
        and rec2["geom"]["insert"] == [0, 0]
        and rec2["lwt"] == 50
    )


def test_w7a_paper_space(doc, tmp_path):
    dst = edit(doc, tmp_path, {"created": [{"layer": "CUT", "geom": LINE, "space": "Layout1"}]})[1]
    d = ezdxf.readfile(dst)
    line = d.layouts.get("Layout1").query("LINE").first
    assert line is not None and not d.modelspace().query(
        "LINE[layer=='CUT']"
    ).first.dxf.end.isclose((5, 5))
    # a sheet entity is not reached by handle until the editor shows paper space geometry
    d.saveas(str(tmp_path / "s2.dxf"))
    with pytest.raises(DxfError) as gone:
        apply_edits(
            str(tmp_path / "s2.dxf"), str(tmp_path / "d2.dxf"), {"deleted": [line.dxf.handle]}
        )
    assert gone.value.code == "EDIT_HANDLE_NOT_FOUND"
    with pytest.raises(DxfError) as ex:
        edit(doc, tmp_path, {"created": [{"layer": "CUT", "geom": LINE, "space": "NoSuchSheet"}]})
    assert ex.value.code == "EDIT_LAYOUT_NOT_FOUND"


def test_w7a_old_dxf_versions_refuse_new_properties(tmp_path):
    d = ezdxf.new("R12")
    d.saveas(str(tmp_path / "r12.dxf"))
    for extra in ({"lineweight": 25}, {"true_color": 1}, {"transparency": 10}):
        with pytest.raises(DxfError):
            apply_edits(
                str(tmp_path / "r12.dxf"),
                str(tmp_path / "o.dxf"),
                {"created": [{"layer": "0", "geom": LINE, **extra}]},
            )


def test_w7a_api_validation():
    from backend.api.main import EditRequest

    ok = {"created": [{"layer": "0", "geom": MT, "lineweight": 35, "space": "Layout1"}]}
    EditRequest.model_validate(ok)
    bad = [
        {"created": [{"layer": "0", "geom": {**MT, "value": "a\x00b"}}]},
        {"created": [{"layer": "0", "geom": MT, "lineweight": 7}]},
        {"created": [{"layer": "0", "geom": MT, "transparency": 95}]},
        {"created": [{"layer": "0", "geom": MT, "true_color": 0x1000000}]},
        {"created": [{"layer": "0", "geom": {**MT, "value": "a\u2028b"}}]},
        {"created": [{"layer": "0", "geom": {**MT, "value": "끝\\\nP"}}]},
        {
            "created": [
                {
                    "layer": "0",
                    "geom": {
                        "type": "TEXT",
                        "insert": [0, 0],
                        "height": 2,
                        "value": "a\n0\nLINE",
                        "rotation": 0,
                    },
                }
            ]
        },
        {
            "created": [
                {
                    "layer": "0",
                    "geom": {
                        "type": "MULTILEADER",
                        "vertices": [[0, 0], [5, 5]],
                        "value": "a\x00",
                        "height": 2,
                    },
                }
            ]
        },
        {
            "blocks": [
                {
                    "name": "BX",
                    "base": [0, 0],
                    "entities": [{"layer": "0", "geom": LINE, "space": "Layout1"}],
                }
            ]
        },
        {"created": [{"layer": "0", "geom": {**MT, "attachment": 10}}]},
        {"modified": [{"handle": "1A", "layer": "0", "geom": MT, "space": "Layout1"}]},
    ]
    import pydantic

    for b in bad:
        with pytest.raises(pydantic.ValidationError):
            EditRequest.model_validate(b)


def test_w7a_long_text_stays_read_only(tmp_path):
    d = ezdxf.new(setup=True)
    d.modelspace().add_mtext("x" * 10_001)
    d.modelspace().add_mtext("짧은 글", dxfattribs={"char_height": 2})
    d.saveas(str(tmp_path / "long.dxf"))
    kinds = [
        e.get("geom", {}).get("type") for e in parse_dxf(str(tmp_path / "long.dxf"))["entities"]
    ]
    assert kinds.count("MTEXT") == 1  # only the short one is editable


def test_w7a_crlf_becomes_a_paragraph_and_r12_has_no_mtext(doc, tmp_path):
    dst = edit(doc, tmp_path, {"created": [{"layer": "0", "geom": {**MT, "value": "a\r\nb"}}]})[1]
    assert ezdxf.readfile(dst).modelspace().query("MTEXT").first.text == "a\\Pb"
    r12 = ezdxf.new("R12")
    r12.saveas(str(tmp_path / "r12m.dxf"))
    with pytest.raises(DxfError):
        apply_edits(
            str(tmp_path / "r12m.dxf"),
            str(tmp_path / "o.dxf"),
            {"created": [{"layer": "0", "geom": MT}]},
        )
