import { test, expect, type APIRequestContext } from "@playwright/test";
import { mkdirSync, readFileSync } from "node:fs";

async function fixture(request: APIRequestContext, reset = false) {
  const { token } = await (await request.get("/api/bootstrap")).json();
  const headers = { "X-CytoForge-Token": token };
  let doc = await (
    await request.post("/api/workspaces", {
      headers,
      data: { name: "Kinetics desktop validation" },
    })
  ).json();
  for (const [name, offset] of (reset
    ? [["Clock reset", 0]]
    : [
        ["Response A", 0],
        ["Response B", 2],
      ]) as [string, number][]) {
    const rows = ["Time,Signal"];
    for (let i = 0; i < 8; i++)
      for (let j = 0; j < 4; j++)
        rows.push(
          `${(reset && i >= 4 ? i - 4 : i) + 0.5 + offset},${j + 1 + (i >= 4 ? 10 : 0)}`,
        );
    const response = await request.post(
      `/api/workspaces/${doc.id}/import?revision=${doc.revision}`,
      {
        headers,
        multipart: {
          files: {
            name: `${name}.csv`,
            mimeType: "text/csv",
            buffer: Buffer.from(rows.join("\n")),
          },
        },
      },
    );
    expect(response.ok(), await response.text()).toBeTruthy();
    doc = (await response.json()).workspace;
  }
  return { doc, headers };
}

test("kinetics aligns two acquisitions, saves ranges, exports and refits live gates", async ({
  page,
  request,
}) => {
  mkdirSync("artifacts/screenshots", { recursive: true });
  const { doc, headers } = await fixture(request);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.addInitScript(
    (id) => localStorage.setItem("cytoforge.workspace", id),
    doc.id,
  );
  await page.goto("/");
  await page.getByRole("button", { name: "Kinetics", exact: true }).click();
  await page
    .getByLabel("Kinetics analysis name", { exact: true })
    .fill("Aligned response");
  await page.getByRole("button", { name: "All samples", exact: true }).click();
  await page
    .getByLabel("Kinetics time offset Response B.csv", { exact: true })
    .fill("-2");
  await page.getByLabel("Kinetics time minimum", { exact: true }).fill("0");
  await page.getByLabel("Kinetics time maximum", { exact: true }).fill("8");
  await page.getByLabel("Kinetics time bins", { exact: true }).fill("8");
  await page
    .getByLabel("Kinetics statistic", { exact: true })
    .selectOption("percent_positive");
  await page
    .getByLabel("Kinetics threshold mode", { exact: true })
    .selectOption("baseline_percentile");
  await page.getByLabel("Kinetics baseline end", { exact: true }).fill("4");
  await page.getByRole("button", { name: "Add range", exact: true }).click();
  await page
    .getByLabel("Kinetics range 1 name", { exact: true })
    .fill("Reference");
  await page.getByLabel("Kinetics range 1 end", { exact: true }).fill("4");
  await page.getByRole("button", { name: "Add range", exact: true }).click();
  await page
    .getByLabel("Kinetics range 2 name", { exact: true })
    .fill("Response");
  await page.getByLabel("Kinetics range 2 end", { exact: true }).fill("8");
  await page
    .getByLabel("Create kinetics responder populations", { exact: true })
    .check();
  await page
    .getByRole("button", { name: "Calculate kinetics", exact: true })
    .click();
  await expect(
    page.getByRole("img", {
      name: "Kinetics time course with measured gaps",
      exact: true,
    }),
  ).toBeVisible();
  const save = page.getByRole("button", {
    name: "Save kinetics analysis",
    exact: true,
  });
  await expect(save).toBeDisabled();
  await page.getByLabel("Overlay kinetics samples", { exact: true }).check();
  const exportJSON = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "Analysis JSON", exact: true })
    .click();
  await (await exportJSON).saveAs("artifacts/kinetics-browser-report.json");
  const report = JSON.parse(
    readFileSync("artifacts/kinetics-browser-report.json", "utf8"),
  );
  expect(report.fits).toHaveLength(2);
  for (const fit of report.fits) {
    expect(fit.threshold).toBe(4);
    expect(fit.bins.map((b: { value: number }) => b.value)).toEqual([
      0, 0, 0, 0, 100, 100, 100, 100,
    ]);
    expect(
      fit.ranges.map((r: { responder_count: number }) => r.responder_count),
    ).toEqual([0, 16]);
  }
  await page
    .getByLabel("Kinetics review confirmation", { exact: true })
    .check();
  await save.click();
  await expect(page.getByText("Saved analysis", { exact: true })).toBeVisible();
  const before = await (
    await request.get(`/api/workspaces/${doc.id}`, { headers })
  ).json();
  expect(before.gates).toHaveLength(10);
  const ids = before.gates.map((g: { id: string }) => g.id).sort();
  await page.screenshot({
    path: "artifacts/screenshots/kinetics-desktop-workflow.png",
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "Refit and replace", exact: true })
    .click();
  await page
    .getByLabel("Kinetics threshold mode", { exact: true })
    .selectOption("absolute");
  await page
    .getByLabel("Kinetics absolute threshold", { exact: true })
    .fill("12");
  await page.getByLabel("Kinetics range 2 start", { exact: true }).fill("5");
  await page
    .getByRole("button", { name: "Calculate kinetics", exact: true })
    .click();
  await expect(
    page.getByText("Review time course", { exact: true }),
  ).toBeVisible();
  await page
    .getByLabel("Kinetics review confirmation", { exact: true })
    .check();
  await save.click();
  await expect(page.getByText("Saved analysis", { exact: true })).toBeVisible();
  const after = await (
    await request.get(`/api/workspaces/${doc.id}`, { headers })
  ).json();
  expect(after.kinetics_results).toHaveLength(2);
  expect(after.gates.map((g: { id: string }) => g.id).sort()).toEqual(ids);
  expect(after.kinetics_results[1].fits[0].ranges[1].responder_count).toBe(6);
  const model = after.kinetics_results[1];
  await page.getByRole("button", { name: "Statistics", exact: true }).click();
  await page
    .getByRole("button", { name: "Custom tables", exact: true })
    .click();
  await page
    .getByLabel("Custom table name", { exact: true })
    .fill("Kinetic response summary");
  await page.getByRole("button", { name: "Add column", exact: true }).click();
  await page
    .getByLabel("Custom column name", { exact: true })
    .fill("Responders");
  await page
    .getByLabel("Custom column type", { exact: true })
    .selectOption("biology");
  await page
    .getByLabel("Table biology platform", { exact: true })
    .selectOption("kinetics");
  await page
    .getByLabel("Table biological model", { exact: true })
    .selectOption(model.id);
  await page
    .getByLabel("Table biology statistic", { exact: true })
    .selectOption("responder_count");
  await page
    .getByLabel("Table kinetics time range", { exact: true })
    .selectOption(model.fits[0].ranges[1].id);
  const table = page.getByRole("table", {
    name: "Custom statistics table",
    exact: true,
  });
  await expect(
    table
      .getByRole("row")
      .filter({ hasText: "Response A.csv" })
      .getByRole("cell")
      .filter({ hasText: /^6(?:\.0+)?$/ }),
  ).toBeVisible();
  const tableSaved = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/workspaces/${doc.id}/tables/save`) &&
      response.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "Save custom table", exact: true })
    .click();
  const tableResponse = await tableSaved;
  expect(tableResponse.ok(), await tableResponse.text()).toBeTruthy();
  const latest = await tableResponse.json();
  expect(
    latest.tables.some(
      (t: { name: string }) => t.name === "Kinetic response summary",
    ),
  ).toBeTruthy();
  const plate = await request.post(`/api/workspaces/${doc.id}/plates/save`, {
    headers,
    data: {
      revision: latest.revision,
      plate: {
        name: "Time response plate",
        assignments: { A01: [latest.samples[0].id] },
        columns: [
          {
            id: "f".repeat(32),
            name: "Responders",
            kind: "biology",
            platform: "kinetics",
            result_id: model.id,
            kinetics_range_id: model.fits[0].ranges[1].id,
            biology_metric: "responder_count",
          },
        ],
      },
    },
  });
  expect(plate.ok(), await plate.text()).toBeTruthy();
  await page.reload();
  await page.getByRole("button", { name: "Plates", exact: true }).click();
  await page.locator('.plate-grid-status[data-ready="true"]').waitFor();
  await expect(page.locator('[data-well="A01"] .plate-well-value')).toHaveText(
    "6",
  );
  await page.getByRole("tab", { name: "Measurements", exact: true }).click();
  await expect(
    page.getByLabel("Plate biology platform", { exact: true }),
  ).toHaveValue("kinetics");
  await expect(
    page.getByLabel("Plate kinetics time range", { exact: true }),
  ).toHaveValue(model.fits[0].ranges[1].id);
  await page.getByRole("button", { name: "Kinetics", exact: true }).click();
  await expect(page.getByText("Saved analysis", { exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: "Explore kinetics Response", exact: true })
    .click();
  await expect(page.getByLabel("X axis channel", { exact: true })).toHaveValue(
    after.kinetics_results[1].columns[0],
  );
  await expect(page.getByLabel("Y axis channel", { exact: true })).toHaveValue(
    after.kinetics_results[1].columns[1],
  );
  expect(errors).toEqual([]);
});

test("kinetics reports a clock reset and reruns with explicit unwrapping", async ({
  page,
  request,
}) => {
  const { doc } = await fixture(request, true);
  await page.addInitScript(
    (id) => localStorage.setItem("cytoforge.workspace", id),
    doc.id,
  );
  await page.goto("/");
  await page.getByRole("button", { name: "Kinetics", exact: true }).click();
  await page.getByLabel("Kinetics time bins", { exact: true }).fill("8");
  await page
    .getByRole("button", { name: "Calculate kinetics", exact: true })
    .click();
  await expect(page.getByText(/Time decreases 1 times/)).toBeVisible();
  await page
    .getByLabel("Kinetics clock decreases", { exact: true })
    .selectOption("unwrap");
  await page
    .getByRole("button", { name: "Calculate kinetics", exact: true })
    .click();
  await expect(
    page.getByRole("img", {
      name: "Kinetics time course with measured gaps",
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.getByText(/1 clock decreases: unwrap/)).toBeVisible();
});
