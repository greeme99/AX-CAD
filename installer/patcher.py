"""AX-CAD per-PC install (Track 7): signed patch files — build, verify, apply, roll back.

An install keeps the program in app/ and the user's data in data/ (never touched by a patch).
app/ is made of components, each one directory replaced as a whole:
    python  pgsql  node   runtimes (large, change rarely)
    server  web  scripts  AX-CAD itself (change with every release)
app/components.json lists every component's files with their SHA-256 and an id (hash of that list).

A patch is a zip:
    manifest.json   {"product", "version", "components": {name: {"id", "files": {path: sha256}}},
                     "requires": {name: id},   components not carried must already be installed as is
                     "release": {"grade", "notes", "released_at", "from_version"},
                     "migrations": [revision file names]}
    manifest.sig    Ed25519 signature of manifest.json (hex)
    files/<component>/<path>
The trust anchor is the public key of the INSTALLED copy (app/scripts/patch_pub.hex): a patch can
only come from whoever holds the matching private key, never from the patch itself.

Release grades (Track 8 P5, plan.md §14.4): app/RELEASE.json, written at build time, says what a
version is; the signed manifest carries it and apply() writes it to the install.
    UPDATE   R1.0 -> R1.3 (same major): any older version of that major, skipping is fine
             (server/web/scripts are replaced whole). Must not change a runtime.
    UPGRADE  R1.x -> R2.0 (next major): only from "from_version", the last update of the previous
             major; may carry new runtimes (PostgreSQL major versions are still refused by update.ps1).
Either way the install's database migrations must all still be in the patch (no squash), so the
database can be migrated forward. Versions: R1.1 on screen = 1.1.0 here, a fix 1.1.1 = R1.1 (수정 1).

Standard library only: update.ps1 runs this with a copy of the bundled Python while app/ is being
swapped, so it cannot depend on the site-packages it may replace. Ed25519 is the RFC 8032 §6
reference algorithm (checked against RFC test vectors and the `cryptography` package in tests);
signing is not constant-time, so sign only on the release machine.

    python patcher.py keygen KEYFILE                    -> private key file, prints the public key
    python patcher.py describe APP                      -> writes APP/components.json
    python patcher.py make APP --key KEYFILE --out P [--only server,web,scripts]
    python patcher.py apply P --app APP --pub PUBFILE   -> swaps components, keeps the old ones
    python patcher.py commit APP | rollback APP         -> drop / restore the old components
"""

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

COMPONENTS = ("python", "pgsql", "node", "server", "web", "scripts")
RUNTIMES = ("python", "pgsql", "node")
GRADES = ("UPDATE", "UPGRADE")
MIGRATIONS = "server/backend/db/migrations/versions"
MAX_NOTES, MAX_NOTE = 200, 300
PRODUCT = "AX-CAD"
MAX_FILES = 200_000
MAX_TOTAL = 6 * 1024**3  # uncompressed bytes a patch may carry (all runtimes ~2 GB)
VERSION_RE = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,6}$", re.ASCII)
MAX_MANIFEST = 64 * 1024**2
CHUNK = 1024 * 1024

# --- Ed25519 (RFC 8032 §6 reference) -------------------------------------------------------

_P = 2**255 - 19
_Q = 2**252 + 27742317777372353535851937790883648493


def _inv(x: int) -> int:
    return pow(x, _P - 2, _P)


_D = -121665 * _inv(121666) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)
Point = tuple[int, int, int, int]


def _add(a: Point, b: Point) -> Point:
    pa, pb = (a[1] - a[0]) * (b[1] - b[0]) % _P, (a[1] + a[0]) * (b[1] + b[0]) % _P
    pc, pd = 2 * a[3] * b[3] * _D % _P, 2 * a[2] * b[2] % _P
    e, f, g, h = pb - pa, pd - pc, pd + pc, pb + pa
    return (e * f, g * h, f * g, e * h)


def _mul(s: int, pt: Point) -> Point:
    out: Point = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            out = _add(out, pt)
        pt = _add(pt, pt)
        s >>= 1
    return out


def _equal(a: Point, b: Point) -> bool:
    return (a[0] * b[2] - b[0] * a[2]) % _P == 0 and (a[1] * b[2] - b[1] * a[2]) % _P == 0


def _recover_x(y: int, sign: int) -> int | None:
    if y >= _P:
        return None
    x2 = (y * y - 1) * _inv(_D * y * y + 1)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P != 0:
        return None
    return _P - x if (x & 1) != sign else x


_GY = 4 * _inv(5) % _P
_GX = _recover_x(_GY, 0)
assert _GX is not None
_G: Point = (_GX, _GY, 1, _GX * _GY % _P)


def _compress(pt: Point) -> bytes:
    zinv = _inv(pt[2])
    x, y = pt[0] * zinv % _P, pt[1] * zinv % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(s: bytes) -> Point | None:
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign, y = y >> 255, y & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


def _h(data: bytes) -> int:
    return int.from_bytes(hashlib.sha512(data).digest(), "little") % _Q


def _expand(secret: bytes) -> tuple[int, bytes]:
    if len(secret) != 32:
        raise ValueError("private key must be 32 bytes")
    h = hashlib.sha512(secret).digest()
    a = int.from_bytes(h[:32], "little") & ((1 << 254) - 8) | (1 << 254)
    return a, h[32:]


def public_key(secret: bytes) -> bytes:
    return _compress(_mul(_expand(secret)[0], _G))


def sign(secret: bytes, msg: bytes) -> bytes:
    a, prefix = _expand(secret)
    pub = _compress(_mul(a, _G))
    r = _h(prefix + msg)
    rs = _compress(_mul(r, _G))
    s = (r + _h(rs + pub + msg) * a) % _Q
    return rs + int.to_bytes(s, 32, "little")


def verify(public: bytes, msg: bytes, signature: bytes) -> bool:
    if len(public) != 32 or len(signature) != 64:
        return False
    a, r = _decompress(public), _decompress(signature[:32])
    s = int.from_bytes(signature[32:], "little")
    if a is None or r is None or s >= _Q:
        return False
    h = _h(signature[:32] + public + msg)
    return _equal(_mul(s, _G), _add(r, _mul(h, a)))


# --- components -----------------------------------------------------------------------------


class PatchError(Exception):
    """Refused or failed patch; the install is left as it was."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _files(root: Path) -> dict[str, str]:
    """Every file but bytecode caches: Python writes those wherever it runs (build, install,
    update), so hashing them would make two identical runtimes look different."""
    return {
        p.relative_to(root).as_posix(): _sha256(p)
        for p in sorted(root.rglob("*"))
        if p.is_file()
        and not p.is_symlink()
        and p.suffix != ".pyc"
        and "__pycache__" not in p.relative_to(root).parts
    }


def _write(path: Path, text: str) -> None:
    """State files are replaced whole (temp + rename), so a crash never leaves half of one."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _component_id(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def describe(app: Path) -> dict[str, Any]:
    """Hash every component directory into app/components.json (run at build time)."""
    comps = {}
    for name in COMPONENTS:
        if (app / name).is_dir():
            files = _files(app / name)
            comps[name] = {"id": _component_id(files), "files": files}
    out = {"version": _version(app), "components": comps, "migrations": _migrations(app)}
    _write(app / "components.json", json.dumps(out, sort_keys=True))
    return out


def _migrations(app: Path) -> list[str]:
    return sorted(p.stem for p in (app / MIGRATIONS).glob("*.py") if p.stem != "__init__")


def release(app: Path, version: str) -> dict[str, Any]:
    """app/RELEASE.json checked: grade, change notes, release date, and for an upgrade the version
    it starts from. A build without one is an update without notes."""
    try:
        r = json.loads((app / "RELEASE.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        r = {}
    except (OSError, ValueError) as e:
        raise PatchError(f"RELEASE.json unreadable: {e}") from None
    grade = r.get("grade", "UPDATE")
    notes = r.get("notes", [])
    if grade not in GRADES:
        raise PatchError(f"grade must be one of {', '.join(GRADES)}")
    if (
        not isinstance(notes, list)
        or len(notes) > MAX_NOTES
        or not all(isinstance(n, str) and 0 < len(n) <= MAX_NOTE for n in notes)
    ):
        raise PatchError(f"notes: up to {MAX_NOTES} lines of 1-{MAX_NOTE} characters")
    out = {"grade": grade, "notes": notes, "released_at": str(r.get("released_at", ""))[:10]}
    if grade == "UPGRADE":
        frm = str(r.get("from_version", ""))
        if not VERSION_RE.fullmatch(frm) or _vtuple(frm)[0] + 1 != _vtuple(version)[0]:
            raise PatchError(
                "an upgrade names from_version, the last version of the previous major"
            )
        out["from_version"] = frm
    elif _vtuple(version)[1:] == (0, 0) and _vtuple(version)[0] > 1:
        raise PatchError(f"{version} starts a new major: make it an UPGRADE")
    return out


def check_grade(m: dict[str, Any], installed: dict[str, Any], current: str) -> None:
    """The release rules between an install and a patch (manifests without "release" are older
    Track 7 patches: updates)."""
    rel = m.get("release") or {"grade": "UPDATE"}
    major, have = _vtuple(m["version"])[0], _vtuple(current)[0]
    if rel["grade"] == "UPDATE":
        if major != have:
            raise PatchError(
                f"R{major} 업데이트는 R{have}에 적용할 수 없습니다: 업그레이드 패치가 필요합니다"
            )
        for name in RUNTIMES:
            comp = m["components"].get(name)
            if comp and comp["id"] != installed["components"].get(name, {}).get("id"):
                raise PatchError(f"런타임 '{name}'을 바꾸는 패치는 업그레이드여야 합니다")
    else:
        if major != have + 1 or current != rel.get("from_version"):
            raise PatchError(
                f"{m['version']} 업그레이드는 {rel.get('from_version')}에서만 됩니다 "
                f"(설치 {current}): R{have}의 마지막 업데이트를 먼저 적용하세요"
            )
    lost = set(installed.get("migrations", [])) - set(m.get("migrations", []))
    if "migrations" in m and lost:
        raise PatchError(
            f"설치된 DB 마이그레이션이 패치에 없습니다 ({', '.join(sorted(lost)[:3])})"
        )


def _version(app: Path) -> str:
    v = (app / "VERSION").read_text(encoding="utf-8").strip()
    if not VERSION_RE.fullmatch(v):
        raise PatchError(f"bad version '{v[:20]}'")
    return v


def _vtuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def _installed(app: Path) -> dict[str, Any]:
    try:
        data: dict[str, Any] = json.loads((app / "components.json").read_text(encoding="utf-8"))
        return data
    except (OSError, ValueError) as e:
        raise PatchError(f"install has no readable components.json: {e}") from None


# --- make -----------------------------------------------------------------------------------


def make(
    app: Path,
    key: bytes,
    out: Path,
    only: list[str] | None = None,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Patch carrying `only` components (default: all), requiring the rest as built here."""
    try:
        pub = _read_hex(app / "scripts" / "patch_pub.hex", 32)
    except (OSError, ValueError):
        raise PatchError("app/scripts/patch_pub.hex missing or unreadable") from None
    if public_key(key) != pub:  # installs trust that key: a mismatch would end their updates
        raise PatchError("signing key does not match app/scripts/patch_pub.hex")
    desc = describe(app)
    rel = release(app, desc["version"])
    if previous is not None:  # the release this one follows: catch a wrong grade at build time
        check_grade(
            {
                "version": desc["version"],
                "release": rel,
                "components": desc["components"],
                "migrations": desc["migrations"],
            },
            previous,
            previous["version"],
        )
    carried = only or list(desc["components"])
    unknown = set(carried) - set(desc["components"])
    if unknown:
        raise PatchError(f"no such component: {', '.join(sorted(unknown))}")
    manifest = {
        "product": PRODUCT,
        "version": desc["version"],
        "components": {n: desc["components"][n] for n in carried},
        "requires": {n: c["id"] for n, c in desc["components"].items() if n not in carried},
        "release": rel,
        "migrations": desc["migrations"],
    }
    raw = json.dumps(manifest, sort_keys=True).encode()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        z.writestr("manifest.json", raw)
        z.writestr("manifest.sig", sign(key, raw).hex())
        for name in carried:
            for rel in desc["components"][name]["files"]:
                z.write(app / name / rel, f"files/{name}/{rel}")
    return manifest


# --- apply / commit / rollback --------------------------------------------------------------


def _safe_rel(rel: str) -> PurePosixPath:
    p = PurePosixPath(rel)
    if (
        not rel
        or "\\" in rel
        or ":" in rel
        or p.is_absolute()
        or any(part in ("", ".", "..") for part in p.parts)
        or rel != p.as_posix()
    ):
        raise PatchError(f"unsafe path in patch: {rel[:80]!r}")
    return p


def _read_manifest(z: zipfile.ZipFile, pub: bytes) -> dict[str, Any]:
    try:
        if (
            z.getinfo("manifest.json").file_size > MAX_MANIFEST
            or z.getinfo("manifest.sig").file_size > 1024
        ):
            raise PatchError("manifest too large")
        raw = z.read("manifest.json")
        sig = bytes.fromhex(z.read("manifest.sig").decode("ascii").strip())
    except (KeyError, ValueError, UnicodeDecodeError):
        raise PatchError("not an AX-CAD patch (manifest missing)") from None
    if not verify(pub, raw, sig):
        raise PatchError("서명이 이 설치본의 키와 맞지 않습니다: 패치를 거부합니다")
    m: dict[str, Any] = json.loads(raw)
    if m.get("product") != PRODUCT or not VERSION_RE.fullmatch(str(m.get("version", ""))):
        raise PatchError("manifest is not an AX-CAD patch")
    if not set(m["components"]) <= set(COMPONENTS) or not set(m["requires"]) <= set(COMPONENTS):
        raise PatchError("manifest names an unknown component")
    return m


def _admissible(
    z: zipfile.ZipFile, app: Path, pub: bytes, allow_downgrade: bool = False
) -> dict[str, Any]:
    """Signature, version order, release grade and the components the patch builds on."""
    installed, current = _installed(app), _version(app)
    m = _read_manifest(z, pub)
    if not allow_downgrade and _vtuple(m["version"]) <= _vtuple(current):
        raise PatchError(f"패치 {m['version']}이 설치된 {current}보다 새 버전이 아닙니다")
    if not allow_downgrade:
        check_grade(m, installed, current)
    have = {n: c["id"] for n, c in installed["components"].items()}
    for name, cid in m["requires"].items():
        if have.get(name) != cid:
            raise PatchError(
                f"component '{name}' differs from what this patch was built on: "
                "apply the full patch (all components) instead"
            )
    return m


def check(patch: Path, app: Path, pub: bytes) -> dict[str, Any]:
    """Would apply() take this patch? (no file is written; contents are verified by apply)"""
    with zipfile.ZipFile(patch) as z:
        return _admissible(z, app, pub)


def apply(patch: Path, app: Path, pub: bytes, allow_downgrade: bool = False) -> dict[str, Any]:
    """Verify everything, stage the carried components next to app/, then swap them in. The old
    directories stay as app/.<name>.old until commit() or rollback()."""
    if (app / "rollback.json").exists():
        raise PatchError("a previous patch is not committed or rolled back yet")
    for name in COMPONENTS:  # leftovers of an interrupted commit()
        shutil.rmtree(app / f".{name}.old", ignore_errors=True)
    installed = _installed(app)
    current = _version(app)
    with zipfile.ZipFile(patch) as z:
        m = _admissible(z, app, pub, allow_downgrade)
        wanted: dict[str, tuple[str, str]] = {}  # zip name -> (component, sha256)
        total = 0
        for name, comp in m["components"].items():
            for rel, digest in comp["files"].items():
                _safe_rel(rel)
                wanted[f"files/{name}/{rel}"] = (name, digest)
        if len({w.casefold() for w in wanted}) != len(wanted):  # one file on NTFS
            raise PatchError("patch has paths differing only in case")
        infos = z.infolist()
        if len(infos) > MAX_FILES:
            raise PatchError("patch has too many entries")
        for info in infos:
            if info.filename in ("manifest.json", "manifest.sig"):
                continue
            if info.filename not in wanted or info.is_dir():
                raise PatchError(f"unexpected entry in patch: {info.filename[:80]!r}")
            total += info.file_size
        if total > MAX_TOTAL or set(wanted) - {i.filename for i in infos}:
            raise PatchError("patch is incomplete or too large")
        staged: list[str] = []
        try:
            for name in m["components"]:
                stage = app / f".{name}.new"
                shutil.rmtree(stage, ignore_errors=True)
                stage.mkdir()
                staged.append(name)
            for zname, (name, digest) in wanted.items():
                dst = app / f".{name}.new" / zname.removeprefix(f"files/{name}/")
                dst.parent.mkdir(parents=True, exist_ok=True)
                h = hashlib.sha256()
                with z.open(zname) as src, dst.open("wb") as f:
                    while chunk := src.read(CHUNK):
                        h.update(chunk)
                        f.write(chunk)
                if h.hexdigest() != digest:
                    raise PatchError(f"file damaged in patch: {zname[:80]!r}")
        except BaseException:
            for name in staged:
                shutil.rmtree(app / f".{name}.new", ignore_errors=True)
            raise
    # everything verified: swap directories (renames only), recording each step for rollback
    swapped: list[str] = []
    state = {"version": current, "components_json": installed, "swapped": swapped}
    try:
        for name in m["components"]:
            swapped.append(name)  # recorded before the renames: rollback() handles either step
            _write(app / "rollback.json", json.dumps(state))
            if (app / name).exists():
                (app / name).rename(app / f".{name}.old")
            (app / f".{name}.new").rename(app / name)
        merged = {**installed["components"], **m["components"]}
        out = {"version": m["version"], "components": merged}
        migrations = m.get("migrations", installed.get("migrations"))  # an older-format patch
        if migrations is not None:  # keeps what the install had, so later checks still hold
            out["migrations"] = migrations
        _write(app / "components.json", json.dumps(out, sort_keys=True))
        if "release" in m:  # what the app shows: R-label, grade, change notes
            state["release_json"] = _read_text(app / "RELEASE.json")
            _write(app / "rollback.json", json.dumps(state))
            _write(app / "RELEASE.json", json.dumps({"version": m["version"], **m["release"]}))
        _write(app / "VERSION", m["version"])
    except BaseException:
        rollback(app)
        raise
    return m


def rollback(app: Path) -> str:
    """Put back the components replaced by the last apply(); returns the restored version."""
    try:
        state = json.loads((app / "rollback.json").read_text(encoding="utf-8"))
    except OSError:
        raise PatchError("nothing to roll back") from None
    had = state["components_json"]["components"]
    for name in reversed(state["swapped"]):
        old = app / f".{name}.old"
        if old.exists():  # swapped (or half-swapped): the old one goes back
            shutil.rmtree(app / name, ignore_errors=True)
            old.rename(app / name)
        elif name not in had:  # a component this patch added
            shutil.rmtree(app / name, ignore_errors=True)
        # else: interrupted before the first rename, app/<name> is still the old one
    for name in COMPONENTS:
        shutil.rmtree(app / f".{name}.new", ignore_errors=True)
    _write(app / "components.json", json.dumps(state["components_json"], sort_keys=True))
    if "release_json" in state:
        if state["release_json"] is None:
            (app / "RELEASE.json").unlink(missing_ok=True)
        else:
            _write(app / "RELEASE.json", state["release_json"])
    _write(app / "VERSION", state["version"])
    (app / "rollback.json").unlink()
    return str(state["version"])


def commit(app: Path) -> None:
    """The new version runs: drop the old components."""
    for name in COMPONENTS:
        shutil.rmtree(app / f".{name}.old", ignore_errors=True)
    (app / "rollback.json").unlink(missing_ok=True)


# --- CLI ------------------------------------------------------------------------------------


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def _read_hex(path: Path, size: int) -> bytes:
    raw = bytes.fromhex(path.read_text(encoding="ascii").strip())
    if len(raw) != size:
        raise PatchError(f"{path.name}: expected {size} bytes")
    return raw


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="patcher")
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen")
    k.add_argument("keyfile", type=Path)
    d = sub.add_parser("describe")
    d.add_argument("app", type=Path)
    mk = sub.add_parser("make")
    mk.add_argument("app", type=Path)
    mk.add_argument("--key", type=Path, required=True)
    mk.add_argument("--out", type=Path, required=True)
    mk.add_argument("--only", default="")
    mk.add_argument("--previous", type=Path, help="components.json of the release this follows")
    a = sub.add_parser("apply")
    a.add_argument("patch", type=Path)
    a.add_argument("--app", type=Path, required=True)
    a.add_argument("--pub", type=Path, required=True)
    a.add_argument("--allow-downgrade", action="store_true")
    for name in ("commit", "rollback"):
        sub.add_parser(name).add_argument("app", type=Path)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "keygen":
            if args.keyfile.exists():
                raise PatchError(f"{args.keyfile} exists: never overwrite a signing key")
            seed = secrets.token_bytes(32)
            fd = os.open(args.keyfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="ascii") as f:
                f.write(seed.hex())
            print(public_key(seed).hex())  # commit this as installer/patch_pub.hex
        elif args.cmd == "describe":
            describe(args.app)
        elif args.cmd == "make":
            only = [x for x in args.only.split(",") if x] or None
            prev = json.loads(args.previous.read_text(encoding="utf-8")) if args.previous else None
            m = make(args.app, _read_hex(args.key, 32), args.out, only, prev)
            print(
                f"{args.out.name}: {m['version']} {m['release']['grade']} "
                f"carries {', '.join(m['components'])}"
            )
        elif args.cmd == "apply":
            m = apply(args.patch, args.app, _read_hex(args.pub, 32), args.allow_downgrade)
            print(f"applied {m['version']}: {', '.join(m['components'])}")
        elif args.cmd == "rollback":
            print(f"rolled back to {rollback(args.app)}")
        else:
            commit(args.app)
    except (PatchError, OSError, zipfile.BadZipFile) as e:
        print(f"patch error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
