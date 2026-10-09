"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
import AppShell, { useMe } from "@/components/shell/AppShell";
import { btn2, btnPrimary, card, Err, field, Field, fmt, link, Loading, Modal, StatusBadge, td, th } from "@/components/ui";
import { api, can, errText, qs, type Doc, type List, type Project, type User } from "@/lib/api";
import { useApi } from "@/lib/useApi";

type Member = { user_id: number; login_id: string; user_name: string; roles: string[] };

function NewDoc({ projectId, onClose, onDone }: { projectId: string; onClose: () => void; onDone: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const doc_no = String(f.get("doc_no")).trim();
    const title = String(f.get("title")).trim();
    if (!doc_no || !title) return setError("도면번호와 제목은 필수입니다");
    setBusy(true);
    try {
      await api(`/projects/${projectId}/documents`, { method: "POST", json: { doc_no, title, doc_type: f.get("doc_type") } });
      onDone();
    } catch (err) {
      setError(errText(err));
      setBusy(false);
    }
  }
  return (
    <Modal title="새 도면" onClose={onClose}>
      <form onSubmit={submit} className="space-y-3">
        <Field label="도면번호">
          <input name="doc_no" required className={field} />
        </Field>
        <Field label="유형">
          <select name="doc_type" className={field}>
            <option value="DRAWING">DRAWING</option>
            <option value="PART">PART</option>
            <option value="ASSEMBLY">ASSEMBLY</option>
          </select>
        </Field>
        <Field label="제목">
          <input name="title" required className={field} />
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

function Members({ projectId }: { projectId: string }) {
  const me = useMe();
  const admin = can(me, "ADMIN");
  const members = useApi<List<Member>>(`/projects/${projectId}/members`);
  const users = useApi<List<User>>(admin ? "/users" : null);
  const [error, setError] = useState<string | null>(null);
  const list = members.data?.items;
  const candidates = users.data?.items.filter((u) => !list?.some((m) => m.user_id === u.user_id));

  async function act(path: string, init: Parameters<typeof api>[1]) {
    setError(null);
    try {
      await api(path, init);
      members.reload();
    } catch (err) {
      setError(errText(err));
    }
  }

  return (
    <section aria-labelledby="members" className={card}>
      <h2 id="members" className="mb-2 text-lg font-semibold text-foreground">
        구성원
      </h2>
      <Err text={error ?? (members.error && errText(members.error))} />
      {!list ? (
        !members.error && <Loading />
      ) : (
        <ul className="divide-y divide-[var(--color-border)] text-sm">
          {list.map((m) => (
            <li key={m.user_id} className="flex items-center justify-between py-1.5">
              <span>
                {m.user_name} <span className="text-muted-foreground">({m.login_id} · {m.roles.join(", ")})</span>
              </span>
              {admin && (
                <button type="button" onClick={() => void act(`/projects/${projectId}/members/${m.user_id}`, { method: "DELETE" })} className={btn2}>
                  제거
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {admin && candidates && (
        <form
          className="mt-3 flex items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            const id = Number(new FormData(e.currentTarget).get("user_id"));
            if (id) void act(`/projects/${projectId}/members`, { method: "POST", json: { user_id: id } });
          }}
        >
          <Field label="구성원 추가">
            <select name="user_id" className={field} defaultValue="">
              <option value="">사용자 선택</option>
              {candidates.map((u) => (
                <option key={u.user_id} value={u.user_id}>
                  {u.user_name} ({u.login_id})
                </option>
              ))}
            </select>
          </Field>
          <button type="submit" className={btnPrimary}>
            추가
          </button>
        </form>
      )}
    </section>
  );
}

function Detail({ projectId }: { projectId: string }) {
  const me = useMe();
  const [q, setQ] = useState("");
  const [dq, setDq] = useState("");
  const [status, setStatus] = useState("");
  const [type, setType] = useState("");
  const [open, setOpen] = useState(false);
  const project = useApi<Project>(`/projects/${projectId}`);
  const docs = useApi<List<Doc>>(`/projects/${projectId}/documents` + qs({ q: dq, status, doc_type: type }));

  useEffect(() => {
    const t = setTimeout(() => setDq(q), 300);
    return () => clearTimeout(t);
  }, [q]);

  return (
    <>
      <Err text={project.error && errText(project.error)} />
      {project.data && (
        <p className="text-sm text-body">
          <span className="font-mono">{project.data.project_code}</span> · {project.data.project_name} · {project.data.customer_name ?? "-"} <StatusBadge status={project.data.status} />
        </p>
      )}
      <div className="flex flex-wrap items-end gap-3">
        <Field label="검색">
          <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="도면번호 / 제목" className={field} />
        </Field>
        <Field label="유형">
          <select value={type} onChange={(e) => setType(e.target.value)} className={field}>
            <option value="">전체</option>
            <option value="DRAWING">DRAWING</option>
            <option value="PART">PART</option>
            <option value="ASSEMBLY">ASSEMBLY</option>
          </select>
        </Field>
        <Field label="상태">
          <select value={status} onChange={(e) => setStatus(e.target.value)} className={field}>
            <option value="">전체</option>
            {["DRAFT", "IN_REVIEW", "APPROVED", "REJECTED", "RELEASED"].map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </Field>
        <span className="flex-1" />
        {can(me, "DESIGNER") && (
          <button type="button" onClick={() => setOpen(true)} className={btnPrimary}>
            새 도면
          </button>
        )}
      </div>
      <Err text={docs.error && errText(docs.error)} />
      {!docs.data ? (
        !docs.error && <Loading />
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th className={th}>도면번호</th>
              <th className={th}>제목</th>
              <th className={th}>유형</th>
              <th className={th}>개정</th>
              <th className={th}>상태</th>
              <th className={th}>생성일</th>
              <th className={th}>작업</th>
            </tr>
          </thead>
          <tbody>
            {docs.data.items.map((d) => (
              <tr key={d.document_id} className="border-t border-line">
                <td className={`${td} font-mono`}>{d.doc_no}</td>
                <td className={td}>{d.title}</td>
                <td className={td}>{d.doc_type}</td>
                <td className={td}>{d.current_revision_no ?? "-"}</td>
                <td className={td}>
                  <StatusBadge status={d.status} />
                </td>
                <td className={td}>{fmt(d.created_at)}</td>
                <td className={`${td} space-x-3`}>
                  {d.current_revision_id ? (
                    <Link href={`/viewer/${d.current_revision_id}?doc=${d.document_id}`} className={link}>
                      열기
                    </Link>
                  ) : (
                    <span aria-disabled="true" title="리비전이 없습니다" className="text-muted-foreground">
                      열기
                    </span>
                  )}
                  <Link href={`/documents/${d.document_id}`} className={link}>
                    상세
                  </Link>
                </td>
              </tr>
            ))}
            {docs.data.items.length === 0 && (
              <tr>
                <td colSpan={7} className={`${td} text-muted-foreground`}>
                  도면이 없습니다.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}
      <Members projectId={projectId} />
      {open && (
        <NewDoc
          projectId={projectId}
          onClose={() => setOpen(false)}
          onDone={() => {
            setOpen(false);
            docs.reload();
          }}
        />
      )}
    </>
  );
}

export default function ProjectPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = use(params);
  return (
    <AppShell title="도면 목록">
      <Detail projectId={projectId} />
    </AppShell>
  );
}
