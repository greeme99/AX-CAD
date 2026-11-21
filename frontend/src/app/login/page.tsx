"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { api, errText, setTokens } from "@/lib/api";
import { btnPrimary, card, Err, field, Field } from "@/components/ui";
import { ThemeToggle } from "@/components/ThemeToggle";

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
    <main className="flex min-h-screen flex-col items-center justify-center p-4 bg-background text-foreground transition-colors">
      <div className="absolute top-4 right-4">
        <ThemeToggle />
      </div>
      <form onSubmit={submit} className={`w-full max-w-[420px] space-y-5 ${card} p-8 shadow-[var(--shadow-card)]`}>
        <div className="flex flex-col items-center text-center space-y-2">
          <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-[var(--color-primary)] text-white shadow-md">
            <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <polygon points="12 2 2 7 12 12 22 7 12 2" />
              <polyline points="2 17 12 22 22 17" />
              <polyline points="2 12 12 17 22 12" />
            </svg>
          </div>
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-foreground">AX-CAD 로그인</h1>
            <p className="text-xs text-muted-foreground mt-1">2D/3D 웹 기반 CAD 워크벤치 및 자동 견적 플랫폼</p>
          </div>
        </div>
        <Field label="아이디">
          <input name="login_id" required autoFocus autoComplete="username" className={field} placeholder="아이디 입력" />
        </Field>
        <Field label="비밀번호">
          <input name="password" type="password" required autoComplete="current-password" className={field} placeholder="비밀번호 입력" />
        </Field>
        <Err text={error} />
        <button type="submit" disabled={busy} aria-busy={busy} className={`${btnPrimary} w-full py-2 text-base`}>
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
