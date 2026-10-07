// Exercise actual native windows and literal CSV populations, never the user's profile.
import assert from "node:assert/strict";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { _electron as electron, expect } from "playwright/test";

const root = process.cwd(),
  profile = path.join(root, ".tmp/desktop-population-history-" + Date.now());
const output =
  process.env.CYTOFORGE_POPULATION_HISTORY_EVIDENCE ||
  "artifacts/desktop-population-history-source.json";
const binary = process.env.CYTOFORGE_TEST_BINARY;
const evidence = {
  status: "running",
  binary: binary || "development Electron",
  profile,
  checks: [],
};
mkdirSync(profile, { recursive: true });
const save = () => writeFileSync(output, JSON.stringify(evidence, null, 2));
const passed = (s) => {
  evidence.checks.push(s);
  save();
};
save();
let application, main, desktopProcess;
const errors = [];
const timeout = setTimeout(() => desktopProcess?.kill(), 300000);
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
  main.on("pageerror", (e) => errors.push(e.message));
  await main.setViewportSize({ width: 1540, height: 1100 });
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
const session = (page, workspaceId) =>
  page.evaluate(
    (id) => globalThis.cytoforgeDesktop.getPlotSession(id),
    workspaceId,
  );
const state = async (page) => (await session(page)).state;
const ready = async (page) => {
  await page
    .locator('canvas[data-ready="true"]')
    .first()
    .waitFor({ timeout: 30000 });
  await expect(page.locator(".busy-indicator")).toHaveCount(0);
};
const display = (v) => ({
  sampleId: v.sampleId,
  gateId: v.gateId,
  x: v.x,
  y: ["histogram", "cdf"].includes(v.mode) ? null : v.y,
  mode: v.mode,
  bounds: v.bounds ?? null,
  bins: v.bins ?? 160,
  palette: v.graphOptions?.palette ?? "ocean",
  smooth: v.graphOptions?.smooth ?? null,
  coordinateGateId: v.coordinateGateId ?? null,
  backgateId: v.backgateId ?? null,
  xTransform: v.xTransform
    ? { kind: v.xTransform.kind, cofactor: v.xTransform.cofactor ?? 150 }
    : null,
  threeD:
    v.mode === "3d"
      ? {
          z: v.threeD.z,
          yaw: v.threeD.yaw,
          pitch: v.threeD.pitch,
          zoom: v.threeD.zoom,
          pan: v.threeD.pan,
          color_by: v.threeD.color_by ?? null,
          size_by: v.threeD.size_by ?? null,
          all_events: v.threeD.all_events !== false,
        }
      : null,
});
async function open(from, view) {
  const pending = application.waitForEvent("window");
  await from.evaluate(
    (v) => globalThis.cytoforgeDesktop.openPlotWindow(v),
    view,
  );
  const page = await pending;
  page.on("pageerror", (e) => errors.push(e.message));
  await page.setViewportSize({ width: 1540, height: 1100 });
  await ready(page);
  return page;
}
async function navigate(page, direction, targetGateId) {
  await page.evaluate(
    (action) => globalThis.cytoforgeDesktop.navigatePlot(action),
    {
      direction,
      sync: false,
      ...(direction === "population" ? { targetGateId } : {}),
    },
  );
  await ready(page);
}
async function polygon(page, n = 3) {
  await page.getByRole("button", { name: "Polygon gate", exact: true }).click();
  const stage = page.locator(".primary-plot .plot-stage");
  await stage.scrollIntoViewIfNeeded();
  const box = await stage.boundingBox();
  assert(box);
  for (const [x, y] of [
    [110, 60],
    [260, 60],
    [185, 180],
  ].slice(0, n))
    await page.mouse.click(box.x + x, box.y + y);
  await expect.poll(async () => (await session(page)).dirty).toBe(true);
  await expect(page.locator(".draw-overlay circle")).toHaveCount(n);
  const after = await stage.boundingBox();
  assert(
    Math.abs(after.y - box.y) < 1,
    "Starting a gate must not move the plotting surface",
  );
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
    .fill("Population view memory");
  await main
    .getByRole("button", { name: "Create workspace", exact: true })
    .click();
  const files = ["First", "Second"].map((name, sampleIndex) => {
    const file = path.join(profile, name + ".csv");
    writeFileSync(
      file,
      [
        "X,Y,Z",
        ...Array.from(
          { length: 100 },
          (_, i) => `${i % 10},${Math.floor(i / 10)},${i + sampleIndex * 0.01}`,
        ),
      ].join("\n"),
    );
    return file;
  });
  const chooser = main.waitForEvent("filechooser");
  await main.getByRole("button", { name: "Import FCS", exact: true }).click();
  await (await chooser).setFiles(files);
  await main.getByRole("button", { name: "Continue", exact: true }).click();
  await ready(main);
  const workspaceId = await main.evaluate(() =>
    localStorage.getItem("cytoforge.workspace"),
  );
  const route = "/workspaces/" + workspaceId;
  let doc = await request(route);
  const [first, second] = doc.samples;
  const uuid = () => crypto.randomUUID().replaceAll("-", "");
  const cells = {
    id: uuid(),
    sample_id: first.id,
    parent_id: null,
    name: "Cells",
    kind: "rectangle",
    x: "X",
    y: "Y",
    bounds: [0, 5, 0, 5],
  };
  const positive = {
    id: uuid(),
    sample_id: first.id,
    parent_id: cells.id,
    name: "Positive",
    kind: "range",
    x: "X",
    bounds: [1, 3],
  };
  const negative = {
    ...positive,
    id: uuid(),
    name: "Negative",
    bounds: [3, 5],
  };
  const volume = {
    id: uuid(),
    sample_id: first.id,
    parent_id: cells.id,
    name: "Volume",
    kind: "hyperrectangle",
    dimensions: "XYZ".split("").map((channel, i) => ({
      channel,
      transform: { kind: "linear" },
      minimum: 0,
      maximum: i === 0 ? 3 : i === 1 ? 5 : 31,
    })),
  };
  volume.dimensions[0].minimum = 1;
  const nested = [];
  let parent = positive.id;
  for (let i = 0; i < 8; i++) {
    const g = {
      ...positive,
      id: uuid(),
      name: "Depth " + (i + 1),
      parent_id: parent,
    };
    nested.push(g);
    parent = g.id;
  }
  doc = await request(route + "/gates/batch", {
    revision: doc.revision,
    gates: [cells, positive, negative, volume, ...nested],
  });
  doc = await request(route + "/gates/apply", {
    revision: doc.revision,
    source_sample_id: first.id,
    target_sample_ids: [second.id],
    replace: true,
  });
  await main.evaluate(
    (id) => globalThis.cytoforgeDesktop.notifyWorkspaceChanged(id),
    workspaceId,
  );
  await expect(
    main.getByLabel("Open child population", { exact: true }),
  ).toBeEnabled();
  const scientificBefore = await request(route),
    historyBefore = await request(route + "/history");
  const base = {
    workspaceId,
    sampleId: first.id,
    gateId: cells.id,
    x: "X",
    y: "Y",
    mode: "contour",
    bounds: [-1, 11, -1, 11],
    graphOptions: { palette: "viridis", smooth: false },
    bins: 72,
  };
  const planar = await open(main, base);
  const cloud = await open(main, {
    ...base,
    mode: "3d",
    bounds: [-1, 11, -1, 11, -1, 101],
    threeD: {
      z: "Z",
      yaw: 1.2,
      pitch: -0.3,
      zoom: 1.4,
      pan: [0.3, -0.4],
      color_by: "X",
      size_by: "Y",
      all_events: true,
    },
  });
  const rootPlanar = display(await state(planar)),
    rootCloud = display(await state(cloud));
  await planar
    .getByLabel("Open child population", { exact: true })
    .selectOption(positive.id);
  await ready(planar);
  assert.equal((await state(planar)).gateId, positive.id);
  await planar.getByRole("button", { name: "CDF", exact: true }).click();
  await ready(planar);
  await planar.getByLabel("X axis channel", { exact: true }).selectOption("Z");
  await ready(planar);
  await planar
    .getByRole("button", { name: "Zoom into region", exact: true })
    .click();
  const zoomStage = planar.locator(".primary-plot .plot-stage");
  await zoomStage.scrollIntoViewIfNeeded();
  const zoomBox = await zoomStage.boundingBox();
  assert(zoomBox);
  await planar.mouse.move(zoomBox.x + 110, zoomBox.y + 60);
  await planar.mouse.down();
  await planar.mouse.move(zoomBox.x + 400, zoomBox.y + 170, { steps: 4 });
  await planar.mouse.up();
  await expect
    .poll(async () => (await state(planar)).bounds !== null)
    .toBe(true);
  await ready(planar);
  const positiveView = display(await state(planar));
  await planar
    .getByRole("button", { name: "View population Cells", exact: true })
    .click();
  await ready(planar);
  assert.deepEqual(display(await state(planar)), rootPlanar);
  await planar
    .locator(".population-breadcrumbs")
    .click({ position: { x: 4, y: 4 } });
  await planar.keyboard.press("Control+o");
  await ready(planar);
  assert.deepEqual(display(await state(planar)), positiveView);
  await planar
    .getByRole("button", { name: "Next sibling population", exact: true })
    .click();
  await ready(planar);
  assert.equal((await state(planar)).gateId, negative.id);
  await planar
    .getByRole("button", { name: "Previous sibling population", exact: true })
    .click();
  await ready(planar);
  assert.deepEqual(display(await state(planar)), positiveView);
  await expect(
    planar.getByRole("button", {
      name: "Previous sibling population",
      exact: true,
    }),
  ).toBeDisabled();
  assert.deepEqual(display(await state(cloud)), rootCloud);
  passed(
    "Breadcrumbs, first-child shortcut and ordered siblings restore per-population CDF axes and two-axis zoom; another window keeps its independent 3D camera",
  );

  const duplicate = await open(planar, await state(planar));
  await navigate(duplicate, "parent");
  assert.deepEqual(display(await state(duplicate)), rootPlanar);
  await duplicate
    .getByLabel("X axis channel", { exact: true })
    .selectOption("Y");
  await ready(duplicate);
  const duplicateRoot = display(await state(duplicate));
  await navigate(duplicate, "population", positive.id);
  assert.deepEqual(display(await state(duplicate)), positiveView);
  await navigate(duplicate, "parent");
  assert.deepEqual(display(await state(duplicate)), duplicateRoot);
  await navigate(planar, "parent");
  assert.deepEqual(display(await state(planar)), rootPlanar);
  await navigate(planar, "population", positive.id);
  passed(
    "Duplicating a native plot copies its bounded population history; subsequent view edits do not alter the original window",
  );

  await navigate(cloud, "population", volume.id);
  await ready(cloud);
  assert.equal((await state(cloud)).mode, "3d");
  const literalIds = [1, 2, 11, 12, 21, 22];
  const counts = await request(route + "/samples/" + first.id + "/counts");
  assert.equal(counts.find((v) => v.id === cells.id).count, 25);
  assert.equal(counts.find((v) => v.id === positive.id).count, 10);
  assert.equal(counts.find((v) => v.id === negative.id).count, 10);
  assert.equal(counts.find((v) => v.id === volume.id).count, 6);
  const selectedVolume = await state(cloud);
  const parameters = new URLSearchParams({
    x: "X",
    y: "Y",
    gate_id: volume.id,
    coordinate_gate_id: volume.id,
    mode: "3d",
    three_d: JSON.stringify(selectedVolume.threeD),
    graph_options: JSON.stringify(selectedVolume.graphOptions),
  });
  const sampleRoute = route + "/samples/" + first.id;
  const metadata = await request(sampleRoute + "/plot?" + parameters);
  assert.equal(metadata.count, 6);
  parameters.set("revision", String(doc.revision));
  parameters.set("data_key", metadata.data_key);
  const ids = await main.evaluate(
    async (url) => {
      const { token } = await (await fetch("/api/bootstrap")).json();
      const response = await fetch("/api" + url, {
        headers: { "X-CytoForge-Token": token },
      });
      if (!response.ok) throw new Error(await response.text());
      const bytes = await response.arrayBuffer(),
        values = new DataView(bytes);
      return Array.from({ length: bytes.byteLength / 32 }, (_, i) =>
        Number(values.getBigUint64(i * 32 + 20, true)),
      );
    },
    sampleRoute + "/plot3d/points?" + parameters,
  );
  assert.deepEqual(ids, literalIds);
  evidence.literal_event_ids = ids;
  await cloud.locator(".three-d-stage").focus();
  await cloud.keyboard.press("ArrowLeft");
  await expect
    .poll(async () => (await state(cloud)).threeD.yaw)
    .not.toBe(rootCloud.threeD.yaw);
  const volumeView = display(await state(cloud));
  await navigate(cloud, "parent");
  assert.deepEqual(display(await state(cloud)), rootCloud);
  await navigate(cloud, "population", volume.id);
  assert.deepEqual(display(await state(cloud)), volumeView);
  passed(
    "First visit uses actual XYZ volume coordinates; 25/10/10/6 counts and all six literal event IDs agree with the CSV; each population restores its own camera",
  );

  await navigate(planar, "population", nested.at(-1).id);
  await expect(
    planar.getByLabel("Earlier population ancestors", { exact: true }),
  ).toBeVisible();
  await expect(
    planar.locator(
      '.population-breadcrumbs button[aria-label^="View population "]',
    ),
  ).toHaveCount(6);
  await planar
    .getByLabel("Earlier population ancestors", { exact: true })
    .selectOption(cells.id);
  await ready(planar);
  assert.deepEqual(display(await state(planar)), rootPlanar);
  assert.deepEqual(await request(route), scientificBefore);
  assert.deepEqual(await request(route + "/history"), historyBefore);
  passed(
    "Deep gating trees keep all ancestors accessible without overflowing the breadcrumb bar; view navigation does not change science or undo history",
  );

  await polygon(planar, 3);
  await expect(
    planar.getByLabel("X axis channel", { exact: true }),
  ).toBeDisabled();
  await expect(
    planar.getByRole("button", { name: "View all events", exact: true }),
  ).toBeDisabled();
  const peersBefore = await Promise.all(
    [main, planar, cloud, duplicate].map(state),
  );
  const refused = await cloud.evaluate(async () => {
    try {
      await globalThis.cytoforgeDesktop.navigatePlot({
        direction: "next",
        sync: true,
      });
      return false;
    } catch (e) {
      return e.message;
    }
  });
  assert.match(refused, /Save or cancel gate edits/);
  assert.deepEqual(
    await Promise.all([main, planar, cloud, duplicate].map(state)),
    peersBefore,
  );
  const planarId = await planar.evaluate(() =>
    globalThis.cytoforgeDesktop.getPlotWindow().then((v) => v.id),
  );
  await application.evaluate(({ BrowserWindow, dialog }, id) => {
    globalThis.historyOriginalMessageBox = dialog.showMessageBox;
    globalThis.historyCloseMessages = [];
    dialog.showMessageBox = async (_parent, options) => {
      globalThis.historyCloseMessages.push(options.message);
      return { response: 0 };
    };
    BrowserWindow.getAllWindows()
      .find((w) => w.webContents.getURL().includes("plotWindow=" + id))
      .close();
  }, planarId);
  await expect
    .poll(() =>
      application.evaluate(() => globalThis.historyCloseMessages.length),
    )
    .toBe(1);
  assert(!planar.isClosed());
  await application.evaluate(({ dialog }) => {
    dialog.showMessageBox = globalThis.historyOriginalMessageBox;
  });
  await planar
    .getByRole("button", { name: "Finish polygon", exact: true })
    .click();
  await expect(planar.getByRole("dialog")).toBeVisible();
  assert((await session(planar)).dirty);
  await planar.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect.poll(async () => (await session(planar)).dirty).toBe(false);
  passed(
    "An unfinished polygon blocks local view changes, all coordinated moves and native close; finishing transfers protection to the gate dialog without a gap",
  );

  await polygon(planar, 3);
  doc = await request(
    route + "/gates/" + cells.id + "?revision=" + doc.revision,
    undefined,
    "DELETE",
  );
  await expect(
    planar.getByText(
      /Workspace or plot coordinates changed\. This drawing is retained/,
    ),
  ).toBeVisible();
  await expect(planar.locator(".draw-overlay circle")).toHaveCount(3);
  await expect(
    planar.getByRole("button", { name: "Finish polygon", exact: true }),
  ).toBeDisabled();
  assert((await session(planar)).dirty);
  const staleDoc = await request(route),
    staleHistory = await request(route + "/history");
  await planar.locator(".primary-plot .plot-stage").focus();
  await planar.keyboard.press("Enter");
  await expect(planar.getByRole("dialog")).toHaveCount(0);
  assert.deepEqual(await request(route), staleDoc);
  assert.deepEqual(await request(route + "/history"), staleHistory);
  await planar
    .getByRole("button", { name: "Cancel unfinished drawing", exact: true })
    .click();
  await expect.poll(async () => (await session(planar)).dirty).toBe(false);
  doc = await request(route + "/undo", { revision: doc.revision });
  await ready(planar);
  await ready(cloud);
  passed(
    "Removing the population during a polygon retains every vertex and blocks stale completion; explicit cancellation and undo recover the original source",
  );

  await cloud
    .getByRole("button", { name: "3D bounds / box gate", exact: true })
    .click();
  await cloud.getByLabel("3D X minimum", { exact: true }).fill("-0.5");
  await expect.poll(async () => (await session(cloud)).dirty).toBe(true);
  await expect(
    cloud.getByLabel("Z axis channel", { exact: true }),
  ).toBeDisabled();
  await expect(
    cloud.getByLabel("3D compensation", { exact: true }),
  ).toBeDisabled();
  doc = await request(route + "/groups", {
    revision: doc.revision,
    group: {
      id: uuid(),
      name: "Revision check",
      sample_ids: [first.id, second.id],
    },
  });
  await expect(
    cloud.getByText(
      /Workspace or plot coordinates changed\. Your bounds are retained/,
    ),
  ).toBeVisible();
  await expect(cloud.getByLabel("3D X minimum", { exact: true })).toHaveValue(
    "-0.5",
  );
  await expect(
    cloud.getByRole("button", { name: "Create 3D box gate", exact: true }),
  ).toBeDisabled();
  assert((await session(cloud)).dirty);
  await cloud
    .getByRole("button", { name: "Cancel unfinished drawing", exact: true })
    .click();
  await ready(cloud);
  await expect.poll(async () => (await session(cloud)).dirty).toBe(false);
  passed(
    "3D bounds drafts protect navigation and axes, retain typed values through a workspace revision, and reject stale volume-gate creation",
  );

  const secondCells = doc.gates.find(
    (g) => g.sample_id === second.id && g.name === "Cells",
  );
  const removedSource = await open(main, {
    ...base,
    sampleId: second.id,
    gateId: secondCells.id,
  });
  await polygon(removedSource, 1);
  doc = await request(
    route + "/samples/" + second.id + "?revision=" + doc.revision,
    undefined,
    "DELETE",
  );
  await expect(
    removedSource.getByText(
      /Workspace or plot coordinates changed\. This drawing is retained/,
    ),
  ).toBeVisible();
  await expect(removedSource.locator(".draw-overlay circle")).toHaveCount(1);
  assert((await session(removedSource)).dirty);
  await removedSource
    .getByRole("button", { name: "Cancel unfinished drawing", exact: true })
    .click();
  doc = await request(route + "/undo", { revision: doc.revision });
  await ready(removedSource);
  await polygon(removedSource, 1);
  await removedSource.locator(".primary-plot .plot-stage").focus();
  await removedSource.keyboard.press("Escape");
  await expect
    .poll(async () => (await session(removedSource)).dirty)
    .toBe(false);
  await removedSource.evaluate(() =>
    globalThis.cytoforgeDesktop.closePlotWindow(),
  );
  passed(
    "Removing a sample cannot discard a partial polygon; undo restores the pinned sample and Escape deliberately cancels a fresh drawing",
  );

  const invalid = await cloud.evaluate(async () => {
    const foreign = await globalThis.cytoforgeDesktop.getPlotSession(
      "f".repeat(32),
    );
    try {
      await globalThis.cytoforgeDesktop.navigatePlot({
        direction: "population",
        sync: false,
        targetGateId: null,
        rememberedViews: [],
      });
      return false;
    } catch {
      return foreign === null;
    }
  });
  assert(invalid);
  await main
    .getByLabel("Open child population", { exact: true })
    .selectOption(cells.id);
  await ready(main);
  await main.getByRole("button", { name: "3D", exact: true }).click();
  await ready(main);
  await main.locator(".three-d-stage").focus();
  await main.keyboard.press("ArrowRight");
  await main
    .getByLabel("3D color parameter", { exact: true })
    .selectOption("Z");
  await ready(main);
  const mainView = display(await state(main));
  const another = await request("/workspaces", {
    name: "Switching view recovery",
  });
  await main.evaluate(() =>
    globalThis.cytoforgeDesktop.notifyWorkspaceChanged("f".repeat(32)),
  );
  await main.reload();
  await ready(main);
  assert.deepEqual(display(await state(main)), mainView);
  await main
    .getByLabel("Active workspace", { exact: true })
    .selectOption(another.id);
  await expect(
    main.getByRole("heading", {
      name: "Bring your experiment to life",
      exact: true,
    }),
  ).toBeVisible();
  await main
    .getByLabel("Active workspace", { exact: true })
    .selectOption(workspaceId);
  await ready(main);
  assert.deepEqual(display(await state(main)), mainView);
  passed(
    "Popup IPC cannot access another workspace or inject remembered descriptors; the main desktop restores its exact plot after reload and workspace switching",
  );

  const beforePanelRace = await state(main);
  await application.evaluate(() => {
    globalThis.populationOriginalFetch = globalThis.fetch;
    globalThis.populationPlanPaused = false;
    globalThis.fetch = async (...args) => {
      const response = await globalThis.populationOriginalFetch(...args);
      if (String(args[0]).endsWith("/plot-navigation/plan")) {
        globalThis.populationPlanPaused = true;
        await new Promise((resolve) => {
          globalThis.populationPlanRelease = resolve;
        });
      }
      return response;
    };
  });
  const panelMove = main.evaluate(async (id) => {
    try {
      await globalThis.cytoforgeDesktop.navigatePlot({
        direction: "population",
        sync: false,
        targetGateId: id,
      });
      return "unexpected success";
    } catch (error) {
      return error.message;
    }
  }, positive.id);
  await expect
    .poll(() => application.evaluate(() => globalThis.populationPlanPaused))
    .toBe(true);
  await main.getByRole("button", { name: "Compensation", exact: true }).click();
  await expect.poll(async () => (await session(main)).active).toBe(false);
  await application.evaluate(() => globalThis.populationPlanRelease());
  assert.match(await panelMove, /changed while navigating/);
  await application.evaluate(() => {
    globalThis.fetch = globalThis.populationOriginalFetch;
  });
  await expect(
    main.getByRole("button", { name: "Compensation", exact: true }),
  ).toHaveAttribute("aria-current", "page");
  assert.deepEqual(await state(main), beforePanelRace);
  await main.getByRole("button", { name: "Analysis", exact: true }).click();
  await ready(main);
  passed(
    "Changing the main panel during an in-flight native population plan rejects the stale move and retains the selected panel and original plot",
  );

  const expectedMain = await state(main),
    expected = await Promise.all(
      [planar, cloud, duplicate].map(async (page) => ({
        id: await page.evaluate(() =>
          globalThis.cytoforgeDesktop.getPlotWindow().then((v) => v.id),
        ),
        state: await state(page),
      })),
    );
  await planar.screenshot({
    path: "artifacts/screenshots/desktop-population-breadcrumbs.png",
  });
  await quit();
  const saved = JSON.parse(
    readFileSync(path.join(profile, ".config/plot-windows.json"), "utf8"),
  );
  assert.equal(saved.version, 2);
  assert.equal(saved.windows.length, 3);
  assert(
    saved.main.views.length > 0 &&
      saved.windows.every((r) => r.views.length > 0 && r.views.length <= 96),
  );
  await launch();
  await ready(main);
  assert.deepEqual(await state(main), expectedMain);
  await expect.poll(async () => (await application.windows()).length).toBe(4);
  const recovered = (await application.windows()).filter((p) =>
    p.url().includes("plotWindow="),
  );
  for (const page of recovered) {
    await page.setViewportSize({ width: 1540, height: 1100 });
    await ready(page);
    const id = await page.evaluate(() =>
      globalThis.cytoforgeDesktop.getPlotWindow().then((v) => v.id),
    );
    assert.deepEqual(
      await state(page),
      expected.find((v) => v.id === id).state,
    );
  }
  const restoredPlanar = recovered.find((p) => p.url().includes(planarId));
  await navigate(restoredPlanar, "population", positive.id);
  assert.deepEqual(display(await state(restoredPlanar)), positiveView);
  await navigate(restoredPlanar, "parent");
  assert.deepEqual(display(await state(restoredPlanar)), rootPlanar);
  assert.deepEqual(errors, []);
  passed(
    "Normal desktop exit and restart recover the main plot, independent popup views and each popup’s population history across a changed private engine port",
  );
  evidence.status = "passed";
  evidence.workspace = workspaceId;
  evidence.revision = doc.revision;
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
