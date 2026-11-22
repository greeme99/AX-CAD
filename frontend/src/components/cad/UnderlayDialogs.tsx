"use client";

import { useRef, useState } from "react";
import { btn2, btnPrimary, Err, field, Field, Modal } from "@/components/ui";
import { errText } from "@/lib/api";
import type { ImportResult } from "@/lib/cad/underlayVec";
import { uploadUnderlay, type UnderlayFile } from "@/lib/underlayApi";

// 2D underlay U3/U5: attach a PDF/DWFx of the project (PDFATTACH) and turn a page into objects (PDFIMPORT).

export type AttachAutoTrace = { autoDimension: boolean; referenceWidthMm?: number };

const IMAGE_EXT_RE = /\.(png|jpe?g|svg|webp|bmp)$/i;
function formatBadge(f: UnderlayFile): string {
  const m = IMAGE_EXT_RE.exec(f.name);
  if (m) return `이미지(${m[1].toUpperCase().replace("JPEG", "JPG")})`;
  return f.kind === "pdf" ? "PDF" : "DWFx";
}

export function AttachDialog({ projectId, files, onUploaded, onAttach, onClose }: { projectId: number; files: UnderlayFile[] | null; onUploaded: (f: UnderlayFile) => void; onAttach: (f: UnderlayFile, page: number, insert: [number, number] | null, autoTrace?: AttachAutoTrace) => void; onClose: () => void }) {
  const [sel, setSel] = useState<string>("");
  const [page, setPage] = useState(1);
  const [at, setAt] = useState({ x: "0", y: "0" });
  const [pick, setPick] = useState(false);
  const [traceAfter, setTraceAfter] = useState(false);
  const [autoDimAfter, setAutoDimAfter] = useState(true);
  const [refWidth, setRefWidth] = useState("");
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
      if (IMAGE_EXT_RE.test(row.name)) setTraceAfter(true);
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
    let traceOpts: AttachAutoTrace | undefined;
    if (traceAfter) {
      const rw = refWidth.trim() ? Number(refWidth.trim()) : undefined;
      if (rw !== undefined && !(Number.isFinite(rw) && rw > 0)) {
        return setError("기준 치수(mm)는 0보다 큰 숫자로 입력하세요");
      }
      traceOpts = { autoDimension: autoDimAfter, referenceWidthMm: rw };
    }
    onAttach(file, page - 1, pick ? null : [x, y], traceOpts);
  };
  return (
    <Modal title="PDF / DWFx / 이미지 부착 (PDFATTACH · IMAGEATTACH)" onClose={onClose} wide>
      <div className="space-y-3">
        <p className="text-sm text-muted-foreground">PDF·DWFx뿐 아니라 이미지(PNG, JPG, SVG, WEBP, BMP)를 2D 도면에 오버레이하고, 자동 따라그리기와 치수 기입을 바로 실행할 수 있습니다. 벡터 PDF·SVG는 끝점·중점·교차점·중심에 스냅됩니다.</p>
        <div className="flex flex-wrap items-center gap-2">
          <input ref={input} type="file" accept=".pdf,.dwfx,.png,.jpg,.jpeg,.svg,.webp,.bmp" className="text-sm" aria-label="새 파일 올리기" onChange={(e) => void upload(e.target.files?.[0])} />
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
                {formatBadge(f)} · {f.page_count}쪽 · {(f.size_bytes / 1024 / 1024).toFixed(1)} MB
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
        <fieldset className="space-y-2 rounded-md border border-line bg-background/50 p-2.5">
          <legend className="px-1 text-xs font-semibold text-muted-foreground">자동 따라그리기 · 치수 기입</legend>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={traceAfter} onChange={(e) => setTraceAfter(e.target.checked)} />
            부착 직후 자동으로 외곽선 따라그리기 실행
          </label>
          {traceAfter && (
            <div className="space-y-2 pl-5">
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={autoDimAfter} onChange={(e) => setAutoDimAfter(e.target.checked)} />
                추출 도형에 치수(가로·세로·지름/반지름) 자동 기입
              </label>
              <Field label="기준 가로 치수 (mm, 선택)">
                <input className={field} inputMode="decimal" placeholder="예: 200 (비우면 원본 크기 기준)" value={refWidth} onChange={(e) => setRefWidth(e.target.value)} />
              </Field>
            </div>
          )}
        </fieldset>
        <p className="text-xs text-muted-foreground">부착하면 축척 1(인쇄 크기)로 놓입니다. 실제 치수와 맞는지 기준 치수 입력이나 두 점 맞춤/길이로 축척을 보정하세요.</p>
        <Err text={error} />
        <div className="flex justify-end gap-2">
          <button type="button" className={btn2} onClick={onClose}>
            취소
          </button>
          <button type="button" className={btnPrimary} disabled={!file || busy} onClick={go}>
            {traceAfter ? "부착 + 자동 따라그리기" : "부착"}
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
