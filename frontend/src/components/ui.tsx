"use client";

import { useEffect, useId, useRef } from "react";

const b = "rounded-md px-3 py-1.5 text-sm font-medium focus-visible:outline-2 focus-visible:outline-ring disabled:cursor-not-allowed disabled:opacity-50";
export const btnPrimary = `${b} bg-[var(--color-primary)] text-white hover:bg-[var(--color-primary-hover)]`;
export const btn2 = `${b} border border-[var(--color-border-strong)] bg-card text-foreground hover:bg-hover`;
export const btnDanger = `${b} bg-red-600 text-white hover:bg-red-700`;
export const field = "w-full rounded-md border border-[var(--color-border-strong)] bg-card px-2 py-1.5 text-sm text-foreground focus-visible:outline-2 focus-visible:outline-ring";
export const card = "rounded-xl border border-line bg-card p-4 shadow-[var(--shadow-card)]";
export const th = "px-3 py-2 text-left font-medium text-muted-foreground";
export const td = "px-3 py-2";
export const link = "text-[var(--color-primary)] hover:underline focus-visible:outline-2 focus-visible:outline-ring";

export const fmt = (s: string) => new Date(s).toLocaleString("ko-KR", { hour12: false });

export function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block text-sm">
      <span className="mb-1 block font-medium text-foreground">{label}</span>
      {children}
    </label>
  );
}

export const Err = ({ text }: { text?: string | null }) =>
  text ? (
    <p role="alert" className="text-sm text-red-600">
      {text}
    </p>
  ) : null;

export const Loading = () => (
  <p role="status" className="text-sm text-muted-foreground">
    불러오는 중...
  </p>
);

// DESIGN §1.3: color + icon + text, never color alone
const BADGE: Record<string, [string, string, string]> = {
  DRAFT: ["✎", "초안", "border border-line text-muted-foreground"],
  IN_REVIEW: ["⏳", "검토중", "bg-[var(--color-primary-light)] text-[var(--color-primary)]"],
  PENDING: ["⏳", "대기", "bg-[var(--color-primary-light)] text-[var(--color-primary)]"],
  APPROVED: ["✓", "승인", "bg-snap/15 text-emerald-700"],
  REJECTED: ["✕", "반려", "bg-red-100 text-red-700"],
  RELEASED: ["⬆", "배포", "bg-emerald-600 text-white"],
};
export function StatusBadge({ status }: { status: string }) {
  const [icon, label, cls] = BADGE[status] ?? ["", status, "border border-line text-muted-foreground"];
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${cls}`}>
      <span aria-hidden>{icon}</span>
      {label}
    </span>
  );
}

// native <dialog>: focus trap + Esc for free. Render it conditionally; onClose unmounts it.
export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  const id = useId();
  useEffect(() => ref.current?.showModal(), []);
  return (
    <dialog ref={ref} onClose={onClose} aria-labelledby={id} className="m-auto w-full max-w-md rounded-xl border border-line bg-card p-6 text-body shadow-[var(--shadow-card)] backdrop:bg-black/40">
      <h2 id={id} className="mb-4 text-lg font-semibold text-foreground">
        {title}
      </h2>
      {children}
    </dialog>
  );
}

export type Diff = { added: number; removed: number; changed: number; layers: { layer: string; added: number; removed: number; changed: number }[] };
export function DiffSummary({ d }: { d: Diff }) {
  return (
    <div className="space-y-2 text-sm">
      <p className="text-foreground">
        추가 <b>{d.added}</b> · 삭제 <b>{d.removed}</b> · 변경 <b>{d.changed}</b>
      </p>
      {d.layers.length > 0 && (
        <table className="w-full">
          <thead>
            <tr>
              <th className={th}>레이어</th>
              <th className={th}>추가</th>
              <th className={th}>삭제</th>
              <th className={th}>변경</th>
            </tr>
          </thead>
          <tbody>
            {d.layers.map((l) => (
              <tr key={l.layer} className="border-t border-line">
                <td className={td}>{l.layer}</td>
                <td className={td}>{l.added}</td>
                <td className={td}>{l.removed}</td>
                <td className={td}>{l.changed}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
