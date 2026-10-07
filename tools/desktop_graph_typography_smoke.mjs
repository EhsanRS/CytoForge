// Packaged native font controls, popup independence and unchanged scientific requests.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  createReadStream,
  mkdirSync,
  realpathSync,
  writeFileSync,
} from "node:fs";
import path from "node:path";
import { _electron as electron, expect } from "playwright/test";

const root = realpathSync(process.cwd());
const gateStyleTest = process.env.CYTOFORGE_TEST_GATE_STYLE === "1";
const binary = realpathSync(
  process.env.CYTOFORGE_TEST_BINARY ??
    path.join(
      root,
      "artifacts/candidates/graph-fonts/desktop-final/CytoForge-0.1.0.AppImage",
    ),
);
assert.ok(binary.startsWith(root + path.sep) && binary.endsWith(".AppImage"));
const profile = path.join(
  root,
  `.tmp/desktop-graph-${gateStyleTest ? "gates" : "typography"}-${Date.now()}`,
);
const output = path.join(
  root,
  `artifacts/desktop-graph-${gateStyleTest ? "gates" : "fonts"}-appimage.json`,
);
mkdirSync(profile, { recursive: true });
const hash = createHash("sha256");
for await (const chunk of createReadStream(binary)) hash.update(chunk);
const binaryHash = hash.digest("hex");
const noSandbox = process.env.CYTOFORGE_TEST_NO_SANDBOX === "1";
let application;
const errors = [];
async function request(page, route, body) {
  return page.evaluate(
    async ({ route, body }) => {
      const { token } = await (await fetch("/api/bootstrap")).json();
      const response = await fetch(`/api${route}`, {
        method: body ? "POST" : "GET",
        headers: {
          "X-CytoForge-Token": token,
          "Content-Type": "application/json",
        },
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
      if (!response.ok) throw new Error(await response.text());
      return response.json();
    },
    { route, body },
  );
}
try {
  application = await electron.launch({
    executablePath: binary,
    args: [
      "--headless",
      "--ozone-platform=headless",
      "--disable-gpu",
      ...(noSandbox ? ["--no-sandbox"] : []),
    ],
    chromiumSandbox: !noSandbox,
    env: {
      ...process.env,
      APPIMAGE_EXTRACT_AND_RUN: "1",
      CYTOFORGE_HOME: profile,
      CYTOFORGE_HEADLESS_TEST: "1",
    },
    timeout: 60000,
  });
  const main = await application.firstWindow();
  main.on("pageerror", (error) => errors.push(error.message));
  await main.setViewportSize({ width: 1540, height: 1050 });
  await main.waitForLoadState("networkidle");
  let workspace = await request(main, "/workspaces", {
    name: "Native graph typography truth",
  });
  workspace = await main.evaluate(async (workspace) => {
    const { token } = await (await fetch("/api/bootstrap")).json();
    const form = new FormData();
    form.append(
      "files",
      new File(["X,Y,Z\n0,0,0\n1,1,1\n2,2,2\n"], "Known.csv", {
        type: "text/csv",
      }),
    );
    const response = await fetch(
      `/api/workspaces/${workspace.id}/import?revision=${workspace.revision}`,
      { method: "POST", headers: { "X-CytoForge-Token": token }, body: form },
    );
    if (!response.ok) throw new Error(await response.text());
    return (await response.json()).workspace;
  }, workspace);
  if (gateStyleTest) {
    workspace = await request(main, `/workspaces/${workspace.id}/gates`, {
      revision: workspace.revision,
      gate: {
        sample_id: workspace.samples[0].id,
        name: "Known excluded centre",
        kind: "polygon",
        x: "X",
        y: "Y",
        color: "#135791",
        x_transform: { kind: "linear" },
        y_transform: { kind: "linear" },
        vertices: [
          [-0.5, -0.5],
          [2.5, -0.5],
          [2.5, 2.5],
          [-0.5, 2.5],
        ],
        holes: [
          [
            [0.5, 0.5],
            [1.2, 0.5],
            [1.2, 1.2],
            [0.5, 1.2],
          ],
          [
            [0.8, 0.8],
            [1.5, 0.8],
            [1.5, 1.5],
            [0.8, 1.5],
          ],
        ],
      },
    });
  }
  await main.evaluate(
    (workspace) => globalThis.cytoforgeDesktop.saveWorkspace(workspace.id),
    workspace,
  );
  await main.reload();
  await expect(main.getByRole("img", { name: /3 events/ })).toBeVisible();
  const created = application.waitForEvent("window");
  await main
    .getByRole("button", { name: "New plot window", exact: true })
    .click();
  const popup = await created;
  popup.on("pageerror", (error) => errors.push(error.message));
  await popup.setViewportSize({ width: 1540, height: 1050 });
  await popup.waitForLoadState("networkidle");
  const before = await popup.evaluate(() =>
    globalThis.cytoforgeDesktop.getPlotWindow(),
  );
  const requests = [];
  popup.on("request", (request) => {
    if (request.url().includes("/plot?")) requests.push(request.url());
  });
  await popup.evaluate(() => {
    globalThis.graphGlyphs = [];
    const draw = CanvasRenderingContext2D.prototype.fillText;
    CanvasRenderingContext2D.prototype.fillText = function (
      text,
      ...arguments_
    ) {
      globalThis.graphGlyphs.push({
        text: String(text),
        font: this.font,
        color: this.fillStyle,
      });
      return draw.call(this, text, ...arguments_);
    };
  });
  const settings = popup.locator("details.graph-settings");
  if (!(await settings.evaluate((element) => element.open)))
    await settings.locator("summary").first().click();
  const fonts = popup.locator("details.graph-font-settings");
  await fonts.locator("summary").click();
  await popup
    .getByLabel("Plot font target", { exact: true })
    .selectOption("axis_labels");
  await popup
    .getByLabel("Plot typeface", { exact: true })
    .selectOption("serif");
  await popup
    .getByLabel("Plot font size in points", { exact: true })
    .pressSequentially("18");
  await popup
    .getByLabel("Plot font weight", { exact: true })
    .selectOption("bold");
  await popup
    .getByLabel("Plot font style", { exact: true })
    .selectOption("italic");
  await popup.getByLabel("Plot text color", { exact: true }).fill("#112233");
  await expect
    .poll(() =>
      popup.evaluate(() =>
        globalThis.graphGlyphs.some(
          (glyph) =>
            glyph.font.includes("24px") &&
            glyph.font.includes("CytoForge Serif") &&
            glyph.color === "#112233",
        ),
      ),
    )
    .toBe(true);
  assert.equal(
    requests.length,
    0,
    "Font changes must not request scientific recomputation",
  );
  await expect
    .poll(() =>
      popup.evaluate(
        async () =>
          (await globalThis.cytoforgeDesktop.getPlotWindow()).graphOptions
            ?.typography?.axis_labels?.font_size_pt,
      ),
    )
    .toBe(18);
  const styled = await popup.evaluate(() =>
    globalThis.cytoforgeDesktop.getPlotWindow(),
  );
  assert.equal(styled.graphOptions.typography.axis_labels.font_size_pt, 18);
  assert.equal(styled.sampleId, before.sampleId);
  if (gateStyleTest) {
    await popup.evaluate(() => {
      globalThis.gateClipRules = [];
      const clip = CanvasRenderingContext2D.prototype.clip;
      CanvasRenderingContext2D.prototype.clip = function (...args) {
        globalThis.gateClipRules.push(args.at(-1));
        return clip.apply(this, args);
      };
    });
    await popup.locator("details.graph-gate-settings > summary").click();
    await popup
      .getByLabel("Gate border width (px)", { exact: true })
      .fill("4.5");
    await popup.getByLabel("Gate fill opacity", { exact: true }).fill("0.4");
    await popup.getByLabel("Show gate labels", { exact: true }).uncheck();
    await expect
      .poll(() =>
        popup.evaluate(
          async () =>
            (await globalThis.cytoforgeDesktop.getPlotWindow()).graphOptions
              ?.gate_style,
        ),
      )
      .toEqual({ line_width_px: 4.5, fill_opacity: 0.4, show_labels: false });
    await expect
      .poll(() =>
        popup.evaluate(
          () =>
            globalThis.gateClipRules.filter((rule) => rule === "evenodd")
              .length,
        ),
      )
      .toBeGreaterThanOrEqual(3);
    assert.equal(
      await main.getByLabel("Gate fill opacity", { exact: true }).inputValue(),
      "0",
    );
    assert.equal(
      requests.length,
      0,
      "Gate presentation must reuse scientific results",
    );
  }
  assert.equal(
    await main
      .getByLabel("Plot font size in points", { exact: true })
      .inputValue(),
    "",
  );
  await expect(popup.getByRole("img", { name: /3 events/ })).toBeVisible();
  const after = await request(popup, `/workspaces/${workspace.id}`);
  assert.deepEqual(
    after.samples.map((sample) => sample.sha256),
    workspace.samples.map((sample) => sample.sha256),
  );
  const isolation = await application.evaluate(({ app, BrowserWindow }) => ({
    packaged: app.isPackaged,
    sandbox_exception: app.commandLine.hasSwitch("no-sandbox"),
    windows: BrowserWindow.getAllWindows().map((window) => ({
      contextIsolation:
        window.webContents.getLastWebPreferences().contextIsolation,
      nodeIntegration:
        window.webContents.getLastWebPreferences().nodeIntegration,
    })),
  }));
  assert.ok(
    isolation.packaged &&
      isolation.windows.length >= 2 &&
      isolation.windows.every(
        (window) => window.contextIsolation && !window.nodeIntegration,
      ),
  );
  assert.equal(isolation.sandbox_exception, noSandbox);
  assert.deepEqual(errors, []);
  writeFileSync(
    output,
    JSON.stringify(
      {
        status: "passed",
        appimage_sha256: binaryHash,
        profile: path.relative(root, profile),
        actual_font_glyphs_verified: true,
        font_changes_do_not_refetch_science: true,
        native_popup_style_independence_verified: true,
        source_hashes_unchanged: true,
        ...(gateStyleTest ? { native_gate_style_workflow_verified: true } : {}),
        isolation,
      },
      null,
      2,
    ) + "\n",
  );
} catch (error) {
  writeFileSync(
    output,
    JSON.stringify(
      {
        status: "failed",
        appimage_sha256: binaryHash,
        error: error.stack,
        errors,
      },
      null,
      2,
    ) + "\n",
  );
  throw error;
} finally {
  await application?.close();
}
