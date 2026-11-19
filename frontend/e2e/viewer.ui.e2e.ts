// E2E: 2D viewer + UI Phase 3 (DynamicInput F12 toggle) + AI Copilot rule-based check + BOM page.
import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { ADMIN, ADMIN_PW, apiCall, apiToken, need, uiLogin, watchCsp } from "./helpers";

const run = Date.now().toString(36);

test("2D viewer: DynamicInput (F12) + AI Copilot rule check + BOM navigation", async ({
  browser,
  request,
  baseURL,
}) => {
  need(!!ADMIN_PW, "set E2E_ADMIN_PASSWORD or E2E_ADMIN_PASSWORD_FILE");
  const admin = await apiToken(request, ADMIN, ADMIN_PW);

  // --- setup: designer, project, 2D drawing with bracket.dxf
  const pw = `E2e-${randomUUID()}`;
  const loginId = `des-2d-${run}`;
  const designer = await apiCall(request, admin, "POST", "/api/users", {
    login_id: loginId,
    user_name: `뷰어-${run}`,
    password: pw,
    roles: ["DESIGNER"],
  });
  const project = await apiCall(request, admin, "POST", "/api/projects", {
    project_code: `E2E-2D-${run}`,
    project_name: `Viewer E2E ${run}`,
    customer_name: "E2E 고객(주)",
  });
  await apiCall(request, admin, "POST", `/api/projects/${project.project_id}/members`, {
    user_id: designer.user_id,
  });
  const doc = await apiCall(request, admin, "POST", `/api/projects/${project.project_id}/documents`, {
    doc_no: `VW-${run}`,
    doc_type: "DRAWING",
    title: "E2E 2D 뷰어 테스트",
  });
  const up = await request.post(`/api/documents/${doc.document_id}/dxf`, {
    headers: { Authorization: `Bearer ${admin}` },
    multipart: {
      file: {
        name: "bracket.dxf",
        mimeType: "application/dxf",
        buffer: readFileSync(join(__dirname, "fixtures", "bracket.dxf")),
      },
    },
  });
  expect(up.ok(), await up.text()).toBeTruthy();
  const revisionId = (await up.json()).data.revision_id as string;

  // --- open 2D viewer
  const ctx = await browser.newContext({ baseURL, acceptDownloads: true, viewport: { width: 1440, height: 1000 } });
  const page = await ctx.newPage();
  const blocked = watchCsp(page);

  await uiLogin(page, loginId, pw);
  await page.goto(`/viewer/${revisionId}?doc=${doc.document_id}`);

  const canvas = page.locator("canvas").first();
  await expect(canvas).toBeVisible();

  // --- UI Phase 3: switch to LINE tool ('l'), move pointer over canvas, verify DynamicInput ABS/REL toggle & SELECT hide
  await page.keyboard.press("l");
  const box = await canvas.boundingBox();
  expect(box).not.toBeNull();
  await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2);

  const dynModeBtn = page.locator("button[title='# 키로 전환']");
  await expect(dynModeBtn).toBeVisible();
  await expect(dynModeBtn).toHaveText("ABS");
  await page.keyboard.press("v");
  await expect(dynModeBtn).toBeHidden();

  // --- AI Copilot: open via header button, verify rule-based check runs without LLM key
  const copilotPanel = page.locator("aside#copilot-panel");
  if (!(await copilotPanel.isVisible())) {
    await page.getByRole("button", { name: "AI Copilot" }).click();
  }
  await expect(copilotPanel).toBeVisible();
  await expect(copilotPanel.getByRole("button", { name: "다시 점검" })).toBeVisible();
  await copilotPanel.getByRole("button", { name: "다시 점검" }).click();
  await expect(copilotPanel.getByText("점검 중…")).toBeHidden({ timeout: 15_000 });

  // Close Copilot cleanly
  await copilotPanel.getByRole("button", { name: "Copilot 감추기" }).click();
  await expect(copilotPanel).toBeHidden();

  // --- BOM page navigation check
  await page.goto(`/documents/${doc.document_id}/bom`);
  await expect(page.getByRole("heading", { name: /BOM/ })).toBeVisible();

  expect(blocked, "CSP violations").toEqual([]);
  await ctx.close();
});
