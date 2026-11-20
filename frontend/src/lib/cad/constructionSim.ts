// Pure-TS helpers for Construction Process Gantt, S-Curve SVG & KRW formatting.

export function formatKrw(amount: number): string {
  const n = Math.round(amount);
  return `${n.toLocaleString("ko-KR")}원`;
}

export function ganttBarRect(
  earlyStart: number,
  earlyFinish: number,
  totalDays: number,
): { leftPct: number; widthPct: number } {
  const total = Math.max(0.5, totalDays);
  const left = Math.max(0, Math.min(100, (earlyStart / total) * 100));
  const width = Math.max(2, Math.min(100 - left, ((earlyFinish - earlyStart) / total) * 100));
  return { leftPct: Math.round(left * 100) / 100, widthPct: Math.round(width * 100) / 100 };
}

export function buildScurvePolyline(
  points: { day: number; progress_pct: number }[],
  width: number,
  height: number,
  pad = 12,
): string {
  if (!points.length) return "";
  const maxDay = Math.max(1, points[points.length - 1].day);
  const w = Math.max(10, width - pad * 2);
  const h = Math.max(10, height - pad * 2);
  return points
    .map((p) => {
      const x = pad + (p.day / maxDay) * w;
      const y = pad + h - (Math.max(0, Math.min(100, p.progress_pct)) / 100) * h;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}
