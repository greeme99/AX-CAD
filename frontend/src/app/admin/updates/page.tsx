"use client";

import { useEffect, useState } from "react";
import AppShell from "@/components/shell/AppShell";
import { btnPrimary, Err, field, Loading } from "@/components/ui";
import { api, errText } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { GRADE, outcome, type Release } from "@/lib/release";

/** Track 8 P5: this PC's release (R1.1 …, update/upgrade, change notes) and applying a signed
 * patch. The app restarts during the update; the page waits for it and shows the outcome. */
export default function UpdatesPage() {
  const { data, error, reload } = useApi<Release>("/system/release");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [waiting, setWaiting] = useState<Release | null>(null); // the patch being applied

  useEffect(() => {
    if (!waiting) return;
    const t = setInterval(async () => {
      try {
        const r = await api<Release>("/system/release");
        if (r.last_update?.status && r.last_update.status !== "RUNNING") {
          setWaiting(null);
          reload();
        }
      } catch {
        // the app is restarting: keep waiting
      }
    }, 3000);
    return () => clearInterval(t);
  }, [waiting, reload]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setErr(null);
    try {
      const body = new FormData();
      body.append("file", file);
      setWaiting(await api<Release>("/system/updates", { method: "POST", body }));
    } catch (e2) {
      setErr(errText(e2));
    } finally {
      setBusy(false);
    }
  }

  const last = outcome(data?.last_update);
  return (
    <AppShell title="업데이트">
      <Err text={error && errText(error)} />
      {!data ? (
        !error && <Loading />
      ) : !data.installed ? (
        <p className="text-sm text-muted-foreground">PC 설치본이 아니라 버전 정보와 업데이트가 없습니다.</p>
      ) : (
        <div className="max-w-2xl space-y-6 text-sm">
          <section aria-label="현재 버전" className="space-y-2">
            <h2 className="text-lg font-semibold text-foreground">
              {data.label} <span className="text-sm font-normal text-muted-foreground">{data.grade ? GRADE[data.grade] : ""} · {data.version}{data.released_at ? ` · ${data.released_at}` : ""}</span>
            </h2>
            {data.notes.length > 0 && (
              <ul className="list-disc pl-5">
                {data.notes.map((n, i) => (
                  <li key={i}>{n}</li>
                ))}
              </ul>
            )}
            {last && <p className={last.ok ? "text-emerald-700" : "text-amber-800"}>{last.text}</p>}
          </section>
          {waiting ? (
            <p role="status" className="rounded-md bg-muted px-3 py-2">
              {waiting.label}({waiting.grade ? GRADE[waiting.grade] : ""}) 적용 중입니다. 백업 → 교체 → DB 갱신 → 재시작 순서로 진행되며 몇 분 걸릴 수 있습니다. 이 창을 닫지 마세요.
            </p>
          ) : data.can_update ? (
            <form onSubmit={submit} aria-label="패치 적용" className="space-y-2">
              <p className="text-muted-foreground">
                받은 서명 패치 파일(AX-CAD-버전-patch.zip)을 고르세요. 서명·버전 순서·등급 규칙을 먼저 확인하고, 적용 전에 데이터를 자동 백업합니다. 실패하면 이전 버전으로 되돌립니다.
              </p>
              <input type="file" accept=".zip" aria-label="패치 파일" onChange={(e) => setFile(e.target.files?.[0] ?? null)} className={field} />
              <button type="submit" disabled={!file || busy} className={btnPrimary}>
                {busy ? "확인 중..." : "패치 적용"}
              </button>
              <Err text={err} />
            </form>
          ) : null}
        </div>
      )}
    </AppShell>
  );
}
