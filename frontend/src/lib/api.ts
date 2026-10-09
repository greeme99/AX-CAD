// Fetch wrapper: base /api, Bearer token, one refresh-on-401, {success,data,error} envelope unwrap.
// ponytail: sessionStorage tokens (XSS-readable); move to httpOnly cookies via a BFF if exposed beyond the intranet
const BASE = "/api";
const A = "ax.access";
const R = "ax.refresh";

export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public status = 0,
    public details?: unknown,
  ) {
    super(message);
  }
}

export type User = { user_id: number; login_id: string; user_name: string; roles: string[] };
export type List<T> = { items: T[]; total: number };
export type Project = {
  project_id: number;
  project_code: string;
  project_name: string;
  customer_name: string | null;
  status: string;
  created_by: number;
  created_at: string;
  document_count: number;
};
export type Doc = {
  document_id: number;
  project_id: number;
  doc_no: string;
  doc_type: string;
  title: string;
  status: string;
  current_revision_id: number | null;
  current_revision_no: number | null;
  created_by: number;
  created_at: string;
};

export const setTokens = (access: string, refresh?: string) => {
  sessionStorage.setItem(A, access);
  if (refresh) sessionStorage.setItem(R, refresh);
};
export const clearTokens = () => {
  sessionStorage.removeItem(A);
  sessionStorage.removeItem(R);
};
// ADMIN passes every role check, like the server's need()
export const can = (u: User, ...roles: string[]) => u.roles.includes("ADMIN") || roles.some((r) => u.roles.includes(r));
export const qs = (p: Record<string, string | undefined>) => {
  const s = new URLSearchParams(Object.entries(p).filter((e): e is [string, string] => !!e[1])).toString();
  return s ? "?" + s : "";
};

// overridable so tests can observe the redirect
export const nav = {
  toLogin: () => window.location.assign("/login?next=" + encodeURIComponent(location.pathname + location.search)),
};

const CODE_MSG: Record<string, string> = {
  AUTH_FAILED: "아이디 또는 비밀번호가 올바르지 않습니다",
  AUTH_LOCKED: "계정이 잠겼습니다. 잠시 후 다시 시도하세요",
  DOCUMENT_IN_REVIEW: "검토 중인 도면은 수정할 수 없습니다",
  REVISION_NOT_CURRENT: "최신 리비전이 아닙니다. 최신 리비전을 열어 다시 시도하세요",
  EXPORT_NOT_APPROVED: "승인된 도면만 DXF로 내보낼 수 있습니다",
  DUPLICATE_KEY: "이미 존재하는 번호입니다",
  APPROVER_SELF: "본인을 승인자로 지정할 수 없습니다",
  APPROVER_INVALID: "승인자는 프로젝트 구성원인 검토자여야 합니다",
  INVALID_STATE: "현재 상태에서는 수행할 수 없습니다",
  COMMENT_REQUIRED: "반려 시 의견을 입력하세요",
  FORBIDDEN: "권한이 없습니다",
  GEOM_OPEN_WIRE: "프로파일이 닫혀 있지 않습니다. 빨간 표시된 끝점을 확인하세요",
  GEOM_SELF_INTERSECTION: "프로파일이 자기 교차합니다",
  GEOM_DEGENERATE_EDGE: "길이가 0에 가까운 퇴화 모서리가 있습니다",
  GEOM_INVALID_PARAM: "거리 또는 방향 값이 올바르지 않습니다",
  GEOM_INVALID_WIRE: "프로파일을 하나의 연결된 윤곽으로 만들 수 없습니다",
  GEOM_INVALID_RESULT: "생성된 형상이 유효하지 않습니다",
  GEOM_MULTIPLE_PROFILES: "여러 개의 분리된 프로파일은 아직 지원하지 않습니다",
  GEOM_KERNEL_CRASH: "형상 엔진 오류가 발생했습니다. 다시 시도하세요",
  GEOM_KERNEL_TIMEOUT: "형상 계산 시간이 초과되었습니다",
};
export const errText = (e: unknown) => (e instanceof ApiError ? (CODE_MSG[e.code] ?? e.message) : String(e));

type Init = Omit<RequestInit, "body"> & { body?: BodyInit; json?: unknown; anon?: boolean };

function send(path: string, init: Init) {
  const headers = new Headers(init.headers);
  const t = sessionStorage.getItem(A);
  if (t && !init.anon) headers.set("Authorization", "Bearer " + t);
  let body = init.body;
  if (init.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(init.json);
  }
  return fetch(BASE + path, { ...init, headers, body });
}

let refreshing: Promise<boolean> | null = null;
function refresh() {
  refreshing ??= (async () => {
    const r = sessionStorage.getItem(R);
    if (!r) return false;
    try {
      const j = await (await send("/auth/refresh", { method: "POST", json: { refresh_token: r }, anon: true })).json();
      if (j.success) setTokens(j.data.access_token);
      return !!j.success;
    } catch {
      return false;
    }
  })().finally(() => (refreshing = null));
  return refreshing;
}

async function request(path: string, init: Init) {
  let res = await send(path, init);
  if (res.status === 401 && !init.anon) {
    if (await refresh()) res = await send(path, init);
    if (res.status === 401) {
      clearTokens();
      nav.toLogin();
    }
  }
  return res;
}

async function fail(res: Response): Promise<never> {
  const j = await res.json().catch(() => null);
  throw new ApiError(j?.error?.code ?? "ERROR", j?.error?.message ?? `HTTP ${res.status}`, res.status, j?.error?.details);
}

export async function api<T>(path: string, init: Init = {}): Promise<T> {
  let res: Response;
  try {
    res = await request(path, init);
  } catch (e) {
    throw new ApiError("NETWORK", e instanceof Error ? e.message : "요청 실패");
  }
  const j = await res.json().catch(() => null);
  if (!j?.success) throw new ApiError(j?.error?.code ?? "ERROR", j?.error?.message ?? `HTTP ${res.status}`, res.status, j?.error?.details);
  return j.data as T;
}

// authenticated file download (a plain <a href> cannot send the Bearer header)
export async function download(path: string, filename: string) {
  const res = await request(path, {});
  if (!res.ok) return fail(res);
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
