"""Track 7 per-PC patches: Ed25519 (RFC 8032 vectors, cross-checked with `cryptography`), signed
component swap, tamper/wrong-key/downgrade/zip-slip refusal, rollback and commit."""

import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "installer"))
import patcher

# RFC 8032 §7.1 TEST 1 and TEST 2
VECTORS = [
    (
        "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        "",
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
    ),
    (
        "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        "72",
        "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00",
    ),
]
KEY = bytes(range(32))
PUB = patcher.public_key(KEY)


@pytest.mark.parametrize(("sk", "pk", "msg", "sig"), VECTORS)
def test_rfc8032_vectors(sk, pk, msg, sig):
    sk, pk, msg, sig = (bytes.fromhex(x) for x in (sk, pk, msg, sig))
    assert patcher.public_key(sk) == pk
    assert patcher.sign(sk, msg) == sig
    assert patcher.verify(pk, msg, sig)
    assert not patcher.verify(pk, msg + b"x", sig)
    assert not patcher.verify(pk, msg, sig[:-1] + bytes([sig[-1] ^ 1]))


def test_matches_the_cryptography_package():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    ref = Ed25519PrivateKey.from_private_bytes(KEY)
    msg = b"AX-CAD manifest"
    assert ref.sign(msg) == patcher.sign(KEY, msg)
    ref.public_key().verify(patcher.sign(KEY, msg), msg)  # raises if invalid


def build(root: Path, version: str, web: str = "v1", server: str = "s1") -> Path:
    app = root / "app"
    for name, files in {
        "python": {"python.exe": "py", "Lib/x.py": "lib"},
        "server": {"backend/main.py": server},
        "web": {"server.js": web, ".next/static/a.js": web},
        "scripts": {"start.ps1": "start", "patch_pub.hex": PUB.hex()},
    }.items():
        for rel, text in files.items():
            p = app / name / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
    (app / "VERSION").write_text(version)
    patcher.describe(app)
    return app


def test_make_apply_commit_and_rollback(tmp_path):
    installed = build(tmp_path / "pc", "1.0.0")
    new = build(tmp_path / "rel", "1.1.0", web="v2", server="s2")
    (new / "web" / "server.js").unlink()  # a file removed in the new version
    p = tmp_path / "p.zip"
    m = patcher.make(new, KEY, p, ["server", "web", "scripts"])
    assert set(m["requires"]) == {"python"}  # unchanged runtime is not carried

    patcher.apply(p, installed, PUB)
    assert (installed / "VERSION").read_text() == "1.1.0"
    assert (installed / "server/backend/main.py").read_text() == "s2"
    assert not (installed / "web/server.js").exists()  # whole component replaced
    assert (installed / ".web.old/server.js").read_text() == "v1"
    with pytest.raises(patcher.PatchError, match="not committed"):
        patcher.apply(p, installed, PUB)

    assert patcher.rollback(installed) == "1.0.0"
    assert (installed / "server/backend/main.py").read_text() == "s1"
    assert (installed / "web/server.js").read_text() == "v1"
    assert json.loads((installed / "components.json").read_text())["version"] == "1.0.0"

    patcher.apply(p, installed, PUB)
    patcher.commit(installed)
    assert not (installed / ".web.old").exists() and not (installed / "rollback.json").exists()
    with pytest.raises(patcher.PatchError, match="not newer"):  # no silent downgrade/replay
        patcher.apply(p, installed, PUB)


def test_refuses_wrong_key_tampering_and_mismatched_runtime(tmp_path):
    installed = build(tmp_path / "pc", "1.0.0")
    new = build(tmp_path / "rel", "1.1.0", server="s2")
    p = tmp_path / "p.zip"
    patcher.make(new, KEY, p, ["server"])
    other = bytes(32 * [7])
    with pytest.raises(patcher.PatchError, match="signature"):
        patcher.apply(p, installed, patcher.public_key(other))

    bad = tmp_path / "bad.zip"  # same manifest and signature, different file content
    with zipfile.ZipFile(p) as src, zipfile.ZipFile(bad, "w") as dst:
        for info in src.infolist():
            data = src.read(info.filename)
            dst.writestr(info, b"evil" if info.filename.startswith("files/") else data)
    with pytest.raises(patcher.PatchError, match="damaged"):
        patcher.apply(bad, installed, PUB)
    assert (installed / "server/backend/main.py").read_text() == "s1"  # untouched
    assert not (installed / ".server.new").exists()

    extra = tmp_path / "extra.zip"  # a signed manifest cannot smuggle extra entries
    with zipfile.ZipFile(p) as src, zipfile.ZipFile(extra, "w") as dst:
        for info in src.infolist():
            dst.writestr(info, src.read(info.filename))
        dst.writestr("files/server/../../../evil.txt", b"x")
    with pytest.raises(patcher.PatchError, match="unexpected entry"):
        patcher.apply(extra, installed, PUB)

    (installed / "python" / "python.exe").write_text("changed runtime")
    patcher.describe(installed)
    with pytest.raises(patcher.PatchError, match="full patch"):
        patcher.apply(p, installed, PUB)


def test_unsafe_paths_rejected_even_when_signed(tmp_path):
    installed = build(tmp_path / "pc", "1.0.0")
    for rel in ("../x", "/etc/x", "C:/x", "a\\b", "a/./b", "a//b", ""):
        with pytest.raises(patcher.PatchError, match="unsafe"):
            patcher._safe_rel(rel)
    manifest = {
        "product": "AX-CAD",
        "version": "9.9.9",
        "components": {"server": {"id": "x", "files": {"../../evil": "00"}}},
        "requires": {},
    }
    raw = json.dumps(manifest).encode()
    p = tmp_path / "slip.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("manifest.json", raw)
        z.writestr("manifest.sig", patcher.sign(KEY, raw).hex())
        z.writestr("files/server/../../evil", b"x")
    with pytest.raises(patcher.PatchError, match="unsafe"):
        patcher.apply(p, installed, PUB)
    assert not (tmp_path / "pc" / "evil").exists()


def test_cli_keygen_never_overwrites(tmp_path, capsys):
    key = tmp_path / "k.hex"
    assert patcher.main(["keygen", str(key)]) == 0
    pub = capsys.readouterr().out.strip()
    assert patcher.public_key(bytes.fromhex(key.read_text())).hex() == pub
    assert patcher.main(["keygen", str(key)]) == 1  # refuses to replace a signing key


def test_make_refuses_a_key_the_installs_do_not_trust(tmp_path):
    new = build(tmp_path / "rel", "1.1.0")
    with pytest.raises(patcher.PatchError, match="does not match"):
        patcher.make(new, bytes(32 * [7]), tmp_path / "p.zip")


def test_case_only_collisions_rejected(tmp_path):
    installed = build(tmp_path / "pc", "1.0.0")
    files = {"a.txt": hashlib.sha256(b"x").hexdigest(), "A.txt": hashlib.sha256(b"x").hexdigest()}
    manifest = {
        "product": "AX-CAD",
        "version": "1.1.0",
        "components": {"server": {"id": "x", "files": files}},
        "requires": {},
    }
    raw = json.dumps(manifest).encode()
    p = tmp_path / "case.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("manifest.json", raw)
        z.writestr("manifest.sig", patcher.sign(KEY, raw).hex())
        for rel in files:
            z.writestr(f"files/server/{rel}", b"x")
    with pytest.raises(patcher.PatchError, match="case"):
        patcher.apply(p, installed, PUB)


def test_rollback_after_an_interrupted_swap(tmp_path, monkeypatch):
    installed = build(tmp_path / "pc", "1.0.0")
    new = build(tmp_path / "rel", "1.1.0", web="v2", server="s2")
    p = tmp_path / "p.zip"
    patcher.make(new, KEY, p, ["server", "web"])
    real_rename = Path.rename

    def rename(self, target):  # power cut between moving web aside and moving the new one in
        if self.name == ".web.new":
            raise OSError("interrupted")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", rename)
    with pytest.raises(OSError):
        patcher.apply(p, installed, PUB)
    monkeypatch.undo()
    assert (installed / "web/server.js").read_text() == "v1"  # apply() rolled itself back
    assert (installed / "server/backend/main.py").read_text() == "s1"
    assert not (installed / "rollback.json").exists() and not (installed / ".web.old").exists()
    patcher.apply(p, installed, PUB)  # and the next attempt works
    assert (installed / "web/server.js").read_text() == "v2"


def test_bytecode_caches_do_not_change_a_component(tmp_path):
    app = build(tmp_path / "pc", "1.0.0")
    before = json.loads((app / "components.json").read_text())["components"]["python"]["id"]
    cache = app / "python" / "Lib" / "__pycache__" / "x.cpython-312.pyc"
    cache.parent.mkdir()
    cache.write_bytes(b"compiled at install time")
    assert patcher.describe(app)["components"]["python"]["id"] == before
