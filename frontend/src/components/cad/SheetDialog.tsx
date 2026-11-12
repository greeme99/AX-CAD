"use client";

import { useState } from "react";
import { btn2, btnPrimary, Err, field, Field, Modal } from "@/components/ui";
import { api, errText } from "@/lib/api";
import { createBody, featureName, isSheet, SHEET_DEFAULT, sheetBaseParams, sheetFlangeParams, type Feature, type SheetDraft, type SheetInfo } from "@/lib/cad/features";
import { useApi } from "@/lib/useApi";

// Track 9 Q3: sheet metal — a base plate from a sketch, or a flange on one of its outline edges
export default function SheetDialog({
  docId,
  features,
  initial,
  onClose,
  onDone,
}: {
  docId: string;
  features: Feature[];
  initial?: Partial<SheetDraft> & { mode?: "BASE" | "FLANGE" };
  onClose: () => void;
  onDone: (id: number) => void;
}) {
  const sheets = features.filter((f) => isSheet(f) && f.visible && f.status === "OK");
  const sketches = features.filter((f) => f.feature_type === "SKETCH" && f.visible);
  const [mode, setMode] = useState<"BASE" | "FLANGE">(initial?.mode ?? (sheets.length ? "FLANGE" : "BASE"));
  const [d, setD] = useState<SheetDraft>({
    ...SHEET_DEFAULT,
    sketch: String(sketches[sketches.length - 1]?.feature_id ?? ""),
    target: String(sheets[sheets.length - 1]?.feature_id ?? ""),
    ...initial,
  });
  const materials = useApi<{ items: { material_code: string; material_name: string | null }[] }>(mode === "BASE" ? "/materials" : null);
  const info = useApi<SheetInfo>(mode === "FLANGE" && d.target ? `/features/${encodeURIComponent(d.target)}/sheet` : null);
  const used = new Set(info.data?.flanges.map((f) => f.edge) ?? []);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof SheetDraft) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setD({ ...d, [k]: e.target.value });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const p = mode === "BASE" ? sheetBaseParams(d) : sheetFlangeParams(d, info.data?.edges ?? []);
    if (typeof p === "string") return setError(p);
    setBusy(true);
    setError(null);
    try {
      const f = await api<Feature>(`/documents/${encodeURIComponent(docId)}/features`, { method: "POST", json: createBody(mode === "BASE" ? "SHEET_BASE" : "SHEET_FLANGE", p) });
      onDone(f.feature_id);
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="판금" onClose={onClose}>
      <form onSubmit={submit} className="space-y-3">
        <div role="radiogroup" aria-label="판금 작업" className="flex gap-2">
          {(["BASE", "FLANGE"] as const).map((m) => (
            <label key={m} className="flex items-center gap-1 text-sm">
              <input type="radio" name="sheet-mode" checked={mode === m} disabled={m === "FLANGE" && !sheets.length} onChange={() => setMode(m)} />
              {m === "BASE" ? "베이스 플랜지 (판 만들기)" : "모서리 플랜지 (절곡)"}
            </label>
          ))}
        </div>
        {mode === "BASE" ? (
          <>
            <p className="text-sm text-muted-foreground">직선으로 닫힌 스케치 외곽(구멍은 원·직선 루프)을 두께만큼 판으로 만듭니다.</p>
            <Field label="스케치">
              <select className={field} value={d.sketch} onChange={set("sketch")}>
                <option value="">선택</option>
                {sketches.map((f) => (
                  <option key={f.feature_id} value={f.feature_id}>
                    {featureName(f)}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="재질 (기준정보 판금 절곡 표)">
              <select className={field} value={d.material} onChange={set("material")}>
                <option value="">지정 안 함</option>
                {materials.data?.items.map((m) => (
                  <option key={m.material_code} value={m.material_code}>
                    {m.material_code}
                    {m.material_name ? ` · ${m.material_name}` : ""}
                  </option>
                ))}
              </select>
            </Field>
            <div className="grid grid-cols-3 gap-2">
              <Field label="두께 T (mm)">
                <input className={field} inputMode="decimal" value={d.thickness} onChange={set("thickness")} />
              </Field>
              <Field label="내측 굽힘 R (mm)">
                <input className={field} inputMode="decimal" placeholder="표 / = T" value={d.radius} onChange={set("radius")} />
              </Field>
              <Field label="K-factor">
                <input className={field} inputMode="decimal" placeholder="표 / 0.44" value={d.k} onChange={set("k")} />
              </Field>
            </div>
          </>
        ) : (
          <>
            <Field label="판금 형상">
              <select className={field} value={d.target} onChange={set("target")}>
                {sheets.map((f) => (
                  <option key={f.feature_id} value={f.feature_id}>
                    {featureName(f)}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="외곽 변 (플랜지가 붙을 곳)">
              <select className={field} value={d.edge} onChange={set("edge")}>
                {info.data?.edges.map((e) => (
                  <option key={e.index} value={e.index} disabled={used.has(e.index)}>
                    #{e.index} · 길이 {e.length.toFixed(1)} mm · ({e.start.map((v) => v.toFixed(0)).join(",")})→({e.end.map((v) => v.toFixed(0)).join(",")}){used.has(e.index) ? " · 사용 중" : ""}
                  </option>
                ))}
              </select>
            </Field>
            <div className="grid grid-cols-2 gap-2">
              <Field label="직선부 길이 L (굽힘 제외, mm)">
                <input className={field} inputMode="decimal" value={d.length} onChange={set("length")} />
              </Field>
              <Field label="굽힘 각도 (deg)">
                <input className={field} inputMode="decimal" value={d.angle} onChange={set("angle")} />
              </Field>
              <Field label="시작 들여쓰기 (mm)">
                <input className={field} inputMode="decimal" value={d.insetStart} onChange={set("insetStart")} />
              </Field>
              <Field label="끝 들여쓰기 (mm)">
                <input className={field} inputMode="decimal" value={d.insetEnd} onChange={set("insetEnd")} />
              </Field>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={d.up} onChange={(e) => setD({ ...d, up: e.target.checked })} />
              위로 굽힘 (해제하면 아래로)
            </label>
            {info.data && (
              <p className="text-xs text-muted-foreground">
                T {info.data.thickness} · R {info.data.bend_radius} · K {Number(info.data.k_factor.toFixed(4))}
                {info.data.bend_source?.startsWith("TABLE") ? " (기준정보 표)" : info.data.bend_source === "DEFAULT" ? " (기본값)" : ""} · 현재 전개 {info.data.flat.metrics.flat_bbox.size.map((v) => v.toFixed(1)).join("×")} mm · 절곡 {info.data.flat.metrics.bend_count}
              </p>
            )}
            {info.error && <Err text={errText(info.error)} />}
          </>
        )}
        <Err text={error} />
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className={btn2}>
            취소
          </button>
          <button type="submit" disabled={busy} aria-busy={busy} className={btnPrimary}>
            {busy ? "생성 중..." : "생성"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
