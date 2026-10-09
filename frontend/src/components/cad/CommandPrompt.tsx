"use client";

import { useState } from "react";

type Props = { prompt: string; onSubmit: (text: string) => void };

export default function CommandPrompt({ prompt, onSubmit }: Props) {
  const [value, setValue] = useState("");
  return (
    <form
      className="flex items-center gap-2 border-t border-line bg-card px-3 py-1"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit(value);
        setValue("");
      }}
    >
      <span role="status" className="shrink-0 text-sm text-body">
        {prompt}
      </span>
      <input
        aria-label="명령 입력"
        placeholder="명령 · x,y · @dx,dy · 값"
        autoComplete="off"
        spellCheck={false}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        className="min-w-0 flex-1 rounded-md border border-line bg-background px-2 py-1 font-mono text-sm text-foreground focus-visible:outline-2 focus-visible:outline-ring"
      />
    </form>
  );
}
