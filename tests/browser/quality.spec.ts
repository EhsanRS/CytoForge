import { test, expect, type APIRequestContext } from "@playwright/test";
import { mkdirSync, readFileSync } from "node:fs";

async function acquisition(request: APIRequestContext) {
  const { token } = await (await request.get("/api/bootstrap")).json();
  const headers = { "X-CytoForge-Token": token };
  let doc = await (
    await request.post("/api/workspaces", {
      headers,
      data: { name: "Acquisition QC validation" },
    })
  ).json();
  let time = 0;
  const rows = ["Time,Marker,FSC-A,FSC-H"];
  for (let i = 0; i < 10000; i++) {
    const marker =
      i === 82
        ? "NaN"
        : 100 + (i % 500) / 50 + (i >= 4000 && i < 4500 ? 200 : 0);
    const height = 50 + (i % 37) / 10;
    const area = height * (i === 50 ? 4 : 2);
    rows.push(`${time},${marker},${area},${height}`);
    time += i >= 6000 && i < 6500 ? 0.01 : 0.001;
  }
  const imported = await request.post(
    `/api/workspaces/${doc.id}/import?revision=${doc.revision}`,
    {
      headers,
      multipart: {
        files: {
          name: "Known acquisition.csv",
          mimeType: "text/csv",
          buffer: Buffer.from(rows.join("\n")),
        },
      },
    },
  );
  expect(imported.ok(), await imported.text()).toBeTruthy();
  doc = (await imported.json()).workspace;
  return { doc, headers };
}

test("acquisition QC reviews exact intervals, pulses, identities and archived populations", async ({
  page,
  request,
}) => {
  mkdirSync("artifacts/screenshots", { recursive: true });
  const { doc, headers } = await acquisition(request);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.addInitScript(
    (id) => localStorage.setItem("cytoforge.workspace", id),
    doc.id,
  );
  await page.goto("/");
  await page
    .getByRole("button", { name: "Acquisition QC", exact: true })
    .click();
  await page.getByLabel("Run name", { exact: true }).fill("QC browser");
  await page.getByLabel("Use channel transforms for signals").uncheck();
  await page.getByLabel("Inspect area / height pulse ratio").check();
  await page
    .getByRole("button", { name: "Run acquisition QC", exact: true })
    .click();
  await expect(page.getByTestId("qc-retained-count")).toHaveText("8,999");
  await expect(
    page.getByLabel("Exclude interval 9", { exact: true }),
  ).toBeChecked();
  await expect(
    page.getByLabel("Exclude interval 13", { exact: true }),
  ).toBeChecked();
  await page.getByLabel("QC trace").selectOption("Marker");
  await expect(
    page.getByRole("group", { name: "Marker acquisition trace" }),
  ).toBeVisible();
  await page.getByLabel("Exclude interval 9", { exact: true }).uncheck();
  await expect(page.getByTestId("qc-retained-count")).toHaveText("9,499");
  await page.getByLabel("Pulse-ratio outliers").check();
  await expect(page.getByTestId("qc-retained-count")).toHaveText("9,498");
  await expect(
    page.getByRole("img", { name: "Pulse area versus height preview" }),
  ).toBeVisible();
  // Exporting a draft uses the visible review, including overlapping event exclusions.
  const reportDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "QC report", exact: true }).click();
  const reportFile = "artifacts/qc-browser-report.json";
  await (await reportDownload).saveAs(reportFile);
  const report = JSON.parse(readFileSync(reportFile, "utf8"));
  expect(report.review_draft.choices.excluded_bins).toEqual([12]);
  expect(report.review_draft.counts.retained_count).toBe(9498);
  const eventDownload = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "Event identities CSV", exact: true })
    .click();
  const eventFile = "artifacts/qc-browser-events.csv";
  await (await eventDownload).saveAs(eventFile);
  const events = readFileSync(eventFile, "utf8").trim().split("\n");
  expect(events[0]).toContain("review_keep");
  expect(events).toHaveLength(10001);
  expect(
    events.slice(1).reduce((n, row) => n + Number(row.split(",").at(-1)), 0),
  ).toBe(9498);
  await page
    .getByRole("button", { name: "Create reviewed populations", exact: true })
    .click();
  await expect(
    page.getByRole("button", {
      name: "Update reviewed populations",
      exact: true,
    }),
  ).toBeEnabled();
  let saved = await (
    await request.get(`/api/workspaces/${doc.id}`, { headers })
  ).json();
  expect(saved.quality_results).toHaveLength(1);
  expect(saved.gates.map((g: { kind: string }) => g.kind)).toEqual([
    "quality",
    "quality",
  ]);
  const ids = saved.gates.map((g: { id: string }) => g.id);
  await page.screenshot({
    path: "artifacts/screenshots/acquisition-qc.png",
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "Open saved clean population", exact: true })
    .click();
  await expect(
    page.getByRole("heading", {
      name: "QC browser · Clean",
      exact: true,
      level: 1,
    }),
  ).toBeVisible();
  await expect(page.getByRole("img", { name: /density plot/ })).toBeVisible();
  const archive = await request.get(
    `/api/workspaces/${doc.id}/export/project`,
    { headers },
  );
  expect(archive.ok()).toBeTruthy();
  const restore = await request.post("/api/import/project", {
    headers,
    multipart: {
      file: {
        name: "qc.cytoforge",
        mimeType: "application/zip",
        buffer: await archive.body(),
      },
    },
  });
  expect(restore.ok(), await restore.text()).toBeTruthy();
  const restored = await restore.json();
  await page.reload();
  await page
    .getByRole("combobox", { name: "Active workspace" })
    .selectOption(restored.id);
  await page
    .getByRole("button", { name: "Acquisition QC", exact: true })
    .click();
  await page
    .locator(".quality-run-select")
    .filter({ hasText: "QC browser" })
    .click();
  await expect(page.getByTestId("qc-retained-count")).toHaveText("9,498");
  await page
    .getByRole("button", { name: "Keep all intervals", exact: true })
    .click();
  await page.getByLabel("Pulse-ratio outliers").uncheck();
  await expect(page.getByTestId("qc-retained-count")).toHaveText("9,999");
  await page
    .getByRole("button", { name: "Update reviewed populations", exact: true })
    .click();
  await expect
    .poll(async () => {
      saved = await (
        await request.get(`/api/workspaces/${restored.id}`, { headers })
      ).json();
      return saved.gates[0].quality_excluded_bins;
    })
    .toEqual([]);
  expect(saved.gates.map((g: { id: string }) => g.id)).toEqual(ids);
  const counts = await (
    await request.get(
      `/api/workspaces/${restored.id}/samples/${saved.samples[0].id}/counts`,
      { headers },
    )
  ).json();
  expect(
    counts
      .map((c: { count: number }) => c.count)
      .sort((a: number, b: number) => a - b),
  ).toEqual([1, 9999]);
  expect(errors).toEqual([]);
});

test("QC results expose changed transforms and prevent a stale cleanup apply", async ({
  page,
  request,
}) => {
  const { doc, headers } = await acquisition(request);
  await page.addInitScript(
    (id) => localStorage.setItem("cytoforge.workspace", id),
    doc.id,
  );
  await page.goto("/");
  await page
    .getByRole("button", { name: "Acquisition QC", exact: true })
    .click();
  await page.getByLabel("Run name", { exact: true }).fill("Stale QC");
  await page
    .getByRole("button", { name: "Run acquisition QC", exact: true })
    .click();
  await expect(
    page.getByRole("button", {
      name: "Create reviewed populations",
      exact: true,
    }),
  ).toBeEnabled();
  const change = await request.post(`/api/workspaces/${doc.id}/transforms`, {
    headers,
    data: {
      revision: doc.revision,
      channel: "Marker",
      transform: { kind: "asinh", cofactor: 5 },
      sample_ids: [doc.samples[0].id],
    },
  });
  expect(change.ok(), await change.text()).toBeTruthy();
  await page.reload();
  await page
    .getByRole("button", { name: "Acquisition QC", exact: true })
    .click();
  await page
    .locator(".quality-run-select")
    .filter({ hasText: "Stale QC" })
    .click();
  await expect(
    page.getByText(/The scientific inputs changed after this run/),
  ).toBeVisible();
  await expect(
    page.getByRole("button", {
      name: "Create reviewed populations",
      exact: true,
    }),
  ).toBeDisabled();
  const saved = await (
    await request.get(`/api/workspaces/${doc.id}`, { headers })
  ).json();
  expect(saved.gates).toHaveLength(0);
});
