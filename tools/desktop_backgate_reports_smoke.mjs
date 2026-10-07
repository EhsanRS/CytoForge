// Actual packaged desktop backgates, independent plot windows and ancestry reports.
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
      "artifacts/candidates/backgate-reports/desktop/CytoForge-0.1.0.AppImage",
    ),
);
assert.ok(binary.startsWith(root + path.sep) && binary.endsWith(".AppImage"));
const profile = path.join(root, `.tmp/desktop-backgate-reports-${Date.now()}`);
const output = path.join(
  root,
  "artifacts/desktop-backgate-reports-appimage.json",
);
mkdirSync(profile, { recursive: true });
const hash = createHash("sha256");
for await (const chunk of createReadStream(binary)) hash.update(chunk);
const binaryHash = hash.digest("hex");
const errors = [];
let application;
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
  const noSandbox = process.env.CYTOFORGE_TEST_NO_SANDBOX === "1";
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
  await main.setViewportSize({ width: 1600, height: 1100 });
  await main.waitForLoadState("networkidle");
  let workspace = await request(main, "/workspaces", {
    name: "Native backgate truth",
  });
  workspace = await main.evaluate(async (workspace) => {
    const { token } = await (await fetch("/api/bootstrap")).json();
    const form = new FormData();
    form.append(
      "files",
      new File(
        [
          "X,Y,Z\n0,0,0\n1,1,1\n2,2,2\n3,3,3\n4,4,4\n-1,-1,-1\nnan,1,1\n1,nan,1\n1,1,nan\n",
        ],
        "Known.csv",
        { type: "text/csv" },
      ),
    );
    const response = await fetch(
      `/api/workspaces/${workspace.id}/import?revision=${workspace.revision}`,
      { method: "POST", headers: { "X-CytoForge-Token": token }, body: form },
    );
    if (!response.ok) throw new Error(await response.text());
    return (await response.json()).workspace;
  }, workspace);
  const sample = workspace.samples[0];
  let parent = null;
  for (const [name, x, bounds] of [
    ["First", "X", [0, 4]],
    ["Second", "Y", [1, 4]],
    ["Interest", "Z", [2, 4]],
  ]) {
    workspace = await request(main, `/workspaces/${workspace.id}/gates`, {
      revision: workspace.revision,
      gate: {
        sample_id: sample.id,
        parent_id: parent,
        name,
        kind: "range",
        x,
        bounds,
        x_transform: { kind: "linear" },
      },
    });
    parent = workspace.gates.at(-1).id;
  }
  await main.evaluate(
    (workspace) => globalThis.cytoforgeDesktop.saveWorkspace(workspace.id),
    workspace,
  );
  await main.reload();
  await expect(main.getByRole("img", { name: /9 events/ })).toBeVisible();
  const opened = application.waitForEvent("window");
  await main
    .getByRole("button", { name: "New plot window", exact: true })
    .click();
  const popup = await opened;
  popup.on("pageerror", (error) => errors.push(error.message));
  await popup.waitForLoadState("networkidle");
  await popup
    .getByLabel("Backgate population", { exact: true })
    .selectOption(parent);
  await expect(popup.locator(".plot-footer")).toContainText(
    "2 backgate finite",
  );
  await expect
    .poll(
      async () =>
        (
          await popup.evaluate(() =>
            globalThis.cytoforgeDesktop.getPlotWindow(),
          )
        ).backgateId,
    )
    .toBe(parent);
  assert.equal(
    (await main.evaluate(() => globalThis.cytoforgeDesktop.getPlotWindow()))
      ?.backgateId ?? null,
    null,
  );
  await main
    .getByLabel("Backgate population", { exact: true })
    .selectOption(parent);
  await main
    .getByRole("button", { name: "Layout studio", exact: true })
    .click();
  await main
    .getByRole("button", { name: "Add current plot", exact: true })
    .click();
  await expect(
    main.getByLabel("Layer 1 backgate", { exact: true }),
  ).toHaveValue(parent);
  await main
    .getByLabel("Backgate ancestry orientation", { exact: true })
    .selectOption("vertical");
  await main
    .getByRole("button", { name: "Add backgate ancestry", exact: true })
    .click();
  await main.getByRole("button", { name: /^Save layout/ }).click();
  await expect
    .poll(
      async () =>
        (await request(main, `/workspaces/${workspace.id}`)).layouts[0]
          ?.elements.length,
    )
    .toBe(4);
  const saved = await request(main, `/workspaces/${workspace.id}`);
  const layout = saved.layouts[0];
  assert.ok(
    layout.elements.every((element) => element.plot.backgate_id === parent),
  );
  assert.deepEqual(
    layout.elements.slice(1).map((element) => element.plot.gate_id),
    [null, workspace.gates[0].id, workspace.gates[1].id],
  );
  const page = await request(
    main,
    `/workspaces/${workspace.id}/reports/render`,
    { revision: saved.revision, definition: layout, validate_sources: true },
  );
  assert.ok(page.exportable, JSON.stringify(page.issues));
  assert.equal(page.manifest.elements[0].layers[0].backgate.count, 2);
  assert.equal(saved.samples[0].sha256, sample.sha256);
  assert.equal(
    (await popup.evaluate(() => globalThis.cytoforgeDesktop.getPlotWindow()))
      .backgateId,
    parent,
  );
  assert.equal(errors.length, 0, errors.join("\n"));
  writeFileSync(
    output,
    JSON.stringify(
      {
        status: "passed",
        appimage_sha256: binaryHash,
        profile: path.relative(root, profile),
        native_popup_independence_verified: true,
        native_backgate_controls_and_footer_verified: true,
        saved_current_plot_backgate_verified: true,
        native_ancestry_controls_verified: true,
        saved_ancestry_and_report_provenance_verified: true,
        source_sha256_unchanged: true,
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
        error: error.message,
        profile: path.relative(root, profile),
      },
      null,
      2,
    ) + "\n",
  );
  throw error;
} finally {
  if (application) await application.close();
}
