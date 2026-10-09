"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { api, errText, setTokens } from "@/lib/api";
import { btnPrimary, Err, field, Field } from "@/components/ui";

function LoginForm() {
  const router = useRouter();
  const next = useSearchParams().get("next");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setBusy(true);
    setError(null);
    try {
      const d = await api<{ access_token: string; refresh_token: string }>("/auth/login", {
        method: "POST",
        json: { login_id: f.get("login_id"), password: f.get("password") },
        anon: true,
      });
      setTokens(d.access_token, d.refresh_token);
      router.replace(next && /^\/(?![\/\\])/.test(next) ? next : "/"); // same-origin paths only ("/\\x" is "//x" to browsers)
    } catch (err) {
      setError(errText(err));
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <form onSubmit={submit} className="w-full max-w-[400px] space-y-4 rounded-xl border border-line bg-card p-6 shadow-[var(--shadow-card)]">
        <h1 className="text-xl font-bold text-foreground">AX-CAD 로그인</h1>
        <Field label="아이디">
          <input name="login_id" required autoFocus autoComplete="username" className={field} />
        </Field>
        <Field label="비밀번호">
          <input name="password" type="password" required autoComplete="current-password" className={field} />
        </Field>
        <Err text={error} />
        <button type="submit" disabled={busy} aria-busy={busy} className={`${btnPrimary} w-full`}>
          {busy ? "로그인 중..." : "로그인"}
        </button>
      </form>
    </main>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}
