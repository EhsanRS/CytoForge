// Isolated native desktop comparison workflow, window independence and restoration.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { _electron as electron, expect } from "playwright/test";

const root = process.cwd(),
  profile = path.join(
    root,
    ".tmp",
    `desktop-population-comparison-${Date.now()}`,
  );
const binary = process.env.CYTOFORGE_TEST_BINARY;
const output =
  process.env.CYTOFORGE_COMPARISON_EVIDENCE ||
  "artifacts/desktop-population-comparison-source.json";
assert(path.resolve(output).startsWith(root + path.sep));
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
const errors = [],
  save = () => writeFileSync(output, JSON.stringify(evidence, null, 2) + "\n");
const passed = (check) => {
  evidence.checks.push(check);
  save();
};
let application, main;
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
    packaged: app.isPackaged,
    headless: app.commandLine.hasSwitch("headless"),
    disable_gpu: app.commandLine.hasSwitch("disable-gpu"),
    no_sandbox: app.commandLine.hasSwitch("no-sandbox"),
  }));
  assert.equal(switches.packaged, !!binary);
  assert(switches.headless && switches.disable_gpu);
  assert.equal(
    switches.no_sandbox,
    process.env.CYTOFORGE_TEST_NO_SANDBOX === "1",
  );
  evidence.native_command_line_switches = switches;
  evidence.sandbox_exception = switches.no_sandbox;
  evidence.scope =
    "Linux x64 native desktop, explicit isolated headless exception, hardware GPU disabled";
  main = await application.firstWindow();
  main.on("pageerror", (e) => errors.push(e.message));
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
      const value = await response.json();
      if (method !== "GET" && value.id && Array.isArray(value.samples))
        globalThis.cytoforgeDesktop.notifyWorkspaceChanged(value.id);
      return value;
    },
    { route, body, method },
  );
}
async function importCSV(doc, name, values) {
  return main.evaluate(
    async ({ doc, name, values }) => {
      const { token } = await (await fetch("/api/bootstrap")).json();
      const form = new FormData();
      form.append(
        "files",
        new Blob(["X,Y\n" + values.map((v) => `${v},${v}`).join("\n")]),
        name,
      );
      const response = await fetch(
        `/api/workspaces/${doc.id}/import?revision=${doc.revision}`,
        { method: "POST", headers: { "X-CytoForge-Token": token }, body: form },
      );
      if (!response.ok) throw new Error(await response.text());
      return (await response.json()).workspace;
    },
    { doc, name, values },
  );
}
async function close() {
  const closed = application.waitForEvent("close");
  await application.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows()
      .find(
        (w) =>
          !/[?&](plotWindow|comparisonWindow)=/.test(w.webContents.getURL()),
      )
      ?.close(),
  );
  await closed;
}
try {
  await launch();
  let doc = await request("/workspaces", {
    name: "Native comparison validation",
  });
  doc = await importCSV(doc, "Target.csv", [0, 0, 1, 1, 1, 2, 3, 3, 3, 3]);
  doc = await importCSV(doc, "Control.csv", [0, 0, 0, 0, 1, 1, 1, 1, 2, 2]);
  const original = JSON.parse(JSON.stringify(doc));
  await main.evaluate(
    (id) => globalThis.cytoforgeDesktop.saveWorkspace(id),
    doc.id,
  );
  await expect
    .poll(() => main.evaluate(() => globalThis.cytoforgeDesktop.getWorkspace()))
    .toBe(doc.id);
  await main.reload();
  await main
    .getByRole("button", { name: "Population comparison", exact: true })
    .click();
  await main
    .getByLabel("Comparison name", { exact: true })
    .fill("Native ENS reference");
  await main.getByLabel("Probability bins", { exact: true }).fill("4");
  await main
    .getByLabel("Minimum probability bin events", { exact: true })
    .fill("1");
  const started = main.waitForResponse(
    (r) =>
      r.url().endsWith("/population-comparison/jobs") &&
      r.request().method() === "POST",
  );
  await main
    .getByRole("button", { name: "Run comparison", exact: true })
    .click();
  const created = await (await started).json();
  await expect(
    main.getByRole("button", {
      name: "Review complete · Save comparison",
      exact: true,
    }),
  ).toBeEnabled({ timeout: 30000 });
  const base = `/workspaces/${doc.id}/population-comparison`,
    result = await request(`${base}/${created.id}`);
  assert.equal(result.rows[0].metrics.overton_cumulative_percent, 40);
  assert(Math.abs(result.rows[0].metrics.ens_percent - 44) < 1e-9);
  assert.deepEqual(
    (await request(`/workspaces/${doc.id}`)).samples,
    original.samples,
  );
  passed(
    "GUI calculation uses actual sources, known ENS-1 truth and changes no acquired events or workspace",
  );
  await main
    .getByRole("button", {
      name: "Review complete · Save comparison",
      exact: true,
    })
    .click();
  await expect(
    main.getByRole("button", { name: "Open comparison window", exact: true }),
  ).toBeVisible();
  doc = await request(`/workspaces/${doc.id}`);
  assert.equal(doc.comparison_results.length, 1);
  assert.equal(doc.revision, original.revision + 1);
  assert.deepEqual(doc.samples, original.samples);
  assert.deepEqual(doc.gates, original.gates);
  passed(
    "Review saves one undo step with source channels, populations and acquisitions preserved",
  );
  const opened = application.waitForEvent("window");
  await main
    .getByRole("button", { name: "Open comparison window", exact: true })
    .click();
  const popup = await opened;
  popup.on("pageerror", (e) => errors.push(e.message));
  await expect(
    popup.getByRole("heading", { name: "Native ENS reference", exact: true }),
  ).toBeVisible();
  await popup
    .getByLabel("Comparison graph mode", { exact: true })
    .selectOption("cdf");
  await popup
    .getByLabel("Comparison control tint", { exact: true })
    .fill("#ee5544");
  await expect(
    main.getByLabel("Comparison graph mode", { exact: true }),
  ).toHaveValue("histogram");
  await expect(
    main.getByLabel("Comparison control tint", { exact: true }),
  ).toHaveValue("#38d9ba");
  passed(
    "Native popup holds independent graph mode and tints alongside the main workspace",
  );
  const state = await popup.evaluate(() =>
    globalThis.cytoforgeDesktop.getComparisonWindow(),
  );
  assert.equal(state.mode, "cdf");
  assert.equal(state.controlColor, "#ee5544");
  const blocked = await popup.evaluate(async (s) => {
    const { id: ignored, ...view } = s;
    return {
      bad_workspace: await globalThis.cytoforgeDesktop.updateComparisonWindow({
        ...view,
        workspaceId: "f".repeat(32),
      }),
      bad_parameter: await globalThis.cytoforgeDesktop.updateComparisonWindow({
        ...view,
        parameterId: "f".repeat(32),
      }),
    };
  }, state);
  assert.equal(blocked.bad_workspace, false);
  assert.equal(blocked.bad_parameter, false);
  const isolation = await application.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows()
      .filter((w) => /comparisonWindow=/.test(w.webContents.getURL()))
      .map((w) => w.webContents.getLastWebPreferences()),
  );
  assert(
    isolation.every(
      (w) =>
        w.sandbox && w.contextIsolation && !w.nodeIntegration && w.webSecurity,
    ),
  );
  passed(
    "Comparison IPC validates ownership and parameter identity; the renderer stays isolated",
  );
  await popup.screenshot({
    path: "artifacts/screenshots/population-comparison-native.png",
  });
  await close();
  await launch();
  const restored =
    application.windows().find((p) => p.url().includes("comparisonWindow=")) ??
    (await application.waitForEvent("window", {
      predicate: (p) => p.url().includes("comparisonWindow="),
    }));
  restored.on("pageerror", (e) => errors.push(e.message));
  await expect(
    restored.getByLabel("Comparison graph mode", { exact: true }),
  ).toHaveValue("cdf");
  await expect(
    restored.getByLabel("Comparison control tint", { exact: true }),
  ).toHaveValue("#ee5544");
  passed(
    "Saved comparison, native window bounds and independent presentation restore after desktop restart",
  );
  assert.deepEqual(
    (await request(`/workspaces/${doc.id}`)).samples,
    original.samples,
  );
  assert.equal(errors.length, 0, errors.join("; "));
  evidence.renderer_errors = errors;
  await close();
  evidence.status = "passed";
  save();
} catch (error) {
  evidence.status = "failed";
  evidence.error = error.stack || String(error);
  evidence.renderer_errors = errors;
  save();
  try {
    await application?.close();
  } catch {}
  throw error;
}
