"use client";

import { useEffect, useRef, useState } from "react";
import { btn2, btnPrimary, Err, field, Field, Modal } from "@/components/ui";
import { errText } from "@/lib/api";
import {
  defaultViewLabel,
  MAX_PHOTO3D_FILES,
  mmPerPixel,
  uniformTurntableAngles,
  validatePhoto3dInputs,
  type OutputMode,
  type PhotoMode,
  type ViewLabel,
} from "@/lib/cad/photo3d";
import {
  getPhoto3dStatus,
  reconstructPhoto3d,
  type Photo3dReconstructResponse,
  type Photo3dStatus,
} from "@/lib/photo3dApi";

const VIEW_LABELS: ViewLabel[] = ["FRONT", "SIDE", "TOP", "BACK", "LEFT", "RIGHT", "BOTTOM", "ANGLE"];

type PhotoEntry = {
  file: File;
  url: string;
  view: ViewLabel;
  angleDeg: number;
};

export default function Photo3dDialog({
  docId,
  onClose,
  onDone,
}: {
  docId: string;
  onClose: () => void;
  onDone: (featureId: number) => void;
}) {
  const [status, setStatus] = useState<Photo3dStatus | null>(null);
  const [mode, setMode] = useState<PhotoMode>("ORTHO_MULTI");
  const [outputMode, setOutputMode] = useState<OutputMode>("HYBRID");
  const [entries, setEntries] = useState<PhotoEntry[]>([]);
  const [refMm, setRefMm] = useState("100");
  const [refAxis, setRefAxis] = useState<"X" | "Y" | "Z">("X");
  const [pickPts, setPickPts] = useState<[number, number][]>([]);
  const [useAi, setUseAi] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Photo3dReconstructResponse | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);

  useEffect(() => {
    void getPhoto3dStatus().then(setStatus, () => setStatus(null));
  }, []);

  useEffect(() => {
    return () => {
      for (const e of entries) URL.revokeObjectURL(e.url);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const addFiles = (list: FileList | null) => {
    if (!list || list.length === 0) return;
    setError(null);
    setResult(null);
    const incoming = Array.from(list).slice(0, Math.max(0, MAX_PHOTO3D_FILES - entries.length));
    const nextTotal = entries.length + incoming.length;
    const angles = uniformTurntableAngles(nextTotal);
    const created: PhotoEntry[] = incoming.map((file, i) => ({
      file,
      url: URL.createObjectURL(file),
      view: defaultViewLabel(mode, entries.length + i),
      angleDeg: angles[entries.length + i] ?? 0,
    }));
    setEntries((prev) => {
      const merged = [...prev, ...created];
      if (mode === "TURNTABLE") {
        const angs = uniformTurntableAngles(merged.length);
        return merged.map((item, idx) => ({ ...item, angleDeg: angs[idx] ?? 0 }));
      }
      return merged;
    });
  };

  const removeAt = (idx: number) => {
    setEntries((prev) => {
      const removed = prev[idx];
      if (removed) URL.revokeObjectURL(removed.url);
      const rest = prev.filter((_, i) => i !== idx);
      if (idx === 0) setPickPts([]);
      return rest;
    });
  };

  const onClickPrimaryImage = (e: React.MouseEvent<HTMLImageElement>) => {
    const el = imgRef.current;
    if (!el || !el.naturalWidth || !el.naturalHeight) return;
    const rect = el.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * el.naturalWidth;
    const py = ((e.clientY - rect.top) / rect.height) * el.naturalHeight;
    setPickPts((prev) => (prev.length >= 2 ? [[px, py]] : [...prev, [px, py]]));
  };

  const calibScale = pickPts.length === 2 ? mmPerPixel(Number(refMm), pickPts[0], pickPts[1]) : null;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const vErr = validatePhoto3dInputs({ fileCount: entries.length, mode, referenceMm: Number(refMm) });
    if (vErr) return setError(vErr);
    setBusy(true);
    setError(null);
    try {
      const res = await reconstructPhoto3d(
        docId,
        entries.map((x) => x.file),
        {
          mode,
          output_mode: outputMode,
          reference_mm: Number(refMm),
          reference_axis: refAxis,
          ref_p1_px: pickPts.length === 2 ? pickPts[0] : undefined,
          ref_p2_px: pickPts.length === 2 ? pickPts[1] : undefined,
          view_labels: entries.map((x) => x.view),
          angles_deg: mode === "TURNTABLE" ? entries.map((x) => x.angleDeg) : undefined,
          use_ai: useAi,
          create_feature: true,
        },
      );
      setResult(res);
      if (res.feature_id !== null) onDone(res.feature_id);
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title="사진 → 3D 실측 모형 자동 생성 (Photo-to-3D)" onClose={onClose} wide>
      <form onSubmit={submit} className="space-y-3 text-sm">
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-[var(--color-primary-light)]/60 px-3 py-2 text-xs">
          <span>
            <strong>1. 다각도·연속 사진 선택</strong> → <strong>2. 기준 척도 치수(mm) 입력</strong> → <strong>3. 3D 실측 모형 생성</strong>
          </span>
          <span className="rounded border border-line bg-card px-2 py-0.5 font-mono text-[11px]">
            {status?.sdk_installed ? "Antigravity SDK 활성" : "로컬 기하 엔진 (SDK 폴백)"}
          </span>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <Field label="촬영 방식">
            <select
              value={mode}
              onChange={(e) => {
                const m = e.target.value as PhotoMode;
                setMode(m);
                const angs = uniformTurntableAngles(entries.length);
                setEntries((prev) =>
                  prev.map((item, idx) => ({
                    ...item,
                    view: defaultViewLabel(m, idx),
                    angleDeg: angs[idx] ?? 0,
                  })),
                );
              }}
              className={field}
            >
              <option value="ORTHO_MULTI">① 정투상 다각도 (정면·측면·평면 2~6장)</option>
              <option value="TURNTABLE">② 연속 턴테이블 사진 (4~36장, 원주 회전)</option>
              <option value="FREE_ANGLE">③ 단일 / 자유 각도 사진 (AI 형상 추론)</option>
            </select>
          </Field>
          <Field label="3D 생성 형태">
            <select value={outputMode} onChange={(e) => setOutputMode(e.target.value as OutputMode)} className={field}>
              <option value="HYBRID">하이브리드 (권장: 자동 선택)</option>
              <option value="PARAMETRIC">파라메트릭 B-Rep 피처 (편집 가능)</option>
              <option value="VISUAL_HULL">실측 3D Visual Hull 솔리드</option>
            </select>
          </Field>
        </div>

        <div className="space-y-2 rounded-md border border-line p-2.5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <label className={`${btn2} cursor-pointer`}>
              사진 추가 (PNG · JPG · WEBP, 최대 {MAX_PHOTO3D_FILES}장)
              <input
                type="file"
                accept="image/png,image/jpeg,image/webp"
                multiple
                className="sr-only"
                onChange={(e) => {
                  addFiles(e.target.files);
                  e.target.value = "";
                }}
              />
            </label>
            <span className="text-xs text-muted-foreground">선택됨: {entries.length}장</span>
          </div>

          {entries.length > 0 && (
            <div className="grid max-h-40 grid-cols-3 gap-2 overflow-y-auto pt-1">
              {entries.map((item, idx) => (
                <div key={item.url} className="flex items-center gap-2 rounded border border-line bg-background p-1.5 text-xs">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={item.url} alt={item.file.name} className="h-10 w-10 rounded object-cover" />
                  <div className="min-w-0 flex-1 space-y-1">
                    <p className="truncate font-medium">{item.file.name}</p>
                    {mode === "TURNTABLE" ? (
                      <span className="font-mono text-[11px] text-muted-foreground">{item.angleDeg}°</span>
                    ) : (
                      <select
                        aria-label={`${item.file.name} 시점`}
                        value={item.view}
                        onChange={(e) =>
                          setEntries((prev) =>
                            prev.map((x, i) => (i === idx ? { ...x, view: e.target.value as ViewLabel } : x)),
                          )
                        }
                        className="w-full rounded border border-line bg-card px-1 py-0.5 text-[11px]"
                      >
                        {VIEW_LABELS.map((vl) => (
                          <option key={vl} value={vl}>
                            {vl}
                          </option>
                        ))}
                      </select>
                    )}
                  </div>
                  <button type="button" onClick={() => removeAt(idx)} className="text-xs text-muted-foreground hover:text-foreground">
                    ✕
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="grid grid-cols-2 gap-3">
          <Field label="기준 척도 치수 (mm)">
            <input
              type="number"
              min="0.1"
              step="any"
              value={refMm}
              onChange={(e) => setRefMm(e.target.value)}
              className={`${field} font-mono`}
            />
          </Field>
          <Field label="기준 방향">
            <select value={refAxis} onChange={(e) => setRefAxis(e.target.value as "X" | "Y" | "Z")} className={field}>
              <option value="X">가로 폭 (X축 기준)</option>
              <option value="Z">높이 (Z축 기준)</option>
              <option value="Y">깊이 (Y축 기준)</option>
            </select>
          </Field>
        </div>

        {entries[0] && (
          <div className="space-y-1 rounded-md border border-line p-2">
            <div className="flex items-center justify-between text-xs">
              <span className="text-muted-foreground">
                선택: 첫 번째 사진 위 두 점을 클릭하면 정밀 2점 픽셀 보정을 적용합니다 ({pickPts.length}/2점)
                {calibScale !== null ? ` · ${calibScale.toFixed(4)} mm/px` : ""}
              </span>
              {pickPts.length > 0 && (
                <button type="button" onClick={() => setPickPts([])} className={btn2}>
                  2점 초기화
                </button>
              )}
            </div>
            <div className="flex max-h-44 justify-center overflow-hidden rounded bg-black/5">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                ref={imgRef}
                src={entries[0].url}
                alt="기준 척도 보정 사진"
                onClick={onClickPrimaryImage}
                className="max-h-44 cursor-crosshair object-contain"
              />
            </div>
          </div>
        )}

        <label className="flex items-start gap-2 rounded-md border border-line bg-background/60 p-2 text-xs">
          <input type="checkbox" checked={useAi} onChange={(e) => setUseAi(e.target.checked)} className="mt-0.5" />
          <span>
            <strong>AI 정밀 추론 사용 (Antigravity SDK 백엔드 연동)</strong> — 체크 시 윤곽/비율 요약 데이터를 읽기 전용 Antigravity Agent로 분석해 최적 3D 피처 구조를 추론합니다. (미설치·오프라인 시 로컬 기하 엔진 자동 폴백)
          </span>
        </label>

        {result && (
          <div role="status" className="rounded-md border border-line bg-[var(--color-primary-light)]/40 p-2 text-xs">
            <p className="font-medium">
              복원 완료 ({result.resolved_output} · {result.ai_provider}) — 크기 {result.bbox_mm.join(" × ")} mm · 부피 {result.volume_mm3.toLocaleString()} mm³
            </p>
            {result.warnings.map((w, i) => (
              <p key={i} className="text-amber-800 dark:text-amber-300">
                {w}
              </p>
            ))}
          </div>
        )}

        <Err text={error} />
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className={btn2}>
            닫기
          </button>
          <button type="submit" disabled={busy || entries.length === 0} aria-busy={busy} className={btnPrimary}>
            {busy ? "3D 실측 모형 복원 중..." : "3D 실측 모형 생성"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
