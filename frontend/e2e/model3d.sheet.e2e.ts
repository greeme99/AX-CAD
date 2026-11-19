// E2E: 3D modeler + Sheet Metal S1~S5 (base flange, bend flange, flat pattern DXF)
// + UI Phase 3 (InlinePanel Escape close, FeatureTimeline).
import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { ADMIN, ADMIN_PW, apiCall, apiToken, need, uiLogin, watchCsp } from "./helpers";

const run = Date.now().toString(36);

test("3D modeler: Sheet Metal base + flange + flat DXF + InlinePanel + Timeline", async ({
  browser,
  request,
  baseURL,
}) => {
  need(!!ADMIN_PW, "set E2E_ADMIN_PASSWORD or E2E_ADMIN_PASSWORD_FILE");
  const admin = await apiToken(request, ADMIN, ADMIN_PW);

  // --- setup: designer, project, 3D model document, closed 100x60 XY sketch
  const pw = `E2e-${randomUUID()}`;
  const loginId = `des-sh-${run}`;
  const designer = await apiCall(request, admin, "POST", "/api/users", {
    login_id: loginId,
    user_name: `설계-${run}`,
    password: pw,
    roles: ["DESIGNER"],
  });
  const project = await apiCall(request, admin, "POST", "/api/projects", {
    project_code: `E2E-SH-${run}`,
    project_name: `Sheet E2E ${run}`,
    customer_name: "E2E 고객(주)",
  });
  await apiCall(request, admin, "POST", `/api/projects/${project.project_id}/members`, {
    user_id: designer.user_id,
  });
  const doc = await apiCall(request, admin, "POST", `/api/projects/${project.project_id}/documents`, {
    doc_no: `SH-${run}`,
    doc_type: "DRAWING",
    title: "E2E 판금 브래킷",
  });
  await apiCall(request, admin, "POST", `/api/documents/${doc.document_id}/features`, {
    feature_type: "SKETCH",
    params: {
      plane: { origin: [0, 0, 0], normal: [0, 0, 1], xdir: [1, 0, 0] },
      entities: [
        { id: 1, type: "LINE", start: [0, 0], end: [100, 0] },
        { id: 2, type: "LINE", start: [100, 0], end: [100, 60] },
        { id: 3, type: "LINE", start: [100, 60], end: [0, 60] },
        { id: 4, type: "LINE", start: [0, 60], end: [0, 0] },
      ],
      constraints: [],
    },
  });

  // --- open 3D modeler
  const ctx = await browser.newContext({ baseURL, acceptDownloads: true, viewport: { width: 1440, height: 1000 } });
  const page = await ctx.newPage();
  const blocked = watchCsp(page);

  await uiLogin(page, loginId, pw);
  await page.goto(`/model/${doc.document_id}`);

  // 3D viewport canvas and Timeline render
  await expect(page.locator("canvas").first()).toBeVisible();
  await expect(page.getByText("Timeline")).toBeVisible();

  // --- UI Phase 3: InlinePanel opens for Primitive and closes on Escape
  await page.getByRole("button", { name: "원시 형상" }).click();
  const inlinePanel = page.locator("aside[role='dialog']");
  await expect(inlinePanel).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(inlinePanel).toBeHidden();

  // --- Sheet Metal S1: create SHEET_BASE via SheetDialog
  await page.getByRole("button", { name: "판금" }).click();
  const sheetModal = page.locator("dialog[open]");
  await expect(sheetModal).toBeVisible();
  await sheetModal.locator("button[type='submit']").click();
  await expect(sheetModal).toBeHidden();

  // Metrics panel updates with non-zero volume
  await expect(page.locator("section[aria-label='계측']")).toContainText("mm³");

  // --- Sheet Metal S2: add SHEET_FLANGE on edge 0
  await page.getByRole("button", { name: "판금" }).click();
  await expect(sheetModal).toBeVisible();
  await sheetModal.getByLabel("플랜지 · 헴 · 조그 (절곡)").check();
  await sheetModal.locator("button[type='submit']").click();
  await expect(sheetModal).toBeHidden();

  // --- Sheet Metal flat pattern DXF download
  const flatBtn = page.getByRole("button", { name: "전개도 DXF" });
  await expect(flatBtn).toBeVisible();
  const [dxf] = await Promise.all([page.waitForEvent("download"), flatBtn.click()]);
  expect(dxf.suggestedFilename()).toMatch(/-flat\.dxf$/);
  const dxfText = readFileSync((await dxf.path())!, "utf8");
  expect(dxfText).toContain("SECTION");
  expect(dxfText).toContain("ENTITIES");

  expect(blocked, "CSP violations").toEqual([]);
  await ctx.close();
});
