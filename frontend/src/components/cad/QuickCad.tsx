"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { btn2, btnPrimary, card, Err, field, Field } from "@/components/ui";
import { api, errText, type Doc, type Project } from "@/lib/api";
import { SHEETS, startBlank, type Sheet } from "@/lib/blank";
import QuoteChoice from "./QuoteChoice";

const MAX_BYTES = 50 * 1024 * 1024;

// a unique drawing number for a throw-away drawing: Q-yyyymmdd-hhmmss-xx
function quickNo(): string {
  const t = new Date().toISOString().replace(/\D/g, "").slice(0, 14);
  return `Q-${t.slice(0, 8)}-${t.slice(8)}-${Math.random().toString(36).slice(2, 4)}`;
}

// 캐드 메뉴: draft or view a drawing right away, with no project to set up first.
// The drawings live in one shared "빠른 도면" project (POST /quick-cad), still reachable from 프로젝트.
export default function QuickCad({ hideTitle = false }: { hideTitle?: boolean }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [home, setHome] = useState<number | null>(null);
  // asked for every new drawing (no default): quote drawing or general drawing
  const [drawQuote, setDrawQuote] = useState<boolean | null>(null);
  const [viewQuote, setViewQuote] = useState<boolean | null>(null);

  async function newDoc(title: string, needsQuote: boolean): Promise<Doc> {
    const p = await api<Project>("/quick-cad", { method: "POST" });
    setHome(p.project_id);
    return api<Doc>(`/projects/${p.project_id}/documents`, { method: "POST", json: { doc_no: quickNo(), title, doc_type: "DRAWING", needs_quote: needsQuote } });
  }
  async function run(job: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await job();
    } catch (err) {
      setError(errText(err));
      setBusy(false);
    }
  }
  function draw(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (drawQuote === null) return setError("도면 종류(견적 도면 / 일반 도면)를 고르세요.");
    const f = new FormData(e.currentTarget);
    void run(async () => {
      const d = await newDoc("빠른 도면", drawQuote);
      const rev = await startBlank(d.document_id, f.get("sheet") as Sheet, f.get("portrait") === "on");
      router.push(`/viewer/${rev}?doc=${d.document_id}`);
    });
  }
  function view(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    if (viewQuote === null) return setError("도면 종류(견적 도면 / 일반 도면)를 고르세요.");
    if (!/\.(dxf|dwg)$/i.test(file.name)) {
      return setError("지원하지 않는 파일 형식입니다. .dxf 또는 .dwg 파일(최대 50MB)을 선택하거나 아래 '새 도면 그리기'로 시작하세요.");
    }
    if (file.size > MAX_BYTES) return setError("파일 크기는 50MB 이하여야 합니다.");
    void run(async () => {
      const d = await newDoc(file.name.replace(/\.[^.]+$/, "").slice(0, 200) || "빠른 도면", viewQuote);
      const body = new FormData();
      body.set("file", file);
      const saved = await api<{ revision_id: string }>(`/documents/${d.document_id}/dxf`, { method: "POST", body });
      router.push(`/viewer/${saved.revision_id}?doc=${d.document_id}`);
    });
  }

  return (
    <section aria-labelledby="quick-cad" className="space-y-3">
      {!hideTitle && (
        <h2 id="quick-cad" className="text-lg font-semibold text-foreground">
          캐드 메뉴
        </h2>
      )}
      <p className="text-sm text-body">프로젝트를 만들지 않고 바로 도면을 그리거나 열어 봅니다. 도면은 &ldquo;빠른 도면&rdquo; 프로젝트에 보관됩니다.</p>
      <div className="grid gap-5 md:grid-cols-2 items-stretch">
        <form onSubmit={draw} className={`${card} card-hover-interactive flex flex-col justify-between space-y-4`}>
          <div className="space-y-4">
            <div className="flex items-center gap-2.5">
              <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-[var(--color-primary-light)] text-[var(--color-primary)]">
                <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M12 5v14" />
                  <path d="M5 12h14" />
                </svg>
              </div>
              <div>
                <h3 className="font-semibold text-foreground">새 도면 작성</h3>
                <p className="text-xs text-muted-foreground">표준 레이어·mm 단위의 빈 도면을 만들고 작도 화면을 엽니다.</p>
              </div>
            </div>
            <Field label="용지">
              <select name="sheet" defaultValue="A3" className={field}>
                {SHEETS.map((s) => (
                  <option key={s}>{s}</option>
                ))}
              </select>
            </Field>
            <label className="flex items-center gap-2 text-sm text-body cursor-pointer select-none">
              <input type="checkbox" name="portrait" className="rounded border-line text-[var(--color-primary)] focus:ring-[var(--color-primary)]" /> 세로 방향
            </label>
            <QuoteChoice name="draw-quote" value={drawQuote} onChange={setDrawQuote} />
          </div>
          <button type="submit" disabled={busy || drawQuote === null} className={btnPrimary}>
            {busy ? "여는 중…" : "작도 시작"}
          </button>
        </form>
        <div className={`${card} card-hover-interactive flex flex-col justify-between space-y-4`}>
          <div className="space-y-4">
            <div className="flex items-center gap-2.5">
              <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-500/15 text-emerald-600 dark:text-emerald-400">
                <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                  <polyline points="17 8 12 3 7 8" />
                  <line x1="12" y1="3" x2="12" y2="15" />
                </svg>
              </div>
              <div>
                <h3 className="font-semibold text-foreground">도면 열기 (뷰어)</h3>
                <p className="text-xs text-muted-foreground">DXF·DWG 파일(50MB 이하)을 골라 바로 보고, 필요하면 이어서 편집합니다.</p>
              </div>
            </div>
            <QuoteChoice name="view-quote" value={viewQuote} onChange={setViewQuote} />
          </div>
          <div>
            <label className={`${btn2} inline-flex items-center gap-1.5 cursor-pointer shadow-xs ${busy || viewQuote === null ? "pointer-events-none opacity-60" : ""}`}>
              <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <polyline points="14 2 14 8 20 8" />
              </svg>
              파일 선택
              <input type="file" accept=".dxf,.dwg" onChange={view} disabled={busy || viewQuote === null} className="sr-only" />
            </label>
          </div>
        </div>
      </div>
      <Err text={error} />
      {home !== null && (
        <Link href={`/projects/${home}`} className="text-sm underline">
          빠른 도면 목록 보기
        </Link>
      )}
    </section>
  );
}
