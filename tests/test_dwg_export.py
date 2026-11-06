"""Track 10: DWG save, AutoCAD versions, the original DWG and its conversion report, batch import
and the converter status — through a stand-in ODA File Converter (TC-181~182)."""

import io
import os

import ezdxf
import pytest

from core.dxf import dwg
from core.dxf.reader import DxfError
from tests.test_dwg import DWG, fake

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the stand-in converter is a POSIX script")

# ODA: <in dir> <out dir> <version> <type> <recurse> <audit> <filter>. DWG in -> a two-object DXF
# (with a 3D mesh, which AX-CAD reports as lost); DXF in -> DWG bytes starting with the
# version string, or the same DXF with the version noted, so the test can see what was asked.
ODA = """
import ezdxf
src, out, ver, kind, filt = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[7]
open(os.path.join(src, '..', 'args.log'), 'w').write(' '.join(sys.argv[3:]))
open(LOG, 'a').write(' '.join(sys.argv[3:]) + '\\n')
if filt.endswith('.dwg'):
    d = ezdxf.new(); d.units = 4
    d.modelspace().add_line((0, 0), (25, 0)); d.modelspace().add_circle((0, 0), 5)
    me = d.modelspace().add_mesh()
    with me.edit_data() as md:
        md.vertices = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]; md.faces = [(0, 1, 2)]
    d.saveas(os.path.join(out, 'drawing.dxf'))
elif kind == 'DWG':
    head = {'ACAD2018': b'AC1032', 'ACAD2000': b'AC1015'}.get(ver, b'AC1027')
    open(os.path.join(out, 'drawing.dwg'), 'wb').write(head + b'\\x00' * 64)
else:
    text = open(os.path.join(src, 'drawing.dxf'), encoding='utf-8').read()
    open(os.path.join(out, 'drawing.dxf'), 'w', encoding='utf-8').write('999\\n' + ver + '\\n' + text)
"""


@pytest.fixture
def oda(tmp_path, monkeypatch):
    log = tmp_path / "oda.log"
    exe = fake(tmp_path, "ODAFileConverter", f"LOG = {str(log)!r}\n" + ODA)
    monkeypatch.setenv("AXCAD_DWG_CONVERTER", str(exe))
    return log


def upload(client, world, name="plate.dwg", data=DWG):
    r = client.post(
        f"/api/documents/{world.doc}/dxf",
        files={"file": (name, data)},
        headers=world.h["designer"],
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_export_versions_core(oda):
    dxf = b"0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n"
    assert dwg.export_drawing(dxf, "DWG", "2018").startswith(b"AC1032")
    assert dwg.export_drawing(dxf, "DWG", "2000").startswith(b"AC1015")
    assert dwg.export_drawing(dxf, "DXF", "R12").startswith(b"999\nACAD12\n")
    assert oda.read_text().splitlines() == [
        "ACAD2018 DWG 0 1 drawing.dxf",
        "ACAD2000 DWG 0 1 drawing.dxf",
        "ACAD12 DXF 0 1 drawing.dxf",
    ]
    for fmt, ver in (("DWG", "R12"), ("PDF", "2018"), ("DWG", "2019")):
        with pytest.raises(DxfError) as e:
            dwg.export_drawing(dxf, fmt, ver)
        assert e.value.code == "EXPORT_VERSION_INVALID"
    assert dwg.dwg_release(b"AC1018....") == "2004" and dwg.dwg_release(b"XX") is None


def test_save_needs_oda(tmp_path, monkeypatch):
    monkeypatch.delenv("AXCAD_DWG_CONVERTER", raising=False)
    assert dwg.info()["available"] is False
    with pytest.raises(DxfError) as e:
        dwg.export_drawing(b"x", "DWG", "2018")
    assert e.value.code == "DWG_CONVERTER_MISSING"
    monkeypatch.setenv("AXCAD_DWG_CONVERTER", str(fake(tmp_path, "dwg2dxf", "pass")))
    with pytest.raises(DxfError) as e:
        dwg.export_drawing(b"x", "DWG", "2018")
    assert e.value.code == "DWG_WRITE_UNSUPPORTED"  # LibreDWG reads only
    i = dwg.info()
    assert i["kind"] == "LibreDWG" and i["can_read"] and not i["can_write"] and i["versions"] == []
    assert i["program"] == "dwg2dxf" and "/" not in i["program"]  # no folder of the program


def test_windows_discovery_in_program_files(tmp_path, monkeypatch):
    monkeypatch.delenv("AXCAD_DWG_CONVERTER", raising=False)
    for v in ("26.4.0", "25.12.0", "9.0.0"):  # by number: 26 > 9
        exe = tmp_path / "ODA" / f"ODAFileConverter {v}" / "ODAFileConverter.exe"
        exe.parent.mkdir(parents=True)
        exe.write_text("")
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setattr(dwg, "WINDOWS", True)
    found = dwg._installed_oda()
    assert found is not None and "26.4.0" in str(found)


def test_api_original_report_and_save_as(client, world, oda):
    d = upload(client, world)
    rid, h = d["revision_id"], world.h["viewer"]
    # the original comes back byte for byte
    r = client.get(f"/api/revisions/{rid}/original", headers=h)
    assert r.status_code == 200 and r.content == DWG
    rep = client.get(f"/api/revisions/{rid}/conversion", headers=h).json()["data"]
    assert rep["converter"] == "ODA" and rep["clean"] and rep["source_release"] == "2018"
    assert rep["source_bytes"] == len(DWG) and rep["entities_by_type"] == {"CIRCLE": 1, "LINE": 1}
    assert rep["lost"] == ["UNSUPPORTED_ENTITY:MESH x1"]
    # save as DWG 2000 and as DXF 2004
    r = client.get(f"/api/revisions/{rid}/export?format=DWG&version=2000", headers=h)
    assert r.status_code == 200 and r.content.startswith(b"AC1015")
    assert r.headers["content-disposition"].endswith(f'{rid}-2000.dwg"')
    r = client.get(f"/api/revisions/{rid}/export?format=DXF&version=2004", headers=h)
    assert r.content.startswith(b"999\nACAD2004\n")
    assert client.get(f"/api/revisions/{rid}/export?version=2019", headers=h).status_code == 422
    r = client.get(f"/api/revisions/{rid}/export?format=DWG&version=R12", headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "EXPORT_VERSION_INVALID"
    # a DXF revision has no original
    dxf = io.StringIO()
    doc = ezdxf.new()
    doc.modelspace().add_line((0, 0), (5, 0))
    doc.write(dxf)
    d2 = upload(client, world, "x.dxf", dxf.getvalue().encode())
    r = client.get(f"/api/revisions/{d2['revision_id']}/original", headers=h)
    assert r.status_code == 404 and r.json()["error"]["code"] == "ORIGINAL_NOT_FOUND"
    assert (
        client.get(f"/api/revisions/{d2['revision_id']}/conversion", headers=h).status_code == 404
    )
    # status for the screens
    st = client.get("/api/system/dwg-converter", headers=h).json()["data"]
    assert st["kind"] == "ODA" and st["can_write"] and "2018" in st["versions"]


def test_batch_import(client, world, oda):
    dxf = io.StringIO()
    doc = ezdxf.new()
    doc.modelspace().add_line((0, 0), (5, 0))
    doc.write(dxf)
    files = [
        ("files", ("평면도 A-101.dwg", DWG)),
        ("files", ("A_101.dwg", DWG)),  # same doc no. as the first -> A_101-2
        ("files", ("detail.dxf", dxf.getvalue().encode())),
        ("files", ("notes.txt", b"hello")),
        ("files", ("broken.dwg", b"not a dwg")),
    ]
    r = client.post(
        f"/api/projects/{world.pid}/drawings/import", files=files, headers=world.h["designer"]
    )
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["imported"] == 3
    got = {i["file"]: i for i in d["items"]}
    assert got["평면도 A-101.dwg"]["doc_no"] == "A-101"
    assert got["A_101.dwg"]["doc_no"] == "A_101"
    assert got["detail.dxf"]["ok"] and not got["detail.dxf"]["warnings"][0].startswith("DWG")
    assert got["notes.txt"]["code"] == "DXF_INVALID_FILE"
    assert got["broken.dwg"]["code"] == "DWG_INVALID_FILE"
    # a second run with the same names gets new numbers; failed files left no empty documents
    again = client.post(
        f"/api/projects/{world.pid}/drawings/import",
        files=[("files", ("A_101.dwg", DWG))],
        headers=world.h["designer"],
    ).json()["data"]["items"][0]
    assert again["doc_no"] == "A_101-2"
    docs = client.get(f"/api/projects/{world.pid}/documents", headers=world.h["designer"])
    nos = {x["doc_no"] for x in docs.json()["data"]["items"]}
    assert not {"notes", "broken"} & nos
    # limits and roles
    too_many = [("files", (f"f{i}.dxf", b"x")) for i in range(21)]
    r = client.post(
        f"/api/projects/{world.pid}/drawings/import", files=too_many, headers=world.h["designer"]
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "IMPORT_TOO_MANY"
    r = client.post(
        f"/api/projects/{world.pid}/drawings/import", files=files[:1], headers=world.h["viewer"]
    )
    assert r.status_code == 403


def test_security_review_fixes(client, world, oda, monkeypatch):
    """BUG-59: an export is converted once per revision/format/version; a batch file that
    fails for any reason (disk full) leaves no document and the others go on; the batch has a
    time budget and a body limit."""
    d = upload(client, world)
    h = world.h["viewer"]
    before = len(oda.read_text().splitlines())
    for _ in range(3):
        r = client.get(
            f"/api/revisions/{d['revision_id']}/export?format=DWG&version=2010", headers=h
        )
        assert r.status_code == 200 and r.content.startswith(b"AC1027")
    assert len(oda.read_text().splitlines()) == before + 1  # converted once

    import backend.api.main as m

    real = m._upload

    def flaky(db, user, document_id, name, data, note):
        if name.startswith("bad"):
            raise OSError("No space left on device")
        return real(db, user, document_id, name, data, note)

    monkeypatch.setattr(m, "_upload", flaky)
    files = [("files", ("bad.dwg", DWG)), ("files", ("good.dwg", DWG))]
    r = client.post(
        f"/api/projects/{world.pid}/drawings/import", files=files, headers=world.h["designer"]
    ).json()["data"]
    assert [(i["file"], i["ok"], i.get("code")) for i in r["items"]] == [
        ("bad.dwg", False, "IMPORT_FAILED"),
        ("good.dwg", True, None),
    ]
    nos = {
        x["doc_no"]
        for x in client.get(f"/api/projects/{world.pid}/documents", headers=h).json()["data"][
            "items"
        ]
    }
    assert "bad" not in nos and "good" in nos
    monkeypatch.setattr(m, "IMPORT_BUDGET_S", -1)
    r = client.post(
        f"/api/projects/{world.pid}/drawings/import",
        files=[("files", ("late.dwg", DWG))],
        headers=world.h["designer"],
    ).json()["data"]
    assert r["items"][0]["code"] == "IMPORT_TIME_LIMIT" and r["imported"] == 0
    big = client.post(
        f"/api/projects/{world.pid}/drawings/import",
        content=b"x",
        headers={**world.h["designer"], "content-length": str(m.MAX_IMPORT_BODY + 1)},
    )
    assert big.status_code == 413
