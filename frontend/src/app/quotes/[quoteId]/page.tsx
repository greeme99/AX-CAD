"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useEffect, useState } from "react";
import AppShell from "@/components/shell/AppShell";
import { btn2, btnPrimary, card, Err, field, Field, fmt, link, Loading, StatusBadge, td, th } from "@/components/ui";
import { api, can, download, errText, type List, type User } from "@/lib/api";
import { approverCandidates, CATEGORY, lineStatus, sourceLink, TOTAL_KEYS, TOTAL_LABEL, won, type Log, type Quote, type QuoteApproval, type QuoteLine } from "@/lib/quote";
import { useApi } from "@/lib/useApi";

const BADGE = { AUTO: "border border-line text-muted-foreground", MANUAL: "bg-[var(--color-primary-light)] text-[var(--color-primary)]", ERR: "bg-red-100 text-red-700" } as const;
const SEVERITY = { ERROR: ["⛔", "text-red-700"], WARN: ["⚠", "text-amber-700"], INFO: ["ℹ", "text-muted-foreground"] } as const;
const SOURCE = { USER: "직접 입력", TITLE_BLOCK: "표제란" } as Record<string, string>;
const FIELDS = { amount: "금액", unit_price: "단가", qty: "수량" } as const;

function Logs({ logs, onLine }: { logs: Log[]; onLine?: (no: number) => void }) {
  if (!logs.length) return <p className="text-sm text-emerald-700">✓ 문제 없음</p>;
  return (
    <ul className="space-y-1 text-sm">
      {logs.map((l, i) => {
        const [icon, cls] = SEVERITY[l.severity];
        return (
          <li key={i} className={cls}>
            <span aria-hidden>{icon}</span> {l.line_no && onLine ? (
              <button type="button" onClick={() => onLine(l.line_no!)} className="underline">
                라인 {l.line_no}
              </button>
            ) : null}{" "}
            {l.message}
          </li>
        );
      })}
    </ul>
  );
}

// SCR-10: how the line was computed, where it came from, and the FN-19 manual adjustment
function TraceDrawer({ q, line, canEdit, onClose, onChanged }: { q: Quote; line: QuoteLine; canEdit: boolean; onClose: () => void; onChanged: (q: Quote) => void }) {
  const [f, setF] = useState<keyof typeof FIELDS>("amount");
  const [value, setValue] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const status = lineStatus(line);

  async function run(fn: () => Promise<Quote>) {
    setBusy(true);
    setError(null);
    try {
      onChanged(await fn());
      setValue("");
      setReason("");
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }
  const apply = (e: React.FormEvent) => {
    e.preventDefault();
    void run(() => api<Quote>(`/quote-lines/${line.quote_line_id}`, { method: "PATCH", json: { field: f, value, reason } }));
  };

  return (
    <aside role="dialog" aria-label={`라인 ${line.line_no} 추적`} className="fixed inset-y-0 right-0 z-40 w-full max-w-[480px] space-y-4 overflow-y-auto border-l border-line bg-card p-5 shadow-[var(--shadow-card)]">
      <div className="flex items-start justify-between gap-3">
        <h2 className="text-lg font-semibold text-foreground">
          라인 {line.line_no} · {line.item_name}
        </h2>
        <button type="button" onClick={onClose} className={btn2} aria-label="닫기">
          ✕
        </button>
      </div>
      {line.traces.map((t, i) => {
        const href = sourceLink(t, q.document_id);
        return (
          <section key={i} className="space-y-2 rounded-md border border-line p-3 text-sm">
            <p>
              원천: {t.source_kind === "FEATURE" ? "3D 형상" : t.source_kind === "ENTITY" ? "도면 엔티티" : "도면 전체"} {t.source_count}개
              {t.source_count > t.sources.length && ` (앞 ${t.sources.length}개 표시)`}{" "}
              {href && (
                <Link href={href} target="_blank" className={link}>
                  도면에서 보기 ↗
                </Link>
              )}
            </p>
            <p>
              규칙: <span className="font-mono">{t.rule_code}</span> (기준정보 #{q.master_version_id})
            </p>
            <p className="rounded bg-muted p-2 font-mono text-xs">{t.formula_text}</p>
            <dl className="grid grid-cols-2 gap-x-3 gap-y-0.5 font-mono text-xs">
              {Object.entries(t.inputs).map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="truncate text-muted-foreground">{k}</dt>
                  <dd className="text-right">{String(v)}</dd>
                </div>
              ))}
            </dl>
            <p>
              단가: {t.price_item_code ?? "공식 결과"} {t.unit_price == null ? <span className="text-red-700">없음</span> : <span className="font-mono">{won(t.unit_price, 2)}</span>}
            </p>
          </section>
        );
      })}
      <p className="text-sm">
        금액: <span className="font-mono">{won(line.calculated_qty, 6)}</span> {line.unit} × <span className="font-mono">{won(line.calculated_unit_price, 2)}</span> = <span className={`font-mono ${status === "MANUAL" ? "line-through text-muted-foreground" : ""}`}>{won(line.calculated_amount)}</span>
        {status === "MANUAL" && <span className="ml-2 font-mono font-semibold">{won(line.override_amount)} (수동)</span>}
      </p>
      {line.override_reason && <p className="text-sm text-muted-foreground">조정 사유: {line.override_reason}</p>}
      {canEdit && q.status === "DRAFT" && (
        <form onSubmit={apply} className="space-y-3 border-t border-line pt-3" aria-label="수동 조정">
          <h3 className="font-semibold text-foreground">수동 조정</h3>
          <div className="flex gap-2">
            <Field label="항목">
              <select value={f} onChange={(e) => setF(e.target.value as keyof typeof FIELDS)} className={field}>
                {Object.entries(FIELDS).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="값">
              <input type="number" min="0" step="any" required value={value} onChange={(e) => setValue(e.target.value)} className={`${field} font-mono`} />
            </Field>
          </div>
          <Field label="사유 (5자 이상)">
            <input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} className={field} />
          </Field>
          <Err text={error} />
          <div className="flex gap-2">
            <button type="submit" disabled={busy || reason.trim().length < 5 || value === ""} className={btnPrimary}>
              적용
            </button>
            {status === "MANUAL" && (
              <button type="button" disabled={busy} onClick={() => void run(() => api<Quote>(`/quote-lines/${line.quote_line_id}/override`, { method: "DELETE" }))} className={btn2}>
                조정 취소
              </button>
            )}
          </div>
        </form>
      )}
    </aside>
  );
}

const DECISION = { PENDING: "대기", APPROVED: "승인", REJECTED: "반려", CANCELLED: "취소(관리자)" } as const;

// FN-22: request review (no ERROR), approve / reject (comment), history; lines freeze from IN_REVIEW
function ApprovalPanel({ q, me, hasErrors, onChanged }: { q: Quote; me: User; hasErrors: boolean; onChanged: (q: Quote) => void }) {
  const members = useApi<List<{ user_id: number; user_name: string; roles: string[] }>>(`/projects/${q.project_id}/members`);
  const [approver, setApprover] = useState("");
  const [comment, setComment] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const pending: QuoteApproval | undefined = q.approvals.find((a) => a.status === "PENDING");
  const candidates = approverCandidates(members.data?.items ?? [], q, me.user_id);

  async function run(path: string, json?: object) {
    setBusy(true);
    setError(null);
    try {
      onChanged(await api<Quote>(path, { method: "POST", json }));
      setComment("");
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section aria-label="승인" className={`${card} space-y-3`}>
      <h2 className="font-semibold text-foreground">승인</h2>
      {q.status === "DRAFT" && can(me, "ESTIMATOR") && (
        <form
          className="space-y-2"
          onSubmit={(e) => {
            e.preventDefault();
            void run(`/quotes/${q.quote_id}/approvals`, { approver_id: Number(approver), comment: comment || undefined });
          }}
        >
          <Field label="승인자 (작성·조정하지 않은 검토자)">
            <select value={approver} onChange={(e) => setApprover(e.target.value)} className={field}>
              <option value="">선택</option>
              {candidates.map((m) => (
                <option key={m.user_id} value={m.user_id}>
                  {m.user_name}
                </option>
              ))}
            </select>
          </Field>
          {members.data && candidates.length === 0 && <p className="text-xs text-muted-foreground">선택할 수 있는 검토자가 없습니다. 관리자에게 프로젝트 검토자 추가를 요청하세요.</p>}
          <Field label="의견 (선택)">
            <input value={comment} onChange={(e) => setComment(e.target.value)} maxLength={2000} className={field} />
          </Field>
          <button type="submit" disabled={busy || hasErrors || !approver} title={hasErrors ? "검증 ERROR를 먼저 해결하세요" : undefined} className={btnPrimary}>
            승인 요청
          </button>
        </form>
      )}
      {pending && pending.approver_id === me.user_id && (
        <div className="space-y-2">
          <Field label="의견 (반려 시 필수)">
            <input value={comment} onChange={(e) => setComment(e.target.value)} maxLength={2000} className={field} />
          </Field>
          <div className="flex gap-2">
            <button type="button" disabled={busy} onClick={() => void run(`/quote-approvals/${pending.approval_id}/decision`, { decision: "APPROVED", comment: comment || undefined })} className={btnPrimary}>
              승인
            </button>
            <button type="button" disabled={busy || !comment.trim()} onClick={() => void run(`/quote-approvals/${pending.approval_id}/decision`, { decision: "REJECTED", comment })} className={btn2}>
              반려
            </button>
          </div>
        </div>
      )}
      {pending && pending.approver_id !== me.user_id && <p className="text-sm text-muted-foreground">{pending.approver_name} 님이 검토 중입니다. 검토 중에는 라인을 조정할 수 없습니다.</p>}
      {pending && can(me) && pending.approver_id !== me.user_id && (
        <button type="button" disabled={busy} onClick={() => void run(`/quote-approvals/${pending.approval_id}/cancel`)} className={btn2}>
          요청 취소(관리자)
        </button>
      )}
      <Err text={error} />
      {q.approvals.length > 0 && (
        <ul className="space-y-1 border-t border-line pt-2 text-xs">
          {q.approvals.map((a) => (
            <li key={a.approval_id}>
              <b>{DECISION[a.status]}</b> · {a.approver_name} · {fmt(a.decided_at ?? a.created_at)}
              {a.comment && <span className="block text-muted-foreground">요청: “{a.comment}”</span>}
              {a.decision_comment && <span className="block text-muted-foreground">결정: “{a.decision_comment}”</span>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// FN-21: drafts any time (watermarked, may carry the internal cost basis), the official document
// only once the quote is confirmed and never with the internal basis
function ReportButtons({ q, onError }: { q: Quote; onError: (m: string | null) => void }) {
  const [basis, setBasis] = useState(false);
  const [busy, setBusy] = useState(false);
  const official = q.status === "CONFIRMED";
  async function get(format: "pdf" | "xlsx") {
    setBusy(true);
    onError(null);
    try {
      await download(`/quotes/${q.quote_id}/report?format=${format}&official=${official}&basis=${!official && basis}`, `${q.quote_no}${official ? "" : "-DRAFT"}.${format}`);
    } catch (e) {
      onError(errText(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="ml-auto flex flex-wrap items-center gap-2" aria-label="견적서 출력">
      {!official && (
        <label className="flex items-center gap-1 text-xs text-muted-foreground" title="원가·조정 사유가 들어갑니다. 외부 발송 금지">
          <input type="checkbox" checked={basis} onChange={(e) => setBasis(e.target.checked)} /> 산출근거 첨부(내부용)
        </label>
      )}
      <button type="button" disabled={busy} onClick={() => void get("pdf")} className={official ? btnPrimary : btn2} title={official ? undefined : "승인 전에는 초안(DRAFT) 워터마크가 들어갑니다"}>
        {official ? "견적서 PDF" : "초안 PDF"}
      </button>
      <button type="button" disabled={busy} onClick={() => void get("xlsx")} className={btn2}>
        {official ? "견적서 Excel" : "초안 Excel"}
      </button>
    </div>
  );
}

export default function QuotePage({ params }: { params: Promise<{ quoteId: string }> }) {
  const { quoteId } = use(params);
  const router = useRouter();
  const me = useApi<User>("/auth/me").data;
  const { data, error } = useApi<Quote>(`/quotes/${encodeURIComponent(quoteId)}`);
  const [q, setQ] = useState<Quote | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [check, setCheck] = useState<{ logs: Log[]; has_errors: boolean } | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const canEdit = !!me && can(me, "ESTIMATOR");
  const quote = q ?? data ?? null;

  const validate = () =>
    api<{ logs: Log[]; has_errors: boolean }>(`/quotes/${encodeURIComponent(quoteId)}/validate`, { method: "POST" }).then(setCheck, (e) => setMsg(errText(e)));
  useEffect(() => {
    if (me && can(me, "ESTIMATOR", "REVIEWER")) void validate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [me, quote?.lines]);

  async function recalc() {
    if (!quote) return;
    setBusy(true);
    setMsg(null);
    try {
      const src = quote.source_kind === "REVISION" ? { revision_id: quote.revision_id } : { document_id: quote.document_id };
      const { qty, material_code, thickness_mm } = quote.inputs;
      const n = await api<Quote>("/quotes", { method: "POST", json: { ...src, qty, material_code: material_code ?? undefined, thickness_mm: thickness_mm ?? undefined } });
      router.push(`/quotes/${n.quote_id}`);
    } catch (e) {
      setMsg(errText(e));
    } finally {
      setBusy(false);
    }
  }

  if (error) return <AppShell title="견적"><Err text={errText(error)} /></AppShell>;
  if (!quote) return <AppShell title="견적"><Loading /></AppShell>;
  const sel = quote.lines.find((l) => l.quote_line_id === selected) ?? null;
  const eff = quote.effective;
  const counts = { AUTO: 0, MANUAL: 0, ERR: 0 };
  quote.lines.forEach((l) => counts[lineStatus(l)]++);
  const src = quote.inputs.input_source ?? {};

  return (
    <AppShell title={`견적 ${quote.quote_no}`}>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <StatusBadge status={quote.status} />
        <Link href={`/documents/${quote.document_id}`} className={link}>
          ← 원천 도면
        </Link>
        <span className="text-muted-foreground">
          {quote.source_kind === "REVISION" ? "2D 도면" : "3D 모델"} · 기준정보 #{quote.master_version_id} · {fmt(quote.created_at)}
        </span>
        <span>
          재질 <b>{quote.inputs.material_code ?? "-"}</b>
          {src.material_code && <span className="text-xs text-muted-foreground"> ({SOURCE[src.material_code]})</span>} · 두께 <b>{quote.inputs.thickness_mm ?? "-"}</b>
          {src.thickness_mm && <span className="text-xs text-muted-foreground"> ({SOURCE[src.thickness_mm]})</span>} · 수량 <b>{quote.inputs.qty}</b>
          {src.qty && <span className="text-xs text-muted-foreground"> ({SOURCE[src.qty]})</span>}
        </span>
        <div className="ml-auto flex gap-2">
          <button type="button" onClick={() => void validate()} className={btn2}>
            검증
          </button>
          {canEdit && (
            <button type="button" disabled={busy} onClick={() => void recalc()} className={btnPrimary} title="현재 기준정보와 도면으로 새 견적을 만듭니다">
              {busy ? "산출 중..." : "재산출"}
            </button>
          )}
        </div>
      </div>
      <Err text={msg} />

      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <div className="overflow-x-auto">
          <table className="w-full text-sm" aria-label="견적 라인">
            <thead>
              <tr>
                {["No", "구분", "항목", "수량", "단위", "단가", "금액", "상태"].map((h) => (
                  <th key={h} className={`${th} ${["수량", "단가", "금액"].includes(h) ? "text-right" : ""}`}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {quote.lines.map((l) => {
                const s = lineStatus(l);
                return (
                  <tr key={l.quote_line_id} onClick={() => setSelected(l.quote_line_id)} aria-selected={selected === l.quote_line_id} className={`cursor-pointer border-t border-line hover:bg-hover ${selected === l.quote_line_id ? "bg-[var(--color-primary-light)]" : ""}`}>
                    <td className={td}>{l.line_no}</td>
                    <td className={td}>{CATEGORY[l.cost_category]}</td>
                    <td className={td}>
                      <button type="button" className="text-left hover:underline" onClick={() => setSelected(l.quote_line_id)}>
                        {l.item_name}
                      </button>
                    </td>
                    <td className={`${td} text-right font-mono`}>{won(l.override_qty ?? l.calculated_qty, 4)}</td>
                    <td className={td}>{l.unit}</td>
                    <td className={`${td} text-right font-mono`}>{won(l.override_unit_price ?? l.calculated_unit_price, 2)}</td>
                    <td className={`${td} text-right font-mono`}>
                      {s === "MANUAL" && <span className="mr-1 text-xs text-muted-foreground line-through">{won(l.calculated_amount)}</span>}
                      {won(l.effective_amount)}
                    </td>
                    <td className={td}>
                      <span className={`rounded-full px-2 py-0.5 text-xs ${BADGE[s]}`}>{s}</span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="mt-2 text-xs text-muted-foreground">행을 누르면 계산 근거와 원천 도면을 볼 수 있습니다.</p>
        </div>

        <div className="space-y-4">
          <section aria-label="원가 요약" className={card}>
            <h2 className="mb-2 font-semibold text-foreground">원가 요약</h2>
            {eff ? (
              <dl className="space-y-1 text-sm">
                {TOTAL_KEYS.map((k) => (
                  <div key={k} className={`flex justify-between gap-2 ${k === "manufacturing_cost" || k === "supply_amount" ? "border-t border-line pt-1" : ""} ${k === "total_amount" ? "font-semibold text-foreground" : ""}`}>
                    <dt className="text-muted-foreground">{TOTAL_LABEL[k]}</dt>
                    <dd className="text-right font-mono">
                      {eff[k] !== quote[k] && <span className="mr-1 text-xs text-muted-foreground line-through">{won(quote[k])}</span>}
                      {won(eff[k])}
                    </dd>
                  </div>
                ))}
              </dl>
            ) : (
              <Err text="합계가 범위를 벗어났습니다" />
            )}
          </section>
          <section aria-label="검증" className={card}>
            <h2 className="mb-2 font-semibold text-foreground">검증 {check?.has_errors && <span className="text-sm text-red-700">(승인 요청 불가)</span>}</h2>
            {check ? <Logs logs={check.logs} onLine={(no) => setSelected(quote.lines.find((l) => l.line_no === no)?.quote_line_id ?? null)} /> : <Loading />}
          </section>
          {me && <ApprovalPanel q={quote} me={me} hasErrors={!check || check.has_errors} onChanged={setQ} />}
          {quote.logs.some((l) => l.severity !== "ERROR") && (
            <section aria-label="산출 메모" className={card}>
              <h2 className="mb-2 font-semibold text-foreground">산출 메모</h2>
              <Logs logs={quote.logs.filter((l) => l.severity !== "ERROR")} />
            </section>
          )}
        </div>
      </div>

      <footer className="flex flex-wrap items-center gap-4 border-t border-line pt-3 text-sm">
        <span>
          합계(VAT 포함) <b className="font-mono">{eff ? `₩${won(eff.total_amount)}` : "-"}</b>
        </span>
        <span className="text-muted-foreground">
          자동 {counts.AUTO} · 수동 {counts.MANUAL} · 오류 {counts.ERR}
        </span>
        {me && can(me, "ESTIMATOR", "REVIEWER") && <ReportButtons q={quote} onError={setMsg} />}
      </footer>

      {sel && <TraceDrawer key={sel.quote_line_id} q={quote} line={sel} canEdit={canEdit} onClose={() => setSelected(null)} onChanged={setQ} />}
    </AppShell>
  );
}
