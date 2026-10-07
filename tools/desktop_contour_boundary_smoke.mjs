// Verify contour geometry, actual native pixels and a genuine vector PDF.
// All imports and edits use this isolated synthetic profile.
import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { _electron as electron, expect } from "playwright/test";
import {
  PDFDocument,
  PDFName,
  PDFDict,
  PDFArray,
  PDFRawStream,
  decodePDFRawStream,
} from "pdf-lib";

const root = process.cwd();
const profile = path.join(
  root,
  ".tmp/desktop-contour-boundaries-" + Date.now(),
);
const output =
  process.env.CYTOFORGE_CONTOUR_EVIDENCE ||
  "artifacts/desktop-contour-boundaries-source.json";
const binary = process.env.CYTOFORGE_TEST_BINARY;
mkdirSync(profile, { recursive: true });
mkdirSync("artifacts/screenshots", { recursive: true });
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
const save = () => writeFileSync(output, JSON.stringify(evidence, null, 2));
const passed = (message) => {
  evidence.checks.push(message);
  save();
};
save();
let application, main;
const errors = [];
const timer = setTimeout(() => application?.process().kill(), 300000);
const uuid = () => randomUUID().replaceAll("-", "");
async function request(route, body, timed = false) {
  return main.evaluate(
    async ({ route, body, timed }) => {
      const { token } = await (await fetch("/api/bootstrap")).json();
      const started = performance.now();
      const response = await fetch("/api" + route, {
        method: body ? "POST" : "GET",
        headers: {
          "X-CytoForge-Token": token,
          "Content-Type": "application/json",
        },
        body: body ? JSON.stringify(body) : undefined,
      });
      const headersAt = performance.now();
      if (!response.ok) throw new Error(await response.text());
      const result = await response.json();
      if (timed)
        globalThis.contourTransferMetrics = {
          headers_ms: headersAt - started,
          body_and_json_parse_ms: performance.now() - headersAt,
          response_and_parse_ms: performance.now() - started,
          scope:
            "Measured inside the native renderer; excludes returning the large result through the test-driver protocol",
        };
      return result;
    },
    { route, body, timed },
  );
}
async function ready(page) {
  await page
    .locator('canvas[data-ready="true"]')
    .first()
    .waitFor({ timeout: 30000 });
  await expect(page.locator(".busy-indicator")).toHaveCount(0);
  await page.evaluate(
    () =>
      new Promise((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(resolve)),
      ),
  );
}
async function open(view) {
  const created = application.waitForEvent("window");
  await main.evaluate(
    (view) => globalThis.cytoforgeDesktop.openPlotWindow(view),
    view,
  );
  const page = await created;
  page.on("pageerror", (e) => errors.push(e.message));
  await page.setViewportSize({ width: 1540, height: 1100 });
  await ready(page);
  return page;
}
function regionArea(paths) {
  return paths.reduce(
    (sum, path) =>
      sum +
      path
        .slice(1)
        .reduce(
          (area, p, i) => area + (path[i][0] * p[1] - p[0] * path[i][1]) / 2,
          0,
        ),
    0,
  );
}
async function pixels(page, contours, domain, bounds) {
  return page.locator(".primary-plot canvas").evaluate(
    (canvas, { contours, domain, bounds }) => {
      const context = canvas.getContext("2d"),
        dpr = window.devicePixelRatio || 1;
      const width = canvas.width / dpr - 88,
        height = canvas.height / dpr - 78;
      const image = context.getImageData(
        0,
        0,
        canvas.width,
        canvas.height,
      ).data;
      return contours.flatMap((c) =>
        c.paths.map((path) => {
          const xs = path.map(
            (p) =>
              (64 +
                ((domain[0] + p[0] * (domain[1] - domain[0]) - bounds[0]) /
                  (bounds[1] - bounds[0])) *
                  width) *
              dpr,
          );
          const ys = path.map(
            (p) =>
              (24 +
                (1 -
                  (domain[2] + p[1] * (domain[3] - domain[2]) - bounds[2]) /
                    (bounds[3] - bounds[2])) *
                  height) *
              dpr,
          );
          let colored = 0;
          for (
            let y = Math.max(0, Math.floor(Math.min(...ys) - 2));
            y <= Math.min(canvas.height - 1, Math.ceil(Math.max(...ys) + 2));
            y++
          )
            for (
              let x = Math.max(0, Math.floor(Math.min(...xs) - 2));
              x <= Math.min(canvas.width - 1, Math.ceil(Math.max(...xs) + 2));
              x++
            ) {
              const at = (y * canvas.width + x) * 4;
              const r = image[at],
                g = image[at + 1],
                b = image[at + 2];
              const alpha = (g - 23) / 201;
              // The contour color (#76e0ce) blended with the dark canvas. Gate
              // colors and axis/grid text do not match this color direction.
              if (
                alpha > 0.15 &&
                Math.abs(r - (17 + 101 * alpha)) < 8 &&
                Math.abs(b - (31 + 175 * alpha)) < 8
              )
                colored++;
            }
          return {
            colored_pixels: colored,
            width: Math.max(...xs) - Math.min(...xs),
            height: Math.max(...ys) - Math.min(...ys),
          };
        }),
      );
    },
    { contours, domain, bounds },
  );
}
try {
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
  main = await application.firstWindow();
  main.on("pageerror", (e) => errors.push(e.message));
  await main.setViewportSize({ width: 1540, height: 1100 });
  await main.waitForLoadState("networkidle");
  await main
    .getByRole("button", { name: "New experiment", exact: true })
    .click();
  await main
    .getByLabel("Experiment name", { exact: true })
    .fill("Contour boundary truth");
  await main
    .getByRole("button", { name: "Create workspace", exact: true })
    .click();
  const fixtures = [
    [
      "Grid",
      Array.from({ length: 100 }, (_, i) => `${i % 10},${Math.floor(i / 10)}`),
    ],
    [
      "Hole",
      Array.from({ length: 9 }, (_, i) =>
        i === 4
          ? null
          : `${(5.5 + (i % 3)) / 16},${(5.5 + Math.floor(i / 3)) / 16}`,
      ).filter(Boolean),
    ],
    ["Peak", ["0.5,0.5"]],
    [
      "Fragmented",
      Array.from({ length: 256 * 256 }, (_, i) => {
        const x = i % 256,
          y = Math.floor(i / 256);
        return (x + y) % 2 === 0
          ? `${(x + 64.5) / 384},${(y + 64.5) / 384}`
          : null;
      }).filter(Boolean),
    ],
  ];
  const sources = fixtures.map(([name, values]) => {
    const file = path.join(profile, name + ".csv");
    writeFileSync(file, ["X,Y", ...values].join("\n"));
    return file;
  });
  const chooser = main.waitForEvent("filechooser");
  await main.getByRole("button", { name: "Import FCS", exact: true }).click();
  await (await chooser).setFiles(sources);
  await main.getByRole("button", { name: "Continue", exact: true }).click();
  await ready(main);
  const workspaceId = await main.getByLabel("Active workspace").inputValue();
  const route = "/workspaces/" + workspaceId;
  let doc = await request(route);
  assert.deepEqual(
    doc.samples.map((s) => s.event_count),
    [100, 8, 1, 32768],
  );
  const [grid, hole, peak, fragmented] = doc.samples;
  for (const [name, count, title] of [
    ["Grid.csv", "100", "100 events"],
    ["Hole.csv", "8", "8 events"],
    ["Peak.csv", "1", "1 event"],
    ["Fragmented.csv", "32.8k", "32,768 events"],
  ]) {
    const badge = main
      .locator(".sidebar-sample")
      .filter({ hasText: name })
      .locator("small");
    await expect(badge).toHaveText(count);
    await expect(badge).toHaveAttribute("title", title);
  }
  passed(
    "Rare acquisitions show exact sidebar counts and every sample exposes its complete count",
  );
  const gate = {
    id: uuid(),
    sample_id: grid.id,
    parent_id: null,
    name: "25 literal events",
    kind: "rectangle",
    x: "X",
    y: "Y",
    bounds: [0, 5, 0, 5],
  };
  doc = await request(route + "/gates/batch", {
    revision: doc.revision,
    gates: [gate],
  });
  await main.evaluate(
    (id) => globalThis.cytoforgeDesktop.notifyWorkspaceChanged(id),
    workspaceId,
  );
  const before = JSON.stringify(await request(route));
  const historyBefore = JSON.stringify(await request(route + "/history"));
  const common = {
    workspaceId,
    x: "X",
    y: "Y",
    mode: "contour",
    graphOptions: { smooth: false, axis_extent: "full", contour_spacing: "10" },
  };
  const sparseView = {
    ...common,
    sampleId: grid.id,
    gateId: gate.id,
    bins: 160,
    bounds: [-1, 11, -1, 11],
  };
  const sparse = await open(sparseView);
  const sparsePayload = await request(
    route +
      `/samples/${grid.id}/plot?` +
      new URLSearchParams({
        x: "X",
        y: "Y",
        gate_id: gate.id,
        mode: "contour",
        bins: "160",
        bounds: JSON.stringify(sparseView.bounds),
        graph_options: JSON.stringify(sparseView.graphOptions),
      }),
  );
  assert.equal(sparsePayload.count, 25);
  assert.equal(sparsePayload.contours.length, 1);
  assert.equal(sparsePayload.contours[0].geometry, "bin_cells");
  assert.equal(sparsePayload.contours[0].paths.length, 25);
  assert(
    Math.abs(regionArea(sparsePayload.contours[0].paths) - 25 / 160 ** 2) <
      1e-12,
  );
  const sparsePixels = await pixels(
    sparse,
    sparsePayload.contours,
    sparsePayload.density_bounds,
    sparsePayload.bounds,
  );
  assert(
    sparsePixels.every(
      (p) => p.colored_pixels >= 4 && p.width > 2 && p.height > 1,
    ),
  );
  await sparse.locator(".probability-audit summary").click();
  await expect(sparse.locator(".probability-audit")).toContainText(
    "Contours follow bin edges",
  );
  await sparse.screenshot({
    path: "artifacts/screenshots/desktop-contour-sparse-fixed.png",
  });
  evidence.sparse_pixels = sparsePixels;
  evidence.sparse_levels = sparsePayload.probability_levels;
  passed(
    "25 unsmoothed tied bins retain exact mass, positive area and visible native contour pixels",
  );
  const holeView = {
    ...common,
    sampleId: hole.id,
    gateId: null,
    bins: 16,
    bounds: [0, 1, 0, 1],
  };
  const holeWindow = await open(holeView);
  const holePayload = await request(
    route +
      `/samples/${hole.id}/plot?` +
      new URLSearchParams({
        x: "X",
        y: "Y",
        mode: "contour",
        bins: "16",
        bounds: "[0,1,0,1]",
        graph_options: JSON.stringify(holeView.graphOptions),
      }),
  );
  const holePaths = holePayload.contours[0].paths;
  assert.equal(holePaths.length, 2);
  assert(holePaths.some((p) => regionArea([p]) < 0));
  assert(Math.abs(regionArea(holePaths) - 8 / 256) < 1e-12);
  const holePixels = await pixels(
    holeWindow,
    holePayload.contours,
    holePayload.density_bounds,
    holePayload.bounds,
  );
  assert(holePixels.every((p) => p.colored_pixels >= 4));
  await holeWindow.screenshot({
    path: "artifacts/screenshots/desktop-contour-hole-fixed.png",
  });
  evidence.hole_pixels = holePixels;
  passed(
    "Native outer and hole boundaries preserve exactly eight occupied cells",
  );
  const peakView = {
    ...common,
    sampleId: peak.id,
    gateId: null,
    bins: 16,
    bounds: [0, 1, 0, 1],
    graphOptions: { smooth: true, contour_spacing: "2", axis_extent: "full" },
  };
  const peakWindow = await open(peakView);
  const peakPayload = await request(
    route +
      `/samples/${peak.id}/plot?` +
      new URLSearchParams({
        x: "X",
        y: "Y",
        mode: "contour",
        bins: "16",
        bounds: "[0,1,0,1]",
        graph_options: JSON.stringify(peakView.graphOptions),
      }),
  );
  const peakContour = peakPayload.contours.find(
    (c) => c.threshold === peakPayload.density_max,
  );
  assert.equal(peakContour.geometry, "bin_cells");
  assert(Math.abs(regionArea(peakContour.paths) - 1 / 256) < 1e-12);
  assert(
    peakPayload.contours.some((c) => c.geometry === "interpolated_centers"),
  );
  const peakPixels = await pixels(
    peakWindow,
    [peakContour],
    peakPayload.density_bounds,
    peakPayload.bounds,
  );
  assert(peakPixels.every((p) => p.colored_pixels >= 4));
  evidence.peak_pixels = peakPixels;
  passed(
    "A smoothed single-event peak remains visible while other density levels retain interpolation",
  );
  const fragmentedView = {
    ...common,
    sampleId: fragmented.id,
    gateId: null,
    bins: 384,
    bounds: [0, 1, 0, 1],
  };
  const openedAt = performance.now();
  const fragmentedWindow = await open(fragmentedView);
  const readyMilliseconds = performance.now() - openedAt;
  const requestAt = performance.now();
  const fragmentedPayload = await request(
    route +
      `/samples/${fragmented.id}/plot?` +
      new URLSearchParams({
        x: "X",
        y: "Y",
        mode: "contour",
        bins: "384",
        bounds: "[0,1,0,1]",
        graph_options: JSON.stringify(fragmentedView.graphOptions),
      }),
    undefined,
    true,
  );
  const requestMilliseconds = performance.now() - requestAt;
  const nativeTransfer = await main.evaluate(
    () => globalThis.contourTransferMetrics,
  );
  assert.equal(fragmentedPayload.count, 32768);
  assert.equal(fragmentedPayload.finite_count, 32768);
  assert.equal(fragmentedPayload.probability_denominator, 32768);
  assert.equal(fragmentedPayload.density_count, 32768);
  assert.deepEqual(fragmentedPayload.density_bounds, [0, 1, 0, 1]);
  assert.equal(fragmentedPayload.contour_vertices, 150000);
  assert.equal(fragmentedPayload.contours[0].paths.length, 30000);
  assert.equal(fragmentedPayload.contours_truncated, true);
  assert.equal(fragmentedPayload.outlier_count, 0);
  assert(
    fragmentedPayload.probability_levels.every(
      (level) =>
        level.estimated_probability === 1 &&
        level.binned_event_probability === 1 &&
        level.tied_bins === 32768,
    ),
  );
  await expect(fragmentedWindow.locator(".plot-footer")).toContainText(
    "32,768 events",
  );
  await expect(fragmentedWindow.locator(".plot-footer")).toContainText(
    "contour drawing limit reached",
  );
  const fragmentPixels = await pixels(
    fragmentedWindow,
    [{ paths: fragmentedPayload.contours[0].paths.slice(100, 110) }],
    fragmentedPayload.density_bounds,
    fragmentedPayload.bounds,
  );
  assert(fragmentPixels.every((p) => p.colored_pixels >= 4));
  await fragmentedWindow.screenshot({
    path: "artifacts/screenshots/desktop-contour-fragmented.png",
  });
  evidence.fragmented = {
    events: 32768,
    drawn_vertices: 150000,
    drawn_regions: 30000,
    full_probability_audit: true,
    drawing_limit_visible: true,
    window_open_to_ready_ms: readyMilliseconds,
    complete_plot_response_ms: requestMilliseconds,
    native_response_and_parse: nativeTransfer,
    pixels: fragmentPixels,
    timing_scope:
      "Observed native headless window readiness and authenticated response transfer; includes renderer and protocol overhead, synthetic data on this host",
  };
  passed(
    "Native fragmented contours draw the same 30,000 regions while retaining all 32,768 events and visible drawing-limit provenance",
  );
  assert.equal(JSON.stringify(await request(route)), before);
  assert.equal(
    JSON.stringify(await request(route + "/history")),
    historyBefore,
  );
  passed(
    "Rendering and independent native views leave workspace data and scientific history unchanged",
  );

  const views = [
    sparseView,
    { ...sparseView, mode: "zebra" },
    holeView,
    peakView,
  ];
  const layout = {
    id: uuid(),
    name: "Contour boundary publication",
    show_header: true,
    show_footer: true,
    batch: { mode: "off" },
    pages: views.map(() => ({ width_mm: 210, height_mm: 297, margin_mm: 12 })),
    elements: views.map((v, page) => ({
      id: uuid(),
      kind: "plot",
      page,
      title: [
        "25 sparse events",
        "Sparse zebra",
        "Eight-cell hole",
        "Smoothed peak",
      ][page],
      x_mm: 15,
      y_mm: 40,
      width_mm: 175,
      height_mm: 150,
      plot: {
        id: uuid(),
        sample_id: v.sampleId,
        gate_id: v.gateId,
        x: v.x,
        y: v.y,
        mode: v.mode,
        bounds: v.bounds,
        bins: v.bins,
        graph_options: v.graphOptions,
        show_gates: false,
        color: "#087e8b",
      },
    })),
  };
  doc = await request(route + "/layouts/save", {
    revision: doc.revision,
    definition: layout,
  });
  await main.reload();
  await main
    .getByRole("button", { name: "Layout studio", exact: true })
    .click();
  await main
    .locator('.studio-svg [data-ready="true"]')
    .first()
    .waitFor({ timeout: 30000 });
  await expect(main.locator(".studio-canvas-status")).not.toContainText(
    "Updating figures",
  );
  const pdfPath = path.join(profile, "exports/contour-boundaries.pdf");
  await application.evaluate(({ dialog }, filePath) => {
    dialog.showSaveDialog = async () => ({ canceled: false, filePath });
  }, pdfPath);
  await main.getByRole("button", { name: "Export PDF", exact: true }).click();
  await expect(main.locator(".report-message")).toContainText("PDF saved", {
    timeout: 45000,
  });
  const bytes = readFileSync(pdfPath),
    pdf = await PDFDocument.load(bytes);
  assert.equal(pdf.getPageCount(), 4);
  const names = pdf.context.lookup(
    pdf.catalog.get(PDFName.of("Names")),
    PDFDict,
  );
  const files = pdf.context.lookup(
    names.get(PDFName.of("EmbeddedFiles")),
    PDFDict,
  );
  const entries = pdf.context.lookup(files.get(PDFName.of("Names")), PDFArray);
  const spec = pdf.context.lookup(entries.get(1), PDFDict);
  const embedded = pdf.context.lookup(spec.get(PDFName.of("EF")), PDFDict);
  const stream = pdf.context.lookup(
    embedded.get(PDFName.of("F")),
    PDFRawStream,
  );
  const manifest = JSON.parse(
    Buffer.from(decodePDFRawStream(stream).decode()).toString("utf8"),
  );
  const expectedCounts = [25, 25, 8, 1];
  manifest.pages.forEach((p, i) => {
    const layer = p.elements[0].layers[0];
    assert.equal(layer.population_count, expectedCounts[i]);
    assert.equal(layer.probability_denominator, expectedCounts[i]);
    assert(
      layer.contour_geometry_levels.some((c) => c.geometry === "bin_cells"),
    );
    assert(layer.contour_geometry && !layer.contours_truncated);
  });
  evidence.pdf = {
    path: pdfPath,
    pages: 4,
    bytes: bytes.length,
    sha256: createHash("sha256").update(bytes).digest("hex"),
  };
  evidence.manifest = manifest;
  evidence.expected_counts = expectedCounts;
  passed(
    "Four-page native PDF retains sparse, zebra, hole and peak contour geometry provenance",
  );
  assert.deepEqual(errors, []);
  evidence.native = await application.evaluate(({ app }) => ({
    packaged: app.isPackaged,
    sandbox_exception: app.commandLine.hasSwitch("no-sandbox"),
  }));
  evidence.status = "passed";
  evidence.validated_at = new Date().toISOString();
  evidence.renderer_errors = errors;
  if (binary)
    assert.equal(
      evidence.binary_sha256,
      createHash("sha256").update(readFileSync(binary)).digest("hex"),
      "The installer changed during native validation",
    );
  save();
  console.log(
    JSON.stringify({
      status: evidence.status,
      packaged: evidence.native.packaged,
      checks: evidence.checks.length,
      evidence: output,
    }),
  );
} catch (error) {
  Object.assign(evidence, {
    status: "failed",
    error: String(error.stack || error),
    renderer_errors: errors,
  });
  save();
  throw error;
} finally {
  clearTimeout(timer);
  await application?.close().catch(() => {});
}
