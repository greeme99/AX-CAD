"use client";

import { useState } from "react";
import { btn2, btnPrimary, field, Field, Modal } from "@/components/ui";
import { download, errText } from "@/lib/api";

const PAPERS = ["A4", "A3", "A2", "A1", "A0"] as const;
const SCALE = /^\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)\s*$/; // "1:50", "2:1"

/** Track 8 P1-4: the saved revision's model space as a PDF sheet with a title block;
 *  P3-4: a paper-space sheet (sheet = its name) prints as laid out, on its own paper. */
export default function PlotDialog({ revisionId, filename, hidden, dirty, sheet, onClose }: { revisionId: string; filename: string; hidden: Set<string>; dirty: boolean; sheet?: string; onClose: () => void }) {
  const [paper, setPaper] = useState<(typeof PAPERS)[number]>("A3");
  const [landscape, setLandscape] = useState(true);
  const [scale, setScale] = useState(""); // empty = fit
  const [color, setColor] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const m = SCALE.exec(scale);
    if (scale.trim() && (!m || !Number(m[1]) || !Number(m[2]))) return setError("축척은 1:50, 2:1처럼 입력하세요 (비우면 자동)");
    setBusy(true);
    setError(null);
    try {
      const json = sheet ? { sheet, color, hidden: [...hidden] } : { paper, landscape, color, hidden: [...hidden], scale: m ? Number(m[1]) / Number(m[2]) : null };
      await download(`/revisions/${encodeURIComponent(revisionId)}/pdf`, filename, { method: "POST", json });
      onClose();
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="도면 PDF 출력" onClose={onClose}>
      <form onSubmit={submit} className="space-y-3">
        {dirty && <p className="text-sm text-amber-700">저장하지 않은 변경은 출력되지 않습니다. 먼저 저장하세요.</p>}
        {sheet ? (
          <p className="text-sm text-foreground">시트 「{sheet}」를 배치된 그대로(용지·뷰포트·표제란) 1:1로 출력합니다.</p>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-3">
              <Field label="용지">
                <select value={paper} onChange={(e) => setPaper(e.target.value as (typeof PAPERS)[number])} className={field}>
                  {PAPERS.map((p) => (
                    <option key={p}>{p}</option>
                  ))}
                </select>
              </Field>
              <Field label="방향">
                <select value={landscape ? "L" : "P"} onChange={(e) => setLandscape(e.target.value === "L")} className={field}>
                  <option value="L">가로</option>
                  <option value="P">세로</option>
                </select>
              </Field>
            </div>
            <Field label="축척 (비우면 용지에 맞는 표준 축척 자동)">
              <input value={scale} onChange={(e) => setScale(e.target.value)} placeholder="예: 1:50" className={field} />
            </Field>
          </>
        )}
        <label className="flex items-center gap-2 text-sm text-foreground">
          <input type="checkbox" checked={color} onChange={(e) => setColor(e.target.checked)} /> 색상 출력 (기본: 흑백)
        </label>
        <p className="text-xs text-muted-foreground">
          숨긴 레이어는 출력하지 않습니다.{sheet ? "" : " 표제란에 도면번호·도면명·리비전·축척·용지·일자·작성자가 들어갑니다."}
        </p>
        {error && (
          <p role="alert" className="text-sm text-red-600">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className={btn2}>
            취소
          </button>
          <button type="submit" disabled={busy} className={btnPrimary}>
            {busy ? "만드는 중..." : "PDF 내려받기"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
