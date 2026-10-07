// Inline gate editing in real native main and independent plot windows.
import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { _electron as electron, expect } from "playwright/test";

const root = process.cwd(),
  profile = path.join(root, ".tmp", "desktop-inline-" + Date.now());
const evidencePath =
  process.env.CYTOFORGE_INLINE_EVIDENCE ||
  path.join(root, "artifacts/desktop-inline-gate-editing-source.json");
const binary = process.env.CYTOFORGE_TEST_BINARY;
mkdirSync(profile, { recursive: true });
mkdirSync(path.join(root, "artifacts/screenshots"), { recursive: true });
const evidence = {
  status: "running",
  binary: binary || "development Electron",
  profile,
  checks: [],
};
if (binary)
  evidence.binary_sha256 = createHash("sha256")
    .update(readFileSync(binary))
    .digest("hex");
const errors = [],
  drafts = new Map(),
  plots = new Map();
let application, main, popup, workspaceId, sampleId;
const uid = () => randomUUID().replaceAll("-", "");
const rows = [
  ...Array.from({ length: 81 }, (_, i) => [
    ((i % 9) - 4) * 0.15,
    (Math.floor(i / 9) - 4) * 0.15,
  ]),
  ...Array.from({ length: 137 }, (_, i) => [
    3.2 + (i % 11) * 0.001,
    2.2 + (i % 13) * 0.001,
  ]),
  ...Array.from({ length: 53 }, (_, i) => [
    -3.2 - (i % 7) * 0.001,
    -2.2 - (i % 5) * 0.001,
  ]),
  ...Array.from({ length: 1024 }, (_, i) => [
    8 + (i % 17) * 0.001,
    8 + (i % 19) * 0.001,
  ]),
];
function observe(page) {
  page.on("response", async (response) => {
    if (response.url().includes("/plot?") && response.ok()) {
      try {
        plots.set(page, await response.json());
      } catch {}
    }
  });
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => {
    if (request.url().endsWith("/gates/preview-shape"))
      drafts.set(page, request.postDataJSON().gate);
  });
}
async function launch() {
  application = await electron.launch({
    executablePath: binary,
    args: [
      ...(binary ? [] : ["."]),
      "--headless",
      "--ozone-platform=headless",
      "--disable-gpu",
      ...(process.env.CYTOFORGE_TEST_NO_SANDBOX === "1"
        ? ["--no-sandbox"]
        : []),
    ],
    chromiumSandbox: process.env.CYTOFORGE_TEST_NO_SANDBOX !== "1",
    env: {
      ...process.env,
      CYTOFORGE_HOME: profile,
      CYTOFORGE_HEADLESS_TEST: "1",
    },
    timeout: 60000,
  });
  const switches = await application.evaluate(({ app }) => ({
    no_sandbox: app.commandLine.hasSwitch("no-sandbox"),
    headless: app.commandLine.hasSwitch("headless"),
    disable_gpu: app.commandLine.hasSwitch("disable-gpu"),
  }));
  assert.equal(
    switches.no_sandbox,
    process.env.CYTOFORGE_TEST_NO_SANDBOX === "1",
  );
  assert(switches.headless && switches.disable_gpu);
  evidence.sandbox_exception = switches.no_sandbox;
  evidence.native_command_line_switches = switches;
  evidence.scope =
    "Linux x64 headless native desktop with hardware GPU disabled";
  main = await application.firstWindow();
  observe(main);
  await main.setViewportSize({ width: 1540, height: 1050 });
  await main.waitForLoadState("networkidle");
}
async function request(route, body, method = body ? "POST" : "GET") {
  const result = await main.evaluate(
    async ({ route, body, method }) => {
      const { token } = await (await fetch("/api/bootstrap")).json();
      const response = await fetch("/api" + route, {
        method,
        headers: {
          "X-CytoForge-Token": token,
          "Content-Type": "application/json",
        },
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!response.ok) throw new Error(await response.text());
      const value = await response.json();
      if (value.id && Array.isArray(value.gates))
        await globalThis.cytoforgeDesktop.notifyWorkspaceChanged(value.id);
      return value;
    },
    { route, body, method },
  );
  if (result.id && Array.isArray(result.gates))
    await expect(
      main.getByRole("button", {
        name: `Revision ${result.revision} · History`,
        exact: true,
      }),
    ).toBeVisible();
  return result;
}
const documentFor = () => request(`/workspaces/${workspaceId}`);
async function count(gateId) {
  return (
    await request(`/workspaces/${workspaceId}/samples/${sampleId}/counts`)
  ).find((g) => g.id === gateId).count;
}
async function inspector(page) {
  await expect(page.locator(".primary-plot canvas")).toBeVisible();
  const show = page.getByRole("button", {
    name: "Show inspector",
    exact: true,
  });
  if (await show.isVisible()) await show.click();
}
async function previewReady(page) {
  await expect(page.locator(".gate-shape-overlay")).toBeVisible();
  await expect(page.locator(".shape-editor-status")).toContainText(
    /\d[\d,]* draft events/,
  );
}
async function point(page, world) {
  return page.locator(".gate-shape-overlay").evaluate((svg, world) => {
    const limits = JSON.parse(svg.getAttribute("data-shape-bounds")),
      box = svg.getBoundingClientRect();
    return [
      box.left +
        64 +
        ((world[0] - limits[0]) / (limits[1] - limits[0])) * (box.width - 88),
      limits.length === 4
        ? box.top +
          24 +
          (1 - (world[1] - limits[2]) / (limits[3] - limits[2])) *
            (box.height - 78)
        : box.top + 24 + (box.height - 78) / 2,
    ];
  }, world);
}
async function drag(page, from, to) {
  await page.locator(".gate-shape-overlay").scrollIntoViewIfNeeded();
  const a = await point(page, from),
    b = await point(page, to);
  await page.mouse.move(...a);
  await page.mouse.down();
  await page.mouse.move(...b, { steps: 12 });
  await page.mouse.up();
  await previewReady(page);
}
async function dragHandle(page, key, target) {
  const handle = page.locator(`[data-shape-handle="${key}"]`);
  await handle.scrollIntoViewIfNeeded();
  const box = await handle.boundingBox(),
    destination = await point(page, target);
  assert(box);
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(...destination, { steps: 12 });
  await page.mouse.up();
  await previewReady(page);
}
const rectangleCount = (gate) =>
  rows.filter(
    ([x, y]) =>
      x >= gate.bounds[0] &&
      x <= gate.bounds[1] &&
      y >= gate.bounds[2] &&
      y <= gate.bounds[3],
  ).length;
async function closeDesktop() {
  const closed = application.waitForEvent("close");
  await application.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows()
      .find((w) => !w.webContents.getURL().includes("plotWindow="))
      ?.close(),
  );
  await closed;
}
const state = (page) =>
  page.evaluate(
    async () => (await globalThis.cytoforgeDesktop.getPlotSession()).state,
  );
async function selectGate(page, gate) {
  await page.locator(".gate-row").filter({ hasText: gate.name }).click();
  await expect(page.locator(".analysis-heading h1")).toHaveText(gate.name);
  await expect(
    page.locator('.primary-plot canvas[data-ready="true"]'),
  ).toBeVisible();
}
async function inline(page, gate) {
  await selectGate(page, gate);
  await page
    .getByRole("button", { name: "Edit gate on plot", exact: true })
    .click();
  await expect(
    page.locator(".primary-plot .inline-gate-editor"),
  ).toHaveAttribute("data-gate-id", gate.id);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await previewReady(page);
}
async function apply(page) {
  await page
    .getByRole("button", { name: "Apply gate edit", exact: true })
    .click();
  await expect(page.locator(".inline-gate-editor")).toHaveCount(0);
  await expect(
    page.locator('.primary-plot canvas[data-ready="true"]'),
  ).toBeVisible();
  return documentFor();
}
async function cancel(page) {
  await page
    .getByRole("button", { name: "Cancel gate edit", exact: true })
    .click();
  await expect(page.locator(".inline-gate-editor")).toHaveCount(0);
  await expect(
    page.locator('.primary-plot canvas[data-ready="true"]'),
  ).toBeVisible();
}
async function plotPoint(page, world) {
  const limits = plots.get(page).bounds;
  return page.locator(".primary-plot .plot-stage").evaluate(
    (element, { limits, world }) => {
      const box = element.getBoundingClientRect();
      return [
        box.left +
          64 +
          ((world[0] - limits[0]) / (limits[1] - limits[0])) * (box.width - 88),
        box.top +
          24 +
          (1 - (world[1] - limits[2]) / (limits[3] - limits[2])) *
            (box.height - 78),
      ];
    },
    { limits, world },
  );
}
const deadline = setTimeout(() => application?.process().kill(), 300000);
try {
  await launch();
  await main
    .getByRole("button", { name: "New experiment", exact: true })
    .click();
  await main
    .getByLabel("Experiment name", { exact: true })
    .fill("Native inline gate truth");
  await main
    .getByRole("button", { name: "Create workspace", exact: true })
    .click();
  const csv = path.join(profile, "independent-inline-labels.csv");
  writeFileSync(csv, ["X,Y", ...rows.map((p) => p.join(","))].join("\n"));
  const chooser = main.waitForEvent("filechooser");
  await main.getByRole("button", { name: "Import FCS", exact: true }).click();
  await (await chooser).setFiles(csv);
  await main.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(main.getByRole("dialog", { name: /Import/ })).toHaveCount(0);
  workspaceId = await main.getByLabel("Active workspace").inputValue();
  let doc = await documentFor();
  sampleId = doc.samples[0].id;
  const rectangle = {
    id: uid(),
    sample_id: sampleId,
    name: "Inline rectangle",
    kind: "rectangle",
    x: "X",
    y: "Y",
    bounds: [-1, 1, -1, 1],
  };
  const child = {
    id: uid(),
    sample_id: sampleId,
    parent_id: rectangle.id,
    name: "Dependent child",
    kind: "range",
    x: "Y",
    bounds: [-1, 1],
  };
  doc = await request(`/workspaces/${workspaceId}/gates/batch`, {
    revision: doc.revision,
    gates: [rectangle, child],
  });
  await selectGate(main, rectangle);
  // Population navigation initially follows the one-dimensional child gate.
  // Deliberately choose the user's planar view before opening its independent copy.
  await main.getByRole("button", { name: "Density", exact: true }).click();
  await main.getByLabel("X axis channel", { exact: true }).selectOption("X");
  await main.getByLabel("Y axis channel", { exact: true }).selectOption("Y");
  await expect.poll(() => plots.get(main)?.bounds.length).toBe(4);
  await expect(
    main.locator('.primary-plot canvas[data-ready="true"]'),
  ).toBeVisible();
  const opened = application.waitForEvent("window");
  await main
    .getByRole("button", { name: "Open plot in new window", exact: true })
    .click();
  popup = await opened;
  observe(popup);
  await popup.setViewportSize({ width: 1260, height: 900 });
  await inspector(popup);
  await expect(popup.locator(".inspector-count strong")).toHaveText("81");
  const beforeView = await state(main),
    history = await request(`/workspaces/${workspaceId}/history`);
  await inline(main, rectangle);
  await drag(main, [0.4, 0.3], [3.6, 2.5]);
  await expect(main.locator(".shape-editor-status")).toContainText(
    "137 draft events / 1,295 parent",
  );
  await expect(main.getByLabel("Active workspace")).toBeDisabled();
  await expect(
    main.getByRole("button", { name: "Layout studio", exact: true }),
  ).toBeDisabled();
  await main.keyboard.press("h");
  await expect(main.locator(".inline-gate-editor")).toBeVisible();
  assert.equal((await documentFor()).revision, doc.revision);
  assert.deepEqual((await documentFor()).gates, doc.gates);
  assert.deepEqual(
    await request(`/workspaces/${workspaceId}/history`),
    history,
  );
  assert.equal(await count(rectangle.id), 81);
  evidence.checks.push(
    "Native main-plot drag shows 137 of all 1,295 parent events, locks source navigation and leaves saved science and history unchanged until Apply",
  );
  doc = await apply(main);
  const moved = doc.gates.find((g) => g.id === rectangle.id);
  assert.equal(rectangleCount(moved), 137);
  assert.equal(await count(rectangle.id), 137);
  assert.equal(await count(child.id), 0);
  assert.deepEqual(await state(main), beforeView);
  await expect(popup.locator(".inspector-count strong")).toHaveText("137");
  await main.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(popup.locator(".inspector-count strong")).toHaveText("81");
  assert.equal(await count(child.id), 81);
  await main.getByRole("button", { name: "Redo", exact: true }).click();
  await expect(popup.locator(".inspector-count strong")).toHaveText("137");
  evidence.checks.push(
    "Apply synchronizes real popup memberships and dependent children; undo/redo restore both while the main plot view stays identical",
  );
  await main.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(popup.locator(".inspector-count strong")).toHaveText("81");
  await expect(
    main.locator('.primary-plot canvas[data-ready="true"]'),
  ).toBeVisible();
  // Move the viewport only if its acquisition limits clip the saved gate border.
  if (plots.get(main).bounds[1] < 1.05) {
    await main.getByRole("button", { name: "Pan", exact: true }).click();
    const limits = plots.get(main).bounds;
    const from = await plotPoint(main, [(limits[0] + limits[1]) / 2, 0.3]);
    await main.mouse.move(...from);
    await main.mouse.down();
    await main.mouse.move(from[0] - 200, from[1], { steps: 8 });
    await main.mouse.up();
    await expect(
      main.locator('.primary-plot canvas[data-ready="true"]'),
    ).toBeVisible();
  }
  await main.getByRole("button", { name: "Select", exact: true }).click();
  const beforeBorder = await documentFor(),
    borderView = await state(main);
  const border = await plotPoint(main, [1, 0.3]);
  await main.mouse.dblclick(...border);
  await expect(main.locator(".inline-gate-editor")).toHaveAttribute(
    "data-gate-id",
    rectangle.id,
  );
  await previewReady(main);
  await drag(main, [0.3, 0.3], [1.5, 1.5]);
  await cancel(main);
  assert.deepEqual((await documentFor()).gates, beforeBorder.gates);
  assert.equal((await documentFor()).revision, beforeBorder.revision);
  assert.deepEqual(await state(main), borderView);
  evidence.checks.push(
    "Double-clicking a saved border selects that gate ahead of overlapping child fills; Cancel preserves saved geometry, revision and the original viewport",
  );
  await inline(popup, rectangle);
  const popupView = await state(popup);
  await dragHandle(popup, "b1", [1.5, 0]);
  const resized = structuredClone(drafts.get(popup));
  const popupId = await popup.evaluate(
    async () => (await globalThis.cytoforgeDesktop.getPlotWindow()).id,
  );
  await application.evaluate(({ BrowserWindow, dialog }, id) => {
    globalThis.inlineOriginalMessageBox = dialog.showMessageBox;
    globalThis.inlineCloseMessages = [];
    dialog.showMessageBox = async (_parent, options) => {
      globalThis.inlineCloseMessages.push(options.message);
      return { response: 0 };
    };
    BrowserWindow.getAllWindows()
      .find((w) => w.webContents.getURL().includes("plotWindow=" + id))
      .close();
  }, popupId);
  await expect
    .poll(() =>
      application.evaluate(() => globalThis.inlineCloseMessages.length),
    )
    .toBe(1);
  assert(!popup.isClosed());
  await application.evaluate(({ dialog }) => {
    dialog.showMessageBox = globalThis.inlineOriginalMessageBox;
  });
  doc = await documentFor();
  await request(
    `/workspaces/${workspaceId}/gates/${child.id}`,
    {
      revision: doc.revision,
      gate: {
        ...doc.gates.find((g) => g.id === child.id),
        name: "Revised in another window",
      },
    },
    "PUT",
  );
  await expect(popup.locator(".gate-draft-conflict")).toBeVisible();
  await expect(
    popup.getByRole("button", { name: "Apply gate edit", exact: true }),
  ).toBeDisabled();
  await expect(popup.locator(".gate-shape-overlay")).toHaveClass(/disabled/);
  assert.deepEqual(drafts.get(popup), resized);
  evidence.checks.push(
    "A popup inline draft protects native window close and is retained, with Apply blocked, when another window changes the workspace",
  );
  await popup
    .getByRole("button", { name: "Numeric settings", exact: true })
    .click();
  await expect(popup.locator(".inline-gate-editor")).toHaveCount(0);
  await expect(
    popup.getByRole("dialog", { name: "Edit population", exact: true }),
  ).toBeVisible();
  await expect(popup.locator(".gate-draft-conflict")).toBeVisible();
  await expect(
    popup.getByRole("button", { name: "Save population", exact: true }),
  ).toBeDisabled();
  await popup
    .getByRole("button", {
      name: "Keep draft and use current workspace",
      exact: true,
    })
    .click();
  await popup
    .getByRole("button", { name: "Edit shape visually", exact: true })
    .click();
  await previewReady(popup);
  assert.deepEqual(drafts.get(popup), resized);
  await popup
    .getByRole("button", { name: "Save population", exact: true })
    .click();
  await expect(popup.getByRole("dialog")).toHaveCount(0);
  doc = await documentFor();
  assert.deepEqual(
    doc.gates.find((g) => g.id === rectangle.id),
    resized,
  );
  assert.deepEqual(await state(popup), popupView);
  evidence.checks.push(
    "Numeric-settings handoff preserves resized geometry and its stale revision; explicit review is required before saving and the popup view remains unchanged",
  );
  await inline(
    main,
    doc.gates.find((g) => g.id === rectangle.id),
  );
  const beforeEscape = structuredClone(drafts.get(main));
  await main.locator(".gate-shape-overlay").scrollIntoViewIfNeeded();
  const a = await point(main, [0.3, 0.3]),
    b = await point(main, [1.5, 1.5]);
  await main.mouse.move(...a);
  await main.mouse.down();
  await main.mouse.move(...b, { steps: 8 });
  await expect(
    main.getByRole("button", { name: "Apply gate edit", exact: true }),
  ).toBeDisabled();
  await main.keyboard.press("Escape");
  await main.mouse.up();
  await previewReady(main);
  assert.deepEqual(drafts.get(main), beforeEscape);
  await main.locator(".gate-shape-body").focus();
  await main.keyboard.press("ArrowRight");
  await previewReady(main);
  assert(drafts.get(main).bounds[0] > beforeEscape.bounds[0]);
  await cancel(main);
  assert.deepEqual((await documentFor()).gates, doc.gates);
  evidence.checks.push(
    "Escape restores an active inline gesture without closing the editor; keyboard movement edits the draft and Cancel leaves science untouched",
  );
  await inline(
    popup,
    doc.gates.find((g) => g.id === rectangle.id),
  );
  await dragHandle(popup, "b1", [1.9, 0]);
  const removedDraft = structuredClone(drafts.get(popup));
  doc = await documentFor();
  await request(
    `/workspaces/${workspaceId}/gates/${rectangle.id}?revision=${doc.revision}`,
    undefined,
    "DELETE",
  );
  await expect(popup.locator(".inline-gate-editor")).toBeVisible();
  await expect(popup.locator(".gate-draft-conflict")).toContainText(
    "This population was removed",
  );
  await expect(
    popup.getByRole("button", { name: "Apply gate edit", exact: true }),
  ).toBeDisabled();
  await expect(
    popup.getByRole("button", {
      name: "Keep draft and use current workspace",
      exact: true,
    }),
  ).toHaveCount(0);
  doc = await documentFor();
  await request(`/workspaces/${workspaceId}/undo`, { revision: doc.revision });
  await popup
    .getByRole("button", {
      name: "Keep draft and use current workspace",
      exact: true,
    })
    .click();
  await previewReady(popup);
  assert.deepEqual(drafts.get(popup), removedDraft);
  doc = await apply(popup);
  assert.deepEqual(
    doc.gates.find((g) => g.id === rectangle.id),
    removedDraft,
  );
  evidence.checks.push(
    "Removing an edited population retains the inline draft without permitting recreation; undo and deliberate revision review restore that exact draft",
  );
  await inline(
    popup,
    doc.gates.find((g) => g.id === rectangle.id),
  );
  await dragHandle(popup, "b1", [2.1, 0]);
  const sampleDraft = structuredClone(drafts.get(popup));
  doc = await documentFor();
  await request(
    `/workspaces/${workspaceId}/samples/${sampleId}?revision=${doc.revision}`,
    undefined,
    "DELETE",
  );
  await expect(popup.locator(".inline-gate-editor")).toBeVisible();
  await expect(popup.locator(".gate-shape-editor")).toHaveCount(0);
  await expect(popup.locator(".inline-gate-editor")).toContainText(
    "The source sample is unavailable",
  );
  await expect(
    popup.getByRole("button", { name: "Apply gate edit", exact: true }),
  ).toBeDisabled();
  doc = await documentFor();
  await request(`/workspaces/${workspaceId}/undo`, { revision: doc.revision });
  await popup
    .getByRole("button", {
      name: "Keep draft and use current workspace",
      exact: true,
    })
    .click();
  await previewReady(popup);
  assert.deepEqual(drafts.get(popup), sampleDraft);
  doc = await apply(popup);
  evidence.checks.push(
    "Removing the source sample keeps the native inline editor and local geometry available; restored acquisition data requires explicit review before applying",
  );
  await inline(
    popup,
    doc.gates.find((g) => g.id === rectangle.id),
  );
  await drag(popup, [0.3, 0.3], [3.5, 2.5]);
  await expect(popup.locator(".shape-editor-status")).toContainText(
    "137 draft events / 1,295 parent",
  );
  doc = await apply(popup);
  assert.equal(
    rectangleCount(doc.gates.find((g) => g.id === rectangle.id)),
    137,
  );
  assert.equal(await count(rectangle.id), 137);
  await inspector(main);
  await expect(main.locator(".inspector-count strong")).toHaveText("137");
  await popup.screenshot({
    path: path.join(
      root,
      "artifacts/screenshots/desktop-inline-gate-editing.png",
    ),
  });
  const finalViews = [await state(main), await state(popup)];
  await closeDesktop();
  await launch();
  await expect(main.getByLabel("Active workspace")).toHaveValue(workspaceId);
  await expect.poll(async () => (await application.windows()).length).toBe(2);
  popup = (await application.windows()).find((p) =>
    p.url().includes("plotWindow="),
  );
  assert(popup);
  observe(popup);
  await expect(
    popup.locator('.primary-plot canvas[data-ready="true"]'),
  ).toBeVisible();
  const restored = await documentFor();
  assert.equal(restored.revision, doc.revision);
  assert.deepEqual(restored.gates, doc.gates);
  assert.deepEqual([await state(main), await state(popup)], finalViews);
  assert.deepEqual(errors, []);
  evidence.checks.push(
    "Applying an inline popup edit updates the main view; scientific geometry, revision and both independent native plot views survive normal desktop restart",
  );
  evidence.status = "passed";
  evidence.validated_at = new Date().toISOString();
  evidence.workspace_id = workspaceId;
  evidence.revision = restored.revision;
  await closeDesktop();
} catch (error) {
  evidence.status = "failed";
  evidence.error = error.message;
  evidence.checkpoint = evidence.checks.length;
  evidence.renderer_errors = errors;
  await main
    ?.screenshot({
      path: path.join(
        root,
        "artifacts/screenshots/desktop-inline-gate-editing-failure.png",
      ),
    })
    .catch(() => {});
  throw error;
} finally {
  clearTimeout(deadline);
  writeFileSync(evidencePath, JSON.stringify(evidence, null, 2));
  if (application) await application.close().catch(() => {});
}
