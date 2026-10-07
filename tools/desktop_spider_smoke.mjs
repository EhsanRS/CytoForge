// Native spider partitions, shared center/arms, full-event counts and atomic edits.
import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { _electron as electron, expect } from "playwright/test";

const root = process.cwd();
const profile = path.join(root, ".tmp", "desktop-spiders-" + Date.now());
const evidencePath =
  process.env.CYTOFORGE_SPIDER_EVIDENCE ||
  path.join(root, "artifacts/desktop-spider-source.json");
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
        const value = await response.json();
        if (value.bounds?.length === 4) plots.set(page, value);
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
async function drawSpider(page, name) {
  await ready(page);
  await page.getByRole("button", { name: "Spider gates", exact: true }).click();
  await page.mouse.click(...(await plotPoint(page, [0, 0])));
  await expect(
    page.getByRole("dialog", {
      name: "Create spider populations",
      exact: true,
    }),
  ).toBeVisible();
  await page.getByLabel("Population name", { exact: true }).fill(name);
  await page.getByLabel("X spider center", { exact: true }).fill("0");
  await page.getByLabel("Y spider center", { exact: true }).fill("0");
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
function independentCounts(geometry, values = rows) {
  const tau = 2 * Math.PI,
    counts = [0, 0, 0, 0];
  const members = [2, 1, 4, 3],
    owners = [2, 2, 1, 3];
  for (const [x, y] of values) {
    if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
    const dx = (x - geometry.center[0]) / geometry.scale[0];
    const dy = (y - geometry.center[1]) / geometry.scale[1];
    let member = 2;
    if (dx !== 0 || dy !== 0) {
      const angle = (Math.atan2(dy, dx) + tau) % tau;
      const offsets = geometry.angles.map(
        (a) => (a - geometry.angles[0] + tau) % tau,
      );
      const offset = (angle - geometry.angles[0] + tau) % tau;
      const boundary = offsets.findIndex((a) => Math.abs(a - offset) < 1e-12);
      if (boundary >= 0) member = owners[boundary];
      else member = members[offsets.findLastIndex((a) => offset > a)];
    }
    counts[member - 1]++;
  }
  assert.equal(
    counts.reduce((a, b) => a + b, 0),
    values.length,
  );
  return counts;
}
const deadline = setTimeout(() => application?.process().kill(), 300000);
try {
  await launch();
  await main
    .getByRole("button", { name: "New experiment", exact: true })
    .click();
  await main
    .getByLabel("Experiment name", { exact: true })
    .fill("Native spider gate truth");
  await main
    .getByRole("button", { name: "Create workspace", exact: true })
    .click();
  const csv = path.join(profile, "weighted-spider-boundaries.csv");
  writeFileSync(csv, ["X,Y", ...rows.map((p) => p.join(","))].join("\n"));
  await importCsv(main, csv);
  workspaceId = await main.getByLabel("Active workspace").inputValue();
  let doc = await documentFor();
  sampleId = doc.samples[0].id;
  await main.getByRole("button", { name: "Histogram", exact: true }).click();
  await ready(main);
  await expect(
    main.getByRole("button", { name: "Spider gates", exact: true }),
  ).toBeDisabled();
  await main.locator(".plot-stage").focus();
  await main.keyboard.press("s");
  await expect(
    main.getByRole("button", { name: "Spider gates", exact: true }),
  ).not.toHaveAttribute("aria-pressed", "true");
  await main.getByRole("button", { name: "Density", exact: true }).click();
  await ready(main);
  evidence.checks.push(
    "Spider toolbar and S shortcut are restricted to native two-dimensional plots",
  );
  const before = await documentFor();
  const history = await request(`/workspaces/${workspaceId}/history`);
  await drawSpider(main, "Spider");
  await familyCounts(main, [45, 180, 60, 15]);
  assert.deepEqual(await documentFor(), before);
  assert.deepEqual(
    await request(`/workspaces/${workspaceId}/history`),
    history,
  );
  const saved = await saveFamily(main, "Spider");
  let family = saved.family;
  doc = saved.doc;
  assert.equal(doc.revision, before.revision + 1);
  assert(family.every((g) => g.kind === "spider"));
  await assertCounts(family, [45, 180, 60, 15]);
  assert.deepEqual(independentCounts(family[0].spider), [45, 180, 60, 15]);
  evidence.checks.push(
    "Native click and review create four linked unbounded sectors in one revision; the full-parent preview is read-only and exact axis/center boundary counts are 45/180/60/15",
  );
  const opened = application.waitForEvent("window");
  await main
    .getByRole("button", { name: "Open plot in new window", exact: true })
    .click();
  popup = await opened;
  observe(popup);
  await popup.setViewportSize({ width: 1360, height: 980 });
  await selectGate(popup, family[1]);
  await inspector(popup);
  const popupView = await state(popup);
  await inline(main, family[0]);
  await expect(main.locator("[data-shape-handle^='spider-arm']")).toHaveCount(
    4,
  );
  await dragHandle(main, "spider-arm0", [1, 0.55]);
  const rotated = drafts.get(main).spider;
  assert(rotated.angles[0] > 0.2 && rotated.angles[0] < Math.PI / 2);
  const expected = independentCounts(rotated);
  await familyCounts(main, expected);
  assert.deepEqual((await documentFor()).gates, doc.gates);
  await cancel(main);
  await assertCounts(family, [45, 180, 60, 15]);
  assert.deepEqual(await state(popup), popupView);
  evidence.checks.push(
    "All four real arm handles are available; rotating one previews independently calculated linked counts while Cancel keeps saved gates and the popup view unchanged",
  );
  await inline(main, family[0]);
  await dragHandle(main, "spider-arm0", [1, 0.55]);
  const accepted = structuredClone(drafts.get(main).spider);
  doc = await apply(main);
  family = family.map((g) => doc.gates.find((v) => v.id === g.id));
  assert(
    family.every((g) => JSON.stringify(g.spider) === JSON.stringify(accepted)),
  );
  await assertCounts(family, independentCounts(accepted));
  await expect(popup.locator(".inspector-count strong")).toHaveText(
    String(independentCounts(accepted)[1]),
  );
  await request(`/workspaces/${workspaceId}/undo`, { revision: doc.revision });
  await assertCounts(family, [45, 180, 60, 15]);
  doc = await request(`/workspaces/${workspaceId}/redo`, {
    revision: (await documentFor()).revision,
  });
  await assertCounts(family, independentCounts(accepted));
  evidence.checks.push(
    "Apply updates every shared arm in one operation with stable member IDs; popup counts synchronize and Undo/Redo restore exact scientific geometry",
  );
  await inline(main, family[0]);
  const stable = structuredClone(drafts.get(main));
  const handle = main.locator('[data-shape-handle="spider-arm0"]');
  const box = await handle.boundingBox();
  await main.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await main.mouse.down();
  await main.mouse.move(...(await plotPoint(main, [-1, 0.3], true)));
  await main.mouse.up();
  await expect(main.locator(".shape-edit-error")).toContainText(
    "circular order",
  );
  assert.deepEqual(drafts.get(main).spider, stable.spider);
  await cancel(main);
  evidence.checks.push(
    "Crossing an adjacent arm is rejected visibly and retains the complete draft without relabelling or persisting a different sector",
  );
  await inline(main, family[0]);
  await dragHandle(main, "spider-center", [0.35, -0.25]);
  const moved = structuredClone(drafts.get(main).spider);
  await familyCounts(main, independentCounts(moved));
  assert.deepEqual(moved.angles, accepted.angles);
  doc = await apply(main);
  family = family.map((g) => doc.gates.find((v) => v.id === g.id));
  assert(
    family.every((g) => JSON.stringify(g.spider) === JSON.stringify(moved)),
  );
  await assertCounts(family, independentCounts(moved));
  evidence.checks.push(
    "Dragging the actual center translates all four unbounded populations while retaining arm angles and scientifically saved coordinate scales",
  );
  await inline(popup, family[1]);
  const retained = structuredClone(drafts.get(popup));
  doc = await request(
    `/workspaces/${workspaceId}`,
    { revision: doc.revision, name: "External spider revision" },
    "PATCH",
  );
  await expect(
    popup.locator('.gate-draft-conflict[role="alert"]'),
  ).toContainText("workspace changed");
  await expect(
    popup.getByRole("button", { name: "Apply gate edit", exact: true }),
  ).toBeDisabled();
  assert.deepEqual(drafts.get(popup).spider, retained.spider);
  await cancel(popup);
  evidence.checks.push(
    "Concurrent workspace changes retain a native popup's center/arm draft and block stale Apply",
  );
  await inline(main, family[0]);
  await main
    .getByRole("button", { name: "Numeric settings", exact: true })
    .click();
  await expect(
    main.getByRole("dialog", { name: "Edit population", exact: true }),
  ).toBeVisible();
  await main
    .getByLabel("Spider arm 1 angle (degrees)", { exact: true })
    .fill("30");
  await main.getByLabel("X spider center", { exact: true }).fill("0");
  await main.getByLabel("Y spider center", { exact: true }).fill("0");
  await main
    .getByRole("button", { name: "Save population", exact: true })
    .click();
  await expect(main.getByRole("dialog")).toHaveCount(0);
  doc = await documentFor();
  family = family.map((g) => doc.gates.find((v) => v.id === g.id));
  assert(
    family.every((g) => Math.abs(g.spider.angles[0] - Math.PI / 6) < 1e-15),
  );
  await assertCounts(family, independentCounts(family[0].spider));
  evidence.checks.push(
    "Inline-to-numeric handoff retains the family and edits shared center/angles without finite cropping; all member counts match independent angular labels",
  );
  await rootPopulation(main);
  const matrix = {
    id: uid(),
    name: "Fixed spider compensation",
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
  const basis = {
    id: uid(),
    name: "Native spider basis",
    sample_id: sampleId,
    kind: "hyperrectangle",
    dimensions: [
      {
        channel: "X",
        compensation_ref: "uncompensated",
        transform: { kind: "asinh", cofactor: 1 },
        minimum: -2,
        maximum: 2,
      },
      {
        channel: "X",
        compensation_ref: matrix.id,
        transform: { kind: "linear" },
        ratio_channels: ["Y", "X"],
        ratio_a: 1,
        ratio_b: 0,
        ratio_c: 0,
        minimum: -3,
        maximum: 3,
      },
    ],
  };
  doc = await request(`/workspaces/${workspaceId}/gates`, {
    revision: doc.revision,
    gate: basis,
  });
  await selectGate(
    main,
    doc.gates.find((g) => g.id === basis.id),
  );
  await expect.poll(() => plots.get(main)?.coordinate_gate_id).toBe(basis.id);
  await drawSpider(main, "Native spiders");
  await familyCounts(main, [27, 135, 45, 33]);
  const nativeSaved = await saveFamily(main, "Native spiders");
  doc = nativeSaved.doc;
  const native = nativeSaved.family;
  for (const gate of native) {
    assert.equal(gate.parent_id, basis.id);
    assert.equal(gate.dimensions[0].channel, "X");
    assert.equal(gate.dimensions[1].channel, "X");
    assert.equal(gate.dimensions[0].transform.kind, "asinh");
    assert.equal(gate.dimensions[0].compensation_ref, "uncompensated");
    assert.equal(gate.dimensions[1].compensation_ref, matrix.id);
    assert.deepEqual(gate.dimensions[1].ratio_channels, ["Y", "X"]);
  }
  await assertCounts(native, [27, 135, 45, 33]);
  const nativeRows = rows
    .filter(([x]) => x !== 0)
    .map(([x, y]) => [Math.asinh(x), y / (2 * x)]);
  assert.deepEqual(
    independentCounts(native[0].spider, nativeRows),
    [27, 135, 45, 33],
  );
  await inline(main, native[0]);
  await dragHandle(main, "spider-arm0", [1, 0.6]);
  const nativeDraft = structuredClone(drafts.get(main));
  await familyCounts(main, independentCounts(nativeDraft.spider, nativeRows));
  doc = await apply(main);
  const nativeFinal = native.map((g) => doc.gates.find((v) => v.id === g.id));
  await assertCounts(
    nativeFinal,
    independentCounts(nativeDraft.spider, nativeRows),
  );
  evidence.checks.push(
    "Native spider drawing and arm dragging preserve ordered duplicate labels, uncompensated asinh coordinates and a fixed-matrix ratio axis; every one of the 240 finite parent events has independently verified membership",
  );
  await inline(main, nativeFinal[0]);
  await main.screenshot({
    path: path.join(root, "artifacts/screenshots/desktop-spider-gates.png"),
  });
  await cancel(main);
  const finalDoc = await documentFor(),
    views = [await state(main), await state(popup)];
  await closeDesktop();
  await launch();
  await expect(main.getByLabel("Active workspace")).toHaveValue(workspaceId);
  await expect.poll(() => application.windows().length).toBe(2);
  popup = application.windows().find((p) => p.url().includes("plotWindow="));
  await ready(popup);
  doc = await documentFor();
  assert.deepEqual(doc.gates, finalDoc.gates);
  assert.equal(doc.revision, finalDoc.revision);
  assert.deepEqual([await state(main), await state(popup)], views);
  await assertCounts(family, independentCounts(family[0].spider));
  assert.deepEqual(errors, []);
  evidence.checks.push(
    "Normal native restart restores all spider geometry, shared IDs, revision, scientific counts and independent popup state with no renderer errors",
  );
  assert.equal(evidence.checks.length, 10);
  evidence.status = "passed";
  evidence.validated_at = new Date().toISOString();
  evidence.workspace_id = workspaceId;
  evidence.revision = doc.revision;
  evidence.renderer_errors = errors;
  await closeDesktop();
} catch (error) {
  evidence.status = "failed";
  evidence.error = error.message;
  evidence.checkpoint = evidence.checks.length;
  evidence.renderer_errors = errors;
  await main
    ?.screenshot({
      path: path.join(root, "artifacts/screenshots/desktop-spider-failure.png"),
    })
    .catch(() => {});
  throw error;
} finally {
  clearTimeout(deadline);
  writeFileSync(evidencePath, JSON.stringify(evidence, null, 2));
  if (application) await application.close().catch(() => {});
}
