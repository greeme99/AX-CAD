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
  const r = await api.post("/api/auth/login", { data: { login_id: login, password } });
  expect(r.ok(), `login ${login}`).toBeTruthy();
  return (await r.json()).data.access_token as string;
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
  await page.click("button[type=submit]");
  await page.waitForURL((u) => !u.pathname.startsWith("/login"));
}

export function watchCsp(page: Page): string[] {
  const blocked: string[] = [];
  page.on("console", (m) => {
    if (m.text().includes("Content Security Policy")) blocked.push(m.text());
  });
  return blocked;
}
