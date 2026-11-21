"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext } from "react";
import { can, clearTokens, errText, type User } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { Err, Loading } from "@/components/ui";
import { CopilotToggle } from "@/components/copilot/CopilotProvider";
import { ThemeToggle } from "@/components/ThemeToggle";

const Ctx = createContext<User | null>(null);
export const useMe = () => useContext(Ctx)!;

// Business-page shell (wireframe §0.1). Guards the route: api() redirects to /login on 401.
// ponytail: fixed 224px sidebar that stacks on top under md; collapsible 64px rail later
export default function AppShell({ title, children }: { title: string; children: React.ReactNode }) {
  const router = useRouter();
  const path = usePathname();
  const me = useApi<User>("/auth/me");
  const user = me.data;
  const reviewer = !!user && can(user, "REVIEWER");

  const logout = () => {
    clearTokens();
    router.replace("/login");
  };
  const links: [string, string][] = [["/", "홈"], ["/cad", "캐드 메뉴"], ["/projects", "프로젝트"]];
  if (user && can(user, "ADMIN") && !user.local) links.push(["/admin/users", "사용자"]);
  if (user && can(user, "ESTIMATOR")) links.push(["/admin/master-data", "기준정보"]);
  if (reviewer) links.push(["/admin/audit", "감사 로그"]);
  if (user && can(user, "ADMIN") && user.local) links.push(["/admin/updates", "업데이트"]); // per-PC install (P5)

  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground transition-colors duration-200">
      <header className="flex h-14 shrink-0 items-center gap-3 border-b border-line bg-card px-4 text-sm shadow-[0_1px_3px_rgba(0,0,0,0.03)]">
        <Link href="/" className="flex items-center gap-2 font-bold tracking-tight text-foreground transition-opacity hover:opacity-85">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-[var(--color-primary)] text-white shadow-sm">
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <polygon points="12 2 2 7 12 12 22 7 12 2" />
              <polyline points="2 17 12 22 22 17" />
              <polyline points="2 12 12 17 22 12" />
            </svg>
          </div>
          <span className="text-base font-semibold">AX-CAD</span>
        </Link>
        <span className="flex-1" />
        <ThemeToggle />
        <CopilotToggle />
        {user && !user.local && (
          <div className="flex items-center gap-2 pl-2 border-l border-line">
            <span className="text-body text-xs md:text-sm">
              {user.user_name} <span className="text-muted-foreground hidden sm:inline">({user.roles.join(", ")})</span>
            </span>
            <button
              type="button"
              onClick={logout}
              className="rounded-md border border-line px-2.5 py-1 text-xs hover:bg-hover transition-colors focus-visible:outline-2 focus-visible:outline-ring"
            >
              로그아웃
            </button>
          </div>
        )}
      </header>
      <div className="flex flex-1 flex-col md:flex-row">
        <nav aria-label="주 메뉴" className="shrink-0 border-b border-line bg-card p-2 md:w-56 md:border-r md:border-b-0">
          <ul className="flex gap-1 overflow-x-auto md:flex-col">
            {links.map(([href, label]) => {
              const on = href === "/" ? path === "/" : path.startsWith(href);
              return (
                <li key={href}>
                  <Link
                    href={href}
                    aria-current={on ? "page" : undefined}
                    className={`block rounded-lg px-3 py-2 text-sm whitespace-nowrap transition-all duration-150 focus-visible:outline-2 focus-visible:outline-ring ${
                      on
                        ? "bg-[var(--color-primary-light)] font-medium text-[var(--color-primary-hover)] shadow-xs"
                        : "text-body hover:bg-hover"
                    }`}
                  >
                    {label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
        <main className="min-w-0 flex-1 space-y-4 overflow-x-auto p-6">
          <h1 className="text-2xl font-bold tracking-tight text-foreground">{title}</h1>
          {me.error ? <Err text={errText(me.error)} /> : user ? <Ctx.Provider value={user}>{children}</Ctx.Provider> : <Loading />}
        </main>
      </div>
    </div>
  );
}
