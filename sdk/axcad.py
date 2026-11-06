"""AX-CAD Python SDK (Track 9 Q4, plan.md §15.5): automate AX-CAD from outside the app.

Standard library only (copy this one file next to your script). It talks to the app's REST API —
the same rules, roles and audit log as the screens; nothing runs inside the app.

    from axcad import Client
    cad = Client.local()                       # this PC's install (reads the install token)
    cad = Client("https://cad.example", token=Client.login_token("https://cad.example", "me", "pw"))

Errors raise AxcadError(status, code, message) with the API's error code.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

Transport = Callable[[str, str, dict[str, str], bytes | None], tuple[int, dict[str, str], bytes]]
TIMEOUT_S = 300


class AxcadError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code, self.message = status, code, message


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # urllib copies the Authorization / install-token headers to a redirect target: never follow
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_opener = urllib.request.build_opener(_NoRedirect)
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _urllib(
    method: str, url: str, headers: dict[str, str], data: bytes | None
) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _opener.open(req, timeout=TIMEOUT_S) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


class Client:
    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        local_token: str | None = None,
        transport: Transport | None = None,
    ):
        self.base = base_url.rstrip("/")
        u = urllib.parse.urlsplit(self.base)
        loopback = u.hostname in LOOPBACK
        if u.scheme not in ("http", "https") or (u.scheme == "http" and not loopback and token):
            raise AxcadError(0, "BAD_URL", "use https (plain http only for this PC)")
        if local_token and not loopback:
            raise AxcadError(0, "BAD_URL", "the install token is only sent to this PC")
        self.headers: dict[str, str] = {}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"
        if local_token:
            self.headers["X-AXCAD-Local"] = local_token
        self._send = transport or _urllib

    # --- connection ------------------------------------------------------------------------

    @classmethod
    def local(cls, port: int = 18000, token: str | None = None) -> Client:
        """The per-PC install: its API on 127.0.0.1 and the install token (AXCAD_LOCAL_TOKEN, or
        the axcad.env of %LOCALAPPDATA%\\AX-CAD\\data)."""
        token = token or os.environ.get("AXCAD_LOCAL_TOKEN") or _env_token()
        if not token:
            raise AxcadError(
                0, "NO_LOCAL_TOKEN", "AXCAD_LOCAL_TOKEN not set and no axcad.env found"
            )
        return cls(f"http://127.0.0.1:{port}", local_token=token)

    @staticmethod
    def login_token(
        base_url: str, login_id: str, password: str, transport: Transport | None = None
    ) -> str:
        u = urllib.parse.urlsplit(base_url)
        if u.scheme != "https" and u.hostname not in LOOPBACK:
            raise AxcadError(0, "BAD_URL", "use https (plain http only for this PC)")
        c = Client(base_url, transport=transport)
        return str(
            c.post("/api/auth/login", {"login_id": login_id, "password": password})["access_token"]
        )

    # --- raw calls -------------------------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        raw: bytes | None = None,
        content_type: str | None = None,
    ) -> Any:
        headers = dict(self.headers)
        data = raw
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        elif content_type:
            headers["Content-Type"] = content_type
        status, resp_headers, payload = self._send(method, self.base + path, headers, data)
        ctype = {k.lower(): v for k, v in resp_headers.items()}.get("content-type", "")
        if "application/json" not in ctype:
            if status >= 300:  # a redirect is not followed (it would carry the token)
                raise AxcadError(status, "HTTP_ERROR", payload[:200].decode(errors="replace"))
            return payload
        j = json.loads(payload or b"{}")
        if not j.get("success"):
            err = j.get("error") or {}
            raise AxcadError(status, err.get("code", "ERROR"), err.get("message", ""))
        return j["data"]

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def post(self, path: str, body: Any = None) -> Any:
        return self.request("POST", path, body if body is not None else {})

    # --- projects and documents ------------------------------------------------------------

    def create_project(self, code: str, name: str) -> dict[str, Any]:
        return self.post("/api/projects", {"project_code": code, "project_name": name})

    def create_document(
        self, project_id: int, doc_no: str, doc_type: str, title: str
    ) -> dict[str, Any]:
        return self.post(
            f"/api/projects/{project_id}/documents",
            {"doc_no": doc_no, "doc_type": doc_type, "title": title},
        )

    def upload_dxf(self, document_id: int, path: str | Path) -> dict[str, Any]:
        p = Path(path)
        name = "".join(c for c in p.name if c not in '"\r\n\\')
        boundary = uuid.uuid4().hex
        data = (
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
                "Content-Type: application/dxf\r\n\r\n"
            ).encode()
            + p.read_bytes()
            + f"\r\n--{boundary}--\r\n".encode()
        )
        return self.request(
            "POST",
            f"/api/documents/{document_id}/dxf",
            raw=data,
            content_type=f"multipart/form-data; boundary={boundary}",
        )

    # --- 3D model --------------------------------------------------------------------------

    def add_feature(
        self, document_id: int, feature_type: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        return self.post(
            f"/api/documents/{document_id}/features",
            {"feature_type": feature_type, "params": params},
        )

    def features(self, document_id: int) -> list[dict[str, Any]]:
        return list(self.get(f"/api/documents/{document_id}/features")["items"])

    def sketch(
        self, document_id: int, entities: list[dict[str, Any]], plane: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        plane = plane or {"origin": [0, 0, 0], "normal": [0, 0, 1], "xdir": [1, 0, 0]}
        ents = [{"id": i + 1, **e} for i, e in enumerate(entities)]
        return self.add_feature(
            document_id, "SKETCH", {"plane": plane, "entities": ents, "constraints": []}
        )

    def export_model(self, document_id: int, fmt: str = "STEP") -> bytes:
        return bytes(self.get(f"/api/documents/{document_id}/model?format={fmt}"))

    # --- sheet metal -----------------------------------------------------------------------

    def sheet_base(
        self,
        document_id: int,
        sketch_id: int,
        thickness: float,
        bend_radius: float | None = None,
        k_factor: float = 0.44,
    ) -> dict[str, Any]:
        return self.add_feature(
            document_id,
            "SHEET_BASE",
            {
                "sketch_feature_id": sketch_id,
                "thickness": thickness,
                "bend_radius": bend_radius,
                "k_factor": k_factor,
            },
        )

    def flange(
        self,
        document_id: int,
        target_id: int,
        edge: int,
        length: float,
        angle: float = 90,
        up: bool = True,
        inset_start: float = 0,
        inset_end: float = 0,
    ) -> dict[str, Any]:
        return self.add_feature(
            document_id,
            "SHEET_FLANGE",
            {
                "target_feature_id": target_id,
                "edge": edge,
                "length": length,
                "angle": angle,
                "up": up,
                "inset_start": inset_start,
                "inset_end": inset_end,
            },
        )

    def flat_pattern(self, feature_id: int) -> dict[str, Any]:
        return dict(self.get(f"/api/features/{feature_id}/sheet"))

    def flat_dxf(self, feature_id: int) -> bytes:
        return bytes(self.get(f"/api/features/{feature_id}/sheet?format=dxf"))

    # --- assemblies ------------------------------------------------------------------------

    def add_component(
        self,
        assembly_id: int,
        feature_id: int,
        t: tuple[float, float, float] = (0, 0, 0),
        r: tuple[float, float, float] = (0, 0, 0),
        fixed: bool | None = None,
    ) -> dict[str, Any]:
        return self.post(
            f"/api/documents/{assembly_id}/components",
            {
                "source_feature_id": feature_id,
                "placement": {"t": list(t), "r": list(r)},
                "fixed": fixed,
            },
        )

    def add_mate(
        self,
        assembly_id: int,
        mate_type: str,
        a: tuple[int, dict[str, Any]],
        b: tuple[int, dict[str, Any]],
        value: float | None = None,
        flip: bool = False,
    ) -> dict[str, Any]:
        return self.post(
            f"/api/documents/{assembly_id}/mates",
            {
                "mate_type": mate_type,
                "a": {"component_id": a[0], "face": a[1]},
                "b": {"component_id": b[0], "face": b[1]},
                "value": value,
                "flip": flip,
            },
        )

    def interference(self, assembly_id: int, clearance: float = 0) -> dict[str, Any]:
        return self.post(f"/api/documents/{assembly_id}/interference", {"clearance": clearance})

    def faces(self, feature_id: int) -> list[dict[str, Any]]:
        """Face signatures of a body (what a mate or a fillet pick sends)."""
        return [f["sig"] for f in self.get(f"/api/features/{feature_id}/mesh")["faces"]]

    # --- parametric blocks and quotes ------------------------------------------------------

    def param_block(
        self, template: str, values: dict[str, float], state: str | None = None
    ) -> dict[str, Any]:
        return self.post(
            f"/api/param-blocks/{urllib.parse.quote(template, safe='')}/instantiate",
            {"values": values, "state": state},
        )

    def quote_model(
        self, document_id: int, qty: int, material_code: str, **extra: Any
    ) -> dict[str, Any]:
        return self.post(
            "/api/quotes",
            {"document_id": document_id, "qty": qty, "material_code": material_code, **extra},
        )

    def quote_drawing(
        self, revision_id: str, qty: int, material_code: str, thickness_mm: str, **extra: Any
    ) -> dict[str, Any]:
        return self.post(
            "/api/quotes",
            {
                "revision_id": revision_id,
                "qty": qty,
                "material_code": material_code,
                "thickness_mm": thickness_mm,
                **extra,
            },
        )

    def quote_report(self, quote_id: int, fmt: str = "xlsx") -> bytes:
        return bytes(
            self.get(f"/api/quotes/{int(quote_id)}/report?format={urllib.parse.quote(fmt)}")
        )


def _env_token() -> str | None:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return None
    try:
        for line in (
            (Path(base) / "AX-CAD" / "data" / "axcad.env").read_text(encoding="utf-8").splitlines()
        ):
            if line.startswith("LOCAL_TOKEN="):
                return line.split("=", 1)[1].strip() or None
    except OSError:
        return None
    return None
