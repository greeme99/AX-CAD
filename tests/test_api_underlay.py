"""2D underlays U1 (TC-195): upload, dedupe, permissions, vectors, images, delete."""

import io
import os

import pytest
from PIL import Image

from backend.api import routes_underlay
from tests import underlay_samples as S


def up(client, pid, data, headers, name="plan.pdf"):
    return client.post(
        f"/api/projects/{pid}/underlays",
        files={"file": (name, data, "application/pdf")},
        headers=headers,
    )


def test_upload_dedupe_list_and_permissions(client, world, make_user, headers):
    d, v = world.h["designer"], world.h["viewer"]
    r = up(client, world.pid, S.vector_pdf(), d, "../../etc/pla<n>.pdf")
    assert r.status_code == 200, r.text
    row = r.json()["data"]
    assert row["kind"] == "pdf" and row["page_count"] == 1 and row["name"] == "pla_n_.pdf"
    assert row["pages"][0]["width"] > 200 and len(row["sha256"]) == 64
    assert os.path.isfile(routes_underlay._dir() / f"{row['sha256']}.pdf")
    assert (
        up(client, world.pid, S.vector_pdf(), d, "again.pdf").json()["data"]["underlay_id"]
        == row["underlay_id"]
    )
    dx = up(client, world.pid, S.dwfx(), d, "t.dwfx").json()["data"]
    assert dx["kind"] == "dwfx"
    items = client.get(f"/api/projects/{world.pid}/underlays", headers=v).json()["data"]["items"]
    assert [i["underlay_id"] for i in items] == [row["underlay_id"], dx["underlay_id"]]
    # a viewer reads but does not upload or delete; a non-member sees nothing (404, not 403)
    assert up(client, world.pid, S.vector_pdf(), v).status_code == 403
    assert client.delete(f"/api/underlays/{row['underlay_id']}", headers=v).status_code == 403
    make_user("outsider", "DESIGNER")
    o = headers("outsider")
    assert client.get(f"/api/underlays/{row['underlay_id']}", headers=o).status_code == 404
    assert client.get(f"/api/projects/{world.pid}/underlays", headers=o).status_code == 404
    assert up(client, world.pid, S.vector_pdf(), o).status_code == 404
    assert client.get("/api/underlays/" + "0" * 32, headers=d).status_code == 404


def test_what_is_refused(client, world, monkeypatch):
    d = world.h["designer"]
    r = up(client, world.pid, S.classic_dwf(), d, "old.dwf")
    assert r.status_code == 422 and r.json()["error"]["code"] == "UNDERLAY_DWF_CLASSIC"
    assert (
        up(client, world.pid, S.dwf_package(), d, "pkg.dwf").json()["error"]["code"]
        == "UNDERLAY_DWF_CLASSIC"
    )
    for bad in (b"", b"hello", b"%PDF-1.5\nnot really", b"PK\x03\x04junk"):
        assert up(client, world.pid, bad, d).status_code == 422
    assert not list(routes_underlay._dir().glob("*.pdf"))  # nothing unparsable is kept
    assert not list(routes_underlay._dir().glob("*.tmp"))
    monkeypatch.setattr(routes_underlay, "MAX_UNDERLAY_BYTES", 1000)
    big = up(client, world.pid, S.vector_pdf(), d)
    assert big.status_code == 413 and big.json()["error"]["code"] == "UNDERLAY_TOO_BIG"
    monkeypatch.setattr(routes_underlay, "MAX_UNDERLAY_BYTES", 10**8)
    monkeypatch.setattr(routes_underlay, "MAX_PER_PROJECT", 1)
    assert up(client, world.pid, S.vector_pdf(), d).status_code == 200
    assert up(client, world.pid, S.dwfx(), d).json()["error"]["code"] == "UNDERLAY_LIMIT"


def test_vectors_cache_and_images(client, world):
    d = world.h["designer"]
    row = up(client, world.pid, S.vector_pdf(), d).json()["data"]
    uid = row["underlay_id"]
    r = client.get(f"/api/underlays/{uid}/pages/0/vectors", headers=d)
    assert r.status_code == 200
    vec = r.json()["data"]
    assert (
        vec["layers"] == ["Walls", "Doors"]
        and len(vec["arcs"]) == 1
        and vec["texts"][0]["v"] == "Hello PDF"
    )
    cache = routes_underlay._dir() / f"{row['sha256']}.p0.json"
    assert cache.is_file()
    cache.write_text("{broken", encoding="utf-8")  # a damaged cache is rebuilt
    assert client.get(f"/api/underlays/{uid}/pages/0/vectors", headers=d).json()["data"] == vec
    assert client.get(f"/api/underlays/{uid}/pages/1/vectors", headers=d).status_code == 404
    assert client.get(f"/api/underlays/{uid}/pages/-1/vectors", headers=d).status_code == 404

    img = client.get(f"/api/underlays/{uid}/pages/0/image?width=300", headers=d)
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    assert Image.open(io.BytesIO(img.content)).size[0] == 300
    hidden = client.get(f"/api/underlays/{uid}/pages/0/image?width=300&hide=Walls", headers=d)
    assert hidden.content != img.content
    win = client.get(
        f"/api/underlays/{uid}/pages/0/image?width=200&x0=0&y0=0&x1=100&y1=50", headers=d
    )
    assert Image.open(io.BytesIO(win.content)).size == (200, 100)
    for q in (
        "width=8",  # under the smallest
        "width=99999",
        "x0=0&y0=0&x1=10",  # a part of a window
        "x0=10&y0=0&x1=5&y1=5",  # nothing inside
        "x0=0&y0=0&x1=nan&y1=5",
        "x0=0&y0=0&x1=1&y1=1000&width=8000",  # too many pixels
    ):
        assert client.get(f"/api/underlays/{uid}/pages/0/image?{q}", headers=d).status_code in (
            422,
            400,
        ), q


def test_dwfx_image_and_delete_keeps_shared_content(client, world):
    d, admin = world.h["designer"], world.h["admin"]
    row = up(client, world.pid, S.dwfx(), d, "a.dwfx").json()["data"]
    png = client.get(f"/api/underlays/{row['underlay_id']}/pages/0/image?width=400", headers=d)
    assert png.status_code == 200 and Image.open(io.BytesIO(png.content)).size == (400, 300)
    # a second project with the same content shares the stored bytes
    p2 = client.post(
        "/api/projects", json={"project_code": "P2", "project_name": "P2"}, headers=admin
    ).json()["data"]["project_id"]
    row2 = up(client, p2, S.dwfx(), admin, "b.dwfx").json()["data"]
    assert row2["underlay_id"] != row["underlay_id"] and row2["sha256"] == row["sha256"]
    path = routes_underlay._dir() / f"{row['sha256']}.dwfx"
    assert client.delete(f"/api/underlays/{row['underlay_id']}", headers=d).status_code == 200
    assert path.is_file()  # the other project still uses it
    assert client.get(f"/api/underlays/{row['underlay_id']}", headers=d).status_code == 404
    assert client.delete(f"/api/underlays/{row2['underlay_id']}", headers=admin).status_code == 200
    assert not path.exists()  # the last reference is gone


# --- U2: underlays in the drawing's DXF ------------------------------------------------------------


def blank_rev(client, world):
    r = client.post(
        f"/api/documents/{world.doc}/blank", json={"sheet": "A3"}, headers=world.h["designer"]
    )
    return r.json()["data"]["revision_id"]


def geom(sha, **kw):
    g = {
        "format": "pdf",
        "sha256": sha,
        "name": "plan.pdf",
        "page": 0,
        "insert": [100, 50],
        "scale": 1.0,
        "rotation": 0,
        "flags": {"on": True, "monochrome": False, "adjust": False, "inside": True},
        "contrast": 50,
        "fade": 0,
        "clip": None,
    }
    g.update(kw)
    return g


def edit(client, rev, h, ops):
    return client.post(f"/api/revisions/{rev}/edits", json={"underlays": ops}, headers=h)


def underlays_of(client, rev, h):
    data = client.get(f"/api/revisions/{rev}/render", headers=h).json()["data"]
    return data, [e for e in data["entities"] if e["type"] == "UNDERLAY"]


def test_underlay_round_trip_through_the_drawing(client, world):
    d = world.h["designer"]
    row = up(client, world.pid, S.vector_pdf(), d).json()["data"]
    rev = blank_rev(client, world)
    clip = {"kind": "poly", "points": [[0, 0], [100, 0], [100, 50], [20, 80]]}
    g = geom(
        row["sha256"],
        scale=0.5,
        rotation=30,
        fade=40,
        contrast=70,
        clip=clip,
        flags={"on": True, "monochrome": True, "adjust": False, "inside": True},
    )
    r = edit(client, rev, d, [{"op": "create", "layer": "OUTLINE", "geom": g}])
    assert r.status_code == 200, r.text
    rev2 = r.json()["data"]["revision_id"]
    _, (u,) = underlays_of(client, rev2, d)
    assert u["layer"] == "OUTLINE" and u["paths"] == []
    got = u["underlay"]
    assert (
        got["linked"]
        and got["sha256"] == row["sha256"]
        and got["name"] == "plan.pdf"
        and got["format"] == "pdf"
    )
    assert (
        got["insert"] == [100, 50]
        and got["scale"] == 0.5
        and got["rotation"] == 30
        and got["page"] == 0
    )
    assert (
        got["fade"] == 40
        and got["contrast"] == 70
        and got["flags"]["monochrome"]
        and got["flags"]["on"]
    )
    assert got["clip"] == {"kind": "poly", "points": clip["points"]} and got["uniform"]
    # modify: move, other page of a DWFx would need that file; here page stays, rect clip, hidden
    g2 = geom(
        row["sha256"],
        insert=[10, 20],
        scale=2.0,
        rotation=-15,
        clip={"kind": "rect", "points": [[5, 5], [60, 40]]},
        flags={"on": False, "monochrome": False, "adjust": True, "inside": False},
    )
    r2 = edit(client, rev2, d, [{"op": "modify", "handle": u["handle"], "geom": g2}])
    assert r2.status_code == 200, r2.text
    _, (u2,) = underlays_of(client, r2.json()["data"]["revision_id"], d)
    assert (
        u2["handle"] == u["handle"]
        and u2["underlay"]["insert"] == [10, 20]
        and u2["underlay"]["scale"] == 2.0
    )
    assert u2["underlay"]["clip"] == {"kind": "rect", "points": [[5, 5], [60, 40]]}
    assert u2["underlay"]["flags"] == {
        "on": False,
        "monochrome": False,
        "adjust": True,
        "inside": False,
    }
    # delete: nothing left
    r3 = edit(
        client, r2.json()["data"]["revision_id"], d, [{"op": "delete", "handle": u["handle"]}]
    )
    assert r3.status_code == 200
    assert underlays_of(client, r3.json()["data"]["revision_id"], d)[1] == []


def test_underlay_edits_are_checked(client, world, make_user, headers):
    d = world.h["designer"]
    row = up(client, world.pid, S.vector_pdf(), d).json()["data"]
    dx = up(client, world.pid, S.dwfx(), d, "t.dwfx").json()["data"]
    rev = blank_rev(client, world)
    ok = [{"op": "create", "layer": "OUTLINE", "geom": geom(row["sha256"])}]
    assert edit(client, rev, world.h["viewer"], ok).status_code == 403
    bad = [
        (
            [{"op": "create", "layer": "OUTLINE", "geom": geom("a" * 64)}],
            "EDIT_UNDERLAY_NOT_FOUND",
        ),  # not a file of this project
        (
            [{"op": "create", "layer": "OUTLINE", "geom": geom(row["sha256"], page=3)}],
            "EDIT_UNDERLAY_PAGE",
        ),
        (
            [{"op": "create", "layer": "OUTLINE", "geom": geom(row["sha256"], format="dwf")}],
            "EDIT_UNDERLAY_PAGE",
        ),  # a pdf is not a dwfx
        ([{"op": "create", "layer": "NOPE", "geom": geom(row["sha256"])}], "EDIT_LAYER_NOT_FOUND"),
        ([{"op": "delete", "handle": "FFFF"}], "EDIT_HANDLE_NOT_FOUND"),
    ]
    for ops, code in bad:
        r = edit(client, rev, d, ops)
        assert r.status_code == 422 and r.json()["error"]["code"] == code, (code, r.text)
    for ops in (
        [{"op": "create", "layer": "OUTLINE"}],  # no geometry
        [{"op": "delete", "handle": "1", "geom": geom(row["sha256"])}],
        [{"op": "modify", "geom": geom(row["sha256"])}],  # no handle
        [{"op": "create", "layer": "OUTLINE", "geom": geom(row["sha256"], scale=0)}],
        [{"op": "create", "layer": "OUTLINE", "geom": geom(row["sha256"], scale="nan")}],
        [{"op": "create", "layer": "OUTLINE", "geom": geom(row["sha256"], fade=99)}],
        [
            {"op": "create", "layer": "OUTLINE", "geom": geom(row["sha256"], name="a\nb")}
        ],  # no DXF injection
        [
            {
                "op": "create",
                "layer": "OUTLINE",
                "geom": geom(row["sha256"], clip={"kind": "rect", "points": [[0, 0], [0, 5]]}),
            }
        ],
        [
            {
                "op": "create",
                "layer": "OUTLINE",
                "geom": geom(row["sha256"], clip={"kind": "poly", "points": [[0, 0], [1, 1]]}),
            }
        ],
        [{"op": "create", "layer": "OUTLINE", "geom": geom("../../etc/passwd")}],
    ):
        assert edit(client, rev, d, ops).status_code == 422, ops
    # another project's file cannot be used, even by its sha
    admin = world.h["admin"]
    p2 = client.post(
        "/api/projects", json={"project_code": "P2", "project_name": "P2"}, headers=admin
    ).json()["data"]["project_id"]
    other = up(client, p2, S.scan_pdf(), admin).json()["data"]
    r = edit(client, rev, d, [{"op": "create", "layer": "OUTLINE", "geom": geom(other["sha256"])}])
    assert r.json()["error"]["code"] == "EDIT_UNDERLAY_NOT_FOUND"
    # a dwfx is placed as format "dwf"
    r = edit(
        client,
        rev,
        d,
        [
            {
                "op": "create",
                "layer": "OUTLINE",
                "geom": geom(dx["sha256"], format="dwf", name="t.dwfx"),
            }
        ],
    )
    assert r.status_code == 200, r.text
    _, (u,) = underlays_of(client, r.json()["data"]["revision_id"], d)
    assert u["underlay"]["format"] == "dwf"


def test_a_foreign_path_is_shown_but_never_followed(client, world):
    """An AutoCAD drawing with a PDF underlay on the user's disk: not linked, the name only."""
    import ezdxf

    doc = ezdxf.new("R2018")
    doc.units = ezdxf.units.MM
    ud = doc.add_underlay_def(r"C:\Users\me\secret\plan.pdf", fmt="pdf", name="3")
    doc.modelspace().add_underlay(ud, insert=(1, 2, 0), scale=(2, 3, 2))  # skewed scale
    buf = io.StringIO()
    doc.write(buf)
    r = client.post(
        f"/api/documents/{world.doc}/dxf",
        files={"file": ("a.dxf", buf.getvalue().encode(), "application/dxf")},
        headers=world.h["designer"],
    )
    rev = r.json()["data"]["revision_id"]
    assert any(w.startswith("UNDERLAY_NOT_LINKED") for w in r.json()["data"]["warnings"]), r.text
    assert any(w.startswith("UNDERLAY_SCALE_SKEWED") for w in r.json()["data"]["warnings"])
    _, (u,) = underlays_of(client, rev, world.h["designer"])
    got = u["underlay"]
    assert (
        got["linked"] is False
        and got["sha256"] is None
        and got["name"] == "plan.pdf"
        and got["page"] == 2
    )
    assert got["scale"] == 2.0 and got["uniform"] is False and got["insert"] == [1, 2]
    assert "secret" not in str(got)


def test_underlay_units_follow_the_drawing(client, world):
    """A drawing in inches: the insert and the scale are stored in drawing units."""
    import ezdxf

    d = world.h["designer"]
    row = up(client, world.pid, S.vector_pdf(), d).json()["data"]
    doc = ezdxf.new("R2018")
    doc.units = ezdxf.units.IN
    doc.layers.add("A")
    buf = io.StringIO()
    doc.write(buf)
    rev = client.post(
        f"/api/documents/{world.doc}/dxf",
        files={"file": ("i.dxf", buf.getvalue().encode(), "application/dxf")},
        headers=d,
    ).json()["data"]["revision_id"]
    r = edit(
        client,
        rev,
        d,
        [
            {
                "op": "create",
                "layer": "A",
                "geom": geom(row["sha256"], insert=[25.4, 50.8], scale=25.4),
            }
        ],
    )
    assert r.status_code == 200, r.text
    new = r.json()["data"]["revision_id"]
    _, (u,) = underlays_of(client, new, d)
    assert u["underlay"]["insert"] == [25.4, 50.8] and u["underlay"]["scale"] == 25.4  # back in mm
    raw = (routes_underlay._dir().parent / "uploads" / f"{new}.dxf").read_text()
    out = ezdxf.read(io.StringIO(raw))
    (e,) = [x for x in out.modelspace() if x.dxftype() == "PDFUNDERLAY"]
    assert tuple(e.dxf.insert)[:2] == pytest.approx((1.0, 2.0), abs=1e-6)  # inches
    assert e.dxf.scale_x == pytest.approx(1.0, abs=1e-6)
