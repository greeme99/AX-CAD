// E2E: Surface S6~S8 + PR #78~#81 (SurfaceDialog create, FeatureTree, Curvature/Continuity/Draft/Zebra overlays & clear).
// Self-contained against origin/main (same pattern as tc82.e2e.ts).
import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const ADMIN = process.env.E2E_ADMIN_LOGIN ?? "e2e";
const ADMIN_PW =
  process.env.E2E_ADMIN_PASSWORD ??
  (process.env.E2E_ADMIN_PASSWORD_FILE ? readFileSync(process.env.E2E_ADMIN_PASSWORD_FILE, "utf8").trim() : "");
const run = Date.now().toString(36);
const need = (ok: boolean, why: string) =>
  process.env.E2E_REQUIRE ? expect(ok, why).toBeTruthy() : test.skip(!ok, why);

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

test("3D modeler: Surface boundary create + curvature/continuity/draft/zebra analysis", async ({
  browser,
  request,
  baseURL,
}) => {
  need(!!ADMIN_PW, "set E2E_ADMIN_PASSWORD or E2E_ADMIN_PASSWORD_FILE");
  const admin = await token(request, ADMIN, ADMIN_PW);

  // --- setup: designer, project, 3D model document
  const pw = `E2e-${randomUUID()}`;
  const loginId = `des-sf-${run}`;
  const designer = await call(request, admin, "POST", "/api/users", {
    login_id: loginId,
    user_name: `곡면-${run}`,
    password: pw,
    roles: ["DESIGNER"],
  });
  const project = await call(request, admin, "POST", "/api/projects", {
    project_code: `E2E-SF-${run}`,
    project_name: `Surface E2E ${run}`,
    customer_name: "E2E 고객(주)",
  });
  await call(request, admin, "POST", `/api/projects/${project.project_id}/members`, {
    user_id: designer.user_id,
  });
  const doc = await call(request, admin, "POST", `/api/projects/${project.project_id}/documents`, {
    doc_no: `SF-${run}`,
    doc_type: "DRAWING",
    title: "E2E 곡면 테스트",
  });

  // --- open 3D modeler
  const ctx = await browser.newContext({ baseURL, acceptDownloads: true, viewport: { width: 1440, height: 1000 } });
  const page = await ctx.newPage();
  const blocked: string[] = [];
  const pageErrors: string[] = [];
  page.on("console", (m) => m.text().includes("Content Security Policy") && blocked.push(m.text()));
  page.on("pageerror", (e) => pageErrors.push(e.message));

  await login(page, loginId, pw);
  await page.goto(`/model/${doc.document_id}`);

  await expect(page.locator('canvas[aria-label^="3D 모델 뷰포트"]')).toBeVisible();

  // --- Surface S6: create SURF_BOUNDARY (4-edge closed loop, self-contained without sketch)
  await page.getByRole("button", { name: "곡면", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "곡면" });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("작업").selectOption("SURF_BOUNDARY");
  await dialog
    .getByLabel("변의 점 (mm)")
    .fill("0,0,0; 40,0,0\n40,0,0; 40,40,0\n40,40,0; 0,40,0\n0,40,0; 0,0,0");
  await dialog.getByRole("button", { name: "만들기" }).click();
  await expect(dialog).toBeHidden({ timeout: 20_000 });

  // Verify feature created and selected in FeatureTree
  const featureList = page.getByRole("list", { name: "Feature 목록" });
  await expect(featureList.getByRole("button", { pressed: true })).toContainText("경계 곡면");

  // --- Surface S8 + PR #81: Curvature, Continuity, Signed Draft (+Z), Zebra, and Clear
  const analysis = page.locator('section[aria-label="곡면 분석"]');
  await expect(analysis).toBeVisible();

  // 1) Curvature map
  await analysis.getByRole("button", { name: "곡률", exact: true }).click();
  await expect(analysis.getByLabel("곡률 종류")).toBeVisible({ timeout: 15_000 });
  await expect(analysis).toContainText("1/mm²");

  // 2) Continuity check
  await analysis.getByRole("button", { name: "연속성", exact: true }).click();
  await expect(analysis.getByText(/모서리|면이 맞닿는 모서리가 없습니다/)).toBeVisible({ timeout: 15_000 });

  // 3) Signed draft (+Z)
  await analysis.getByRole("button", { name: "구배 (+Z)", exact: true }).click();
  await expect(analysis).toContainText("언더컷", { timeout: 15_000 });

  // 4) Clear overlay
  const clearBtn = analysis.getByRole("button", { name: "지우기", exact: true });
  await expect(clearBtn).toBeVisible();
  await clearBtn.click();
  await expect(clearBtn).toBeHidden();

  // 5) Zebra stripes toggle on & off
  const zebraBtn = analysis.getByRole("button", { name: "제브라", exact: true });
  await zebraBtn.click();
  await expect(zebraBtn).toHaveAttribute("aria-pressed", "true");
  await zebraBtn.click();
  await expect(zebraBtn).toHaveAttribute("aria-pressed", "false");

  expect(blocked, "CSP violations").toEqual([]);
  expect(pageErrors, "uncaught page errors").toEqual([]);
  await ctx.close();
});
