"use client";

import { useMemo, useState } from "react";
import { btn2, btnPrimary, field, Modal } from "@/components/ui";
import { applyCleanup, suggestCleanup, TOL_DEFAULT, TOL_MAX, type CleanEnt, type Suggestion } from "@/lib/cad/cleanup";
import type { Result } from "@/lib/cad/tools";

const KIND: Record<Suggestion["kind"], string> = { GAP: "끝점 이음", OVERLAP: "겹친 선", DUPLICATE: "같은 선" };

/** Backlog B-4: suggestions only. Applying is an ordinary undoable edit; saving makes the new revision. */
export default function CleanupDialog({ ents, onClose, onShow, onApply }: { ents: CleanEnt[]; onClose: () => void; onShow: (handles: string[]) => void; onApply: (r: Result, note: string) => void }) {
  const [tolText, setTolText] = useState(String(TOL_DEFAULT));
  const [tol, setTol] = useState(TOL_DEFAULT);
  const run = useMemo(() => {
    try {
      return { ...suggestCleanup(ents, tol), error: null };
    } catch (e) {
      return { items: [] as Suggestion[], capped: false, error: e instanceof Error ? e.message : String(e) };
    }
  }, [ents, tol]);
  const [off, setOff] = useState<Set<string>>(new Set()); // unticked suggestions (all start ticked)
  const picked = run.items.filter((s) => !off.has(s.id));
  const toggle = (id: string) => setOff((o) => (o.has(id) ? new Set([...o].filter((x) => x !== id)) : new Set(o).add(id)));
  const apply = () => {
    const { result, applied, skipped } = applyCleanup(ents, picked);
    onApply(result, `도면 정리 ${applied}건을 적용했습니다${skipped ? ` (${skipped}건은 다른 정리와 겹쳐 건너뜀 — 다시 검사하세요)` : ""}. 저장하면 새 리비전이 됩니다 (Ctrl+Z로 되돌리기).`);
  };
  return (
    <Modal title="도면 정리 제안" onClose={onClose} wide>
      <div className="space-y-3 text-sm">
        <p className="text-muted-foreground">
          보이는·잠기지 않은 레이어의 선·호·열린 폴리라인에서 끊긴 끝점(허용 간격 안에서 떨어진 끝점)과 겹친 직선을 찾습니다. 도면은 자동으로 바뀌지 않으며, 고른 제안만 적용합니다. 레이어·색·선종류가 다른 선은 합치지 않고, 호는 움직이지 않습니다(선이 호 끝으로 이동).
        </p>
        <form
          className="flex items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            const v = Number(tolText);
            setOff(new Set());
            setTol(Number.isFinite(v) ? v : NaN);
          }}
        >
          <label className="block">
            <span className="mb-1 block font-medium text-foreground">허용 간격 (mm, 최대 {TOL_MAX})</span>
            <input value={tolText} onChange={(e) => setTolText(e.target.value)} inputMode="decimal" className={`${field} w-32`} aria-label="허용 간격 (mm)" />
          </label>
          <button type="submit" className={btn2}>
            다시 검사
          </button>
        </form>
        {run.error ? (
          <p role="alert" className="text-red-600 dark:text-red-400">
            {run.error}
          </p>
        ) : run.items.length === 0 ? (
          <p>정리할 곳이 없습니다.</p>
        ) : (
          <>
            {run.capped && <p className="rounded-md bg-amber-50 px-2 py-1 text-amber-800">⚠ 도면이 커서 일부만 검사했습니다(제안 500건까지). 적용 후 다시 검사하세요.</p>}
            <ul className="max-h-80 space-y-1 overflow-auto" aria-label="정리 제안">
              {run.items.map((s) => (
                <li key={s.id} className="flex items-center gap-2 rounded-md border border-line px-2 py-1">
                  <input type="checkbox" checked={!off.has(s.id)} onChange={() => toggle(s.id)} aria-label={`${KIND[s.kind]} 적용`} />
                  <span className="shrink-0 rounded bg-muted px-1 text-xs">{KIND[s.kind]}</span>
                  <span className="min-w-0 flex-1 truncate">{s.summary}</span>
                  <button type="button" onClick={() => onShow(s.handles)} className="shrink-0 text-[var(--color-primary)] hover:underline focus-visible:outline-2 focus-visible:outline-ring">
                    도면에서 보기
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className={btn2}>
            닫기
          </button>
          <button type="button" onClick={apply} disabled={!picked.length} className={`${btnPrimary} disabled:opacity-50`}>
            선택한 {picked.length}건 적용
          </button>
        </div>
      </div>
    </Modal>
  );
}
