"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useMemo, useState } from "react";
import type { Approval } from "@/app/approvals/page";
import CanvasViewport, { type RenderEntity } from "@/components/cad/CanvasViewport";
import AppShell, { useMe } from "@/components/shell/AppShell";
import { btnDanger, btnPrimary, card, DiffSummary, Err, field, Field, fmt, link, Loading, StatusBadge, type Diff } from "@/components/ui";
import { api, errText, type List } from "@/lib/api";
import type { SnapKind } from "@/lib/cad/snap";
import type { Extents } from "@/lib/cad/view";
import { useApi } from "@/lib/useApi";

type Render = { extents: Extents; layers: { name: string; visible: boolean }[]; parent_revision_id: number | string | null; entities: RenderEntity[] };

const NONE = new Set<string>();
const NO_SNAP = new Set<SnapKind>();
const NOOP = () => {};

// Read-only preview: same canvas, no tools, no snapping, picks ignored
function Preview({ r }: { r: Render }) {
  const hidden = useMemo(() => new Set(r.layers.filter((l) => !l.visible).map((l) => l.name)), [r]);
  return (
    <div className="h-96 overflow-hidden rounded-xl border border-line">
      <CanvasViewport entities={r.entities} extents={r.extents} hidden={hidden} onCursor={NOOP} onZoom={NOOP} selected={NONE} preview={[]} onPick={NOOP} snapOn={false} snapKinds={NO_SNAP} gridStep={10} showGrid />
    </div>
  );
}

function Review({ id }: { id: string }) {
  const me = useMe();
  const router = useRouter();
  // ponytail: no single-approval endpoint in the contract, so find it in the list (all statuses)
  const list = useApi<List<Approval>>("/approvals");
  const a = list.data?.items.find((x) => String(x.approval_id) === id);
  const render = useApi<Render>(a ? `/revisions/${a.revision_id}/render` : null);
  const parent = render.data?.parent_revision_id;
  const diff = useApi<Diff>(a && parent ? `/revisions/${parent}/diff/${a.revision_id}` : null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function decide(decision: "APPROVED" | "REJECTED", form: HTMLFormElement) {
    const comment = String(new FormData(form).get("comment")).trim();
    if (decision === "REJECTED" && !comment) return setError("반려 시 의견을 입력하세요.");
    setBusy(true);
    setError(null);
    try {
      await api(`/approvals/${id}/decision`, { method: "POST", json: { decision, comment: comment || undefined } });
      router.replace("/approvals");
    } catch (err) {
      setError(errText(err));
      setBusy(false);
    }
  }

  if (list.error) return <Err text={errText(list.error)} />;
  if (!list.data) return <Loading />;
  if (!a) return <p className="text-sm text-muted-foreground">승인 요청을 찾을 수 없습니다.</p>;
  const mine = a.status === "PENDING" && a.approver_id === me.user_id;
  return (
    <>
      <Link href="/approvals" className={link}>
        ← 승인함
      </Link>
      <section aria-label="요청 요약" className={`${card} flex flex-wrap items-center gap-x-8 gap-y-2 text-sm`}>
        <span className="font-mono text-base font-semibold text-foreground">{a.doc_no}</span>
        <span>{a.title}</span>
        <span>Rev {a.revision_no}</span>
        <span>
          요청자 {a.requested_by_name} · {fmt(a.created_at)}
        </span>
        <StatusBadge status={a.status} />
        {a.comment && <span>의견: {a.comment}</span>}
      </section>
      <Err text={render.error && errText(render.error)} />
      {render.data ? <Preview r={render.data} /> : !render.error && <Loading />}
      {parent && (
        <section aria-labelledby="df" className={card}>
          <h2 id="df" className="mb-2 text-lg font-semibold text-foreground">
            이전 리비전 대비 변경
          </h2>
          {diff.data ? <DiffSummary d={diff.data} /> : <Loading />}
        </section>
      )}
      {mine && (
        <form onSubmit={(e) => e.preventDefault()} className={`${card} space-y-3`}>
          <Field label="의견 (반려 시 필수)">
            <input name="comment" className={field} />
          </Field>
          <Err text={error} />
          <div className="flex gap-2">
            <button type="button" disabled={busy} onClick={(e) => void decide("APPROVED", e.currentTarget.form!)} className={btnPrimary}>
              승인
            </button>
            <button type="button" disabled={busy} onClick={(e) => void decide("REJECTED", e.currentTarget.form!)} className={btnDanger}>
              반려
            </button>
          </div>
        </form>
      )}
    </>
  );
}

export default function ApprovalPage({ params }: { params: Promise<{ approvalId: string }> }) {
  const { approvalId } = use(params);
  return (
    <AppShell title="승인 검토">
      <Review id={approvalId} />
    </AppShell>
  );
}
