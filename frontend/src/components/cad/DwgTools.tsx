"use client";

import { useState } from "react";
import { btn2, btnPrimary, card, Err, field, Field, td, th } from "@/components/ui";
import { api, ApiError, download, errText, warnText } from "@/lib/api";
import { useApi } from "@/lib/useApi";

// Track 10: DWG save-as by AutoCAD version, the original DWG and its conversion report, batch
// import and the converter status (with the ODA File Converter install helper)

export type DwgConverter = { available: boolean; kind: "ODA" | "LibreDWG" | null; program: string | null; can_read: boolean; can_write: boolean; versions: string[]; download_url: string };

export function useDwgConverter() {
  return useApi<DwgConverter>("/system/dwg-converter");
}

export function ConverterStatus({ conv }: { conv: ReturnType<typeof useDwgConverter> }) {
  const c = conv.data;
  if (conv.error) return <Err text={errText(conv.error)} />;
  if (!c) return null;
  if (c.available)
    return (
      <p role="status" className="text-sm text-muted-foreground">
        DWG 변환기: {c.kind === "ODA" ? "ODA File Converter" : "LibreDWG"} ({c.program}) · 읽기 {c.can_read ? "가능" : "불가"} · 저장 {c.can_write ? `가능 (AutoCAD ${c.versions.join("/")})` : "불가 — 저장은 ODA 필요"}
      </p>
    );
  return (
    <div role="status" className="space-y-2 rounded-md border border-line p-3 text-sm">
      <p className="font-medium text-foreground">DWG 변환기가 없습니다 — DWG 열기·저장에 ODA File Converter(무료)가 필요합니다.</p>
      <ol className="list-decimal space-y-0.5 pl-5 text-body">
        <li>
          아래 주소에서 Windows용 ODA File Converter를 내려받아 기본 위치(Program Files\ODA)에 설치합니다:{" "}
          <span className="select-all font-mono text-xs">{c.download_url}</span>
        </li>
        <li>설치가 끝나면 &quot;다시 확인&quot;을 누릅니다(AX-CAD를 다시 시작하지 않아도 됩니다).</li>
        <li>설치할 수 없으면 원래 CAD에서 DXF로 저장해 올리세요.</li>
      </ol>
      <div className="flex gap-2">
        <a href={c.download_url} target="_blank" rel="noopener noreferrer" className={btn2}>
          ODA 내려받기 페이지
        </a>
        <button type="button" onClick={conv.reload} className={btn2}>
          다시 확인
        </button>
      </div>
    </div>
  );
}

const LABEL: Record<string, string> = { R12: "R12 (DXF만)" };

export function SaveAs({ revisionId, name }: { revisionId: string; name: string }) {
  const conv = useDwgConverter();
  const [fmt, setFmt] = useState<"DWG" | "DXF">("DWG");
  const [ver, setVer] = useState("2018");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const versions = (conv.data?.versions ?? []).filter((v) => fmt === "DXF" || v !== "R12");
  async function save() {
    setBusy(true);
    setError(null);
    try {
      const q = new URLSearchParams({ format: fmt, version: ver });
      await download(`/revisions/${encodeURIComponent(revisionId)}/export?${q}`, `${name}-${ver}.${fmt.toLowerCase()}`);
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-end gap-2">
        <Field label="형식">
          <select
            value={fmt}
            onChange={(e) => {
              const f = e.target.value as "DWG" | "DXF";
              setFmt(f);
              if (f === "DWG" && ver === "R12") setVer("2018");
            }}
            className={field}
          >
            <option value="DWG">DWG</option>
            <option value="DXF">DXF</option>
          </select>
        </Field>
        <Field label="AutoCAD 버전">
          <select value={ver} onChange={(e) => setVer(e.target.value)} disabled={!versions.length} className={field}>
            {versions.map((v) => (
              <option key={v} value={v}>
                {LABEL[v] ?? v}
              </option>
            ))}
          </select>
        </Field>
        <button type="button" onClick={() => void save()} disabled={busy || !conv.data?.can_write} aria-busy={busy} className={btnPrimary}>
          {busy ? "변환 중..." : "다른 이름으로 저장"}
        </button>
      </div>
      {conv.data && !conv.data.can_write && <ConverterStatus conv={conv} />}
      <Err text={error} />
    </div>
  );
}

type Report = { converter: string; clean: boolean; source_release: string | null; source_bytes: number; dxf_bytes: number; summary: { entity_count: number; layer_count: number; block_count: number }; entities_by_type: Record<string, number>; lost: string[] };

export function Conversion({ revisionId, name }: { revisionId: string; name: string }) {
  const r = useApi<Report>(`/revisions/${encodeURIComponent(revisionId)}/conversion`);
  const [error, setError] = useState<string | null>(null);
  if (!r.data) return null; // a DXF revision has no report (404)
  const d = r.data;
  const kb = (n: number) => `${Math.round(n / 1024).toLocaleString("ko-KR")} KB`;
  return (
    <section aria-labelledby="conv" className={`${card} space-y-2`}>
      <h2 id="conv" className="text-lg font-semibold text-foreground">
        DWG 변환 보고서
      </h2>
      <p className="text-sm text-body">
        원본 AutoCAD {d.source_release ?? "알 수 없음"} DWG ({kb(d.source_bytes)}) → DXF ({kb(d.dxf_bytes)}) · 변환기 {d.converter} {d.clean ? "정상 종료" : "· 오류 보고 — 원본과 비교하세요"} · 엔티티 {d.summary.entity_count} · 레이어 {d.summary.layer_count} · 블록 {d.summary.block_count}
      </p>
      <table className="text-sm">
        <thead>
          <tr>
            <th className={th}>객체</th>
            <th className={`${th} text-right`}>개수</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(d.entities_by_type).map(([k, n]) => (
            <tr key={k} className="border-t border-line">
              <td className={`${td} font-mono`}>{k}</td>
              <td className={`${td} text-right font-mono`}>{n}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {d.lost.length > 0 ? (
        <div className="text-sm">
          <p className="font-medium text-foreground">화면에 표시되지 않는 객체 (원본 DWG에는 남아 있음)</p>
          <ul className="list-disc pl-5 text-body">
            {d.lost.map((w) => (
              <li key={w} className="font-mono">
                {w.startsWith("UNSUPPORTED_ENTITY:") ? w.slice(19) : w === "DXF_XREF_IGNORED" ? "외부 참조(XREF) — 참조 도면은 따로 올리세요" : warnText(w)}
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">빠진 객체 유형이 없습니다.</p>
      )}
      <button
        type="button"
        onClick={() => {
          setError(null);
          download(`/revisions/${encodeURIComponent(revisionId)}/original`, `${name}.dwg`).catch((e) => setError(errText(e)));
        }}
        className={btn2}
      >
        원본 DWG 다운로드
      </button>
      <Err text={error} />
    </section>
  );
}

type ImportItem = { file: string; ok: boolean; document_id?: number; doc_no?: string; warnings?: string[]; code?: string; message?: string };

export function BatchImport({ projectId, onDone }: { projectId: string; onDone: () => void }) {
  const conv = useDwgConverter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [items, setItems] = useState<ImportItem[] | null>(null);
  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const files = (e.currentTarget.elements.namedItem("files") as HTMLInputElement).files;
    if (!files?.length) return setError("DXF·DWG 파일을 고르세요.");
    if (files.length > 20) return setError("한 번에 20개까지 올릴 수 있습니다.");
    const all = Array.from(files);
    if (all.some((x) => x.size > 50 * 1024 * 1024)) return setError("파일마다 50MB 이하여야 합니다.");
    if (all.reduce((t, x) => t + x.size, 0) > 199 * 1024 * 1024) return setError("한 번에 합계 200MB까지 올릴 수 있습니다. 나눠서 올리세요.");
    const f = new FormData();
    for (const file of Array.from(files)) f.append("files", file);
    setBusy(true);
    setError(null);
    try {
      const d = await api<{ items: ImportItem[] }>(`/projects/${encodeURIComponent(projectId)}/drawings/import`, { method: "POST", body: f });
      setItems(d.items);
      onDone();
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section aria-labelledby="batch" className={`${card} space-y-3`}>
      <h2 id="batch" className="text-lg font-semibold text-foreground">
        여러 도면 한 번에 올리기
      </h2>
      <p className="text-sm text-muted-foreground">DXF·DWG 파일마다 도면 문서를 하나씩 만듭니다(도면번호 = 파일 이름, 겹치면 -2, -3). 한 번에 20개(합계 200MB), 파일당 50MB까지. 실패한 파일은 건너뛰고 이유를 보여 줍니다.</p>
      <ConverterStatus conv={conv} />
      <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
        <label htmlFor="batch-files" className="sr-only">
          도면 파일들
        </label>
        <input id="batch-files" name="files" type="file" multiple accept=".dxf,.dwg" disabled={busy} className="text-sm" />
        <button type="submit" disabled={busy} aria-busy={busy} className={btnPrimary}>
          {busy ? "가져오는 중..." : "가져오기"}
        </button>
      </form>
      <Err text={error} />
      {items && (
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th className={th}>파일</th>
              <th className={th}>결과</th>
            </tr>
          </thead>
          <tbody>
            {items.map((i, n) => (
              <tr key={n} className="border-t border-line">
                <td className={td}>{i.file}</td>
                <td className={td}>
                  {i.ok ? (
                    <a href={`/documents/${i.document_id}`} className="text-[var(--color-primary-hover)] underline">
                      {i.doc_no} 생성{i.warnings?.length ? ` · ${i.warnings.map(warnText).join(", ")}` : ""}
                    </a>
                  ) : (
                    <span className="text-red-700">{errText(new ApiError(i.code ?? "ERROR", i.message ?? ""))}</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
