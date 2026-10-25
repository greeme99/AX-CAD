"""Track 8 P1 security review (BUG-48): limits and tolerance added after the review."""

import json

import ezdxf
import pytest

from core.dxf import plot
from core.dxf.reader import DxfError, parse_dxf
from core.dxf.writer import apply_edits

LINE = {"type": "LINE", "start": [0, 0], "end": [10, 0]}


def run(tmp_path, edits, doc):
    src, dst = str(tmp_path / "s.dxf"), str(tmp_path / "d.dxf")
    doc.saveas(src)
    apply_edits(src, dst, edits)
    return dst


def test_broken_entities_only_lose_editing(tmp_path):
    """A zero-length aligned dimension and a NaN line in an unused block opened before P1:
    they must still open (drawn, not editable / not previewed)."""
    d = ezdxf.new(setup=True)
    d.units = ezdxf.units.MM
    dim = d.modelspace().add_aligned_dim(p1=(0, 0), p2=(30, 0), distance=5, dimstyle="Standard")
    dim.render()
    dim.dimension.dxf.dimtype = 1 | 32
    dim.dimension.dxf.defpoint3 = dim.dimension.dxf.defpoint2
    junk = d.blocks.new("JUNK")
    junk.add_line((0, 0), (1, 1)).dxf.end = (float("nan"), 0, 0)
    d.saveas(tmp_path / "b.dxf")
    p = parse_dxf(str(tmp_path / "b.dxf"))
    assert p["entities"] and not [e for e in p["entities"] if "geom" in e]
    assert next(b for b in p["blocks"] if b["name"] == "JUNK")["paths"] == []


def test_dimension_delete_keeps_blocks_it_does_not_own(tmp_path):
    d = ezdxf.new(setup=True)
    part = d.blocks.new("PART")
    part.add_line((0, 0), (1, 0))
    dim = d.modelspace().add_linear_dim(base=(0, 5), p1=(0, 0), p2=(10, 0), dimstyle="Standard")
    dim.render()
    dim.dimension.dxf.geometry = "PART"  # a file may point anywhere
    out = ezdxf.readfile(run(tmp_path, {"deleted": [dim.dimension.dxf.handle]}, d))
    assert "PART" in out.blocks


def test_block_cannot_contain_itself_in_any_case(tmp_path):
    d = ezdxf.new(setup=True)
    ins = {
        "type": "INSERT",
        "name": "FOO",
        "insert": [0, 0],
        "xscale": 1,
        "yscale": 1,
        "rotation": 0,
    }
    edits = {"blocks": [{"name": "foo", "base": [0, 0], "entities": [{"layer": "0", "geom": ins}]}]}
    with pytest.raises(DxfError) as exc:
        run(tmp_path, edits, d)
    assert exc.value.code == "EDIT_BLOCK_INVALID"


def test_plot_text_budget(monkeypatch):
    payload = {
        "layers": [{"name": "0", "visible": True}],
        "extents": {"min": [0, 0], "max": [100, 100]},
        "entities": [
            {
                "handle": "1",
                "type": "TEXT",
                "layer": "0",
                "color": None,
                "paths": [],
                "text": {"insert": [50, 50], "height": 5, "value": "X" * 50, "rotation": 0},
            },
            # off the sheet at a fixed scale: skipped, not counted
            {
                "handle": "2",
                "type": "TEXT",
                "layer": "0",
                "color": None,
                "paths": [],
                "text": {"insert": [5000, 5000], "height": 5, "value": "Y" * 500, "rotation": 0},
            },
        ],
    }
    monkeypatch.setattr(plot, "MAX_PLOT_TEXT_CHARS", 100)
    plot.plot_pdf(payload, "A4", True, 1, {}, set())
    monkeypatch.setattr(plot, "MAX_PLOT_TEXT_CHARS", 10)
    with pytest.raises(DxfError) as exc:
        plot.plot_pdf(payload, "A4", True, 1, {}, set())
    assert exc.value.code == "PLOT_TOO_LARGE"


def test_api_limits(client, world):
    spline = {"type": "SPLINE", "fit": [[i, i % 7] for i in range(1001)]}
    bad = [
        {"created": [{"layer": "0", "geom": spline}]},  # 1,000 fit points at most
        {"layers": [{"name": "A\n0\nENDSEC", "create": True}]},  # no control characters
        {"layers": [{"name": f"L{i}", "rename": f"R{i}"} for i in range(21)]},  # 20 renames
    ]
    for body in bad:
        r = client.post(f"/api/revisions/{'0' * 32}/edits", json=body, headers=world.h["designer"])
        assert r.status_code == 422 and r.json()["error"]["code"] == "EDIT_INVALID", str(body)[:60]


def test_stale_payload_is_served_when_the_reparse_fails(client, world, tmp_path, monkeypatch):
    from backend.api import main
    from tests.samples.drawings import write_all

    path = write_all(tmp_path)["hatch"]
    r = client.post(
        f"/api/documents/{world.doc}/dxf",
        files={"file": ("h.dxf", path.read_bytes())},
        headers=world.h["designer"],
    )
    rid = r.json()["data"]["revision_id"]
    stored = main.VAR_DIR / "revisions" / f"{rid}.json"
    old = json.loads(stored.read_text())
    old["format"] = 1
    stored.write_text(json.dumps(old))

    def broken(path, timeout_s=30):
        raise DxfError("DXF_PARSE_TIMEOUT", "Parsing timed out", 504)

    monkeypatch.setattr(main, "parse_dxf_with_timeout", broken)
    data = client.get(f"/api/revisions/{rid}/render", headers=world.h["viewer"]).json()["data"]
    assert data["format"] == 1 and data["entities"]  # still viewable
    pdf = client.post(f"/api/revisions/{rid}/pdf", json={}, headers=world.h["viewer"])
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
