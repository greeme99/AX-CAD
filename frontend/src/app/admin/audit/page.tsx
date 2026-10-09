"use client";

import { useState } from "react";
import AppShell from "@/components/shell/AppShell";
import { btnPrimary, Err, field, Field, fmt, Loading, td, th } from "@/components/ui";
import { errText, qs, type List } from "@/lib/api";
import { useApi } from "@/lib/useApi";

type Log = { audit_log_id: number; object_type: string; object_id: string; action: string; old_value: unknown; new_value: unknown; user_id: number | null; created_at: string };

const Json = ({ label, v }: { label: string; v: unknown }) =>
  v == null ? null : (
    <details>
      <summary className="cursor-pointer">{label}</summary>
      <pre className="max-w-md overflow-auto rounded bg-muted p-2 font-mono text-xs">{JSON.stringify(v, null, 2)}</pre>
    </details>
  );

export default function AuditPage() {
  // applied filters; the form only commits on submit so each keystroke does not refetch
  const [f, setF] = useState({ object_type: "", object_id: "", limit: "100" });
  const { data, error } = useApi<List<Log>>("/audit-logs" + qs(f));
  return (
    <AppShell title="감사 로그">
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          const d = new FormData(e.currentTarget);
          setF({ object_type: String(d.get("object_type")).trim(), object_id: String(d.get("object_id")).trim(), limit: String(d.get("limit")) });
        }}
      >
        <Field label="객체 유형">
          <input name="object_type" defaultValue={f.object_type} placeholder="예: document" className={field} />
        </Field>
        <Field label="객체 ID">
          <input name="object_id" defaultValue={f.object_id} className={field} />
        </Field>
        <Field label="건수 (1~500)">
          <input name="limit" type="number" min={1} max={500} defaultValue={f.limit} className={field} />
        </Field>
        <button type="submit" className={btnPrimary}>
          조회
        </button>
      </form>
      <Err text={error && errText(error)} />
      {!data ? (
        !error && <Loading />
      ) : (
        <>
          <p className="text-sm text-muted-foreground">
            전체 {data.total}건 중 {data.items.length}건 표시
          </p>
          <table className="w-full text-sm">
            <thead>
              <tr>
                <th className={th}>일시</th>
                <th className={th}>객체</th>
                <th className={th}>동작</th>
                <th className={th}>사용자</th>
                <th className={th}>변경 내용</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((l) => (
                <tr key={l.audit_log_id} className="border-t border-line align-top">
                  <td className={td}>{fmt(l.created_at)}</td>
                  <td className={`${td} font-mono`}>
                    {l.object_type}#{l.object_id}
                  </td>
                  <td className={td}>{l.action}</td>
                  <td className={td}>{l.user_id ?? "-"}</td>
                  <td className={td}>
                    <Json label="이전 값" v={l.old_value} />
                    <Json label="이후 값" v={l.new_value} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </AppShell>
  );
}
