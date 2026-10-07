// Linked native bisectors/quadrants, labelled populations and atomic edit guards.
import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { _electron as electron, expect } from "playwright/test";

const root = process.cwd();
const profile = path.join(root, ".tmp", "desktop-partitions-" + Date.now());
const evidencePath =
  process.env.CYTOFORGE_PARTITION_EVIDENCE ||
  path.join(root, "artifacts/desktop-linked-partitions-source.json");
const binary = process.env.CYTOFORGE_TEST_BINARY;
assert(path.resolve(evidencePath).startsWith(root + path.sep));
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
const rows = [];
for (let xi = 0; xi < 5; xi++)
  for (let yi = 0; yi < 5; yi++)
    for (let n = 0; n < (xi + 1) * (yi + 2); n++) rows.push([xi - 2, yi - 2]);
assert.equal(rows.length, 300);
const uid = () => randomUUID().replaceAll("-", "");
const errors = [],
  plots = new Map(),
  drafts = new Map();
let application, main, popup, workspaceId, sampleId;
function observe(page) {
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("response", async (response) => {
    if (response.ok() && response.url().includes("/plot?")) {
      try {
        plots.set(page, await response.json());
      } catch {}
    }
  });
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
      if (method !== "GET" && value.id && Array.isArray(value.gates))
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
const state = (page) =>
  page.evaluate(
    async () => (await globalThis.cytoforgeDesktop.getPlotSession()).state,
  );
async function ready(page) {
  await expect(
    page.locator('.primary-plot canvas[data-ready="true"]'),
  ).toBeVisible();
}
async function selectGate(page, gate) {
  await page.locator(".gate-row").filter({ hasText: gate.name }).click();
  await expect(page.locator(".analysis-heading h1")).toHaveText(gate.name);
  await ready(page);
}
async function rootPopulation(page) {
  await page
    .getByRole("button", { name: "View all events", exact: true })
    .click();
  await ready(page);
}
async function inspector(page) {
  const button = page.getByRole("button", {
    name: "Show inspector",
    exact: true,
  });
  if (await button.isVisible()) await button.click();
}
async function plotPoint(page, world, editor = false) {
  const locator = page.locator(
    editor ? ".gate-shape-overlay" : ".primary-plot .plot-stage",
  );
  return locator.evaluate(
    (element, { world, fallback, editor }) => {
      const limits = editor
        ? JSON.parse(element.getAttribute("data-shape-bounds"))
        : fallback;
      const box = element.getBoundingClientRect();
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
    },
    { world, fallback: plots.get(page)?.bounds, editor },
  );
}
async function previewReady(page) {
  await expect(page.locator(".shape-editor-status")).toContainText(
    /\d[\d,]* draft events/,
  );
  await expect(page.getByLabel("Linked population counts")).toBeVisible();
}
async function familyCounts(page, expected) {
  await previewReady(page);
  await expect
    .poll(async () =>
      page.locator(".partition-preview strong").allTextContents(),
    )
    .toEqual(expected.map(String));
}
async function drawFamily(page, kind, name) {
  await ready(page);
  await page
    .getByRole("button", {
      name: kind === "bisector" ? "Bisector gates" : "Quadrant gates",
      exact: true,
    })
    .click();
  await page.mouse.click(...(await plotPoint(page, [0, 0])));
  await expect(
    page.getByRole("dialog", {
      name: `Create ${kind} populations`,
      exact: true,
    }),
  ).toBeVisible();
  await page.getByLabel("Population name", { exact: true }).fill(name);
  await page.getByLabel("X threshold", { exact: true }).fill("0");
  if (kind === "quadrant")
    await page.getByLabel("Y threshold", { exact: true }).fill("0");
  await page
    .getByRole("button", { name: "Edit shape visually", exact: true })
    .click();
}
async function saveFamily(page, name) {
  await page
    .getByRole("button", { name: "Create population", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  const doc = await documentFor();
  const family = doc.gates
    .filter((g) => g.name.startsWith(name + " "))
    .sort((a, b) => a.partition.member - b.partition.member);
  assert(family.length === 2 || family.length === 4);
  assert.equal(new Set(family.map((g) => g.partition.id)).size, 1);
  return { doc, family };
}
async function assertCounts(family, expected, source = sampleId) {
  const counts = await request(
    `/workspaces/${workspaceId}/samples/${source}/counts`,
  );
  assert.deepEqual(
    family.map((g) => counts.find((c) => c.id === g.id).count),
    expected,
  );
}
async function inline(page, gate) {
  await selectGate(page, gate);
  await page
    .getByRole("button", { name: "Edit gate on plot", exact: true })
    .click();
  await expect(page.locator(".inline-gate-editor")).toHaveAttribute(
    "data-gate-id",
    gate.id,
  );
  await previewReady(page);
}
async function dragHandle(page, key, target) {
  const handle = page.locator(`[data-shape-handle="${key}"]`);
  await handle.scrollIntoViewIfNeeded();
  const box = await handle.boundingBox();
  assert(box);
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(...(await plotPoint(page, target, true)), {
    steps: 12,
  });
  await page.mouse.up();
  await previewReady(page);
}
async function cancel(page) {
  await page
    .getByRole("button", { name: "Cancel gate edit", exact: true })
    .click();
  await expect(page.locator(".inline-gate-editor")).toHaveCount(0);
  await ready(page);
}
async function apply(page) {
  await page
    .getByRole("button", { name: "Apply gate edit", exact: true })
    .click();
  await expect(page.locator(".inline-gate-editor")).toHaveCount(0);
  await ready(page);
  return documentFor();
}
async function importCsv(page, file) {
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Import FCS", exact: true }).click();
  await (await chooser).setFiles(file);
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(page.getByRole("dialog", { name: /Import/ })).toHaveCount(0);
  await ready(page);
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
const deadline = setTimeout(() => application?.process().kill(), 300000);
try {
  await launch();
  await main
    .getByRole("button", { name: "New experiment", exact: true })
    .click();
  await main
    .getByLabel("Experiment name", { exact: true })
    .fill("Native linked partition truth");
  await main
    .getByRole("button", { name: "Create workspace", exact: true })
    .click();
  const csv = path.join(profile, "weighted-boundaries.csv");
  writeFileSync(csv, ["X,Y", ...rows.map((p) => p.join(","))].join("\n"));
  await importCsv(main, csv);
  workspaceId = await main.getByLabel("Active workspace").inputValue();
  let doc = await documentFor();
  sampleId = doc.samples[0].id;
  assert.equal(doc.samples[0].event_count, 300);
  await expect(
    main.getByRole("button", { name: "Bisector gates", exact: true }),
  ).toBeDisabled();
  await main.locator(".plot-stage").focus();
  await main.keyboard.press("b");
  await expect(
    main.getByRole("button", { name: "Bisector gates", exact: true }),
  ).not.toHaveAttribute("aria-pressed", "true");
  await main.getByRole("button", { name: "Histogram", exact: true }).click();
  await ready(main);
  await main.locator(".plot-stage").focus();
  await main.keyboard.press("b");
  await expect(
    main.getByRole("button", { name: "Bisector gates", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  evidence.checks.push(
    "Bisector toolbar and B shortcut are enabled only in native histogram/CDF views; 2D selection is rejected",
  );
  const before = await documentFor(),
    history = await request(`/workspaces/${workspaceId}/history`);
  await drawFamily(main, "bisector", "Split");
  await familyCounts(main, [60, 240]);
  assert.deepEqual(await documentFor(), before);
  assert.deepEqual(
    await request(`/workspaces/${workspaceId}/history`),
    history,
  );
  let saved = await saveFamily(main, "Split"),
    bisector = saved.family;
  doc = saved.doc;
  assert.equal(doc.revision, before.revision + 1);
  await assertCounts(bisector, [60, 240]);
  evidence.checks.push(
    "Native histogram click creates both linked open-ended ranges in one revision; exact zero-boundary labels give 60 negative and 240 positive events and draft preview writes nothing",
  );
  const opened = application.waitForEvent("window");
  await main
    .getByRole("button", { name: "Open plot in new window", exact: true })
    .click();
  popup = await opened;
  observe(popup);
  await popup.setViewportSize({ width: 1260, height: 900 });
  await ready(popup);
  await inspector(popup);
  await selectGate(popup, bisector[1]);
  await expect(popup.locator(".inspector-count strong")).toHaveText("240");
  await popup.getByRole("button", { name: "CDF", exact: true }).click();
  await ready(popup);
  const mainView = await state(main),
    popupView = await state(popup);
  await inline(main, bisector[0]);
  const savedView = await state(main),
    baseline = await documentFor();
  await dragHandle(main, "b1", [0.25, 0]);
  await familyCounts(main, [120, 180]);
  assert.deepEqual((await documentFor()).gates, baseline.gates);
  await expect(popup.locator(".inspector-count strong")).toHaveText("240");
  await cancel(main);
  assert.deepEqual((await documentFor()).gates, baseline.gates);
  assert.deepEqual(await state(main), savedView);
  assert.deepEqual(await state(popup), popupView);
  evidence.checks.push(
    "A real native threshold-handle drag previews 120/180 full-parent counts for both members; Cancel retains both saved ranges and each window's independent view",
  );
  await inline(main, bisector[0]);
  await dragHandle(main, "b1", [0.25, 0]);
  doc = await apply(main);
  bisector = bisector.map((g) => doc.gates.find((item) => item.id === g.id));
  await assertCounts(bisector, [120, 180]);
  await expect(popup.locator(".inspector-count strong")).toHaveText("180");
  assert.deepEqual(await state(popup), popupView);
  await main.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(popup.locator(".inspector-count strong")).toHaveText("240");
  await assertCounts(bisector, [60, 240]);
  await main.getByRole("button", { name: "Redo", exact: true }).click();
  await expect(popup.locator(".inspector-count strong")).toHaveText("180");
  await assertCounts(bisector, [120, 180]);
  evidence.checks.push(
    "Apply, undo and redo update both bisector members and the actual native popup together without changing popup CDF selection",
  );
  await inline(popup, bisector[1]);
  await dragHandle(popup, "b0", [0.5, 0]);
  const retained = drafts.get(popup);
  doc = await documentFor();
  doc = await request(
    `/workspaces/${workspaceId}`,
    { revision: doc.revision, name: "External partition edit" },
    "PATCH",
  );
  await expect(
    popup.getByRole("button", { name: "Apply gate edit", exact: true }),
  ).toBeDisabled();
  assert.deepEqual(drafts.get(popup).dimensions, retained.dimensions);
  await cancel(popup);
  await assertCounts(bisector, [120, 180]);
  evidence.checks.push(
    "External workspace changes preserve the linked threshold draft and block a stale native Apply; Cancel leaves every saved member untouched",
  );
  await rootPopulation(main);
  await main.getByRole("button", { name: "Density", exact: true }).click();
  await ready(main);
  await expect(
    main.getByRole("button", { name: "Bisector gates", exact: true }),
  ).toBeDisabled();
  await drawFamily(main, "quadrant", "Quadrants");
  await familyCounts(main, [45, 180, 60, 15]);
  saved = await saveFamily(main, "Quadrants");
  let quadrants = saved.family;
  doc = saved.doc;
  await assertCounts(quadrants, [45, 180, 60, 15]);
  evidence.checks.push(
    "The native quadrant tool creates one linked four-population family; axis and center boundary events are assigned once, with independently labelled counts 45/180/60/15",
  );
  await selectGate(popup, quadrants[1]);
  await popup.getByRole("button", { name: "Density", exact: true }).click();
  await ready(popup);
  await inline(main, quadrants[0]);
  const quadBefore = await documentFor();
  await dragHandle(main, "c12", [0.25, 0.25]);
  await familyCounts(main, [66, 99, 81, 54]);
  assert.deepEqual((await documentFor()).gates, quadBefore.gates);
  doc = await apply(main);
  quadrants = quadrants.map((g) => doc.gates.find((item) => item.id === g.id));
  await assertCounts(quadrants, [66, 99, 81, 54]);
  await expect(popup.locator(".inspector-count strong")).toHaveText("99");
  evidence.checks.push(
    "Dragging the real shared quadrant corner previews and atomically applies both thresholds to all four populations; popup membership becomes 99 and their sum remains every one of the 300 events",
  );
  await inline(popup, quadrants[1]);
  await popup
    .getByRole("button", { name: "Numeric settings", exact: true })
    .click();
  await expect(
    popup.getByRole("dialog", { name: "Edit population", exact: true }),
  ).toBeVisible();
  await popup.getByLabel("X threshold", { exact: true }).fill("1");
  await popup.getByLabel("Y threshold", { exact: true }).fill("1");
  await expect(
    popup.getByText("Select the complement within the parent population", {
      exact: true,
    }),
  ).toHaveCount(0);
  await popup
    .getByRole("button", { name: "Save population", exact: true })
    .click();
  await expect(popup.getByRole("dialog")).toHaveCount(0);
  doc = await documentFor();
  quadrants = quadrants.map((g) => doc.gates.find((item) => item.id === g.id));
  await assertCounts(quadrants, [66, 99, 81, 54]);
  assert(
    quadrants.every((g) =>
      g.dimensions.every((d) => (d.minimum ?? d.maximum) === 1),
    ),
  );
  evidence.checks.push(
    "Native inline-to-numeric handoff exposes only shared X/Y thresholds, preserves family identity, and applies exact numeric boundary 1 to every member",
  );
  const copy = path.join(profile, "weighted-target.csv");
  writeFileSync(
    copy,
    ["X,Y", ...[...rows].reverse().map((p) => p.join(","))].join("\n"),
  );
  await importCsv(main, copy);
  doc = await documentFor();
  const targetId = doc.samples.find((s) => s.id !== sampleId).id;
  doc = await request(`/workspaces/${workspaceId}/gates/apply`, {
    revision: doc.revision,
    source_sample_id: sampleId,
    target_sample_ids: [targetId],
  });
  const targetQuads = doc.gates
    .filter((g) => g.sample_id === targetId && g.partition?.kind === "quadrant")
    .sort((a, b) => a.partition.member - b.partition.member);
  assert.notEqual(targetQuads[0].partition.id, quadrants[0].partition.id);
  await assertCounts(targetQuads, [66, 99, 81, 54], targetId);
  await inline(popup, quadrants[1]);
  await dragHandle(popup, "c02", [0, 0]);
  doc = await apply(popup);
  quadrants = quadrants.map((g) => doc.gates.find((item) => item.id === g.id));
  await assertCounts(quadrants, [45, 180, 60, 15]);
  await assertCounts(targetQuads, [66, 99, 81, 54], targetId);
  evidence.checks.push(
    "Tree propagation preserves each complete linked family but assigns fresh target IDs; a subsequent native source edit leaves target thresholds and all independently counted target populations unchanged",
  );
  await selectGate(popup, quadrants[0]);
  await popup
    .getByRole("button", { name: "Delete population", exact: true })
    .click();
  await expect(
    popup.getByRole("dialog", {
      name: "Delete linked quadrant populations?",
      exact: true,
    }),
  ).toBeVisible();
  await popup
    .getByRole("button", { name: "Delete linked populations", exact: true })
    .click();
  await expect(popup.getByRole("dialog")).toHaveCount(0);
  doc = await documentFor();
  assert(!doc.gates.some((g) => g.partition?.id === quadrants[0].partition.id));
  await assertCounts(targetQuads, [66, 99, 81, 54], targetId);
  await popup.getByRole("button", { name: "Undo", exact: true }).click();
  await assertCounts(quadrants, [45, 180, 60, 15]);
  evidence.checks.push(
    "Native delete confirmation names the entire linked family; deleting one member removes all four in one operation and Undo restores their original IDs while target families remain intact",
  );
  await main.getByLabel("Active workspace").selectOption(workspaceId);
  await main.getByLabel("Plot sample", { exact: true }).selectOption(sampleId);
  await rootPopulation(main);
  doc = await documentFor();
  const matrix = {
    id: uid(),
    name: "Fixed partition matrix",
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
    compensation: matrix,
  });
  const linear = { kind: "linear" };
  const dimensions = [
    {
      channel: "X",
      compensation_ref: "uncompensated",
      transform: { ...linear, kind: "asinh", cofactor: 1 },
      minimum: -2,
      maximum: 2,
    },
    {
      channel: "X",
      compensation_ref: matrix.id,
      transform: linear,
      ratio_channels: ["Y", "X"],
      ratio_a: 1,
      ratio_b: 0,
      ratio_c: 0,
      minimum: -3,
      maximum: 3,
    },
  ];
  const basis = {
    id: uid(),
    name: "Native partition basis",
    sample_id: sampleId,
    kind: "hyperrectangle",
    dimensions,
  };
  doc = await request(`/workspaces/${workspaceId}/gates`, {
    revision: doc.revision,
    gate: basis,
  });
  await selectGate(
    main,
    doc.gates.find((g) => g.id === basis.id),
  );
  await main.getByRole("button", { name: "Density", exact: true }).click();
  await ready(main);
  await expect.poll(() => plots.get(main)?.coordinate_gate_id).toBe(basis.id);
  await drawFamily(main, "quadrant", "Native quadrants");
  await familyCounts(main, [27, 135, 45, 33]);
  saved = await saveFamily(main, "Native quadrants");
  const native = saved.family;
  for (const gate of native) {
    assert.equal(gate.parent_id, basis.id);
    assert.equal(gate.dimensions[0].channel, "X");
    assert.equal(gate.dimensions[1].channel, "X");
    assert.equal(gate.dimensions[0].compensation_ref, "uncompensated");
    assert.equal(gate.dimensions[0].transform.kind, "asinh");
    assert.deepEqual(gate.dimensions[1].ratio_channels, ["Y", "X"]);
    assert.equal(gate.dimensions[1].compensation_ref, matrix.id);
  }
  await assertCounts(native, [27, 135, 45, 33]);
  evidence.checks.push(
    "Native quadrant drawing preserves ordered duplicate channel labels, an uncompensated asinh X axis and a fixed-matrix Y/X ratio axis; independent labels give 27/135/45/33 of the 240 eligible parent events",
  );
  await selectGate(popup, bisector[0]);
  await popup.getByRole("button", { name: "CDF", exact: true }).click();
  await ready(popup);
  await drawFamily(popup, "bisector", "Nested CDF split");
  await familyCounts(popup, [60, 60]);
  const nested = await saveFamily(popup, "Nested CDF split");
  await assertCounts(nested.family, [60, 60]);
  assert(nested.family.every((g) => g.parent_id === bisector[0].id));
  evidence.checks.push(
    "Bisectors can be drawn inside a population in the actual native CDF popup; both children retain the same parent and partition all 120 eligible events into 60/60",
  );
  await main.screenshot({
    path: path.join(
      root,
      "artifacts/screenshots/desktop-linked-partitions.png",
    ),
  });
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
  assert.deepEqual(restored.gates, finalDoc.gates);
  assert.equal(restored.revision, finalDoc.revision);
  assert.deepEqual([await state(main), await state(popup)], finalViews);
  assert.deepEqual(errors, []);
  evidence.checks.push(
    "Normal native restart restores all linked IDs, scientific definitions, exact revision and independent main/popup view state with no renderer errors",
  );
  assert.equal(evidence.checks.length, 13);
  evidence.status = "passed";
  evidence.validated_at = new Date().toISOString();
  evidence.workspace_id = workspaceId;
  evidence.revision = restored.revision;
  evidence.renderer_errors = errors;
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
        "artifacts/screenshots/desktop-linked-partitions-failure.png",
      ),
    })
    .catch(() => {});
  throw error;
} finally {
  clearTimeout(deadline);
  writeFileSync(evidencePath, JSON.stringify(evidence, null, 2));
  if (application) await application.close().catch(() => {});
}
