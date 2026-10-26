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

export type User = { user_id: number; login_id: string; user_name: string; roles: string[]; local?: boolean };
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
  current_revision_id: string | null; // 32-hex
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
  LOCAL_ONLY: "시작 메뉴(또는 바탕화면)의 'AX-CAD'로 다시 여세요",
  QUOTE_HAS_ERRORS: "검증 ERROR가 있어 견적서를 낼 수 없습니다. 검증 결과를 먼저 해결하세요",
  REVISION_NOT_CURRENT: "최신 리비전이 아닙니다. 최신 리비전을 열어 다시 시도하세요",
  EDIT_LAYER_EXISTS: "같은 이름의 레이어가 있습니다",
  DWG_CONVERTER_MISSING: "DWG 변환기가 설치되어 있지 않습니다. ODA File Converter(무료)를 설치한 뒤 AX-CAD를 다시 시작하거나, DXF로 저장해 올리세요",
  DWG_INVALID_FILE: "DWG 파일이 아닙니다",
  DWG_CONVERT_FAILED: "DWG를 변환하지 못했습니다. 손상된 파일이거나 변환기가 지원하지 않는 버전입니다",
  DWG_CONVERT_TIMEOUT: "DWG 변환 시간(120초)이 초과되었습니다",
  PLOT_TOO_LARGE: "출력할 내용이 너무 많습니다(문자 100만 자 또는 PDF 50MB 초과). 축척을 키우거나 레이어를 숨기세요",
  PLOT_FAILED: "도면을 PDF로 만들지 못했습니다",
  EDIT_LAYER_INVALID: "사용할 수 없는 레이어 이름입니다 (< > / \\ \" : ; ? * | = ' 불가, 0·Defpoints 레이어는 이름 변경 불가)",
  EDIT_LAYER_NOT_FOUND: "레이어를 찾을 수 없습니다",
  EDIT_LAYER_LOCKED: "잠긴 레이어는 수정할 수 없습니다",
  EDIT_LINETYPE_NOT_FOUND: "도면에 없는 선종류입니다",
  DUPLICATE_KEY: "이미 존재하는 번호입니다",
  INVALID_STATE: "현재 상태에서는 수행할 수 없습니다",
  FORBIDDEN: "권한이 없습니다",
  GEOM_OPEN_WIRE: "프로파일이 닫혀 있지 않습니다. 빨간 표시된 끝점을 확인하세요",
  GEOM_SELF_INTERSECTION: "프로파일이 자기 교차합니다",
  GEOM_DEGENERATE_EDGE: "길이가 0에 가까운 퇴화 모서리가 있습니다",
  GEOM_INVALID_PARAM: "Feature 파라미터가 올바르지 않습니다 (회전축이 프로파일을 가로지르는지, 입력 형상이 이미 사용 중인지 확인하세요)",
  GEOM_BOOLEAN_FAILED: "Boolean 연산에 실패했습니다",
  GEOM_REF_LOST: "고른 모서리·면을 바뀐 형상에서 찾을 수 없습니다. 3D 화면에서 다시 고르세요",
  GEOM_EMPTY_RESULT: "연산 결과 형상이 비어 있습니다 (두 형상이 겹치는지 확인하세요)",
  FEATURE_IN_USE: "다른 Feature(돌출·회전·Boolean·가공)의 입력으로 사용 중입니다. 그 Feature를 먼저 삭제하거나 억제하세요",
  FEATURE_NOT_MOVABLE: "더 옮길 수 없습니다 (트리 끝이거나, Feature는 자기가 쓰는 Feature보다 뒤에 있어야 합니다)",
  GEOM_FEATURE_FAILED: "가공 Feature를 만들 수 없습니다. 반지름·거리·두께가 형상보다 크거나 구멍 위치가 형상 밖인지 확인하세요",
  FEATURE_NOT_EDITABLE: "가져온 형상은 파라미터를 수정할 수 없습니다",
  STEP_INVALID_FILE: "STEP/IGES 파일이 아니거나 외부 파일 참조를 포함하고 있습니다",
  STEP_READ_FAILED: "파일을 읽을 수 없습니다. 손상되었거나 지원하지 않는 형식입니다",
  STEP_EMPTY: "파일에 형상이 없습니다",
  STEP_TIMEOUT: "파일 처리 시간(120초)이 초과되었습니다",
  MODEL_EMPTY: "내보낼 3D 형상이 없습니다",
  GEOM_EXPORT_VERIFY_FAILED: "내보낸 파일을 다시 읽어 확인하는 데 실패해 내려받기를 막았습니다. 관리자에게 알려 주세요",
  MASTER_FROZEN: "확정된 기준정보는 바꿀 수 없습니다. 복사해 새 버전을 만드세요",
  MASTER_NOT_FOUND: "기준정보 버전을 찾을 수 없습니다",
  METRIC_FAILED: "메트릭을 계산할 수 없습니다",
  FILE_TOO_LARGE: "파일이 너무 큽니다",
  SERVER_BUSY: "서버가 다른 작업을 처리 중입니다. 잠시 후 다시 시도하세요",
  QTO_BASE_INVALID: "기존 도면은 같은 프로젝트의 다른 리비전이어야 합니다",
  QTO_FAILED: "도면에서 공사 수량을 산출할 수 없습니다",
  GEOM_INVALID_WIRE: "프로파일을 하나의 연결된 윤곽으로 만들 수 없습니다",
  GEOM_INVALID_RESULT: "생성된 형상이 유효하지 않습니다",
  GEOM_MULTIPLE_PROFILES: "윤곽이 서로 떨어져 있습니다 (도면 프로파일은 닫힌 윤곽 하나, 스윕 경로는 한 줄로 이어진 선이어야 합니다)",
  DOCUMENT_HAS_REVISION: "이미 리비전이 있는 도면입니다. 빈 도면 시작은 리비전이 없는 새 도면에서만 할 수 있습니다",
  SKETCH_CONFLICT: "스케치 구속이 서로 충돌합니다. 빨간 구속을 지우거나 값을 고치세요",
  SKETCH_INVALID_CONSTRAINT: "이 요소들에는 그 구속을 걸 수 없습니다",
  SKETCH_INVALID_ENTITY: "구속이 없는 요소를 가리킵니다",
  SKETCH_TOO_LARGE: "스케치 요소(500개)나 구속(1,000개)이 너무 많습니다",
  GEOM_KERNEL_CRASH: "형상 엔진 오류가 발생했습니다. 다시 시도하세요",
  GEOM_KERNEL_TIMEOUT: "형상 계산 시간이 초과되었습니다",
};
export const errText = (e: unknown) => (e instanceof ApiError ? (CODE_MSG[e.code] ?? e.message) : String(e));
// DXF/metric warnings (core/dxf/reader.py, core/quote_engine/metrics2d.py) a user can act on,
// in words ({n} = the count after ":"); the rest stay as their codes
const UNITS = "도면 단위가 지정되지 않아 mm로 가정했습니다. 치수(축척)를 확인하세요";
const WARN_TEXT: Record<string, string> = {
  MODEL_3D_USE_STEP: "3D 모델(솔리드·곡면)은 표시하지 않습니다. 3D 모델은 STEP 파일로 올려 주세요",
  VIEW_ONLY_3D: "3D 요소 {n}개는 위에서 본 선으로만 표시합니다 (편집·견적 제외)",
  UNITS_ASSUMED_MM: UNITS,
  METRIC_UNITS_ASSUMED_MM: UNITS,
  METRIC_OPEN_CONTOUR: "닫히지 않은 윤곽 {n}개가 있습니다. 절단길이·면적을 확인하세요",
  METRIC_DUPLICATE_GEOMETRY: "겹친 선 {n}개는 한 번만 셌습니다. 도면을 확인하세요",
  METRIC_NO_OUTER_CONTOUR: "외곽 윤곽을 찾지 못해 면적·중량이 산출되지 않았습니다",
  METRIC_MULTIPLE_PARTS: "분리된 윤곽 {n}개 중 가장 큰 것만 부품 외곽으로 봤습니다",
  QTO_OPEN_ROOM: "닫히지 않은 실 윤곽 {n}개는 면적에서 빠졌습니다",
  QTO_NOTHING_MAPPED: "공사 수량 레이어(ROOM*, WALL*, DOOR* 등)를 하나도 찾지 못했습니다",
  QTO_ROOM_NAMES_SKIPPED: "실과 문자가 너무 많아 일부 실 이름을 읽지 않았습니다",
  DWG_CONVERTED: "DWG를 DXF로 변환해 저장했습니다 ({n})",
  DWG_CONVERT_PARTIAL: "변환기가 오류를 보고했습니다. 빠진 객체가 없는지 원본과 비교하세요",
};
export const warnText = (w: string) => {
  const [code, n = ""] = w.split(":", 2);
  return Object.hasOwn(WARN_TEXT, code) ? WARN_TEXT[code].replace("{n}", n) : w;
};
export const is3dNotice = (w: string) => w === "MODEL_3D_USE_STEP" || w.startsWith("VIEW_ONLY_3D:");

type Init = Omit<RequestInit, "body"> & { body?: BodyInit; json?: unknown; anon?: boolean };

// per-PC mode (Track 7): start.ps1 opens /#axcad=<install token>; the API refuses every request
// without it, so other sites and other Windows accounts get nothing. Kept for later tabs/bookmarks.
const L = "ax.local";
function localToken() {
  const m = /^#axcad=([0-9a-f]{64})$/.exec(location.hash);
  if (m) {
    localStorage.setItem(L, m[1]);
    history.replaceState(null, "", location.pathname + location.search);
  }
  return localStorage.getItem(L) ?? "1";
}

function send(path: string, init: Init) {
  const headers = new Headers(init.headers);
  headers.set("X-AXCAD-Local", localToken());
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
export async function download(path: string, filename: string, init: Init = {}) {
  const res = await request(path, init);
  if (!res.ok) return fail(res);
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
