"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext } from "react";
import { can, clearTokens, errText, type List, type User } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { Err, Loading } from "@/components/ui";

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
  const pending = useApi<List<unknown>>(reviewer ? "/approvals?status=PENDING" : null).data?.total;

  const logout = () => {
    clearTokens();
    router.replace("/login");
  };
  const links: [string, string][] = [["/", "홈"], ["/projects", "프로젝트"]];
  if (reviewer) links.push(["/approvals", "승인함"]);
  if (user && can(user, "ADMIN")) links.push(["/admin/users", "사용자"]);
  if (user && can(user, "ESTIMATOR")) links.push(["/admin/master-data", "기준정보"]);
  if (reviewer) links.push(["/admin/audit", "감사 로그"]);
  if (user && can(user, "MANUFACTURING")) links.push(["/admin/integrations", "연동 작업"]);

  return (
    <div className="flex min-h-screen flex-col">
      <header className="flex h-14 shrink-0 items-center gap-4 border-b border-line bg-card px-4 text-sm">
        <span className="font-semibold text-foreground">AX-CAD</span>
        <span className="flex-1" />
        {user && (
          <>
            <span className="text-body">
              {user.user_name} <span className="text-muted-foreground">({user.roles.join(", ")})</span>
            </span>
            <button type="button" onClick={logout} className="rounded-md border border-line px-2 py-1 hover:bg-hover focus-visible:outline-2 focus-visible:outline-ring">
              로그아웃
            </button>
          </>
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
                    className={`block rounded-md px-3 py-2 text-sm whitespace-nowrap focus-visible:outline-2 focus-visible:outline-ring ${on ? "bg-[var(--color-primary-light)] font-medium text-[var(--color-primary-hover)]" : "text-body hover:bg-hover"}`}
                  >
                    {label}
                    {href === "/approvals" && pending ? (
                      <span className="ml-2 rounded-full bg-[var(--color-primary-solid)] px-1.5 text-xs text-white" aria-label={`대기 ${pending}건`}>
                        {pending}
                      </span>
                    ) : null}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
        <main className="min-w-0 flex-1 space-y-4 overflow-x-auto p-6">
          <h1 className="text-2xl font-bold text-foreground">{title}</h1>
          {me.error ? <Err text={errText(me.error)} /> : user ? <Ctx.Provider value={user}>{children}</Ctx.Provider> : <Loading />}
        </main>
      </div>
    </div>
  );
}
