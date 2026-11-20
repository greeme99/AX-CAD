"use client";

import { useEffect, useRef, useState } from "react";
import type * as THREE from "three";
import {
  buildCaeResultOverlay,
  type CaeDisplayMode,
  type CaeOverlayHandle,
  type CaeResultData,
} from "@/lib/cad/cae";

interface CaePanelProps {
  result: CaeResultData | null;
  sceneGroup?: THREE.Group | null;
  onDownloadReport?: () => void;
}

const MODE_LABELS: Record<CaeDisplayMode, string> = {
  von_mises: "von Mises 응력 (MPa)",
  displacement: "변위 (mm)",
  safety_factor: "안전율 (Safety Factor)",
  temperature: "온도 분포 (°C)",
};

export default function CaePanel({ result, sceneGroup, onDownloadReport }: CaePanelProps) {
  const [mode, setMode] = useState<CaeDisplayMode>("von_mises");
  const [deformScale, setDeformScale] = useState(10);
  const overlayRef = useRef<CaeOverlayHandle | null>(null);

  useEffect(() => {
    if (overlayRef.current) {
      if (sceneGroup) sceneGroup.remove(overlayRef.current.group);
      overlayRef.current.dispose();
      overlayRef.current = null;
    }
    if (!result || !sceneGroup) return;
    const handle = buildCaeResultOverlay(result, mode, deformScale);
    overlayRef.current = handle;
    sceneGroup.add(handle.group);
    return () => {
      if (overlayRef.current) {
        sceneGroup.remove(overlayRef.current.group);
        overlayRef.current.dispose();
        overlayRef.current = null;
      }
    };
  }, [result, mode, deformScale, sceneGroup]);

  return (
    <section aria-label="CAE 선형 정적 해석" className="rounded-md border border-line bg-card p-3 text-sm">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="font-semibold text-foreground">CAE 선형 정적 FEA 결과</h3>
        {result && (
          <span className="font-mono text-xs text-muted">
            {result.node_count} nodes · {result.element_count} Tet4
          </span>
        )}
      </div>

      {!result ? (
        <p className="text-xs text-muted">해석 결과가 없습니다.</p>
      ) : (
        <div className="space-y-2">
          <div className="grid grid-cols-3 gap-2 rounded bg-surface p-2 text-xs">
            <div>
              <span className="block text-muted">최대 변위</span>
              <span className="font-mono font-medium">{result.max_disp_mm.toFixed(4)} mm</span>
            </div>
            <div>
              <span className="block text-muted">최대 응력</span>
              <span className="font-mono font-medium">{result.max_von_mises_mpa.toFixed(2)} MPa</span>
            </div>
            <div>
              <span className="block text-muted">최소 안전율</span>
              <span className="font-mono font-medium">{result.min_safety_factor.toFixed(2)}</span>
            </div>
          </div>

          <div className="flex flex-wrap gap-1">
            {(Object.keys(MODE_LABELS) as CaeDisplayMode[]).map((m) => (
              <button
                key={m}
                type="button"
                aria-pressed={mode === m}
                onClick={() => setMode(m)}
                className={`rounded border px-2 py-1 text-xs ${
                  mode === m
                    ? "border-[var(--color-primary)] bg-[var(--color-primary-light)] font-medium text-[var(--color-primary-hover)]"
                    : "border-line hover:bg-hover"
                }`}
              >
                {MODE_LABELS[m]}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-2">
            <span className="text-xs text-muted">변위 과장</span>
            <input
              type="range"
              aria-label="변위 과장 배율"
              min={0}
              max={50}
              value={deformScale}
              onChange={(e) => setDeformScale(Number(e.target.value))}
              className="flex-1"
            />
            <span className="w-10 text-right font-mono text-xs">{deformScale}×</span>
            <button
              type="button"
              onClick={() => {
                setMode("von_mises");
                setDeformScale(1);
              }}
              className="rounded border border-line px-2 py-0.5 text-xs hover:bg-hover"
            >
              초기화
            </button>
          </div>

          <p className="text-[11px] text-muted">
            주의: 선형 정적 근사 해석 참고용 (비선형·소성·좌굴 제외)
          </p>

          {onDownloadReport && (
            <button
              type="button"
              onClick={onDownloadReport}
              className="rounded bg-[var(--color-primary)] px-2.5 py-1 text-xs font-medium text-white"
            >
              PDF 해석 보고서 다운로드
            </button>
          )}
        </div>
      )}
    </section>
  );
}
