"use client";

import Link from "next/link";
import AppShell, { useMe } from "@/components/shell/AppShell";
import { card, Err, link, Loading } from "@/components/ui";
import { can, errText, type List, type Project } from "@/lib/api";
import { useApi } from "@/lib/useApi";

function Home() {
  const me = useMe();
  const { data, error } = useApi<List<Project>>("/projects");
  const pending = useApi<List<unknown>>(can(me, "REVIEWER") ? "/approvals?status=PENDING" : null).data?.total;
  // ponytail: newest-created stands in for "recent"; needs last-opened tracking server side
  const recent = data?.items.toSorted((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 6);
  return (
    <>
      {pending !== undefined && (
        <Link href="/approvals" className={`${card} block ${link}`}>
          승인 대기 <b className="text-lg">{pending}</b>건
        </Link>
      )}
      <h2 className="text-lg font-semibold text-foreground">내 프로젝트</h2>
      <Err text={error && errText(error)} />
      {!data ? (
        !error && <Loading />
      ) : recent!.length === 0 ? (
        <p className="text-sm text-muted-foreground">참여 중인 프로젝트가 없습니다.</p>
      ) : (
        <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {recent!.map((p) => (
            <li key={p.project_id}>
              <Link href={`/projects/${p.project_id}`} className={`${card} block hover:bg-hover focus-visible:outline-2 focus-visible:outline-ring`}>
                <span className="font-mono text-xs text-muted-foreground">{p.project_code}</span>
                <span className="block font-semibold text-foreground">{p.project_name}</span>
                <span className="text-sm text-body">
                  {p.customer_name ?? "-"} · 도면 {p.document_count}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

export default function HomePage() {
  return (
    <AppShell title="홈">
      <Home />
    </AppShell>
  );
}
