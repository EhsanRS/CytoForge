// Actual packaged custom-plate controls, separate native acquisition windows and draft recovery.
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
const binary = realpathSync(
  process.env.CYTOFORGE_TEST_BINARY ??
    path.join(
      root,
      "artifacts/candidates/custom-plates/desktop/CytoForge-0.1.0.AppImage",
    ),
);
assert.ok(binary.startsWith(root + path.sep) && binary.endsWith(".AppImage"));
const profile = path.join(root, `.tmp/desktop-custom-plates-${Date.now()}`);
const output = path.join(root, "artifacts/desktop-custom-plates-appimage.json");
mkdirSync(path.join(root, "artifacts/screenshots"), { recursive: true });
const hash = createHash("sha256");
for await (const chunk of createReadStream(binary)) hash.update(chunk);
const binaryHash = hash.digest("hex");
let application, main;
const pageErrors = [];
const noSandbox = process.env.CYTOFORGE_TEST_NO_SANDBOX === "1";
async function launch() {
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
  main = await application.firstWindow();
  main.on("pageerror", (error) => pageErrors.push(error.message));
  await main.setViewportSize({ width: 1540, height: 1050 });
  await main.waitForLoadState("networkidle");
}
async function request(page, route, body, method = body ? "POST" : "GET") {
  return page.evaluate(
    async ({ route, body, method }) => {
      const { token } = await (await fetch("/api/bootstrap")).json();
      const response = await fetch(`/api${route}`, {
        method,
        headers: {
          "X-CytoForge-Token": token,
          "Content-Type": "application/json",
        },
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
      if (!response.ok) throw new Error(await response.text());
      return response.json();
    },
    { route, body, method },
  );
}
async function dimensions(rows, columns) {
  const details = main.locator("details.plate-custom-geometry");
  if (!(await details.evaluate((element) => element.open)))
    await details.locator("summary").click();
  await main
    .getByLabel("Custom plate rows", { exact: true })
    .fill(String(rows));
  await main
    .getByLabel("Custom plate columns", { exact: true })
    .fill(String(columns));
  await main
    .getByRole("button", { name: "Review custom dimensions", exact: true })
    .click();
  await expect(main.getByRole("dialog")).toBeVisible();
}
async function stage() {
  await main
    .getByRole("button", { name: "Stage reviewed plan", exact: true })
    .click();
  await main.getByRole("dialog").waitFor({ state: "hidden" });
}
async function savePlate() {
  const response = main.waitForResponse(
    (response) =>
      response.url().endsWith("/plates/save") &&
      response.request().method() === "POST",
  );
  await main.getByRole("button", { name: "Save plate", exact: true }).click();
  const saved = await response;
  assert.ok(saved.ok(), await saved.text());
  return saved.json();
}
try {
  await launch();
  let workspace = await request(main, "/workspaces", {
    name: "Native custom plate independent truth",
  });
  const fixtures = [
    { name: "First acquisition", csv: "X,Y\n1,2\n2,3\n3,4\n4,5\n" },
    { name: "Second acquisition", csv: "X,Y\n100,200\n200,300\n" },
    { name: "Outside acquisition", csv: "X,Y\n10,20\n20,30\n30,40\n" },
  ];
  for (const fixture of fixtures) {
    workspace = await main.evaluate(
      async ({ fixture, workspace }) => {
        const { token } = await (await fetch("/api/bootstrap")).json();
        const form = new FormData();
        form.append(
          "files",
          new File([fixture.csv], `${fixture.name}.csv`, { type: "text/csv" }),
        );
        const response = await fetch(
          `/api/workspaces/${workspace.id}/import?revision=${workspace.revision}`,
          {
            method: "POST",
            headers: { "X-CytoForge-Token": token },
            body: form,
          },
        );
        if (!response.ok) throw new Error(await response.text());
        return (await response.json()).workspace;
      },
      { fixture, workspace },
    );
  }
  const [first, second, third] = workspace.samples;
  assert.deepEqual(
    workspace.samples.map((sample) => sample.event_count),
    [4, 2, 3],
  );
  const rawHashes = workspace.samples.map((sample) => sample.sha256);
  const plate = {
    id: "a".repeat(32),
    name: "Native geometry truth",
    format: 96,
    assignments: { A01: [first.id, second.id], B03: [third.id] },
    annotations: { B03: { Dose: "0.25" } },
    columns: [
      { id: "b".repeat(32), name: "Events", statistic: "count", decimals: 0 },
    ],
  };
  workspace = await request(main, `/workspaces/${workspace.id}/plates/save`, {
    revision: workspace.revision,
    plate,
  });
  await main.evaluate(
    (workspace) => globalThis.cytoforgeDesktop.saveWorkspace(workspace.id),
    workspace,
  );
  await main.reload();
  await main.getByRole("button", { name: "Plates", exact: true }).click();
  await dimensions(2, 3);
  await stage();
  await expect(
    main.getByRole("table", { name: "6-well plate", exact: true }),
  ).toBeVisible();
  workspace = await savePlate();
  const savedPlate = workspace.plates.find((p) => p.id === plate.id);
  assert.deepEqual(savedPlate.geometry, { rows: 2, columns: 3 });
  const evaluation = await request(
    main,
    `/workspaces/${workspace.id}/plates/${plate.id}/evaluate`,
  );
  assert.equal(evaluation.wells.length, 6);
  assert.equal(evaluation.wells[0].values[plate.columns[0].id], 3);
  assert.equal(evaluation.wells[5].values[plate.columns[0].id], 3);
  await main.getByRole("button", { name: /^Well A01,/ }).click();
  const popups = [];
  for (const [sample, count] of [
    [first, 4],
    [second, 2],
  ]) {
    const created = application.waitForEvent("window");
    await main
      .getByRole("button", {
        name: `Open ${sample.name} in new plot window`,
        exact: true,
      })
      .click();
    const popup = await created;
    popup.on("pageerror", (error) => pageErrors.push(error.message));
    await popup.waitForLoadState("networkidle");
    await expect(
      popup.getByRole("img", {
        name: `density plot of X versus Y, ${count} events`,
        exact: true,
      }),
    ).toBeVisible();
    const state = await popup.evaluate(() =>
      globalThis.cytoforgeDesktop.getPlotWindow(),
    );
    assert.equal(state.sampleId, sample.id);
    assert.equal(state.gateId, null);
    assert.equal(state.groupId, null);
    assert.equal(state.pooled, false);
    popups.push(popup);
  }
  const native = await application.evaluate(({ app, BrowserWindow }) => ({
    packaged: app.isPackaged,
    windows: BrowserWindow.getAllWindows().map((window) =>
      window.webContents.getLastWebPreferences(),
    ),
  }));
  assert.equal(native.packaged, true);
  assert.equal(native.windows.length, 3);
  for (const preferences of native.windows) {
    assert.equal(preferences.contextIsolation, true);
    assert.equal(preferences.nodeIntegration, false);
    assert.equal(preferences.sandbox, !noSandbox);
  }
  for (const popup of popups) await popup.close();
  await dimensions(3, 2);
  await expect(main.getByRole("dialog")).toContainText("B03");
  await expect(main.getByRole("dialog")).toContainText(
    "1 acquisition assignments and 1 planned annotation keys",
  );
  // Leaving a reviewed resize un-staged must retain the saved grid and source.
  await main.getByRole("button", { name: "Cancel", exact: true }).click();
  assert.deepEqual(
    (await request(main, `/workspaces/${workspace.id}`)).plates[0].geometry,
    { rows: 2, columns: 3 },
  );
  const nativeDraft = await main.evaluate(
    (id) => globalThis.cytoforgeDesktop.getPlateDraft(id),
    workspace.id,
  );
  assert.deepEqual(JSON.parse(nativeDraft).plate.geometry, {
    rows: 2,
    columns: 3,
  });
  await main.screenshot({
    path: path.join(root, "artifacts/screenshots/desktop-custom-plates.png"),
  });
  await application.close();
  application = null;
  await launch();
  await main.getByRole("button", { name: "Plates", exact: true }).click();
  await expect(
    main.getByRole("table", { name: "6-well plate", exact: true }),
  ).toBeVisible();
  const restored = await request(main, `/workspaces/${workspace.id}`);
  assert.deepEqual(restored.plates[0].geometry, { rows: 2, columns: 3 });
  assert.deepEqual(
    restored.samples.map((sample) => sample.sha256),
    rawHashes,
  );
  assert.deepEqual(pageErrors, []);
  writeFileSync(
    output,
    JSON.stringify(
      {
        status: "passed",
        binary,
        binary_sha256: binaryHash,
        isPackaged: true,
        custom_dimensions: [2, 3],
        complete_wells: 6,
        independent_native_acquisition_counts: [4, 2],
        native_window_count: 3,
        context_isolation: true,
        node_integration: false,
        sandbox: !noSandbox,
        resize_review_and_cancel_verified: true,
        native_draft_and_restart_verified: true,
        original_acquisition_hashes_unchanged: true,
        page_errors: pageErrors,
      },
      null,
      2,
    ) + "\n",
  );
  console.log(
    "Passed packaged native custom plate geometry, individual windows and draft recovery.",
  );
} catch (error) {
  if (main)
    await main
      .screenshot({
        path: path.join(
          root,
          "artifacts/screenshots/desktop-custom-plates-failure.png",
        ),
      })
      .catch(() => {});
  writeFileSync(
    output,
    JSON.stringify(
      {
        status: "failed",
        binary,
        binary_sha256: binaryHash,
        error: String(error),
        page_errors: pageErrors,
      },
      null,
      2,
    ) + "\n",
  );
  throw error;
} finally {
  if (application) await application.close();
}
