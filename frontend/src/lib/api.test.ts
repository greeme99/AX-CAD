import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, can, nav, qs } from "./api";

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
  });

  it("throws {code,message} from the error envelope", async () => {
    fetchMock.mockResolvedValueOnce(res(409, { success: false, data: null, error: { code: "DUPLICATE_KEY", message: "dup" } }));
    await expect(api("/p")).rejects.toMatchObject({ code: "DUPLICATE_KEY", message: "dup", status: 409 });
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
});
