"use client";

import { useState } from "react";
import AppShell from "@/components/shell/AppShell";
import { btn2, Err, fmt, Loading, StatusBadge, td, th } from "@/components/ui";
import { api, errText, type List } from "@/lib/api";
import type { Job } from "@/lib/bom";
import { useApi } from "@/lib/useApi";

const FILTERS = ["", "FAILED", "RUNNING", "PENDING", "SUCCESS"] as const;

// SCR-17: ERP transfer jobs, failures first to hand, manual retry
export default function IntegrationsPage() {
  const [status, setStatus] = useState<(typeof FILTERS)[number]>("");
  const jobs = useApi<List<Job>>(`/integration-jobs${status ? `?status=${status}` : ""}`);
  const [msg, setMsg] = useState<string | null>(null);
  const retry = (id: number) => {
    setMsg(null);
    api<Job>(`/integration-jobs/${id}/retry`, { method: "POST" }).then(() => jobs.reload(), (e) => setMsg(errText(e)));
  };
  return (
    <AppShell title="연동 작업">
      <div className="flex flex-wrap gap-2" role="group" aria-label="상태 필터">
        {FILTERS.map((f) => (
          <button key={f} type="button" aria-pressed={status === f} onClick={() => setStatus(f)} className={`${btn2} ${status === f ? "bg-hover" : ""}`}>
            {f ? <StatusBadge status={f} /> : "전체"}
          </button>
        ))}
        <button type="button" onClick={() => jobs.reload()} className={`${btn2} ml-auto`}>
          새로고침
        </button>
      </div>
      <Err text={(jobs.error && errText(jobs.error)) || msg} />
      {!jobs.data ? (
        !jobs.error && <Loading />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm" aria-label="연동 작업">
            <thead>
              <tr>
                {["#", "대상", "멱등 키", "상태", "시도", "ERP 응답", "갱신", ""].map((h) => (
                  <th key={h} className={th}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {jobs.data.items.map((j) => (
                <tr key={j.job_id} className="border-t border-line">
                  <td className={td}>{j.job_id}</td>
                  <td className={td}>ERP · BOM</td>
                  <td className={`${td} font-mono text-xs`}>{j.idempotency_key}</td>
                  <td className={td}>
                    <StatusBadge status={j.status} />
                  </td>
                  <td className={td}>{j.attempt_count}/3</td>
                  <td className={`${td} max-w-xs truncate text-xs`} title={j.response_payload ? JSON.stringify(j.response_payload.body) : undefined}>
                    {j.last_error ? <span className="text-red-700">{j.last_error}</span> : j.response_payload ? `HTTP ${j.response_payload.status} ${JSON.stringify(j.response_payload.body)}` : "-"}
                  </td>
                  <td className={td}>{fmt(j.updated_at)}</td>
                  <td className={td}>
                    {j.status === "FAILED" && (
                      <button type="button" onClick={() => retry(j.job_id)} className={btn2}>
                        재시도
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {jobs.data.items.length === 0 && (
                <tr>
                  <td colSpan={8} className={`${td} text-muted-foreground`}>
                    연동 작업이 없습니다.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </AppShell>
  );
}
