// Independent native axis controls, exact gate capture and persistent plot windows.
import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { _electron as electron, expect } from "playwright/test";

const root = process.cwd();
const profile = path.join(
  root,
  ".tmp",
  `desktop-axis-definitions-${Date.now()}`,
);
const binary = process.env.CYTOFORGE_TEST_BINARY;
const output =
  process.env.CYTOFORGE_AXIS_EVIDENCE ||
  "artifacts/desktop-axis-definitions-source.json";
assert(path.resolve(output).startsWith(root + path.sep));
mkdirSync(profile, { recursive: true });
mkdirSync("artifacts/screenshots", { recursive: true });
const rows = [
  [-2, 4, 1],
  [-1, 2, 2],
  [0, 0, 3],
  [1, 2, 4],
  [2, 4, 5],
  [3, 1, 6],
  [4, 2, 7],
  [5, 5, 8],
  [6, 3, 9],
];
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
  plots = new Map();
let application, main, popup, workspaceId, sampleId, matrix;
const save = () =>
  writeFileSync(output, JSON.stringify(evidence, null, 2) + "\n");
const passed = (message) => {
  evidence.checks.push(message);
  save();
};
const uid = () => randomUUID().replaceAll("-", "");
function observe(page) {
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("response", async (response) => {
    if (response.ok() && response.url().includes("/plot?")) {
      try {
        plots.set(page, {
          data: await response.json(),
          query: new URL(response.url()).search,
        });
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
    packaged: app.isPackaged,
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
  return main.evaluate(
    async ({ route, body, method }) => {
      const { token } = await (await fetch("/api/bootstrap")).json();
      const r = await fetch("/api" + route, {
        method,
        headers: {
          "X-CytoForge-Token": token,
          "Content-Type": "application/json",
        },
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!r.ok) throw new Error(await r.text());
      const result = await r.json();
      if (method !== "GET" && result.id && Array.isArray(result.gates))
        await globalThis.cytoforgeDesktop.notifyWorkspaceChanged(result.id);
      return result;
    },
    { route, body, method },
  );
}
const documentFor = () => request(`/workspaces/${workspaceId}`);
const state = (page) =>
  page.evaluate(
    async () => (await globalThis.cytoforgeDesktop.getPlotSession()).state,
  );
async function ready(page, predicate = () => true) {
  await expect.poll(() => predicate(plots.get(page)?.data)).toBe(true);
  await page
    .locator('.primary-plot canvas[data-ready="true"]')
    .first()
    .waitFor();
}
async function definition(page, axis, edit) {
  await page
    .getByRole("button", { name: `${axis} coordinate definition`, exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: `${axis} coordinate definition`,
    exact: true,
  });
  await edit(dialog);
  await dialog
    .getByRole("button", { name: "Apply to plot", exact: true })
    .click();
  await expect(dialog).toHaveCount(0);
}
async function channelDefinition(page, axis, parameter, compensation) {
  await definition(page, axis, async (dialog) => {
    await dialog
      .getByLabel("Parameter type", { exact: true })
      .selectOption("channel");
    await dialog
      .getByLabel("Parameter", { exact: true })
      .selectOption(parameter);
    await dialog
      .getByLabel("Compensation", { exact: true })
      .selectOption(compensation);
    await dialog
      .getByLabel("Coordinate scale", { exact: true })
      .selectOption("linear");
  });
}
async function ratioDefinition(page, axis, label) {
  await definition(page, axis, async (dialog) => {
    await dialog
      .getByLabel("Parameter type", { exact: true })
      .selectOption("ratio");
    await dialog.getByLabel("Ratio label", { exact: true }).fill(label);
    await dialog.getByLabel("Numerator", { exact: true }).selectOption("X");
    await dialog.getByLabel("Denominator", { exact: true }).selectOption("Y");
    await dialog
      .getByLabel("Compensation", { exact: true })
      .selectOption("uncompensated");
    await dialog
      .getByLabel("Coordinate scale", { exact: true })
      .selectOption("linear");
  });
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
save();
try {
  await launch();
  await main
    .getByRole("button", { name: "New experiment", exact: true })
    .click();
  await main
    .getByLabel("Experiment name", { exact: true })
    .fill("Explicit native axis definitions");
  await main
    .getByRole("button", { name: "Create workspace", exact: true })
    .click();
  const csv = path.join(profile, "axis-truth.csv");
  writeFileSync(csv, ["X,Y,Z", ...rows.map((row) => row.join(","))].join("\n"));
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
      name: "Fixed diagonal truth",
      detectors: ["X", "Y", "Z"],
      matrix: [
        [2, 0, 0],
        [0, 4, 0],
        [0, 0, 8],
      ],
    },
  });
  matrix = doc.compensations[0].id;
  await expect(
    main.getByRole("button", {
      name: `Revision ${doc.revision} · History`,
      exact: true,
    }),
  ).toBeVisible();
  await main.getByRole("button", { name: "Scatter", exact: true }).click();
  await ready(main);
  const before = await documentFor(),
    oldState = await state(main);
  await main
    .getByRole("button", { name: "X coordinate definition", exact: true })
    .click();
  await main
    .getByRole("dialog")
    .getByLabel("Compensation", { exact: true })
    .selectOption(matrix);
  await main
    .getByRole("dialog")
    .getByRole("button", { name: "Cancel", exact: true })
    .click();
  assert.deepEqual(await documentFor(), before);
  assert.deepEqual(await state(main), oldState);
  passed(
    "Canceling coordinate review leaves native view, scientific workspace and history unchanged",
  );

  await channelDefinition(main, "X", "X", "uncompensated");
  await channelDefinition(main, "Y", "X", matrix);
  await ready(
    main,
    (d) => d?.mode === "scatter" && d?.axes?.[1]?.compensation_ref === matrix,
  );
  const expected = rows.map(([x]) => [x, x / 2]).sort((a, b) => a[0] - b[0]);
  assert.deepEqual(
    [...plots.get(main).data.points].sort((a, b) => a[0] - b[0]),
    expected,
  );
  assert.deepEqual(await documentFor(), before);
  const mainView = await state(main);
  passed(
    "Separate native X/Y controls display the same channel as raw and fixed-matrix coordinates with independently known values",
  );

  const opened = application.waitForEvent("window");
  await main
    .getByRole("button", { name: "Open plot in new window", exact: true })
    .click();
  popup = await opened;
  observe(popup);
  await popup.setViewportSize({ width: 1320, height: 1000 });
  await popup.waitForLoadState("networkidle");
  await ratioDefinition(popup, "Y", "X divided by Y");
  await ready(popup, (d) => d?.axes?.[1]?.ratio_channels?.[0] === "X");
  assert.equal(plots.get(popup).data.finite_count, 8);
  const ratioExpected = rows
    .filter(([, y]) => y !== 0)
    .map(([x, y]) => [x, x / y])
    .sort((a, b) => a[0] - b[0]);
  assert.deepEqual(
    [...plots.get(popup).data.points].sort((a, b) => a[0] - b[0]),
    ratioExpected,
  );
  assert.deepEqual(await state(main), mainView);
  assert.deepEqual(await documentFor(), before);
  passed(
    "A native popup changes to an explicit ratio independently; zero-denominator events are excluded and the main view remains unchanged",
  );

  const limits = plots.get(popup).data.bounds;
  const box = await popup.locator(".plot-stage").boundingBox();
  const point = ([x, y]) => [
    box.x + 64 + ((x - limits[0]) / (limits[1] - limits[0])) * (box.width - 88),
    box.y +
      24 +
      (1 - (y - limits[2]) / (limits[3] - limits[2])) * (box.height - 78),
  ];
  await popup
    .getByRole("button", { name: "Rectangle gate", exact: true })
    .click();
  await popup.mouse.move(...point([0.5, 0.45]));
  await popup.mouse.down();
  await popup.mouse.move(...point([4.5, 2.5]), { steps: 6 });
  await popup.mouse.up();
  const gateDialog = popup.getByRole("dialog");
  await gateDialog
    .getByLabel("Population name", { exact: true })
    .fill("Captured ratio population");
  for (let axis = 0; axis < 2; axis++) {
    await gateDialog
      .getByLabel("Inclusive minimum", { exact: true })
      .nth(axis)
      .fill(String([0.5, 0.45][axis]));
    await gateDialog
      .getByLabel("Exclusive maximum", { exact: true })
      .nth(axis)
      .fill(String([4.5, 2.5][axis]));
  }
  await gateDialog
    .getByRole("button", { name: "Create population", exact: true })
    .click();
  await expect(gateDialog).toHaveCount(0);
  doc = await documentFor();
  const gate = doc.gates.find((g) => g.name === "Captured ratio population");
  assert(gate);
  assert.equal(gate.dimensions[0].compensation_ref, "uncompensated");
  assert.deepEqual(gate.dimensions[1].ratio_channels, ["X", "Y"]);
  const independentCount = rows.filter(
    ([x, y]) =>
      x >= 0.5 &&
      x < 4.5 &&
      Number.isFinite(x / y) &&
      x / y >= 0.45 &&
      x / y < 2.5,
  ).length;
  assert.equal(independentCount, 3);
  const counts = await request(
    `/workspaces/${workspaceId}/samples/${sampleId}/counts`,
  );
  assert.equal(counts.find((c) => c.id === gate.id).count, independentCount);
  await main.getByRole("button", { name: "Undo", exact: true }).click();
  await expect.poll(async () => (await documentFor()).gates.length).toBe(0);
  await main.getByRole("button", { name: "Redo", exact: true }).click();
  await expect.poll(async () => (await documentFor()).gates.length).toBe(1);
  assert.equal(
    (
      await request(`/workspaces/${workspaceId}/samples/${sampleId}/counts`)
    ).find((c) => c.id === gate.id).count,
    3,
  );
  passed(
    "Drawing in the ratio popup captures both definitions in the saved gate; full-event counts match an independent predicate and survive undo/redo",
  );

  await popup.getByRole("button", { name: "3D", exact: true }).click();
  await channelDefinition(popup, "Y", "X", matrix);
  await ratioDefinition(popup, "Z", "3D ratio");
  await channelDefinition(popup, "Color", "X", matrix);
  await channelDefinition(popup, "Size", "X", "uncompensated");
  await ready(
    popup,
    (d) =>
      d?.mode === "3d" &&
      d?.axes?.[2]?.ratio_channels &&
      d?.scalar_dimensions?.[1]?.compensation_ref === "uncompensated",
  );
  const cloud = plots.get(popup);
  assert.equal(cloud.data.finite_count, 8);
  assert.deepEqual(
    cloud.data.axes.map((d) => d.compensation_ref),
    ["uncompensated", matrix, "uncompensated"],
  );
  const stream = await popup.evaluate(
    async ({ workspaceId, sampleId, cloud }) => {
      const { token } = await (await fetch("/api/bootstrap")).json();
      const r = await fetch(
        `/api/workspaces/${workspaceId}/samples/${sampleId}/plot3d/points${cloud.query}&revision=${cloud.data.revision}&data_key=${cloud.data.data_key}`,
        { headers: { "X-CytoForge-Token": token } },
      );
      if (!r.ok) throw new Error(await r.text());
      const data = new DataView(await r.arrayBuffer());
      return Array.from({ length: data.byteLength / 32 }, (_, i) => ({
        id: Number(data.getBigUint64(i * 32 + 20, true)),
        position: [0, 4, 8].map((offset) =>
          data.getFloat32(i * 32 + offset, true),
        ),
      }));
    },
    { workspaceId, sampleId, cloud },
  );
  assert.deepEqual(
    stream.map((r) => r.id),
    [0, 1, 3, 4, 5, 6, 7, 8],
  );
  for (const row of stream) {
    const [x, y] = rows[row.id],
      coordinates = [x, x / 2, x / y];
    coordinates.forEach((value, axis) => {
      const lo = cloud.data.bounds[axis * 2],
        hi = cloud.data.bounds[axis * 2 + 1];
      assert(Math.abs(row.position[axis] - (value - lo) / (hi - lo)) < 2e-6);
    });
  }
  passed(
    "Native 3D X/Y/Z and color/size accept independent definitions; typed event IDs and coordinates match the synthetic truth",
  );

  await ratioDefinition(main, "X", "Report ratio");
  await main.getByRole("button", { name: "Histogram", exact: true }).click();
  await ready(main, (d) => d?.mode === "histogram" && d?.x === "Report ratio");
  assert.equal(plots.get(main).data.finite_count, 8);
  await main.getByRole("button", { name: "CDF", exact: true }).click();
  await ready(main, (d) => d?.mode === "cdf");
  assert.equal(plots.get(main).data.cdf_denominator, 8);
  await main.getByRole("button", { name: "Density", exact: true }).click();
  await ready(main, (d) => d?.mode === "density");
  passed(
    "Explicit ratio definitions remain correct when switching between native histogram, CDF and two-dimensional views",
  );

  await main
    .getByRole("button", { name: "Layout studio", exact: true })
    .click();
  await main
    .getByRole("button", { name: "Add current plot", exact: true })
    .click();
  await main.getByRole("button", { name: /Save layout/ }).click();
  await expect.poll(async () => (await documentFor()).layouts.length).toBe(1);
  doc = await documentFor();
  const plot = doc.layouts[0].elements.find((e) => e.plot)?.plot;
  assert.deepEqual(plot.x_dimension.ratio_channels, ["X", "Y"]);
  assert.equal(plot.y_dimension.compensation_ref, matrix);
  await main.getByRole("button", { name: "Analysis", exact: true }).click();
  await ready(main);
  passed(
    "Layout studio captures explicit ratio and fixed-matrix definitions from the native current plot",
  );

  const originalWindows = application.windows().length;
  const invalid = await main.evaluate(async (matrix) => {
    const state = (await globalThis.cytoforgeDesktop.getPlotSession()).state;
    try {
      await globalThis.cytoforgeDesktop.openPlotWindow({
        ...state,
        xDimension: { ...state.xDimension, compensation_ref: matrix },
      });
      return false;
    } catch {
      return true;
    }
  }, "file:///outside-workspace");
  assert(invalid);
  assert.equal(application.windows().length, originalWindows);
  passed(
    "Native IPC rejects a coordinate descriptor containing a path instead of an allowed matrix reference",
  );

  const beforeRestart = await documentFor(),
    mainSaved = await state(main),
    popupSaved = await state(popup);
  await popup.screenshot({
    path: "artifacts/screenshots/native-axis-definitions-3d.png",
  });
  await closeDesktop();
  await launch();
  await expect(main.getByLabel("Active workspace")).toHaveValue(workspaceId);
  await expect.poll(() => application.windows().length).toBe(2);
  popup = application.windows().find((p) => p.url().includes("plotWindow="));
  assert(popup);
  await expect
    .poll(async () => JSON.stringify(await state(main)))
    .toBe(JSON.stringify(mainSaved));
  await expect
    .poll(async () => JSON.stringify(await state(popup)))
    .toBe(JSON.stringify(popupSaved));
  assert.deepEqual(await documentFor(), beforeRestart);
  assert.deepEqual(errors, []);
  evidence.gate_count = 3;
  evidence.total_events = rows.length;
  evidence.renderer_errors = errors;
  passed(
    "Independent per-axis and scalar definitions, exact gate counts and saved report definitions survive a native desktop restart",
  );
  evidence.status = "passed";
  evidence.validated_at = new Date().toISOString();
  save();
  await closeDesktop();
} catch (error) {
  evidence.status = "failed";
  evidence.error = error.stack;
  evidence.renderer_errors = errors;
  save();
  await main
    ?.screenshot({
      path: "artifacts/screenshots/native-axis-definitions-failure.png",
    })
    .catch(() => {});
  await application?.close().catch(() => {});
  throw error;
} finally {
  clearTimeout(deadline);
}
console.log(
  JSON.stringify({
    status: evidence.status,
    checks: evidence.checks.length,
    evidence: output,
  }),
);
