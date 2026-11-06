"""Track 9 Q1: AI Copilot API (plan.md §15.2). Rules first — design checks, quote advice, Korean
commands, built-in help — and the optional LLM only for what they cannot answer."""

import re
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from backend.api.auth import CurrentUser, Db, get_document, need
from backend.api.common import ApiError, body
from backend.db.models import AuditLog, Document, Feature, Revision
from core.copilot import dfm, kb, llm, nlcmd

router = APIRouter()
Page = Literal["home", "project", "document", "cad2d", "model3d", "quote", "admin", "other"]
CHECK_RE = re.compile(r"(?:점검|검사|검토|문제|체크|확인해|dfm|오류\s*찾)", re.IGNORECASE)
Id = Annotated[int, Field(ge=1, le=2**63 - 1)]
RevId = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
MAX_SHEET_CHECKS = 20  # sheet bodies checked per request (CPU in the request thread)


class Context(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: Page = "other"
    document_id: Id | None = None
    revision_id: RevId | None = None
    quote_id: Id | None = None


class AskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: Annotated[str, Field(min_length=1, max_length=llm.MAX_QUESTION)]
    context: Context = Context()


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    llm_enabled: bool | None = None
    # "" clears the saved key; None keeps it
    api_key: Annotated[str, Field(max_length=200, pattern=r"^[A-Za-z0-9_\-]*$")] | None = None


def _var() -> Any:
    from backend.api import main  # late: main imports this module

    return main.VAR_DIR


def _features(db: Db, document_id: int) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(Feature).where(Feature.document_id == document_id).order_by(Feature.seq)
    )
    return [
        {
            "feature_id": f.feature_id,
            "feature_type": f.feature_type,
            "status": f.status,
            "suppressed": f.suppressed,
        }
        for f in rows
    ]


def _drawing(db: Db, rev: Revision) -> list[dict[str, Any]]:
    from backend.api.routes_master import KST, active_version
    from backend.api.routes_quote import _metrics2d
    from core.dxf.reader import DxfError

    try:
        m = _metrics2d(db, rev, active_version(db, datetime.now(KST).date()))
    except (DxfError, ApiError) as e:  # a drawing that cannot be measured is itself a finding
        msg = getattr(e, "message", str(e))
        fix = "도면을 다시 저장하거나 ezdxf audit로 오류를 확인하세요"
        return [
            dfm.finding(
                "ERROR", "DFM_DRAWING_UNREADABLE", f"도면을 분석하지 못했습니다: {msg}", fix
            )
        ]
    return dfm.check_drawing(m)


def _findings(
    db: Db, user: Any, ctx: Context
) -> tuple[list[dict[str, Any]], str | None, list[dict[str, Any]]]:
    """(findings, document type, features) for the context's quote, drawing revision or document."""
    if ctx.quote_id is not None:
        from backend.api.routes_quote import _validation, _visible_quote

        need(user, "ESTIMATOR", "REVIEWER")
        logs = _validation(db, _visible_quote(db, user, ctx.quote_id))
        return dfm.sort_findings(dfm.advise_quote(logs)), "QUOTE", []
    if ctx.revision_id is not None:
        from backend.api import main

        need(user, "ESTIMATOR", "DESIGNER")
        rev, _ = main._revision(db, user, ctx.revision_id)
        return dfm.sort_findings(_drawing(db, rev)), "DRAWING", []
    if ctx.document_id is None:
        return [], None, []
    from backend.api.routes_quote import _bodies

    doc: Document = get_document(db, user, ctx.document_id)
    need(user, "ESTIMATOR", "DESIGNER")
    if doc.doc_type == "ASSEMBLY":
        from backend.api.routes_assembly import _state

        st = _state(db, doc)
        return dfm.sort_findings(dfm.check_assembly(st["components"], st["mates"])), "ASSEMBLY", []
    items: list[dict[str, Any]] = []
    # a drawing document can also hold a 3D model built from its DXF: check both
    if doc.doc_type == "DRAWING" and doc.current_revision_id:
        rev = db.get(Revision, doc.current_revision_id)
        items += _drawing(db, rev) if rev else []
    feats = _features(db, doc.document_id)
    if feats:
        items += dfm.check_model(feats, _bodies(db, doc.document_id))
        items += _sheet_checks(db, doc)
    return dfm.sort_findings(items), doc.doc_type, feats


def _sheet_checks(db: Db, doc: Document) -> list[dict[str, Any]]:
    """Track 9 Q3: each sheet metal body shown (the last feature of its chain)."""
    from backend.api.routes_model import SHEET_TYPES, _consumed, _params, _sheet_spec
    from core.geometry.errors import GeomError

    consumed = _consumed(db, doc.document_id)
    rows = db.scalars(
        select(Feature).where(
            Feature.document_id == doc.document_id, Feature.feature_type.in_(SHEET_TYPES)
        )
    )
    out: list[dict[str, Any]] = []
    shown = [f for f in rows if f.feature_id not in consumed and f.status == "OK"]
    for f in shown[:MAX_SHEET_CHECKS]:  # each one recomputes its outline in this request
        try:
            spec = _sheet_spec(db, doc, _params(f.feature_type, f.params))
        except (GeomError, ArithmeticError, LookupError, ValueError):
            continue  # a broken sheet is reported as a failed feature, not here
        out += dfm.check_sheet(f.feature_id, spec)
    return out


@router.get("/api/copilot/findings")
def findings(
    user: CurrentUser,
    db: Db,
    document_id: Annotated[int, Query(ge=1)] | None = None,
    revision_id: Annotated[str, Query(pattern=r"^[0-9a-f]{32}$")] | None = None,
    quote_id: Annotated[int, Query(ge=1)] | None = None,
) -> Any:
    """Design checks of a document / drawing revision, or advice on a quote's validation."""
    if [document_id, revision_id, quote_id].count(None) != 2:
        raise ApiError(
            422, "REQUEST_INVALID", "Give exactly one of document_id, revision_id, quote_id"
        )
    ctx = Context(document_id=document_id, revision_id=revision_id, quote_id=quote_id)
    items, doc_type, _ = _findings(db, user, ctx)
    return body(
        {
            "document_type": doc_type,
            "findings": items,
            "counts": {s: sum(1 for f in items if f["severity"] == s) for s in dfm.SEVERITIES},
        }
    )


def _summary_text(items: list[dict[str, Any]]) -> str:
    if not items:
        return "점검 결과 지적 사항이 없습니다."
    c = {s: sum(1 for f in items if f["severity"] == s) for s in dfm.SEVERITIES}
    head = f"오류 {c['ERROR']}건, 경고 {c['WARN']}건, 참고 {c['INFO']}건입니다."
    top = "\n".join(f"- {f['message']} → {f['fix']}" for f in items[:5])
    return f"{head}\n{top}" + ("\n(전체는 점검/조언 탭에서 보세요)" if len(items) > 5 else "")


@router.post("/api/copilot/ask")
def ask(req: AskIn, user: CurrentUser, db: Db) -> Any:
    ctx, text = req.context, req.message.strip()
    out: dict[str, Any] = {
        "answer": "",
        "source": "NONE",
        "actions": [],
        "findings": [],
        "related": [],
    }
    cmd = nlcmd.parse(text, ctx.page)
    if cmd is not None:
        if "error" in cmd:
            out |= {"answer": cmd["error"], "source": "RULE"}
        else:
            out |= {
                "answer": f"{cmd['summary']} — 미리보기를 확인하고 실행을 누르세요.",
                "source": "RULE",
                "actions": [cmd],
            }
        return body(out)
    has_target = any(v is not None for v in (ctx.document_id, ctx.revision_id, ctx.quote_id))
    if CHECK_RE.search(text) and has_target:
        items, _, _ = _findings(db, user, ctx)
        return body(out | {"answer": _summary_text(items), "source": "RULE", "findings": items})
    hits = kb.search(text, ctx.page)
    if hits:
        return body(out | {"answer": hits[0]["a"], "source": "KB", "related": hits[1:]})
    prov = llm.provider(_var())
    # the LLM spends the administrator's key: working roles only (a viewer gets the help topics)
    may_llm = bool({"ADMIN", "DESIGNER", "ESTIMATOR", "REVIEWER"} & set(user.role_codes))
    if prov is not None and may_llm:
        if not llm.LIMIT.take(user.user_id):
            return body(out | {"answer": "LLM 질문이 너무 잦습니다. 1분 뒤 다시 시도하세요."})
        try:
            items, doc_type, feats = _findings(db, user, ctx) if has_target else ([], None, [])
        except ApiError:
            items, doc_type, feats = [], None, []  # no access to the checks: ask without them
        try:
            answer = prov.answer(llm.scrub(text), llm.summary(ctx.page, doc_type, feats, items))
            return body(out | {"answer": answer, "source": "LLM"})
        except llm.LlmError as e:
            return body(
                out
                | {
                    "answer": f"{e}. 규칙 기반 도움말을 참고하세요.",
                    "related": kb.for_page(ctx.page),
                }
            )
    return body(
        out
        | {
            "answer": "규칙으로 답을 찾지 못했습니다. 아래 도움말을 보거나 '점검해 줘', '100x50 사각형'처럼 물어보세요.",
            "related": kb.for_page(ctx.page),
        }
    )


@router.get("/api/copilot/help")
def help_topics(user: CurrentUser, page: Page = "other") -> Any:
    return body({"topics": kb.for_page(page, limit=8)})


@router.get("/api/copilot/settings")
def get_settings(user: CurrentUser) -> Any:
    return body(llm.public(_var()))


@router.put("/api/copilot/settings")
def put_settings(req: SettingsIn, user: CurrentUser, db: Db) -> Any:
    need(user, "ADMIN")
    before = llm.public(_var())
    llm.save(_var(), req.llm_enabled, req.api_key)
    after = llm.public(_var())
    # who turned the outbound LLM on or changed its key (never the key itself)
    key = None if req.api_key is None else ("CLEARED" if req.api_key == "" else "SET")
    db.add(
        AuditLog(
            object_type="copilot_settings",
            object_id="llm",
            action="UPDATE",
            old_value={"llm_enabled": before["llm_enabled"], "key_source": before["key_source"]},
            new_value={
                "llm_enabled": after["llm_enabled"],
                "key_source": after["key_source"],
                "key": key,
            },
            user_id=user.user_id,
        )
    )
    db.commit()
    return body(after)
