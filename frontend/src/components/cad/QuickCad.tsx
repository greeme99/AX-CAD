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
export default function QuickCad() {
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
    if (!/\.(dxf|dwg)$/i.test(file.name)) return setError(".dxf 또는 .dwg 파일만 열 수 있습니다.");
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
      <h2 id="quick-cad" className="text-lg font-semibold text-foreground">
        캐드 메뉴
      </h2>
      <p className="text-sm text-body">프로젝트를 만들지 않고 바로 도면을 그리거나 열어 봅니다. 도면은 &ldquo;빠른 도면&rdquo; 프로젝트에 보관됩니다.</p>
      <div className="grid gap-4 md:grid-cols-2">
        <form onSubmit={draw} className={`${card} space-y-3`}>
          <h3 className="font-semibold text-foreground">새 도면 작성</h3>
          <p className="text-xs text-muted-foreground">표준 레이어·mm 단위의 빈 도면을 만들고 작도 화면을 엽니다.</p>
          <Field label="용지">
            <select name="sheet" defaultValue="A3" className={field}>
              {SHEETS.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </Field>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" name="portrait" /> 세로
          </label>
          <QuoteChoice name="draw-quote" value={drawQuote} onChange={setDrawQuote} />
          <button type="submit" disabled={busy || drawQuote === null} className={btnPrimary}>
            {busy ? "여는 중…" : "작도 시작"}
          </button>
        </form>
        <div className={`${card} space-y-3`}>
          <h3 className="font-semibold text-foreground">도면 열기 (뷰어)</h3>
          <p className="text-xs text-muted-foreground">DXF·DWG 파일(50MB 이하)을 골라 바로 보고, 필요하면 이어서 편집합니다.</p>
          <QuoteChoice name="view-quote" value={viewQuote} onChange={setViewQuote} />
          <label className={`${btn2} inline-block cursor-pointer ${busy || viewQuote === null ? "pointer-events-none opacity-60" : ""}`}>
            파일 선택
            <input type="file" accept=".dxf,.dwg" onChange={view} disabled={busy || viewQuote === null} className="sr-only" />
          </label>
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
