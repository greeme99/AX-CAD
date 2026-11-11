"""W4 security review H1/M1: dictionaries sent with the drawing are budgeted by characters, an
XRecord pointed at by many keys is sent once, and odd tag values still make valid JSON."""

import json

import ezdxf

from core.dxf.reader import parse_dxf


def test_aliases_are_sent_once_and_strings_capped(tmp_path):
    d = ezdxf.new("R2018")
    d.units = ezdxf.units.MM
    big = d.rootdict.add_new_dict("BIG")
    xr = big.add_xrecord("ONE")
    xr.reset([(1, "x" * 5000), (10, (1.0, 2.0, 3.0)), (40, 1.5)])
    for i in range(200):
        big[f"ALIAS{i}"] = xr  # the same object under many keys
    d.modelspace().add_line((0, 0), (1, 0))
    p = str(tmp_path / "a.dxf")
    d.saveas(p)
    out = parse_dxf(p)
    entries = out["dicts"]["BIG"]["entries"]
    full = [n for n in entries.values() if "tags" in n]
    assert len(full) == 1  # the others are {"type", "handle"} only
    tags = full[0]["tags"]
    assert len(tags[0][1]) == 2049 and tags[1][1] == [1.0, 2.0, 3.0] and tags[2][1] == 1.5
    assert len(json.dumps(out["dicts"], allow_nan=False)) < 20_000
