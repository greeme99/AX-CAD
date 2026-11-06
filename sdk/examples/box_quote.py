"""Model a block, quote it in SS400 (two pieces) and save the quote as Excel."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from axcad import Client


def main(cad: Client, project_id: int, out: Path) -> dict:
    doc = cad.create_document(project_id, "BLK-SDK", "PART", "SDK 블록")
    cad.add_feature(
        doc["document_id"], "PRIMITIVE", {"kind": "BOX", "length": 80, "width": 40, "height": 20}
    )
    q = cad.quote_model(doc["document_id"], qty=2, material_code="SS400")
    out.write_bytes(cad.quote_report(q["quote_id"], "xlsx"))
    return q


if __name__ == "__main__":
    cad = Client.local()
    q = main(cad, int(sys.argv[1]), Path("block-quote.xlsx"))
    print(q["quote_id"], q["total_amount"])
