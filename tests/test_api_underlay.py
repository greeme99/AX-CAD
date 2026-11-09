"""2D underlays U1 (TC-195): upload, dedupe, permissions, vectors, images, delete."""

import io
import os

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
