// Native plotting and drawing must retain distinct definitions of the same parameter.
import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { _electron as electron, expect } from "playwright/test";

const root = process.cwd();
const profile = path.join(root, ".tmp", `desktop-coordinates-${Date.now()}`);
const binary = process.env.CYTOFORGE_TEST_BINARY;
const evidencePath =
  process.env.CYTOFORGE_COORDINATE_EVIDENCE ||
  "artifacts/desktop-ordered-coordinates-source.json";
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
const errors = [],
  received = new Map();
let application, main, workspaceId, sampleId;
function observe(page) {
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("response", async (r) => {
    if (r.url().includes("/plot?") && r.ok()) {
      try {
        received.set(page, await r.json());
      } catch {}
    }
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
  main = await application.firstWindow();
  observe(main);
  await main.setViewportSize({ width: 1540, height: 1050 });
  await main.waitForLoadState("networkidle");
}
async function request(route, body, page = main) {
  const value = await page.evaluate(
    async ({ route, body }) => {
      const { token } = await (await fetch("/api/bootstrap")).json();
      const r = await fetch("/api" + route, {
        method: body ? "POST" : "GET",
        headers: {
          "X-CytoForge-Token": token,
          "Content-Type": "application/json",
        },
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!r.ok) throw new Error(await r.text());
      const value = await r.json();
      if (value.id && Array.isArray(value.gates))
        await globalThis.cytoforgeDesktop.notifyWorkspaceChanged(value.id);
      return value;
    },
    { route, body },
  );
  return value;
}
const documentFor = () => request(`/workspaces/${workspaceId}`);
async function openWindow(state) {
  const opened = application.waitForEvent("window");
  await main.evaluate(
    (state) => globalThis.cytoforgeDesktop.openPlotWindow(state),
    state,
  );
  const page = await opened;
  observe(page);
  await page.setViewportSize({ width: 1260, height: 900 });
  await page.waitForLoadState("networkidle");
  return page;
}
async function payload(page, coordinate, mode) {
  await expect
    .poll(() => {
      const d = received.get(page);
      return d?.coordinate_gate_id === coordinate && d?.mode === mode;
    })
    .toBe(true);
  await page.locator('.primary-plot canvas[data-ready="true"]').waitFor();
  return received.get(page);
}
async function inspector(page) {
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
const deadline = setTimeout(() => application?.process().kill(), 300000);
try {
  await launch();
  await main
    .getByRole("button", { name: "New experiment", exact: true })
    .click();
  await main
    .getByLabel("Experiment name", { exact: true })
    .fill("Native ordered coordinates");
  await main
    .getByRole("button", { name: "Create workspace", exact: true })
    .click();
  const csv = path.join(profile, "independent-axis-labels.csv");
  writeFileSync(csv, ["X,Y", ...rows.map((p) => p.join(","))].join("\n"));
  const chooser = main.waitForEvent("filechooser");
  await main.getByRole("button", { name: "Import FCS", exact: true }).click();
  await (await chooser).setFiles(csv);
  await main.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(main.getByRole("dialog", { name: /Import/ })).toHaveCount(0);
  workspaceId = await main.getByLabel("Active workspace").inputValue();
  let doc = await documentFor();
  sampleId = doc.samples[0].id;
  doc = await request(`/workspaces/${workspaceId}/compensations`, {
    revision: doc.revision,
    compensation: {
      id: uid(),
      name: "Native diagonal control",
      detectors: ["X", "Y"],
      matrix: [
        [2, 0],
        [0, 4],
      ],
    },
  });
  const matrix = doc.compensations[0].id;
  const parent = {
    id: uid(),
    sample_id: sampleId,
    name: "Separate definitions",
    kind: "hyperrectangle",
    dimensions: [
      {
        channel: "X",
        compensation_ref: "uncompensated",
        minimum: -10,
        maximum: 10,
        transform: { kind: "linear" },
      },
      {
        channel: "X",
        compensation_ref: matrix,
        minimum: -2,
        maximum: 2,
        transform: { kind: "asinh", cofactor: 2 },
      },
    ],
  };
  const volume = {
    ...parent,
    id: uid(),
    name: "Three native definitions",
    dimensions: [
      ...parent.dimensions,
      {
        channel: "X",
        compensation_ref: "uncompensated",
        ratio_channels: ["X", "Y"],
        minimum: -10,
        maximum: 10,
        transform: { kind: "linear" },
      },
    ],
  };
  doc = await request(`/workspaces/${workspaceId}/gates/batch`, {
    revision: doc.revision,
    gates: [parent, volume],
  });
  await main.locator(".gate-row").filter({ hasText: parent.name }).click();
  await expect(main.locator(".analysis-heading h1")).toHaveText(parent.name);
  const state = {
    workspaceId,
    sampleId,
    gateId: parent.id,
    coordinateGateId: parent.id,
    x: "X",
    y: "X",
    mode: "scatter",
    bounds: [-5, 10, -2, 2],
  };
  const popup = await openWindow(state);
  const plotted = await payload(popup, parent.id, "scatter");
  assert.equal(plotted.x_transform.kind, "linear");
  assert.equal(plotted.y_transform.kind, "asinh");
  assert.equal(plotted.count, 1295);
  assert(
    plotted.points.some(
      (p) =>
        Math.abs(p[0] - 3.2) < 1e-8 && Math.abs(p[1] - Math.asinh(0.8)) < 1e-8,
    ),
  );
  evidence.checks.push(
    "A native scatter popup renders raw X and independently compensated/asinh X with all acquisition events",
  );
  await popup
    .getByRole("button", { name: "Rectangle gate", exact: true })
    .click();
  const canvas = popup.locator(".primary-plot canvas");
  await canvas.scrollIntoViewIfNeeded();
  const box = await canvas.boundingBox();
  const pixel = ([x, y]) => [
    box.x + 64 + ((x + 5) / 15) * (box.width - 88),
    box.y + 24 + (1 - (y + 2) / 4) * (box.height - 78),
  ];
  const a = pixel([3.1, 0.65]),
    b = pixel([3.4, 0.85]);
  await popup.mouse.move(...a);
  await popup.mouse.down();
  await popup.mouse.move(...b, { steps: 10 });
  await popup.mouse.up();
  await popup
    .getByLabel("Population name", { exact: true })
    .fill("Native drawn truth");
  await popup
    .getByRole("button", { name: "Create population", exact: true })
    .click();
  await expect(popup.getByRole("dialog")).toHaveCount(0);
  doc = await documentFor();
  const drawn = doc.gates.find((g) => g.name === "Native drawn truth");
  assert(
    drawn && drawn.parent_id === parent.id && drawn.dimensions.length === 2,
  );
  assert.equal(drawn.dimensions[0].compensation_ref, "uncompensated");
  assert.equal(drawn.dimensions[1].compensation_ref, matrix);
  assert.equal(drawn.dimensions[0].transform.kind, "linear");
  assert.equal(drawn.dimensions[1].transform.kind, "asinh");
  const counts = await request(
    `/workspaces/${workspaceId}/samples/${sampleId}/counts`,
  );
  assert.equal(counts.find((g) => g.id === drawn.id).count, 137);
  await expect(main.locator(".gate-tree")).toContainText(drawn.name);
  evidence.checks.push(
    "Native pointer drawing saves both original axis references and independently selects exactly 137 labelled events across windows",
  );
  const histogram = await openWindow({
    ...state,
    mode: "cdf",
    y: null,
    bounds: [-5, 10],
  });
  const cdf = await payload(histogram, parent.id, "cdf");
  assert.equal(cdf.x_transform.kind, "linear");
  assert.equal(cdf.cdf_denominator, 1295);
  evidence.checks.push(
    "A native CDF retains its first raw coordinate and the complete acquisition denominator",
  );
  const cloud = await openWindow({
    ...state,
    coordinateGateId: volume.id,
    gateId: parent.id,
    mode: "3d",
    bounds: [-5, 10, -2, 2, -5, 5],
    threeD: { z: "X", all_events: true },
  });
  await cloud.locator('.three-d-overlay[data-ready="true"]').waitFor();
  const xyz = received.get(cloud);
  assert.equal(xyz.axes[0].compensation_ref, "uncompensated");
  assert.equal(xyz.axes[1].compensation_ref, matrix);
  assert.deepEqual(xyz.axes[2].ratio_channels, ["X", "Y"]);
  assert.equal(xyz.count, 1295);
  assert.equal(xyz.finite_count, 1286);
  evidence.checks.push(
    "A native 3D window keeps raw, fixed/asinh and ratio definitions of the same measured parameter with exact finite-event accounting",
  );
  await cloud
    .getByRole("button", { name: "3D bounds / box gate", exact: true })
    .click();
  for (const [i, axis] of ["X", "Y", "Z"].entries()) {
    const limits = [
      [3.1, 3.4],
      [0.65, 0.85],
      [1.4, 1.6],
    ][i];
    await cloud
      .getByLabel(`3D ${axis} minimum`, { exact: true })
      .fill(String(limits[0]));
    await cloud
      .getByLabel(`3D ${axis} maximum`, { exact: true })
      .fill(String(limits[1]));
  }
  await cloud
    .getByRole("button", { name: "Create 3D box gate", exact: true })
    .click();
  await cloud
    .getByLabel("Population name", { exact: true })
    .fill("Three-axis drawn truth");
  await cloud
    .getByRole("button", { name: "Create population", exact: true })
    .click();
  await expect(cloud.getByRole("dialog")).toHaveCount(0);
  doc = await documentFor();
  const volumeDrawn = doc.gates.find(
    (g) => g.name === "Three-axis drawn truth",
  );
  assert(volumeDrawn && volumeDrawn.dimensions.length === 3);
  assert.deepEqual(
    volumeDrawn.dimensions.map((d) => d.compensation_ref),
    ["uncompensated", matrix, "uncompensated"],
  );
  assert.deepEqual(volumeDrawn.dimensions[2].ratio_channels, ["X", "Y"]);
  const volumeCounts = await request(
    `/workspaces/${workspaceId}/samples/${sampleId}/counts`,
  );
  assert.equal(volumeCounts.find((g) => g.id === volumeDrawn.id).count, 137);
  evidence.checks.push(
    "Native volume gating accepts repeated measured parameters while preserving all three bases and exactly 137 independently labelled cells",
  );
  const report = await request(`/workspaces/${workspaceId}/reports/render`, {
    revision: doc.revision,
    definition: {
      name: "Ordered native vector report",
      elements: [
        {
          kind: "plot",
          width_mm: 150,
          height_mm: 100,
          plot: {
            sample_id: sampleId,
            gate_id: drawn.id,
            coordinate_gate_id: parent.id,
            x: "X",
            y: "X",
            mode: "scatter",
            bounds: [-5, 10, -2, 2],
          },
        },
      ],
    },
    validate_sources: true,
  });
  assert(
    report.exportable &&
      report.svg.includes("<path") &&
      !report.svg.includes("<image"),
  );
  const layer = report.manifest.elements[0].layers[0];
  assert.equal(layer.population_count, 137);
  evidence.checks.push(
    "The native application's vector report retains the exact drawn population and exports without raster substitution",
  );
  await popup.locator(".gate-row").filter({ hasText: drawn.name }).click();
  await expect(popup.locator(".analysis-heading h1")).toHaveText(drawn.name);
  await inspector(popup);
  await expect(popup.locator(".inspector-count strong")).toHaveText("137");
  await popup.screenshot({
    path: path.join(
      root,
      "artifacts/screenshots/desktop-ordered-coordinates.png",
    ),
  });
  const before = await documentFor();
  await closeDesktop();
  await launch();
  await expect(main.getByLabel("Active workspace")).toHaveValue(workspaceId);
  const after = await documentFor();
  assert.deepEqual(after.gates, before.gates);
  assert.equal(after.revision, before.revision);
  await expect.poll(() => application.windows().length).toBe(4);
  assert.deepEqual(errors, []);
  evidence.checks.push(
    "Native independent views and exact saved dimension definitions survive application restart",
  );
  evidence.status = "passed";
  evidence.validated_at = new Date().toISOString();
  evidence.workspace_id = workspaceId;
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
        "artifacts/screenshots/desktop-ordered-coordinates-failure.png",
      ),
    })
    .catch(() => {});
  throw error;
} finally {
  clearTimeout(deadline);
  writeFileSync(evidencePath, JSON.stringify(evidence, null, 2));
  if (application) await application.close().catch(() => {});
}
