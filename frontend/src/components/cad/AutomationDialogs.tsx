"use client";

import { useEffect, useMemo, useState } from "react";
import { btn2, btnPrimary, Err, field, Field, Modal } from "@/components/ui";
import { api, errText } from "@/lib/api";
import { MAX_SCRIPT_BYTES } from "@/lib/cad/axs";
import { geomsToPreviewSvg, pushRecentBlock, type PlaceOptions } from "@/lib/cad/blockPalette";
import type { Geom } from "@/lib/cad/geom";
import { useApi } from "@/lib/useApi";

// Track 9 Q4: .axs command scripts and parametric blocks in the 2D editor (plan.md §15.5)

// A4: opened with a finished command journal (initial), which can be edited, saved as a file and run
export function ScriptDialog({ initial, fileName = "script.axs", onRun, onLibrary, onClose }: { initial?: string; fileName?: string; onRun: (text: string) => string | null; onLibrary?: (name: string, source: string) => Promise<string>; onClose: () => void }) {
  const [text, setText] = useState(initial ?? "; 예: 사각형과 구멍\nREC\n0,0\n@100,50\nC\n20,25\n4\n");
  const [error, setError] = useState<string | null>(null);
  const [libName, setLibName] = useState(fileName.replace(/\.axs$/i, ""));
  const [saved, setSaved] = useState<string | null>(null);
  const toLibrary = async () => {
    setError(null);
    try {
      setSaved(`라이브러리에 저장: ${await onLibrary!(libName, text)} (명령행: SCRIPT ${libName.replace(/\.axs$/i, "")})`);
    } catch (e) {
      setSaved(null);
      setError(errText(e));
    }
  };
  const saveFile = () => {
    const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = fileName.replace(/[\\/:*?"<>|]/g, "_");
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000); // after the browser has started the download
  };
  const load = (f: File | undefined) => {
    if (!f) return;
    if (f.size > MAX_SCRIPT_BYTES) return setError("스크립트가 너무 큽니다 (1 MB 이하)");
    f.text().then(setText, (e) => setError(errText(e)));
  };
  return (
    <Modal title={initial ? "명령 기록 (.axs)" : "명령 스크립트 (.axs)"} onClose={onClose} wide>
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          const err = onRun(text);
          if (err) setError(err);
          else onClose();
        }}
      >
        <p className="text-sm text-muted-foreground">한 줄에 명령 프롬프트 입력 하나 (빈 줄 = Enter, ^C = 취소, ; 로 시작하면 주석). 전체가 한 번의 실행 취소로 적용되고, 오류가 나면 그 줄을 알려 주고 아무것도 바꾸지 않습니다.</p>
        <Field label="스크립트 파일 열기">
          <input type="file" accept=".axs,.scr,.txt" className="text-sm" onChange={(e) => load(e.target.files?.[0])} />
        </Field>
        <label htmlFor="axs-text" className="sr-only">
          스크립트
        </label>
        <textarea id="axs-text" className={`${field} h-64 font-mono`} value={text} spellCheck={false} onChange={(e) => setText(e.target.value)} />
        <Err text={error} />
        {onLibrary && (
          <div className="flex flex-wrap items-end gap-2">
            <Field label="라이브러리 이름 (.axs)">
              <input className={field} value={libName} maxLength={56} onChange={(e) => setLibName(e.target.value)} />
            </Field>
            <button type="button" className={btn2} onClick={() => void toLibrary()}>
              라이브러리에 저장
            </button>
            {saved && (
              <span role="status" className="text-sm text-muted-foreground">
                {saved}
              </span>
            )}
          </div>
        )}
        <div className="flex justify-end gap-2">
          <button type="button" className={btn2} onClick={onClose}>
            {initial ? "닫기" : "취소"}
          </button>
          <button type="button" className={btn2} onClick={saveFile}>
            파일로 저장 (.axs)
          </button>
          <button type="submit" className={btnPrimary}>
            {initial ? "이 도면에 실행" : "실행"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

type Param = { name: string; label?: string; default: number; min: number; max: number };
type Template = { name: string; label: string; category?: string; base_point?: string; layer_policy?: string; params: Param[]; states: string[]; builtin: boolean };
export type Placed = { name: string; geoms: Geom[]; description: string; values: Record<string, string>; state: string | null };
export type ParamInitial = {
  template: string;
  values: Record<string, string>;
  state: string | null;
  at?: [number, number];
  rotation?: number;
  xscale?: number;
  yscale?: number;
  layer?: string;
};

const BLOCK_CATEGORIES = ["전체", "건축", "창호·문", "가구·퍼니처", "주방", "욕실", "베란다·공용", "오피스", "기계", "배관·공조", "전기", "회사"] as const;
const RECENT_KEY = "axcad:recent_blocks";
const ROTATION_PRESETS = [0, 90, 180, 270] as const;

export function ParamBlockDialog({
  initial,
  layers = [],
  currentLayer,
  onPlace,
  onPickOnCanvas,
  onClose,
}: {
  initial?: ParamInitial;
  layers?: string[];
  currentLayer?: string;
  onPlace: (b: Placed, at: [number, number], opts?: Partial<PlaceOptions>) => void;
  onPickOnCanvas?: (b: Placed, label: string, opts: Partial<PlaceOptions>) => void;
  onClose: () => void;
}) {
  const list = useApi<{ items: Template[] }>("/param-blocks");
  const [cat, setCat] = useState<string>("전체");
  const [q, setQ] = useState("");
  const [name, setName] = useState(initial?.template ?? "DOOR");
  const [recent, setRecent] = useState<string[]>(() => {
    if (typeof window === "undefined") return [];
    try {
      const raw = JSON.parse(window.localStorage.getItem(RECENT_KEY) ?? "[]");
      return Array.isArray(raw) ? raw.filter((x): x is string => typeof x === "string").slice(0, 6) : [];
    } catch {
      return [];
    }
  });
  const query = q.trim().toLowerCase();
  const filtered = (list.data?.items ?? []).filter((x) => {
    if (initial) return true;
    const catOk = cat === "전체" ? true : cat === "회사" ? !x.builtin : (x.category ?? "기계") === cat;
    if (!catOk) return false;
    if (!query) return true;
    return `${x.name} ${x.label} ${x.category ?? ""}`.toLowerCase().includes(query);
  });
  const t = list.data?.items.find((x) => x.name === name);
  const [vals, setVals] = useState<Record<string, string>>(initial?.values ?? {});
  const [state, setState] = useState<string | null>(initial?.state ?? null);
  const [at, setAt] = useState(initial?.at ? initial.at.join(",") : "0,0");

  // Rich insertion options: rotation, scale, mirror flip, target layer, array, continuous mode
  const [rotation, setRotation] = useState(String(initial?.rotation ?? 0));
  const [uniformScale, setUniformScale] = useState(
    initial?.xscale === undefined || initial?.yscale === undefined || Math.abs(initial.xscale) === Math.abs(initial.yscale),
  );
  const [xscale, setXscale] = useState(String(Math.abs(initial?.xscale ?? 1)));
  const [yscale, setYscale] = useState(String(Math.abs(initial?.yscale ?? 1)));
  const [flipX, setFlipX] = useState((initial?.xscale ?? 1) < 0);
  const [flipY, setFlipY] = useState((initial?.yscale ?? 1) < 0);
  const [targetLayer, setTargetLayer] = useState(initial?.layer ?? "");
  const [useArray, setUseArray] = useState(false);
  const [cols, setCols] = useState("2");
  const [rows, setRows] = useState("1");
  const [dx, setDx] = useState("1500");
  const [dy, setDy] = useState("1500");
  const [keepOpen, setKeepOpen] = useState(false);
  const [placedCount, setPlacedCount] = useState(0);

  // Live SVG preview state
  const [previewGeoms, setPreviewGeoms] = useState<Geom[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const pick = (n: string) => {
    setName(n);
    setVals({});
    setState(null);
  };

  // Debounced live geometry preview whenever selected block / params / visibility state changes
  const valsKey = JSON.stringify(vals);
  useEffect(() => {
    if (!t) return;
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      const parsed: Record<string, number> = {};
      const map: Record<string, string> = JSON.parse(valsKey);
      for (const p of t.params) {
        const raw = map[p.name];
        if (raw === undefined || raw.trim() === "") continue;
        const v = Number(raw);
        if (Number.isFinite(v)) parsed[p.name] = v;
      }
      try {
        const b = await api<Placed>(`/param-blocks/${encodeURIComponent(t.name)}/instantiate`, {
          method: "POST",
          json: { values: parsed, state: t.states.length ? (state ?? t.states[0]) : null },
        });
        if (!cancelled) setPreviewGeoms(b.geoms);
      } catch {
        if (!cancelled) setPreviewGeoms(null);
      }
    }, 180);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [t, valsKey, state]);

  const previewSvg = useMemo(
    () => (previewGeoms ? geomsToPreviewSvg(previewGeoms, { rotation: Number(rotation) || 0, flipX, flipY }) : null),
    [previewGeoms, rotation, flipX, flipY],
  );

  function parseOptionsAndValues(): { values: Record<string, number>; opts: Partial<PlaceOptions>; colsNum: number; rowsNum: number; dxNum: number } | null {
    if (!t) return null;
    const rotNum = Number(rotation);
    if (!Number.isFinite(rotNum)) {
      setError("회전 각도에 숫자를 입력하세요");
      return null;
    }
    const sxNum = Number(xscale);
    const syNum = uniformScale ? sxNum : Number(yscale);
    if (!Number.isFinite(sxNum) || sxNum <= 0 || !Number.isFinite(syNum) || syNum <= 0) {
      setError("축척은 0보다 큰 숫자여야 합니다");
      return null;
    }
    const colsNum = useArray && !initial ? Number(cols) : 1;
    const rowsNum = useArray && !initial ? Number(rows) : 1;
    const dxNum = useArray && !initial ? Number(dx) : 0;
    const dyNum = useArray && !initial ? Number(dy) : 0;
    if (!Number.isInteger(colsNum) || colsNum < 1 || colsNum > 20 || !Number.isInteger(rowsNum) || rowsNum < 1 || rowsNum > 20) {
      setError("배열 열/행은 1~20 정수입니다");
      return null;
    }
    if (!Number.isFinite(dxNum) || !Number.isFinite(dyNum)) {
      setError("배열 간격(dx, dy)에 숫자를 입력하세요");
      return null;
    }
    const values: Record<string, number> = {};
    for (const p of t.params) {
      const raw = vals[p.name];
      if (raw === undefined || raw.trim() === "") continue;
      const v = Number(raw);
      if (!Number.isFinite(v)) {
        setError(`${p.label ?? p.name}에 숫자를 입력하세요`);
        return null;
      }
      values[p.name] = v;
    }
    return {
      values,
      colsNum,
      rowsNum,
      dxNum,
      opts: {
        rotation: rotNum,
        xscale: sxNum,
        yscale: syNum,
        flipX,
        flipY,
        layer: targetLayer || undefined,
        cols: colsNum,
        rows: rowsNum,
        dx: dxNum,
        dy: dyNum,
        keepOpen,
      },
    };
  }

  const recordRecent = (tplName: string) => {
    const nextRecent = pushRecentBlock(recent, tplName);
    setRecent(nextRecent);
    try {
      window.localStorage.setItem(RECENT_KEY, JSON.stringify(nextRecent));
    } catch {
      // ignore storage quota errors
    }
  };

  async function startCanvasPick() {
    if (!t || !onPickOnCanvas) return;
    const parsed = parseOptionsAndValues();
    if (!parsed) return;
    setBusy(true);
    setError(null);
    try {
      const b = await api<Placed>(`/param-blocks/${encodeURIComponent(t.name)}/instantiate`, {
        method: "POST",
        json: { values: parsed.values, state: t.states.length ? (state ?? t.states[0]) : null },
      });
      recordRecent(t.name);
      onPickOnCanvas(b, t.label, parsed.opts);
      onClose();
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!t) return;
    const m = /^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$/.exec(at);
    if (!m) return setError("삽입점을 x,y 로 입력하세요");
    const parsed = parseOptionsAndValues();
    if (!parsed) return;
    setBusy(true);
    setError(null);
    try {
      const b = await api<Placed>(`/param-blocks/${encodeURIComponent(t.name)}/instantiate`, {
        method: "POST",
        json: { values: parsed.values, state: t.states.length ? (state ?? t.states[0]) : null },
      });
      onPlace(b, [Number(m[1]), Number(m[2])], parsed.opts);
      recordRecent(t.name);
      if (keepOpen && !initial) {
        setPlacedCount((c) => c + parsed.colsNum * parsed.rowsNum);
        const stepX = useArray ? parsed.dxNum * parsed.colsNum : 1000;
        setAt(`${Number(m[1]) + stepX},${Number(m[2])}`);
      } else {
        onClose();
      }
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={initial ? "표준 · 파라미터 블록 수정" : `표준 · 파라미터 블록 도구 팔레트 (${list.data?.items.length ?? 60}종 · Ctrl+3)`} onClose={onClose} wide>
      <form className="space-y-3" onSubmit={submit}>
        {list.error && <Err text={errText(list.error)} />}
        {!initial && (
          <div className="space-y-2">
            <div className="flex flex-wrap gap-1" role="tablist" aria-label="블록 분야">
              {BLOCK_CATEGORIES.map((c) => (
                <button
                  key={c}
                  type="button"
                  role="tab"
                  aria-selected={cat === c}
                  onClick={() => {
                    setCat(c);
                    const first = (list.data?.items ?? []).find((x) => {
                      const catOk = c === "전체" ? true : c === "회사" ? !x.builtin : (x.category ?? "기계") === c;
                      return catOk && (!query || `${x.name} ${x.label} ${x.category ?? ""}`.toLowerCase().includes(query));
                    });
                    if (first) pick(first.name);
                  }}
                  className={`rounded-md px-2.5 py-1 text-xs font-medium ${
                    cat === c ? "bg-[var(--color-primary)] text-white" : "border border-line bg-card text-foreground hover:bg-hover"
                  }`}
                >
                  {c}
                </button>
              ))}
            </div>
            <input
              type="search"
              className={field}
              placeholder="블록 검색 (예: 침대, 소파, 싱크대, 욕조, 베란다, 현관, 책상, 복합기...)"
              value={q}
              onChange={(e) => {
                const next = e.target.value;
                setQ(next);
                const nq = next.trim().toLowerCase();
                const first = (list.data?.items ?? []).find((x) => {
                  const catOk = cat === "전체" ? true : cat === "회사" ? !x.builtin : (x.category ?? "기계") === cat;
                  return catOk && (!nq || `${x.name} ${x.label} ${x.category ?? ""}`.toLowerCase().includes(nq));
                });
                if (first) pick(first.name);
              }}
            />
            {recent.length > 0 && (
              <div className="flex flex-wrap items-center gap-1 text-xs">
                <span className="text-muted-foreground">최근 사용:</span>
                {recent.map((rn) => {
                  const item = list.data?.items.find((x) => x.name === rn);
                  if (!item) return null;
                  return (
                    <button
                      key={rn}
                      type="button"
                      onClick={() => {
                        setCat("전체");
                        setQ("");
                        pick(rn);
                      }}
                      className={`rounded px-2 py-0.5 border text-[11px] ${
                        name === rn ? "border-[var(--color-primary)] bg-[var(--color-primary-light)] font-semibold" : "border-line bg-card hover:bg-hover"
                      }`}
                    >
                      {item.label.split("(")[0].trim()}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        )}

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_170px]">
          <div className="space-y-2">
            <Field label={`블록 (${filtered.length}종)`}>
              <select className={field} value={name} disabled={!!initial} onChange={(e) => pick(e.target.value)}>
                {filtered.map((x) => (
                  <option key={x.name} value={x.name}>
                    [{x.category ?? "기계"}] {x.label}
                    {x.builtin ? "" : " (회사)"}
                  </option>
                ))}
              </select>
            </Field>
            {t && (
              <p className="rounded-md bg-[var(--color-primary-light)]/50 px-2.5 py-1.5 text-xs text-muted-foreground">
                <strong>표준화 기준</strong>: 레이어 <code>{t.layer_policy ?? "0 / ByLayer"}</code> · 기준점 <strong>{t.base_point ?? "(0,0)"}</strong>
              </p>
            )}
            {t && (
              <div className="grid grid-cols-2 gap-2">
                {t.params.map((p) => (
                  <Field key={p.name} label={`${p.label ?? p.name} (${p.min}~${p.max})`}>
                    <input className={field} inputMode="decimal" placeholder={String(p.default)} value={vals[p.name] ?? ""} onChange={(e) => setVals({ ...vals, [p.name]: e.target.value })} />
                  </Field>
                ))}
              </div>
            )}
            {t && t.states.length > 0 && (
              <Field label="가시성 상태">
                <select className={field} value={state ?? t.states[0]} onChange={(e) => setState(e.target.value)}>
                  {t.states.map((s) => (
                    <option key={s}>{s}</option>
                  ))}
                </select>
              </Field>
            )}
          </div>

          <div className="flex flex-col items-center justify-between rounded-md border border-line bg-background p-2">
            <span className="text-[11px] font-medium text-muted-foreground">실시간 미리보기 (회전·반전 반영)</span>
            {previewSvg ? (
              <svg viewBox={previewSvg.viewBox} className="h-32 w-full" aria-label="블록 미리보기">
                {previewSvg.paths.map((d, i) => (
                  <path key={i} d={d} fill="none" stroke="currentColor" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
                ))}
                <line
                  x1={-previewSvg.originCross[2]}
                  y1={0}
                  x2={previewSvg.originCross[2]}
                  y2={0}
                  stroke="#ef4444"
                  strokeWidth="1.5"
                  vectorEffect="non-scaling-stroke"
                />
                <line
                  x1={0}
                  y1={-previewSvg.originCross[2]}
                  x2={0}
                  y2={previewSvg.originCross[2]}
                  stroke="#ef4444"
                  strokeWidth="1.5"
                  vectorEffect="non-scaling-stroke"
                />
              </svg>
            ) : (
              <div className="flex h-32 items-center justify-center text-xs text-muted-foreground">미리보기 로딩...</div>
            )}
            <span className="text-[10px] text-muted-foreground">빨간 십자 = 삽입 기준점 (0,0)</span>
          </div>
        </div>

        {/* Insertion Transform & Layer Options */}
        <div className="space-y-2 rounded-md border border-line bg-card/50 p-2.5">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            <Field label="삽입점 x,y (mm)">
              <input className={field} value={at} onChange={(e) => setAt(e.target.value)} />
            </Field>
            <Field label="회전 각도 (°)">
              <div className="space-y-1">
                <input className={field} inputMode="decimal" value={rotation} onChange={(e) => setRotation(e.target.value)} />
                <div className="flex gap-1">
                  {ROTATION_PRESETS.map((deg) => (
                    <button
                      key={deg}
                      type="button"
                      onClick={() => setRotation(String(deg))}
                      className={`flex-1 rounded border px-1 py-0.5 text-[11px] ${
                        Number(rotation) === deg ? "border-[var(--color-primary)] bg-[var(--color-primary)] text-white" : "border-line bg-card hover:bg-hover"
                      }`}
                    >
                      {deg}°
                    </button>
                  ))}
                </div>
              </div>
            </Field>
            <Field label="삽입 대상 레이어">
              <select className={field} value={targetLayer} onChange={(e) => setTargetLayer(e.target.value)}>
                <option value="">현재 레이어{currentLayer ? ` (${currentLayer})` : ""}</option>
                {layers.map((ly) => (
                  <option key={ly} value={ly}>
                    {ly}
                  </option>
                ))}
              </select>
            </Field>
          </div>

          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 items-end">
            <Field label={uniformScale ? "축척 (Scale)" : "X 축척"}>
              <input className={field} inputMode="decimal" value={xscale} onChange={(e) => setXscale(e.target.value)} />
            </Field>
            {!uniformScale && (
              <Field label="Y 축척">
                <input className={field} inputMode="decimal" value={yscale} onChange={(e) => setYscale(e.target.value)} />
              </Field>
            )}
            <label className="flex items-center gap-1.5 pb-1.5 text-xs">
              <input type="checkbox" checked={uniformScale} onChange={(e) => setUniformScale(e.target.checked)} />
              균일 축척
            </label>
            <div className="flex gap-3 pb-1.5 text-xs">
              <label className="flex items-center gap-1">
                <input type="checkbox" checked={flipX} onChange={(e) => setFlipX(e.target.checked)} />
                좌우 반전
              </label>
              <label className="flex items-center gap-1">
                <input type="checkbox" checked={flipY} onChange={(e) => setFlipY(e.target.checked)} />
                상하 반전
              </label>
            </div>
          </div>

          {!initial && (
            <div className="space-y-2 border-t border-line pt-2">
              <div className="flex flex-wrap items-center gap-4 text-xs">
                <label className="flex items-center gap-1.5 font-medium">
                  <input type="checkbox" checked={useArray} onChange={(e) => setUseArray(e.target.checked)} />
                  직사각형 배열 배치 (행 × 열)
                </label>
                <label className="flex items-center gap-1.5 font-medium">
                  <input type="checkbox" checked={keepOpen} onChange={(e) => setKeepOpen(e.target.checked)} />
                  연속 배치 (삽입 후 팔레트 유지)
                </label>
                {placedCount > 0 && <span className="rounded bg-emerald-600/15 px-2 py-0.5 text-emerald-700">누적 {placedCount}개 삽입됨</span>}
              </div>
              {useArray && (
                <div className="grid grid-cols-4 gap-2">
                  <Field label="열 (Cols)">
                    <input className={field} inputMode="numeric" value={cols} onChange={(e) => setCols(e.target.value)} />
                  </Field>
                  <Field label="행 (Rows)">
                    <input className={field} inputMode="numeric" value={rows} onChange={(e) => setRows(e.target.value)} />
                  </Field>
                  <Field label="열 간격 dx (mm)">
                    <input className={field} inputMode="decimal" value={dx} onChange={(e) => setDx(e.target.value)} />
                  </Field>
                  <Field label="행 간격 dy (mm)">
                    <input className={field} inputMode="decimal" value={dy} onChange={(e) => setDy(e.target.value)} />
                  </Field>
                </div>
              )}
            </div>
          )}
        </div>

        <Err text={error} />
        <div className="flex flex-wrap justify-end gap-2">
          <button type="button" className={btn2} onClick={onClose}>
            {keepOpen && placedCount > 0 ? "완료 (닫기)" : "취소"}
          </button>
          {!initial && onPickOnCanvas && (
            <button type="button" className={btnPrimary} disabled={busy || !t} onClick={startCanvasPick}>
              캔버스 클릭 배치 (스냅)
            </button>
          )}
          <button type="submit" className={!initial && onPickOnCanvas ? btn2 : btnPrimary} disabled={busy || !t}>
            {initial ? "다시 만들기" : useArray ? `좌표 배열 삽입 (${(Number(cols) || 1) * (Number(rows) || 1)}개)` : "좌표로 즉시 삽입"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
