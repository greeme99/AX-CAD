"use client";

import { useState } from "react";
import { validateGeom, type Geom } from "@/lib/cad/geom";
import type { RenderEntity } from "./CanvasViewport";

type Props = {
  entity: RenderEntity | null;
  count: number;
  layers: { name: string; locked: boolean }[];
  lockedLayer: boolean; // entity's own layer is locked
  onApply: (handle: string, patch: { layer?: string; geom?: Geom }) => void;
  onMessage: (msg: string) => void;
};

// [label, key, index] -> numeric field inside the geom
const FIELDS: Record<string, [string, string, number?][]> = {
  LINE: [["시작 X", "start", 0], ["시작 Y", "start", 1], ["끝 X", "end", 0], ["끝 Y", "end", 1]],
  CIRCLE: [["중심 X", "center", 0], ["중심 Y", "center", 1], ["반지름", "radius"]],
  ARC: [["중심 X", "center", 0], ["중심 Y", "center", 1], ["반지름", "radius"], ["시작각(°)", "start_angle"], ["끝각(°)", "end_angle"]],
  TEXT: [["삽입 X", "insert", 0], ["삽입 Y", "insert", 1], ["높이", "height"], ["회전(°)", "rotation"]],
};

const read = (g: Geom, key: string, i?: number) => {
  const v = (g as unknown as Record<string, unknown>)[key];
  return String(i === undefined ? v : (v as number[])[i]);
};

const input =
  "w-full rounded-md border border-line bg-background px-2 py-1 text-sm text-foreground focus-visible:outline-2 focus-visible:outline-ring";

export default function PropertyInspector({ entity, count, layers, lockedLayer, onApply, onMessage }: Props) {
  return (
    <aside aria-label="속성" className="w-80 shrink-0 overflow-y-auto border-l border-line bg-card p-3">
      <h2 className="mb-2 text-sm font-semibold text-foreground">속성</h2>
      {count === 0 ? (
        <p className="text-sm text-muted-foreground">선택된 객체가 없습니다.</p>
      ) : count > 1 || !entity ? (
        <p className="text-sm text-muted-foreground">{count}개 객체 선택됨</p>
      ) : (
        // key resets the draft inputs whenever the entity (or its geometry) changes
        <Form key={entity.handle + JSON.stringify(entity.geom) + entity.layer} {...{ entity, layers, lockedLayer, onApply, onMessage }} />
      )}
    </aside>
  );
}

function Form({ entity, layers, lockedLayer, onApply, onMessage }: Omit<Props, "count"> & { entity: RenderEntity }) {
  const g = entity.geom;
  const defs = g ? (FIELDS[g.type] ?? []) : [];
  const [draft, setDraft] = useState<string[]>(() => (g ? defs.map(([, k, i]) => read(g, k, i)) : []));
  const [value, setValue] = useState(g?.type === "TEXT" ? g.value : "");

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
    if (g.type === "TEXT") next.value = value;
    const err = validateGeom(next as unknown as Geom);
    if (err) return onMessage(err);
    onApply(entity.handle, { geom: next as unknown as Geom });
  };

  return (
    <div className="space-y-3 text-sm">
      <dl className="grid grid-cols-[5rem_1fr] gap-y-1">
        <dt className="text-muted-foreground">유형</dt>
        <dd className="font-mono text-foreground">{entity.type}</dd>
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
              {defs.map(([label], n) => (
                <label key={label} className="block">
                  <span className="text-muted-foreground">{label}</span>
                  <input
                    inputMode="decimal"
                    disabled={lockedLayer}
                    value={draft[n]}
                    onChange={(e) => setDraft((d) => d.map((v, j) => (j === n ? e.target.value : v)))}
                    className={`${input} text-right font-mono`}
                  />
                </label>
              ))}
              {g.type === "TEXT" && (
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
          {lockedLayer && <p className="text-muted-foreground">잠긴 레이어: 수정할 수 없습니다.</p>}
        </>
      )}
    </div>
  );
}
