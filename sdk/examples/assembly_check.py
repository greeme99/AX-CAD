"""Two plates in an assembly: stack them with a mate, then check interference."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from axcad import Client


def main(cad: Client, project_id: int) -> dict:
    part = cad.create_document(project_id, "PLT-SDK", "PART", "SDK 판")
    plate = cad.add_feature(
        part["document_id"], "PRIMITIVE", {"kind": "BOX", "length": 100, "width": 100, "height": 10}
    )
    asm = cad.create_document(project_id, "ASM-SDK", "ASSEMBLY", "SDK 조립")
    a = cad.add_component(asm["document_id"], plate["feature_id"])
    b = cad.add_component(asm["document_id"], plate["feature_id"], t=(5, 5, 30))
    faces = cad.faces(plate["feature_id"])
    top = next(
        f for f in faces if f["kind"] == "PLANE" and f["dir"] == [0, 0, 1] and f["mid"][2] == 10
    )
    bottom = next(
        f for f in faces if f["kind"] == "PLANE" and f["dir"] == [0, 0, 1] and f["mid"][2] == 0
    )
    cad.add_mate(
        asm["document_id"], "COINCIDENT", (a["component_id"], top), (b["component_id"], bottom)
    )
    return cad.interference(asm["document_id"], clearance=0.5)


if __name__ == "__main__":
    print(main(Client.local(), int(sys.argv[1])))
