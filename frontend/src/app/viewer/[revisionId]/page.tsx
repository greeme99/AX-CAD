"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
import CanvasViewport, { type RenderEntity } from "@/components/cad/CanvasViewport";
import type { Extents } from "@/lib/cad/view";

type RenderData = {
  units: string;
  extents: Extents;
  layers: { name: string; color: string | null; visible: boolean }[];
  entities: RenderEntity[];
  summary: { entity_count: number; layer_count: number; block_count: number; paperspace_layouts: number };
  warnings: string[];
};

export default function ViewerPage({ params }: { params: Promise<{ revisionId: string }> }) {
  const { revisionId } = use(params);
  const [data, setData] = useState<RenderData | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Set, not a plain object: layer names like "constructor" must not hit Object.prototype
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [cursor, setCursor] = useState<[number, number] | null>(null);
  const [scale, setScale] = useState(1);

  useEffect(() => {
    let live = true;
    fetch(`/api/revisions/${encodeURIComponent(revisionId)}/render`)
      .then((r) => r.json())
      .then((j) => {
        if (!live) return;
        if (!j.success) return setError(`${j.error?.code ?? "ERROR"}: ${j.error?.message ?? "렌더 데이터를 불러오지 못했습니다."}`);
        const d: RenderData = j.data;
        setData(d);
        setHidden(new Set(d.layers.filter((l) => !l.visible).map((l) => l.name)));
      })
      .catch((e) => live && setError(`NETWORK: ${e instanceof Error ? e.message : "요청 실패"}`));
    return () => {
      live = false;
    };
  }, [revisionId]);

  return (
    <div className="flex h-screen flex-col bg-background">
      <header className="flex h-12 shrink-0 items-center gap-4 border-b border-line bg-card px-4 text-sm">
        <span className="font-semibold text-foreground">AX-CAD</span>
        <Link href="/" className="text-[var(--color-primary)] hover:underline focus-visible:outline-2 focus-visible:outline-ring">
          ← 업로드
        </Link>
        {data && (
          <span className="font-mono text-muted-foreground">
            리비전 {revisionId} · 엔티티 {data.summary.entity_count} · 레이어 {data.summary.layer_count} · 블록 {data.summary.block_count}
          </span>
        )}
      </header>

      {error ? (
        <p role="alert" className="p-6 text-red-600">
          {error}
        </p>
      ) : !data ? (
        <p role="status" className="p-6 text-muted-foreground">
          불러오는 중...
        </p>
      ) : (
        <>
          <div className="flex min-h-0 flex-1">
            <aside aria-label="레이어" className="w-60 shrink-0 overflow-y-auto border-r border-line bg-card p-3">
              <h2 className="mb-2 text-sm font-semibold text-foreground">레이어</h2>
              <ul className="space-y-1">
                {data.layers.map((l) => (
                  <li key={l.name}>
                    <label className="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm hover:bg-hover">
                      <input
                        type="checkbox"
                        checked={!hidden.has(l.name)}
                        onChange={(e) =>
                          setHidden((h) => {
                            const next = new Set(h);
                            if (e.target.checked) next.delete(l.name);
                            else next.add(l.name);
                            return next;
                          })
                        }
                      />
                      <span
                        aria-hidden
                        className="inline-block h-3 w-3 rounded-sm border border-line-strong"
                        style={{ background: l.color ?? "var(--canvas-fg)" }}
                      />
                      <span className="truncate text-body">{l.name}</span>
                    </label>
                  </li>
                ))}
              </ul>
            </aside>
            <main className="min-w-0 flex-1">
              <CanvasViewport entities={data.entities} extents={data.extents} hidden={hidden} onCursor={setCursor} onZoom={setScale} />
            </main>
          </div>
          <footer className="flex h-8 shrink-0 items-center gap-6 border-t border-line bg-muted px-4 font-mono text-xs text-body">
            <span>
              X {cursor ? cursor[0].toFixed(2) : "--"} Y {cursor ? cursor[1].toFixed(2) : "--"} mm
            </span>
            <span>줌 {Math.round(scale * 100)}%</span>
            <span>단위 {data.units}</span>
            <span>엔티티 {data.summary.entity_count}</span>
            <span title={data.warnings.join("\n")}>경고 {data.warnings.length}</span>
          </footer>
        </>
      )}
    </div>
  );
}
