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


def test_program_writes_one_data_file(client, world):
    """2D automation A1: AX-LISP open/write-line/close write one data file; LISP programs are safe."""
    h = world.h["designer"]
    client.put(
        "/api/lisp",
        json={"files": [{"name": "plate", "source": "(defun c:plate () 1)"}]},
        headers=h,
    )
    r = client.put("/api/lisp/files/sum.txt", json={"source": "300\n"}, headers=h)
    assert r.status_code == 200
    r = client.put("/api/lisp/files/SUM.TXT", json={"source": "301\n"}, headers=h)
    assert r.json()["data"]["name"] == "sum.txt"  # same file, its name kept
    got = client.get("/api/lisp/files/Sum.txt", headers=h).json()["data"]
    assert {k: got[k] for k in ("name", "source")} == {"name": "sum.txt", "source": "301\n"}
    assert got["modified"]  # W3: the server records when a program wrote it
    names = [f["name"] for f in client.get("/api/lisp", headers=h).json()["data"]["items"]]
    assert names == ["plate", "sum.txt"]  # the program file is still there
    for bad in ("plate", "x.lsp", "evil.js"):
        r = client.put(f"/api/lisp/files/{bad}", json={"source": "x"}, headers=h)
        assert r.status_code == 422 and r.json()["error"]["code"] == "LISP_FILE_KIND"
    assert client.get("/api/lisp/files/none.txt", headers=h).status_code == 404
    r = client.put("/api/lisp/files/a.txt", json={"source": "x"}, headers=world.h["viewer"])
    assert r.status_code == 403
    r = client.put("/api/lisp/files/..txt", json={"source": "x"}, headers=h)
    assert r.status_code == 422


def test_damaged_library_is_not_replaced_by_one_file(client, world):
    from backend.api import routes_lisp

    h = world.h["designer"]
    routes_lisp._path().parent.mkdir(parents=True, exist_ok=True)
    routes_lisp._path().write_text("{broken", encoding="utf-8")
    r = client.put("/api/lisp/files/a.txt", json={"source": "x"}, headers=h)
    assert r.status_code == 500 and r.json()["error"]["code"] == "LISP_LIBRARY_DAMAGED"
    assert routes_lisp._path().read_text(encoding="utf-8") == "{broken"


def test_w3_names_and_delete_data_files_only(client, world):
    """W3 (plan §22.4): vl-directory-files / vl-file-systime read names and times; a program
    deletes data files only, never a LISP program."""
    h = world.h["designer"]
    client.put("/api/lisp", json={"files": [{"name": "tool.lsp", "source": "(princ)"}]}, headers=h)
    assert client.put("/api/lisp/files/a.txt", json={"source": "x"}, headers=h).status_code == 200
    items = client.get("/api/lisp/names", headers=h).json()["data"]["items"]
    assert {i["name"] for i in items} == {"tool.lsp", "a.txt"}
    assert all(i["modified"] and "source" not in i for i in items)
    r = client.delete("/api/lisp/files/tool.lsp", headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "LISP_FILE_KIND"
    assert client.delete("/api/lisp/files/a.txt", headers=world.h["viewer"]).status_code == 403
    assert client.delete("/api/lisp/files/a.txt", headers=h).status_code == 200
    assert client.delete("/api/lisp/files/a.txt", headers=h).status_code == 404
    names = [i["name"] for i in client.get("/api/lisp/names", headers=h).json()["data"]["items"]]
    assert names == ["tool.lsp"]
    # the client cannot set the time itself
    r = client.put(
        "/api/lisp",
        json={"files": [{"name": "x.lsp", "source": "", "modified": "1999"}]},
        headers=h,
    )
    assert r.status_code == 422
