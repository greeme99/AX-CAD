"use client";

import { useState } from "react";
import { ACI_COLORS, BYLAYER } from "@/lib/cad/edits";
import { validateGeom, type Geom } from "@/lib/cad/geom";
import { HATCH_PATTERNS } from "@/lib/cad/tools";
import type { RenderEntity } from "./CanvasViewport";

type Props = {
  entity: RenderEntity | null;
  count: number;
  layers: { name: string; locked: boolean }[];
  linetypes: string[]; // the drawing's linetype names
  lockedLayer: boolean; // entity's own layer is locked
  onApply: (handle: string, patch: { layer?: string; geom?: Geom; aci?: number; linetype?: string }) => void;
  onMessage: (msg: string) => void;
};

// [label, key, index] -> numeric field inside the geom
const FIELDS: Record<string, [string, string, number?][]> = {
  LINE: [["시작 X", "start", 0], ["시작 Y", "start", 1], ["끝 X", "end", 0], ["끝 Y", "end", 1]],
  CIRCLE: [["중심 X", "center", 0], ["중심 Y", "center", 1], ["반지름", "radius"]],
  ARC: [["중심 X", "center", 0], ["중심 Y", "center", 1], ["반지름", "radius"], ["시작각(°)", "start_angle"], ["끝각(°)", "end_angle"]],
  TEXT: [["삽입 X", "insert", 0], ["삽입 Y", "insert", 1], ["높이", "height"], ["회전(°)", "rotation"]],
  HATCH: [["축척", "scale"], ["각도(°)", "angle"]],
  MULTILEADER: [["문자 높이", "height"]],
  POINT: [["X", "point", 0], ["Y", "point", 1]],
  ELLIPSE: [["중심 X", "center", 0], ["중심 Y", "center", 1], ["장축 X", "major", 0], ["장축 Y", "major", 1], ["단축/장축 비", "ratio"]],
  INSERT: [["삽입 X", "insert", 0], ["삽입 Y", "insert", 1], ["X 축척", "xscale"], ["Y 축척", "yscale"], ["회전(°)", "rotation"]],
};

const read = (g: Geom, key: string, i?: number) => {
  const v = (g as unknown as Record<string, unknown>)[key];
  return String(i === undefined ? v : (v as number[])[i]);
};

const input =
  "w-full rounded-md border border-line bg-background px-2 py-1 text-sm text-foreground focus-visible:outline-2 focus-visible:outline-ring";

export default function PropertyInspector({ entity, count, layers, linetypes, lockedLayer, onApply, onMessage }: Props) {
  return (
    <aside aria-label="속성" className="w-80 shrink-0 overflow-y-auto border-l border-line bg-card p-3">
      <h2 className="mb-2 text-sm font-semibold text-foreground">속성</h2>
      {count === 0 ? (
        <div className="rounded-lg border border-dashed border-line bg-background/50 p-3 text-xs text-muted-foreground space-y-1.5">
          <p className="font-medium text-body">선택된 객체가 없습니다.</p>
          <p>도면 객체를 클릭하거나 좌→우(포함) / 우→좌(교차) 드래그하여 속성을 확인하세요.</p>
        </div>
      ) : count > 1 || !entity ? (
        <p className="text-sm text-muted-foreground">{count}개 객체 선택됨</p>
      ) : (
        // key resets the draft inputs whenever the entity (or its geometry) changes
        <Form key={entity.handle + JSON.stringify(entity.geom) + entity.layer} {...{ entity, layers, linetypes, lockedLayer, onApply, onMessage }} />
      )}
    </aside>
  );
}

function Form({ entity, layers, linetypes, lockedLayer, onApply, onMessage }: Omit<Props, "count"> & { entity: RenderEntity }) {
  const g = entity.geom;
  const defs = g ? (FIELDS[g.type] ?? []) : [];
  const [draft, setDraft] = useState<string[]>(() => (g ? defs.map(([, k, i]) => read(g, k, i)) : []));
  const [value, setValue] = useState(g?.type === "TEXT" || g?.type === "MULTILEADER" ? g.value : g?.type === "HATCH" ? g.pattern : "");

  const apply = () => {
    if (!g) return;
    if (lockedLayer) return onMessage("잠긴 레이어의 객체는 수정할 수 없습니다");
    const next = structuredClone(g) as unknown as Record<string, unknown>;
    for (let n = 0; n < defs.length; n++) {
      const num = draft[n].trim() === "" ? NaN : Number(draft[n]);
      if (!Number.isFinite(num)) return onMessage(`${defs[n][0]}: 숫자를 입력하세요`);
      const [, key, idx] = defs[n];
      if (idx === undefined) next[key] = num;
      else (next[key] as number[])[idx] = num;
    }
    if (g.type === "TEXT" || g.type === "MULTILEADER") next.value = value;
    if (g.type === "HATCH") next.pattern = value;
    const err = validateGeom(next as unknown as Geom);
    if (err) return onMessage(err);
    onApply(entity.handle, { geom: next as unknown as Geom });
  };

  return (
    <div className="space-y-3 text-sm">
      <dl className="grid grid-cols-[5rem_1fr] gap-y-1">
        <dt className="text-muted-foreground">유형</dt>
        <dd className="font-mono text-foreground">{entity.geom?.type ?? entity.type}</dd>
        <dt className="text-muted-foreground">핸들</dt>
        <dd className="font-mono text-foreground">{entity.handle}</dd>
      </dl>
      {!g ? (
        <p className="text-muted-foreground">읽기 전용 (블록/미지원 엔티티)</p>
      ) : (
        <>
          <label className="block">
            <span className="text-muted-foreground">레이어</span>
            <select
              value={entity.layer}
              disabled={lockedLayer}
              onChange={(e) => onApply(entity.handle, { layer: e.target.value })}
              className={input}
            >
              {layers.map((l) => (
                <option key={l.name} value={l.name} disabled={l.locked && l.name !== entity.layer}>
                  {l.name}
                  {l.locked ? " (잠금)" : ""}
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="text-muted-foreground">색상</span>
            <select
              value={entity.aci ?? BYLAYER}
              disabled={lockedLayer}
              onChange={(e) => onApply(entity.handle, { aci: Number(e.target.value) })}
              className={input}
            >
              <option value={BYLAYER}>BYLAYER (레이어 색)</option>
              {ACI_COLORS.map(([aci, label]) => (
                <option key={aci} value={aci}>
                  {aci} {label}
                </option>
              ))}
              {entity.aci !== undefined && entity.aci !== BYLAYER && !ACI_COLORS.some(([a]) => a === entity.aci) && (
                <option value={entity.aci}>{entity.aci === 0 ? "BYBLOCK" : `ACI ${entity.aci}`}</option>
              )}
            </select>
          </label>
          <label className="block">
            <span className="text-muted-foreground">선종류</span>
            <select
              value={(entity.linetype ?? "BYLAYER").toUpperCase() === "BYLAYER" ? "BYLAYER" : entity.linetype}
              disabled={lockedLayer}
              onChange={(e) => onApply(entity.handle, { linetype: e.target.value })}
              className={input}
            >
              <option value="BYLAYER">BYLAYER (레이어 선종류)</option>
              {[...new Set([...linetypes.filter((n) => !/^by(layer|block)$/i.test(n)), ...(entity.linetype && !/^bylayer$/i.test(entity.linetype) ? [entity.linetype] : [])])].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
          {g.type === "LWPOLYLINE" ? (
            <p className="font-mono text-foreground">
              닫힘 {g.closed ? "예" : "아니오"} · 꼭짓점 {g.points.length}
            </p>
          ) : (
            <form
              className="space-y-2"
              onSubmit={(e) => {
                e.preventDefault();
                apply();
              }}
            >
              {defs.map(([label], n) => {
                const isDeg = label.includes("(°)");
                const isRatio = label.includes("축척") || label.includes("비");
                const unit = isDeg ? "°" : isRatio ? "" : "mm";
                const cleanLabel = label.replace("(°)", "").trim();
                return (
                  <label key={label} className="block">
                    <span className="text-muted-foreground">{cleanLabel}</span>
                    <div className="relative flex items-center">
                      <input
                        inputMode="decimal"
                        disabled={lockedLayer}
                        value={draft[n]}
                        onChange={(e) => setDraft((d) => d.map((v, j) => (j === n ? e.target.value : v)))}
                        className={`${input} text-right font-mono tabular-nums ${unit ? "pr-9" : ""}`}
                      />
                      {unit && (
                        <span className="pointer-events-none absolute right-2.5 font-mono text-xs text-muted-foreground">
                          {unit}
                        </span>
                      )}
                    </div>
                  </label>
                );
              })}
              {g.type === "HATCH" && (
                <label className="block">
                  <span className="text-muted-foreground">패턴</span>
                  <select disabled={lockedLayer} value={value} onChange={(e) => setValue(e.target.value)} className={input}>
                    {[...new Set([...HATCH_PATTERNS, g.pattern])].map((n) => (
                      <option key={n} value={n}>
                        {n}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {(g.type === "TEXT" || g.type === "MULTILEADER") && (
                <label className="block">
                  <span className="text-muted-foreground">문자열</span>
                  <input disabled={lockedLayer} value={value} onChange={(e) => setValue(e.target.value)} className={input} />
                </label>
              )}
              <button
                type="submit"
                disabled={lockedLayer}
                className="rounded-md border border-line px-3 py-1 text-foreground hover:bg-hover focus-visible:outline-2 focus-visible:outline-ring disabled:opacity-40"
              >
                적용 (Enter)
              </button>
            </form>
          )}
          {lockedLayer && (
            <div
              role="status"
              className="space-y-1 rounded-md border border-[var(--color-cad-warning)]/40 bg-[var(--color-cad-warning)]/12 p-2.5 text-xs text-foreground"
            >
              <p className="font-semibold text-[var(--color-cad-warning)]">잠긴 레이어 (편집 불가)</p>
              <p className="text-muted-foreground">좌측 레이어 패널의 자물쇠 아이콘을 클릭해 잠금을 해제한 뒤 수정하세요.</p>
            </div>
          )}
        </>
      )}
    </div>
  );
}
