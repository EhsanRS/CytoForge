// Actual packaged file chooser, known packed values and independent plot windows.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  createReadStream,
  mkdirSync,
  readFileSync,
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
      "artifacts/candidates/fcs-packed/desktop/CytoForge-0.1.0.AppImage",
    ),
);
assert.ok(binary.startsWith(root + path.sep) && binary.endsWith(".AppImage"));
const fixture = path.join(root, "artifacts/fcs-packed-fixture");
const truth = JSON.parse(
  readFileSync(path.join(fixture, "truth.json"), "utf8"),
);
assert.equal(truth.synthetic, true);
const profile = path.join(root, `.tmp/desktop-fcs-packed-${Date.now()}`);
const output = path.join(root, "artifacts/desktop-fcs-packed-appimage.json");
mkdirSync(profile, { recursive: true });
async function digest(file) {
  const hash = createHash("sha256");
  for await (const chunk of createReadStream(file)) hash.update(chunk);
  return hash.digest("hex");
}
const binaryHash = await digest(binary);
for (const [name, hash] of Object.entries(truth.files))
  assert.equal(await digest(path.join(fixture, name)), hash);
const errors = [];
const noSandbox = process.env.CYTOFORGE_TEST_NO_SANDBOX === "1";
let application;
async function launch() {
  return electron.launch({
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
}
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
function observe(page) {
  page.on("pageerror", (error) => errors.push(error.message));
}
try {
  application = await launch();
  assert.equal(await application.evaluate(({ app }) => app.isPackaged), true);
  let main = await application.firstWindow();
  observe(main);
  await main.setViewportSize({ width: 1600, height: 1100 });
  await main.waitForLoadState("networkidle");
  await main
    .getByRole("button", { name: "New experiment", exact: true })
    .click();
  await main
    .getByLabel("Experiment name", { exact: true })
    .fill("Synthetic packed native truth");
  await main
    .getByRole("button", { name: "Create workspace", exact: true })
    .click();
  const chooser = main.waitForEvent("filechooser");
  await main.getByRole("button", { name: "Import FCS", exact: true }).click();
  await (
    await chooser
  ).setFiles(Object.keys(truth.files).map((name) => path.join(fixture, name)));
  await expect(
    main.getByRole("heading", { name: "1 sample imported", exact: true }),
  ).toBeVisible({ timeout: 30000 });
  await expect(main.locator(".import-message.error")).toHaveCount(1);
  await expect(main.locator(".import-message.error")).toContainText(
    truth.expected_failure,
  );
  await main.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(main.getByRole("img", { name: /31 events/ })).toBeVisible();
  const workspaceId = await main.evaluate(() =>
    globalThis.cytoforgeDesktop.getWorkspace(),
  );
  let workspace = await request(main, `/workspaces/${workspaceId}`);
  assert.equal(workspace.samples.length, 1);
  const sample = workspace.samples[0];
  assert.equal(sample.sha256, truth.expected_numpy_sha256);
  const source = path.join(
    profile,
    "data/events",
    workspace.id,
    sample.id + ".npy",
  );
  assert.equal(await digest(source), truth.expected_numpy_sha256);
  assert.deepEqual(
    sample.channels.map((channel) => channel.name),
    truth.parameter_names,
  );
  assert.deepEqual(
    [1, 2, 3].map((i) => Number(sample.metadata[`p${i}b`])),
    truth.parameter_widths,
  );
  for (const [channel, mean] of [
    ["Y", 127.5],
    ["Time", 3.75],
  ]) {
    const stats = await request(
      main,
      `/workspaces/${workspace.id}/samples/${sample.id}/statistics?channel=${channel}`,
    );
    assert.equal(stats.count, 31);
    assert.ok(Math.abs(stats.mean - mean) <= 1e-12);
  }
  workspace = await request(main, `/workspaces/${workspace.id}/gates`, {
    revision: workspace.revision,
    gate: {
      sample_id: sample.id,
      name: truth.gate.name,
      kind: "range",
      x: "X",
      bounds: truth.gate.bounds,
      x_transform: { kind: "linear" },
    },
  });
  const gate = workspace.gates.at(-1);
  await main.reload();
  await expect(main.getByRole("img", { name: /31 events/ })).toBeVisible();
  const opened = application.waitForEvent("window");
  await main
    .getByRole("button", { name: "New plot window", exact: true })
    .click();
  const popup = await opened;
  observe(popup);
  await popup.waitForLoadState("networkidle");
  await popup
    .getByLabel("Backgate population", { exact: true })
    .selectOption(gate.id);
  await expect(popup.locator(".plot-footer")).toContainText(
    "12 backgate finite",
  );
  await main.locator(".gate-row").filter({ hasText: truth.gate.name }).click();
  await expect(main.getByRole("img", { name: /12 events/ })).toBeVisible();
  await expect(popup.getByRole("img", { name: /31 events/ })).toBeVisible();
  await expect
    .poll(
      async () =>
        (
          await popup.evaluate(() =>
            globalThis.cytoforgeDesktop.getPlotWindow(),
          )
        ).backgateId,
    )
    .toBe(gate.id);
  const stats = await request(
    main,
    `/workspaces/${workspace.id}/samples/${sample.id}/statistics?channel=X&gate_id=${gate.id}`,
  );
  assert.equal(stats.count, 12);
  assert.ok(Math.abs(stats.mean - 3) <= 1e-12);
  assert.equal(await digest(source), truth.expected_numpy_sha256);
  await application.close();
  application = await launch();
  main = await application.firstWindow();
  observe(main);
  await main.waitForLoadState("networkidle");
  assert.equal(
    await main.evaluate(() => globalThis.cytoforgeDesktop.getWorkspace()),
    workspace.id,
  );
  const restored = await request(main, `/workspaces/${workspace.id}`);
  assert.equal(restored.samples[0].id, sample.id);
  assert.equal(restored.gates[0].id, gate.id);
  assert.equal(await digest(source), truth.expected_numpy_sha256);
  assert.equal(errors.length, 0, errors.join("\n"));
  writeFileSync(
    output,
    JSON.stringify(
      {
        status: "passed",
        appimage_sha256: binaryHash,
        profile: path.relative(root, profile),
        actual_file_chooser_executed: true,
        known_values_and_units_verified: true,
        mixed_batch_preserves_valid_import: true,
        native_popup_independence_verified: true,
        backgate_count_verified: true,
        restart_identity_verified: true,
        actual_event_file_sha256_verified: true,
        headless_sandbox_override: noSandbox,
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
        profile: path.relative(root, profile),
        error: error.message,
      },
      null,
      2,
    ) + "\n",
  );
  throw error;
} finally {
  if (application) await application.close();
}
