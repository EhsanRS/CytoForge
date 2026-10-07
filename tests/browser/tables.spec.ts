import { expect, test } from "@playwright/test";
import { mkdirSync, readFileSync } from "node:fs";
import { randomUUID } from "node:crypto";

test("custom tables preserve column bindings, compare samples, export spreadsheets and restore definitions", async ({
  page,
  request,
}) => {
  mkdirSync("artifacts/screenshots", { recursive: true });
  const { token } = await (await request.get("/api/bootstrap")).json();
  const headers = { "X-CytoForge-Token": token };
  let doc = await (
    await request.post("/api/workspaces", {
      headers,
      data: { name: "Table editor validation" },
    })
  ).json();
  for (const [i, value] of [1, 2, 4, 6, 8, 11].entries()) {
    const csv =
      "X,Y\n" +
      [value - 1, value, value + 1, value + 2, value + 3, "NaN"]
        .map((x, j) => `${x},${j}`)
        .join("\n");
    const imported = await request.post(
      `/api/workspaces/${doc.id}/import?revision=${doc.revision}`,
      {
        headers,
        multipart: {
          files: {
            name: `Sample ${i}.csv`,
            mimeType: "text/csv",
            buffer: Buffer.from(csv),
          },
        },
      },
    );
    expect(imported.ok(), await imported.text()).toBeTruthy();
    doc = (await imported.json()).workspace;
    const sample = doc.samples.at(-1);
    const annotated = await request.patch(
      `/api/workspaces/${doc.id}/samples/${sample.id}`,
      {
        headers,
        data: {
          revision: doc.revision,
          name: `Sample ${i}`,
          tags: { Treatment: i < 3 ? "A" : "B", Donor: String(i % 3) },
        },
      },
    );
    expect(annotated.ok()).toBeTruthy();
    doc = await annotated.json();
    const gate = await request.post(`/api/workspaces/${doc.id}/gates`, {
      headers,
      data: {
        revision: doc.revision,
        gate: {
          id: randomUUID().replaceAll("-", ""),
          sample_id: sample.id,
          name: "Cells",
          kind: "range",
          x: "Y",
          bounds: [1, 5],
        },
      },
    });
    expect(gate.ok(), await gate.text()).toBeTruthy();
    doc = await gate.json();
  }
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.addInitScript(
    (id) => localStorage.setItem("cytoforge.workspace", id),
    doc.id,
  );
  await page.goto("/");
  await page.getByRole("button", { name: "Statistics", exact: true }).click();
  await page
    .getByRole("button", { name: "Custom tables", exact: true })
    .click();
  await page
    .getByLabel("Custom table name", { exact: true })
    .fill("Response table");
  await page
    .getByLabel("Custom column population", { exact: true })
    .selectOption('["Cells"]');
  async function add(name: string) {
    await page.getByRole("button", { name: "Add column", exact: true }).click();
    await page.getByLabel("Custom column name", { exact: true }).fill(name);
  }
  await add("Median");
  await page
    .getByLabel("Custom column statistic", { exact: true })
    .selectOption("median");
  await page
    .getByLabel("Custom column parameter", { exact: true })
    .selectOption("X");
  await page
    .getByLabel("Custom column population", { exact: true })
    .selectOption('["Cells"]');
  await page.getByLabel("Table column heatmap", { exact: true }).check();
  await add("Treatment");
  await page
    .getByLabel("Custom column type", { exact: true })
    .selectOption("metadata");
  await page.getByLabel("Custom keyword", { exact: true }).fill("Treatment");
  await add("Relative");
  await page
    .getByLabel("Custom column type", { exact: true })
    .selectOption("formula");
  await page
    .getByLabel("Custom table formula", { exact: true })
    .fill('col("Median") / mean(col("Median"))');
  await add("Helper");
  await page
    .getByLabel("Custom column type", { exact: true })
    .selectOption("formula");
  await page
    .getByLabel("Custom table formula", { exact: true })
    .fill('col("Median") * 2');
  await page.getByLabel("Hide table column", { exact: true }).check();
  const save = page.getByRole("button", {
    name: "Save custom table",
    exact: true,
  });
  await expect(save).toBeEnabled();
  await page
    .getByRole("button", { name: "Edit table column Median", exact: true })
    .click();
  await page
    .getByLabel("Custom column name", { exact: true })
    .fill("Unsaved signal");
  await expect(
    page
      .getByRole("table", { name: "Custom statistics table", exact: true })
      .getByRole("columnheader", { name: "Unsaved signal", exact: true }),
  ).toBeVisible();
  await expect(save).toBeEnabled();
  await save.click();
  await expect(
    page.getByLabel("Custom saved table", { exact: true }),
  ).not.toHaveValue("");
  await page
    .getByRole("button", {
      name: "Edit table column Unsaved signal",
      exact: true,
    })
    .click();
  await page.getByLabel("Custom column name", { exact: true }).fill("Signal");
  await expect(
    page
      .getByRole("table", { name: "Custom statistics table", exact: true })
      .getByRole("columnheader", { name: "Signal", exact: true }),
  ).toBeVisible();
  await expect(save).toBeEnabled();
  let releaseSave!: () => void;
  const heldSave = new Promise<void>((resolve) => {
    releaseSave = resolve;
  });
  await page.route(
    `**/api/workspaces/${doc.id}/tables/save`,
    async (route) => {
      await heldSave;
      await route.continue();
    },
    { times: 1 },
  );
  await save.click();
  try {
    for (const name of ["CSV", "XLSX", "JSON"])
      await expect(
        page.getByRole("button", { name, exact: true }),
      ).toBeDisabled();
  } finally {
    releaseSave();
  }
  await expect(save).toBeEnabled();
  const data = page.getByRole("table", {
    name: "Custom statistics table",
    exact: true,
  });
  await expect(data.getByRole("row")).toHaveCount(7);
  await expect(
    data.getByRole("columnheader", { name: "Helper", exact: true }),
  ).toHaveCount(0);
  await expect(
    data.getByRole("row").filter({ hasText: "Sample 0" }),
  ).toContainText("2.50");
  const firstRow = data.getByRole("row").filter({ hasText: "Sample 0" });
  await expect(firstRow.getByRole("cell").nth(1)).not.toHaveAttribute(
    "style",
    /background-color/,
  );
  await expect(firstRow.getByRole("cell").nth(2)).toHaveAttribute(
    "style",
    /background-color/,
  );
  const csvDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "CSV", exact: true }).click();
  await (await csvDownload).saveAs("artifacts/custom-table.csv");
  const lines = readFileSync("artifacts/custom-table.csv", "utf8")
    .trim()
    .split("\n");
  expect(lines).toHaveLength(7);
  expect(lines[0]).toContain("Signal");
  expect(lines[0]).not.toContain("Helper");
  await page.getByRole("button", { name: "Pivot", exact: true }).click();
  await page.getByLabel("Enable table pivot", { exact: true }).check();
  await page.getByLabel("Pivot rows: Treatment", { exact: true }).check();
  await page.getByLabel("Pivot measures: Events", { exact: true }).uncheck();
  await page.getByLabel("Pivot measures: Signal", { exact: true }).check();
  await expect(
    page
      .getByRole("table", { name: "Custom pivot table", exact: true })
      .getByRole("row"),
  ).toHaveCount(3);
  await page.getByRole("button", { name: "Comparisons", exact: true }).click();
  await page.getByLabel("Enable table comparisons", { exact: true }).check();
  await page.getByLabel("Comparison group A", { exact: true }).fill("A");
  await page.getByLabel("Comparison group B", { exact: true }).fill("B");
  await page
    .getByLabel("Comparison measures: Events", { exact: true })
    .uncheck();
  await page.getByLabel("Comparison measures: Signal", { exact: true }).check();
  await page
    .getByLabel("Comparison measures: Relative", { exact: true })
    .check();
  await expect(
    page
      .getByRole("table", { name: "Sample comparison results", exact: true })
      .getByRole("row"),
  ).toHaveCount(3);
  await expect(save).toBeEnabled();
  await save.click();
  await expect(save).toBeEnabled();
  const jsonDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "JSON", exact: true }).click();
  await (await jsonDownload).saveAs("artifacts/custom-table.json");
  const report = JSON.parse(
    readFileSync("artifacts/custom-table.json", "utf8"),
  );
  expect(report.comparisons[0].n_a).toBe(3);
  expect(report.comparisons[0].n_b).toBe(3);
  expect(report.comparisons[0].mean_difference).toBeCloseTo(-6);
  expect(report.comparisons[0].p_value).toBeGreaterThan(0);
  expect(report.comparisons[0].adjusted_p_value).toBeCloseTo(
    2 * report.comparisons[0].p_value,
    8,
  );
  expect(report.pivot.rows).toHaveLength(2);
  const xlsxDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "XLSX", exact: true }).click();
  await (await xlsxDownload).saveAs("artifacts/custom-table.xlsx");
  expect(
    readFileSync("artifacts/custom-table.xlsx").subarray(0, 2).toString(),
  ).toBe("PK");
  await page.locator(".main-content").evaluate((e) => {
    e.scrollTop = 0;
  });
  await page.screenshot({
    path: "artifacts/screenshots/custom-table-comparisons.png",
    fullPage: true,
  });
  await page
    .getByRole("table", { name: "Sample comparison results", exact: true })
    .screenshot({
      path: "artifacts/screenshots/custom-table-comparison-results.png",
    });
  await page.getByRole("button", { name: "Data", exact: true }).click();
  await page
    .locator(".custom-table-result")
    .screenshot({ path: "artifacts/screenshots/custom-table-data.png" });
  const archive = await request.get(
    `/api/workspaces/${doc.id}/export/project`,
    { headers },
  );
  const restored = await request.post("/api/import/project", {
    headers,
    multipart: {
      file: {
        name: "tables.cytoforge",
        mimeType: "application/zip",
        buffer: await archive.body(),
      },
    },
  });
  expect(restored.ok(), await restored.text()).toBeTruthy();
  const restoredDoc = await restored.json();
  await page.evaluate(
    (id) => localStorage.setItem("cytoforge.workspace", id),
    restoredDoc.id,
  );
  await page.reload();
  await page.getByRole("button", { name: "Statistics", exact: true }).click();
  await page
    .getByRole("button", { name: "Custom tables", exact: true })
    .click();
  await page
    .getByLabel("Custom saved table", { exact: true })
    .selectOption(report.definition.id);
  await expect(
    page
      .getByRole("table", { name: "Custom statistics table", exact: true })
      .getByRole("row"),
  ).toHaveCount(7);
  await page
    .getByRole("button", { name: "Remove saved table", exact: true })
    .click();
  await expect(
    page
      .getByLabel("Custom saved table", { exact: true })
      .getByRole("option", { name: "Response table", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(
    page
      .getByLabel("Custom saved table", { exact: true })
      .getByRole("option", { name: "Response table", exact: true }),
  ).toHaveCount(1);
  expect(errors).toEqual([]);
});
