"use client";

import Link from "next/link";
import { use, useState } from "react";
import AppShell, { useMe } from "@/components/shell/AppShell";
import { btn2, btnPrimary, card, DiffSummary, Err, field, Field, fmt, link, Loading, StatusBadge, td, th, type Diff } from "@/components/ui";
import { api, can, download, errText, type Doc, type List } from "@/lib/api";
import { useApi } from "@/lib/useApi";

type Rev = { revision_id: number; revision_no: number; parent_revision_id: number | null; note: string | null; created_by_name: string; created_at: string; is_current: boolean; entity_count: number };
type Member = { user_id: number; user_name: string; roles: string[] };
type Saved = { revision_no: number; entity_count: number; warnings: string[] };
type Msg = { ok: boolean; text: string };

const MAX_BYTES = 50 * 1024 * 1024;

const Note = ({ m }: { m: Msg | null }) =>
  m &&
  (m.ok ? (
    <p role="status" className="text-sm text-emerald-700">
      {m.text}
    </p>
  ) : (
    <Err text={m.text} />
  ));

function Upload({ docId, onDone }: { docId: string; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg | null>(null);
  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const f = new FormData(form);
    const file = f.get("file") as File;
    if (!file.name) return setMsg({ ok: false, text: "DXF 파일을 선택하세요." });
    if (!file.name.toLowerCase().endsWith(".dxf")) return setMsg({ ok: false, text: ".dxf 파일만 업로드할 수 있습니다." });
    if (file.size > MAX_BYTES) return setMsg({ ok: false, text: "파일 크기는 50MB 이하여야 합니다." });
    if (!f.get("note")) f.delete("note");
    setBusy(true);
    setMsg(null);
    try {
      // ponytail: fetch has no upload progress, busy state only; switch to XHR if a progress bar is needed
      const d = await api<Saved>(`/documents/${docId}/dxf`, { method: "POST", body: f });
      const noChange = d.warnings.includes("NO_CHANGE");
      const rest = d.warnings.filter((w) => w !== "NO_CHANGE");
      setMsg({
        ok: true,
        text: (noChange ? "변경 사항이 없어 새 리비전이 만들어지지 않았습니다." : `리비전 ${d.revision_no} 업로드됨 · 엔티티 ${d.entity_count}`) + (rest.length ? ` · 경고: ${rest.join(", ")}` : ""),
      });
      form.reset();
      onDone();
    } catch (err) {
      setMsg({ ok: false, text: errText(err) });
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={submit} className={`${card} space-y-3`}>
      <h2 className="text-lg font-semibold text-foreground">DXF 업로드</h2>
      <Field label="DXF 파일 (최대 50MB)">
        <input name="file" type="file" accept=".dxf" disabled={busy} className="block w-full text-sm text-body" />
      </Field>
      <Field label="메모 (선택)">
        <input name="note" disabled={busy} className={field} />
      </Field>
      <Note m={msg} />
      <button type="submit" disabled={busy} aria-busy={busy} className={btnPrimary}>
        {busy ? "업로드 중..." : "업로드"}
      </button>
    </form>
  );
}

function Compare({ revs }: { revs: Rev[] }) {
  const [diff, setDiff] = useState<Diff | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setDiff(null);
    setError(null);
    if (!f.get("a") || !f.get("b") || f.get("a") === f.get("b")) return setError("서로 다른 두 리비전을 선택하세요.");
    try {
      setDiff(await api<Diff>(`/revisions/${f.get("a")}/diff/${f.get("b")}`));
    } catch (err) {
      setError(errText(err));
    }
  }
  const sel = (name: string, label: string) => (
    <Field label={label}>
      <select name={name} defaultValue="" className={field}>
        <option value="">선택</option>
        {revs.map((r) => (
          <option key={r.revision_id} value={r.revision_id}>
            Rev {r.revision_no}
          </option>
        ))}
      </select>
    </Field>
  );
  return (
    <section aria-labelledby="cmp" className={`${card} space-y-3`}>
      <h2 id="cmp" className="text-lg font-semibold text-foreground">
        리비전 비교
      </h2>
      <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
        {sel("a", "A (기준)")}
        {sel("b", "B (비교)")}
        <button type="submit" className={btn2}>
          비교
        </button>
      </form>
      <Err text={error} />
      {diff && <DiffSummary d={diff} />}
    </section>
  );
}

function Approval({ doc, hasRev, onDone }: { doc: Doc; hasRev: boolean; onDone: () => void }) {
  const me = useMe();
  const members = useApi<List<Member>>(`/projects/${doc.project_id}/members`);
  const [msg, setMsg] = useState<Msg | null>(null);
  const approvers = members.data?.items.filter((m) => m.roles.includes("REVIEWER") && m.user_id !== me.user_id);
  const canRequest = can(me, "DESIGNER") && hasRev && (doc.status === "DRAFT" || doc.status === "REJECTED");

  async function act(fn: () => Promise<unknown>, ok: string) {
    setMsg(null);
    try {
      await fn();
      setMsg({ ok: true, text: ok });
      onDone();
    } catch (err) {
      setMsg({ ok: false, text: errText(err) });
    }
  }
  return (
    <section aria-labelledby="apv" className={`${card} space-y-3`}>
      <h2 id="apv" className="text-lg font-semibold text-foreground">
        승인
      </h2>
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          const f = new FormData(e.currentTarget);
          if (!f.get("approver_id")) return setMsg({ ok: false, text: "승인자를 선택하세요." });
          void act(() => api(`/documents/${doc.document_id}/approvals`, { method: "POST", json: { approver_id: Number(f.get("approver_id")), comment: f.get("comment") || undefined } }), "승인을 요청했습니다.");
        }}
      >
        <Field label="승인자 (프로젝트 검토자)">
          <select name="approver_id" disabled={!canRequest} defaultValue="" className={field}>
            <option value="">선택</option>
            {approvers?.map((m) => (
              <option key={m.user_id} value={m.user_id}>
                {m.user_name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="의견 (선택)">
          <input name="comment" disabled={!canRequest} className={field} />
        </Field>
        <button type="submit" disabled={!canRequest} title={canRequest ? undefined : "초안/반려 상태의 리비전이 있는 도면만 요청할 수 있습니다"} className={btnPrimary}>
          승인 요청
        </button>
      </form>
      {can(me, "REVIEWER") && doc.status === "APPROVED" && (
        <button type="button" onClick={() => void act(() => api(`/documents/${doc.document_id}/release`, { method: "POST" }), "배포했습니다.")} className={btn2}>
          배포
        </button>
      )}
      <Note m={msg} />
    </section>
  );
}

function Detail({ docId }: { docId: string }) {
  const me = useMe();
  const doc = useApi<Doc & { current_revision: { note: string | null; checksum: string } | null }>(`/documents/${docId}`);
  const revs = useApi<List<Rev>>(`/documents/${docId}/revisions`);
  const [dl, setDl] = useState<string | null>(null);
  const d = doc.data;
  const reload = () => {
    doc.reload();
    revs.reload();
  };
  const items = revs.data?.items.toSorted((a, b) => b.revision_no - a.revision_no);

  return (
    <>
      <Err text={doc.error && errText(doc.error)} />
      {!d ? (
        !doc.error && <Loading />
      ) : (
        <>
          <Link href={`/projects/${d.project_id}`} className={link}>
            ← 도면 목록
          </Link>
          <section aria-label="도면 정보" className={`${card} flex flex-wrap items-center gap-x-8 gap-y-2 text-sm`}>
            <span className="font-mono text-base font-semibold text-foreground">{d.doc_no}</span>
            <span>{d.title}</span>
            <span>유형 {d.doc_type}</span>
            <span>현재 Rev {d.current_revision_no ?? "-"}</span>
            <StatusBadge status={d.status} />
            <button
              type="button"
              disabled={!d.current_revision_id}
              onClick={() => {
                setDl(null);
                download(`/revisions/${d.current_revision_id}/dxf`, `${d.doc_no}.dxf`).catch((e) => setDl(errText(e)));
              }}
              className={`${btn2} ml-auto`}
            >
              DXF 다운로드
            </button>
            <Err text={dl} />
          </section>
          {can(me, "DESIGNER") && <Upload docId={docId} onDone={reload} />}
          <section aria-labelledby="rev" className={card}>
            <h2 id="rev" className="mb-2 text-lg font-semibold text-foreground">
              리비전 이력
            </h2>
            <Err text={revs.error && errText(revs.error)} />
            {!items ? (
              !revs.error && <Loading />
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr>
                    <th className={th}>Rev</th>
                    <th className={th}>작성자</th>
                    <th className={th}>일시</th>
                    <th className={th}>메모</th>
                    <th className={th}>엔티티</th>
                    <th className={th}></th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((r) => (
                    <tr key={r.revision_id} className="border-t border-line">
                      <td className={td}>
                        {r.revision_no}
                        {r.is_current && <span className="ml-2 rounded-full bg-[var(--color-primary-light)] px-2 py-0.5 text-xs text-[var(--color-primary)]">현재</span>}
                      </td>
                      <td className={td}>{r.created_by_name}</td>
                      <td className={td}>{fmt(r.created_at)}</td>
                      <td className={td}>{r.note ?? "-"}</td>
                      <td className={td}>{r.entity_count}</td>
                      <td className={td}>
                        <Link href={`/viewer/${r.revision_id}?doc=${docId}`} className={link}>
                          뷰어에서 열기
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
          {items && <Compare revs={items} />}
          <Approval doc={d} hasRev={!!d.current_revision_id} onDone={reload} />
        </>
      )}
    </>
  );
}

export default function DocumentPage({ params }: { params: Promise<{ documentId: string }> }) {
  const { documentId } = use(params);
  return (
    <AppShell title="도면 상세">
      <Detail docId={documentId} />
    </AppShell>
  );
}
