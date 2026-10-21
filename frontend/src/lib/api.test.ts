import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, can, is3dNotice, nav, qs, warnText } from "./api";

const store = new Map<string, string>();
vi.stubGlobal("sessionStorage", {
  getItem: (k: string) => store.get(k) ?? null,
  setItem: (k: string, v: string) => void store.set(k, v),
  removeItem: (k: string) => void store.delete(k),
});
const res = (status: number, body: unknown) => new Response(JSON.stringify(body), { status });
const ok = (data: unknown) => res(200, { success: true, data, error: null });
const fetchMock = vi.fn();
vi.stubGlobal("fetch", fetchMock);

beforeEach(() => {
  store.clear();
  fetchMock.mockReset();
  nav.toLogin = vi.fn();
});

describe("api", () => {
  it("unwraps data and sends the Bearer token", async () => {
    store.set("ax.access", "A1");
    fetchMock.mockResolvedValueOnce(ok({ x: 1 }));
    expect(await api("/p")).toEqual({ x: 1 });
    expect(fetchMock.mock.calls[0][1].headers.get("Authorization")).toBe("Bearer A1");
    expect(fetchMock.mock.calls[0][1].headers.get("X-AXCAD-Local")).toBe("1"); // per-PC CSRF guard
  });

  it("throws {code,message} from the error envelope", async () => {
    fetchMock.mockResolvedValueOnce(res(409, { success: false, data: null, error: { code: "DUPLICATE_KEY", message: "dup" } }));
    await expect(api("/p")).rejects.toMatchObject({ code: "DUPLICATE_KEY", message: "dup", status: 409 });
  });

  it("exposes error details", async () => {
    fetchMock.mockResolvedValueOnce(res(422, { success: false, data: null, error: { code: "GEOM_OPEN_WIRE", message: "open", details: { dangling: [[1, 2]] } } }));
    await expect(api("/p")).rejects.toMatchObject({ details: { dangling: [[1, 2]] } });
  });

  it("refreshes once on 401 and retries", async () => {
    store.set("ax.access", "old");
    store.set("ax.refresh", "R1");
    fetchMock
      .mockResolvedValueOnce(res(401, { success: false, error: { code: "AUTH_INVALID", message: "x" } }))
      .mockResolvedValueOnce(ok({ access_token: "new" }))
      .mockResolvedValueOnce(ok("done"));
    expect(await api("/p")).toBe("done");
    expect(store.get("ax.access")).toBe("new");
    expect(fetchMock.mock.calls[2][1].headers.get("Authorization")).toBe("Bearer new");
    expect(nav.toLogin).not.toHaveBeenCalled();
  });

  it("clears tokens and redirects when refresh fails", async () => {
    store.set("ax.access", "old");
    store.set("ax.refresh", "R1");
    fetchMock
      .mockResolvedValueOnce(res(401, { success: false, error: { code: "AUTH_INVALID", message: "x" } }))
      .mockResolvedValueOnce(res(401, { success: false, error: { code: "AUTH_INVALID", message: "x" } }));
    await expect(api("/p")).rejects.toMatchObject({ code: "AUTH_INVALID" });
    expect(store.size).toBe(0);
    expect(nav.toLogin).toHaveBeenCalledOnce();
  });
});

describe("helpers", () => {
  it("qs drops empty values", () => expect(qs({ q: "a b", status: "" })).toBe("?q=a+b"));
  it("can: ADMIN passes any role", () => {
    const u = (roles: string[]) => ({ user_id: 1, login_id: "a", user_name: "a", roles });
    expect(can(u(["ADMIN"]), "REVIEWER")).toBe(true);
    expect(can(u(["VIEWER"]), "DESIGNER")).toBe(false);
  });
  it("explains 3D-in-DXF warnings and leaves other codes as they are", () => {
    expect(warnText("VIEW_ONLY_3D:4")).toBe("3D 요소 4개는 위에서 본 선으로만 표시합니다 (편집·견적 제외)");
    expect(warnText("MODEL_3D_USE_STEP")).toContain("STEP");
    expect(warnText("UNITS_ASSUMED_MM")).toContain("mm로 가정");
    expect(warnText("METRIC_OPEN_CONTOUR:3")).toBe("닫히지 않은 윤곽 3개가 있습니다. 절단길이·면적을 확인하세요");
    expect(warnText("UNSUPPORTED_ENTITY:3DSOLID x1")).toBe("UNSUPPORTED_ENTITY:3DSOLID x1");
    expect(["VIEW_ONLY_3D:2", "MODEL_3D_USE_STEP", "UNSUPPORTED_ENTITY:3DSOLID x1"].filter(is3dNotice)).toHaveLength(2);
  });
});
