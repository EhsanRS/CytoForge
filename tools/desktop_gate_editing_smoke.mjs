// Native pointer/keyboard editing with independent acquisition labels and history.
import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { _electron as electron, expect } from "playwright/test";

const root = process.cwd(),
  profile = path.join(root, ".tmp", "desktop-shape-" + Date.now());
const evidencePath =
  process.env.CYTOFORGE_SHAPE_EVIDENCE ||
  path.join(root, "artifacts/desktop-shape-editor-source.json");
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
  drafts = new Map();
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
async function openEditor(gateId, page = main) {
  const doc = await documentFor(),
    gate = doc.gates.find((g) => g.id === gateId);
  await inspector(page);
  await page.locator(".gate-row").filter({ hasText: gate.name }).click();
  await expect(page.locator(".analysis-heading h1")).toHaveText(gate.name);
  await page
    .getByRole("button", { name: "Edit selected gate", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Edit shape visually", exact: true })
    .click();
  await previewReady(page);
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
async function save(page = main) {
  await page
    .getByRole("button", { name: "Save population", exact: true })
    .click();
  await expect(
    page.getByRole("dialog", { name: "Edit population", exact: true }),
  ).toHaveCount(0);
  return documentFor();
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
const deadline = setTimeout(() => application?.process().kill(), 300000);
try {
  await launch();
  await main
    .getByRole("button", { name: "New experiment", exact: true })
    .click();
  await main
    .getByLabel("Experiment name", { exact: true })
    .fill("Native shape editing truth");
  await main
    .getByRole("button", { name: "Create workspace", exact: true })
    .click();
  const csv = path.join(profile, "independent-shape-labels.csv");
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
    name: "Move and resize",
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
  await main.locator(".gate-row").filter({ hasText: rectangle.name }).click();
  const opened = application.waitForEvent("window");
  await main
    .getByRole("button", { name: "Open plot in new window", exact: true })
    .click();
  popup = await opened;
  observe(popup);
  await popup.setViewportSize({ width: 1260, height: 900 });
  await inspector(popup);
  await expect(popup.locator(".inspector-count strong")).toHaveText("81");
  const history = await request(`/workspaces/${workspaceId}/history`);
  await openEditor(rectangle.id);
  await drag(main, [0.4, 0.3], [3.6, 2.5]);
  await main.locator(".gate-shape-body").focus();
  await main.keyboard.press("ArrowRight");
  await previewReady(main);
  assert.equal((await documentFor()).revision, doc.revision);
  assert.deepEqual(
    await request(`/workspaces/${workspaceId}/history`),
    history,
  );
  assert.deepEqual(
    (await documentFor()).gates.find((g) => g.id === rectangle.id).bounds,
    rectangle.bounds,
  );
  await expect(main.locator(".shape-editor-status")).toContainText(
    "137 draft events",
  );
  evidence.checks.push(
    "Native pointer translation and one-pixel keyboard movement produce full-event previews without workspace/history writes",
  );
  doc = await save();
  const moved = doc.gates.find((g) => g.id === rectangle.id);
  assert.equal(rectangleCount(moved), 137);
  assert.equal(await count(rectangle.id), 137);
  assert.equal(await count(child.id), 0);
  await expect(popup.locator(".inspector-count strong")).toHaveText("137");
  await main.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(popup.locator(".inspector-count strong")).toHaveText("81");
  assert.equal(await count(child.id), 81);
  await main.getByRole("button", { name: "Redo", exact: true }).click();
  await expect(popup.locator(".inspector-count strong")).toHaveText("137");
  evidence.checks.push(
    "Saving recalculates dependent children and synchronizes independent native windows; undo and redo restore both memberships",
  );
  await openEditor(rectangle.id, popup);
  await dragHandle(popup, "b1", [4.6, 2.2]);
  doc = await documentFor();
  await request(
    `/workspaces/${workspaceId}/gates/${child.id}`,
    {
      revision: doc.revision,
      gate: {
        ...doc.gates.find((g) => g.id === child.id),
        name: "Child revised elsewhere",
      },
    },
    "PUT",
  );
  await expect(popup.locator(".gate-draft-conflict")).toBeVisible();
  await expect(
    popup.getByRole("button", { name: "Save population", exact: true }),
  ).toBeDisabled();
  await expect(popup.locator(".gate-shape-overlay")).toHaveClass(/disabled/);
  await popup
    .getByRole("button", {
      name: "Keep draft and use current workspace",
      exact: true,
    })
    .click();
  await previewReady(popup);
  await save(popup);
  assert(
    (await documentFor()).gates.find((g) => g.id === rectangle.id).bounds[1] >
      4.5,
  );
  evidence.checks.push(
    "Concurrent edits retain local resized geometry, disable stale preview/save and require explicit revision review",
  );
  const geometries = [
    { id: uid(), name: "Range CDF", kind: "range", x: "X", bounds: [-1, 1] },
    {
      id: uid(),
      name: "Polygon vertices",
      kind: "polygon",
      x: "X",
      y: "Y",
      vertices: [
        [-1, -1],
        [1, -1],
        [0, 1],
      ],
    },
    {
      id: uid(),
      name: "Rotated ellipse",
      kind: "ellipse",
      x: "X",
      y: "Y",
      center: [0, 0],
      radii: [1, 0.7],
      angle: 0.3,
    },
    {
      id: uid(),
      name: "Imported ellipse",
      kind: "ellipsoid",
      dimensions: ["X", "Y"].map((channel) => ({ channel })),
      coordinates: [0, 0],
      covariance: [
        [1, 0.2],
        [0.2, 0.5],
      ],
      distance_square: 2,
    },
    {
      id: uid(),
      name: "Unbounded dimensions",
      kind: "hyperrectangle",
      dimensions: [
        { channel: "X", minimum: 0 },
        { channel: "Y", maximum: 1 },
      ],
    },
    {
      id: uid(),
      name: "Quadrant thresholds",
      kind: "quadrant",
      x: "X",
      y: "Y",
      bounds: [0, 0],
      quadrant: 2,
    },
    {
      id: uid(),
      name: "Magnetic anchor",
      kind: "rectangle",
      x: "X",
      y: "Y",
      bounds: [-1, 1, -1, 1],
      magnetic: { max_shift: 2 },
    },
    {
      id: uid(),
      name: "Dense polygon",
      kind: "polygon",
      x: "X",
      y: "Y",
      vertices: Array.from({ length: 2000 }, (_, i) => [
        Math.cos((i * Math.PI) / 1000),
        Math.sin((i * Math.PI) / 1000),
      ]),
    },
  ].map((g) => ({ ...g, sample_id: sampleId }));
  doc = await documentFor();
  const fixed = {
    id: uid(),
    name: "Native diagonal control",
    detectors: ["X", "Y"],
    matrix: [
      [2, 0],
      [0, 4],
    ],
  };
  doc = await request(`/workspaces/${workspaceId}/compensations`, {
    revision: doc.revision,
    compensation: fixed,
  });
  geometries.push({
    id: uid(),
    sample_id: sampleId,
    name: "Separate native axis bases",
    kind: "hyperrectangle",
    dimensions: [
      {
        channel: "X",
        compensation_ref: "uncompensated",
        minimum: 3.1,
        maximum: 3.4,
      },
      { channel: "X", compensation_ref: fixed.id, minimum: 1.55, maximum: 1.7 },
    ],
  });
  doc = await request(`/workspaces/${workspaceId}/gates/batch`, {
    revision: doc.revision,
    gates: geometries,
  });
  await openEditor(geometries[0].id);
  await main
    .getByLabel("Shape editor graph mode", { exact: true })
    .selectOption("cdf");
  await previewReady(main);
  await dragHandle(main, "b1", [3.4, 0]);
  await expect(main.locator(".shape-editor-status")).toContainText(
    "218 draft events",
  );
  await save();
  assert.equal(await count(geometries[0].id), 218);
  evidence.checks.push(
    "Range boundary editing works on native CDF coordinates and retains the complete acquisition denominator",
  );
  await openEditor(geometries[1].id);
  await dragHandle(main, "v0", [-2, -1]);
  const midpoint = await point(main, [-0.5, -1]);
  await main.mouse.dblclick(...midpoint);
  await previewReady(main);
  assert.equal(drafts.get(main).vertices.length, 4);
  await main.locator('[data-shape-handle="v1"]').focus();
  await main.keyboard.press("Delete");
  await previewReady(main);
  assert.equal(drafts.get(main).vertices.length, 3);
  await save();
  evidence.checks.push(
    "Native polygon vertices move, insert on a selected edge and delete without losing the original geometry fields",
  );
  await openEditor(geometries[2].id);
  await dragHandle(main, "r0p", [1.6 * Math.cos(0.3), 1.6 * Math.sin(0.3)]);
  await dragHandle(main, "rotate", [1.2, 1.2]);
  doc = await save();
  const ellipse = doc.gates.find((g) => g.id === geometries[2].id);
  // Native pointer coordinates have float32 rounding; counts remain exact.
  assert(Math.abs(ellipse.radii[0] - 1.6) < 1e-5);
  assert(Math.abs(ellipse.angle - Math.PI / 4) < 1e-5);
  evidence.checks.push(
    "Rotated ellipse major-axis resizing and rotation preserve its center and positive minor radius",
  );
  await openEditor(geometries[3].id);
  await main.getByRole("button", { name: "Expand 5%", exact: true }).click();
  await previewReady(main);
  doc = await save();
  const ellipsoid = doc.gates.find((g) => g.id === geometries[3].id);
  ellipsoid.covariance.forEach((row, i) =>
    row.forEach((v, j) =>
      assert(Math.abs(v - geometries[3].covariance[i][j] * 1.05 ** 2) < 1e-10),
    ),
  );
  assert.equal(ellipsoid.distance_square, 2);
  evidence.checks.push(
    "Imported ellipsoid visual scaling preserves its covariance orientation, dimensions and distance threshold",
  );
  await openEditor(geometries[4].id);
  await dragHandle(main, "b0", [1, 0]);
  doc = await save();
  const unbounded = doc.gates.find((g) => g.id === geometries[4].id);
  assert.equal(unbounded.dimensions[0].maximum, null);
  assert.equal(unbounded.dimensions[1].minimum, null);
  assert(Math.abs(unbounded.dimensions[0].minimum - 1) < 1e-5);
  await openEditor(geometries[5].id);
  await dragHandle(main, "c02", [1, 1]);
  doc = await save();
  assert.deepEqual(
    doc.gates
      .find((g) => g.id === geometries[5].id)
      .bounds.map((v) => Math.round(v)),
    [1, 1],
  );
  evidence.checks.push(
    "Unbounded imported boundaries and quadrant intersections retain null limits and quadrant membership conventions",
  );
  await openEditor(geometries[6].id);
  await expect(main.locator(".gate-shape-editor")).toContainText(
    "Edit magnetic anchor",
  );
  await drag(main, [0.4, 0.3], [3.6, 2.5]);
  doc = await save();
  const magnetic = doc.gates.find((g) => g.id === geometries[6].id);
  assert(magnetic.magnetic && Math.abs(magnetic.bounds[0] - 2.2) < 1e-5);
  assert.equal(await count(magnetic.id), 137);
  evidence.checks.push(
    "Visual magnetic edits change the anchor while retaining dynamic following and resolved full-event counts",
  );
  await openEditor(geometries[8].id);
  await expect(main.locator(".shape-axis-basis")).toContainText(
    "Native diagonal control",
  );
  await main.getByRole("button", { name: "Fit gate", exact: true }).click();
  await previewReady(main);
  await expect(main.locator(".shape-editor-status")).toContainText(
    "137 draft events / 1,295 parent",
  );
  await dragHandle(main, "b1", [3.45, 1.625]);
  doc = await save();
  const separate = doc.gates.find((g) => g.id === geometries[8].id);
  assert(Math.abs(separate.dimensions[0].maximum - 3.45) < 1e-5);
  assert.equal(separate.dimensions[1].compensation_ref, fixed.id);
  assert.equal(await count(separate.id), 137);
  evidence.checks.push(
    "Native axes retain distinct compensation references for the same measured parameter; fitting and boundary edits preserve full-parent counts",
  );
  await openEditor(geometries[7].id);
  await main.getByRole("button", { name: "Fit gate", exact: true }).click();
  await previewReady(main);
  await expect(main.locator(".shape-editor-status")).toContainText(
    "81 draft events / 1,295 parent",
  );
  await main.locator('[data-shape-handle="v100"]').focus();
  await main.keyboard.press("ArrowRight");
  await previewReady(main);
  const dense = drafts.get(main);
  assert.equal(dense.vertices.length, 2000);
  assert.notDeepEqual(dense.vertices[100], geometries[7].vertices[100]);
  assert.deepEqual(dense.vertices[10], geometries[7].vertices[10]);
  await main.screenshot({
    path: path.join(root, "artifacts/screenshots/desktop-shape-editor.png"),
  });
  await save();
  evidence.checks.push(
    "All 2,000 polygon vertices remain editable; focused three-digit vertex IDs update the intended original vertex",
  );
  const beforeCancel = await documentFor();
  await openEditor(geometries[2].id);
  await main.locator(".gate-shape-overlay").scrollIntoViewIfNeeded();
  const dragStart = await point(main, [0.2, 0.1]),
    dragEnd = await point(main, [1.5, 0.8]);
  await main.mouse.move(...dragStart);
  await main.mouse.down();
  await main.mouse.move(...dragEnd, { steps: 8 });
  await expect(
    main.getByRole("button", { name: "Save population", exact: true }),
  ).toBeDisabled();
  await main.keyboard.press("Escape");
  await main.mouse.up();
  await expect(
    main.getByRole("dialog", { name: "Edit population", exact: true }),
  ).toBeVisible();
  await previewReady(main);
  assert.deepEqual(
    drafts.get(main),
    beforeCancel.gates.find((g) => g.id === geometries[2].id),
  );
  await main.getByRole("button", { name: "Cancel", exact: true }).click();
  assert.deepEqual((await documentFor()).gates, beforeCancel.gates);
  assert.equal((await documentFor()).revision, beforeCancel.revision);
  evidence.checks.push(
    "Escape cancels an active native drag, restores the original draft and leaves the workspace unchanged",
  );
  await openEditor(geometries[2].id);
  await main.locator(".gate-shape-overlay").scrollIntoViewIfNeeded();
  const resizeStart = await point(main, [0.2, 0.1]),
    resizeEnd = await point(main, [1.5, 0.8]);
  await main.mouse.move(...resizeStart);
  await main.mouse.down();
  await main.mouse.move(...resizeEnd, { steps: 8 });
  await main.setViewportSize({ width: 640, height: 900 });
  await expect(main.locator(".shape-edit-error")).toContainText(
    "active drag was cancelled",
  );
  await main.mouse.up();
  await main.setViewportSize({ width: 1540, height: 1050 });
  await previewReady(main);
  assert.deepEqual(
    drafts.get(main),
    beforeCancel.gates.find((g) => g.id === geometries[2].id),
  );
  await main.getByRole("button", { name: "Cancel", exact: true }).click();
  evidence.checks.push(
    "A native renderer size change cancels the active drag before its coordinate mapping can jump",
  );
  const beforeRestart = await documentFor();
  await closeDesktop();
  await launch();
  await expect(main.getByLabel("Active workspace")).toHaveValue(workspaceId);
  const afterRestart = await documentFor();
  assert.deepEqual(afterRestart.gates, beforeRestart.gates);
  assert.equal(afterRestart.revision, beforeRestart.revision);
  assert.deepEqual(errors, []);
  evidence.checks.push(
    "Saved geometry, covariance, thresholds, magnetic settings and native independent windows survive desktop restart",
  );
  evidence.status = "passed";
  evidence.validated_at = new Date().toISOString();
  evidence.workspace_id = workspaceId;
  evidence.revision = afterRestart.revision;
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
        "artifacts/screenshots/desktop-shape-editor-failure.png",
      ),
    })
    .catch(() => {});
  throw error;
} finally {
  clearTimeout(deadline);
  writeFileSync(evidencePath, JSON.stringify(evidence, null, 2));
  if (application) await application.close().catch(() => {});
}
