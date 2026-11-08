"use client";

import { useEffect, useRef, useState } from "react";
import { api, can, errText, type User } from "@/lib/api";
import {
  findingsPath,
  runnable,
  SEVERITY_LABEL,
  SOURCE_LABEL,
  whereToRun,
  type Action,
  type Answer,
  type Finding,
  type Findings,
  type Settings,
  type Topic,
} from "@/lib/copilot";
import { useApi } from "@/lib/useApi";
import { btn2, btnPrimary, field } from "@/components/ui";
import { useCopilot } from "./CopilotProvider";
import DrawTab from "./DrawTab";
import LispTab from "./LispTab";

type Tab = "drawing" | "support";
type Sub = "text" | "lisp" | "check" | "chat" | "help";
type Msg = { role: "user" | "bot"; text: string; source?: Answer["source"]; model?: string | null; actions?: Action[]; related?: Topic[]; done?: string };
const MAX_MSGS = 40;
const SEV_STYLE: Record<Finding["severity"], string> = {
  ERROR: "border-red-500/60 bg-red-500/10 text-red-700 dark:text-red-300",
  WARN: "border-amber-500/60 bg-amber-500/10 text-amber-800 dark:text-amber-200",
  INFO: "border-line bg-[var(--color-bg-subtle)] text-body",
};

export default function CopilotPanel() {
  const cp = useCopilot()!;
  const drawable = cp.context.page === "cad2d" || cp.context.page === "model3d";
  // Track 11: Drawing (text to draw, AX-LISP) and Support (checks, questions, help)
  const [tab, setTab] = useState<Tab>(drawable ? "drawing" : "support");
  const [sub, setSub] = useState<Record<Tab, Sub>>({ drawing: "text", support: "check" });
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => closeRef.current?.focus(), []);
  const tabs: [Tab, string][] = [["drawing", "Drawing"], ["support", "Support"]];
  const subs: Record<Tab, [Sub, string][]> = {
    drawing: [["text", "문장으로 그리기"], ["lisp", "LISP"]],
    support: [["check", cp.context.page === "quote" ? "견적 조언" : "설계 점검"], ["chat", "질문"], ["help", "도움말"]],
  };
  const cur = sub[tab];
  return (
    <aside
      id="copilot-panel"
      aria-label="AI Copilot"
      className="fixed inset-y-0 right-0 z-40 flex w-full max-w-[360px] flex-col border-l border-line bg-card shadow-[var(--shadow-card)] md:sticky md:top-0 md:h-screen md:w-[360px] md:shrink-0 md:shadow-none"
      onKeyDown={(e) => e.key === "Escape" && cp.setOpen(false)}
    >
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-line px-3">
        <span className="font-semibold text-foreground">AI Copilot</span>
        <span className="rounded bg-[var(--color-bg-subtle)] px-1.5 py-0.5 text-xs text-muted-foreground">규칙 기반</span>
        <span className="flex-1" />
        <button
          ref={closeRef}
          type="button"
          onClick={() => cp.setOpen(false)}
          aria-label="Copilot 감추기"
          title="감추기 (Ctrl+. / Esc)"
          className="rounded-md px-2 py-1 text-body hover:bg-hover focus-visible:outline-2 focus-visible:outline-ring"
        >
          ✕
        </button>
      </div>
      <div role="tablist" aria-label="Copilot 메뉴" className="flex shrink-0 gap-1 border-b border-line px-2 py-1.5">
        {tabs.map(([t, label]) => (
          <button
            key={t}
            type="button"
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={`rounded-md px-3 py-1 text-sm focus-visible:outline-2 focus-visible:outline-ring ${tab === t ? "bg-[var(--color-primary-light)] font-medium text-[var(--color-primary-hover)]" : "text-body hover:bg-hover"}`}
          >
            {label}
          </button>
        ))}
      </div>
      <div role="tablist" aria-label={`${tab === "drawing" ? "Drawing" : "Support"} 하위 메뉴`} className="flex shrink-0 gap-1 border-b border-line px-2 py-1">
        {subs[tab].map(([t, label]) => (
          <button
            key={t}
            type="button"
            role="tab"
            aria-selected={cur === t}
            onClick={() => setSub((x) => ({ ...x, [tab]: t }))}
            className={`rounded px-2 py-0.5 text-xs focus-visible:outline-2 focus-visible:outline-ring ${cur === t ? "bg-[var(--color-bg-subtle)] font-medium text-foreground" : "text-muted-foreground hover:bg-hover"}`}
          >
            {label}
          </button>
        ))}
      </div>
      <div role="tabpanel" className="min-h-0 flex-1 overflow-y-auto">
        {cur === "text" && (drawable ? <DrawTab /> : <ChatTab key="draw" mode="draw" />)}
        {cur === "lisp" && <LispTab />}
        {cur === "check" && <CheckTab />}
        {cur === "chat" && <ChatTab key="ask" mode="ask" />}
        {cur === "help" && <HelpTab />}
      </div>
    </aside>
  );
}

function CheckTab() {
  const cp = useCopilot()!;
  const path = findingsPath(cp.context);
  const res = useApi<Findings>(path);
  if (!path)
    return <p className="p-4 text-sm text-muted-foreground">도면·3D 모델·견적 화면에서 열면 지금 문서의 설계 문제(DFM)와 견적 검증 결과를 고칠 방법과 함께 보여 줍니다.</p>;
  const jump = cp.handlers.current?.target;
  return (
    <div className="space-y-3 p-3">
      <div className="flex items-center gap-2 text-sm">
        {res.data && (
          <span className="text-body">
            오류 <b>{res.data.counts.ERROR}</b> · 경고 <b>{res.data.counts.WARN}</b> · 참고 <b>{res.data.counts.INFO}</b>
          </span>
        )}
        <span className="flex-1" />
        <button type="button" className={btn2} onClick={res.reload}>
          다시 점검
        </button>
      </div>
      {res.error && <p className="text-sm text-red-600">{errText(res.error)}</p>}
      {!res.data && !res.error && <p className="text-sm text-muted-foreground">점검 중…</p>}
      {res.data && !res.data.findings.length && <p className="text-sm text-[var(--color-success-text)]">지적 사항이 없습니다.</p>}
      <ul className="space-y-2">
        {res.data?.findings.map((f, i) => (
          <li key={`${f.code}-${i}`} className={`rounded-md border p-2.5 text-sm ${SEV_STYLE[f.severity]}`}>
            <div className="flex items-start gap-2">
              <span className="shrink-0 rounded px-1 text-xs font-semibold">{SEVERITY_LABEL[f.severity]}</span>
              <span className="min-w-0 break-words">{f.message}</span>
            </div>
            <p className="mt-1 text-xs text-body">→ {f.fix}</p>
            {f.target && jump && (
              <button type="button" className="mt-1 text-xs text-[var(--color-primary)] hover:underline" onClick={() => jump(f.target!)}>
                {f.target.kind === "line" ? `라인 ${f.target.id}로 이동` : `피처 #${f.target.id} 선택`}
              </button>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function ChatTab({ mode }: { mode: "draw" | "ask" }) {
  const cp = useCopilot()!;
  const [msgs, setMsgs] = useState<Msg[]>([
    {
      role: "bot",
      text:
        mode === "ask"
          ? "사용법·단축키·견적 규칙을 묻거나 '점검해 줘'라고 해 보세요."
          : cp.context.page === "cad2d"
          ? "무엇을 그릴까요? 예: '100x50 사각형 (10,20)', '반지름 20 원', '육각형 반지름 30'"
          : cp.context.page === "model3d"
            ? "예: '100x50x20 상자', '지름 30 높이 50 원통', 'M8 탭 구멍 4개', 'M10x30 볼트 8개', '점검해 줘'"
            : "도면(2D) 또는 3D 모델 화면에서 문장으로 그릴 수 있습니다.",
    },
  ]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => end.current?.scrollIntoView({ block: "end" }), [msgs]);
  const push = (m: Msg) => setMsgs((xs) => [...xs, m].slice(-MAX_MSGS));

  const send = async () => {
    const q = text.trim();
    if (!q || busy) return;
    setText("");
    push({ role: "user", text: q });
    setBusy(true);
    try {
      const a = await api<Answer>("/copilot/ask", { method: "POST", json: { message: q, context: cp.context } });
      push({ role: "bot", text: a.answer, source: a.source, model: a.model, actions: a.actions, related: a.related });
    } catch (e) {
      push({ role: "bot", text: errText(e) });
    } finally {
      setBusy(false);
    }
  };

  const run = async (i: number, a: Action) => {
    const h = cp.handlers.current ?? {};
    let err: string | null = null;
    try {
      if (a.kind === "cad2d" && h.cad2d) err = h.cad2d(a.inputs);
      else if (a.kind === "feature" && h.feature) err = await h.feature(a.feature_type, a.params);
      else if (a.kind === "dialog" && h.dialog) h.dialog(a.dialog, a.values);
    } catch (e) {
      err = errText(e);
    }
    setMsgs((xs) => xs.map((m, j) => (j === i ? { ...m, done: err ? `실행 실패: ${err}` : a.kind === "dialog" ? "창을 열었습니다" : "실행했습니다" } : m)));
  };

  return (
    <div className="flex h-full flex-col">
      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3" aria-live="polite">
        {msgs.map((m, i) => (
          <div key={i} className={m.role === "user" ? "ml-8 rounded-lg bg-[var(--color-primary-light)] p-2 text-sm text-foreground" : "mr-4 rounded-lg border border-line p-2 text-sm text-body"}>
            {m.source && <span className="mb-1 block text-xs text-muted-foreground">{SOURCE_LABEL[m.source]}{m.model ? ` · ${m.model}` : ""}</span>}
            <p className="whitespace-pre-wrap break-words">{m.text}</p>
            {m.actions?.map((a, k) => (
              <div key={k} className="mt-2 rounded-md bg-[var(--color-bg-subtle)] p-2">
                <p className="text-xs font-medium text-foreground">미리보기: {a.summary}</p>
                {a.kind === "cad2d" && <code className="mt-1 block break-all font-mono text-xs text-body">{a.inputs.map((x) => (x === "" ? "⏎" : x)).join(" → ")}</code>}
                {a.kind === "feature" && <code className="mt-1 block break-all font-mono text-xs text-body">{a.feature_type} {JSON.stringify(a.params)}</code>}
                {m.done ? (
                  <p className="mt-1 text-xs text-muted-foreground">{m.done}</p>
                ) : runnable(a, cp.handlers.current ?? {}) ? (
                  <button type="button" className={`${btnPrimary} mt-2`} onClick={() => run(i, a)}>
                    {a.kind === "dialog" ? "열기" : "실행"}
                  </button>
                ) : (
                  <p className="mt-1 text-xs text-muted-foreground">{whereToRun[a.kind]}</p>
                )}
              </div>
            ))}
            {!!m.related?.length && (
              <ul className="mt-2 space-y-1 text-xs">
                {m.related.map((t) => (
                  <li key={t.id}>
                    <details>
                      <summary className="cursor-pointer text-[var(--color-primary)]">{t.q}</summary>
                      <p className="mt-1 text-body">{t.a}</p>
                    </details>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
        {busy && <p className="text-xs text-muted-foreground">생각 중…</p>}
        <div ref={end} />
      </div>
      <form
        className="flex shrink-0 gap-2 border-t border-line p-2"
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
      >
        <label htmlFor="copilot-input" className="sr-only">
          Copilot에게 묻기
        </label>
        <input id="copilot-input" value={text} maxLength={500} onChange={(e) => setText(e.target.value)} placeholder={mode === "draw" ? "그릴 것을 문장으로 입력하세요" : "질문을 입력하세요"} className={field} autoComplete="off" />
        <button type="submit" className={`${btnPrimary} shrink-0 whitespace-nowrap`} disabled={busy || !text.trim()}>
          보내기
        </button>
      </form>
    </div>
  );
}

function HelpTab() {
  const cp = useCopilot()!;
  const topics = useApi<{ topics: Topic[] }>(`/copilot/help?page=${cp.context.page}`);
  const me = useApi<User>("/auth/me");
  return (
    <div className="space-y-3 p-3 text-sm">
      <p className="text-muted-foreground">단축키: Ctrl+. 패널 보이기/감추기 · Esc 닫기</p>
      <ul className="space-y-2">
        {topics.data?.topics.map((t) => (
          <li key={t.id} className="rounded-md border border-line p-2">
            <details>
              <summary className="cursor-pointer font-medium text-foreground">{t.q}</summary>
              <p className="mt-1 text-body">{t.a}</p>
            </details>
          </li>
        ))}
      </ul>
      {me.data && can(me.data, "ADMIN") && <LlmSettings />}
    </div>
  );
}

function LlmSettings() {
  const s = useApi<Settings>("/copilot/settings");
  const [key, setKey] = useState("");
  const [url, setUrl] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const save = (json: { llm_enabled?: boolean; api_key?: string; ollama_enabled?: boolean; ollama_url?: string }) =>
    api<Settings>("/copilot/settings", { method: "PUT", json }).then(
      () => {
        setKey("");
        setUrl(null);
        setNote("저장했습니다");
        s.reload();
      },
      (e) => setNote(errText(e)),
    );
  if (!s.data) return null;
  return (
    <section className="space-y-2 rounded-md border border-line p-2" aria-label="LLM 설정">
      <h2 className="font-medium text-foreground">LLM 연결 (관리자)</h2>
      <p className="text-xs text-muted-foreground">
        순서: 규칙 → Gemini({s.data.gemini_models.join(" → ")}) → 이 PC의 Ollama({s.data.ollama_models.join(" → ")}). 규칙으로 답하지 못한 질문만 보냅니다. 입력한 질문 문장(이메일·긴 숫자는 가림)과 화면 종류·피처 개수·점검 결과 코드가 전송되고, 도면 파일·좌표·견적 금액은 보내지 않습니다. 키는 환경변수 GEMINI_API_KEY(또는 AXCAD_GEMINI_API_KEY)로 두는 것을 권장합니다. 요청은 한 번에 하나씩 처리합니다.
      </p>
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={s.data.llm_enabled} onChange={(e) => save({ llm_enabled: e.target.checked })} />
        Gemini 사용 (인터넷)
      </label>
      <p className="text-xs text-body">
        API 키: {s.data.llm_configured ? (s.data.key_source === "env" ? "환경변수로 설정됨" : "저장됨") : "없음"}
      </p>
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void save({ api_key: key.trim() });
        }}
      >
        <label htmlFor="copilot-key" className="sr-only">
          Gemini API 키
        </label>
        <input id="copilot-key" type="password" value={key} onChange={(e) => setKey(e.target.value)} placeholder="Gemini API 키 (비우면 삭제)" className={field} autoComplete="off" />
        <button type="submit" className={`${btn2} shrink-0 whitespace-nowrap`}>
          저장
        </button>
      </form>
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={s.data.ollama_enabled} onChange={(e) => save({ ollama_enabled: e.target.checked })} />
        Ollama 사용 (이 PC, 인터넷 없이)
      </label>
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (url !== null) void save({ ollama_url: url.trim() });
        }}
      >
        <label htmlFor="copilot-ollama" className="sr-only">
          Ollama 주소
        </label>
        <input id="copilot-ollama" value={url ?? s.data.ollama_url} onChange={(e) => setUrl(e.target.value)} placeholder="http://localhost:11434/v1" className={field} autoComplete="off" />
        <button type="submit" className={`${btn2} shrink-0 whitespace-nowrap`} disabled={url === null}>
          저장
        </button>
      </form>
      {note && <p className="text-xs text-muted-foreground">{note}</p>}
    </section>
  );
}
