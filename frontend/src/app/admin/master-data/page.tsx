"use client";

import { useState } from "react";
import AppShell from "@/components/shell/AppShell";
import { btn2, btnDanger, btnPrimary, Err, field, Field, fmt, Loading, StatusBadge, td, th } from "@/components/ui";
import { api, ApiError, can, download, errText, type List, type User } from "@/lib/api";
import { useApi } from "@/lib/useApi";

type Version = { version_id: number; version_code: string; effective_from: string; status: "DRAFT" | "ACTIVE"; note: string | null; created_at: string; activated_at: string | null };
type Rows = Record<string, unknown>[];
type Bundle = Version & { materials: Rows; price_items: Rows; process_rules: Rows; mapping_rules: Rows; cost_ratios: Record<string, unknown> | null };

// FN-16 columns shown per table; rates are stored as fractions and shown as %
const TABLES: [keyof Bundle, string, string[]][] = [
  ["materials", "재질", ["material_code", "material_name", "thickness_min_mm", "thickness_max_mm", "density_g_cm3", "unit_price_per_kg", "scrap_rate"]],
  ["price_items", "단가(임률·기계경비·외주)", ["item_code", "item_type", "unit", "unit_price"]],
  ["process_rules", "공정 규칙", ["rule_code", "process_code", "process_name", "input_metric", "formula_text", "params", "labor_item_code", "machine_item_code"]],
  ["mapping_rules", "레이어·표제란 매핑", ["rule_type", "target", "pattern"]],
];
const RATES = ["scrap_rate", "overhead_rate", "admin_rate", "profit_rate", "vat_rate"];
const show = (k: string, v: unknown) => (v == null || v === "" ? "—" : RATES.includes(k) ? `${Number(v) * 100}%` : typeof v === "object" ? JSON.stringify(v) : String(v));

function Detail({ id, admin, onChanged, onDeleted }: { id: number; admin: boolean; onChanged: () => void; onDeleted: () => void }) {
  const { data: b, error, reload } = useApi<Bundle>(`/master-versions/${id}`);
  const [msg, setMsg] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
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
            <button type="button" className={btnDanger} onClick={() => void act(() => api(`/master-versions/${id}`, { method: "DELETE" }), true)}>
              초안 삭제
            </button>
          </>
        )}
      </div>
      {b.status === "ACTIVE" && <p className="text-sm text-muted-foreground">확정된 버전은 바꿀 수 없습니다. 변경하려면 이 버전을 복사해 새 버전을 만드세요.</p>}
      {admin && b.status === "DRAFT" && <p className="text-sm text-muted-foreground">G1 엑셀을 가져오면 이 초안의 내용 전체가 엑셀 1~6 시트 내용으로 바뀝니다.</p>}
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
        return (
          <div key={key} className="overflow-x-auto">
            <h3 className="mb-1 font-semibold text-foreground">
              {title} <span className="text-sm font-normal text-muted-foreground">{rows.length}건</span>
            </h3>
            {rows.length ? (
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
                  {rows.map((r, i) => (
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
            ) : (
              <p className="text-sm text-muted-foreground">없음</p>
            )}
          </div>
        );
      })}
    </section>
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
