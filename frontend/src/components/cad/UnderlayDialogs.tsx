"use client";

import { useRef, useState } from "react";
import { btn2, btnPrimary, Err, field, Field, Modal } from "@/components/ui";
import { errText } from "@/lib/api";
import type { ImportResult } from "@/lib/cad/underlayVec";
import { uploadUnderlay, type UnderlayFile } from "@/lib/underlayApi";

// 2D underlay U3/U5: attach a PDF/DWFx of the project (PDFATTACH) and turn a page into objects (PDFIMPORT).

export function AttachDialog({ projectId, files, onUploaded, onAttach, onClose }: { projectId: number; files: UnderlayFile[] | null; onUploaded: (f: UnderlayFile) => void; onAttach: (f: UnderlayFile, page: number, insert: [number, number] | null) => void; onClose: () => void }) {
  const [sel, setSel] = useState<string>("");
  const [page, setPage] = useState(1);
  const [at, setAt] = useState({ x: "0", y: "0" });
  const [pick, setPick] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const file = files?.find((f) => f.underlay_id === sel);
  const upload = async (f: File | undefined) => {
    if (!f) return;
    setBusy(true);
    setError(null);
    try {
      const row = await uploadUnderlay(projectId, f);
      onUploaded(row);
      setSel(row.underlay_id);
      setPage(1);
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  };
  const go = () => {
    if (!file) return setError("파일을 고르세요");
    const [x, y] = [Number(at.x), Number(at.y)];
    if (!pick && !(Number.isFinite(x) && Number.isFinite(y))) return setError("삽입점은 숫자로 입력하세요");
    onAttach(file, page - 1, pick ? null : [x, y]);
  };
  return (
    <Modal title="PDF / DWFx 부착 (PDFATTACH)" onClose={onClose} wide>
      <div className="space-y-3">
        <p className="text-sm text-muted-foreground">언더레이는 도면에 복사하지 않고 파일을 참조해 위에 그릴 때 쓰는 배경입니다. 벡터 PDF는 끝점·중점·교차점·중심에 스냅할 수 있습니다. 고전 DWF(.dwf)는 PDF나 DWFx로 내보내 올리세요.</p>
        <div className="flex flex-wrap items-center gap-2">
          <input ref={input} type="file" accept=".pdf,.dwfx" className="text-sm" aria-label="새 파일 올리기" onChange={(e) => void upload(e.target.files?.[0])} />
          {busy && <span className="text-sm text-muted-foreground">올리는 중…</span>}
        </div>
        <fieldset className="max-h-56 space-y-1 overflow-y-auto rounded-md border border-line p-2">
          <legend className="px-1 text-sm text-muted-foreground">이 프로젝트의 파일</legend>
          {files === null && <p className="text-sm text-muted-foreground">불러오는 중…</p>}
          {files?.length === 0 && <p className="text-sm text-muted-foreground">올린 파일이 없습니다.</p>}
          {files?.map((f) => (
            <label key={f.underlay_id} className="flex items-center gap-2 text-sm">
              <input type="radio" name="ul-file" checked={sel === f.underlay_id} onChange={() => (setSel(f.underlay_id), setPage(1))} />
              <span className="break-all">{f.name}</span>
              <span className="text-muted-foreground">
                {f.kind === "pdf" ? "PDF" : "DWFx"} · {f.page_count}쪽 · {(f.size_bytes / 1024 / 1024).toFixed(1)} MB
              </span>
            </label>
          ))}
        </fieldset>
        {file && file.page_count > 1 && (
          <Field label="페이지">
            <select className={field} value={page} onChange={(e) => setPage(Number(e.target.value))}>
              {Array.from({ length: file.page_count }, (_, i) => (
                <option key={i} value={i + 1}>
                  {i + 1} ({file.pages[i]?.width.toFixed(0)} × {file.pages[i]?.height.toFixed(0)} mm)
                </option>
              ))}
            </select>
          </Field>
        )}
        <fieldset className="space-y-2">
          <legend className="text-sm text-muted-foreground">삽입점 (페이지의 왼쪽 아래 모서리, mm)</legend>
          <div className="flex gap-2">
            <input className={field} aria-label="삽입 X" inputMode="decimal" disabled={pick} value={at.x} onChange={(e) => setAt({ ...at, x: e.target.value })} />
            <input className={field} aria-label="삽입 Y" inputMode="decimal" disabled={pick} value={at.y} onChange={(e) => setAt({ ...at, y: e.target.value })} />
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={pick} onChange={(e) => setPick(e.target.checked)} />
            부착 후 화면에서 삽입점 클릭
          </label>
        </fieldset>
        <p className="text-xs text-muted-foreground">부착하면 축척 1(인쇄 크기)로 놓입니다. 실제 치수와 맞는지 두 점 맞춤이나 길이로 축척을 보정하세요.</p>
        <Err text={error} />
        <div className="flex justify-end gap-2">
          <button type="button" className={btn2} onClick={onClose}>
            취소
          </button>
          <button type="button" className={btnPrimary} disabled={!file || busy} onClick={go}>
            부착
          </button>
        </div>
      </div>
    </Modal>
  );
}

export type ImportUi = { crossing: boolean; text: boolean; fills: boolean; tol: string; layer: string; region: "page" | "box"; boxSet: boolean };

export function ImportDialog({ ui, setUi, result, layers, onPickBox, onImport, onClose }: { ui: ImportUi; setUi: (u: ImportUi) => void; result: ImportResult | null; layers: string[]; onPickBox: () => void; onImport: () => void; onClose: () => void }) {
  const c = result?.counts;
  const total = c ? c.lines + c.polylines + c.circles + c.arcs + c.texts : 0;
  return (
    <Modal title="객체로 가져오기 (PDFIMPORT)" onClose={onClose}>
      <div className="space-y-3">
        <p className="text-sm text-muted-foreground">벡터를 편집할 수 있는 도면 객체로 바꿉니다. 원본 언더레이는 그대로 남고, 래스터 이미지는 바뀌지 않습니다. 가져온 객체는 한 번의 실행 취소로 되돌릴 수 있습니다.</p>
        <fieldset className="space-y-1">
          <legend className="text-sm text-muted-foreground">범위</legend>
          <label className="flex items-center gap-2 text-sm">
            <input type="radio" checked={ui.region === "page"} onChange={() => setUi({ ...ui, region: "page" })} /> 보이는 페이지 전체
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input type="radio" checked={ui.region === "box"} onChange={() => setUi({ ...ui, region: "box" })} /> 사각형 영역
            <button type="button" className={btn2} onClick={onPickBox}>
              {ui.boxSet ? "다시 지정" : "화면에서 지정"}
            </button>
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={ui.crossing} onChange={(e) => setUi({ ...ui, crossing: e.target.checked })} /> 영역에 걸친 객체도 포함
          </label>
        </fieldset>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={ui.text} onChange={(e) => setUi({ ...ui, text: e.target.checked })} /> 문자 포함
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={ui.fills} onChange={(e) => setUi({ ...ui, fills: e.target.checked })} /> 채워진 도형은 닫힌 외곽선으로 (해치는 만들지 않음)
        </label>
        <Field label="끝점 연결 허용오차 (mm)">
          <input className={field} inputMode="decimal" value={ui.tol} onChange={(e) => setUi({ ...ui, tol: e.target.value })} />
        </Field>
        <Field label="가져올 레이어">
          <input className={field} list="ul-import-layers" value={ui.layer} maxLength={60} onChange={(e) => setUi({ ...ui, layer: e.target.value })} />
          <datalist id="ul-import-layers">
            {layers.map((l) => (
              <option key={l} value={l} />
            ))}
          </datalist>
        </Field>
        <div role="status" className="rounded-md border border-line p-2 text-sm">
          {result?.error ? (
            <span className="text-red-700 dark:text-red-300">{result.error}</span>
          ) : c ? (
            <>
              <p className="font-medium">객체 {total.toLocaleString()}개를 만듭니다</p>
              <p className="text-muted-foreground">
                선 {c.lines} · 폴리라인 {c.polylines} (이어 붙인 선 {c.joined}) · 원 {c.circles} · 호 {c.arcs} · 문자 {c.texts}
                {c.fills ? ` · 채움 외곽선 ${c.fills}` : ""}
                {result && result.skipped ? ` · 영역 밖 ${result.skipped}` : ""}
              </p>
              <p className="text-muted-foreground">곡선은 선분으로 근사한 것이 있을 수 있습니다 (허용오차 약 0.05 mm).</p>
            </>
          ) : (
            <span className="text-muted-foreground">벡터를 읽는 중…</span>
          )}
        </div>
        <div className="flex justify-end gap-2">
          <button type="button" className={btn2} onClick={onClose}>
            닫기
          </button>
          <button type="button" className={btnPrimary} disabled={!result || !!result.error || total === 0 || (ui.region === "box" && !ui.boxSet) || !ui.layer.trim()} onClick={onImport}>
            가져오기
          </button>
        </div>
      </div>
    </Modal>
  );
}
