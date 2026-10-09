"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

const MAX_BYTES = 50 * 1024 * 1024;

export default function UploadPage() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);

  async function upload(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setWarnings([]);
    if (!file) return setError("CLIENT: DXF 파일을 선택하세요.");
    if (!file.name.toLowerCase().endsWith(".dxf")) return setError("CLIENT_EXT: .dxf 파일만 업로드할 수 있습니다.");
    if (file.size > MAX_BYTES) return setError("CLIENT_SIZE: 파일 크기는 50MB 이하여야 합니다.");

    setBusy(true);
    try {
      const body = new FormData();
      body.append("file", file);
      // ponytail: fetch has no upload progress, busy state only; switch to XHR if a progress bar is needed
      const res = await fetch("/api/dxf", { method: "POST", body });
      const json = await res.json();
      if (!json.success) {
        setError(`${json.error?.code ?? res.status}: ${json.error?.message ?? "업로드 실패"}`);
        return;
      }
      const { revision_id, warnings: w } = json.data;
      if (w?.length) {
        setWarnings(w);
        // 경고를 볼 수 있도록 잠시 후 이동
        setTimeout(() => router.push("/viewer/" + revision_id), 2000);
      } else {
        router.push("/viewer/" + revision_id);
      }
    } catch (err) {
      setError(`NETWORK: ${err instanceof Error ? err.message : "요청 실패"}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto max-w-xl p-8">
      <h1 className="mb-6 text-2xl font-semibold text-foreground">AX-CAD 도면 업로드</h1>
      <form onSubmit={upload} className="space-y-4 rounded-lg border border-line bg-card p-6 shadow-[var(--shadow-card)]">
        <div>
          <label htmlFor="dxf" className="mb-2 block text-sm font-medium text-foreground">
            DXF 파일 (최대 50MB)
          </label>
          <input
            id="dxf"
            type="file"
            accept=".dxf"
            disabled={busy}
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="block w-full text-sm text-body"
          />
        </div>
        <button
          type="submit"
          disabled={busy}
          aria-busy={busy}
          className="rounded-md bg-[var(--color-primary)] px-4 py-2 text-sm font-medium text-white hover:bg-[var(--color-primary-hover)] focus-visible:outline-2 focus-visible:outline-ring disabled:opacity-50"
        >
          {busy ? "업로드 중..." : "업로드"}
        </button>
        {error && (
          <p role="alert" className="text-sm text-red-600">
            {error}
          </p>
        )}
        {warnings.length > 0 && (
          <div role="status" className="text-sm text-warning">
            <p className="font-medium">경고 {warnings.length}건 (잠시 후 뷰어로 이동합니다)</p>
            <ul className="list-disc pl-5">
              {warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          </div>
        )}
      </form>
    </main>
  );
}
