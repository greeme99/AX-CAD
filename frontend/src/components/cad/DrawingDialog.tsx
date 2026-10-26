"use client";

import { useState } from "react";
import { btn2, btnPrimary, Err, field, Field, Modal } from "@/components/ui";
import { api, errText, type List } from "@/lib/api";
import { DRAWING_DRAFT, drawingRequest, MAX_EXTRA, PAPERS, ROUGHNESS, TOLERANCE, VIEWS, type Detail, type DrawingDraft, type Section } from "@/lib/cad/drawing";
import { useApi } from "@/lib/useApi";

type Material = { material_code: string };

/** Track 8 P3: a production drawing (DXF revision) from the document's 3D bodies. "다시 생성"
 *  redraws with the settings stored in the drawing; the user's own entities are kept. */
export default function DrawingDialog({ docId, onDone, onClose }: { docId: string; onDone: (revisionId: string) => void; onClose: () => void }) {
  const [d, setD] = useState(DRAWING_DRAFT);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const materials = useApi<List<Material>>("/materials").data?.items ?? [];
  const set = (p: Partial<DrawingDraft>) => setD((v) => ({ ...v, ...p }));

  async function post(json: Record<string, unknown>) {
    setBusy(true);
    setError(null);
    try {
      onDone((await api<{ revision_id: string }>(`/documents/${encodeURIComponent(docId)}/drawing`, { method: "POST", json })).revision_id);
    } catch (e) {
      setError(errText(e));
      setBusy(false);
    }
  }
  function submit(e: React.FormEvent) {
    e.preventDefault();
    const body = drawingRequest(d);
    if (typeof body === "string") return setError(body);
    void post(body);
  }
  const row = "grid grid-cols-[auto_1fr_auto] items-center gap-2";

  return (
    <Modal title="도면 만들기" onClose={onClose} wide>
      <form onSubmit={submit} className="space-y-3 text-sm">
        <fieldset className="flex flex-wrap gap-3">
          <legend className="mb-1 font-medium text-foreground">투상도</legend>
          {VIEWS.map(([v, label]) => (
            <label key={v} className="flex items-center gap-1 text-foreground">
              <input type="checkbox" checked={d.views.includes(v)} onChange={(e) => set({ views: e.target.checked ? [...d.views, v] : d.views.filter((x) => x !== v) })} /> {label}
            </label>
          ))}
        </fieldset>
        <div className="grid grid-cols-4 gap-3">
          <Field label="투상법">
            <select value={d.projection} onChange={(e) => set({ projection: e.target.value as DrawingDraft["projection"] })} className={field}>
              <option value="THIRD">제3각법</option>
              <option value="FIRST">제1각법</option>
            </select>
          </Field>
          <Field label="용지">
            <select value={d.sheet} onChange={(e) => set({ sheet: e.target.value as DrawingDraft["sheet"] })} className={field}>
              {PAPERS.map((p) => (
                <option key={p}>{p}</option>
              ))}
            </select>
          </Field>
          <Field label="방향">
            <select value={d.portrait ? "P" : "L"} onChange={(e) => set({ portrait: e.target.value === "P" })} className={field}>
              <option value="L">가로</option>
              <option value="P">세로</option>
            </select>
          </Field>
          <Field label="축척 (비우면 자동)">
            <input value={d.scale} onChange={(e) => set({ scale: e.target.value })} placeholder="예: 1:2" className={field} />
          </Field>
        </div>

        <fieldset className="space-y-1">
          <legend className="font-medium text-foreground">단면도 (전단면, 평면 위치 mm)</legend>
          {d.sections.map((s, i) => (
            <div key={i} className={row}>
              <span className="w-10 text-muted-foreground">{String.fromCharCode(65 + i)}-{String.fromCharCode(65 + i)}</span>
              <div className="flex gap-2">
                <select aria-label="단면 축" value={s.axis} onChange={(e) => set({ sections: d.sections.map((x, j) => (j === i ? { ...x, axis: e.target.value as Section["axis"] } : x)) })} className={field}>
                  {(["X", "Y", "Z"] as const).map((a) => (
                    <option key={a} value={a}>
                      {a} =
                    </option>
                  ))}
                </select>
                <input aria-label="단면 위치" value={s.offset} onChange={(e) => set({ sections: d.sections.map((x, j) => (j === i ? { ...x, offset: e.target.value } : x)) })} className={field} />
              </div>
              <button type="button" onClick={() => set({ sections: d.sections.filter((_, j) => j !== i) })} className={btn2}>
                삭제
              </button>
            </div>
          ))}
          <button type="button" disabled={d.sections.length >= MAX_EXTRA} onClick={() => set({ sections: [...d.sections, { axis: "Y", offset: "0" }] })} className={btn2}>
            단면 추가
          </button>
        </fieldset>

        <fieldset className="space-y-1">
          <legend className="font-medium text-foreground">상세도 (뷰 좌표: 정면 X,Z · 평면 X,Y · 우측 Y,Z)</legend>
          {d.details.map((x, i) => {
            const put = (p: Partial<Detail>) => set({ details: d.details.map((y, j) => (j === i ? { ...y, ...p } : y)) });
            return (
              <div key={i} className={row}>
                <span className="w-10 text-muted-foreground">{String.fromCharCode(65 + d.sections.length + i)}</span>
                <div className="grid grid-cols-5 gap-2">
                  <select aria-label="상세 뷰" value={x.view} onChange={(e) => put({ view: e.target.value as Detail["view"] })} className={field}>
                    {VIEWS.slice(0, 3).map(([v, label]) => (
                      <option key={v} value={v}>
                        {label}
                      </option>
                    ))}
                  </select>
                  <input aria-label="중심 X" placeholder="중심 X" value={x.x} onChange={(e) => put({ x: e.target.value })} className={field} />
                  <input aria-label="중심 Y" placeholder="중심 Y" value={x.y} onChange={(e) => put({ y: e.target.value })} className={field} />
                  <input aria-label="반지름" placeholder="반지름" value={x.r} onChange={(e) => put({ r: e.target.value })} className={field} />
                  <input aria-label="배율" placeholder="배율" value={x.factor} onChange={(e) => put({ factor: e.target.value })} className={field} />
                </div>
                <button type="button" onClick={() => set({ details: d.details.filter((_, j) => j !== i) })} className={btn2}>
                  삭제
                </button>
              </div>
            );
          })}
          <button type="button" disabled={d.details.length >= MAX_EXTRA} onClick={() => set({ details: [...d.details, { view: "FRONT", x: "0", y: "0", r: "10", factor: "2" }] })} className={btn2}>
            상세 추가
          </button>
        </fieldset>

        <div className="grid grid-cols-3 gap-3">
          <Field label="일반 공차 (KS B ISO 2768-1)">
            <select value={d.tolerance} onChange={(e) => set({ tolerance: e.target.value as DrawingDraft["tolerance"] })} className={field}>
              {Object.entries(TOLERANCE).map(([k, label]) => (
                <option key={k} value={k}>
                  {label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="표면 거칠기 Ra">
            <select value={d.roughness} onChange={(e) => set({ roughness: e.target.value })} className={field}>
              <option value="">표기 안 함</option>
              {ROUGHNESS.map((r) => (
                <option key={r}>{r}</option>
              ))}
            </select>
          </Field>
          <Field label="재질">
            <input list="drawing-materials" maxLength={40} value={d.material} onChange={(e) => set({ material: e.target.value })} placeholder="예: SS400" className={field} />
            <datalist id="drawing-materials">
              {materials.map((m) => (
                <option key={m.material_code} value={m.material_code} />
              ))}
            </datalist>
          </Field>
        </div>
        <label className="flex items-center gap-2 text-foreground">
          <input type="checkbox" checked={d.dimensions} onChange={(e) => set({ dimensions: e.target.checked })} /> 외형 치수·구멍 지시(콜아웃) 넣기
        </label>
        <p className="text-xs text-muted-foreground">새 리비전(DXF)으로 저장됩니다. 다시 생성하면 생성된 레이어(AXGEN_*)만 다시 그리고 직접 그린 객체는 남깁니다.</p>
        <Err text={error} />
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className={btn2}>
            취소
          </button>
          <button type="button" disabled={busy} title="도면에 저장된 설정 그대로 3D 형상에서 다시 그립니다" onClick={() => void post({})} className={btn2}>
            다시 생성 (저장된 설정)
          </button>
          <button type="submit" disabled={busy} aria-busy={busy} className={btnPrimary}>
            {busy ? "만드는 중..." : "도면 만들기"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
