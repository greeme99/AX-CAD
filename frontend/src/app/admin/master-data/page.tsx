"use client";

import { useState } from "react";
import AppShell from "@/components/shell/AppShell";
import { btn2, btnDanger, btnPrimary, Err, field, Field, fmt, Loading, StatusBadge, td, th } from "@/components/ui";
import { api, ApiError, can, download, errText, qs, type List, type User } from "@/lib/api";
import { categories, filterCostbook, NO_FILTER, type CbFilter, type CbItem } from "@/lib/costbook";
import { useApi } from "@/lib/useApi";

type Version = { version_id: number; version_code: string; effective_from: string; status: "DRAFT" | "ACTIVE"; note: string | null; created_at: string; activated_at: string | null };
type Rows = Record<string, unknown>[];
type Bundle = Version & { materials: Rows; price_items: Rows; process_rules: Rows; mapping_rules: Rows; work_items: Rows; adjust_factors: Rows; costbook_items: Rows; costbook_resources: Rows; resource_prices: Rows; qto_objects: Rows; cost_ratios: Record<string, unknown> | null };

// FN-16 columns shown per table; rates are stored as fractions and shown as %
const TABLES: [keyof Bundle, string, string[]][] = [
  ["materials", "재질", ["material_code", "material_name", "thickness_min_mm", "thickness_max_mm", "density_g_cm3", "unit_price_per_kg", "scrap_rate", "valid_until"]],
  ["price_items", "단가(임률·기계경비·외주)", ["item_code", "item_type", "unit", "unit_price", "valid_until"]],
  ["process_rules", "공정 규칙", ["rule_code", "process_code", "process_name", "input_metric", "formula_text", "params", "labor_item_code", "machine_item_code"]],
  ["mapping_rules", "레이어·표제란·공사 수량 매핑", ["rule_type", "target", "pattern"]],
  // Track 6: construction quotes
  ["work_items", "공종 단가(공사 견적)", ["item_code", "work_category", "item_name", "spec", "unit", "qty_formula", "loss_rate", "material_price", "labor_price", "expense_price", "costbook_item_code", "valid_until"]],
  ["adjust_factors", "보정계수(지역·시점)", ["factor_type", "code", "factor", "note"]],
  // Track 8 P4-6: 건설공사 표준품셈 (items with their source, resources per unit, resource prices)
  ["costbook_items", "표준품셈 항목(출처)", ["item_code", "item_name", "spec", "unit", "category", "notice_no", "effective_from", "page_no", "extract_method", "reviewer", "reviewed_at"]],
  ["costbook_resources", "품셈 투입량(단위당)", ["item_code", "resource_type", "resource_code", "qty_per_unit", "percent_of"]],
  ["resource_prices", "자원 단가(노임·자재·기계경비)", ["resource_code", "resource_type", "resource_name", "unit", "unit_price", "price_date", "source", "valid_until"]],
  ["qto_objects", "도면 객체 수량(obj_코드)", ["code", "name", "layer_pattern", "block_pattern", "attribute", "quantity_type", "factor", "waste_rate"]],
];
const RATES = ["scrap_rate", "overhead_rate", "admin_rate", "profit_rate", "vat_rate", "loss_rate", "indirect_labor_rate", "indirect_cost_rate"];
const show = (k: string, v: unknown) => (v == null || v === "" ? "—" : RATES.includes(k) ? `${Number(v) * 100}%` : typeof v === "object" ? JSON.stringify(v) : String(v));

const PAGE_ROWS = 200; // thousands of rows would freeze the page: show a page at a time

// one master table: text search over its columns, a page of rows at a time
function DataTable({ title, rows, total = rows.length, cols, query, onQuery, extra, action }: { title: string; rows: Rows; total?: number; cols: string[]; query: string; onQuery: (q: string) => void; extra?: React.ReactNode; action?: (shown: Rows) => React.ReactNode }) {
  const [more, setMore] = useState(1);
  const q = query.trim().toLowerCase();
  const hit = q ? rows.filter((r) => cols.some((c) => String(r[c] ?? "").toLowerCase().includes(q))) : rows;
  const shown = hit.slice(0, PAGE_ROWS * more);
  return (
    <div className="overflow-x-auto">
      <h3 className="mb-1 font-semibold text-foreground">
        {title} <span className="text-sm font-normal text-muted-foreground">{hit.length === rows.length ? `${rows.length}건` : `${hit.length} / ${rows.length}건`}</span>
      </h3>
      {total > 20 && (
        <div className="mb-2 flex flex-wrap items-end gap-3">
          <input aria-label={`${title} 검색`} placeholder="검색 (코드·이름 등)" value={query} onChange={(e) => (onQuery(e.target.value), setMore(1))} className={`${field} w-56`} />
          {extra}
          {action?.(hit)}
        </div>
      )}
      {rows.length ? (
        <>
          <table className="w-full text-sm">
            <thead>
              <tr>
                {cols.map((c) => (
                  <th key={c} className={th}>
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {shown.map((r, i) => (
                <tr key={i} className="border-t border-line">
                  {cols.map((c) => (
                    <td key={c} className={`${td} font-mono text-xs`}>
                      {show(c, r[c])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {hit.length > shown.length && (
            <button type="button" className={`${btn2} mt-2`} onClick={() => setMore(more + 1)}>
              더 보기 ({shown.length} / {hit.length})
            </button>
          )}
        </>
      ) : (
        <p className="text-sm text-muted-foreground">없음</p>
      )}
    </div>
  );
}

function Detail({ id, admin, onChanged, onDeleted }: { id: number; admin: boolean; onChanged: () => void; onDeleted: () => void }) {
  const { data: b, error, reload } = useApi<Bundle>(`/master-versions/${id}`);
  const [msg, setMsg] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [queries, setQueries] = useState<Record<string, string>>({});
  const [cb, setCb] = useState<CbFilter>(NO_FILTER);
  const [reviewer, setReviewer] = useState("");
  async function importXlsx(file: File | undefined) {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".xlsx")) return setMsg("G1 양식(.xlsx) 파일만 가져올 수 있습니다.");
    const body = new FormData();
    body.append("file", file);
    setNote(null);
    await act(async () => {
      const r = await api<Bundle & { warnings: string[] }>(`/master-versions/${id}/import-xlsx`, { method: "POST", body });
      setNote(["가져왔습니다.", ...r.warnings].join(" "));
    });
  }
  const act = async (fn: () => Promise<unknown>, deleted = false) => {
    setMsg(null);
    try {
      await fn();
      if (deleted) return onDeleted();
      reload();
      onChanged();
    } catch (e) {
      const missing = e instanceof ApiError && e.code === "MASTER_INCOMPLETE" ? e.message.replace(/^Missing: /, "") : null;
      setMsg(missing ? `확정하려면 다음 값을 먼저 입력해야 합니다: ${missing}` : errText(e));
    }
  };
  if (error) return <Err text={errText(error)} />;
  if (!b) return <Loading />;
  return (
    <section aria-label={`버전 ${b.version_code}`} className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-semibold text-foreground">
          {b.version_code} <span className="text-sm font-normal text-muted-foreground">적용 {b.effective_from}</span>
        </h2>
        <StatusBadge status={b.status} />
        {admin && b.status === "DRAFT" && (
          <>
            <button type="button" className={btnPrimary} onClick={() => void act(() => api(`/master-versions/${id}/activate`, { method: "POST" }))}>
              활성화(확정)
            </button>
            <label className={`${btn2} cursor-pointer`}>
              G1 엑셀 가져오기
              <input
                type="file"
                accept=".xlsx"
                className="sr-only"
                onChange={(e) => {
                  void importXlsx(e.target.files?.[0]);
                  e.target.value = "";
                }}
              />
            </label>
            <button
              type="button"
              className={btn2}
              title="3D 견적용 드릴·탭·카운터보어·접시·밀링·선반 규칙과 단가(업계 참고값)를 추가합니다. 이미 있는 코드는 그대로 둡니다"
              onClick={() =>
                void act(async () => {
                  const r = await api<{ added: string[] }>(`/master-versions/${id}/machining-defaults`, { method: "POST" });
                  setNote(r.added.length ? `기본 가공 규칙 ${r.added.length}개를 추가했습니다 (${r.added.join(", ")}). 단가·공수는 참고값이니 사내 값으로 바꾸세요.` : "기본 가공 규칙이 이미 모두 있습니다.");
                })
              }
            >
              기본 가공 규칙 넣기
            </button>
            <button type="button" className={btnDanger} onClick={() => void act(() => api(`/master-versions/${id}`, { method: "DELETE" }), true)}>
              초안 삭제
            </button>
          </>
        )}
      </div>
      {b.status === "ACTIVE" && <p className="text-sm text-muted-foreground">확정된 버전은 바꿀 수 없습니다. 변경하려면 이 버전을 복사해 새 버전을 만드세요.</p>}
      {admin && b.status === "DRAFT" && <p className="text-sm text-muted-foreground">G1 엑셀을 가져오면 이 초안의 내용 전체가 엑셀 1~6·9~14 시트 내용으로 바뀝니다.</p>}
      {note && (
        <p role="status" className="text-sm text-[var(--color-success-text)]">
          {note}
        </p>
      )}
      <div className="whitespace-pre-line">
        <Err text={msg} />
      </div>
      <div>
        <h3 className="mb-1 font-semibold text-foreground">원가 비율</h3>
        {b.cost_ratios ? (
          <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm md:grid-cols-4">
            {Object.entries(b.cost_ratios).map(([k, v]) => (
              <div key={k}>
                <dt className="text-muted-foreground">{k}</dt>
                <dd className="font-mono">{show(k, v)}</dd>
              </div>
            ))}
          </dl>
        ) : (
          <p className="text-sm text-muted-foreground">미입력</p>
        )}
      </div>
      {TABLES.map(([key, title, cols]) => {
        const rows = b[key] as Rows;
        const common = { title, cols, query: queries[key] ?? "", onQuery: (q: string) => setQueries({ ...queries, [key]: q }) };
        if (key !== "costbook_items") return <DataTable key={key} rows={rows} {...common} />;
        // 표준품셈 항목: filter by text, 분류, 검수 and original page; mark the shown ones as checked
        const items = rows as unknown as (CbItem & Record<string, unknown>)[];
        const hit = filterCostbook(items, cb);
        return (
          <DataTable
            key={key}
            rows={hit as Rows}
            total={items.length}
            {...common}
            title={`${title}${hit.length === items.length ? "" : ` (필터 ${hit.length} / ${items.length})`}`}
            extra={
              <>
                <select aria-label="분류" value={cb.category} onChange={(e) => setCb({ ...cb, category: e.target.value })} className={`${field} w-32`}>
                  <option value="">분류 전체</option>
                  {categories(items).map((c) => (
                    <option key={c}>{c}</option>
                  ))}
                </select>
                <select aria-label="검수 상태" value={cb.review} onChange={(e) => setCb({ ...cb, review: e.target.value as CbFilter["review"] })} className={`${field} w-32`}>
                  <option value="ALL">검수 전체</option>
                  <option value="TODO">검수 전</option>
                  <option value="DONE">검수 완료</option>
                </select>
                <input aria-label="원문 쪽 시작" type="number" min={1} placeholder="쪽 부터" onChange={(e) => setCb({ ...cb, pageFrom: e.target.value ? Number(e.target.value) : undefined })} className={`${field} w-24`} />
                <input aria-label="원문 쪽 끝" type="number" min={1} placeholder="쪽 까지" onChange={(e) => setCb({ ...cb, pageTo: e.target.value ? Number(e.target.value) : undefined })} className={`${field} w-24`} />
              </>
            }
            action={(shown) =>
              admin &&
              b.status === "DRAFT" && (
                <>
                  <input aria-label="검수자" placeholder="검수자 이름" value={reviewer} onChange={(e) => setReviewer(e.target.value)} className={`${field} w-32`} />
                  <button
                    type="button"
                    className={btn2}
                    disabled={!reviewer.trim() || !shown.length}
                    title="지금 보이는(검색·필터된) 품셈 항목 전체를 원문과 대조했다고 표시합니다"
                    onClick={() =>
                      void act(async () => {
                        const r = await api<{ reviewed: number }>(`/master-versions/${id}/costbook-review`, { method: "POST", json: { reviewer: reviewer.trim(), item_codes: shown.map((r) => r.item_code) } });
                        setNote(`${r.reviewed}건을 ${reviewer.trim()} 검수로 표시했습니다.`);
                      })
                    }
                  >
                    보이는 {shown.length}건 검수 완료 표시
                  </button>
                </>
              )
            }
          />
        );
      })}
    </section>
  );
}

// P6-2: a 표준품셈 PDF's tables -> a G1 workbook of draft 품셈 rows (nothing is saved; a person checks it, then imports it)
function CostbookDraft() {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const body = new FormData(e.currentTarget);
    const file = body.get("file") as File;
    if (!file.name.toLowerCase().endsWith(".pdf")) return setNote({ ok: false, text: "표준품셈 PDF(.pdf) 파일을 선택하세요." });
    for (const k of ["notice", "effective_from"]) if (!body.get(k)) body.delete(k);
    setBusy(true);
    setNote(null);
    try {
      await download("/master-data/costbook-draft", "AX-CAD_품셈초안.xlsx", { method: "POST", body });
      setNote({ ok: true, text: "초안을 내려받았습니다. '추출점검' 시트와 원문 쪽을 확인하고 검수자를 적은 뒤 가져오세요." });
    } catch (err) {
      setNote({ ok: false, text: errText(err) });
    } finally {
      setBusy(false);
    }
  }
  return (
    <details className="rounded-md border border-line p-3">
      <summary className="cursor-pointer text-sm font-medium text-foreground">표준품셈 PDF에서 초안 만들기</summary>
      <form onSubmit={submit} className="mt-3 flex flex-wrap items-end gap-3">
        <Field label="품셈 PDF (100MB 이하)">
          <input name="file" type="file" accept=".pdf" required className={field} />
        </Field>
        <Field label="쪽 (부터)">
          <input name="page_from" type="number" min={1} defaultValue={1} className={`${field} w-24`} />
        </Field>
        <Field label="쪽 (까지, 최대 150쪽)">
          <input name="page_to" type="number" min={1} defaultValue={50} className={`${field} w-24`} />
        </Field>
        <Field label="고시번호 (선택)">
          <input name="notice" placeholder="국토교통부 공고 제2025-1574호" className={field} />
        </Field>
        <Field label="적용일 (선택)">
          <input name="effective_from" type="date" className={field} />
        </Field>
        <button type="submit" disabled={busy} aria-busy={busy} className={btn2}>
          {busy ? "만드는 중…" : "초안 만들기"}
        </button>
      </form>
      <p className="mt-2 text-xs text-muted-foreground">선이 그려진 표만 읽고, 스캔된 쪽은 읽지 않습니다. 투입량은 반드시 원문과 대조하세요(검수자 칸이 빈 품셈은 견적에 경고가 표시됩니다).</p>
      {note && <p className={`mt-2 text-sm ${note.ok ? "text-body" : "text-red-600"}`}>{note.text}</p>}
    </details>
  );
}

// P6-4: what changed in the 표준품셈 between two versions (the older edition against the newer)
type ResChange = { resource_code: string; resource_name: string | null; resource_type: string; status: string; a_qty: string | null; b_qty: string | null; pct: number | null };
type ItemDiff = { status: string; name: string; spec: string | null; a_code: string | null; b_code: string | null; fields: { field: string; a: string | null; b: string | null }[]; resources: ResChange[] };
type PriceDiff = { resource_code: string; resource_name: string | null; status: string; a_price: string | null; b_price: string | null; pct: number | null };
type Compare = { a: string; b: string; match: string; summary: Record<string, number>; items: ItemDiff[]; prices: PriceDiff[] };
const STATUS = { ADDED: "추가", REMOVED: "삭제", CHANGED: "변경" } as Record<string, string>;
const pct = (v: number | null) => (v == null ? "" : `${v > 0 ? "+" : ""}${v}%`);

function VersionCompare({ versions }: { versions: Version[] }) {
  const [res, setRes] = useState<Compare | null>(null);
  const [status, setStatus] = useState("");
  const [more, setMore] = useState(1);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const read = (f: HTMLFormElement) => {
    const d = new FormData(f);
    return { a: String(d.get("a")), b: String(d.get("b")), match: String(d.get("match")) };
  };
  async function run(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const { a, b, match } = read(e.currentTarget);
    if (!a || !b || a === b) return setErr("서로 다른 두 버전을 고르세요.");
    setBusy(true);
    setErr(null);
    setMore(1);
    try {
      setRes(await api<Compare>(`/master-versions/${a}/compare/${b}${qs({ match })}`));
    } catch (x) {
      setErr(errText(x));
    } finally {
      setBusy(false);
    }
  }
  const shown = res?.items.filter((i) => !status || i.status === status) ?? [];
  return (
    <details className="rounded-md border border-line p-3">
      <summary className="cursor-pointer text-sm font-medium text-foreground">품셈 개정 비교 (두 버전)</summary>
      <form onSubmit={(e) => void run(e)} className="mt-3 flex flex-wrap items-end gap-3">
        <Field label="이전 버전">
          <select name="a" className={field} defaultValue="">
            <option value="" disabled>선택</option>
            {versions.map((v) => (
              <option key={v.version_id} value={v.version_id}>{v.version_code}</option>
            ))}
          </select>
        </Field>
        <Field label="이후 버전">
          <select name="b" className={field} defaultValue="">
            <option value="" disabled>선택</option>
            {versions.map((v) => (
              <option key={v.version_id} value={v.version_id}>{v.version_code}</option>
            ))}
          </select>
        </Field>
        <Field label="항목 대응 기준">
          <select name="match" className={field} defaultValue="code">
            <option value="code">품셈 코드</option>
            <option value="name">품명 + 규격 (판마다 코드가 다를 때)</option>
          </select>
        </Field>
        <button type="submit" disabled={busy} aria-busy={busy} className={btn2}>{busy ? "비교 중…" : "비교"}</button>
        {res && (
          <button type="button" className={btn2} onClick={(e) => {
            const f = e.currentTarget.form!;
            const { a, b, match } = read(f);
            void download(`/master-versions/${a}/compare/${b}${qs({ match, format: "xlsx" })}`, "AX-CAD_품셈개정비교.xlsx").catch((x) => setErr(errText(x)));
          }}>엑셀로 내려받기</button>
        )}
      </form>
      <Err text={err} />
      {res && (
        <div className="mt-3 space-y-3 text-sm">
          <p>
            <b>{res.a}</b> → <b>{res.b}</b>: 추가 {res.summary.items_added} · 삭제 {res.summary.items_removed} · 변경 {res.summary.items_changed} · 변경 없음 {res.summary.items_same} · 단가 변경 {res.summary.prices_changed}
          </p>
          <div className="flex items-center gap-2">
            <select aria-label="상태" value={status} onChange={(e) => (setStatus(e.target.value), setMore(1))} className={`${field} w-32`}>
              <option value="">전체</option>
              <option value="ADDED">추가</option>
              <option value="REMOVED">삭제</option>
              <option value="CHANGED">변경</option>
            </select>
            <span className="text-muted-foreground">{shown.length}건</span>
          </div>
          <ul className="space-y-1">
            {shown.slice(0, PAGE_ROWS * more).map((i, n) => (
              <li key={n} className="rounded border border-line px-2 py-1">
                <details>
                  <summary className="cursor-pointer">
                    <span className="mr-2 font-semibold">{STATUS[i.status]}</span>
                    {i.name}{i.spec ? ` (${i.spec})` : ""} <span className="font-mono text-xs text-muted-foreground">{i.a_code ?? "—"} → {i.b_code ?? "—"}</span>
                    {i.resources.length ? ` · 투입량 ${i.resources.length}건` : ""}
                  </summary>
                  <ul className="mt-1 space-y-0.5 pl-4 font-mono text-xs">
                    {i.fields.map((f) => (
                      <li key={f.field}>{f.field}: {f.a ?? "—"} → {f.b ?? "—"}</li>
                    ))}
                    {i.resources.map((r) => (
                      <li key={r.resource_code}>{STATUS[r.status]} {r.resource_name ?? r.resource_code} ({r.resource_type}): {r.a_qty ?? "—"} → {r.b_qty ?? "—"} {pct(r.pct)}</li>
                    ))}
                  </ul>
                </details>
              </li>
            ))}
          </ul>
          {shown.length > PAGE_ROWS * more && <button type="button" className={btn2} onClick={() => setMore(more + 1)}>더 보기</button>}
          {res.prices.length > 0 && (
            <div className="overflow-x-auto">
              <h4 className="mb-1 font-semibold">자원 단가 변경 {res.prices.length}건</h4>
              <table className="w-full text-xs">
                <thead><tr><th className={th}>자원</th><th className={th}>상태</th><th className={th}>이전</th><th className={th}>이후</th><th className={th}>증감</th></tr></thead>
                <tbody>
                  {res.prices.slice(0, PAGE_ROWS).map((p) => (
                    <tr key={p.resource_code} className="border-t border-line"><td className={td}>{p.resource_name ?? p.resource_code}</td><td className={td}>{STATUS[p.status]}</td><td className={`${td} font-mono`}>{p.a_price ?? "—"}</td><td className={`${td} font-mono`}>{p.b_price ?? "—"}</td><td className={`${td} font-mono`}>{pct(p.pct)}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </details>
  );
}

export default function MasterDataPage() {
  const me = useApi<User>("/auth/me").data;
  const admin = !!me && can(me, "ADMIN");
  const list = useApi<List<Version>>("/master-versions");
  const [sel, setSel] = useState<number | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  async function create(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const copy = Number(f.get("copy_from")) || undefined;
    setMsg(null);
    try {
      const v = await api<Version>("/master-versions", { method: "POST", json: { version_code: String(f.get("version_code")).trim(), effective_from: f.get("effective_from"), copy_from: copy } });
      list.reload();
      setSel(v.version_id);
    } catch (err) {
      setMsg(errText(err));
    }
  }

  return (
    <AppShell title="기준정보">
      {admin && (
        <form onSubmit={create} className="flex flex-wrap items-end gap-3">
          <Field label="버전 코드">
            <input name="version_code" required pattern="[A-Za-z0-9._\-]{1,40}" placeholder="예: 2027-01" className={field} />
          </Field>
          <Field label="적용 시작일">
            <input name="effective_from" type="date" required className={field} />
          </Field>
          <Field label="복사할 버전 (선택)">
            <select name="copy_from" defaultValue="" className={field}>
              <option value="">빈 버전</option>
              {list.data?.items.map((v) => (
                <option key={v.version_id} value={v.version_id}>
                  {v.version_code}
                </option>
              ))}
            </select>
          </Field>
          <button type="submit" className={btnPrimary}>
            새 버전
          </button>
          <button type="button" className={btn2} onClick={() => void download("/master-data/g1-template.xlsx", "AX-CAD_G1_input.xlsx").catch((err) => setMsg(errText(err)))}>
            빈 G1 양식 내려받기
          </button>
        </form>
      )}
      <CostbookDraft />
      {list.data && list.data.items.length > 1 && <VersionCompare versions={list.data.items} />}
      <Err text={msg || (list.error && errText(list.error))} />
      {!list.data ? (
        !list.error && <Loading />
      ) : !list.data.items.length ? (
        <p className="text-sm text-muted-foreground">기준정보 버전이 없습니다. 견적 메트릭은 기본 규칙으로 계산됩니다.</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th className={th}>버전</th>
              <th className={th}>적용 시작일</th>
              <th className={th}>상태</th>
              <th className={th}>확정 일시</th>
              <th className={th} />
            </tr>
          </thead>
          <tbody>
            {list.data.items.map((v) => (
              <tr key={v.version_id} className="border-t border-line">
                <td className={`${td} font-mono`}>{v.version_code}</td>
                <td className={td}>{v.effective_from}</td>
                <td className={td}>
                  <StatusBadge status={v.status} />
                </td>
                <td className={td}>{v.activated_at ? fmt(v.activated_at) : "—"}</td>
                <td className={td}>
                  <button type="button" aria-pressed={sel === v.version_id} onClick={() => setSel(v.version_id)} className={btn2}>
                    보기
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {sel && (
        <Detail
          key={sel}
          id={sel}
          admin={admin}
          onChanged={list.reload}
          onDeleted={() => {
            setSel(null);
            list.reload();
          }}
        />
      )}
    </AppShell>
  );
}
