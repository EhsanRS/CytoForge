// Real native 3D windows, full-event streaming, volume gates and restart recovery.
import assert from "node:assert/strict";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import path from "node:path";
import { _electron as electron, expect } from "playwright/test";

const root = process.cwd(),
  profile = path.join(root, ".tmp/desktop-3d-" + Date.now());
const evidencePath =
  process.env.CYTOFORGE_3D_EVIDENCE ||
  path.join(root, "artifacts/desktop-3d.json");
const binary = process.env.CYTOFORGE_TEST_BINARY;
mkdirSync(profile, { recursive: true });
mkdirSync(path.join(root, "artifacts/screenshots"), { recursive: true });
const evidence = {
  status: "running",
  binary: binary || "development Electron",
  profile,
  checks: [],
};
writeFileSync(evidencePath, JSON.stringify(evidence));
let application, main, desktopProcess;
const errors = [];
const deadline = setTimeout(() => desktopProcess?.kill(), 240000);
async function launch() {
  application = await electron.launch({
    executablePath: binary,
    args: [
      ...(binary ? [] : ["."]),
      "--headless",
      "--ozone-platform=headless",
      ...(process.env.CYTOFORGE_3D_GPU === "1" ? [] : ["--disable-gpu"]),
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
const ready = (page) =>
  page
    .locator('.three-d-overlay[data-ready="true"]')
    .waitFor({ timeout: 30000 });
const context = (page) =>
  page.evaluate(() => globalThis.cytoforgeDesktop.getPlotWindow());
async function newWindow(page = main) {
  const created = application
    .waitForEvent("window")
    .catch((error) => ({ error }));
  await page
    .getByRole("button", { name: "Open plot in new window", exact: true })
    .click();
  const child = await created;
  if (child.error) {
    const message = await page.locator(".toast").allTextContents();
    throw new Error(
      "Native 3D window did not open: " +
        message.join("; ") +
        "; " +
        child.error.message,
    );
  }
  child.on("pageerror", (error) => errors.push(error.message));
  await child.setViewportSize({ width: 1540, height: 1050 });
  await ready(child);
  return child;
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
async function importFile(file) {
  const chooser = main.waitForEvent("filechooser");
  await main.getByRole("button", { name: "Import FCS", exact: true }).click();
  await (await chooser).setFiles(file);
  await main.getByRole("button", { name: "Continue", exact: true }).click();
}
try {
  await launch();
  await main
    .getByRole("button", { name: "New experiment", exact: true })
    .click();
  await main
    .getByLabel("Experiment name", { exact: true })
    .fill("Native 3D volume truth");
  await main
    .getByRole("button", { name: "Create workspace", exact: true })
    .click();
  const rows = Array.from({ length: 27 }, (_, i) => [
    Math.floor(i / 9),
    Math.floor(i / 3) % 3,
    i % 3,
    i === 14 ? "nan" : i,
    i === 14 ? "nan" : i * 10,
  ]);
  const file = path.join(profile, "volume-truth.csv");
  writeFileSync(
    file,
    [
      "X,Y,Z,C,S",
      ...rows.map((r) => r.join(",")),
      "nan,0,0,0,0",
      "0,0,nan,0,0",
    ].join("\n"),
  );
  await importFile(file);
  await main.locator('canvas[data-ready="true"]').waitFor();
  await main.getByRole("button", { name: "3D", exact: true }).click();
  await main.getByLabel("X axis channel").selectOption("X");
  await main.getByLabel("Y axis channel").selectOption("Y");
  await main.getByLabel("Z axis channel").selectOption("Z");
  await ready(main);
  const workspaceId = await main.getByLabel("Active workspace").inputValue(),
    base = "/workspaces/" + workspaceId;
  let doc = await request(base);
  const sample = doc.samples[0];
  assert.equal(sample.event_count, 29);
  await expect(main.locator(".three-d-overlay")).toHaveAttribute(
    "data-population-count",
    "29",
  );
  await expect(main.locator(".three-d-overlay")).toHaveAttribute(
    "data-finite-count",
    "27",
  );
  await expect(main.locator(".three-d-overlay")).toHaveAttribute(
    "data-loaded-count",
    "27",
  );
  await main.getByLabel("3D color parameter").selectOption("C");
  await main.getByLabel("3D size parameter").selectOption("S");
  await ready(main);
  const child = await newWindow();
  const start = await context(child);
  assert.equal(start.mode, "3d");
  assert.equal(start.threeD.z, "Z");
  assert.equal(start.threeD.color_by, "C");
  assert.equal(start.threeD.size_by, "S");
  await child.getByLabel("3D point size", { exact: true }).focus();
  await child.getByLabel("3D point size", { exact: true }).press("Home");
  for (let i = 0; i < 7; i++)
    await child
      .getByLabel("3D point size", { exact: true })
      .press("ArrowRight");
  await expect
    .poll(async () => (await context(child)).threeD.point_size)
    .toBe(4);
  const geometryRequests = [];
  child.on("request", (request) => {
    if (
      request.url().includes("/plot?") ||
      request.url().includes("/plot3d/points?")
    )
      geometryRequests.push(request.url());
  });
  const stage = child.getByRole("application", { name: "Interactive 3D plot" });
  await stage.click();
  const stable = await context(child),
    requestCount = geometryRequests.length;
  await stage.press("ArrowRight");
  await stage.press("ArrowDown");
  await stage.press("+");
  await expect
    .poll(async () => (await context(child)).threeD.zoom)
    .toBeGreaterThan(1);
  const box = await stage.boundingBox();
  assert(box);
  const scrollBefore = await child
    .locator(".main-content")
    .evaluate((element) => element.scrollTop);
  const zoomBefore = (await context(child)).threeD.zoom;
  await child.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await child.mouse.wheel(0, -120);
  await expect
    .poll(async () => (await context(child)).threeD.zoom)
    .toBeGreaterThan(zoomBefore);
  assert.equal(
    await child
      .locator(".main-content")
      .evaluate((element) => element.scrollTop),
    scrollBefore,
    "Wheel zoom scrolled the desktop document",
  );
  await child.keyboard.down("Shift");
  await child.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
  await child.mouse.down();
  await child.mouse.move(box.x + box.width * 0.56, box.y + box.height * 0.54, {
    steps: 6,
  });
  await child.mouse.up();
  await child.keyboard.up("Shift");
  const pose = (await context(child)).threeD;
  assert.notEqual(pose.yaw, stable.threeD.yaw);
  assert.notEqual(pose.pitch, stable.threeD.pitch);
  assert(pose.pan.some((v) => Math.abs(v) > 0));
  assert.equal(
    geometryRequests.length,
    requestCount,
    "Camera motion fetched event data again",
  );
  assert.notDeepEqual(
    (await context(child)).threeD,
    (
      await newWindow(main).then(async (page) => {
        const state = await context(page);
        await page.evaluate(() =>
          globalThis.cytoforgeDesktop.closePlotWindow(),
        );
        return state;
      })
    ).threeD,
  );
  evidence.checks.push(
    "XYZ counts exclude only missing coordinates; color/size missingness keeps event markers",
    "Native cameras rotate, pan and zoom independently without fetching event data",
  );
  await child
    .getByRole("button", { name: "3D bounds / box gate", exact: true })
    .click();
  for (const axis of ["X", "Y", "Z"]) {
    await child.getByLabel(`3D ${axis} minimum`, { exact: true }).fill("0");
    await child.getByLabel(`3D ${axis} maximum`, { exact: true }).fill("2");
  }
  await child
    .getByRole("button", { name: "Create 3D box gate", exact: true })
    .click();
  await child
    .getByLabel("Population name", { exact: true })
    .fill("Genuine XYZ volume");
  await child
    .getByRole("button", { name: "Create population", exact: true })
    .click();
  await expect(main.locator(".gate-tree")).toContainText("Genuine XYZ volume");
  doc = await request(base);
  const gate = doc.gates.find((g) => g.name === "Genuine XYZ volume");
  assert(gate);
  assert.equal(gate.kind, "hyperrectangle");
  assert.equal(gate.dimensions.length, 3);
  const literalCount = rows.filter((r) =>
    r.slice(0, 3).every((v) => v >= 0 && v < 2),
  ).length;
  assert.equal(literalCount, 8);
  const counts = await request(`${base}/samples/${sample.id}/counts`);
  assert.equal(counts.find((c) => c.id === gate.id).count, literalCount);
  await child.getByLabel("Backgate population").selectOption(gate.id);
  await ready(child);
  const afterGate = await context(child);
  assert.equal(afterGate.backgateId, gate.id);
  const populationCreated = application.waitForEvent("window");
  await main
    .locator(".gate-tree .gate-row")
    .filter({ hasText: "Genuine XYZ volume" })
    .dblclick();
  const population = await populationCreated;
  population.on("pageerror", (error) => errors.push(error.message));
  await population.setViewportSize({ width: 1540, height: 1050 });
  await ready(population);
  await expect(population.locator(".three-d-overlay")).toHaveAttribute(
    "data-population-count",
    "8",
  );
  evidence.checks.push(
    "3D box gate matches eight literal source events and synchronizes to root and population windows",
  );
  await child
    .getByRole("button", { name: "3D bounds / box gate", exact: true })
    .click();
  for (const axis of ["X", "Y", "Z"]) {
    await child.getByLabel(`3D ${axis} minimum`, { exact: true }).fill("0");
    await child.getByLabel(`3D ${axis} maximum`, { exact: true }).fill("1");
  }
  await child
    .getByRole("button", { name: "Apply axis bounds", exact: true })
    .click();
  await ready(child);
  await expect(child.locator(".three-d-overlay")).toHaveAttribute(
    "data-loaded-count",
    "8",
  );
  assert.deepEqual((await context(child)).bounds, [0, 1, 0, 1, 0, 1]);
  const duplicate = await newWindow(child);
  assert.deepEqual(
    (await context(duplicate)).threeD,
    (await context(child)).threeD,
  );
  assert.deepEqual((await context(duplicate)).bounds, [0, 1, 0, 1, 0, 1]);
  await duplicate.getByLabel("Z axis channel").selectOption("C");
  await ready(duplicate);
  assert.equal((await context(child)).threeD.z, "Z");
  assert.equal((await context(duplicate)).bounds, null);
  await duplicate.getByLabel("Z axis channel").selectOption("Z");
  await ready(duplicate);
  const { id: duplicateId, ...availableState } = await context(duplicate);
  assert(duplicateId);
  await duplicate.evaluate(
    (state) =>
      globalThis.cytoforgeDesktop.updatePlotWindow(
        { ...state, threeD: { ...state.threeD, z: "Unavailable" } },
        false,
      ),
    availableState,
  );
  await duplicate.reload();
  await expect(duplicate.locator(".plot-input-unavailable")).toContainText(
    "A plot parameter is unavailable",
  );
  assert.equal((await context(duplicate)).threeD.z, "Unavailable");
  await duplicate
    .getByLabel("Z replacement channel", { exact: true })
    .selectOption("Z");
  await ready(duplicate);
  const { id: restoredId, ...restoredState } = await context(duplicate);
  assert(restoredId);
  await duplicate.evaluate(
    (state) =>
      globalThis.cytoforgeDesktop.updatePlotWindow(
        { ...state, threeD: { ...state.threeD, color_by: "Unavailable" } },
        false,
      ),
    restoredState,
  );
  await duplicate.reload();
  await expect(duplicate.locator(".plot-input-unavailable")).toContainText(
    "color or size parameter is unavailable",
  );
  assert.equal((await context(duplicate)).threeD.color_by, "Unavailable");
  await duplicate
    .getByLabel("3D color replacement parameter", { exact: true })
    .selectOption("");
  await ready(duplicate);
  await duplicate.getByRole("button", { name: "Density", exact: true }).click();
  await duplicate.locator('canvas[data-ready="true"]').waitFor();
  assert.equal((await context(duplicate)).threeD.z, "Z");
  await duplicate.getByRole("button", { name: "3D", exact: true }).click();
  await ready(duplicate);
  evidence.checks.push(
    "Unavailable Z/scalar references remain explicit and can be replaced; switching display modes retains the 3D view",
  );
  const valid = await context(child);
  const reject = async (patch) =>
    assert.equal(
      await main.evaluate(
        async (state) => {
          try {
            await globalThis.cytoforgeDesktop.openPlotWindow(state);
            return false;
          } catch {
            return true;
          }
        },
        {
          workspaceId,
          sampleId: sample.id,
          gateId: null,
          x: "X",
          y: "Y",
          mode: "3d",
          threeD: { z: "Z" },
          ...patch,
        },
      ),
      true,
    );
  await reject({ threeD: { z: "Unavailable" } });
  await reject({ threeD: { z: "Z", yaw: 100 } });
  await reject({ threeD: { z: "Z", opacity: 0 } });
  await reject({ threeD: { z: "Z", executable: "/tmp/not-allowed" } });
  await reject({ bounds: [0, 1, 0, 1] });
  evidence.checks.push(
    "Six-axis limits and full camera/settings duplicate independently; malformed native states rejected",
  );
  const image = path.join(profile, "cloud.png");
  await application.evaluate(({ session }, filename) => {
    globalThis.cytoforge3DDownload = "waiting";
    session.defaultSession.once("will-download", (_event, item) => {
      item.setSavePath(filename);
      item.once("done", (_event, state) => {
        globalThis.cytoforge3DDownload = state;
      });
    });
  }, image);
  await child
    .getByRole("button", { name: "Download 3D plot image", exact: true })
    .click();
  await expect
    .poll(() => application.evaluate(() => globalThis.cytoforge3DDownload))
    .toBe("completed");
  const bytes = readFileSync(image);
  assert.equal(bytes.subarray(1, 4).toString(), "PNG");
  assert(bytes.length > 1000);
  evidence.png = {
    path: image,
    bytes: bytes.length,
    sha256: createHash("sha256").update(bytes).digest("hex"),
  };
  await child.screenshot({
    path: path.join(root, "artifacts/screenshots/desktop-3d-cloud.png"),
  });
  evidence.renderer = await child
    .locator(".three-d-overlay")
    .getAttribute("data-renderer");
  evidence.checks.push(
    "Native PNG export composites event cloud, cube, axis labels and gate wires",
  );
  const savedStates = [];
  for (const page of [child, population, duplicate])
    savedStates.push(await context(page));
  await closeDesktop();
  await launch();
  await expect.poll(async () => application.windows().length).toBe(4);
  for (const state of savedStates) {
    const page = application
      .windows()
      .find((p) => p.url().includes("plotWindow=" + state.id));
    assert(page);
    page.on("pageerror", (error) => errors.push(error.message));
    await page.setViewportSize({ width: 1540, height: 1050 });
    await ready(page);
    const restored = await context(page);
    assert.deepEqual(restored.threeD, state.threeD);
    assert.deepEqual(restored.bounds, state.bounds);
    assert.equal(restored.backgateId, state.backgateId);
  }
  evidence.checks.push(
    "All native 3D windows restore XYZ, camera, six bounds, settings and backgate after restart",
  );
  // More than one binary chunk reaches the actual renderer with no implicit marker cap.
  const largeFile = path.join(profile, "full-stream.csv"),
    total = 65536 + 17;
  writeFileSync(
    largeFile,
    [
      "X,Y,Z",
      ...Array.from(
        { length: total },
        (_, i) => `${i % 31},${i % 37},${i % 41}`,
      ),
    ].join("\n"),
  );
  await importFile(largeFile);
  await main.locator('canvas[data-ready="true"]').waitFor();
  await main
    .locator(".sidebar-sample")
    .filter({ hasText: "full-stream.csv" })
    .click();
  await main.getByRole("button", { name: "3D", exact: true }).click();
  await main.getByLabel("X axis channel").selectOption("X");
  await main.getByLabel("Y axis channel").selectOption("Y");
  await main.getByLabel("Z axis channel").selectOption("Z");
  for (const scalar of ["color", "size"]) {
    const replacement = main.getByLabel(`3D ${scalar} replacement parameter`, {
      exact: true,
    });
    if (await replacement.isVisible()) await replacement.selectOption("");
  }
  await main.getByLabel("3D color parameter").selectOption("");
  await main.getByLabel("3D size parameter").selectOption("");
  await ready(main);
  await expect(main.locator(".three-d-overlay")).toHaveAttribute(
    "data-loaded-count",
    String(total),
  );
  await expect(main.locator(".three-d-overlay")).toHaveAttribute(
    "data-displayed-count",
    String(total),
  );
  evidence.full_stream = {
    source_events: total,
    loaded_events: total,
    chunks: 2,
    implicit_sampling: false,
  };
  evidence.checks.push(
    "65,553 original events cross binary chunk boundary into native renderer without sampling",
  );
  await main.locator(".graph-settings summary").click();
  await main.getByLabel("Graph marker limit").selectOption("1000");
  await main.getByLabel("3D all events", { exact: true }).uncheck();
  await ready(main);
  await expect(main.locator(".three-d-overlay")).toHaveAttribute(
    "data-loaded-count",
    "1000",
  );
  await expect(main.locator(".three-d-overlay")).toHaveAttribute(
    "data-population-count",
    String(total),
  );
  await main.getByLabel("3D all events", { exact: true }).check();
  await ready(main);
  await expect(main.locator(".three-d-overlay")).toHaveAttribute(
    "data-loaded-count",
    String(total),
  );
  evidence.checks.push(
    "Explicit sampled display respects marker limit while retaining full population counts; all-event switch restores every event",
  );
  if (process.env.CYTOFORGE_3D_GPU === "1")
    assert.equal(
      evidence.renderer,
      "webgl2",
      "GPU verification requires an actual WebGL2 renderer",
    );
  assert.deepEqual(errors, []);
  await closeDesktop();
  evidence.status = "passed";
  evidence.window_state = valid;
  evidence.volume_gate = {
    kind: gate.kind,
    dimensions: gate.dimensions,
    literal_count: literalCount,
  };
  evidence.page_errors = errors;
  writeFileSync(evidencePath, JSON.stringify(evidence, null, 2) + "\n");
  console.log(
    JSON.stringify({
      status: evidence.status,
      renderer: evidence.renderer,
      checks: evidence.checks.length,
      full_stream: evidence.full_stream,
    }),
  );
} catch (error) {
  evidence.status = "failed";
  evidence.error = error.stack;
  evidence.page_errors = errors;
  writeFileSync(evidencePath, JSON.stringify(evidence, null, 2) + "\n");
  throw error;
} finally {
  clearTimeout(deadline);
  if (desktopProcess?.exitCode === null)
    await application.close().catch(() => desktopProcess.kill());
}
