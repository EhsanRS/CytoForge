// Traced native outlines, exact labelled populations and drawing draft guards.
import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { _electron as electron, expect } from "playwright/test";

const root = process.cwd(),
  profile = path.join(root, ".tmp", "desktop-freehand-" + Date.now());
const evidencePath =
  process.env.CYTOFORGE_FREEHAND_EVIDENCE ||
  path.join(root, "artifacts/desktop-freehand-gates-source.json");
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
  ...Array.from({ length: 47 }, (_, i) => [
    3.2 + (i % 11) * 0.001,
    0.2 + (i % 7) * 0.001,
  ]),
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
async function ready(page) {
  await expect(
    page.locator('.primary-plot canvas[data-ready="true"]'),
  ).toBeVisible();
}
async function point(page, world) {
  const limits = plots.get(page).bounds;
  assert.equal(limits.length, 4);
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
async function chooseFreehand(page) {
  await ready(page);
  await page
    .getByRole("button", { name: "Freehand gate", exact: true })
    .click();
}
async function trace(page, outline, held = false) {
  const start = await point(page, outline[0]);
  await page.mouse.move(...start);
  if (held) await page.mouse.down();
  else await page.mouse.click(...start);
  for (const vertex of outline.slice(1))
    await page.mouse.move(...(await point(page, vertex)), { steps: 9 });
  if (held) await page.mouse.up();
}
async function save(page, name) {
  await expect(
    page.getByRole("dialog", { name: "Create population", exact: true }),
  ).toBeVisible();
  await page.getByLabel("Population name", { exact: true }).fill(name);
  await page
    .getByRole("button", { name: "Create population", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  const doc = await documentFor();
  const gate = doc.gates.find((g) => g.name === name);
  assert(gate && gate.kind === "polygon");
  assert.equal(gate.provenance.drawing_tool, "freehand");
  assert.equal(gate.provenance.vertex_spacing_css_pixels, 2);
  assert(gate.vertices.length > 20 && gate.vertices.length <= 2000);
  return gate;
}
async function cancel(page) {
  await page
    .getByRole("button", { name: "Cancel freehand gate", exact: true })
    .click();
  await expect(page.locator(".polygon-actions")).toHaveCount(0);
  await expect(page.getByRole("dialog")).toHaveCount(0);
}
const deadline = setTimeout(() => application?.process().kill(), 300000);
try {
  await launch();
  await main
    .getByRole("button", { name: "New experiment", exact: true })
    .click();
  await main
    .getByLabel("Experiment name", { exact: true })
    .fill("Native freehand truth");
  await main
    .getByRole("button", { name: "Create workspace", exact: true })
    .click();
  const csv = path.join(profile, "independent-freehand-labels.csv");
  writeFileSync(csv, ["X,Y", ...rows.map((p) => p.join(","))].join("\n"));
  const chooser = main.waitForEvent("filechooser");
  await main.getByRole("button", { name: "Import FCS", exact: true }).click();
  await (await chooser).setFiles(csv);
  await main.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(main.getByRole("dialog", { name: /Import/ })).toHaveCount(0);
  workspaceId = await main.getByLabel("Active workspace").inputValue();
  let doc = await documentFor();
  sampleId = doc.samples[0].id;
  assert.equal(doc.samples[0].event_count, 1342);
  await ready(main);
  const opened = application.waitForEvent("window");
  await main
    .getByRole("button", { name: "Open plot in new window", exact: true })
    .click();
  popup = await opened;
  observe(popup);
  await popup.setViewportSize({ width: 1260, height: 900 });
  await ready(popup);
  const originalView = await state(main);
  await chooseFreehand(main);
  const cOutline = [
    [-1, -1],
    [4, -1],
    [4, 0],
    [1, 0],
    [1, 1.5],
    [4, 1.5],
    [4, 3],
    [-1, 3],
    [-1, -1],
  ];
  await trace(main, cOutline);
  await main
    .getByRole("button", { name: "Edit shape visually", exact: true })
    .click();
  await expect(main.locator(".shape-editor-status")).toContainText(
    "218 draft events / 1,342 parent",
  );
  assert.equal((await documentFor()).revision, doc.revision);
  const cGate = await save(main, "Freehand C-shaped population");
  assert.equal(await count(cGate.id), 218);
  assert.deepEqual(await state(main), originalView);
  await expect(
    popup.locator(".gate-row").filter({ hasText: cGate.name }),
  ).toHaveAttribute("title", /218 events/);
  evidence.checks.push(
    "Native click/trace/start-ring closure records a nonconvex C outline; all-event preview and saved membership select exactly 218 labelled events while 47 notch events stay excluded",
  );
  await chooseFreehand(popup);
  await trace(
    popup,
    [
      [-0.8, -0.8],
      [0.8, -0.8],
      [0.8, 0.8],
      [-0.8, 0.8],
    ],
    true,
  );
  const originGate = await save(popup, "Popup drag outline");
  assert.equal(await count(originGate.id), 81);
  await expect(
    main.locator(".gate-row").filter({ hasText: originGate.name }),
  ).toHaveAttribute("title", /81 events/);
  await main.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(
    popup.locator(".gate-row").filter({ hasText: originGate.name }),
  ).toHaveCount(0);
  await main.getByRole("button", { name: "Redo", exact: true }).click();
  assert.equal(await count(originGate.id), 81);
  evidence.checks.push(
    "Drag/release in an independent native popup closes the outline, selects all 81 origin labels, synchronizes the main tree and survives undo/redo",
  );
  await chooseFreehand(main);
  await trace(main, [
    [2.8, 1.8],
    [3.6, 1.8],
    [3.6, 2.6],
    [2.8, 2.6],
  ]);
  await main.keyboard.press("Enter");
  const targetGate = await save(main, "Keyboard-closed freehand");
  assert.equal(await count(targetGate.id), 137);
  evidence.checks.push(
    "Enter closes a traced outline using its captured coordinates and selects exactly the 137 independent positive labels",
  );
  const beforeCancel = await documentFor(),
    history = await request(`/workspaces/${workspaceId}/history`);
  await chooseFreehand(main);
  await trace(main, [
    [-1, -1],
    [2, -1],
    [2, 1],
  ]);
  await expect(main.getByLabel("Active workspace")).toBeDisabled();
  await expect(
    main.getByRole("button", { name: "Layout studio", exact: true }),
  ).toBeDisabled();
  await application.evaluate(({ BrowserWindow, dialog }) => {
    globalThis.freehandOriginalMessageBox = dialog.showMessageBox;
    globalThis.freehandCloseMessages = [];
    dialog.showMessageBox = async (_parent, options) => {
      globalThis.freehandCloseMessages.push(options.message);
      return { response: 0 };
    };
    BrowserWindow.getAllWindows()
      .find((w) => !w.webContents.getURL().includes("plotWindow="))
      .close();
  });
  await expect
    .poll(() =>
      application.evaluate(() => globalThis.freehandCloseMessages.length),
    )
    .toBe(1);
  assert(!main.isClosed());
  await application.evaluate(({ dialog }) => {
    dialog.showMessageBox = globalThis.freehandOriginalMessageBox;
  });
  await cancel(main);
  assert.deepEqual((await documentFor()).gates, beforeCancel.gates);
  assert.equal((await documentFor()).revision, beforeCancel.revision);
  assert.deepEqual(
    await request(`/workspaces/${workspaceId}/history`),
    history,
  );
  evidence.checks.push(
    "Unfinished freehand tracing locks source navigation and protects native application close; the Cancel control creates no population dialog and leaves science/history unchanged",
  );
  await chooseFreehand(main);
  await trace(main, [
    [-1, -1],
    [2, -1],
    [2, 1],
  ]);
  const captured = await main
    .locator(".draw-overlay polyline")
    .getAttribute("points");
  doc = await documentFor();
  await request(
    `/workspaces/${workspaceId}/gates/${cGate.id}`,
    {
      revision: doc.revision,
      gate: {
        ...doc.gates.find((g) => g.id === cGate.id),
        name: "C revised elsewhere",
      },
    },
    "PUT",
  );
  await expect(main.locator(".plot-stage .plot-draft-notice")).toContainText(
    "This drawing is retained",
  );
  await expect(
    main.getByRole("button", { name: "Finish freehand gate", exact: true }),
  ).toBeDisabled();
  assert.equal(
    await main.locator(".draw-overlay polyline").getAttribute("points"),
    captured,
  );
  await main.keyboard.press("Enter");
  await expect(main.getByRole("dialog")).toHaveCount(0);
  await cancel(main);
  evidence.checks.push(
    "A concurrent saved-gate change retains the captured outline while disabling finish and preventing stale scientific creation",
  );
  await chooseFreehand(main);
  await trace(main, [
    [-1, -1],
    [2, -1],
    [2, 1],
  ]);
  await main.setViewportSize({ width: 640, height: 900 });
  await expect(main.locator(".plot-stage .plot-draft-notice")).toContainText(
    "The plot was resized",
  );
  await expect(
    main.getByRole("button", { name: "Finish freehand gate", exact: true }),
  ).toBeDisabled();
  await main.setViewportSize({ width: 1540, height: 1050 });
  await expect(
    main.getByRole("button", { name: "Finish freehand gate", exact: true }),
  ).toBeDisabled();
  await cancel(main);
  evidence.checks.push(
    "Native viewport resizing freezes and retains the draft; restoring the old window size does not silently resume a changed mapping",
  );
  await chooseFreehand(main);
  await main.mouse.click(...(await point(main, [0, 0])));
  await expect(
    main.getByRole("button", { name: "Finish freehand gate", exact: true }),
  ).toBeDisabled();
  await main.keyboard.press("Enter");
  await expect(main.locator(".freehand-error")).toContainText(
    "outline with an area",
  );
  await expect(main.getByRole("dialog")).toHaveCount(0);
  await main.keyboard.press("Escape");
  await expect(main.locator(".polygon-actions")).toHaveCount(0);
  await chooseFreehand(main);
  await trace(main, [
    [-1, -1],
    [2, -1],
    [2, 1],
  ]);
  await main
    .locator(".plot-stage")
    .dispatchEvent("pointercancel", { pointerType: "pen", pointerId: 1 });
  await expect(main.locator(".polygon-actions")).toHaveCount(0);
  await expect(main.getByRole("dialog")).toHaveCount(0);
  evidence.checks.push(
    "Stationary clicks cannot create zero-area populations; Escape and a synthetic pen pointer-cancel event discard unsaved outlines without creating a population",
  );
  doc = await documentFor();
  await chooseFreehand(main);
  await main.mouse.click(...(await point(main, [-2, -2])));
  for (let i = 0; i < 55; i++)
    await main.mouse.move(...(await point(main, [i % 2 ? 3 : 0, 1])), {
      steps: 2,
    });
  await expect(main.locator(".freehand-error")).toContainText(
    "No partial outline will be saved",
  );
  await expect(main.locator(".freehand-count")).toHaveText(
    "2,000 / 2,000 vertices",
  );
  await expect(
    main.getByRole("button", { name: "Finish freehand gate", exact: true }),
  ).toBeDisabled();
  await main.keyboard.press("Enter");
  await expect(main.getByRole("dialog")).toHaveCount(0);
  await cancel(main);
  assert.equal((await documentFor()).revision, doc.revision);
  evidence.checks.push(
    "Exceeding 2,000 sampled vertices shows the limit, blocks finishing and never silently truncates or simplifies a saved scientific outline",
  );
  await main.getByRole("button", { name: "Histogram", exact: true }).click();
  await expect(
    main.getByRole("button", { name: "Freehand gate", exact: true }),
  ).toBeDisabled();
  await main.locator(".plot-stage").focus();
  await main.keyboard.press("f");
  await expect(
    main.getByRole("button", { name: "Freehand gate", exact: true }),
  ).not.toHaveAttribute("aria-pressed", "true");
  await main.getByRole("button", { name: "Density", exact: true }).click();
  await ready(main);
  // Distinct fixed-compensation and ratio axes, with an independently known parent.
  doc = await documentFor();
  const compensation = {
    id: uid(),
    name: "Freehand fixed basis",
    kind: "spillover",
    detectors: ["X", "Y"],
    outputs: ["X", "Y"],
    matrix: [
      [1, 0],
      [0, 2],
    ],
  };
  doc = await request(`/workspaces/${workspaceId}/compensations`, {
    revision: doc.revision,
    compensation,
  });
  const linear = {
    kind: "linear",
    cofactor: 150,
    t: 262144,
    w: 0.5,
    m: 4.5,
    a: 0,
  };
  const basis = {
    id: uid(),
    sample_id: sampleId,
    name: "Native ratio parent",
    kind: "hyperrectangle",
    dimensions: [
      {
        channel: "X",
        compensation_ref: compensation.id,
        transform: { ...linear, kind: "asinh", cofactor: 1 },
        minimum: 1.5,
        maximum: 2,
      },
      {
        channel: "Y over X",
        ratio_channels: ["Y", "X"],
        compensation_ref: compensation.id,
        transform: linear,
        minimum: 0,
        maximum: 1,
      },
    ],
  };
  doc = await request(`/workspaces/${workspaceId}/gates`, {
    revision: doc.revision,
    gate: basis,
  });
  await popup.locator(".gate-row").filter({ hasText: basis.name }).click();
  await expect(popup.locator(".analysis-heading h1")).toHaveText(basis.name);
  await ready(popup);
  assert.equal(await count(basis.id), 184);
  await chooseFreehand(popup);
  await expect.poll(() => plots.get(popup)?.coordinate_gate_id).toBe(basis.id);
  const limits = plots.get(popup).bounds;
  const x0 = limits[0],
    x1 = limits[1];
  await trace(
    popup,
    [
      [x0, 0.31],
      [x1, 0.31],
      [x1, 0.38],
      [x0, 0.38],
    ],
    true,
  );
  const native = await save(popup, "Freehand in native ratio basis");
  assert.equal(native.parent_id, basis.id);
  assert.deepEqual(
    native.dimensions.map((d) => [
      d.channel,
      d.compensation_ref,
      d.transform.kind,
      d.ratio_channels,
    ]),
    [
      ["X", compensation.id, "asinh", null],
      ["Y over X", compensation.id, "linear", ["Y", "X"]],
    ],
  );
  assert.equal(await count(native.id), 137);
  evidence.checks.push(
    "The one-dimensional display blocks freehand shortcuts; a popup trace in fixed compensation/asinh and ratio coordinates preserves both exact native definitions and selects all 137 labelled parent events",
  );
  await main.getByRole("button", { name: "Select", exact: true }).click();
  await main.locator(".gate-row").filter({ hasText: native.name }).click();
  await expect(main.locator(".analysis-heading h1")).toHaveText(native.name);
  await ready(main);
  await main
    .getByRole("button", { name: "Edit gate on plot", exact: true })
    .click();
  await expect(main.locator(".shape-editor-status")).toContainText(
    "137 draft events / 184 parent",
  );
  await main.screenshot({
    path: path.join(root, "artifacts/screenshots/desktop-freehand-gates.png"),
  });
  await main
    .getByRole("button", { name: "Cancel gate edit", exact: true })
    .click();
  const finalDoc = await documentFor(),
    finalViews = [await state(main), await state(popup)];
  await closeDesktop();
  await launch();
  await expect(main.getByLabel("Active workspace")).toHaveValue(workspaceId);
  await expect.poll(async () => (await application.windows()).length).toBe(2);
  popup = (await application.windows()).find((p) =>
    p.url().includes("plotWindow="),
  );
  assert(popup);
  await ready(popup);
  const restored = await documentFor();
  assert.equal(restored.revision, finalDoc.revision);
  assert.deepEqual(restored.gates, finalDoc.gates);
  assert.deepEqual([await state(main), await state(popup)], finalViews);
  assert.deepEqual(errors, []);
  evidence.checks.push(
    "Traced native polygons remain editable with full-parent counts, and all recorded vertices, provenance, scientific revision and independent popup views survive normal desktop restart",
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
        "artifacts/screenshots/desktop-freehand-gates-failure.png",
      ),
    })
    .catch(() => {});
  throw error;
} finally {
  clearTimeout(deadline);
  writeFileSync(evidencePath, JSON.stringify(evidence, null, 2));
  if (application) await application.close().catch(() => {});
}
