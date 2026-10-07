// Exercise the remote viewer while observing the real desktop through local CDP.
// UI interactions go through the viewer's mouse/keyboard/file-transfer bridge.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync, statSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { chromium } from "playwright";
import { PDFDocument, PDFName, PDFDict } from "pdf-lib";
import path from "node:path";
import { previewConfiguration } from "./desktop-preview/config.mjs";

const selected = previewConfiguration(process.cwd());
const session = JSON.parse(readFileSync(selected.sessionPath));
assert.equal(session.status, "running");
assert.equal(session.packaged, true);
const confirmed = previewConfiguration(process.cwd(), {
  ...process.env,
  ...(selected.name ? { CYTOFORGE_PREVIEW_BINARY: session.binary_path } : {}),
});
assert.equal(session.preview_name || "", confirmed.name);
if (session.runtime)
  assert.equal(
    path.resolve(confirmed.root, session.runtime),
    confirmed.runtime,
  );
const base = new URL(session.url).origin;
const linkOrigin = createServer((_request, response) => {
  response.setHeader("Content-Type", "text/html");
  response.end(`<a href="${session.url}">Open desktop session</a>`);
});
await new Promise((resolve) => linkOrigin.listen(0, "127.0.0.1", resolve));
const debugPort = readFileSync(
  path.join(confirmed.runtime, ".cache/desktop-profile/DevToolsActivePort"),
  "utf8",
).split("\n")[0];
const desktop = await chromium.connectOverCDP(`http://127.0.0.1:${debugPort}`, {
  noDefaults: true,
});
const native = desktop
  .contexts()[0]
  .pages()
  .find(
    (page) =>
      page.url().startsWith("http://127.0.0.1:") &&
      !page.url().includes("plotWindow="),
  );
assert(native);
const browser = await chromium.launch({
  headless: true,
  args: ["--disable-dev-shm-usage"],
});
const context = await browser.newContext({
  viewport: { width: 1280, height: 900 },
});
const viewer = await context.newPage();
const errors = [];
viewer.on("pageerror", (error) => errors.push(error.message));
const nativeInputs = [];
viewer.on("request", (request) => {
  if (request.url() === `${base}/input`)
    nativeInputs.push(JSON.parse(request.postData()));
});
const reportPath = path.join(
  confirmed.root,
  "artifacts",
  confirmed.name
    ? `desktop-preview-${confirmed.name}-validation.json`
    : "desktop-preview-validation.json",
);
writeFileSync(
  reportPath,
  JSON.stringify({ status: "running", desktop_pid: session.desktop_pid }),
);
async function waitFor(predicate, description, timeout = 15000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Timed out: ${description}`);
}
async function clickDesktop(locator, clickCount = 1) {
  const target = await locator.boundingBox();
  assert(target, "Desktop control must be visible");
  const screen = await viewer.locator("#desktop").boundingBox();
  await viewer.mouse.click(
    screen.x + ((target.x + target.width / 2) * screen.width) / 1540,
    screen.y + ((target.y + target.height / 2) * screen.height) / 990,
    { clickCount },
  );
}
async function navigation(label) {
  const button = native.getByRole("button", { name: label, exact: true });
  await clickDesktop(button);
  await waitFor(
    async () => (await button.getAttribute("aria-current")) === "page",
    `${label} navigation through the viewer`,
  );
}
let importedId = null;
let originalWorkspaceId,
  originalWorkspace,
  originalPlotState,
  fixtureWorkspaceId;
let workspaceRestored = false;
let ownedPlotWindow = null;
async function nativeRequest(route, body, method = body ? "POST" : "GET") {
  return native.evaluate(
    async ({ route, body, method }) => {
      const { token } = await (await fetch("/api/bootstrap")).json();
      const response = await fetch("/api" + route, {
        method,
        headers: {
          "Content-Type": "application/json",
          "X-CytoForge-Token": token,
        },
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!response.ok) throw new Error(await response.text());
      return response.json();
    },
    { route, body, method },
  );
}
async function selectWorkspace(id) {
  await native.evaluate(
    (id) => globalThis.cytoforgeDesktop.saveWorkspace(id),
    id,
  );
  await waitFor(
    async () =>
      (await native.evaluate(() =>
        globalThis.cytoforgeDesktop.getWorkspace(),
      )) === id,
    "Desktop workspace selection",
  );
  await native.reload();
  await native.waitForLoadState("networkidle");
}
try {
  assert.equal((await fetch(`${base}/frame.jpg`)).status, 401);
  assert.equal(
    (await fetch(`${base}/input`, { method: "POST", body: "[]" })).status,
    401,
  );
  await viewer.goto(`http://127.0.0.1:${linkOrigin.address().port}`);
  await viewer.getByText("Open desktop session", { exact: true }).click();
  await viewer
    .locator('#desktop[data-ready="true"]')
    .waitFor({ timeout: 20000 });
  await viewer
    .getByText("Connected to the packaged desktop app", { exact: true })
    .waitFor();
  const cookie = (await context.cookies()).find(
    (cookie) => cookie.name === "cytoforge_desktop_preview",
  );
  assert(cookie.httpOnly);
  assert.equal(cookie.sameSite, "Strict");
  assert.equal(viewer.url(), `${base}/`);
  assert.equal(await viewer.locator("#root").count(), 0);
  assert.equal(
    (await context.request.get(`${base}/api/workspaces`)).status(),
    404,
  );
  assert.equal(
    (await context.request.get(`${base}/@vite/client`)).status(),
    404,
  );
  assert.equal(
    (
      await context.request.post(`${base}/input`, {
        headers: { Origin: "http://untrusted.invalid" },
        data: [],
      })
    ).status(),
    403,
  );
  assert.equal(
    (
      await context.request.post(`${base}/input`, {
        headers: { Origin: base },
        data: [{ type: "move", x: -1, y: 20 }],
      })
    ).status(),
    400,
  );
  assert.equal(
    (
      await context.request.post(`${base}/input`, {
        headers: { Origin: base },
        data: [{ type: "evaluate", expression: "process.exit()" }],
      })
    ).status(),
    400,
  );

  // Fixture setup is private and local; viewer interactions use a new synthetic
  // workspace so no existing project or its undo history is changed by this test.
  originalWorkspaceId = await native.evaluate(() =>
    globalThis.cytoforgeDesktop.getWorkspace(),
  );
  originalWorkspace = originalWorkspaceId
    ? await nativeRequest("/workspaces/" + originalWorkspaceId)
    : null;
  const originalSession = await native.evaluate(() =>
    globalThis.cytoforgeDesktop.getPlotSession(),
  );
  assert(
    !originalSession?.dirty,
    "The user's desktop must have no unsaved drawing before validation",
  );
  originalPlotState = originalSession?.state ?? null;
  const fixture = await nativeRequest("/demo", {});
  fixtureWorkspaceId = fixture.id;
  await nativeRequest(
    "/workspaces/" + fixture.id,
    {
      revision: fixture.revision,
      name: "Desktop viewer validation " + new Date().toISOString(),
      description:
        "Owned synthetic viewer test fixture; existing workspaces are preserved.",
    },
    "PATCH",
  );
  await selectWorkspace(fixtureWorkspaceId);

  const windowsBefore = (
    await (await context.request.get(`${base}/state`)).json()
  ).windows;
  assert(Array.isArray(windowsBefore));
  const mainRecord = windowsBefore.find((item) => item.kind === "main");
  assert(mainRecord);
  assert.equal(
    (
      await context.request.post(`${base}/window`, {
        headers: { Origin: "http://untrusted.invalid" },
        data: { id: mainRecord.id },
      })
    ).status(),
    403,
  );
  assert.equal(
    (
      await context.request.post(`${base}/window`, {
        headers: { Origin: base },
        data: { id: 99999999 },
      })
    ).status(),
    400,
  );
  const popupCreated = desktop.contexts()[0].waitForEvent("page");
  await clickDesktop(native.locator(".sidebar-sample").first(), 2);
  ownedPlotWindow = await popupCreated;
  await ownedPlotWindow
    .locator('.plot-window canvas[data-ready="true"]')
    .waitFor();
  let plotRecord;
  await waitFor(async () => {
    const state = await (await context.request.get(`${base}/state`)).json();
    plotRecord = state.windows.find(
      (item) =>
        item.kind === "plot" &&
        !windowsBefore.some((old) => old.id === item.id),
    );
    return !!plotRecord;
  }, "New native plot window registered in the viewer");
  await viewer
    .getByLabel("Desktop window", { exact: true })
    .selectOption(String(plotRecord.id));
  await waitFor(
    async () =>
      (await (await context.request.get(`${base}/state`)).json())
        .selectedWindowId === plotRecord.id,
    "Viewer selects the native plot window",
  );
  await waitFor(
    async () =>
      (await viewer.locator("#desktop").getAttribute("data-window-id")) ===
        String(plotRecord.id) &&
      (await viewer.locator("#desktop").getAttribute("aria-busy")) !== "true",
    "Viewer displays pixels from the selected native window before accepting input",
  );
  const originalAxis = await native.getByLabel("X axis channel").inputValue();
  const replacementAxis = await ownedPlotWindow
    .getByLabel("X axis channel")
    .locator("option")
    .nth(1)
    .getAttribute("value");
  await clickDesktop(ownedPlotWindow.getByLabel("X axis channel"));
  await viewer.keyboard.press("Home");
  await viewer.keyboard.press("ArrowDown");
  await viewer.keyboard.press("Enter");
  await waitFor(
    async () =>
      (await ownedPlotWindow.getByLabel("X axis channel").inputValue()) ===
      replacementAxis,
    "Viewer input changes the selected plot window axis",
  );
  assert.equal(
    await native.getByLabel("X axis channel").inputValue(),
    originalAxis,
  );
  await clickDesktop(
    ownedPlotWindow.getByRole("button", { name: "Histogram", exact: true }),
  );
  await waitFor(
    async () =>
      (await ownedPlotWindow
        .getByRole("button", { name: "Histogram", exact: true })
        .getAttribute("aria-pressed")) === "true",
    "Viewer changes popup display mode",
  );
  assert.equal(
    await native
      .getByRole("button", { name: "Density", exact: true })
      .getAttribute("aria-pressed"),
    "true",
  );
  await ownedPlotWindow.locator('canvas[data-ready="true"]').waitFor();
  const frameNumber = Number(
    await viewer.locator("#desktop").getAttribute("data-frame-number"),
  );
  await waitFor(
    async () =>
      Number(
        await viewer.locator("#desktop").getAttribute("data-frame-number"),
      ) >=
      frameNumber + 2,
    "Viewer captures the completed histogram plot",
  );
  await viewer.screenshot({
    path: "artifacts/screenshots/desktop-preview-plot-window.png",
  });
  await clickDesktop(
    ownedPlotWindow.getByRole("button", { name: "CDF", exact: true }),
  );
  await waitFor(
    async () =>
      (await ownedPlotWindow
        .getByRole("button", { name: "CDF", exact: true })
        .getAttribute("aria-pressed")) === "true",
    "Viewer opens the native cumulative plot",
  );
  await ownedPlotWindow.locator('canvas[data-ready="true"]').waitFor();
  const cdfFrame = Number(
    await viewer.locator("#desktop").getAttribute("data-frame-number"),
  );
  await waitFor(
    async () =>
      Number(
        await viewer.locator("#desktop").getAttribute("data-frame-number"),
      ) >=
      cdfFrame + 2,
    "Viewer receives the completed native CDF pixels",
  );
  await viewer.screenshot({
    path: "artifacts/screenshots/desktop-preview-cdf.png",
  });
  await clickDesktop(
    ownedPlotWindow.getByRole("button", { name: "Zebra", exact: true }),
  );
  await waitFor(
    async () =>
      (await ownedPlotWindow
        .getByRole("button", { name: "Zebra", exact: true })
        .getAttribute("aria-pressed")) === "true",
    "Viewer opens the native probability bands",
  );
  await ownedPlotWindow.locator('canvas[data-ready="true"]').waitFor();
  await clickDesktop(ownedPlotWindow.locator(".graph-settings > summary"));
  await waitFor(
    async () =>
      ownedPlotWindow
        .locator(".graph-settings")
        .evaluate((element) => element.open),
    "Native graph settings expanded",
  );
  await ownedPlotWindow.evaluate(
    () =>
      new Promise((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(resolve)),
      ),
  );
  await ownedPlotWindow.evaluate(() => {
    globalThis.graphPreviewKeys = [];
    document.addEventListener(
      "keydown",
      (event) => {
        globalThis.graphPreviewKeys.push({
          key: event.key,
          control: document.activeElement?.getAttribute("aria-label"),
        });
      },
      true,
    );
    document.addEventListener(
      "pointerdown",
      (event) => {
        const palette = document.querySelector('[aria-label="Graph palette"]');
        const box = palette?.getBoundingClientRect();
        globalThis.graphPreviewKeys.push({
          pointer: [event.clientX, event.clientY],
          target: event.target?.tagName,
          paletteBounds: box ? [box.x, box.y, box.width, box.height] : null,
        });
      },
      true,
    );
  });
  await clickDesktop(
    ownedPlotWindow.getByLabel("Graph palette", { exact: true }),
  );
  await viewer.keyboard.press("Home");
  await viewer.keyboard.press("ArrowDown");
  await viewer.keyboard.press("ArrowDown");
  await viewer.keyboard.press("ArrowDown");
  await viewer.keyboard.press("Enter");
  await waitFor(
    async () =>
      (await ownedPlotWindow
        .getByLabel("Graph palette", { exact: true })
        .inputValue()) === "viridis",
    "Viewer keyboard changes native graph settings",
  );
  await ownedPlotWindow.locator('canvas[data-ready="true"]').waitFor();
  await waitFor(
    async () =>
      (
        await ownedPlotWindow.evaluate(() =>
          globalThis.cytoforgeDesktop.getPlotWindow(),
        )
      ).graphOptions?.palette === "viridis",
    "Native plot window persists the viewer's graph settings",
  );
  const zebraFrame = Number(
    await viewer.locator("#desktop").getAttribute("data-frame-number"),
  );
  await waitFor(
    async () =>
      Number(
        await viewer.locator("#desktop").getAttribute("data-frame-number"),
      ) >=
      zebraFrame + 2,
    "Viewer receives the completed native zebra pixels",
  );
  await viewer.screenshot({
    path: "artifacts/screenshots/desktop-preview-zebra.png",
  });
  await clickDesktop(
    ownedPlotWindow.getByRole("button", { name: "Contour", exact: true }),
  );
  await waitFor(
    async () =>
      (await ownedPlotWindow
        .getByRole("button", { name: "Contour", exact: true })
        .getAttribute("aria-pressed")) === "true",
    "Viewer opens the native contour plot",
  );
  await clickDesktop(
    ownedPlotWindow.getByLabel("Smooth density", { exact: true }),
  );
  await waitFor(
    async () =>
      !(await ownedPlotWindow
        .getByLabel("Smooth density", { exact: true })
        .isChecked()),
    "Viewer disables smoothing in the native contour window",
  );
  await ownedPlotWindow.locator('canvas[data-ready="true"]').waitFor();
  const contourView = await ownedPlotWindow.evaluate(() =>
    globalThis.cytoforgeDesktop.getPlotWindow(),
  );
  const contourQuery = new URLSearchParams({
    x: contourView.x,
    y: contourView.y,
    mode: "contour",
    bins: String(contourView.bins),
    graph_options: JSON.stringify(contourView.graphOptions),
    ...(contourView.bounds
      ? { bounds: JSON.stringify(contourView.bounds) }
      : {}),
    ...(contourView.gateId ? { gate_id: contourView.gateId } : {}),
  });
  const contourData = await nativeRequest(
    `/workspaces/${fixtureWorkspaceId}/samples/${contourView.sampleId}/plot?${contourQuery}`,
  );
  assert(contourData.contours.length > 0);
  assert(contourData.contours.every((c) => c.geometry === "bin_cells"));
  assert(
    contourData.contours.some((c) =>
      c.paths.some((path) => {
        const xs = path.map((p) => p[0]),
          ys = path.map((p) => p[1]);
        return (
          Math.max(...xs) - Math.min(...xs) >= 0.9 / contourView.bins &&
          Math.max(...ys) - Math.min(...ys) >= 0.9 / contourView.bins
        );
      }),
    ),
  );
  const contourFrame = Number(
    await viewer.locator("#desktop").getAttribute("data-frame-number"),
  );
  await waitFor(
    async () =>
      Number(
        await viewer.locator("#desktop").getAttribute("data-frame-number"),
      ) >=
      contourFrame + 2,
    "Viewer receives the native unsmoothed contour pixels",
  );
  await viewer.screenshot({
    path: "artifacts/screenshots/desktop-preview-contour-fixed.png",
  });
  await clickDesktop(
    ownedPlotWindow.getByRole("button", { name: "Zebra", exact: true }),
  );
  await waitFor(
    async () =>
      (await ownedPlotWindow
        .getByRole("button", { name: "Zebra", exact: true })
        .getAttribute("aria-pressed")) === "true",
    "Viewer returns to the native zebra view",
  );
  await clickDesktop(ownedPlotWindow.locator(".graph-settings > summary"));
  await waitFor(
    async () =>
      !(await ownedPlotWindow
        .locator(".graph-settings")
        .evaluate((element) => element.open)),
    "Native graph settings collapsed",
  );
  await ownedPlotWindow.evaluate(
    () =>
      new Promise((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(resolve)),
      ),
  );
  await clickDesktop(
    ownedPlotWindow.getByRole("button", { name: "3D", exact: true }),
  );
  await ownedPlotWindow
    .locator('.three-d-overlay[data-ready="true"]')
    .waitFor({ timeout: 30000 });
  const breadcrumbBefore = await ownedPlotWindow.evaluate(() =>
    globalThis.cytoforgeDesktop.getPlotWindow(),
  );
  assert.equal(breadcrumbBefore.gateId, null);
  const childId = await ownedPlotWindow
    .getByLabel("Open child population", { exact: true })
    .locator("option")
    .nth(1)
    .getAttribute("value");
  assert(childId);
  await clickDesktop(
    ownedPlotWindow.getByLabel("Open child population", { exact: true }),
  );
  await viewer.keyboard.press("ArrowDown");
  await viewer.keyboard.press("Enter");
  await waitFor(
    async () =>
      (
        await ownedPlotWindow.evaluate(() =>
          globalThis.cytoforgeDesktop.getPlotWindow(),
        )
      ).gateId === childId,
    "Viewer opens a child population through native input",
  );
  await ownedPlotWindow
    .locator('canvas[data-ready="true"]')
    .first()
    .waitFor({ timeout: 30000 });
  await clickDesktop(
    ownedPlotWindow.getByRole("button", {
      name: "View all events",
      exact: true,
    }),
  );
  await waitFor(
    async () =>
      (
        await ownedPlotWindow.evaluate(() =>
          globalThis.cytoforgeDesktop.getPlotWindow(),
        )
      ).gateId === null,
    "Viewer breadcrumb restores all events",
  );
  await ownedPlotWindow
    .locator('.three-d-overlay[data-ready="true"]')
    .waitFor({ timeout: 30000 });
  const breadcrumbAfter = await ownedPlotWindow.evaluate(() =>
    globalThis.cytoforgeDesktop.getPlotWindow(),
  );
  for (const key of ["x", "y", "mode", "bins", "bounds"])
    assert.deepEqual(breadcrumbAfter[key], breadcrumbBefore[key]);
  const cameraDefaults = { yaw: -0.65, pitch: 0.45, zoom: 1, pan: [0, 0] };
  assert.equal(breadcrumbAfter.threeD.z, breadcrumbBefore.threeD.z);
  for (const key of ["yaw", "pitch", "zoom", "pan"])
    assert.deepEqual(
      breadcrumbAfter.threeD[key] ?? cameraDefaults[key],
      breadcrumbBefore.threeD[key] ?? cameraDefaults[key],
    );
  const frameBefore = Number(
    await viewer.locator("#desktop").getAttribute("data-frame-number"),
  );
  await waitFor(
    async () =>
      Number(
        await viewer.locator("#desktop").getAttribute("data-frame-number"),
      ) > frameBefore,
    "Updated native breadcrumb view reaches the remote frame",
  );
  await viewer.screenshot({
    path: "artifacts/screenshots/desktop-preview-population-breadcrumbs.png",
  });
  const firstParameter = await ownedPlotWindow
    .getByLabel("X axis channel")
    .locator("option")
    .first()
    .getAttribute("value");
  await clickDesktop(ownedPlotWindow.getByLabel("X axis channel"));
  await viewer.keyboard.press("Home");
  await viewer.keyboard.press("Enter");
  await waitFor(
    async () =>
      (await ownedPlotWindow.getByLabel("X axis channel").inputValue()) ===
      firstParameter,
    "Viewer chooses an independent 3D X parameter",
  );
  await ownedPlotWindow
    .locator('.three-d-overlay[data-ready="true"]')
    .waitFor({ timeout: 30000 });
  const cloud = ownedPlotWindow.getByRole("application", {
    name: "Interactive 3D plot",
  });
  await clickDesktop(cloud);
  const beforeCamera = await ownedPlotWindow.evaluate(() =>
    globalThis.cytoforgeDesktop.getPlotWindow(),
  );
  await viewer.keyboard.press("ArrowRight");
  await waitFor(
    async () =>
      (
        await ownedPlotWindow.evaluate(() =>
          globalThis.cytoforgeDesktop.getPlotWindow(),
        )
      ).threeD.yaw !== beforeCamera.threeD.yaw,
    "Viewer keyboard rotates native 3D camera",
  );
  assert.equal(
    (
      await ownedPlotWindow.evaluate(() =>
        globalThis.cytoforgeDesktop.getPlotWindow(),
      )
    ).mode,
    "3d",
  );
  assert(
    Number(
      await ownedPlotWindow
        .locator(".three-d-overlay")
        .getAttribute("data-loaded-count"),
    ) > 0,
  );
  const cloudFrame = Number(
    await viewer.locator("#desktop").getAttribute("data-frame-number"),
  );
  await waitFor(
    async () =>
      Number(
        await viewer.locator("#desktop").getAttribute("data-frame-number"),
      ) >=
      cloudFrame + 2,
    "Viewer receives rotated native 3D pixels",
  );
  await viewer.screenshot({
    path: "artifacts/screenshots/desktop-preview-3d.png",
  });
  const navigationBefore = await ownedPlotWindow.evaluate(() =>
    globalThis.cytoforgeDesktop.getPlotWindow(),
  );
  const mainSampleBefore = await native
    .getByLabel("Plot sample", { exact: true })
    .inputValue();
  await clickDesktop(
    ownedPlotWindow.getByRole("button", {
      name: "Next matching sample",
      exact: true,
    }),
  );
  await waitFor(
    async () =>
      (
        await ownedPlotWindow.evaluate(() =>
          globalThis.cytoforgeDesktop.getPlotWindow(),
        )
      ).sampleId !== navigationBefore.sampleId,
    "Viewer steps the selected native plot independently",
  );
  assert.equal(
    await native.getByLabel("Plot sample", { exact: true }).inputValue(),
    mainSampleBefore,
  );
  await clickDesktop(
    ownedPlotWindow.getByRole("button", {
      name: "Previous matching sample",
      exact: true,
    }),
  );
  await waitFor(
    async () =>
      (
        await ownedPlotWindow.evaluate(() =>
          globalThis.cytoforgeDesktop.getPlotWindow(),
        )
      ).sampleId === navigationBefore.sampleId,
    "Viewer returns the plot to its original source",
  );
  assert.equal(mainSampleBefore, navigationBefore.sampleId);
  await viewer.keyboard.down("Shift");
  try {
    await clickDesktop(
      ownedPlotWindow.getByRole("button", {
        name: "Next matching sample",
        exact: true,
      }),
    );
  } finally {
    await viewer.keyboard.up("Shift");
  }
  await waitFor(async () => {
    const plot = await ownedPlotWindow.evaluate(() =>
      globalThis.cytoforgeDesktop.getPlotWindow(),
    );
    return (
      plot.sampleId !== navigationBefore.sampleId &&
      plot.sampleId ===
        (await native.getByLabel("Plot sample", { exact: true }).inputValue())
    );
  }, "Viewer Shift-click moves the main and native plot together");
  const navigationAfter = await ownedPlotWindow.evaluate(() =>
    globalThis.cytoforgeDesktop.getPlotWindow(),
  );
  assert.equal(navigationAfter.threeD.yaw, navigationBefore.threeD.yaw);
  assert.deepEqual(
    navigationAfter.threeD.pan,
    navigationBefore.threeD.pan ?? [0, 0],
  );
  assert.equal(
    navigationAfter.threeD.pitch,
    navigationBefore.threeD.pitch ?? 0.45,
  );
  assert.equal(navigationAfter.threeD.zoom, navigationBefore.threeD.zoom ?? 1);
  await ownedPlotWindow
    .locator('.three-d-overlay[data-ready="true"]')
    .waitFor({ timeout: 30000 });
  await viewer.keyboard.down("Shift");
  try {
    await clickDesktop(
      ownedPlotWindow.getByRole("button", {
        name: "Previous matching sample",
        exact: true,
      }),
    );
  } finally {
    await viewer.keyboard.up("Shift");
  }
  await waitFor(
    async () =>
      (await native.getByLabel("Plot sample", { exact: true }).inputValue()) ===
      mainSampleBefore,
    "Viewer returns coordinated plots to the original synthetic sample",
  );
  await ownedPlotWindow
    .locator('.three-d-overlay[data-ready="true"]')
    .waitFor({ timeout: 30000 });
  await viewer.screenshot({
    path: "artifacts/screenshots/desktop-preview-navigation.png",
  });
  await clickDesktop(
    ownedPlotWindow.getByRole("button", {
      name: "Close plot window",
      exact: true,
    }),
  );
  await waitFor(
    () => ownedPlotWindow.isClosed(),
    "Viewer closes the native plot window",
  );
  await waitFor(
    async () =>
      (await (await context.request.get(`${base}/state`)).json())
        .selectedWindowId === mainRecord.id,
    "Viewer returns to the main workspace after popup closes",
  );

  await navigation("Statistics");
  const filter = native.getByLabel("Filter statistics", { exact: true });
  await filter.waitFor();
  await clickDesktop(filter);
  await viewer.keyboard.type("D01");
  await waitFor(
    async () => (await filter.inputValue()) === "D01",
    "Keyboard text reaches the native window",
  );
  await viewer.locator("#desktop").evaluate((element) => {
    const clipboardData = new DataTransfer();
    clipboardData.setData("text/plain", "_paste");
    element.dispatchEvent(
      new ClipboardEvent("paste", {
        clipboardData,
        bubbles: true,
        cancelable: true,
      }),
    );
  });
  await waitFor(
    async () => (await filter.inputValue()) === "D01_paste",
    "Clipboard text reaches the native window",
  );
  await viewer.keyboard.press("Control+a");
  await viewer.keyboard.press("Backspace");
  await waitFor(
    async () => (await filter.inputValue()) === "",
    "Keyboard shortcuts reach the native window",
  );
  await clickDesktop(
    native.getByRole("button", { name: "Export CSV", exact: true }),
  );
  await waitFor(async () => {
    const state = await (await context.request.get(`${base}/state`)).json();
    return state.downloads.some(
      (item) =>
        item.name === "cytoforge-statistics.csv" && item.state === "completed",
    );
  }, "Native CSV export is available in the viewer");
  await waitFor(
    async () =>
      (await viewer
        .locator("#exports option")
        .filter({ hasText: "cytoforge-statistics.csv" })
        .count()) > 0,
    "Export appears in the viewer",
  );
  const csvOption = viewer
    .locator("#exports option")
    .filter({ hasText: "cytoforge-statistics.csv" })
    .last();
  const csvId = await csvOption.getAttribute("value");
  const downloadPromise = viewer.waitForEvent("download");
  await viewer.getByLabel("Desktop exports").selectOption(csvId);
  const download = await downloadPromise;
  assert.equal(download.suggestedFilename(), "cytoforge-statistics.csv");
  await download.saveAs("artifacts/desktop-preview-statistics.csv");
  const csv = readFileSync("artifacts/desktop-preview-statistics.csv", "utf8");
  assert(csv.includes("D01_CTRL.fcs"));
  assert(csv.includes("D06_STIM.fcs"));

  await navigation("Layout studio");
  if ((await native.locator(".studio-object-list button").count()) === 0)
    await clickDesktop(
      native.getByRole("button", { name: "Add current plot", exact: true }),
    );
  await waitFor(
    async () =>
      !(await native.locator(".studio-canvas-status").textContent()).includes(
        "Updating figures",
      ),
    "Native report renderer is ready",
  );
  await waitFor(
    async () =>
      await native
        .getByRole("button", { name: "Export PDF", exact: true })
        .isEnabled(),
    "Native report PDF is available",
  );
  await clickDesktop(
    native.getByRole("button", { name: "Export PDF", exact: true }),
  );
  let pdfItem;
  await waitFor(
    async () => {
      const state = await (await context.request.get(`${base}/state`)).json();
      pdfItem = state.downloads.find(
        (item) => item.name.endsWith(".pdf") && item.state === "completed",
      );
      return !!pdfItem;
    },
    "Native PDF is transferred through the desktop viewer",
    45000,
  );
  const pdfResponse = await context.request.get(
    `${base}/downloads/${pdfItem.id}`,
  );
  assert.equal(pdfResponse.status(), 200);
  const pdfBytes = await pdfResponse.body();
  assert.equal(pdfBytes.subarray(0, 5).toString(), "%PDF-");
  writeFileSync("artifacts/desktop-preview-report.pdf", pdfBytes);
  const pdf = await PDFDocument.load(pdfBytes);
  assert(pdf.getPageCount() >= 1);
  const names = pdf.context.lookup(
    pdf.catalog.get(PDFName.of("Names")),
    PDFDict,
  );
  assert(names.has(PDFName.of("EmbeddedFiles")));

  await clickDesktop(
    native.getByRole("button", { name: "Import FCS", exact: true }),
  );
  await viewer
    .getByRole("button", { name: "Choose files…", exact: true })
    .waitFor({ timeout: 10000 });
  const current = await (await context.request.get(`${base}/state`)).json();
  assert.equal(
    (
      await context.request.post(
        `${base}/upload?chooser=${current.fileChooser}&name=../escape.csv&last=1`,
        { headers: { Origin: base }, data: "X\n1" },
      )
    ).status(),
    400,
  );
  const chooserPromise = viewer.waitForEvent("filechooser");
  await viewer
    .getByRole("button", { name: "Choose files…", exact: true })
    .click();
  const chooser = await chooserPromise;
  await chooser.setFiles({
    name: "desktop-progress-verification.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(
      "FSC-A,SSC-A,Time\n10,20,0\n12,18,1\n11,21,2\n9,19,3\n",
    ),
  });
  await waitFor(async () => {
    const sample = await native.evaluate(async () => {
      const { token } = await (await fetch("/api/bootstrap")).json();
      const id = await globalThis.cytoforgeDesktop.getWorkspace();
      const doc = await (
        await fetch(`/api/workspaces/${id}`, {
          headers: { "X-CytoForge-Token": token },
        })
      ).json();
      return doc.samples.find(
        (sample) =>
          sample.filename === "desktop-progress-verification.csv" ||
          sample.name === "desktop-progress-verification.csv",
      );
    });
    if (sample) {
      assert.equal(sample.event_count, 4);
      importedId = sample.id;
      return true;
    }
    return false;
  }, "Viewer file transfer is imported by the bundled engine");
  assert(importedId);
  assert(nativeInputs.flat().some((event) => event.type === "down"));
  assert(nativeInputs.flat().some((event) => event.type === "keyDown"));
  assert.deepEqual(errors, []);
  await viewer.screenshot({
    path: "artifacts/screenshots/desktop-preview-import.png",
  });
  await clickDesktop(
    native.getByRole("button", { name: "Continue", exact: true }),
  );
  await native.getByRole("dialog").waitFor({ state: "hidden" });
  const appResponse = await fetch(`${base}/app`);
  assert.equal(appResponse.status, 200);
  assert.equal(
    Number(appResponse.headers.get("content-length")),
    statSync("artifacts/installers/CytoForge-0.1.0.AppImage").size,
  );
  await appResponse.body.cancel();
  const hash = createHash("sha256")
    .update(readFileSync("artifacts/installers/CytoForge-0.1.0.AppImage"))
    .digest("hex");
  if (originalWorkspaceId) {
    assert.deepEqual(
      await nativeRequest("/workspaces/" + originalWorkspaceId),
      originalWorkspace,
    );
  }
  await selectWorkspace(originalWorkspaceId);
  await waitFor(
    async () =>
      JSON.stringify(
        (
          await native.evaluate(() =>
            globalThis.cytoforgeDesktop.getPlotSession(),
          )
        )?.state ?? null,
      ) === JSON.stringify(originalPlotState),
    "Original main plot settings restored after viewer validation",
  );
  workspaceRestored = true;
  writeFileSync(
    reportPath,
    JSON.stringify(
      {
        status: "passed",
        validated_at: new Date().toISOString(),
        host: session.host,
        port: session.port,
        packaged: session.packaged,
        desktop_pid: session.desktop_pid,
        private_engine_pid: session.private_engine_pid,
        sandbox_exception: session.sandbox_exception,
        appimage_sha256: hash,
        application_api_exposed: false,
        mouse_navigation: true,
        keyboard_text: true,
        keyboard_shortcuts: true,
        clipboard_text: true,
        cross_site_session_link: true,
        file_upload_import: true,
        desktop_csv_download: true,
        desktop_pdf_download: true,
        desktop_pdf_embedded_sources: true,
        unauthenticated_control_rejected: true,
        hostile_origin_rejected: true,
        path_traversal_rejected: true,
        arbitrary_evaluation_rejected: true,
        native_plot_windows: true,
        native_double_click_popup: true,
        selected_plot_input_and_display: true,
        independent_plot_axes: true,
        native_cdf_and_zebra_input: true,
        native_unsmoothed_contour_input: true,
        native_graph_settings_persisted: true,
        native_three_dimensional_cloud_and_camera_input: true,
        native_independent_and_coordinated_sample_navigation: true,
        sample_navigation_retains_three_d_camera: true,
        native_population_breadcrumbs_and_view_recovery: true,
        frame_identity_matches_native_window: true,
        closed_plot_returns_to_main: true,
        unknown_window_and_hostile_origin_rejected: true,
        browser_errors: errors,
        fixture_workspace_id: fixtureWorkspaceId,
        original_workspace_id: originalWorkspaceId,
        existing_workspace_unchanged: true,
        original_workspace_selection_restored: true,
        original_main_plot_settings_restored: true,
        fixture_setup:
          "Fresh synthetic workspace through the private local API; tested interactions go through the viewer.",
      },
      null,
      2,
    ),
  );
  console.log(
    "Packaged desktop preview: mouse, keyboard, import, CSV/PDF transfer and access checks passed.",
  );
} catch (error) {
  let nativeGraphInput = null;
  if (ownedPlotWindow && !ownedPlotWindow.isClosed()) {
    nativeGraphInput = await ownedPlotWindow.evaluate(() => ({
      palette: document.querySelector('[aria-label="Graph palette"]')?.value,
      focusedControl: document.activeElement?.getAttribute("aria-label"),
      focusedElement: document.activeElement?.tagName,
      keys: globalThis.graphPreviewKeys,
    }));
    await ownedPlotWindow.screenshot({
      path: "artifacts/screenshots/desktop-preview-graph-input-failure.png",
    });
  }
  writeFileSync(
    reportPath,
    JSON.stringify(
      {
        status: "failed",
        validated_at: new Date().toISOString(),
        error: error.message,
        native_graph_input: nativeGraphInput,
      },
      null,
      2,
    ),
  );
  await viewer
    .screenshot({ path: "artifacts/screenshots/desktop-preview-failure.png" })
    .catch(() => {});
  throw error;
} finally {
  if (ownedPlotWindow && !ownedPlotWindow.isClosed())
    await ownedPlotWindow
      .evaluate(() => globalThis.cytoforgeDesktop.closePlotWindow())
      .catch(() => {});
  if (importedId)
    await native
      .evaluate(
        async ({ id, workspaceId }) => {
          const { token } = await (await fetch("/api/bootstrap")).json();
          const headers = { "X-CytoForge-Token": token };
          const doc = await (
            await fetch(`/api/workspaces/${workspaceId}`, { headers })
          ).json();
          await fetch(
            `/api/workspaces/${workspaceId}/samples/${id}?revision=${doc.revision}`,
            { method: "DELETE", headers },
          );
        },
        { id: importedId, workspaceId: fixtureWorkspaceId },
      )
      .catch(() => {});
  if (originalWorkspaceId !== undefined && !workspaceRestored)
    await selectWorkspace(originalWorkspaceId).catch(() => {});
  else await native.reload().catch(() => {});
  await navigation("Analysis").catch(() => {});
  await native
    .locator('canvas[data-ready="true"]')
    .first()
    .waitFor({ timeout: 15000 })
    .catch(() => {});
  await viewer.reload().catch(() => {});
  await viewer
    .locator('#desktop[data-ready="true"]')
    .waitFor({ timeout: 10000 })
    .catch(() => {});
  await viewer
    .screenshot({ path: "artifacts/screenshots/live-desktop-viewer.png" })
    .catch(() => {});
  await browser.close();
  await desktop.close(); // CDP detach; the preview owns the desktop lifetime.
  linkOrigin.closeAllConnections();
  linkOrigin.close();
}
