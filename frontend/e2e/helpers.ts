// Shared helpers for AX-CAD Playwright E2E tests (run against a live stack).
import { readFileSync } from "node:fs";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

export const ADMIN = process.env.E2E_ADMIN_LOGIN ?? "e2e";
export const ADMIN_PW =
  process.env.E2E_ADMIN_PASSWORD ??
  (process.env.E2E_ADMIN_PASSWORD_FILE ? readFileSync(process.env.E2E_ADMIN_PASSWORD_FILE, "utf8").trim() : "");

// CI sets E2E_REQUIRE: a missing prerequisite fails the run instead of skipping quietly.
export function need(ok: boolean, why: string) {
  if (process.env.E2E_REQUIRE) {
    expect(ok, why).toBeTruthy();
  } else {
    test.skip(!ok, why);
  }
}

export async function apiToken(api: APIRequestContext, login: string, password: string): Promise<string> {
  for (let attempt = 0; attempt < 4; attempt++) {
    const r = await api.post("/api/auth/login", { data: { login_id: login, password } });
    if (r.status() === 429 || r.status() === 503) {
      await new Promise((res) => setTimeout(res, 6500));
      continue;
    }
    expect(r.ok(), `login ${login} (${r.status()})`).toBeTruthy();
    return (await r.json()).data.access_token as string;
  }
  throw new Error(`login ${login}: rate-limited after retries`);
}

export async function apiCall(
  api: APIRequestContext,
  tok: string,
  method: "GET" | "POST" | "PUT" | "DELETE",
  path: string,
  data?: unknown,
) {
  const r = await api.fetch(path, { method, data, headers: { Authorization: `Bearer ${tok}` } });
  expect(r.ok(), `${method} ${path}: ${await r.text()}`).toBeTruthy();
  return (await r.json()).data;
}

export async function uiLogin(page: Page, id: string, password: string) {
  await page.goto("/login");
  await page.fill("input[name=login_id]", id);
  await page.fill("input[name=password]", password);
  for (let attempt = 0; attempt < 4; attempt++) {
    const [resp] = await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/auth/login") && r.request().method() === "POST"),
      page.click("button[type=submit]"),
    ]);
    if (resp.status() === 429 || resp.status() === 503) {
      await page.waitForTimeout(6500);
      continue;
    }
    expect(resp.ok(), `uiLogin ${id} (${resp.status()})`).toBeTruthy();
    await page.waitForURL((u) => !u.pathname.startsWith("/login"));
    return;
  }
  throw new Error(`uiLogin ${id}: rate-limited after retries`);
}

export function watchCsp(page: Page): string[] {
  const blocked: string[] = [];
  page.on("console", (m) => {
    if (m.text().includes("Content Security Policy")) blocked.push(m.text());
  });
  return blocked;
}
