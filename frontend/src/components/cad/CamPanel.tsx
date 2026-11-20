"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type * as THREE from "three";
import {
  buildCamToolpathOverlay,
  type CamOverlayHandle,
  type CamSegment,
  type CamToolpathData,
} from "@/lib/cad/cam";

export { buildCamToolpathOverlay, type CamOverlayHandle, type CamSegment, type CamToolpathData };

interface CamPanelProps {
  toolpath: CamToolpathData | null;
  sceneGroup?: THREE.Group | null;
  onDownloadGcode?: (programName: string) => void;
}

export default function CamPanel({ toolpath, sceneGroup, onDownloadGcode }: CamPanelProps) {
  const [playing, setPlaying] = useState(false);
  const [progress, setProgress] = useState(100);
  const [programName, setProgramName] = useState("O0001");
  const overlayRef = useRef<CamOverlayHandle | null>(null);

  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => {
      setProgress((prev) => {
        if (prev >= 100) {
          setPlaying(false);
          return 100;
        }
        return Math.min(100, prev + 2);
      });
    }, 50);
    return () => window.clearInterval(timer);
  }, [playing]);

  useEffect(() => {
    if (overlayRef.current) {
      if (sceneGroup) sceneGroup.remove(overlayRef.current.group);
      overlayRef.current.dispose();
      overlayRef.current = null;
    }
    if (!toolpath || !sceneGroup) return;
    const handle = buildCamToolpathOverlay(toolpath.segments, progress);
    overlayRef.current = handle;
    sceneGroup.add(handle.group);
    return () => {
      if (overlayRef.current) {
        sceneGroup.remove(overlayRef.current.group);
        overlayRef.current.dispose();
        overlayRef.current = null;
      }
    };
  }, [toolpath, progress, sceneGroup]);

  const currentSeg = useMemo(() => {
    if (!toolpath || toolpath.segments.length === 0) return null;
    const idx = Math.min(
      toolpath.segments.length - 1,
      Math.max(0, Math.round((toolpath.segments.length * progress) / 100) - 1),
    );
    return toolpath.segments[idx] ?? null;
  }, [toolpath, progress]);

  return (
    <section aria-label="CAM 시뮬레이션" className="rounded-md border border-line bg-card p-3 text-sm">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="font-semibold text-foreground">CAM 툴패스 시뮬레이션</h3>
        {toolpath && (
          <span className="font-mono text-xs text-muted">
            {toolpath.kind} · {toolpath.segment_count} segs
          </span>
        )}
      </div>

      {!toolpath ? (
        <p className="text-xs text-muted">생성된 CAM 공정이 없습니다.</p>
      ) : (
        <div className="space-y-2">
          <div className="grid grid-cols-3 gap-2 rounded bg-surface p-2 text-xs">
            <div>
              <span className="block text-muted">절삭 길이</span>
              <span className="font-mono font-medium">{toolpath.cut_length_mm.toFixed(1)} mm</span>
            </div>
            <div>
              <span className="block text-muted">급속 이동</span>
              <span className="font-mono font-medium">{toolpath.rapid_length_mm.toFixed(1)} mm</span>
            </div>
            <div>
              <span className="block text-muted">가공 시간</span>
              <span className="font-mono font-medium">{toolpath.cycle_time_s.toFixed(1)} s</span>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => {
                if (progress >= 100) setProgress(0);
                setPlaying((p) => !p);
              }}
              className="rounded border border-line px-2 py-1 text-xs font-medium hover:bg-hover"
            >
              {playing ? "일시정지" : "재생"}
            </button>
            <input
              type="range"
              aria-label="시뮬레이션 진행률"
              min={0}
              max={100}
              value={progress}
              onChange={(e) => {
                setPlaying(false);
                setProgress(Number(e.target.value));
              }}
              className="flex-1"
            />
            <span className="w-10 text-right font-mono text-xs">{progress}%</span>
          </div>

          {currentSeg && (
            <div className="font-mono text-xs text-muted">
              공구 위치: X{currentSeg.end[0].toFixed(2)} Y{currentSeg.end[1].toFixed(2)} Z
              {currentSeg.end[2].toFixed(2)} ({currentSeg.kind})
            </div>
          )}

          {onDownloadGcode && (
            <div className="flex items-center gap-2 pt-1">
              <input
                type="text"
                aria-label="프로그램 번호"
                value={programName}
                onChange={(e) => setProgramName(e.target.value)}
                className="w-24 rounded border border-line bg-background px-2 py-1 font-mono text-xs"
              />
              <button
                type="button"
                onClick={() => onDownloadGcode(programName)}
                className="rounded bg-[var(--color-primary)] px-2.5 py-1 text-xs font-medium text-white"
              >
                G코드(.nc) 다운로드
              </button>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
