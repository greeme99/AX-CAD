"use client";

import { useState } from "react";
import AppShell, { useMe } from "@/components/shell/AppShell";
import { btn2, btnPrimary, Err, field, Field, fmt, Loading, Modal, td, th } from "@/components/ui";
import { api, can, errText, type List } from "@/lib/api";
import { useApi } from "@/lib/useApi";

type AdminUser = { user_id: number; login_id: string; user_name: string; roles: string[]; is_active: boolean; created_at: string; locked: boolean };
const ROLES = ["ADMIN", "DESIGNER", "ESTIMATOR", "REVIEWER", "MANUFACTURING", "VIEWER"];

const Roles = ({ defaults }: { defaults: string[] }) => (
  <fieldset className="text-sm">
    <legend className="mb-1 font-medium text-foreground">역할</legend>
    <div className="flex flex-wrap gap-x-4 gap-y-1">
      {ROLES.map((r) => (
        <label key={r} className="flex items-center gap-1">
          <input type="checkbox" name="roles" value={r} defaultChecked={defaults.includes(r)} />
          {r}
        </label>
      ))}
    </div>
  </fieldset>
);

// one dialog for create (u undefined) and edit-roles
function UserDialog({ u, onClose, onDone }: { u?: AdminUser; onClose: () => void; onDone: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const roles = f.getAll("roles");
    const password = String(f.get("password") ?? "");
    if (!u && password.length < 10) return setError("비밀번호는 10자 이상이어야 합니다.");
    setBusy(true);
    try {
      if (u) await api(`/users/${u.user_id}`, { method: "PATCH", json: { roles } });
      else await api("/users", { method: "POST", json: { login_id: String(f.get("login_id")).trim(), user_name: String(f.get("user_name")).trim(), password, roles } });
      onDone();
    } catch (err) {
      setError(errText(err));
      setBusy(false);
    }
  }
  return (
    <Modal title={u ? `역할 변경 - ${u.login_id}` : "사용자 생성"} onClose={onClose}>
      <form onSubmit={submit} className="space-y-3">
        {!u && (
          <>
            <Field label="아이디">
              <input name="login_id" required className={field} />
            </Field>
            <Field label="이름">
              <input name="user_name" required className={field} />
            </Field>
            <Field label="비밀번호 (10자 이상)">
              <input name="password" type="password" required minLength={10} autoComplete="new-password" className={field} />
            </Field>
          </>
        )}
        <Roles defaults={u?.roles ?? []} />
        <Err text={error} />
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className={btn2}>
            취소
          </button>
          <button type="submit" disabled={busy} className={btnPrimary}>
            {u ? "저장" : "생성"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function Users() {
  const me = useMe();
  const { data, error, reload } = useApi<List<AdminUser>>(can(me, "ADMIN") ? "/users" : null);
  const [dlg, setDlg] = useState<AdminUser | "new" | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function patch(u: AdminUser, json: object) {
    setErr(null);
    try {
      await api(`/users/${u.user_id}`, { method: "PATCH", json });
      reload();
    } catch (e) {
      setErr(errText(e));
    }
  }
  if (!can(me, "ADMIN")) return <Err text="관리자만 접근할 수 있습니다." />;
  return (
    <>
      <div>
        <button type="button" onClick={() => setDlg("new")} className={btnPrimary}>
          사용자 생성
        </button>
      </div>
      <Err text={err ?? (error && errText(error))} />
      {!data ? (
        !error && <Loading />
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th className={th}>아이디</th>
              <th className={th}>이름</th>
              <th className={th}>역할</th>
              <th className={th}>상태</th>
              <th className={th}>생성일</th>
              <th className={th}>작업</th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((u) => (
              <tr key={u.user_id} className="border-t border-line">
                <td className={`${td} font-mono`}>{u.login_id}</td>
                <td className={td}>{u.user_name}</td>
                <td className={td}>{u.roles.join(", ")}</td>
                <td className={td}>
                  {u.is_active ? "✓ 활성" : "✕ 비활성"}
                  {u.locked && " · ⛔ 잠금"}
                </td>
                <td className={td}>{fmt(u.created_at)}</td>
                <td className={`${td} space-x-2`}>
                  <button type="button" onClick={() => void patch(u, { is_active: !u.is_active })} className={btn2}>
                    {u.is_active ? "비활성화" : "활성화"}
                  </button>
                  {u.locked && (
                    <button type="button" onClick={() => void patch(u, { unlock: true })} className={btn2}>
                      잠금 해제
                    </button>
                  )}
                  <button type="button" onClick={() => setDlg(u)} className={btn2}>
                    역할
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {dlg && (
        <UserDialog
          u={dlg === "new" ? undefined : dlg}
          onClose={() => setDlg(null)}
          onDone={() => {
            setDlg(null);
            reload();
          }}
        />
      )}
    </>
  );
}

export default function UsersPage() {
  return (
    <AppShell title="사용자 관리">
      <Users />
    </AppShell>
  );
}
