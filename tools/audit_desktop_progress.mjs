// Read-only audit before replacing the supervised preview; no workspace writes.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  readFileSync,
  readdirSync,
  readlinkSync,
  writeFileSync,
} from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { chromium } from "playwright";
const { validState } = createRequire(import.meta.url)(
  "../desktop/plot-windows.cjs",
);
const root = process.cwd();
const output =
  process.argv[2] ||
  "artifacts/desktop-preview-before-population-history-promotion.json";
assert(path.resolve(output).startsWith(root + path.sep));
const session = JSON.parse(
  readFileSync("artifacts/desktop-preview-session.json", "utf8"),
);
assert.equal(session.status, "running");
const health = await (await fetch("http://127.0.0.1:8001/health")).json();
assert(health.running && health.desktop && health.packaged);
const owners = readdirSync("/proc")
  .filter((p) => /^\d+$/.test(p) && p !== "2677623")
  .filter((p) => {
    try {
      const argv = readFileSync(`/proc/${p}/cmdline`, "utf8").split("\0");
      return (
        argv[0].endsWith("node") &&
        argv[1] === "tools/progress-preview.mjs" &&
        readlinkSync(`/proc/${p}/cwd`) === root
      );
    } catch {
      return false;
    }
  });
assert.equal(
  owners.length,
  1,
  "There must be one exact owned preview controller",
);
const port = readFileSync(
  ".tmp/desktop-progress/.cache/desktop-profile/DevToolsActivePort",
  "utf8",
).split("\n")[0];
const desktop = await chromium.connectOverCDP(`http://127.0.0.1:${port}`, {
  noDefaults: true,
});
try {
  const pages = desktop
    .contexts()[0]
    .pages()
    .filter((p) => p.url().startsWith("http://127.0.0.1:"));
  const main = pages.find(
    (p) => !/[?&](plotWindow|comparisonWindow)=/.test(p.url()),
  );
  assert(main);
  const workspaceId = await main.evaluate(() =>
    globalThis.cytoforgeDesktop.getWorkspace(),
  );
  const nativeSession = await main.evaluate(() =>
    typeof globalThis.cytoforgeDesktop.getPlotSession === "function"
      ? globalThis.cytoforgeDesktop.getPlotSession()
      : null,
  );
  let mainView = nativeSession?.state ?? null;
  if (!nativeSession)
    mainView = await main.evaluate((workspaceId) => {
      // The older release has no session-read IPC. Inspect the current renderer's
      // existing display descriptor without modifying its React tree or state.
      const element = document.getElementById("root")?.firstElementChild;
      const property =
        element &&
        Object.keys(element).find((k) => k.startsWith("__reactFiber$"));
      let fiber = property ? element[property] : null;
      while (fiber?.return) fiber = fiber.return;
      fiber = fiber?.stateNode?.current ?? fiber;
      const stack = fiber ? [fiber] : [],
        seen = new Set();
      while (stack.length && seen.size < 20000) {
        const current = stack.pop();
        if (!current || seen.has(current)) continue;
        seen.add(current);
        let hook = current.memoizedState,
          count = 0;
        while (hook && typeof hook === "object" && count++ < 1000) {
          const value = Array.isArray(hook.memoizedState)
            ? hook.memoizedState[0]
            : null;
          if (
            value?.workspaceId === workspaceId &&
            value.sampleId &&
            value.x &&
            value.mode &&
            Object.hasOwn(value, "gateId")
          )
            return JSON.parse(JSON.stringify(value));
          hook = hook.next;
        }
        if (current.child) stack.push(current.child);
        if (current.sibling) stack.push(current.sibling);
      }
      return null;
    }, workspaceId);
  if (mainView)
    assert(
      validState(mainView),
      "Captured main view must pass native descriptor validation",
    );
  const guards = [];
  const workspaces = new Set(workspaceId ? [workspaceId] : []);
  for (const page of pages) {
    const context = await page.evaluate(() =>
      globalThis.cytoforgeDesktop.getPlotWindow(),
    );
    if (context) workspaces.add(context.workspaceId);
    const comparison = await page.evaluate(() =>
      typeof globalThis.cytoforgeDesktop.getComparisonWindow === "function"
        ? globalThis.cytoforgeDesktop.getComparisonWindow()
        : null,
    );
    if (comparison) workspaces.add(comparison.workspaceId);
    const plotSession = await page.evaluate(() =>
      typeof globalThis.cytoforgeDesktop.getPlotSession === "function"
        ? globalThis.cytoforgeDesktop.getPlotSession()
        : null,
    );
    guards.push({
      title: await page.title(),
      visible_dialogs: await page.locator('[role="dialog"]:visible').count(),
      partial_polygon: await page.locator(".polygon-actions:visible").count(),
      partial_drag: await page
        .locator(
          ".draw-overlay rect, .draw-overlay ellipse, .draw-overlay path",
        )
        .count(),
      box_editor: await page.locator(".three-d-box-editor:visible").count(),
      dirty: plotSession?.dirty ?? false,
    });
  }
  const cookie = readFileSync(".config/desktop-preview-token", "utf8").trim();
  const publicState = await (
    await fetch("http://127.0.0.1:8001/state", {
      headers: { Cookie: "cytoforge_desktop_preview=" + cookie },
    })
  ).json();
  const request = (route) =>
    main.evaluate(async (route) => {
      const { token } = await (await fetch("/api/bootstrap")).json();
      const response = await fetch("/api" + route, {
        headers: { "X-CytoForge-Token": token },
      });
      if (
        response.status === 404 &&
        route.endsWith("/population-comparison/jobs")
      )
        return [];
      if (!response.ok) throw new Error(await response.text());
      return response.json();
    }, route);
  const jobs = [];
  for (const id of workspaces)
    for (const suffix of [
      "/jobs",
      "/cell-cycle/jobs",
      "/proliferation/jobs",
      "/kinetics/jobs",
      "/population-comparison/jobs",
      "/quality/jobs",
      "/compensations/autospill/jobs",
    ]) {
      const values = await request("/workspaces/" + id + suffix);
      assert(Array.isArray(values));
      jobs.push(
        ...values
          .filter(
            (v) => !["completed", "failed", "cancelled"].includes(v.status),
          )
          .map((v) => ({
            workspace: id,
            endpoint: suffix,
            id: v.id,
            status: v.status,
          })),
      );
    }
  const doc = workspaceId ? await request("/workspaces/" + workspaceId) : null;
  const evidence = {
    status: "passed",
    checked_at: new Date().toISOString(),
    preview_pid: Number(owners[0]),
    desktop_pid: session.desktop_pid,
    private_engine_pid: session.private_engine_pid,
    original_workspace_id: workspaceId,
    original_workspace_revision: doc?.revision ?? null,
    original_workspace_samples: doc?.samples.length ?? 0,
    original_workspace_sha256: doc
      ? createHash("sha256").update(JSON.stringify(doc)).digest("hex")
      : null,
    main_view: mainView,
    native_windows: pages.length,
    guards,
    active_jobs: jobs,
    file_chooser: publicState.fileChooser ?? null,
  };
  if (
    guards.some(
      (g) =>
        g.visible_dialogs ||
        g.partial_polygon ||
        g.partial_drag ||
        g.box_editor ||
        g.dirty,
    ) ||
    jobs.length ||
    evidence.file_chooser
  )
    evidence.status = "waiting_for_user_work";
  writeFileSync(output, JSON.stringify(evidence, null, 2));
  console.log(
    JSON.stringify({
      status: evidence.status,
      preview_pid: evidence.preview_pid,
      native_windows: evidence.native_windows,
      main_view_captured: !!mainView,
      active_jobs: jobs.length,
      evidence: output,
    }),
  );
  assert.equal(
    evidence.status,
    "passed",
    "The preview currently has unsaved input or active work; preserve it and retry the audit later",
  );
} finally {
  await desktop.close();
}
