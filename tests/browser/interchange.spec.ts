import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";

const root = path.resolve("tests/fixtures/interchange");

async function openReference(
  page: import("@playwright/test").Page,
  folder: string,
  filename: string,
) {
  const result = await page.request.get("/api/bootstrap");
  const headers = { "X-CytoForge-Token": (await result.json()).token };
  const created = await page.request.post("/api/workspaces", {
    headers,
    data: { name: `Browser ${folder} interchange` },
  });
  const doc = await created.json();
  const imported = await page.request.post(
    `/api/workspaces/${doc.id}/import?revision=0`,
    {
      headers,
      multipart: {
        files: {
          name: filename,
          mimeType: "application/octet-stream",
          buffer: readFileSync(path.join(root, folder, filename)),
        },
      },
    },
  );
  expect(imported.ok()).toBeTruthy();
  const current = (await imported.json()).workspace;
  await page.addInitScript(
    (id) => localStorage.setItem("cytoforge.workspace", id),
    doc.id,
  );
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "All events", exact: true }).first(),
  ).toBeVisible();
  return { doc: current, headers };
}

test("FlowJo review, exact ellipse, source report and drawing in imported coordinates", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const { doc, headers } = await openReference(
    page,
    "flowjo",
    "data_set_simple_line_100.fcs",
  );
  await page.getByRole("button", { name: "Import gates", exact: true }).click();
  await page
    .getByLabel("Choose gate XML or FlowJo workspace")
    .setInputFiles(path.join(root, "flowjo", "single_ellipse_51_events.wsp"));
  const dialog = page.getByRole("dialog");
  const apply = dialog.getByRole("button", {
    name: "Import selected gates",
    exact: true,
  });
  await expect(
    dialog.getByText("1 of 1 gates supported", { exact: false }),
  ).toBeVisible();
  await expect(apply).toBeDisabled();
  await dialog.getByRole("checkbox", { name: /I reviewed the report/ }).check();
  await expect(apply).toBeEnabled();
  await apply.click();
  await expect(
    dialog.getByText("1 gates imported", { exact: true }),
  ).toBeVisible();
  const report = page.waitForEvent("download");
  await dialog
    .getByRole("button", { name: "Download report", exact: true })
    .click();
  expect((await report).suggestedFilename()).toContain("report.json");
  await page.screenshot({
    path: "artifacts/screenshots/interchange-review.png",
    fullPage: true,
  });
  await dialog.getByRole("button", { name: "Done", exact: true }).click();
  await page
    .getByRole("button", { name: /ellipse1/ })
    .first()
    .click();
  const plot = page.getByRole("img", { name: /density plot/ });
  await expect(plot).toHaveAttribute("aria-label", /51 events/);
  await expect(
    page.getByText("Gate coordinates · ellipse1", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Rectangle gate", exact: true })
    .click();
  const box = (await plot.boundingBox())!;
  await page.mouse.move(box.x + 140, box.y + 95);
  await page.mouse.down();
  await page.mouse.move(box.x + 340, box.y + 255, { steps: 5 });
  await page.mouse.up();
  await page.getByLabel("Population name").fill("Imported coordinate child");
  await expect(
    page.getByRole("dialog").getByText("Dimension 1", { exact: false }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Create population", exact: true })
    .click();
  await expect(page.getByRole("dialog")).not.toBeVisible();
  const saved = await (
    await page.request.get(`/api/workspaces/${doc.id}`, { headers })
  ).json();
  const child = saved.gates.find(
    (gate: { name: string }) => gate.name === "Imported coordinate child",
  );
  expect(child.kind).toBe("hyperrectangle");
  expect(child.dimensions[0].compensation_ref).toBe("uncompensated");
  expect(child.dimensions[0].transform.kind).toBe("gml_linear");
  await page.screenshot({
    path: "artifacts/screenshots/interchange-plot.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});

test("GatingML sample mapping, ratio plotting and validated XML download", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const { doc, headers } = await openReference(page, "gatingml", "data1.fcs");
  await page.getByRole("button", { name: "Import gates", exact: true }).click();
  await page
    .getByLabel("Choose gate XML or FlowJo workspace")
    .setInputFiles(
      path.join(root, "gatingml/gml/gml_log_ratio_range1_gate.xml"),
    );
  const dialog = page.getByRole("dialog");
  await expect(
    dialog.getByText("Gating strategy", { exact: true }),
  ).toBeVisible();
  await dialog
    .getByRole("checkbox", { name: "data1.fcs", exact: true })
    .check();
  await dialog
    .getByRole("button", { name: "Import selected gates", exact: true })
    .click();
  await expect(
    dialog.getByText("1 gates imported", { exact: true }),
  ).toBeVisible();
  await dialog.getByRole("button", { name: "Done", exact: true }).click();
  await page
    .getByRole("button", { name: /RatRange1a/ })
    .first()
    .click();
  await expect(page.getByText(/Gate coordinates · RatRange1a/)).toBeVisible();
  await expect(page.getByRole("img", { name: /histogram plot/ })).toBeVisible();
  const exported = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "Export GatingML", exact: true })
    .click();
  expect((await exported).suggestedFilename()).toContain(".gates.xml");
  const saved = await (
    await page.request.get(`/api/workspaces/${doc.id}`, { headers })
  ).json();
  expect(saved.gates[0].dimensions[0].ratio_channels).toHaveLength(2);
  await page.screenshot({
    path: "artifacts/screenshots/interchange-ratio.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});
