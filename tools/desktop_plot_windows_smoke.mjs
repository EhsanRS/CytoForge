// Real native plot windows: scoped views, synchronized gates and restart recovery.
import assert from "node:assert/strict";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { _electron as electron, expect } from "playwright/test";

const root = process.cwd();
const profile = path.join(root, ".tmp/desktop-plots-" + Date.now());
const evidencePath =
  process.env.CYTOFORGE_PLOT_WINDOWS_EVIDENCE ||
  path.join(root, "artifacts/desktop-plot-windows-smoke.json");
const binary = process.env.CYTOFORGE_TEST_BINARY;
mkdirSync(profile, { recursive: true });
mkdirSync(path.join(root, "artifacts/screenshots"), { recursive: true });
let application, main;
const errors = [];
const evidence = {
  status: "running",
  binary: binary || "development Electron",
  profile,
  checks: [],
};
writeFileSync(evidencePath, JSON.stringify(evidence));
const deadline = setTimeout(() => application?.process().kill(), 240000);
function launch() {
  return electron.launch({
    executablePath: binary,
    args: [
      ...(binary ? [] : ["."]),
      ...(process.platform === "linux"
        ? ["--headless", "--ozone-platform=headless", "--disable-gpu"]
        : []),
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
}
async function connect() {
  application = await launch();
  main = await application.firstWindow();
  main.on("pageerror", (error) => errors.push(error.message));
  await main.setViewportSize({ width: 1540, height: 1050 });
  await main.waitForLoadState("networkidle");
}
async function request(route, body, method = body ? "POST" : "GET") {
  return main.evaluate(
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
      return response.json();
    },
    { route, body, method },
  );
}
const plotContext = (page) =>
  page.evaluate(() => globalThis.cytoforgeDesktop.getPlotWindow());
async function newWindow(action) {
  const created = application.waitForEvent("window");
  await action();
  const page = await created;
  page.on("pageerror", (error) => errors.push(error.message));
  await page.setViewportSize({ width: 1540, height: 1050 });
  await page
    .locator('.plot-window canvas[data-ready="true"]')
    .waitFor({ timeout: 30000 });
  return page;
}
const ready = (page) => page.locator('canvas[data-ready="true"]').waitFor();
const documentFor = (id) => request("/workspaces/" + id);
async function drag(page, start = [0.2, 0.2], end = [0.65, 0.65]) {
  await ready(page);
  const box = await page.locator(".primary-plot canvas").boundingBox();
  assert(box);
  const point = ([x, y]) => [
    box.x + 64 + x * (box.width - 88),
    box.y + 24 + y * (box.height - 78),
  ];
  const [sx, sy] = point(start),
    [ex, ey] = point(end);
  await page.mouse.move(sx, sy);
  await page.mouse.down();
  await page.mouse.move(ex, ey, { steps: 8 });
  await page.mouse.up();
}
async function closeDesktop() {
  const closed = application.waitForEvent("close");
  await application.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows()
      .find((window) => !window.webContents.getURL().includes("plotWindow="))
      ?.close(),
  );
  await closed;
}
try {
  await connect();
  await main
    .getByRole("button", { name: "New experiment", exact: true })
    .click();
  await main
    .getByLabel("Experiment name", { exact: true })
    .fill("Independent plot windows");
  await main
    .getByRole("button", { name: "Create workspace", exact: true })
    .click();
  const fixture = path.join(profile, "plot-grid.csv");
  const rows = [
    "X,Y,Time",
    ...Array.from(
      { length: 100 },
      (_, i) => `${i % 10},${Math.floor(i / 10)},${i}`,
    ),
  ];
  writeFileSync(fixture, rows.join("\n"));
  const chooser = main.waitForEvent("filechooser");
  await main.getByRole("button", { name: "Import FCS", exact: true }).click();
  await (await chooser).setFiles(fixture);
  await main.getByRole("button", { name: "Continue", exact: true }).click();
  await ready(main);
  const id = await main.getByLabel("Active workspace").inputValue();
  let doc = await documentFor(id);
  const sample = doc.samples[0];
  assert.equal(sample.event_count, 100);
  assert.equal(sample.channels[0].transform.kind, "linear");
  const otherFixture = path.join(profile, "different-channels.csv");
  writeFileSync(otherFixture, "U,V,Clock\n1,2,0\n3,4,1\n");
  const otherChooser = main.waitForEvent("filechooser");
  await main.getByRole("button", { name: "Import FCS", exact: true }).click();
  await (await otherChooser).setFiles(otherFixture);
  await main.getByRole("button", { name: "Continue", exact: true }).click();
  const differentChannels = await newWindow(() =>
    main
      .locator(".sidebar-sample")
      .filter({ hasText: "different-channels.csv" })
      .dblclick(),
  );
  await expect(differentChannels.getByLabel("X axis channel")).toHaveValue("U");
  await expect(differentChannels.getByLabel("Y axis channel")).toHaveValue("V");
  await differentChannels
    .getByRole("button", { name: "Close plot window", exact: true })
    .click();
  await expect.poll(() => differentChannels.isClosed()).toBe(true);
  evidence.checks.push(
    "Double-clicking a sample with different parameters opens its actual channels",
  );
  await main
    .locator(".sidebar-sample")
    .filter({ hasText: "plot-grid.csv" })
    .click();
  const first = await newWindow(() =>
    main
      .getByRole("button", { name: "Open plot in new window", exact: true })
      .click(),
  );
  const firstState = await plotContext(first);
  const firstDescriptor = { ...firstState };
  delete firstDescriptor.id;
  assert.equal(firstState.workspaceId, id);
  assert.equal(firstState.sampleId, sample.id);
  await expect(first.getByLabel("Active workspace")).toBeDisabled();
  assert.equal(
    await first.getByRole("navigation", { name: "Workbench views" }).count(),
    0,
  );
  assert.equal(
    await first.evaluate(() => typeof globalThis.require),
    "undefined",
  );
  const second = await newWindow(() =>
    main.locator(".sidebar-sample").first().dblclick(),
  );
  const secondState = await plotContext(second);
  assert.notEqual(firstState.id, secondState.id);
  await first.getByLabel("X axis channel").selectOption("Time");
  await first.getByRole("button", { name: "Histogram", exact: true }).click();
  await ready(first);
  await first.getByRole("button", { name: "Density", exact: true }).click();
  await expect(first.getByLabel("Y axis channel")).toHaveValue("Y");
  await first.getByRole("button", { name: "Histogram", exact: true }).click();
  await ready(first);
  await expect(second.getByLabel("X axis channel")).toHaveValue("X");
  await expect(main.getByLabel("X axis channel")).toHaveValue("X");
  await expect(
    second.getByRole("button", { name: "Density", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  evidence.checks.push(
    "Separate native windows retain independent sample, axis and display mode",
  );

  // One-parameter zoom must survive the native IPC validator and duplication.
  await first
    .getByRole("button", { name: "Zoom into region", exact: true })
    .click();
  await drag(first);
  await expect
    .poll(async () => (await plotContext(first)).bounds?.length)
    .toBe(2);
  const histogramZoom = await plotContext(first);
  const histogramCopy = await newWindow(() =>
    first
      .getByRole("button", { name: "Open plot in new window", exact: true })
      .click(),
  );
  assert.deepEqual(
    (await plotContext(histogramCopy)).bounds,
    histogramZoom.bounds,
  );
  await histogramCopy
    .getByRole("button", { name: "Close plot window", exact: true })
    .click();
  await expect.poll(() => histogramCopy.isClosed()).toBe(true);
  await first.getByRole("button", { name: "CDF", exact: true }).click();
  await ready(first);
  await first.locator(".graph-settings summary").click();
  await first.getByLabel("Graph axis extent").selectOption("full");
  await first.getByLabel("Graph resolution").selectOption("64");
  await drag(first);
  await expect.poll(async () => (await plotContext(first)).mode).toBe("cdf");
  await expect
    .poll(async () => (await plotContext(first)).bounds?.length)
    .toBe(2);
  const cdfState = await plotContext(first);
  assert.equal(cdfState.bins, 64);
  assert.equal(cdfState.graphOptions.axis_extent, "full");
  const cdf = await request(
    `/workspaces/${id}/samples/${sample.id}/plot?x=Time&mode=cdf&bins=64&bounds=${encodeURIComponent(JSON.stringify(cdfState.bounds))}`,
  );
  assert.equal(cdf.cdf_denominator, 100);
  assert.deepEqual(
    cdf.cdf_counts,
    cdf.edges.map(
      (edge) =>
        rows.slice(1).filter((row) => Number(row.split(",")[2]) <= edge).length,
    ),
  );
  const cdfCopy = await newWindow(() =>
    first
      .getByRole("button", { name: "Open plot in new window", exact: true })
      .click(),
  );
  const cdfCopyState = await plotContext(cdfCopy);
  assert.deepEqual(cdfCopyState.bounds, cdfState.bounds);
  assert.deepEqual(cdfCopyState.graphOptions, cdfState.graphOptions);
  assert.equal(cdfCopyState.bins, 64);
  assert.equal(cdfCopyState.mode, "cdf");
  await cdfCopy
    .getByRole("button", { name: "Close plot window", exact: true })
    .click();
  await expect.poll(() => cdfCopy.isClosed()).toBe(true);
  evidence.checks.push(
    "Histogram and CDF zoom persist through native IPC and duplication; CDF counts agree with all 100 source events",
  );

  // All probability displays are real canvas renders in an independent native window.
  evidence.graph_views = {};
  for (const mode of ["contour", "zebra", "pseudocolor"]) {
    await second
      .getByRole("button", {
        name: mode[0].toUpperCase() + mode.slice(1),
        exact: true,
      })
      .click();
    await expect.poll(async () => (await plotContext(second)).mode).toBe(mode);
    await ready(second);
    if (!(await second.getByLabel("Graph resolution").isVisible()))
      await second.locator(".graph-settings summary").click();
    await second.getByLabel("Graph resolution").selectOption("64");
    if (mode !== "contour")
      await second.getByLabel("Graph palette").selectOption("viridis");
    if (mode !== "pseudocolor")
      await second.getByLabel("Contour probability spacing").selectOption("10");
    await expect.poll(async () => (await plotContext(second)).bins).toBe(64);
    await ready(second);
    const state = await plotContext(second);
    const payload = await request(
      `/workspaces/${id}/samples/${sample.id}/plot?x=X&y=Y&mode=${mode}&bins=64&graph_options=${encodeURIComponent(JSON.stringify(state.graphOptions))}`,
    );
    assert.equal(payload.count, 100);
    assert.equal(payload.finite_count, 100);
    assert.equal(
      payload.counts.reduce((a, b) => a + b, 0),
      100,
    );
    if (mode !== "pseudocolor")
      assert(
        payload.contour_vertices > 0 && payload.probability_denominator === 100,
      );
    assert.equal((await plotContext(first)).mode, "cdf");
    const image = path.join(
      root,
      "artifacts/screenshots/desktop-graph-" + mode + ".png",
    );
    await second.screenshot({ path: image });
    evidence.graph_views[mode] = {
      count: payload.count,
      finite_count: payload.finite_count,
      contour_vertices: payload.contour_vertices,
      probability_levels: payload.probability_levels,
      options: state.graphOptions,
      screenshot: image,
    };
  }
  await second.getByRole("button", { name: "Density", exact: true }).click();
  await ready(second);
  evidence.checks.push(
    "Contour, zebra and pseudocolor render in native windows with independent probability, palette and resolution controls",
  );

  await second
    .getByRole("button", { name: "Zoom into region", exact: true })
    .click();
  await drag(second);
  await expect(
    second.getByRole("button", { name: "Reset zoom", exact: true }),
  ).toBeVisible();
  await expect(
    first.getByRole("button", { name: "Reset zoom", exact: true }),
  ).toBeVisible();
  assert.deepEqual((await plotContext(first)).bounds, cdfState.bounds);
  await expect(
    main.getByRole("button", { name: "Reset zoom", exact: true }),
  ).toHaveCount(0);
  const zoomed = await plotContext(second);
  assert.equal(zoomed.bounds.length, 4);
  const duplicate = await newWindow(() =>
    second
      .getByRole("button", { name: "Open plot in new window", exact: true })
      .click(),
  );
  assert.deepEqual((await plotContext(duplicate)).bounds, zoomed.bounds);
  await duplicate
    .getByRole("button", { name: "Close plot window", exact: true })
    .click();
  await expect.poll(() => duplicate.isClosed()).toBe(true);
  await second.getByRole("button", { name: "Reset zoom", exact: true }).click();
  await ready(second);
  evidence.checks.push(
    "Zoom is independent and copied deliberately when duplicating a plot window",
  );

  await second
    .getByRole("button", { name: "Rectangle gate", exact: true })
    .click();
  await drag(second);
  await second
    .getByLabel("Population name", { exact: true })
    .fill("Popup selection");
  await second
    .getByRole("button", { name: "Create population", exact: true })
    .click();
  await expect(second.getByRole("dialog")).not.toBeVisible();
  await expect(main.locator(".gate-tree")).toContainText("Popup selection");
  await expect(first.locator(".gate-tree")).toContainText("Popup selection");
  doc = await documentFor(id);
  const createdGate = doc.gates.find((gate) => gate.name === "Popup selection");
  assert(createdGate);
  const [xmin, xmax, ymin, ymax] = createdGate.bounds;
  const independentlyCounted = rows
    .slice(1)
    .map((row) => row.split(",").map(Number))
    .filter(
      ([x, y]) => x >= xmin && x <= xmax && y >= ymin && y <= ymax,
    ).length;
  const counts = await request(`/workspaces/${id}/samples/${sample.id}/counts`);
  assert.equal(
    counts.find((count) => count.id === createdGate.id).count,
    independentlyCounted,
  );
  assert(independentlyCounted > 0 && independentlyCounted < 100);
  evidence.gate = {
    id: createdGate.id,
    bounds: createdGate.bounds,
    independently_counted: independentlyCounted,
  };
  evidence.checks.push(
    "Gate drawn in popup propagates to other windows; independent event count agrees",
  );

  const population = await newWindow(() =>
    main
      .locator(".gate-tree .gate-row")
      .filter({ hasText: "Popup selection" })
      .dblclick(),
  );
  await expect(population.locator(".analysis-heading h1")).toHaveText(
    "Popup selection",
  );
  await population
    .getByRole("button", { name: "Edit selected gate", exact: true })
    .click();
  await population
    .getByLabel("Population name", { exact: true })
    .fill("Retained draft name");
  doc = await documentFor(id);
  doc = await request(
    `/workspaces/${id}/gates/${createdGate.id}`,
    {
      revision: doc.revision,
      gate: {
        ...doc.gates.find((gate) => gate.id === createdGate.id),
        name: "Changed from another window",
        bounds: [1, 4, 1, 4],
      },
    },
    "PUT",
  );
  await expect(population.locator(".gate-draft-conflict")).toBeVisible();
  await expect(
    population.getByRole("button", { name: "Save population", exact: true }),
  ).toBeDisabled();
  await expect(
    population.getByLabel("Population name", { exact: true }),
  ).toHaveValue("Retained draft name");
  await expect(population.locator(".gate-draft-conflict")).toContainText(
    "Changed from another window",
  );
  await population
    .getByRole("button", {
      name: "Keep draft and use current workspace",
      exact: true,
    })
    .click();
  await population
    .getByRole("button", { name: "Save population", exact: true })
    .click();
  await expect(main.locator(".gate-tree")).toContainText("Retained draft name");
  doc = await documentFor(id);
  assert.equal(
    doc.gates.find((gate) => gate.id === createdGate.id).name,
    "Retained draft name",
  );
  assert.deepEqual(
    doc.gates.find((gate) => gate.id === createdGate.id).bounds,
    createdGate.bounds,
  );
  evidence.checks.push(
    "External edits retain local draft and block save until explicit review; no silent overwrite",
  );

  await main.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(population.locator(".analysis-heading h1")).toHaveText(
    "Changed from another window",
  );
  await main.getByRole("button", { name: "Redo", exact: true }).click();
  await expect(population.locator(".analysis-heading h1")).toHaveText(
    "Retained draft name",
  );
  doc = await documentFor(id);
  doc = await request(
    `/workspaces/${id}/gates/${createdGate.id}?revision=${doc.revision}`,
    null,
    "DELETE",
  );
  await expect(population.locator(".plot-input-unavailable")).toContainText(
    "This population is unavailable",
  );
  await expect(population.locator(".primary-plot canvas")).toHaveCount(0);
  assert.equal((await plotContext(population)).gateId, createdGate.id);
  await request(`/workspaces/${id}/undo`, { revision: doc.revision });
  await expect(population.locator(".analysis-heading h1")).toHaveText(
    "Retained draft name",
  );
  await ready(population);
  doc = await documentFor(id);
  doc = await request(
    `/workspaces/${id}/samples/${sample.id}?revision=${doc.revision}`,
    null,
    "DELETE",
  );
  await expect(first.locator(".plot-input-unavailable")).toContainText(
    "This sample is unavailable",
  );
  await expect(first.locator(".primary-plot canvas")).toHaveCount(0);
  assert.equal((await plotContext(first)).sampleId, sample.id);
  await request(`/workspaces/${id}/undo`, { revision: doc.revision });
  await ready(first);
  await ready(second);
  await ready(population);
  evidence.checks.push(
    "Undo, redo and deleted source recovery synchronize without substituting another sample or population",
  );

  const different = await request("/workspaces", {
    name: "Different main workspace",
  });
  await main.reload();
  await main.getByLabel("Active workspace").selectOption(different.id);
  await expect(main.locator(".workspace-breadcrumb")).toContainText(
    different.name,
  );
  assert.equal((await plotContext(first)).workspaceId, id);
  await expect(first.locator(".workspace-breadcrumb")).toContainText(
    "Independent plot windows",
  );
  const forbidden = await first.evaluate(
    async (state) => {
      try {
        await globalThis.cytoforgeDesktop.openPlotWindow(state);
        return false;
      } catch {
        return true;
      }
    },
    { ...firstDescriptor, workspaceId: different.id },
  );
  assert.equal(forbidden, true);
  assert.equal(
    await first.evaluate(async (id) => {
      globalThis.cytoforgeDesktop.saveWorkspace(id);
      return globalThis.cytoforgeDesktop.getWorkspace();
    }, id),
    null,
  );
  assert.equal(
    await first.evaluate(
      (id) => globalThis.cytoforgeDesktop.savePlateDraft(id, "{}"),
      id,
    ),
    false,
  );
  await first.evaluate(() => window.open("https://untrusted.example"));
  assert.equal(application.windows().length, 4);
  const invalid = await main.evaluate(
    async (state) => {
      try {
        await globalThis.cytoforgeDesktop.openPlotWindow({
          ...state,
          x: "missing-channel",
        });
        return false;
      } catch {
        return true;
      }
    },
    { ...firstDescriptor, workspaceId: different.id },
  );
  assert.equal(invalid, true);
  assert.equal(
    await first.evaluate(async (state) => {
      try {
        await globalThis.cytoforgeDesktop.openPlotWindow({
          ...state,
          x: "missing-channel",
        });
        return false;
      } catch {
        return true;
      }
    }, firstDescriptor),
    true,
  );
  evidence.checks.push(
    "Popup workspace remains pinned; foreign workspace, main-only persistence and arbitrary window opening are rejected",
  );

  await application.evaluate(
    ({ session }, filename) => {
      globalThis.plotPngDownload = new Promise((resolve) =>
        session.defaultSession.once("will-download", (_event, item) => {
          item.setSavePath(filename);
          item.once("done", (_event, state) => resolve(state));
        }),
      );
    },
    path.join(profile, "popup.png"),
  );
  await first
    .getByRole("button", { name: "Export plot as PNG", exact: true })
    .click();
  assert.equal(
    await application.evaluate(() => globalThis.plotPngDownload),
    "completed",
  );
  assert.equal(
    readFileSync(path.join(profile, "popup.png")).subarray(1, 4).toString(),
    "PNG",
  );
  evidence.checks.push("Native plot export produces a valid PNG");

  // A dirty plot must keep both child and root alive when the user cancels closing.
  await population
    .getByRole("button", { name: "Edit selected gate", exact: true })
    .click();
  await population
    .getByLabel("Population name", { exact: true })
    .fill("Do not lose this draft");
  await expect
    .poll(() =>
      application.evaluate(
        ({ BrowserWindow }) => BrowserWindow.getAllWindows().length,
      ),
    )
    .toBe(4);
  await application.evaluate(({ dialog }) => {
    globalThis.plotCloseQuestions = [];
    dialog.showMessageBox = async (_window, options) => {
      globalThis.plotCloseQuestions.push(options.title);
      return { response: 0 };
    };
  });
  const populationHandle = await application.browserWindow(population);
  await populationHandle.evaluate((window) => window.close());
  await populationHandle.dispose();
  await expect
    .poll(() =>
      application.evaluate(() => globalThis.plotCloseQuestions.length),
    )
    .toBe(1);
  assert.equal(population.isClosed(), false);
  await application.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows()
      .find((window) => !window.webContents.getURL().includes("plotWindow="))
      ?.close(),
  );
  await expect
    .poll(() =>
      application.evaluate(() => globalThis.plotCloseQuestions.length),
    )
    .toBe(2);
  assert.equal(main.isClosed(), false);
  await expect(
    population.getByLabel("Population name", { exact: true }),
  ).toHaveValue("Do not lose this draft");
  await population.getByRole("button", { name: "Cancel", exact: true }).click();
  await population
    .getByRole("button", { name: "Close plot window", exact: true })
    .click();
  await expect.poll(() => population.isClosed()).toBe(true);
  await main.getByLabel("Active workspace").selectOption(id);
  await main
    .locator(".gate-tree .gate-row")
    .filter({ hasText: "Retained draft name" })
    .click();
  await main
    .getByRole("button", { name: "Edit selected gate", exact: true })
    .click();
  await main
    .getByLabel("Population name", { exact: true })
    .fill("Main window draft");
  await application.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows()
      .find((window) => !window.webContents.getURL().includes("plotWindow="))
      ?.close(),
  );
  await expect
    .poll(() =>
      application.evaluate(() => globalThis.plotCloseQuestions.length),
    )
    .toBe(3);
  assert.equal(main.isClosed(), false);
  await expect(main.getByLabel("Population name", { exact: true })).toHaveValue(
    "Main window draft",
  );
  await main.getByRole("button", { name: "Cancel", exact: true }).click();
  await main.getByLabel("Active workspace").selectOption(different.id);
  evidence.checks.push(
    "Native child and application close confirmation protect unsaved popup and main-window gates",
  );

  // Keep independent views open across process/engine restart, with a new loopback port.
  await second.getByRole("button", { name: "Zebra", exact: true }).click();
  await ready(second);
  await second
    .getByRole("button", { name: "Zoom into region", exact: true })
    .click();
  await drag(second);
  await expect
    .poll(async () => (await plotContext(second)).bounds?.length)
    .toBe(4);
  const beforeRestart = [await plotContext(first), await plotContext(second)];
  assert.equal(beforeRestart[0].mode, "cdf");
  assert.equal(beforeRestart[0].bounds.length, 2);
  assert.equal(beforeRestart[1].mode, "zebra");
  assert.equal(beforeRestart[0].bins, 64);
  assert.equal(beforeRestart[1].bins, 64);
  const privateOrigin = new URL(main.url()).origin;
  const isolated = await application.evaluate(({ app, BrowserWindow }) => ({
    packaged: app.isPackaged,
    no_sandbox: app.commandLine.hasSwitch("no-sandbox"),
    windows: BrowserWindow.getAllWindows().map((window) => ({
      id: window.id,
      node: window.webContents.getLastWebPreferences().nodeIntegration,
      isolation: window.webContents.getLastWebPreferences().contextIsolation,
      sandbox: window.webContents.getLastWebPreferences().sandbox,
    })),
    enginePid: process
      ._getActiveHandles()
      .find((handle) => handle.spawnargs?.includes("--parent-pid"))?.pid,
  }));
  assert(
    isolated.windows.every(
      (window) => !window.node && window.isolation && window.sandbox,
    ),
  );
  assert(isolated.enginePid);
  assert.equal(
    isolated.no_sandbox,
    process.env.CYTOFORGE_TEST_NO_SANDBOX === "1",
  );
  await first.screenshot({
    path: path.join(root, "artifacts/screenshots/desktop-plot-cdf.png"),
  });
  await second.screenshot({
    path: path.join(root, "artifacts/screenshots/desktop-plot-gating.png"),
  });
  await closeDesktop();
  const settings = JSON.parse(
    readFileSync(path.join(profile, ".config/plot-windows.json")),
  );
  assert.equal(settings.windows.length, 2);
  assert.deepEqual(
    settings.windows.map((record) => record.state.bounds),
    beforeRestart.map((record) => record.bounds),
  );
  await expect
    .poll(() => {
      try {
        process.kill(isolated.enginePid, 0);
        return false;
      } catch {
        return true;
      }
    })
    .toBe(true);
  await connect();
  await expect
    .poll(
      () =>
        application
          .windows()
          .filter((page) => page.url().includes("plotWindow=")).length,
    )
    .toBe(2);
  const restored = application
    .windows()
    .filter((page) => page.url().includes("plotWindow="));
  for (const page of restored) {
    await page.setViewportSize({ width: 1540, height: 1050 });
    await ready(page);
    const state = await plotContext(page);
    assert.deepEqual(
      state,
      beforeRestart.find((record) => record.id === state.id),
    );
    await expect(page.getByLabel("Active workspace")).toHaveValue(id);
  }
  await expect(main.getByLabel("Active workspace")).toHaveValue(different.id);
  evidence.checks.push(
    "Open windows, axes, CDF/zebra modes, two/four-axis zoom, graph options and resolution survive native application restart; main selection is independent and old engine exits",
  );
  evidence.native = isolated;
  evidence.restart = {
    previous_private_origin: privateOrigin,
    current_private_origin: new URL(main.url()).origin,
    restored_windows: restored.length,
  };
  assert.deepEqual(errors, []);
  for (const page of restored)
    await page
      .getByRole("button", { name: "Close plot window", exact: true })
      .click();
  await closeDesktop();
  evidence.status = "passed";
  evidence.validated_at = new Date().toISOString();
  writeFileSync(evidencePath, JSON.stringify(evidence, null, 2));
  console.log(
    JSON.stringify({
      status: "passed",
      packaged: isolated.packaged,
      checks: evidence.checks.length,
      report: evidencePath,
    }),
  );
} catch (error) {
  evidence.status = "failed";
  evidence.error = error.stack;
  evidence.renderer_errors = errors;
  writeFileSync(evidencePath, JSON.stringify(evidence, null, 2));
  throw error;
} finally {
  clearTimeout(deadline);
  try {
    if (application && !main?.isClosed()) {
      await application.evaluate(({ dialog }) => {
        dialog.showMessageBox = async () => ({ response: 1 });
      });
      await application.close();
    }
  } catch {
    application?.process().kill("SIGTERM");
  }
}
