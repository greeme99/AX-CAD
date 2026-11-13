"use client";

import { useState } from "react";
import { btn2, btnPrimary, Err, field, Field, Modal } from "@/components/ui";
import { api, errText } from "@/lib/api";
import { createBody, featureName, isSheet, SHEET_CUT_DEFAULT, SHEET_DEFAULT, SHEET_FORM_LABEL, SHEET_MAX_ANGLE, SHEET_RELIEF_LABEL, SHEET_STYLE_LABEL, SHEET_STYLE_PRESET, sheetBaseParams, sheetConvertParams, sheetCutParams, sheetFlangeParams, sheetFormParams, sheetSharpParams, sheetSides, type Feature, type SheetCutDraft, type SheetDraft, type SheetFormDraft, type SheetFormType, type SheetInfo, type SheetReliefType, type SheetStyle } from "@/lib/cad/features";
import { useApi } from "@/lib/useApi";

type Mode = "BASE" | "FLANGE" | "CUT" | "FORM" | "UNFOLD" | "REFOLD" | "CONVERT" | "SHARP";
const MODE_LABEL: Record<Mode, string> = { BASE: "베이스 플랜지 (판 만들기)", FLANGE: "플랜지 · 헴 · 조그 (절곡)", CUT: "컷 (스케치로 따내기)", FORM: "성형 (엠보스 · 루버 등)", UNFOLD: "전개 (펼쳐서 컷)", REFOLD: "다시 접기", CONVERT: "솔리드 → 판금 변환", SHARP: "날카로운 모서리 솔리드 → 판금" };
const MODE_TYPE: Record<Mode, Feature["feature_type"]> = { BASE: "SHEET_BASE", FLANGE: "SHEET_FLANGE", CUT: "SHEET_CUT", FORM: "SHEET_FORM", UNFOLD: "SHEET_UNFOLD", REFOLD: "SHEET_REFOLD", CONVERT: "SHEET_CONVERT", SHARP: "SHEET_CONVERT_SHARP" };
const ON_SHEET: Mode[] = ["FLANGE", "CUT", "FORM", "UNFOLD", "REFOLD"];

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
  initial?: Partial<SheetDraft> & { mode?: Mode };
  onClose: () => void;
  onDone: (id: number) => void;
}) {
  const sheets = features.filter((f) => isSheet(f) && f.visible && f.status === "OK");
  const sketches = features.filter((f) => f.feature_type === "SKETCH" && f.visible);
  // bodies that are not sheet metal yet (plan §23 S3: an imported sheet metal part becomes one)
  const solids = features.filter((f) => !isSheet(f) && f.feature_type !== "SKETCH" && f.feature_type !== "STANDARD" && f.visible && f.status === "OK");
  const [mode, setMode] = useState<Mode>(initial?.mode ?? (sheets.length ? "FLANGE" : "BASE"));
  const [cut, setCut] = useState<SheetCutDraft>({ ...SHEET_CUT_DEFAULT, target: String(sheets[sheets.length - 1]?.feature_id ?? ""), sketch: String(sketches[sketches.length - 1]?.feature_id ?? "") });
  const [conv, setConv] = useState({ target: String(solids[solids.length - 1]?.feature_id ?? ""), thickness: "", k: "" });
  const [sharp, setSharp] = useState({ target: String(solids[solids.length - 1]?.feature_id ?? ""), radius: "2", k: "" });
  const [form, setForm] = useState<SheetFormDraft>({ target: String(sheets[sheets.length - 1]?.feature_id ?? ""), sketch: String(sketches[sketches.length - 1]?.feature_id ?? ""), type: "EMBOSS", depth: "3", up: true });
  const [onSheet, setOnSheet] = useState(String(sheets[sheets.length - 1]?.feature_id ?? ""));
  const [d, setD] = useState<SheetDraft>({
    ...SHEET_DEFAULT,
    sketch: String(sketches[sketches.length - 1]?.feature_id ?? ""),
    target: String(sheets[sheets.length - 1]?.feature_id ?? ""),
    ...initial,
  });
  const materials = useApi<{ items: { material_code: string; material_name: string | null }[] }>(mode === "BASE" ? "/materials" : null);
  const info = useApi<SheetInfo>(mode === "FLANGE" && d.target ? `/features/${encodeURIComponent(d.target)}/sheet` : null);
  const used = new Set(info.data?.flanges.map((f) => f.edge) ?? []);
  const sides = sheetSides(info.data);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof SheetDraft) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setD({ ...d, [k]: e.target.value });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const p =
      mode === "BASE" ? sheetBaseParams(d)
      : mode === "CUT" ? sheetCutParams(cut)
      : mode === "CONVERT" ? sheetConvertParams(conv)
      : mode === "SHARP" ? sheetSharpParams(sharp)
      : mode === "FORM" ? sheetFormParams(form)
      : mode === "UNFOLD" || mode === "REFOLD" ? (Number(onSheet) >= 1 ? { target_feature_id: Number(onSheet) } : "판금 형상을 선택하세요.")
      : sheetFlangeParams(d, info.data?.edges ?? [], info.data);
    if (typeof p === "string") return setError(p);
    setBusy(true);
    setError(null);
    try {
      const f = await api<Feature>(`/documents/${encodeURIComponent(docId)}/features`, { method: "POST", json: createBody(MODE_TYPE[mode], p) });
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
        <div role="radiogroup" aria-label="판금 작업" className="flex flex-wrap gap-x-3 gap-y-1">
          {(Object.keys(MODE_LABEL) as Mode[]).map((m) => (
            <label key={m} className="flex items-center gap-1 text-sm">
              <input type="radio" name="sheet-mode" checked={mode === m} disabled={(ON_SHEET.includes(m) && !sheets.length) || ((m === "CONVERT" || m === "SHARP") && !solids.length)} onChange={() => setMode(m)} />
              {MODE_LABEL[m]}
            </label>
          ))}
        </div>
        {mode === "UNFOLD" || mode === "REFOLD" ? (
          <>
            <p className="text-sm text-muted-foreground">
              {mode === "UNFOLD"
                ? "판을 전개도로 펼쳐 보여 줍니다. 이 뒤에 XY 평면 스케치로 만든 컷은 전개도 위에 그대로 뚫리고, '다시 접기' 뒤에는 접힌 판의 같은 자리에 남습니다."
                : "전개 상태를 끝내고 접힌 판으로 돌아갑니다. 이 뒤의 컷은 다시 접힌 판 기준입니다."}
            </p>
            <Field label="판금 형상">
              <select className={field} value={onSheet} onChange={(e) => setOnSheet(e.target.value)}>
                {sheets.map((f) => (
                  <option key={f.feature_id} value={f.feature_id}>
                    {featureName(f)}
                  </option>
                ))}
              </select>
            </Field>
          </>
        ) : mode === "FORM" ? (
          <>
            <p className="text-sm text-muted-foreground">베이스 판과 같은 평면의 스케치 윤곽을 판 두께를 유지한 채 눌러 올립니다(내리기). 브릿지·루버·랜스는 축에 나란한 사각형으로 그리며, 높이가 판 두께 이상이어야 합니다. 전개도는 성형 전 기준입니다.</p>
            <Field label="판금 형상">
              <select className={field} value={form.target} onChange={(e) => setForm({ ...form, target: e.target.value })}>
                {sheets.map((f) => (
                  <option key={f.feature_id} value={f.feature_id}>
                    {featureName(f)}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="성형 윤곽 스케치">
              <select className={field} value={form.sketch} onChange={(e) => setForm({ ...form, sketch: e.target.value })}>
                <option value="">선택</option>
                {sketches.map((f) => (
                  <option key={f.feature_id} value={f.feature_id}>
                    {featureName(f)}
                  </option>
                ))}
              </select>
            </Field>
            <div className="grid grid-cols-2 gap-2">
              <Field label="종류">
                <select className={field} value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value as SheetFormType })}>
                  {(Object.keys(SHEET_FORM_LABEL) as SheetFormType[]).map((k) => (
                    <option key={k} value={k}>
                      {SHEET_FORM_LABEL[k]}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="높이 (mm)">
                <input className={field} inputMode="decimal" value={form.depth} onChange={(e) => setForm({ ...form, depth: e.target.value })} />
              </Field>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={form.up} onChange={(e) => setForm({ ...form, up: e.target.checked })} />
              위로 성형 (해제하면 아래로)
            </label>
          </>
        ) : mode === "SHARP" ? (
          <>
            <p className="text-sm text-muted-foreground">두께가 일정하고 절곡이 날카로운 모서리(R 없음)인 몸체에 굽힘 R을 주어 판금으로 바꿉니다.</p>
            <Field label="몸체">
              <select className={field} value={sharp.target} onChange={(e) => setSharp({ ...sharp, target: e.target.value })}>
                {solids.map((f) => (
                  <option key={f.feature_id} value={f.feature_id}>
                    {featureName(f)}
                  </option>
                ))}
              </select>
            </Field>
            <div className="grid grid-cols-2 gap-2">
              <Field label="내측 굽힘 R (mm)">
                <input className={field} inputMode="decimal" value={sharp.radius} onChange={(e) => setSharp({ ...sharp, radius: e.target.value })} />
              </Field>
              <Field label="K-factor">
                <input className={field} inputMode="decimal" placeholder="0.44" value={sharp.k} onChange={(e) => setSharp({ ...sharp, k: e.target.value })} />
              </Field>
            </div>
          </>
        ) : mode === "CUT" ? (
          <>
            <p className="text-sm text-muted-foreground">스케치의 닫힌 윤곽을 접힌 판에서 스케치 면에 수직으로 따냅니다. 절곡을 가로지르는 컷도 전개도에 레이저가 자를 위치로 나옵니다.</p>
            <Field label="판금 형상">
              <select className={field} value={cut.target} onChange={(e) => setCut({ ...cut, target: e.target.value })}>
                {sheets.map((f) => (
                  <option key={f.feature_id} value={f.feature_id}>
                    {featureName(f)}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="컷 스케치">
              <select className={field} value={cut.sketch} onChange={(e) => setCut({ ...cut, sketch: e.target.value })}>
                <option value="">선택</option>
                {sketches.map((f) => (
                  <option key={f.feature_id} value={f.feature_id}>
                    {featureName(f)}
                  </option>
                ))}
              </select>
            </Field>
            <div className="grid grid-cols-2 gap-2">
              <Field label="깊이">
                <select className={field} value={cut.reach} onChange={(e) => setCut({ ...cut, reach: e.target.value as SheetCutDraft["reach"] })}>
                  <option value="SHEET">스케치 면의 판 두께만</option>
                  <option value="DEPTH">스케치 양쪽으로 깊이 지정</option>
                  <option value="ALL">전체 관통</option>
                </select>
              </Field>
              {cut.reach === "DEPTH" && (
                <Field label="깊이 (mm, 양쪽)">
                  <input className={field} inputMode="decimal" value={cut.depth} onChange={(e) => setCut({ ...cut, depth: e.target.value })} />
                </Field>
              )}
            </div>
          </>
        ) : mode === "CONVERT" ? (
          <>
            <p className="text-sm text-muted-foreground">두께가 일정하고 절곡부가 원통인 몸체(다른 CAD에서 가져온 판금 STEP 등)를 판금으로 바꿔 전개도·DXF·판금 견적을 냅니다. 날카로운 모서리(절곡 R 없음)는 「날카로운 모서리 솔리드 → 판금」을 쓰세요.</p>
            <Field label="몸체">
              <select className={field} value={conv.target} onChange={(e) => setConv({ ...conv, target: e.target.value })}>
                {solids.map((f) => (
                  <option key={f.feature_id} value={f.feature_id}>
                    {featureName(f)}
                  </option>
                ))}
              </select>
            </Field>
            <div className="grid grid-cols-2 gap-2">
              <Field label="두께 T (mm)">
                <input className={field} inputMode="decimal" placeholder="자동 측정" value={conv.thickness} onChange={(e) => setConv({ ...conv, thickness: e.target.value })} />
              </Field>
              <Field label="K-factor">
                <input className={field} inputMode="decimal" placeholder="0.44" value={conv.k} onChange={(e) => setConv({ ...conv, k: e.target.value })} />
              </Field>
            </div>
          </>
        ) : mode === "BASE" ? (

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
            <div className="grid grid-cols-2 gap-2">
              <Field label="형태">
                <select className={field} value={d.style} onChange={(e) => setD({ ...d, style: e.target.value as SheetStyle, ...SHEET_STYLE_PRESET[e.target.value as SheetStyle] })}>
                  {(Object.keys(SHEET_STYLE_LABEL) as SheetStyle[]).map((k) => (
                    <option key={k} value={k}>
                      {SHEET_STYLE_LABEL[k]}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="붙일 곳">
                <select className={field} value={d.side ? "SIDE" : "EDGE"} onChange={(e) => setD({ ...d, side: e.target.value === "SIDE" ? (sides.find((x) => !x.used)?.key ?? "") : "" })}>
                  <option value="EDGE">베이스 외곽 변</option>
                  <option value="SIDE" disabled={!sides.some((x) => !x.used)}>
                    플랜지 옆면 (플랜지 위 플랜지)
                  </option>
                </select>
              </Field>
            </div>
            {d.side ? (
              <Field label="플랜지 옆면">
                <select className={field} value={d.side} onChange={set("side")}>
                  {sides.map((x) => (
                    <option key={x.key} value={x.key} disabled={x.used}>
                      {x.label}
                      {x.used ? " · 사용 중" : ""}
                    </option>
                  ))}
                </select>
              </Field>
            ) : (
              <Field label="외곽 변 (플랜지가 붙을 곳)">
                <select className={field} value={d.edge} onChange={set("edge")}>
                  {info.data?.edges.map((e) => (
                    <option key={e.index} value={e.index} disabled={used.has(e.index)}>
                      #{e.index} · 길이 {e.length.toFixed(1)} mm · ({e.start.map((v) => v.toFixed(0)).join(",")})→({e.end.map((v) => v.toFixed(0)).join(",")}){used.has(e.index) ? " · 사용 중" : ""}
                    </option>
                  ))}
                </select>
              </Field>
            )}
            <div className="grid grid-cols-2 gap-2">
              <Field label="직선부 길이 L (굽힘 제외, mm)">
                <input className={field} inputMode="decimal" value={d.length} onChange={set("length")} />
              </Field>
              <Field label={`굽힘 각도 (1~${SHEET_MAX_ANGLE} deg)`}>
                <input className={field} inputMode="decimal" value={d.angle} onChange={set("angle")} />
              </Field>
              <Field label="시작 들여쓰기 (mm)">
                <input className={field} inputMode="decimal" value={d.insetStart} onChange={set("insetStart")} />
              </Field>
              <Field label="끝 들여쓰기 (mm)">
                <input className={field} inputMode="decimal" value={d.insetEnd} onChange={set("insetEnd")} />
              </Field>
              <Field label="내측 굽힘 R (mm)">
                <input className={field} inputMode="decimal" placeholder="판의 값" value={d.flangeRadius} onChange={set("flangeRadius")} />
              </Field>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={d.up} onChange={(e) => setD({ ...d, up: e.target.checked })} />
              위로 굽힘 (해제하면 아래로)
            </label>
            {d.style === "CONTOUR" ? (
              <Field label="단면 (판 아랫면 선, 한 줄에 u, v — u: 변 바깥쪽, v: 위. 첫 점 0, 0)">
                <textarea className={`${field} h-20 font-mono`} value={d.profile} onChange={(e) => setD({ ...d, profile: e.target.value })} />
              </Field>
            ) : (
              <Field label="추가 굽힘 (한 줄에 하나: 각도, U 또는 D, 직선부 길이[, R])">
                <textarea className={`${field} h-20 font-mono`} placeholder={"예) 90, D, 15"} value={d.steps} onChange={(e) => setD({ ...d, steps: e.target.value })} />
              </Field>
            )}
            {!d.side && (
              <details className="rounded border border-border p-2 text-sm">
                <summary className="cursor-pointer">모서리 처리 (릴리프 · 마이터 · 닫힌 모서리)</summary>
                <div className="mt-2 grid grid-cols-2 gap-2">
                  {(["reliefStart", "reliefEnd"] as const).map((k) => (
                    <Field key={k} label={k === "reliefStart" ? "시작 쪽 릴리프" : "끝 쪽 릴리프"}>
                      <select className={field} value={d[k]} onChange={(e) => setD({ ...d, [k]: e.target.value as "" | SheetReliefType })}>
                        <option value="">없음 (찢김)</option>
                        {(Object.keys(SHEET_RELIEF_LABEL) as SheetReliefType[]).map((t) => (
                          <option key={t} value={t}>
                            {SHEET_RELIEF_LABEL[t]}
                          </option>
                        ))}
                      </select>
                    </Field>
                  ))}
                  <Field label="릴리프 폭 / 지름 (mm)">
                    <input className={field} inputMode="decimal" value={d.reliefWidth} onChange={set("reliefWidth")} />
                  </Field>
                  <Field label="릴리프 깊이 (슬롯, mm)">
                    <input className={field} inputMode="decimal" value={d.reliefDepth} onChange={set("reliefDepth")} />
                  </Field>
                  <Field label="시작 쪽 마이터 간격 (mm)">
                    <input className={field} inputMode="decimal" placeholder="없음" value={d.miterStart} onChange={set("miterStart")} />
                  </Field>
                  <Field label="끝 쪽 마이터 간격 (mm)">
                    <input className={field} inputMode="decimal" placeholder="없음" value={d.miterEnd} onChange={set("miterEnd")} />
                  </Field>
                  <Field label="시작 쪽 닫힌 모서리 연장 (mm)">
                    <input className={field} inputMode="decimal" placeholder="없음 · R+T면 맞닿음" value={d.closeStart} onChange={set("closeStart")} />
                  </Field>
                  <Field label="끝 쪽 닫힌 모서리 연장 (mm)">
                    <input className={field} inputMode="decimal" placeholder="없음 · R+T면 맞닿음" value={d.closeEnd} onChange={set("closeEnd")} />
                  </Field>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">마이터는 이웃 변 플랜지와 만나는 모서리를 반으로 가르는 면으로 플랜지를 잘라 안으로 굽힌 플랜지끼리 겹치지 않게 합니다.</p>
              </details>
            )}
            <p className="text-xs text-muted-foreground">헴은 180° 한 번, 조그는 같은 각도를 반대 방향으로 두 번 굽힙니다. 굽힘 사이에는 직선부가 있어야 하며, 판과 겹치는 형상은 만들 수 없습니다.</p>
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
