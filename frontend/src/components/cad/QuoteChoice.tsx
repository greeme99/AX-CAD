"use client";

// 견적 도면 / 일반 도면: asked for every new drawing (캐드 메뉴) and for every new project.
// No default on purpose — the quote checks (outer contour, material, thickness) follow this choice.
export default function QuoteChoice({ value, onChange, name, project = false }: { value: boolean | null; onChange: (v: boolean) => void; name: string; project?: boolean }) {
  const opts: [boolean, string, string][] = project
    ? [
        [true, "견적서 있음", "부품·판금 도면: 외곽·재질·두께 등 견적 점검"],
        [false, "견적서 없음", "조립·배치·표지 등: 도면 품질만 점검"],
      ]
    : [
        [true, "견적 도면", "부품·판금: 외곽·재질·두께 등 견적 점검"],
        [false, "일반 도면", "조립·배치·표지 등: 도면 품질만 점검"],
      ];
  return (
    <fieldset className="space-y-1">
      <legend className="text-sm font-medium text-foreground">{project ? "견적서 유무" : "도면 종류"} (필수)</legend>
      {opts.map(([v, label, hint]) => (
        <label key={label} className="flex items-start gap-2 text-sm">
          <input type="radio" name={name} required checked={value === v} onChange={() => onChange(v)} className="mt-1" />
          <span>
            {label} <span className="text-xs text-muted-foreground">— {hint}</span>
          </span>
        </label>
      ))}
    </fieldset>
  );
}
