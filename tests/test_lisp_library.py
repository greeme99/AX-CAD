"""Track 11 L5: the PC's AX-LISP library (TC-187)."""


def test_library_round_trip_and_rules(client, world):
    h, admin = world.h["designer"], world.h["admin"]
    assert client.get("/api/lisp", headers=h).json()["data"]["items"] == []
    src = "(defun c:plate () (command \"REC\" '(0 0) '(100 50)))\n(DEFUN C:Hole2 (/ p) nil)\n(defun helper () 1)"
    r = client.put("/api/lisp", json={"files": [{"name": "판 그리기", "source": src}]}, headers=h)
    assert r.status_code == 200, r.text
    (item,) = r.json()["data"]["items"]
    assert item["name"] == "판 그리기" and item["commands"] == ["HOLE2", "PLATE"]
    assert client.get("/api/lisp", headers=world.h["viewer"]).json()["data"]["items"] == [item]
    # rules: designers write, names are plain, sizes and counts are capped, no duplicates
    assert client.put("/api/lisp", json={"files": []}, headers=world.h["viewer"]).status_code == 403
    for bad in (
        [{"name": "../x", "source": ""}],
        [{"name": "a", "source": "x" * (64 * 1024 + 1)}],
        [{"name": "a", "source": ""}, {"name": "A", "source": ""}],
        [{"name": f"f{i}", "source": ""} for i in range(201)],
        [{"name": "a", "source": "", "path": "/etc"}],
    ):
        assert client.put("/api/lisp", json={"files": bad}, headers=admin).status_code == 422
    assert client.put("/api/lisp", json={"files": []}, headers=admin).json()["data"]["items"] == []


def test_damaged_file_reads_empty(client, world):
    from backend.api import routes_lisp

    p = routes_lisp._path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json", encoding="utf-8")
    assert client.get("/api/lisp", headers=world.h["designer"]).json()["data"]["items"] == []
