import { test, expect } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { autospillFixtures } from "../../tools/autospill_fixture.mjs";

test("AutoSpill reviews cleanup, AF, every detector pair, exports and unmet tolerance", async ({
  page,
  request,
}) => {
  test.setTimeout(90000);
  mkdirSync("artifacts/screenshots", { recursive: true });
  const { token } = await (await request.get("/api/bootstrap")).json();
  const headers = { "X-CytoForge-Token": token };
  let doc = await (
    await request.post("/api/workspaces", {
      headers,
      data: { name: "AutoSpill browser validation" },
    })
  ).json();
  // Mixed-panel workspaces must derive scatter defaults from the selected panel.
  const unrelated = await request.post(
    `/api/workspaces/${doc.id}/import?revision=${doc.revision}`,
    {
      headers,
      multipart: {
        files: {
          name: "Unrelated DNA.csv",
          mimeType: "text/csv",
          buffer: Buffer.from("DNA,Time\n100,0\n200,1\n"),
        },
      },
    },
  );
  expect(unrelated.ok(), await unrelated.text()).toBeTruthy();
  doc = (await unrelated.json()).workspace;
  const fixture = autospillFixtures();
  for (const file of fixture.files) {
    const response = await request.post(
      `/api/workspaces/${doc.id}/import?revision=${doc.revision}`,
      {
        headers,
        multipart: {
          files: {
            name: `${file.name}.csv`,
            mimeType: "text/csv",
            buffer: Buffer.from(file.csv),
          },
        },
      },
    );
    expect(response.ok(), await response.text()).toBeTruthy();
    doc = (await response.json()).workspace;
  }
  const samples = doc.samples.slice(1);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(
    (id) => localStorage.setItem("cytoforge.workspace", id),
    doc.id,
  );
  await page.goto("/");
  await page.getByRole("button", { name: /^D1 single stain.csv/ }).click();
  await page.getByRole("button", { name: "Compensation", exact: true }).click();
  await page
    .getByRole("button", { name: "Control wizard", exact: true })
    .click();
  const wizard = page.getByRole("dialog");
  await wizard.getByLabel("Estimation method").selectOption("autospill");
  await wizard
    .getByLabel("AutoSpill matrix name")
    .fill("Verified AutoSpill panel");
  await expect(wizard.getByLabel("Automatic scatter cleanup")).toBeChecked();
  await wizard.getByLabel("AutoSpill autofluorescence subtraction").check();
  await expect(
    wizard.getByLabel("AutoSpill autofluorescence detector"),
  ).toHaveValue("AF");
  for (let i = 0; i < 3; i++)
    await wizard
      .getByLabel(`AutoSpill control ${i + 1} sample`, { exact: true })
      .selectOption(samples[i].id);
  await wizard
    .getByRole("button", { name: "Run AutoSpill", exact: true })
    .click();
  await expect(wizard.getByText("Converged", { exact: true })).toBeVisible({
    timeout: 30000,
  });
  await expect(
    wizard.getByRole("img", { name: "AutoSpill convergence history" }),
  ).toBeVisible();
  await expect(
    wizard.getByRole("img", { name: "Compensated control", exact: true }),
  ).toBeVisible();
  await wizard.evaluate((element) => (element.scrollTop = 0));
  await page.screenshot({
    path: "artifacts/screenshots/autospill-convergence.png",
    fullPage: true,
  });

  await wizard
    .getByText("Residual slopes for every detector pair", { exact: true })
    .click();
  await wizard.getByTitle("Inspect D1 versus AF").click();
  await expect(wizard.getByLabel("Preview secondary detector")).toHaveValue(
    "AF",
  );
  await expect(
    wizard
      .getByRole("img", { name: "Compensated control", exact: true })
      .locator("circle"),
  ).toHaveCount(300);
  await wizard
    .getByText("Inspect automatic scatter cleanup", { exact: true })
    .click();
  await expect(
    wizard
      .getByRole("img", { name: "Automatic scatter cleanup" })
      .locator("polygon"),
  ).toBeVisible();
  await wizard
    .getByRole("img", { name: "Acquired control", exact: true })
    .scrollIntoViewIfNeeded();
  await page.screenshot({
    path: "artifacts/screenshots/autospill-control-review.png",
    fullPage: true,
  });
  const reportDownload = page.waitForEvent("download");
  await wizard
    .getByRole("button", { name: "Calculation report", exact: true })
    .click();
  const reportStream = await (await reportDownload).createReadStream();
  const chunks = [];
  for await (const chunk of reportStream!) chunks.push(chunk);
  const report = JSON.parse(Buffer.concat(chunks).toString());
  expect(report.diagnostics.converged).toBe(true);
  expect(report.diagnostics.final_max_error).toBeLessThan(
    report.request.tolerance,
  );
  expect(report.request.af_detector).toBe("AF");
  expect(report.compensation.provenance.method).toBe("autospill");
  report.diagnostics.controls.forEach(
    (c: { parent_count: number; cleanup_count: number }) => {
      expect(c.parent_count).toBe(1800);
      expect(c.cleanup_count).toBeGreaterThan(600);
      expect(c.cleanup_count).toBeLessThan(1400);
    },
  );
  const matrixDownload = page.waitForEvent("download");
  await wizard.getByRole("button", { name: "Matrix CSV", exact: true }).click();
  expect((await matrixDownload).suggestedFilename()).toBe(
    "autospill-matrix.csv",
  );
  await wizard
    .locator(".control-targets")
    .getByLabel(samples[3].name, { exact: true })
    .check();
  await wizard
    .getByRole("button", { name: "Save and apply matrix", exact: true })
    .click();
  await expect(wizard).not.toBeVisible();
  const saved = await (
    await request.get(`/api/workspaces/${doc.id}`, { headers })
  ).json();
  expect(
    saved.samples.find((s: { id: string }) => s.id === samples[3].id)
      .compensation_id,
  ).toBe(report.id);
  expect(saved.compensations[0].source).toBe(
    "AutoSpill robust iterative regression",
  );
  expect(saved.compensations[0].provenance.diagnostics.converged).toBe(true);
  saved.compensations[0].matrix.forEach((row: number[], i: number) =>
    row.forEach((v, j) => expect(v).toBeCloseTo(fixture.matrix[i][j], 2)),
  );
  await wizard.waitFor({ state: "hidden" });

  await page
    .getByRole("button", { name: "Control wizard", exact: true })
    .click();
  await wizard.getByLabel("Estimation method").selectOption("autospill");
  await wizard
    .getByText("Previous calculations and saved matrices", { exact: true })
    .click();
  await wizard
    .getByRole("button", { name: /Verified AutoSpill panel/ })
    .click();
  await expect(wizard.getByText("Converged", { exact: true })).toBeVisible();
  await expect(
    wizard.getByRole("button", { name: "Save and apply matrix", exact: true }),
  ).toHaveCount(0);
  await page.setViewportSize({ width: 430, height: 900 });
  await wizard.evaluate((element) => (element.scrollTop = 0));
  await expect
    .poll(() => wizard.evaluate((el) => el.scrollWidth <= el.clientWidth + 1))
    .toBe(true);
  await page.screenshot({
    path: "artifacts/screenshots/autospill-mobile-review.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 1540, height: 1050 });

  await wizard
    .getByRole("button", { name: "Edit controls and recalculate", exact: true })
    .click();
  await wizard
    .getByLabel("AutoSpill matrix name")
    .fill("Incomplete AutoSpill review");
  await wizard
    .getByText("Regression, cleanup and refinement settings", { exact: true })
    .click();
  await wizard.getByLabel("Maximum refinements", { exact: true }).fill("1");
  await wizard
    .getByLabel("Residual slope tolerance", { exact: true })
    .fill("0.00000001");
  await wizard
    .getByRole("button", { name: "Run AutoSpill", exact: true })
    .click();
  await expect(
    wizard.getByText("Tolerance unmet", { exact: true }),
  ).toBeVisible({ timeout: 30000 });
  await expect(
    wizard.getByRole("button", { name: "Save and apply matrix", exact: true }),
  ).toBeDisabled();
  await wizard.getByLabel("Acknowledge AutoSpill unmet tolerance").check();
  await expect(
    wizard.getByRole("button", { name: "Save and apply matrix", exact: true }),
  ).toBeEnabled();
  await wizard.getByRole("button", { name: "Close", exact: true }).click();
  expect(errors).toEqual([]);
});
