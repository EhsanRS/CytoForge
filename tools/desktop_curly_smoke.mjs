// Native curly partitions, shared center/arms, full-event counts and atomic edits.
import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { _electron as electron, expect } from "playwright/test";

const root = process.cwd();
const profile = path.join(root, ".tmp", "desktop-curlys-" + Date.now());
const evidencePath =
  process.env.CYTOFORGE_CURLY_EVIDENCE ||
  path.join(root, "artifacts/desktop-curly-source.json");
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
    for (let n = 0; n < (xi + 1) * (yi + 2); n++)
      rows.push([[-1, 0, 1, 4, 9][xi], [-1, 0, 1, 4, 9][yi]]);
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
async function drawCurly(page, name) {
  await ready(page);
  await page
    .getByRole("button", { name: "Curly quadrants", exact: true })
    .click();
  await page.mouse.click(...(await plotPoint(page, [0, 0])));
  await expect(
    page.getByRole("dialog", {
      name: "Create curly populations",
      exact: true,
    }),
  ).toBeVisible();
  await page.getByLabel("Population name", { exact: true }).fill(name);
  await page.getByLabel("X curly center", { exact: true }).fill("0");
  await page.getByLabel("Y curly center", { exact: true }).fill("0");
  await page.getByLabel("X noise coefficient", { exact: true }).fill("2");
  await page.getByLabel("Y noise coefficient", { exact: true }).fill("3");
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
  const [cx, cy] = geometry.center.map(Math.sinh);
  const [ax, ay] = geometry.coefficients;
  const counts = [0, 0, 0, 0];
  for (const [x, y] of values) {
    const xp =
      x >=
      cx + ax * (Math.sqrt(Math.max(y, cy, 0)) - Math.sqrt(Math.max(cy, 0)));
    const yp =
      y >=
      cy + ay * (Math.sqrt(Math.max(x, cx, 0)) - Math.sqrt(Math.max(cx, 0)));
    counts[(yp ? (xp ? 2 : 1) : xp ? 3 : 4) - 1]++;
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
    .fill("Native curly quadrant truth");
  await main
    .getByRole("button", { name: "Create workspace", exact: true })
    .click();
  const csv = path.join(profile, "weighted-curly-boundaries.csv");
  writeFileSync(
    csv,
    ["FL1-A,FL2-A", ...rows.map((p) => p.join(","))].join("\n"),
  );
  await importCsv(main, csv);
  workspaceId = await main.getByLabel("Active workspace").inputValue();
  let doc = await documentFor();
  sampleId = doc.samples[0].id;
  const basis = {
    id: uid(),
    name: "Fluorescence basis",
    sample_id: sampleId,
    kind: "hyperrectangle",
    dimensions: ["FL1-A", "FL2-A"].map((channel) => ({
      channel,
      compensation_ref: "uncompensated",
      transform: { kind: "linear" },
      minimum: null,
      maximum: null,
    })),
  };
  doc = await request(`/workspaces/${workspaceId}/gates`, {
    revision: doc.revision,
    gate: basis,
  });
  await selectGate(
    main,
    doc.gates.find((g) => g.id === basis.id),
  );
  await expect(
    main.getByRole("button", { name: "Curly quadrants", exact: true }),
  ).toBeDisabled();
  basis.dimensions.forEach(
    (d) => (d.transform = { kind: "asinh", cofactor: 1 }),
  );
  doc = await request(
    `/workspaces/${workspaceId}/gates/${basis.id}`,
    { revision: doc.revision, gate: basis },
    "PUT",
  );
  await main
    .getByRole("button", { name: "Reset population view", exact: true })
    .click();
  await expect(
    main.getByRole("button", { name: "Curly quadrants", exact: true }),
  ).toBeEnabled();
  await expect.poll(() => plots.get(main)?.coordinate_gate_id).toBe(basis.id);
  await main.getByRole("button", { name: "Histogram", exact: true }).click();
  await ready(main);
  await expect(
    main.getByRole("button", { name: "Curly quadrants", exact: true }),
  ).toBeDisabled();
  await main.locator(".plot-stage").focus();
  await main.keyboard.press("c");
  await expect(
    main.getByRole("button", { name: "Curly quadrants", exact: true }),
  ).not.toHaveAttribute("aria-pressed", "true");
  await main.getByRole("button", { name: "Density", exact: true }).click();
  await ready(main);
  evidence.checks.push(
    "Native curly toolbar and C shortcut require two fluorescence axes with nonlinear unclipped transforms; histogram and linear axes are disabled",
  );
  const before = await documentFor();
  const history = await request(`/workspaces/${workspaceId}/history`);
  const initial = { center: [0, 0], coefficients: [2, 3] };
  const initialCounts = independentCounts(initial);
  await drawCurly(main, "Curly");
  await familyCounts(main, initialCounts);
  await expect(main.locator("[data-shape-handle='curly-center']")).toHaveCount(
    1,
  );
  await expect(main.locator(".gate-shape-body path")).toHaveAttribute(
    "d",
    /M.*L/,
  );
  assert.deepEqual(await documentFor(), before);
  assert.deepEqual(
    await request(`/workspaces/${workspaceId}/history`),
    history,
  );
  const saved = await saveFamily(main, "Curly");
  let family = saved.family;
  doc = saved.doc;
  assert.equal(doc.revision, before.revision + 1);
  assert(family.every((g) => g.kind === "curly" && g.parent_id === basis.id));
  await assertCounts(family, initialCounts);
  evidence.initial_counts = initialCounts;
  evidence.checks.push(
    "Native creation previews four shared unbounded square-root populations without changing workspace/history; saved counts match an independent raw-intensity predicate",
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
  await expect(
    main.getByRole("button", { name: "Contract 5%", exact: true }),
  ).toBeDisabled();
  await expect(
    main.getByRole("button", { name: "Expand 5%", exact: true }),
  ).toBeDisabled();
  await dragHandle(main, "curly-center", [0.35, 0.25]);
  const moved = structuredClone(drafts.get(main).curly);
  assert.deepEqual(moved.coefficients, initial.coefficients);
  await familyCounts(main, independentCounts(moved));
  assert.deepEqual((await documentFor()).gates, doc.gates);
  await cancel(main);
  await assertCounts(family, initialCounts);
  assert.deepEqual(await state(popup), popupView);
  evidence.checks.push(
    "The real shared-center handle previews all four counts, keeps noise coefficients unchanged and leaves the independent native popup state intact; Cancel preserves saved geometry",
  );
  await inline(main, family[0]);
  await dragHandle(main, "curly-center", [0.35, 0.25]);
  const accepted = structuredClone(drafts.get(main).curly);
  doc = await apply(main);
  family = family.map((g) => doc.gates.find((v) => v.id === g.id));
  assert(
    family.every((g) => JSON.stringify(g.curly) === JSON.stringify(accepted)),
  );
  await assertCounts(family, independentCounts(accepted));
  await expect(popup.locator(".inspector-count strong")).toHaveText(
    String(independentCounts(accepted)[1]),
  );
  await request(`/workspaces/${workspaceId}/undo`, { revision: doc.revision });
  await assertCounts(family, initialCounts);
  doc = await request(`/workspaces/${workspaceId}/redo`, {
    revision: (await documentFor()).revision,
  });
  await assertCounts(family, independentCounts(accepted));
  evidence.checks.push(
    "Apply moves all four curly populations atomically with stable IDs; native popup counts synchronize and Undo/Redo retain exact scientific geometry",
  );
  await inline(popup, family[1]);
  const retained = structuredClone(drafts.get(popup));
  doc = await request(
    `/workspaces/${workspaceId}`,
    { revision: doc.revision, name: "External curly revision" },
    "PATCH",
  );
  await expect(
    popup.locator('.gate-draft-conflict[role="alert"]'),
  ).toContainText("workspace changed");
  await expect(
    popup.getByRole("button", { name: "Apply gate edit", exact: true }),
  ).toBeDisabled();
  assert.deepEqual(drafts.get(popup).curly, retained.curly);
  await cancel(popup);
  evidence.checks.push(
    "Concurrent edits preserve a native popup's curly draft and block stale Apply until review",
  );
  await inline(main, family[0]);
  await main
    .getByRole("button", { name: "Numeric settings", exact: true })
    .click();
  await main.getByLabel("X curly center", { exact: true }).fill("0");
  await main.getByLabel("Y curly center", { exact: true }).fill("0");
  await main.getByLabel("X noise coefficient", { exact: true }).fill("1");
  await main.getByLabel("Y noise coefficient", { exact: true }).fill("0.5");
  await main
    .getByRole("button", { name: "Save population", exact: true })
    .click();
  await expect(main.getByRole("dialog")).toHaveCount(0);
  doc = await documentFor();
  family = family.map((g) => doc.gates.find((v) => v.id === g.id));
  assert(
    family.every((g) => JSON.stringify(g.curly.coefficients) === "[1,0.5]"),
  );
  assert(
    family.every((g) =>
      g.dimensions.every(
        (d) =>
          d.transform.kind === "asinh" &&
          d.compensation_ref === "uncompensated",
      ),
    ),
  );
  await assertCounts(family, independentCounts(family[0].curly));
  evidence.checks.push(
    "Numeric settings change reviewed raw-unit noise coefficients and shared transformed center while retaining compensation and axis definitions; all member counts agree independently",
  );
  const targetCsv = path.join(profile, "weighted-curly-target.csv");
  writeFileSync(
    targetCsv,
    ["FL1-A,FL2-A", ...[...rows].reverse().map((p) => p.join(","))].join("\n"),
  );
  await importCsv(main, targetCsv);
  doc = await documentFor();
  const target = doc.samples[1];
  assert(target);
  doc = await request(`/workspaces/${workspaceId}/gates/apply`, {
    revision: doc.revision,
    source_sample_id: sampleId,
    target_sample_ids: [target.id],
  });
  const copied = doc.gates
    .filter((g) => g.sample_id === target.id && g.kind === "curly")
    .sort((a, b) => a.partition.member - b.partition.member);
  assert.equal(copied.length, 4);
  assert.notEqual(copied[0].partition.id, family[0].partition.id);
  assert(
    copied.every(
      (g) => JSON.stringify(g.curly) === JSON.stringify(family[0].curly),
    ),
  );
  await assertCounts(copied, independentCounts(family[0].curly), target.id);
  evidence.checks.push(
    "Propagating the scientific hierarchy retains coefficients and ordered dimensions, assigns a separate target family and reproduces all four full-event counts",
  );
  const prior = doc;
  doc = await request(
    `/workspaces/${workspaceId}/gates/${copied[0].id}?revision=${doc.revision}`,
    null,
    "DELETE",
  );
  assert(doc.gates.every((g) => g.partition?.id !== copied[0].partition.id));
  doc = await request(`/workspaces/${workspaceId}/undo`, {
    revision: doc.revision,
  });
  assert.deepEqual(doc.gates, prior.gates);
  await assertCounts(copied, independentCounts(family[0].curly), target.id);
  evidence.checks.push(
    "Deleting one member removes its linked family and Undo restores the target hierarchy, exact coefficients and event counts",
  );
  await main
    .locator(".sidebar-sample")
    .filter({ hasText: "weighted-curly-boundaries.csv" })
    .click();
  await selectGate(main, family[0]);
  await inline(main, family[0]);
  await main.screenshot({
    path: path.join(root, "artifacts/screenshots/desktop-curly-gates.png"),
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
  await assertCounts(family, independentCounts(family[0].curly));
  assert.deepEqual(errors, []);
  evidence.checks.push(
    "Normal native restart restores curly geometry, linked IDs, revision and independent popup state with no renderer errors",
  );
  assert.equal(evidence.checks.length, 9);
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
      path: path.join(root, "artifacts/screenshots/desktop-curly-failure.png"),
    })
    .catch(() => {});
  throw error;
} finally {
  clearTimeout(deadline);
  writeFileSync(evidencePath, JSON.stringify(evidence, null, 2));
  if (application) await application.close().catch(() => {});
}
