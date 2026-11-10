"""W7c (plan §22.8): XDATA, dictionaries / XRecords and their read API."""

import ezdxf
import pydantic
import pytest

from core.dxf.objects import dictionary_entry, entity_tags
from core.dxf.reader import DxfError
from core.dxf.writer import apply_edits

LINE = {"type": "LINE", "start": [0, 0], "end": [5, 5]}
XD = [[1000, "id-7"], [1002, "{"], [1040, 2.5], [1070, 3], [1002, "}"], [1010, [1, 2, 3]]]
XD += [[1004, "0AFF"], [1005, "1A"], [1071, 70000]]


@pytest.fixture
def src(tmp_path):
    d = ezdxf.new("R2018", setup=True)
    d.units = ezdxf.units.MM
    d.appids.add("MYAPP")
    d.layers.add("LOCK").lock()
    msp = d.modelspace()
    msp.add_line((0, 0), (10, 0))
    msp.add_line((0, 0), (1, 1), dxfattribs={"layer": "LOCK"})
    path = str(tmp_path / "s.dxf")
    d.saveas(path)
    return path


def handle_of(path, layer="0"):
    return next(e.dxf.handle for e in ezdxf.readfile(path).modelspace() if e.dxf.layer == layer)


def run(src, tmp_path, edits):
    dst = str(tmp_path / "d.dxf")
    apply_edits(src, dst, edits)
    return dst


def xdata_groups(tags, app):
    i = tags.index([1001, app])
    out = []
    for code, v in tags[i + 1 :]:
        if code == 1001:
            break
        out.append([code, v])
    return out


def test_xdata_round_trip(src, tmp_path):
    h = handle_of(src)
    dst = run(
        src,
        tmp_path,
        {
            "created": [{"layer": "0", "geom": LINE, "xdata": {"MYAPP": XD}}],
            "modified": [{"handle": h, "layer": "0", "geom": LINE, "xdata": {"MYAPP": XD[:1]}}],
        },
    )
    got = entity_tags(dst, h)
    assert got["type"] == "LINE" and xdata_groups(got["tags"], "MYAPP") == [[1000, "id-7"]]
    old = {e.dxf.handle for e in ezdxf.readfile(src).modelspace()}
    new = next(e.dxf.handle for e in ezdxf.readfile(dst).modelspace() if e.dxf.handle not in old)
    groups = xdata_groups(entity_tags(dst, new)["tags"], "MYAPP")
    flat = [[1010, 1.0], [1020, 2.0], [1030, 3.0]]
    assert groups == XD[:5] + flat + [[1004, "0AFF"], [1005, "1A"], [1071, 70000]]
    # an empty list removes the application's XDATA
    dst2 = str(tmp_path / "e.dxf")
    apply_edits(
        dst, dst2, {"modified": [{"handle": h, "layer": "0", "geom": LINE, "xdata": {"MYAPP": []}}]}
    )
    assert [1001, "MYAPP"] not in entity_tags(dst2, h)["tags"]


def test_xdata_refusals(src, tmp_path):
    cases = {
        "EDIT_XDATA_APP": {"OTHER": [[1000, "x"]]},
        "EDIT_XDATA_PROTECTED": {"ACAD": [[1000, "x"]]},
        "EDIT_XDATA_TOO_LARGE": {"MYAPP": [[1000, "x" * 250]] * 70},
    }
    for code, xdata in cases.items():
        with pytest.raises(DxfError) as ex:
            run(src, tmp_path, {"created": [{"layer": "0", "geom": LINE, "xdata": xdata}]})
        assert ex.value.code == code


def test_dictionaries(src, tmp_path):
    h = handle_of(src)
    rec = [[1, "steel"], [10, [1, 2]], [40, 2.5], [70, 4], [290, 1], [310, "00FF"], [320, "1A"]]
    dst = run(
        src,
        tmp_path,
        {
            "dictionaries": [
                {"path": ["AXCAD", "PART", "MAT"], "op": "set", "xrecord": rec},
                {"path": ["AXCAD", "OLD"], "op": "set", "xrecord": [[1, "x"]]},
                {"path": ["AXCAD", "OLD"], "op": "delete"},
                {"path": ["NOTE"], "op": "set", "handle": h, "xrecord": [[300, "hi"]]},
            ]
        },
    )
    root = dictionary_entry(dst, [], None)
    assert any(e["name"] == "AXCAD" for e in root["entries"])
    axcad = dictionary_entry(dst, ["AXCAD"], None)
    assert [e["name"] for e in axcad["entries"]] == ["PART"]
    mat = dictionary_entry(dst, ["AXCAD", "PART", "MAT"], None)
    assert mat["type"] == "XRECORD"
    assert mat["tags"][:3] == [[1, "steel"], [10, [1.0, 2.0, 0.0]], [40, 2.5]]
    assert mat["tags"][5:] == [[310, "00FF"], [320, "1A"]]
    assert dictionary_entry(dst, ["NOTE"], h)["tags"] == [[300, "hi"]]
    tags = entity_tags(dst, h)["tags"]
    assert [102, "{ACAD_XDICTIONARY"] in tags  # entget shows the extension dictionary
    assert ezdxf.readfile(dst).audit().has_errors is False
    with pytest.raises(DxfError) as ex:
        dictionary_entry(dst, ["AXCAD", "NOPE"], None)
    assert ex.value.code == "DICT_NOT_FOUND"


def test_dictionary_refusals(src, tmp_path):
    lock = handle_of(src, "LOCK")
    x = [[1, "a"]]
    cases = [
        ({"path": ["ACAD_GROUP"], "op": "delete"}, "EDIT_DICT_PROTECTED"),
        ({"path": ["acad_layout", "X"], "op": "set", "xrecord": x}, "EDIT_DICT_PROTECTED"),
        ({"path": ["EZDXF_META"], "op": "delete"}, "EDIT_DICT_PROTECTED"),
        ({"path": ["NONE"], "op": "delete"}, "EDIT_DICT_NOT_FOUND"),
        ({"path": ["N"], "op": "set", "handle": lock, "xrecord": x}, "EDIT_LAYER_LOCKED"),
        ({"path": ["N"], "op": "set", "handle": "FFFF", "xrecord": x}, "EDIT_HANDLE_NOT_FOUND"),
    ]
    for op, code in cases:
        with pytest.raises(DxfError) as ex:
            run(src, tmp_path, {"dictionaries": [op]})
        assert ex.value.code == code, op
    with pytest.raises(DxfError) as ex:  # an XRecord is not a dictionary to go through
        run(
            src,
            tmp_path,
            {
                "dictionaries": [
                    {"path": ["A"], "op": "set", "xrecord": x},
                    {"path": ["A", "B"], "op": "set", "xrecord": x},
                ]
            },
        )
    assert ex.value.code == "EDIT_DICT_TYPE"


def test_request_validation():
    from backend.api.main import EditRequest

    created = {"layer": "0", "geom": LINE}
    bad_xdata = [
        {"MYAPP": [[1001, "MYAPP"]]},  # the application name is the key
        {"MYAPP": [[1, "x"]]},  # an XRecord code
        {"MYAPP": [[1000, "a\nb"]]},  # a newline would inject DXF groups
        {"MYAPP": [[1000, "x" * 256]]},
        {"MYAPP": [[1002, "{"]]},  # unbalanced
        {"MYAPP": [[1002, "}"], [1002, "{"]]},
        {"MYAPP": [[1070, 40000]]},
        {"MYAPP": [[1070, True]]},
        {"MYAPP": [[1040, "1"]]},
        {"MYAPP": [[1010, [1, 2, 3, 4]]]},
        {"MYAPP": [[1004, "ABC"]]},  # odd hex
        {"MYAPP": [[1005, "XYZ"]]},
        {"MY;APP": [[1000, "x"]]},
        {"MYAPP": [[1000, "x" * 250]] * 70},  # over 16 KB
    ]
    for xd in bad_xdata:
        with pytest.raises(pydantic.ValidationError):
            EditRequest.model_validate({"created": [{**created, "xdata": xd}]})
    bad_dicts = [
        {"path": [], "op": "delete"},
        {"path": ["A"] * 9, "op": "delete"},
        {"path": ["A"], "op": "set"},
        {"path": ["A"], "op": "delete", "xrecord": []},
        {"path": ["A\x00"], "op": "delete"},
        {"path": ["A"], "op": "set", "xrecord": [[330, "1A"]]},  # owner / pointer handles
        {"path": ["A"], "op": "set", "xrecord": [[5, "1A"]]},
        {"path": ["A"], "op": "set", "xrecord": [[0, "LINE"]]},
        {"path": ["A"], "op": "set", "xrecord": [[290, 2]]},
    ]
    for d in bad_dicts:
        with pytest.raises(pydantic.ValidationError):
            EditRequest.model_validate({"dictionaries": [d]})
    EditRequest.model_validate({"created": [{**created, "xdata": {"MYAPP": XD}}]})
    EditRequest.model_validate({"dictionaries": [{"path": ["A"], "op": "set", "xrecord": []}]})


def test_api_reads(client, world, src, tmp_path):
    h = world.h["designer"]
    with open(src, "rb") as f:
        r = client.post(f"/api/documents/{world.doc}/dxf", files={"file": ("a.dxf", f)}, headers=h)
    rid = r.json()["data"]["revision_id"]
    line = handle_of(src)
    body = {
        "modified": [{"handle": line, "layer": "0", "geom": LINE, "xdata": {"MYAPP": XD[:1]}}],
        "dictionaries": [{"path": ["AXCAD", "R"], "op": "set", "xrecord": [[1, "v"]]}],
    }
    r = client.post(f"/api/revisions/{rid}/edits", json=body, headers=h)
    assert r.status_code == 200, r.text
    new = r.json()["data"]["revision_id"]
    got = client.get(f"/api/revisions/{new}/entities/{line}", headers=h).json()["data"]
    assert xdata_groups(got["tags"], "MYAPP") == [[1000, "id-7"]]
    r = client.get(f"/api/revisions/{new}/dictionaries?path=AXCAD&path=R", headers=h)
    assert r.json()["data"]["tags"] == [[1, "v"]]
    assert client.get(f"/api/revisions/{new}/entities/FFFF", headers=h).status_code == 404
    assert client.get(f"/api/revisions/{new}/entities/zz", headers=h).status_code == 422
    r = client.get(f"/api/revisions/{new}/dictionaries?path=NOPE", headers=h)
    assert r.status_code == 404 and r.json()["error"]["code"] == "DICT_NOT_FOUND"
    assert client.get(f"/api/revisions/{new}/entities/{line}").status_code == 401
    other = world.h.get("outsider")
    if other is not None:  # members of other projects do not see it
        assert client.get(f"/api/revisions/{new}/entities/{line}", headers=other).status_code in (
            403,
            404,
        )
