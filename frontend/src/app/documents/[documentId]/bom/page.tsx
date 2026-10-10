"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
import AppShell, { useMe } from "@/components/shell/AppShell";
import { btn2, btnPrimary, card, Err, field, fmt, link, Loading, StatusBadge, td, th } from "@/components/ui";
import { api, can, download, errText, type Doc, type List } from "@/lib/api";
import { PART_NO_RE, type Bom, type BomItem, type BomSummary, type Job } from "@/lib/bom";
import { useApi } from "@/lib/useApi";

// SCR-14: BOM from the current drawing revision (blocks) or the 3D model (parts)
function MapCell({ item, canEdit, onSaved }: { item: BomItem; canEdit: boolean; onSaved: (b: Bom) => void }) {
  const [value, setValue] = useState(item.part_no ?? "");
  const [error, setError] = useState<string | null>(null);
  if (!canEdit) return <span className="font-mono">{item.part_no ?? "-"}</span>;
  const ok = PART_NO_RE.test(value.trim());
  return (
    <form
      className="flex items-center gap-1"
      onSubmit={(e) => {
        e.preventDefault();
        setError(null);
        api<Bom>(`/bom-items/${item.bom_item_id}`, { method: "PATCH", json: { part_no: value.trim() } }).then(onSaved, (err) => setError(errText(err)));
      }}
    >
      <input aria-label={`${item.source_name} 품번`} value={value} onChange={(e) => setValue(e.target.value)} maxLength={64} className={`${field.replace("w-full", "w-40")} font-mono`} placeholder="품번 입력" />
      <button type="submit" disabled={!ok || value.trim() === item.part_no} className={`${btn2} whitespace-nowrap`}>
        저장
      </button>
      {error && <span className="text-xs text-red-700">{error}</span>}
    </form>
  );
}

// FN-24: send the BOM of an approved drawing to the ERP; one transfer per document + revision
function ErpPanel({ bom, approved }: { bom: Bom; approved: boolean }) {
  const me = useMe();
  const jobs = useApi<List<Job>>(can(me, "MANUFACTURING") ? `/integration-jobs?bom_id=${bom.bom_id}` : null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const job = jobs.data?.items[0];
  const live = job?.status === "PENDING" || job?.status === "RUNNING";
  useEffect(() => {
    if (!live) return;
    const t = setInterval(() => jobs.reload(), 2000); // retries back off in the background
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live]);
  if (!can(me, "MANUFACTURING")) return null;
  const reason = !approved ? "승인(또는 배포)된 도면만 전송할 수 있습니다" : bom.unmapped ? `미매핑 품목 ${bom.unmapped}건의 품번을 먼저 지정하세요` : null;
  async function send() {
    setBusy(true);
    setMsg(null);
    try {
      const j = await api<Job>(`/boms/${bom.bom_id}/erp`, { method: "POST" });
      if (j.duplicate) setMsg("이미 전송한 도면·리비전입니다. 기존 작업을 표시합니다.");
      jobs.reload();
    } catch (e) {
      setMsg(errText(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="flex flex-wrap items-center gap-3 border-t border-line pt-3 text-sm" aria-label="ERP 전송">
      <button type="button" disabled={busy || !!reason || !!job} onClick={() => void send()} title={reason ?? undefined} className={btnPrimary}>
        ERP 전송
      </button>
      {job ? (
        <span className="flex items-center gap-2">
          <StatusBadge status={job.status} /> 시도 {job.attempt_count}회 · {fmt(job.updated_at)}
          {job.last_error && <span className="text-red-700">{job.last_error}</span>}
          <Link href="/admin/integrations" className={link}>
            연동 작업 →
          </Link>
        </span>
      ) : (
        reason && <span className="text-muted-foreground">{reason}</span>
      )}
      {msg && <span className="text-amber-700">{msg}</span>}
    </div>
  );
}

function Detail({ docId }: { docId: string }) {
  const me = useMe();
  const doc = useApi<Doc>(`/documents/${docId}`);
  const list = useApi<List<BomSummary>>(`/documents/${docId}/boms`);
  const [bom, setBom] = useState<Bom | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const canEdit = can(me, "DESIGNER", "MANUFACTURING");
  const latest = list.data?.items[0]?.bom_id;

  useEffect(() => {
    if (latest && !bom) api<Bom>(`/boms/${latest}`).then(setBom, (e) => setMsg(errText(e)));
  }, [latest, bom]);

  async function generate(source: "REVISION" | "MODEL") {
    setBusy(true);
    setMsg(null);
    try {
      setBom(await api<Bom>(`/documents/${docId}/boms`, { method: "POST", json: { source } }));
      list.reload();
    } catch (e) {
      setMsg(errText(e));
    } finally {
      setBusy(false);
    }
  }
  const d = doc.data;
  return (
    <>
      <Err text={(doc.error && errText(doc.error)) || (list.error && errText(list.error)) || msg} />
      {!d ? (
        !doc.error && <Loading />
      ) : (
        <>
          <Link href={`/documents/${docId}`} className={link}>
            ← {d.doc_no} {d.title}
          </Link>
          <div className="flex flex-wrap items-center gap-2">
            {canEdit && (
              <>
                <button type="button" disabled={busy || !d.current_revision_id} onClick={() => void generate("REVISION")} className={btnPrimary}>
                  {busy ? "생성 중..." : `도면(Rev ${d.current_revision_no ?? "-"})에서 생성`}
                </button>
                <button type="button" disabled={busy} onClick={() => void generate("MODEL")} className={btn2}>
                  3D 모델에서 생성
                </button>
              </>
            )}
            {list.data && list.data.items.length > 1 && (
              <select aria-label="BOM 이력" value={bom?.bom_id ?? ""} onChange={(e) => api<Bom>(`/boms/${e.target.value}`).then(setBom, (err) => setMsg(errText(err)))} className={`${field} w-auto`}>
                {list.data.items.map((b) => (
                  <option key={b.bom_id} value={b.bom_id}>
                    {b.bom_no} · {b.source_type === "DXF_BLOCK" ? "도면" : "3D"} · {fmt(b.created_at)}
                  </option>
                ))}
              </select>
            )}
          </div>
          {!bom ? (
            list.data && list.data.items.length === 0 && <p className="text-sm text-muted-foreground">아직 BOM이 없습니다. 도면이나 3D 모델에서 생성하세요.</p>
          ) : (
            <section aria-label="BOM" className={`${card} space-y-3`}>
              <div className="flex flex-wrap items-center gap-3 text-sm">
                <span className="font-mono font-semibold">{bom.bom_no}</span>
                <span className="text-muted-foreground">
                  {bom.source_type === "DXF_BLOCK" ? "도면 블록" : "3D 부품"} · {fmt(bom.created_at)} · 품목 {bom.items.length} · 미매핑 <b className={bom.unmapped ? "text-red-700" : ""}>{bom.unmapped}</b>
                </span>
                <div className="ml-auto flex gap-2">
                  {(["csv", "json"] as const).map((f) => (
                    <button key={f} type="button" onClick={() => download(`/boms/${bom.bom_id}/export?format=${f}`, `${bom.bom_no}.${f}`).catch((e) => setMsg(errText(e)))} className={btn2}>
                      {f.toUpperCase()}
                    </button>
                  ))}
                </div>
              </div>
              {bom.warnings.length > 0 && <p className="text-xs text-amber-700">⚠ {bom.warnings.join(", ")}</p>}
              <ErpPanel key={bom.bom_id} bom={bom} approved={(d.status === "APPROVED" || d.status === "RELEASED") && (bom.revision_id === null || bom.revision_id === d.current_revision_id)} />
              <div className="overflow-x-auto">
                <table className="w-full text-sm" aria-label="BOM 품목">
                  <thead>
                    <tr>
                      {["No", "품번", "품명", "수량", "단위", "레벨", "상태", "원천"].map((h) => (
                        <th key={h} className={`${th} ${h === "수량" ? "text-right" : ""}`}>
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {bom.items.map((it) => (
                      <tr key={it.bom_item_id} className="border-t border-line">
                        <td className={td}>{it.item_no}</td>
                        <td className={td}>
                          <MapCell key={`${it.bom_item_id}-${it.part_no}`} item={it} canEdit={canEdit} onSaved={setBom} />
                        </td>
                        <td className={td}>{it.part_name}</td>
                        <td className={`${td} text-right font-mono`}>{it.qty.toLocaleString("ko-KR")}</td>
                        <td className={td}>{it.unit}</td>
                        <td className={td}>{it.level}</td>
                        <td className={td}>
                          <StatusBadge status={it.mapping_status} />
                        </td>
                        <td className={`${td} font-mono text-xs text-muted-foreground`}>{it.source_name}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </>
      )}
    </>
  );
}

export default function BomPage({ params }: { params: Promise<{ documentId: string }> }) {
  const { documentId } = use(params);
  return (
    <AppShell title="BOM">
      <Detail docId={documentId} />
    </AppShell>
  );
}
