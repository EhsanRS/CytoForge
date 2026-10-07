// Actual Electron windows: population paths, coordinated moves and draft/stale guards.
import assert from "node:assert/strict";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { _electron as electron, expect } from "playwright/test";

const root = process.cwd();
const profile = path.join(root, ".tmp/desktop-navigation-" + Date.now());
const output =
  process.env.CYTOFORGE_NAVIGATION_EVIDENCE ||
  path.join(root, "artifacts/desktop-navigation-source.json");
const binary = process.env.CYTOFORGE_TEST_BINARY;
const evidence = {
  status: "running",
  binary: binary || "development Electron",
  profile,
  checks: [],
};
mkdirSync(profile, { recursive: true });
const save = () => writeFileSync(output, JSON.stringify(evidence, null, 2));
const passed = (check) => {
  evidence.checks.push(check);
  save();
};
save();
let application, main, desktopProcess;
const errors = [];
const timeout = setTimeout(() => desktopProcess?.kill(), 240000);
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
  desktopProcess = application.process();
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
const context = (page) =>
  page.evaluate(() => globalThis.cytoforgeDesktop.getPlotWindow());
const ready = (page) =>
  page.locator('canvas[data-ready="true"]').first().waitFor({ timeout: 30000 });
async function open(state) {
  const pending = application.waitForEvent("window");
  await main.evaluate(
    (value) => globalThis.cytoforgeDesktop.openPlotWindow(value),
    state,
  );
  const page = await pending;
  page.on("pageerror", (error) => errors.push(error.message));
  await page.setViewportSize({ width: 1540, height: 1050 });
  await ready(page);
  return page;
}
async function quit() {
  const closed = application.waitForEvent("close");
  await application.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows()
      .find((w) => !w.webContents.getURL().includes("plotWindow="))
      ?.close(),
  );
  await closed;
}
try {
  await launch();
  await main
    .getByRole("button", { name: "New experiment", exact: true })
    .click();
  await main
    .getByLabel("Experiment name", { exact: true })
    .fill("Native sample stepping");
  await main
    .getByRole("button", { name: "Create workspace", exact: true })
    .click();
  const files = ["First", "Missing", "Third", "Fourth"].map(
    (name, sampleIndex) => {
      const filename = path.join(profile, name + ".csv");
      writeFileSync(
        filename,
        [
          "X,Y,Z",
          ...Array.from(
            { length: 100 },
            (_, i) =>
              `${i % 10},${Math.floor(i / 10)},${i + sampleIndex * 0.01}`,
          ),
        ].join("\n"),
      );
      return filename;
    },
  );
  const chooser = main.waitForEvent("filechooser");
  await main.getByRole("button", { name: "Import FCS", exact: true }).click();
  await (await chooser).setFiles(files);
  await main.getByRole("button", { name: "Continue", exact: true }).click();
  await ready(main);
  await expect(main.locator(".sidebar-sample")).toHaveCount(4, {
    timeout: 30000,
  });
  const workspaceId = await main.evaluate(() =>
    localStorage.getItem("cytoforge.workspace"),
  );
  const route = "/workspaces/" + workspaceId;
  let doc = await request(route);
  // Imports preserve submitted acquisition order.
  const [first, missing, third, fourth] = doc.samples;
  assert.deepEqual(
    doc.samples.map((s) => s.name),
    ["First.csv", "Missing.csv", "Third.csv", "Fourth.csv"],
  );
  const uuid = () => crypto.randomUUID().replaceAll("-", "");
  const cells = {
    id: uuid(),
    name: "Cells",
    sample_id: first.id,
    parent_id: null,
    kind: "rectangle",
    x: "X",
    y: "Y",
    bounds: [0, 5, 0, 5],
  };
  const positive = {
    id: uuid(),
    name: "Positive",
    sample_id: first.id,
    parent_id: cells.id,
    kind: "range",
    x: "X",
    bounds: [1, 3],
  };
  doc = await request(route + "/gates/batch", {
    revision: doc.revision,
    gates: [cells, positive],
  });
  doc = await request(route + "/gates/apply", {
    revision: doc.revision,
    source_sample_id: first.id,
    target_sample_ids: [third.id, fourth.id],
    replace: true,
  });
  const group = {
    id: uuid(),
    name: "Paired",
    sample_ids: [first.id, fourth.id],
  };
  doc = await request(route + "/groups", { revision: doc.revision, group });
  await main.evaluate(
    (id) => globalThis.cytoforgeDesktop.notifyWorkspaceChanged(id),
    workspaceId,
  );
  await expect
    .poll(async () =>
      Number(
        await main
          .locator(".statusbar button")
          .innerText()
          .then((s) => /Revision (\d+)/.exec(s)?.[1]),
      ),
    )
    .toBe(doc.revision);
  const history = await request(route + "/history");
  const view = {
    workspaceId,
    sampleId: first.id,
    gateId: positive.id,
    x: "X",
    y: "Y",
    mode: "contour",
    bounds: [-1, 12, -1, 12],
    bins: 72,
    graphOptions: { palette: "viridis", smooth: false },
    xTransform: { kind: "asinh", cofactor: 50 },
  };
  const planar = await open(view);
  const cloud = await open({
    ...view,
    mode: "3d",
    bounds: [-1, 12, -1, 12, -1, 101],
    groupId: group.id,
    threeD: {
      z: "Z",
      yaw: 1.2,
      pitch: -0.3,
      zoom: 1.4,
      pan: [0.3, -0.4],
      color_by: "X",
      size_by: "Y",
    },
  });
  const other = await open({
    ...view,
    sampleId: third.id,
    gateId: doc.gates.find(
      (g) => g.sample_id === third.id && g.name === "Positive",
    ).id,
    mode: "cdf",
    bounds: [-1, 12],
  });
  const otherBefore = await context(other);
  await planar
    .getByRole("button", { name: "Next matching sample", exact: true })
    .click();
  await expect
    .poll(async () => (await context(planar)).sampleId)
    .toBe(third.id);
  await ready(planar);
  assert.equal((await context(cloud)).sampleId, first.id);
  assert.deepEqual((await context(planar)).bounds, view.bounds);
  assert.equal((await context(planar)).xTransform.cofactor, 50);
  await planar.locator(".plot-navigation").click({ position: { x: 5, y: 5 } });
  await planar.keyboard.press("Control+PageDown");
  await expect
    .poll(async () => (await context(planar)).sampleId)
    .toBe(first.id);
  passed(
    "Independent matching-path stepping skips incompatible acquisitions; keyboard and copied bounds/scales work",
  );
  await planar
    .getByRole("button", { name: "Next matching sample", exact: true })
    .click({ modifiers: ["Shift"] });
  await expect
    .poll(async () => (await context(cloud)).sampleId)
    .toBe(fourth.id);
  await expect
    .poll(async () => (await context(planar)).sampleId)
    .toBe(fourth.id);
  await expect(main.getByLabel("Plot sample", { exact: true })).toHaveValue(
    fourth.id,
  );
  await ready(planar);
  await ready(cloud);
  assert.deepEqual(await context(other), otherBefore);
  const movedCloud = await context(cloud);
  assert.equal(movedCloud.threeD.yaw, 1.2);
  assert.deepEqual(movedCloud.threeD.pan, [0.3, -0.4]);
  assert.deepEqual(movedCloud.bounds, [-1, 12, -1, 12, -1, 101]);
  assert.equal(movedCloud.groupId, group.id);
  const counts = await request(route + "/samples/" + fourth.id + "/counts");
  assert.equal(counts.find((c) => c.id === movedCloud.gateId).count, 10);
  const parameters = new URLSearchParams({
    x: movedCloud.x,
    y: movedCloud.y,
    gate_id: movedCloud.gateId,
    mode: "3d",
    three_d: JSON.stringify(movedCloud.threeD),
    x_transform: JSON.stringify(movedCloud.xTransform),
    y_transform: JSON.stringify(movedCloud.yTransform),
    bounds: JSON.stringify(movedCloud.bounds),
    graph_options: JSON.stringify(movedCloud.graphOptions),
  });
  const sampleRoute = route + "/samples/" + fourth.id;
  const metadata = await request(sampleRoute + "/plot?" + parameters);
  assert.equal(metadata.displayed_count, 10);
  parameters.set("revision", String(doc.revision));
  parameters.set("data_key", metadata.data_key);
  const events = await main.evaluate(
    async (url) => {
      const { token } = await (await fetch("/api/bootstrap")).json();
      const response = await fetch("/api" + url, {
        headers: { "X-CytoForge-Token": token },
      });
      if (!response.ok) throw new Error(await response.text());
      const bytes = await response.arrayBuffer(),
        values = new DataView(bytes);
      return Array.from({ length: bytes.byteLength / 32 }, (_, i) => ({
        id: Number(values.getBigUint64(i * 32 + 20, true)),
        xyz: [0, 4, 8].map((offset) =>
          values.getFloat32(i * 32 + offset, true),
        ),
      }));
    },
    sampleRoute + "/plot3d/points?" + parameters,
  );
  const literalIds = [1, 2, 11, 12, 21, 22, 31, 32, 41, 42];
  assert.deepEqual(
    events.map((e) => e.id),
    literalIds,
  );
  for (const e of events) {
    const expectedX = (Math.asinh((e.id % 10) / 50) + 1) / 13;
    assert(Math.abs(e.xyz[0] - expectedX) < 1e-6);
    assert(Math.abs(e.xyz[1] - (Math.floor(e.id / 10) + 1) / 13) < 1e-6);
    assert(Math.abs(e.xyz[2] - (e.id + 0.03 + 1) / 102) < 1e-6);
  }
  assert.deepEqual(await request(route + "/history"), history);
  assert.equal((await request(route)).revision, doc.revision);
  passed(
    "Shift-click moves main and both native views to one common cohort target; other samples stay independent; 10 literal event IDs and copied XYZ transforms match the target acquisition",
  );
  // A real unsaved gate dialog in one peer must block the entire synchronized move.
  await planar
    .getByRole("button", { name: "Rectangle gate", exact: true })
    .click();
  const box = await planar.locator(".primary-plot canvas").boundingBox();
  assert(box);
  await planar.mouse.move(box.x + 90, box.y + 50);
  await planar.mouse.down();
  await planar.mouse.move(box.x + 240, box.y + 170, { steps: 5 });
  await planar.mouse.up();
  await expect(planar.getByRole("dialog")).toBeVisible();
  const dirtyBefore = await context(cloud);
  await cloud
    .getByRole("button", { name: "Previous matching sample", exact: true })
    .click({ modifiers: ["Shift"] });
  await expect(
    cloud.getByText(
      "Save or cancel gate edits in the affected plot windows before navigating",
      { exact: false },
    ),
  ).toBeVisible();
  assert.deepEqual(await context(cloud), dirtyBefore);
  assert.equal((await context(planar)).sampleId, fourth.id);
  await planar.getByRole("button", { name: "Cancel", exact: true }).click();
  passed(
    "Actual unsaved gate draft blocks the entire coordinated move without discarding edits",
  );
  // Delay a valid native plan and change a peer while it is in flight.
  await application.evaluate(() => {
    globalThis.navigationOriginalFetch = globalThis.fetch;
    globalThis.navigationPaused = false;
    globalThis.fetch = async (...args) => {
      const response = await globalThis.navigationOriginalFetch(...args);
      if (String(args[0]).endsWith("/plot-navigation/plan")) {
        globalThis.navigationPaused = true;
        await new Promise((resolve) => {
          globalThis.navigationRelease = resolve;
        });
      }
      return response;
    };
  });
  const pendingMove = cloud.evaluate(async () => {
    try {
      await globalThis.cytoforgeDesktop.navigatePlot({
        direction: "previous",
        sync: true,
      });
      return "unexpected success";
    } catch (error) {
      return error.message;
    }
  });
  await expect
    .poll(() => application.evaluate(() => globalThis.navigationPaused))
    .toBe(true);
  await planar.getByLabel("X axis channel", { exact: true }).selectOption("Y");
  await expect.poll(async () => (await context(planar)).x).toBe("Y");
  await application.evaluate(() => {
    globalThis.fetch = globalThis.navigationOriginalFetch;
    globalThis.navigationRelease();
  });
  assert.match(await pendingMove, /changed while navigating/);
  assert.equal((await context(cloud)).sampleId, fourth.id);
  assert.equal((await context(planar)).sampleId, fourth.id);
  passed(
    "Concurrent view change rejects an in-flight move across all participating native windows",
  );
  await planar
    .getByRole("button", { name: "Parent population", exact: true })
    .click();
  await expect(
    planar.getByLabel("Y axis channel", { exact: true }),
  ).toHaveCount(0);
  const parent = await context(planar);
  assert.equal(
    parent.gateId,
    doc.gates.find((g) => g.sample_id === fourth.id && g.name === "Cells").id,
  );
  assert.equal(parent.mode, "histogram");
  assert.equal(parent.x, "X");
  assert.equal(parent.bounds, null);
  assert.equal((await context(cloud)).gateId, movedCloud.gateId);
  passed(
    "Parent navigation shows the defining population and its actual gate coordinate scale without retargeting peers",
  );
  doc = await request(
    route + "/groups/" + group.id + "?revision=" + doc.revision,
    undefined,
    "DELETE",
  );
  await expect(
    cloud.getByLabel("Plot sample group", { exact: true }),
  ).toHaveValue(group.id);
  await expect(
    cloud.getByText("Restore or choose a group", { exact: true }),
  ).toBeVisible();
  await expect(
    cloud.getByRole("button", {
      name: "Previous matching sample",
      exact: true,
    }),
  ).toBeDisabled();
  assert.equal((await context(cloud)).groupId, group.id);
  doc = await request(route + "/undo", { revision: doc.revision });
  await expect(
    cloud.getByText("Restore or choose a group", { exact: true }),
  ).toHaveCount(0);
  await expect(
    cloud.getByRole("button", {
      name: "Previous matching sample",
      exact: true,
    }),
  ).toBeEnabled();
  await cloud.getByLabel("Plot sample", { exact: true }).selectOption(first.id);
  await expect.poll(async () => (await context(cloud)).sampleId).toBe(first.id);
  await ready(cloud);
  passed(
    "Removed group remains an explicit unavailable reference; undo restores it and direct cohort selection retains the 3D view",
  );
  const invalid = await cloud.evaluate(async () => {
    try {
      await globalThis.cytoforgeDesktop.navigatePlot({
        direction: "next",
        sync: true,
        workspaceId: "f".repeat(32),
      });
      return false;
    } catch {
      return true;
    }
  });
  assert(invalid);
  assert.equal((await context(cloud)).sampleId, first.id);
  passed(
    "Native IPC rejects malformed and foreign-workspace navigation requests",
  );
  const expected = await Promise.all([planar, cloud, other].map(context));
  await quit();
  const settings = JSON.parse(
    readFileSync(path.join(profile, ".config/plot-windows.json"), "utf8"),
  );
  assert.equal(settings.windows.length, 3);
  await launch();
  await expect.poll(async () => (await application.windows()).length).toBe(4);
  const restored = (await application.windows()).filter((page) =>
    page.url().includes("plotWindow="),
  );
  for (const page of restored) {
    await page.setViewportSize({ width: 1540, height: 1050 });
    await ready(page);
    const value = await context(page);
    assert.deepEqual(
      value,
      expected.find((v) => v.id === value.id),
    );
  }
  assert.deepEqual(errors, []);
  passed(
    "Desktop restart restores population source, cohort, display transforms, bounds and independent 3D camera",
  );
  evidence.status = "passed";
  evidence.workspace = workspaceId;
  evidence.revision = doc.revision;
  evidence.literal_population_count = 10;
  evidence.literal_event_ids = literalIds;
  save();
  console.log(
    JSON.stringify({
      status: evidence.status,
      checks: evidence.checks.length,
      evidence: output,
    }),
  );
  await quit();
} catch (error) {
  evidence.status = "failed";
  evidence.error = String(error.stack || error);
  evidence.renderer_errors = errors;
  save();
  throw error;
} finally {
  clearTimeout(timeout);
  if (desktopProcess?.exitCode === null) desktopProcess.kill();
}
