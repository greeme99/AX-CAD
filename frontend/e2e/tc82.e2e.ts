// TC-82: quote create -> calculate -> adjust -> validate -> PDF, through the UI (Track 7: no approval).
// Setup goes through the API as an admin; every name carries a run suffix, nothing is deleted.
import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const ADMIN = process.env.E2E_ADMIN_LOGIN ?? "e2e";
const ADMIN_PW = process.env.E2E_ADMIN_PASSWORD ?? (process.env.E2E_ADMIN_PASSWORD_FILE ? readFileSync(process.env.E2E_ADMIN_PASSWORD_FILE, "utf8").trim() : "");
const run = Date.now().toString(36);
// CI sets E2E_REQUIRE: a missing prerequisite fails the run instead of skipping it quietly
const need = (ok: boolean, why: string) => (process.env.E2E_REQUIRE ? expect(ok, why).toBeTruthy() : test.skip(!ok, why));

async function token(api: APIRequestContext, login: string, password: string) {
  const r = await api.post("/api/auth/login", { data: { login_id: login, password } });
  expect(r.ok(), `login ${login}`).toBeTruthy();
  return (await r.json()).data.access_token as string;
}

async function call(api: APIRequestContext, tok: string, method: "GET" | "POST", path: string, data?: unknown) {
  const r = await api.fetch(path, { method, data, headers: { Authorization: `Bearer ${tok}` } });
  expect(r.ok(), `${method} ${path}: ${await r.text()}`).toBeTruthy();
  return (await r.json()).data;
}

async function login(page: Page, id: string, password: string) {
  await page.goto("/login");
  await page.fill("input[name=login_id]", id);
  await page.fill("input[name=password]", password);
  await page.click("button[type=submit]");
  await page.waitForURL((u) => !u.pathname.startsWith("/login"));
}

test("TC-82 quote: create, adjust, validate, PDF", async ({ browser, request, baseURL }) => {
  need(!!ADMIN_PW, "set E2E_ADMIN_PASSWORD or E2E_ADMIN_PASSWORD_FILE");
  const admin = await token(request, ADMIN, ADMIN_PW);
  const master = await request.get("/api/master-versions/current", { headers: { Authorization: `Bearer ${admin}` } });
  need(master.ok(), "needs an ACTIVE master data version (기준정보)");

  // --- setup: an estimator, a project with a customer, a drawing with a title block
  const pw = { est: `E2e-${randomUUID()}` };
  const est = await call(request, admin, "POST", "/api/users", { login_id: `est-${run}`, user_name: `견적-${run}`, password: pw.est, roles: ["ESTIMATOR"] });
  const project = await call(request, admin, "POST", "/api/projects", { project_code: `E2E-${run}`, project_name: `TC-82 ${run}`, customer_name: "E2E 고객(주)" });
  await call(request, admin, "POST", `/api/projects/${project.project_id}/members`, { user_id: est.user_id });
  const doc = await call(request, admin, "POST", `/api/projects/${project.project_id}/documents`, { doc_no: `E2E-${run}`, doc_type: "DRAWING", title: "E2E 브래킷" });
  const up = await request.post(`/api/documents/${doc.document_id}/dxf`, {
    headers: { Authorization: `Bearer ${admin}` },
    multipart: { file: { name: "bracket.dxf", mimeType: "application/dxf", buffer: readFileSync(join(__dirname, "fixtures", "bracket.dxf")) } },
  });
  expect(up.ok(), await up.text()).toBeTruthy();
  const revisionId = (await up.json()).data.revision_id as string;

  // --- estimator: viewer -> metrics -> create (title-block defaults)
  const ctx = { baseURL, acceptDownloads: true, viewport: { width: 1440, height: 1000 } };
  const estCtx = await browser.newContext(ctx);
  const page = await estCtx.newPage();
  await login(page, `est-${run}`, pw.est);
  await page.goto(`/viewer/${revisionId}?doc=${doc.document_id}`);
  await page.getByRole("button", { name: "견적 메트릭" }).click();
  const form = page.locator("form[aria-label='견적 생성']");
  await expect(form.getByLabel("수량")).toHaveValue("5"); // read from the title block
  await expect(form.getByLabel("재질")).toHaveValue("SS400");
  await expect(form.getByLabel("두께(mm)")).toHaveValue("2");
  await form.getByRole("button", { name: "견적 생성" }).click();
  await page.waitForURL(/\/quotes\/\d+$/);
  const quoteId = Number(page.url().split("/").pop());
  const lines = page.locator("table[aria-label='견적 라인'] tbody tr");
  await expect(lines.first()).toBeVisible();
  expect(await lines.count()).toBeGreaterThan(1);
  await expect(page.locator("section[aria-label='원가 요약']")).toContainText("합계");

  // --- adjust the material line with a reason (FN-19)
  await page.getByRole("button", { name: /SS400/ }).first().click();
  const drawer = page.locator("aside[role=dialog]");
  await drawer.getByLabel("값").fill("15000");
  await drawer.getByLabel("사유 (5자 이상)").fill("E2E 고객 지정 소재 단가");
  await drawer.getByRole("button", { name: "적용" }).click();
  await expect(page.locator("table[aria-label='견적 라인'] tbody tr", { hasText: "MANUAL" })).toBeVisible();
  await drawer.getByRole("button", { name: "닫기" }).click();

  // --- validate: warnings allowed, no ERROR (FN-20)
  await page.getByRole("button", { name: "검증" }).click();
  const check = page.locator("section[aria-label='검증']");
  await expect(check.locator("li, p").first()).toBeVisible();
  await expect(check).not.toContainText("⛔");

  // --- the quote document, any time (FN-21, no approval step)
  const bar = page.locator("[aria-label='견적서 출력']");
  const [pdf] = await Promise.all([page.waitForEvent("download"), bar.getByRole("button", { name: "견적서 PDF" }).click()]);
  expect(pdf.suggestedFilename()).toMatch(/^Q-\d{8}-\d{6}\.pdf$/);
  expect(readFileSync((await pdf.path())!).subarray(0, 5).toString()).toBe("%PDF-");

  const estTok = await token(request, `est-${run}`, pw.est);
  const q = await call(request, estTok, "GET", `/api/quotes/${quoteId}`);
  expect(q.status).toBe("DRAFT");
  expect(q.lines.some((l: { override_amount: string | null }) => l.override_amount !== null)).toBeTruthy();
  await estCtx.close();
});
