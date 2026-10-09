import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";

// GET `path` (null = skip); data/error are undefined while the path changes. reload() refetches.
export function useApi<T>(path: string | null) {
  const [s, setS] = useState<{ path: string; data?: T; error?: ApiError }>();
  const [n, setN] = useState(0);
  useEffect(() => {
    if (!path) return;
    let live = true;
    api<T>(path).then(
      (data) => live && setS({ path, data }),
      (error) => live && setS({ path, error }),
    );
    return () => {
      live = false;
    };
  }, [path, n]);
  const ok = s?.path === path;
  return { data: ok ? s.data : undefined, error: ok ? s.error : undefined, reload: () => setN((x) => x + 1) };
}
