"use client";

import { useState } from "react";
import { btn2, Err } from "@/components/ui";
import { SCALE_LIMITS, normAngle, type UnderlayGeom, type UnderlayRec } from "@/lib/cad/underlay";
import type { PageVec } from "@/lib/cad/underlayVec";
import type { UnderlayFile } from "@/lib/underlayApi";

// 2D underlay U3/U4: the properties of the selected PDF/DWFx reference. The page is never edited:
// everything here is the placement, the look, the clip and the link to the stored file.
export type PickKind = "clip-rect" | "clip-poly" | "align" | "length";
export type PanelCtl = {
  rec: UnderlayRec;
  layer: string;
  layers: { name: string; locked: boolean }[];
  readOnly: boolean; // locked layer, no rights or an unlinked reference
  files: UnderlayFile[] | null;
  vec: PageVec | "loading" | string | null; // string: why the vectors are not there
  imageError?: string;
  hiddenPdf: string[];
  calibrated: boolean;
  picking: { kind: PickKind; step: number } | null;
  measured: number | null; // a picked length in model mm, waiting for the real length
  snapOn: boolean; // UOSNAP and this format's switch
  onChange: (patch: Partial<UnderlayGeom>) => void;
  onLayer: (name: string) => void;
  onTogglePdfLayer: (name: string) => void;
  onReplace: (file: UnderlayFile) => void;
  onPick: (kind: PickKind) => void;
  onCancelPick: () => void;
  onClearClip: () => void;
  onApplyLength: (real: number) => void;
  onImport: () => void;
  onDelete: () => void;
  onLoadFiles: () => void;
};

const input = "w-full rounded-md border border-line bg-background px-2 py-1 text-sm text-foreground focus-visible:outline-2 focus-visible:outline-ring disabled:opacity-50";
const row = "grid grid-cols-[6.5rem_1fr] items-center gap-2 text-sm";
const PICK_PROMPT: Record<PickKind, string[]> = {
  "clip-rect": ["사각형 클립: 첫 모서리를 클릭", "반대쪽 모서리를 클릭"],
  "clip-poly": ["다각형 클립: 점을 차례로 클릭 (Enter 완료, Esc 취소)"],
  align: ["보정: 언더레이 위의 첫 점", "언더레이 위의 둘째 점", "그 첫 점이 놓일 대상 위치", "그 둘째 점이 놓일 대상 위치"],
  length: ["길이: 언더레이 위의 첫 점", "둘째 점"],
};
export const pickPrompt = (kind: PickKind, step: number) => PICK_PROMPT[kind][Math.min(step, PICK_PROMPT[kind].length - 1)];

/** A number field that applies on Enter or when it loses focus (not on every key), and says no to nonsense. */
function Num({ label, value, min, max, step, disabled, onApply, unit }: { label: string; value: number; min?: number; max?: number; step?: number; disabled?: boolean; onApply: (v: number) => void; unit?: string }) {
  const [draft, setDraft] = useState<string | null>(null);
  const shown = draft ?? String(Math.round(value * 1e6) / 1e6);
  const done = () => {
    if (draft === null) return;
    const v = Number(draft);
    setDraft(null);
    if (draft.trim() !== "" && Number.isFinite(v) && v !== value && (min === undefined || v >= min) && (max === undefined || v <= max)) onApply(v);
  };
  return (
    <label className={row}>
      <span className="text-muted-foreground">
        {label}
        {unit ? ` (${unit})` : ""}
      </span>
      <input
        className={input}
        inputMode="decimal"
        step={step}
        disabled={disabled}
        value={shown}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={done}
        onKeyDown={(e) => e.key === "Enter" && (e.currentTarget.blur(), e.preventDefault())}
      />
    </label>
  );
}

export default function UnderlayPanel({ ctl }: { ctl: PanelCtl }) {
  const { rec, readOnly } = ctl;
  const [real, setReal] = useState("");
  const [file, setFile] = useState("");
  const vec = typeof ctl.vec === "object" && ctl.vec ? ctl.vec : null;
  const others = (ctl.files ?? []).filter((f) => f.sha256 !== rec.sha256 && (f.kind === "pdf") === (rec.format === "pdf"));
  const sc = rec.scale;
  return (
    <aside aria-label="언더레이 속성" className="w-80 shrink-0 space-y-3 overflow-y-auto border-l border-line bg-card p-3">
      <h2 className="text-sm font-semibold text-foreground">언더레이 ({rec.format === "pdf" ? "PDF" : "DWFx"})</h2>
      <div className="space-y-1 text-sm">
        <p className="break-all font-medium text-foreground">{rec.name}</p>
        {rec.size && (
          <p className="text-muted-foreground">
            페이지 {rec.page + 1}/{rec.page_count ?? "?"} · {rec.size[0].toFixed(1)} × {rec.size[1].toFixed(1)} mm
          </p>
        )}
        {!rec.linked && <p className="rounded-md bg-amber-500/10 p-2 text-amber-800 dark:text-amber-300">이 프로젝트의 파일과 연결되지 않았습니다. 다른 프로그램이 가리키는 경로이며, 이름만 표시하고 편집하지 않습니다 (삭제만 가능).</p>}
        {rec.linked && !rec.size && <p className="rounded-md bg-amber-500/10 p-2 text-amber-800 dark:text-amber-300">참조 파일이 프로젝트에서 지워졌습니다. 아래에서 다른 파일로 바꾸세요.</p>}
        {!rec.uniform && <p className="text-muted-foreground">가로·세로 축척이 달랐습니다: 가로 축척으로 표시합니다.</p>}
        {ctl.imageError && <Err text={ctl.imageError} />}
      </div>

      {rec.linked && (
        <>
          <section className="space-y-2" aria-label="배치">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">배치</h3>
            {rec.page_count && rec.page_count > 1 && (
              <label className={row}>
                <span className="text-muted-foreground">페이지</span>
                <select className={input} disabled={readOnly} value={rec.page} onChange={(e) => ctl.onChange({ page: Number(e.target.value) })}>
                  {Array.from({ length: rec.page_count }, (_, i) => (
                    <option key={i} value={i}>
                      {i + 1}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <Num label="삽입 X" unit="mm" value={rec.insert[0]} disabled={readOnly} onApply={(v) => ctl.onChange({ insert: [v, rec.insert[1]] })} />
            <Num label="삽입 Y" unit="mm" value={rec.insert[1]} disabled={readOnly} onApply={(v) => ctl.onChange({ insert: [rec.insert[0], v] })} />
            <Num label="축척" value={sc} min={SCALE_LIMITS.min} max={SCALE_LIMITS.max} disabled={readOnly} onApply={(v) => ctl.onChange({ scale: v })} />
            <Num label="회전" unit="°" value={rec.rotation} disabled={readOnly} onApply={(v) => ctl.onChange({ rotation: normAngle(v) })} />
            <p className="text-xs text-muted-foreground">축척 = 도면 mm / 페이지 mm. 1이면 인쇄된 크기 그대로입니다.</p>
            {sc === 1 && !ctl.calibrated && (
              <p role="status" className="rounded-md bg-amber-500/10 p-2 text-xs text-amber-800 dark:text-amber-300">
                축척이 확정되지 않았습니다. PDF는 실제 치수가 아니라 인쇄 크기일 수 있으니, 아는 길이로 보정하세요.
              </p>
            )}
          </section>

          <section className="space-y-2" aria-label="보정">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">보정 (UALIGN)</h3>
            <div className="flex flex-wrap gap-2">
              <button type="button" className={btn2} disabled={readOnly} onClick={() => ctl.onPick("align")} title="언더레이 위의 두 점을 도면의 두 위치에 맞춰 위치·축척·회전을 한 번에 보정">
                두 점 맞춤
              </button>
              <button type="button" className={btn2} disabled={readOnly} onClick={() => ctl.onPick("length")} title="언더레이 위의 두 점을 찍고 실제 길이를 입력해 축척만 보정">
                길이로 축척
              </button>
            </div>
            {ctl.measured !== null && (
              <form
                className="flex items-end gap-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  const v = Number(real);
                  if (v > 0) {
                    ctl.onApplyLength(v);
                    setReal("");
                  }
                }}
              >
                <label className="flex-1 text-sm">
                  <span className="text-muted-foreground">찍은 길이 {ctl.measured.toFixed(2)} mm → 실제 길이 (mm)</span>
                  <input className={input} inputMode="decimal" value={real} onChange={(e) => setReal(e.target.value)} />
                </label>
                <button type="submit" className={btn2}>
                  적용
                </button>
              </form>
            )}
          </section>

          <section className="space-y-2" aria-label="표시">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">표시</h3>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={rec.flags.on} disabled={readOnly} onChange={(e) => ctl.onChange({ flags: { ...rec.flags, on: e.target.checked } })} />
              표시
            </label>
            <label className={row}>
              <span className="text-muted-foreground">흐리게 {rec.fade}%</span>
              <input type="range" min={0} max={80} value={rec.fade} disabled={readOnly} onChange={(e) => ctl.onChange({ fade: Number(e.target.value) })} />
            </label>
            <label className={row}>
              <span className="text-muted-foreground">대비 {rec.contrast}</span>
              <input type="range" min={0} max={100} value={rec.contrast} disabled={readOnly} onChange={(e) => ctl.onChange({ contrast: Number(e.target.value) })} />
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={rec.flags.monochrome} disabled={readOnly} onChange={(e) => ctl.onChange({ flags: { ...rec.flags, monochrome: e.target.checked } })} />
              흑백
            </label>
            <label className="flex items-center gap-2 text-sm" title="어두운 화면에서 종이를 어둡게, 선을 밝게 바꿔 보여 줍니다">
              <input type="checkbox" checked={rec.flags.adjust} disabled={readOnly} onChange={(e) => ctl.onChange({ flags: { ...rec.flags, adjust: e.target.checked } })} />
              배경에 맞춰 색 조정
            </label>
          </section>

          <section className="space-y-2" aria-label="클립">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">클립 (UCLIP)</h3>
            <div className="flex flex-wrap gap-2">
              <button type="button" className={btn2} disabled={readOnly} onClick={() => ctl.onPick("clip-rect")}>
                사각형
              </button>
              <button type="button" className={btn2} disabled={readOnly} onClick={() => ctl.onPick("clip-poly")}>
                다각형
              </button>
              <button type="button" className={btn2} disabled={readOnly || !rec.clip} onClick={ctl.onClearClip}>
                해제
              </button>
            </div>
            {rec.clip && (
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={!rec.flags.inside} disabled={readOnly} onChange={(e) => ctl.onChange({ flags: { ...rec.flags, inside: !e.target.checked } })} />
                클립 반전 (안쪽을 숨김)
              </label>
            )}
            {rec.clip && <p className="text-xs text-muted-foreground">{rec.clip.kind === "rect" ? "사각형" : `다각형 ${rec.clip.points.length}점`} 클립 — 이동·회전·축척에 따라 함께 움직입니다.</p>}
          </section>

          <section className="space-y-2" aria-label="스냅과 객체">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">스냅 · 객체</h3>
            {ctl.vec === "loading" && <p className="text-sm text-muted-foreground">벡터를 읽는 중…</p>}
            {typeof ctl.vec === "string" && ctl.vec !== "loading" && <Err text={ctl.vec} />}
            {vec?.raster_only && <p className="rounded-md bg-amber-500/10 p-2 text-sm text-amber-800 dark:text-amber-300">스캔(래스터) 페이지입니다: 보이기만 하고 스냅·객체 가져오기는 할 수 없습니다. 기준선은 직접 그려서 쓰세요.</p>}
            {vec && !vec.raster_only && (
              <p className="text-sm text-muted-foreground">
                선 {vec.paths.length.toLocaleString()} · 원/호 {vec.arcs.length.toLocaleString()} · 문자 {vec.texts.length.toLocaleString()} — {ctl.snapOn ? "스냅 켜짐" : "스냅 꺼짐 (UOSNAP/PDFOSNAP/DWFOSNAP, 숨김·잠긴 레이어)"}
                {vec.truncated ? " · 너무 커서 일부만 스냅합니다" : ""}
              </p>
            )}
            {vec && vec.layers.length > 0 && (
              <fieldset className="space-y-1">
                <legend className="text-sm text-muted-foreground">PDF 레이어 (이 화면에서만)</legend>
                {vec.layers.map((n) => (
                  <label key={n} className="flex items-center gap-2 text-sm">
                    <input type="checkbox" checked={!ctl.hiddenPdf.includes(n)} onChange={() => ctl.onTogglePdfLayer(n)} />
                    <span className="break-all">{n}</span>
                  </label>
                ))}
              </fieldset>
            )}
            <button type="button" className={btn2} disabled={readOnly || !vec || vec.raster_only} onClick={ctl.onImport} title="선택한 영역의 벡터를 편집할 수 있는 선·폴리라인·원·호·문자로 바꿉니다 (PDFIMPORT)">
              객체로 가져오기…
            </button>
          </section>

          <section className="space-y-2" aria-label="파일">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">파일</h3>
            <div className="flex gap-2">
              <select className={input} disabled={readOnly} value={file} onFocus={ctl.onLoadFiles} onChange={(e) => setFile(e.target.value)} aria-label="다른 파일로 바꾸기">
                <option value="">다른 파일로 바꾸기…</option>
                {others.map((f) => (
                  <option key={f.underlay_id} value={f.underlay_id}>
                    {f.name} ({f.page_count}쪽)
                  </option>
                ))}
              </select>
              <button
                type="button"
                className={btn2}
                disabled={readOnly || !file}
                onClick={() => {
                  const f = others.find((x) => x.underlay_id === file);
                  if (f) ctl.onReplace(f);
                  setFile("");
                }}
              >
                바꾸기
              </button>
            </div>
            <p className="text-xs text-muted-foreground">파일은 내용(해시)으로 저장됩니다. 원본을 고쳤다면 새 파일로 올려 여기서 바꾸세요.</p>
          </section>

          <label className={row}>
            <span className="text-muted-foreground">도면 레이어</span>
            <select className={input} disabled={readOnly} value={ctl.layer} onChange={(e) => ctl.onLayer(e.target.value)}>
              {ctl.layers.filter((l) => !l.locked || l.name === ctl.layer).map((l) => (
                <option key={l.name} value={l.name}>
                  {l.name}
                </option>
              ))}
            </select>
          </label>
        </>
      )}

      {ctl.picking && (
        <div role="status" className="rounded-md border border-line p-2 text-sm">
          <p>{pickPrompt(ctl.picking.kind, ctl.picking.step)}</p>
          <button type="button" className={`${btn2} mt-2`} onClick={ctl.onCancelPick}>
            취소 (Esc)
          </button>
        </div>
      )}
      <button type="button" className={btn2} disabled={!rec.linked ? false : readOnly} onClick={ctl.onDelete}>
        언더레이 삭제
      </button>
    </aside>
  );
}
