import { _electron as electron } from "playwright";
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import process from "node:process";
import { autospillFixtures } from "./autospill_fixture.mjs";
import { desktopKineticsSmoke } from "./desktop_kinetics_smoke.mjs";
import { desktopPlateSmoke } from "./desktop_plate_smoke.mjs";

const root = process.cwd();
const profile = path.join(root, `.tmp/desktop-smoke-${Date.now()}`);
mkdirSync(path.join(root, "artifacts/screenshots"), { recursive: true });
const reportPath =
  process.env.CYTOFORGE_TEST_REPORT_PATH ??
  path.join(root, "artifacts/desktop-smoke.json");
const binary = process.env.CYTOFORGE_TEST_BINARY;
const args = binary ? [] : ["."];
if (process.platform === "linux")
  args.push("--headless", "--ozone-platform=headless", "--disable-gpu");
// Explicit isolated test override; production launch never adds this switch.
if (process.env.CYTOFORGE_TEST_NO_SANDBOX === "1") args.push("--no-sandbox");
const launchOptions = {
  executablePath: binary,
  chromiumSandbox: process.env.CYTOFORGE_TEST_NO_SANDBOX !== "1",
  args,
  env: {
    ...process.env,
    CYTOFORGE_HEADLESS_TEST: "1",
    CYTOFORGE_HOME: profile,
  },
  timeout: 30000,
};
async function launchDesktop() {
  const application = await electron.launch(launchOptions);
  // Preserve sidecar exceptions if a packaged worker fails before writing its report.
  await application.evaluate(() => {
    globalThis.cytoforgeSmokeEngineStderr = "";
    for (const handle of process._getActiveHandles()) {
      if (handle.spawnargs?.includes("--parent-pid"))
        handle.stderr?.on("data", (data) => {
          globalThis.cytoforgeSmokeEngineStderr = (
            globalThis.cytoforgeSmokeEngineStderr + data.toString()
          ).slice(-65536);
        });
    }
  });
  return application;
}
writeFileSync(
  reportPath,
  JSON.stringify({
    binary: binary ?? "development Electron",
    status: "running",
    profile,
  }),
);
let desktop;
const deadline = setTimeout(() => desktop?.process().kill(), 180000);
try {
  desktop = await launchDesktop();
  const window = await desktop.firstWindow({ timeout: 20000 });
  await window.setViewportSize({ width: 1540, height: 1050 });
  await window.waitForLoadState("networkidle");
  const explore = window.getByRole("button", { name: /Explore the PBMC demo/ });
  if (await explore.isVisible()) await explore.click();
  await window.getByRole("img", { name: /density plot/ }).waitFor();
  await window.locator('canvas[data-ready="true"]').waitFor();
  await window.screenshot({
    path: path.join(root, "artifacts/screenshots/desktop-workbench.png"),
  });
  const evidence = await desktop.evaluate(({ app, BrowserWindow }) => {
    const window = BrowserWindow.getAllWindows()[0];
    return {
      window_count: BrowserWindow.getAllWindows().length,
      preferences: window.webContents.getLastWebPreferences(),
      no_sandbox: app.commandLine.hasSwitch("no-sandbox"),
    };
  });
  if (
    evidence.preferences.nodeIntegration ||
    !evidence.preferences.contextIsolation ||
    !evidence.preferences.sandbox
  )
    throw new Error("Renderer isolation is incorrect");
  if (evidence.no_sandbox !== (process.env.CYTOFORGE_TEST_NO_SANDBOX === "1"))
    throw new Error(
      "Desktop sandbox flags differ from the explicit test setting",
    );
  await window.getByRole("button", { name: "Discovery", exact: true }).click();
  const name = `Desktop PCA ${Date.now()}`;
  await window.getByLabel("Analysis name", { exact: true }).fill(name);
  await window.getByLabel("Maximum fitted events").fill("1000");
  await window.getByRole("button", { name: "Run PCA", exact: true }).click();
  const card = window.locator(".analysis-job").filter({
    has: window.getByRole("heading", { name, exact: true }),
  });
  await card
    .getByRole("button", { name: "Add parameters", exact: true })
    .waitFor({ timeout: 25000 });
  await card
    .getByRole("button", { name: "Add parameters", exact: true })
    .click();
  await card
    .getByRole("button", { name: "Explore result", exact: true })
    .click();
  await window.locator('canvas[data-ready="true"]').waitFor();
  if (!(await window.getByLabel("X axis channel").inputValue()).includes(name))
    throw new Error("The desktop did not select its computed parameter");
  await window.screenshot({
    path: path.join(root, "artifacts/screenshots/desktop-pca.png"),
  });
  if (
    (await window
      .locator(".main-content")
      .evaluate((element) => element.scrollTop)) > 1
  )
    throw new Error(
      "Exploring the result did not restore the population view to the top",
    );
  await window
    .getByRole("button", { name: "Acquisition QC", exact: true })
    .click();
  await window
    .getByLabel("Run name", { exact: true })
    .fill(`Desktop QC ${Date.now()}`);
  await window
    .getByRole("button", { name: "Run acquisition QC", exact: true })
    .click();
  await window
    .getByRole("button", { name: "Create reviewed populations", exact: true })
    .waitFor({ timeout: 25000 });
  await window
    .getByRole("button", { name: "Create reviewed populations", exact: true })
    .click();
  await window
    .getByRole("button", { name: "Open saved clean population", exact: true })
    .waitFor();
  await window.locator(".main-content").evaluate((element) => {
    element.scrollTop = 0;
  });
  await window.screenshot({
    path: path.join(root, "artifacts/screenshots/desktop-qc.png"),
  });
  await window
    .getByRole("button", { name: "Open saved clean population", exact: true })
    .click();
  await window.locator('canvas[data-ready="true"]').waitFor();
  const dna = await window.evaluate(async () => {
    const { token } = await (await fetch("/api/bootstrap")).json();
    const headers = { "X-CytoForge-Token": token };
    const create = await fetch("/api/workspaces", {
      method: "POST",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify({ name: `Desktop DNA ${Date.now()}` }),
    });
    if (!create.ok) throw new Error(await create.text());
    let doc = await create.json();
    let seed = 9512;
    const uniform = () => {
      seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
      return (seed + 0.5) / 4294967296;
    };
    const normal = () =>
      Math.sqrt(-2 * Math.log(uniform())) * Math.cos(2 * Math.PI * uniform());
    const rows = ["DNA,Time"];
    for (let i = 0; i < 5000; i++) {
      const phase = i % 10;
      const truth = phase < 5 ? 100 : phase < 8 ? 100 + 98 * uniform() : 198;
      rows.push(`${truth + truth * 0.04 * normal()},${i}`);
    }
    const form = new FormData();
    form.append(
      "files",
      new File([rows.join("\n")], "desktop-dna.csv", { type: "text/csv" }),
    );
    const imported = await fetch(
      `/api/workspaces/${doc.id}/import?revision=0`,
      { method: "POST", headers, body: form },
    );
    if (!imported.ok) throw new Error(await imported.text());
    doc = (await imported.json()).workspace;
    globalThis.cytoforgeDesktop?.saveWorkspace(doc.id);
    if ((await globalThis.cytoforgeDesktop?.getWorkspace()) !== doc.id)
      throw new Error("Desktop did not remember its DNA workspace");
    localStorage.setItem("cytoforge.workspace", doc.id);
    return { id: doc.id };
  });
  await window.reload();
  await window.getByRole("button", { name: "Cell cycle", exact: true }).click();
  await window.getByLabel("Model name", { exact: true }).fill("Desktop DJF");
  await window
    .getByRole("button", { name: "Fit cell cycle", exact: true })
    .click();
  await window
    .getByRole("img", { name: /full-event DNA histogram/ })
    .waitFor({ timeout: 25000 });
  await window
    .getByLabel("I reviewed the phase curves, constraints and fit warnings.")
    .check();
  await window
    .getByRole("button", { name: "Save fit to workspace", exact: true })
    .click();
  await window
    .getByRole("button", { name: "Explore S", exact: true })
    .waitFor();
  const dnaEvidence = await window.evaluate(async (id) => {
    const { token } = await (await fetch("/api/bootstrap")).json();
    const response = await fetch(`/api/workspaces/${id}`, {
      headers: { "X-CytoForge-Token": token },
    });
    const doc = await response.json();
    return doc.cell_cycle_results[0].fits[0];
  }, dna.id);
  if (
    dnaEvidence.data.fitted_count !== 5000 ||
    dnaEvidence.fractions.some(
      (f, i) => Math.abs(f - [0.5, 0.3, 0.2][i]) > 0.035,
    )
  )
    throw new Error("Desktop DJF did not recover the known latent phases");
  await window.locator(".main-content").evaluate((element) => {
    element.scrollTop = 0;
  });
  await window.screenshot({
    path: path.join(root, "artifacts/screenshots/desktop-cell-cycle.png"),
  });
  await window
    .getByRole("img", { name: /full-event DNA histogram/ })
    .screenshot({
      path: path.join(root, "artifacts/screenshots/desktop-cell-cycle-fit.png"),
    });
  await window.getByRole("button", { name: "Explore S", exact: true }).click();
  await window.locator('canvas[data-ready="true"]').waitFor();
  const cfse = await window.evaluate(async (id) => {
    const { token } = await (await fetch("/api/bootstrap")).json();
    const headers = { "X-CytoForge-Token": token };
    let doc = await (await fetch(`/api/workspaces/${id}`, { headers })).json();
    let seed = 56792;
    const uniform = () => {
      seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
      return (seed + 0.5) / 4294967296;
    };
    const normal = () =>
      Math.sqrt(-2 * Math.log(uniform())) * Math.cos(2 * Math.PI * uniform());
    const rows = ["CFSE,Time"];
    for (let i = 0; i < 6000; i++) {
      const generation = i % 10 < 1 ? 0 : i % 10 < 3 ? 1 : i % 10 < 6 ? 2 : 3;
      rows.push(
        `${20 + 1024 * 0.5 ** generation * Math.exp(Math.sqrt(Math.log1p(0.2 ** 2)) * normal())},${i}`,
      );
    }
    const form = new FormData();
    form.append(
      "files",
      new File([rows.join("\n")], "desktop-cfse.csv", { type: "text/csv" }),
    );
    const imported = await fetch(
      `/api/workspaces/${id}/import?revision=${doc.revision}`,
      {
        method: "POST",
        headers,
        body: form,
      },
    );
    if (!imported.ok) throw new Error(await imported.text());
    doc = (await imported.json()).workspace;
    return { id: doc.samples.at(-1).id };
  }, dna.id);
  await window.reload();
  await window.getByRole("button", { name: /^desktop-cfse.csv/ }).click();
  await window
    .getByRole("button", { name: "Proliferation", exact: true })
    .click();
  await window
    .getByLabel("Proliferation model name", { exact: true })
    .fill("Desktop CFSE");
  await window
    .getByLabel("Generation-zero intensity initial value", { exact: true })
    .fill("1044");
  await window.getByLabel("Background intensity", { exact: true }).fill("20");
  await window.getByLabel("Last generation", { exact: true }).fill("3");
  await window
    .getByRole("button", { name: "Fit proliferation", exact: true })
    .click();
  await window
    .getByRole("img", {
      name: "Generation model histogram and residuals",
      exact: true,
    })
    .waitFor({ timeout: 25000 });
  await window
    .getByLabel("I reviewed the proliferation fit", { exact: true })
    .check();
  await window
    .getByRole("button", { name: "Save proliferation model", exact: true })
    .click();
  await window
    .getByRole("button", { name: "Explore generation 2", exact: true })
    .waitFor();
  const proliferationEvidence = await window.evaluate(async (id) => {
    const { token } = await (await fetch("/api/bootstrap")).json();
    const doc = await (
      await fetch(`/api/workspaces/${id}`, {
        headers: { "X-CytoForge-Token": token },
      })
    ).json();
    return doc.proliferation_results.at(-1).fits[0];
  }, dna.id);
  if (
    proliferationEvidence.sample_id !== cfse.id ||
    proliferationEvidence.data.fitted_count !== 6000 ||
    proliferationEvidence.fractions.some(
      (f, i) => Math.abs(f - [0.1, 0.2, 0.3, 0.4][i]) > 0.025,
    )
  )
    throw new Error(
      "Desktop proliferation did not recover the known latent generations",
    );
  await window
    .getByRole("button", { name: "Expand review", exact: true })
    .click();
  await window.locator(".population-chart").screenshot({
    path: path.join(
      root,
      "artifacts/screenshots/desktop-proliferation-fit.png",
    ),
  });
  await window
    .getByRole("button", { name: "Explore generation 2", exact: true })
    .click();
  await window.locator('canvas[data-ready="true"]').waitFor();
  const modelIds = await window.evaluate(async (id) => {
    const { token } = await (await fetch("/api/bootstrap")).json();
    const doc = await (
      await fetch(`/api/workspaces/${id}`, {
        headers: { "X-CytoForge-Token": token },
      })
    ).json();
    return {
      dna: doc.cell_cycle_results.at(-1).id,
      proliferation: doc.proliferation_results.at(-1).id,
    };
  }, dna.id);
  await window.getByRole("button", { name: "Statistics", exact: true }).click();
  await window
    .getByRole("button", { name: "Custom tables", exact: true })
    .click();
  await window
    .getByLabel("Custom table name", { exact: true })
    .fill("Desktop biology responses");
  await window.getByRole("button", { name: "Add column", exact: true }).click();
  await window
    .getByLabel("Custom column name", { exact: true })
    .fill("G1 fraction");
  await window
    .getByLabel("Custom column type", { exact: true })
    .selectOption("biology");
  await window
    .getByLabel("Table biology platform", { exact: true })
    .selectOption("cell-cycle");
  await window
    .getByLabel("Table biological model", { exact: true })
    .selectOption(modelIds.dna);
  await window.getByRole("button", { name: "Add column", exact: true }).click();
  await window
    .getByLabel("Custom column name", { exact: true })
    .fill("Precursor frequency");
  await window
    .getByLabel("Custom column type", { exact: true })
    .selectOption("biology");
  await window
    .getByLabel("Table biological model", { exact: true })
    .selectOption(modelIds.proliferation);
  const customSave = window.getByRole("button", {
    name: "Save custom table",
    exact: true,
  });
  await customSave.click();
  await window.waitForFunction(
    () =>
      !!document.querySelector('select[aria-label="Custom saved table"]')
        ?.value,
  );
  const tableEvidence = await window.evaluate(async (id) => {
    const { token } = await (await fetch("/api/bootstrap")).json();
    const headers = { "X-CytoForge-Token": token };
    const doc = await (
      await fetch(`/api/workspaces/${id}`, { headers })
    ).json();
    const table = doc.tables.find(
      (t) => t.name === "Desktop biology responses",
    );
    const response = await fetch(
      `/api/workspaces/${id}/tables/${table.id}/evaluate`,
      { headers },
    );
    if (!response.ok) throw new Error(await response.text());
    return await response.json();
  }, dna.id);
  const dnaRow = tableEvidence.rows.find(
    (r) => r.sample_id === dnaEvidence.sample_id,
  );
  const cfseRow = tableEvidence.rows.find((r) => r.sample_id === cfse.id);
  if (
    Math.abs(
      dnaRow.values[tableEvidence.columns[1].id] - dnaEvidence.fractions[0],
    ) > 1e-12 ||
    Math.abs(
      cfseRow.values[tableEvidence.columns[2].id] -
        proliferationEvidence.statistics.precursor_frequency,
    ) > 1e-12
  )
    throw new Error("Desktop biological table differs from its saved models");
  const nativeTable = window.getByRole("table", {
    name: "Custom statistics table",
    exact: true,
  });
  await nativeTable
    .getByRole("row")
    .filter({ hasText: "desktop-dna.csv" })
    .getByText(dnaEvidence.fractions[0].toFixed(2), { exact: true })
    .waitFor();
  mkdirSync(path.join(profile, "exports"), { recursive: true });
  await desktop.evaluate(
    ({ session }, target) => {
      globalThis.cytoforgeSmokeDownload = new Promise((resolve, reject) => {
        session.defaultSession.once("will-download", (_event, item) => {
          item.setSavePath(target);
          item.once("done", (_event, state) => {
            if (state === "completed") resolve(item.getSavePath());
            else reject(new Error(`Desktop download ${state}`));
          });
        });
      });
    },
    path.join(profile, "exports/custom-table.xlsx"),
  );
  await window.getByRole("button", { name: "XLSX", exact: true }).click();
  const tableDownload = await desktop.evaluate(
    () => globalThis.cytoforgeSmokeDownload,
  );
  copyFileSync(
    tableDownload,
    path.join(root, "artifacts/desktop-custom-table.xlsx"),
  );
  if (
    readFileSync(path.join(root, "artifacts/desktop-custom-table.xlsx"))
      .subarray(0, 2)
      .toString() !== "PK"
  )
    throw new Error("Desktop did not export an XLSX spreadsheet");
  await window.locator(".custom-table-result").screenshot({
    path: path.join(root, "artifacts/screenshots/desktop-custom-table.png"),
  });
  const aspFixture = autospillFixtures();
  const aspSamples = await window.evaluate(
    async ({ id, files }) => {
      const { token } = await (await fetch("/api/bootstrap")).json();
      const headers = { "X-CytoForge-Token": token };
      let doc = await (
        await fetch(`/api/workspaces/${id}`, { headers })
      ).json();
      const ids = [];
      for (const file of files) {
        const form = new FormData();
        form.append(
          "files",
          new File([file.csv], `${file.name}.csv`, { type: "text/csv" }),
        );
        const response = await fetch(
          `/api/workspaces/${id}/import?revision=${doc.revision}`,
          { method: "POST", headers, body: form },
        );
        if (!response.ok) throw new Error(await response.text());
        doc = (await response.json()).workspace;
        ids.push(doc.samples.at(-1).id);
      }
      return ids;
    },
    {
      id: dna.id,
      files: aspFixture.files.map(({ name, csv }) => ({ name, csv })),
    },
  );
  await window.reload();
  await window.getByRole("button", { name: /^D1 single stain.csv/ }).click();
  await window
    .getByRole("button", { name: "Compensation", exact: true })
    .click();
  await window
    .getByRole("button", { name: "Control wizard", exact: true })
    .click();
  const wizard = window.getByRole("dialog");
  await wizard.getByLabel("Estimation method").selectOption("autospill");
  const aspName = `Desktop AutoSpill ${Date.now()}`;
  await wizard.getByLabel("AutoSpill matrix name").fill(aspName);
  await wizard.getByLabel("AutoSpill autofluorescence subtraction").check();
  for (let i = 0; i < 3; i++)
    await wizard
      .getByLabel(`AutoSpill control ${i + 1} sample`, { exact: true })
      .selectOption(aspSamples[i]);
  await wizard
    .getByRole("button", { name: "Run AutoSpill", exact: true })
    .click();
  await wizard
    .getByText("Converged", { exact: true })
    .waitFor({ timeout: 30000 });
  await wizard
    .getByRole("img", { name: "AutoSpill convergence history", exact: true })
    .waitFor();
  await wizard
    .getByText("Inspect automatic scatter cleanup", { exact: true })
    .click();
  await wizard
    .getByRole("img", { name: "Automatic scatter cleanup", exact: true })
    .locator("polygon")
    .waitFor();
  await wizard.evaluate((element) => {
    element.scrollTop = 0;
  });
  await window.screenshot({
    path: path.join(root, "artifacts/screenshots/desktop-autospill.png"),
  });
  await desktop.evaluate(
    ({ session }, target) => {
      globalThis.cytoforgeSmokeDownload = new Promise((resolve, reject) => {
        session.defaultSession.once("will-download", (_event, item) => {
          item.setSavePath(target);
          item.once("done", (_event, state) =>
            state === "completed"
              ? resolve(item.getSavePath())
              : reject(new Error(`Desktop download ${state}`)),
          );
        });
      });
    },
    path.join(profile, "exports/autospill.json"),
  );
  await wizard
    .getByRole("button", { name: "Calculation report", exact: true })
    .click();
  const aspDownload = await desktop.evaluate(
    () => globalThis.cytoforgeSmokeDownload,
  );
  copyFileSync(
    aspDownload,
    path.join(root, "artifacts/desktop-autospill.json"),
  );
  const aspReport = JSON.parse(readFileSync(aspDownload, "utf8"));
  if (
    !aspReport.diagnostics.converged ||
    aspReport.diagnostics.final_max_error >= aspReport.request.tolerance
  )
    throw new Error("Desktop AutoSpill did not reach its requested tolerance");
  for (let i = 0; i < 3; i++)
    for (let j = 0; j < 3; j++)
      if (
        Math.abs(
          aspReport.compensation.matrix[i][j] - aspFixture.matrix[i][j],
        ) >= 0.005
      )
        throw new Error(
          "Desktop AutoSpill differs from the independently generated physical matrix",
        );
  await wizard
    .locator(".control-targets")
    .getByLabel("Mixture.csv", { exact: true })
    .check();
  await wizard
    .getByRole("button", { name: "Save and apply matrix", exact: true })
    .click();
  await wizard.waitFor({ state: "hidden" });
  const prior = await window.evaluate(() => ({
    workspace: localStorage.getItem("cytoforge.workspace"),
    origin: location.origin,
    bridge: !!globalThis.cytoforgeDesktop,
  }));
  if (!prior.bridge || !prior.workspace)
    throw new Error("Desktop workspace persistence bridge is unavailable");
  await desktop.close();
  desktop = await launchDesktop();
  const reopened = await desktop.firstWindow({ timeout: 20000 });
  await reopened.getByRole("img", { name: /density plot/ }).waitFor();
  const restored = await reopened.evaluate(async () => ({
    workspace: localStorage.getItem("cytoforge.workspace"),
    origin: location.origin,
    bridgeWorkspace: await globalThis.cytoforgeDesktop?.getWorkspace(),
  }));
  if (
    restored.origin === prior.origin ||
    restored.workspace !== prior.workspace ||
    restored.bridgeWorkspace !== prior.workspace
  )
    throw new Error(
      "Desktop failed to restore its workspace after the engine port changed",
    );
  await reopened
    .getByRole("button", { name: "Statistics", exact: true })
    .click();
  await reopened
    .getByRole("button", { name: "Custom tables", exact: true })
    .click();
  await reopened
    .getByLabel("Custom saved table", { exact: true })
    .selectOption(tableEvidence.definition.id);
  await reopened
    .getByRole("table", { name: "Custom statistics table", exact: true })
    .waitFor();
  await reopened
    .getByRole("button", { name: "Proliferation", exact: true })
    .click();
  await reopened
    .getByRole("table", { name: "Generation counts", exact: true })
    .waitFor();
  await reopened.getByText("Saved model", { exact: true }).waitFor();
  await reopened
    .getByRole("button", { name: "Cell cycle", exact: true })
    .click();
  await reopened
    .getByRole("img", { name: /full-event DNA histogram/ })
    .waitFor();
  await reopened
    .getByRole("button", { name: "Compensation", exact: true })
    .click();
  await reopened
    .getByRole("button", { name: "Control wizard", exact: true })
    .click();
  const reopenedWizard = reopened.getByRole("dialog");
  await reopenedWizard
    .getByLabel("Estimation method")
    .selectOption("autospill");
  await reopenedWizard
    .getByText("Previous calculations and saved matrices", { exact: true })
    .click();
  await reopenedWizard
    .getByRole("button", { name: new RegExp(aspName) })
    .click();
  await reopenedWizard.getByText("Converged", { exact: true }).waitFor();
  await reopenedWizard
    .getByRole("img", { name: "Compensated control", exact: true })
    .waitFor();
  await reopenedWizard
    .getByRole("button", { name: "Close dialog", exact: true })
    .click();
  const kineticsEvidence = await desktopKineticsSmoke(
    desktop,
    reopened,
    root,
    profile,
  );
  const plateEvidence = await desktopPlateSmoke(
    desktop,
    reopened,
    root,
    profile,
  );
  await desktop.close();
  const closedDraft = JSON.parse(
    readFileSync(
      path.join(
        profile,
        ".config",
        "plate-drafts",
        `${plateEvidence.workspace}.json`,
      ),
      "utf8",
    ),
  );
  if (closedDraft.plate.name !== "Recovered desktop plate draft")
    throw new Error("Closing the desktop did not flush its latest plate edit");
  desktop = await launchDesktop();
  const plateReopened = await desktop.firstWindow({ timeout: 20000 });
  await plateReopened
    .getByRole("button", { name: "Plates", exact: true })
    .click();
  await plateReopened
    .locator('.plate-grid-status[data-ready="true"]')
    .waitFor();
  if (
    (await plateReopened
      .getByLabel("Saved plate", { exact: true })
      .inputValue()) !== plateEvidence.plate_id
  )
    throw new Error(
      "Desktop plate definition was not restored after engine restart",
    );
  await plateReopened
    .locator('[data-well="A01"] svg ellipse')
    .first()
    .waitFor();
  await plateReopened.waitForFunction(
    () =>
      document.querySelector('[aria-label="Plate name"]')?.value ===
      "Recovered desktop plate draft",
  );
  await plateReopened
    .getByRole("button", { name: "Save plate", exact: true })
    .click();
  let cacheRemoved = false;
  for (let attempt = 0; attempt < 100; attempt++) {
    const content = await plateReopened.evaluate(
      async (workspace) => globalThis.cytoforgeDesktop.getPlateDraft(workspace),
      plateEvidence.workspace,
    );
    if (content === null) {
      cacheRemoved = true;
      break;
    }
    await plateReopened.waitForTimeout(50);
  }
  if (!cacheRemoved)
    throw new Error("Saving the desktop plate did not clear its draft cache");
  if (
    await plateReopened.evaluate(async () =>
      globalThis.cytoforgeDesktop.savePlateDraft("../../outside", null),
    )
  )
    throw new Error("Desktop draft bridge accepted an invalid workspace ID");
  const restoredKinetics = await plateReopened.evaluate(
    async ({ workspace, result_id }) => {
      const { token } = await (await fetch("/api/bootstrap")).json();
      const response = await fetch(
        `/api/workspaces/${workspace}/kinetics/${result_id}`,
        { headers: { "X-CytoForge-Token": token } },
      );
      if (!response.ok) throw new Error(await response.text());
      return response.json();
    },
    kineticsEvidence,
  );
  if (
    restoredKinetics.stale ||
    Math.abs(restoredKinetics.fits[0].ranges[0].auc - 168) > 1e-12
  )
    throw new Error(
      "Desktop kinetics report failed restoration across engine ports",
    );
  kineticsEvidence.restored_across_engine_port_change = true;
  plateEvidence.unsaved_draft_restored_across_engine_port_change = true;
  plateEvidence.draft_removed_after_save = true;
  plateEvidence.definition_restored_across_engine_port_change = true;
  writeFileSync(
    reportPath,
    JSON.stringify(
      {
        binary: binary ?? "development Electron",
        status: "passed",
        validated_at: new Date().toISOString(),
        headless_sandbox_exception:
          process.env.CYTOFORGE_TEST_NO_SANDBOX === "1",
        renderer_context_isolation: evidence.preferences.contextIsolation,
        renderer_node_integration: evidence.preferences.nodeIntegration,
        renderer_sandbox: evidence.preferences.sandbox,
        effective_no_sandbox_switch: evidence.no_sandbox,
        ran_and_plotted: ["pca", "quality", "djf", "proliferation"],
        cell_cycle: {
          fitted_events: dnaEvidence.data.fitted_count,
          fractions: dnaEvidence.fractions,
        },
        proliferation: {
          fitted_events: proliferationEvidence.data.fitted_count,
          fractions: proliferationEvidence.fractions,
          statistics: proliferationEvidence.statistics,
        },
        workspace_restored_across_engine_port_change: true,
        biological_models_reviewed_after_restart: true,
        custom_tables: {
          biological_metrics_checked: true,
          xlsx_downloaded: true,
          definition_restored_across_engine_port_change: true,
          headless_download_dialog_redirected: true,
        },
        autospill: {
          autofluorescence: true,
          scatter_cleanup_reviewed: true,
          final_residual: aspReport.diagnostics.final_max_error,
          iterations: aspReport.diagnostics.iterations,
          physical_matrix_tolerance: 0.005,
          report_downloaded: true,
          assigned: true,
          review_restored_after_engine_port_change: true,
          headless_download_dialog_redirected: true,
        },
        kinetics: kineticsEvidence,
        plates: plateEvidence,
      },
      null,
      2,
    ),
  );
  console.log(
    "Desktop ran/plotted PCA, QC, DJF, proliferation, AutoSpill, kinetics and plates, exported XLSX/reports/SVG, and restored its models, tables, matrix review and unsaved plate draft across engine port changes.",
  );
} catch (error) {
  const diagnostic = {
    binary: binary ?? "development Electron",
    status: "failed",
    validated_at: new Date().toISOString(),
    profile,
    error: String(error),
  };
  try {
    diagnostic.engine_stderr = await desktop.evaluate(
      () => globalThis.cytoforgeSmokeEngineStderr,
    );
    const currentWindow = desktop.windows()[0];
    if (currentWindow) {
      diagnostic.visible_jobs = await currentWindow
        .locator(".analysis-job")
        .allTextContents();
      diagnostic.alerts = await currentWindow
        .getByRole("alert")
        .allTextContents();
      await currentWindow.screenshot({
        path: path.join(
          root,
          "artifacts/screenshots/desktop-smoke-failure.png",
        ),
      });
    }
  } catch (captureError) {
    diagnostic.capture_error = String(captureError);
  }
  writeFileSync(reportPath, JSON.stringify(diagnostic, null, 2));
  throw error;
} finally {
  clearTimeout(deadline);
  await desktop?.close();
}
