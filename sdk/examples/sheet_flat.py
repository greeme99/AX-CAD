"""A 100 x 50 bracket with one flange, its flat pattern numbers and DXF (for the laser)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from axcad import Client


def main(cad: Client, project_id: int, out: Path) -> dict:
    doc = cad.create_document(project_id, "BRK-SDK", "PART", "SDK 브래킷")
    rect = [(0, 0), (100, 0), (100, 50), (0, 50)]
    lines = [
        {"type": "LINE", "start": list(a), "end": list(b)}
        for a, b in zip(rect, rect[1:] + rect[:1])
    ]
    sk = cad.sketch(
        doc["document_id"], [*lines, {"type": "CIRCLE", "center": [50, 25], "radius": 4}]
    )
    base = cad.sheet_base(doc["document_id"], sk["feature_id"], thickness=2)
    fl = cad.flange(doc["document_id"], base["feature_id"], edge=0, length=30)
    out.write_bytes(cad.flat_dxf(fl["feature_id"]))
    return cad.flat_pattern(fl["feature_id"])["flat"]["metrics"]


if __name__ == "__main__":
    cad = Client.local()
    print(main(cad, int(sys.argv[1]), Path("bracket-flat.dxf")))
