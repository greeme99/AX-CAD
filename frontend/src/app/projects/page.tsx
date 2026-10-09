"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import AppShell, { useMe } from "@/components/shell/AppShell";
import { btn2, btnPrimary, Err, field, Field, fmt, link, Loading, Modal, StatusBadge, td, th } from "@/components/ui";
import { api, can, errText, qs, type List, type Project } from "@/lib/api";
import { useApi } from "@/lib/useApi";

function NewProject({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const code = String(f.get("project_code")).trim();
    const name = String(f.get("project_name")).trim();
    if (!code || !name) return setError("프로젝트 코드와 이름은 필수입니다");
    setBusy(true);
    try {
      await api("/projects", { method: "POST", json: { project_code: code, project_name: name, customer_name: String(f.get("customer_name")).trim() || undefined } });
      onDone();
    } catch (err) {
      setError(errText(err));
      setBusy(false);
    }
  }
  return (
    <Modal title="새 프로젝트" onClose={onClose}>
      <form onSubmit={submit} className="space-y-3">
        <Field label="프로젝트 코드">
          <input name="project_code" required className={field} />
        </Field>
        <Field label="프로젝트 이름">
          <input name="project_name" required className={field} />
        </Field>
        <Field label="고객사 (선택)">
          <input name="customer_name" className={field} />
        </Field>
        <Err text={error} />
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className={btn2}>
            취소
          </button>
          <button type="submit" disabled={busy} className={btnPrimary}>
            {busy ? "생성 중..." : "생성"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function Projects() {
  const me = useMe();
  const router = useRouter();
  const sp = useSearchParams();
  const status = sp.get("status") ?? "";
  const urlQ = sp.get("q") ?? "";
  const [q, setQ] = useState(urlQ);
  const [open, setOpen] = useState(false);
  const { data, error, reload } = useApi<List<Project>>("/projects" + qs({ q: urlQ, status }));

  // 300 ms debounce, synced to the URL
  useEffect(() => {
    if (q === urlQ) return;
    const t = setTimeout(() => router.replace("/projects" + qs({ q, status })), 300);
    return () => clearTimeout(t);
  }, [q, urlQ, status, router]);

  return (
    <>
      <div className="flex flex-wrap items-end gap-3">
        <Field label="검색">
          <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="코드 / 이름" className={field} />
        </Field>
        <Field label="상태">
          <select value={status} onChange={(e) => router.replace("/projects" + qs({ q, status: e.target.value }))} className={field}>
            <option value="">전체</option>
            <option value="ACTIVE">ACTIVE</option>
            <option value="ARCHIVED">ARCHIVED</option>
          </select>
        </Field>
        <span className="flex-1" />
        {can(me, "DESIGNER") && (
          <button type="button" onClick={() => setOpen(true)} className={btnPrimary}>
            새 프로젝트
          </button>
        )}
      </div>
      <Err text={error && errText(error)} />
      {!data ? (
        !error && <Loading />
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th className={th}>코드</th>
              <th className={th}>이름</th>
              <th className={th}>고객사</th>
              <th className={th}>상태</th>
              <th className={th}>도면</th>
              <th className={th}>생성일</th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((p) => (
              <tr key={p.project_id} className="border-t border-line">
                <td className={`${td} font-mono`}>{p.project_code}</td>
                <td className={td}>
                  <Link href={`/projects/${p.project_id}`} className={link}>
                    {p.project_name}
                  </Link>
                </td>
                <td className={td}>{p.customer_name ?? "-"}</td>
                <td className={td}>
                  <StatusBadge status={p.status} />
                </td>
                <td className={td}>{p.document_count}</td>
                <td className={td}>{fmt(p.created_at)}</td>
              </tr>
            ))}
            {data.items.length === 0 && (
              <tr>
                <td colSpan={6} className={`${td} text-muted-foreground`}>
                  프로젝트가 없습니다.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}
      {open && (
        <NewProject
          onClose={() => setOpen(false)}
          onDone={() => {
            setOpen(false);
            reload();
          }}
        />
      )}
    </>
  );
}

export default function ProjectsPage() {
  return (
    <AppShell title="프로젝트">
      <Suspense>
        <Projects />
      </Suspense>
    </AppShell>
  );
}
