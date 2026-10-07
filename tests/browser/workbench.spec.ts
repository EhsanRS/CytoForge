import { test, expect } from "@playwright/test";
import { mkdirSync } from "node:fs";

let workspaceId: string;
test.beforeAll(async ({ request }) => {
  mkdirSync("artifacts/screenshots", { recursive: true });
  const bootstrap = await request.get("/api/bootstrap");
  const { token } = await bootstrap.json();
  const result = await request.post("/api/demo", {
    headers: { "X-CytoForge-Token": token },
    data: {},
  });
  expect(result.ok()).toBeTruthy();
  workspaceId = (await result.json()).id;
});
test.beforeEach(async ({ page }) => {
  await page.addInitScript(
    (id) => localStorage.setItem("cytoforge.workspace", id),
    workspaceId,
  );
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "All events", exact: true }).first(),
  ).toBeVisible();
});

test("real density, gate creation, full-data counts, undo and redo", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page
    .getByRole("button", { name: /CD3\+ T cells/ })
    .first()
    .click();
  await expect(
    page.getByRole("heading", { name: "CD3+ T cells", exact: true }).first(),
  ).toBeVisible();
  const plot = page.getByRole("img", { name: /density plot/ });
  await expect(plot).toHaveAttribute("aria-label", /35,495 events/);
  await page.screenshot({
    path: "artifacts/screenshots/workbench.png",
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "Rectangle gate", exact: true })
    .click();
  const box = (await plot.boundingBox())!;
  await page.mouse.move(box.x + 160, box.y + 115);
  await page.mouse.down();
  await page.mouse.move(box.x + 350, box.y + 255, { steps: 6 });
  await page.mouse.up();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByLabel("Population name").fill("Browser verified gate");
  await page
    .getByRole("button", { name: "Create population", exact: true })
    .click();
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await expect(
    page.getByRole("button", { name: /Browser verified gate/ }).first(),
  ).toBeVisible();
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(
    page.getByRole("button", { name: /Browser verified gate/ }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Redo", exact: true }).click();
  await expect(
    page.getByRole("button", { name: /Browser verified gate/ }).first(),
  ).toBeVisible();
  expect(errors).toEqual([]);
});

test("batch statistics, channel values, CSV download, metadata and sample group", async ({
  page,
}) => {
  await page.getByRole("button", { name: "Statistics", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Table builder" }),
  ).toBeVisible();
  await page.getByLabel("Statistics channel").selectOption("PE-A");
  await expect(
    page.getByRole("columnheader", { name: "Median", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "22,481", exact: true }).first(),
  ).toBeVisible();
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export CSV", exact: true }).click();
  expect((await downloadPromise).suggestedFilename()).toBe(
    "cytoforge-statistics.csv",
  );
  await page.screenshot({
    path: "artifacts/screenshots/statistics.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Samples", exact: true }).click();
  await page
    .getByRole("checkbox", { name: "Select D01_CTRL.fcs", exact: true })
    .check();
  await page
    .getByRole("checkbox", { name: "Select D01_STIM.fcs", exact: true })
    .check();
  await page.getByRole("button", { name: "Create group", exact: true }).click();
  await page.getByLabel("Group name").fill("Donor one");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Create group", exact: true })
    .click();
  await expect(page.getByRole("button", { name: /Donor one/ })).toBeVisible();
});

test("compensation saves and updates, report composition and portable export", async ({
  page,
}) => {
  await page
    .getByRole("button", { name: /CD3\+ T cells/ })
    .first()
    .click();
  await page.getByRole("button", { name: "Compensation", exact: true }).click();
  await page.getByLabel("FITC-A to PE-A percent").fill("11");
  await page
    .getByRole("button", { name: "Save & apply matrix", exact: true })
    .click();
  await expect(
    page
      .getByRole("status")
      .filter({ hasText: "Compensation saved and applied" }),
  ).toBeVisible();
  await page.screenshot({
    path: "artifacts/screenshots/compensation.png",
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "Layout studio", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Add current plot", exact: true })
    .first()
    .click();
  await expect(page.getByRole("img", { name: /density plot/ })).toBeVisible();
  await page.getByLabel("Report title").fill("PBMC validation report");
  await page.getByRole("button", { name: /Save layout/ }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "Report layout saved" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "SVG page", exact: true }),
  ).toBeEnabled();
  await page.waitForLoadState("networkidle");
  await expect(
    page.locator('.studio-svg [data-ready="true"]').first(),
  ).toHaveAttribute("data-ready", "true");
  await expect(page.getByText("Calculating plot")).toHaveCount(0);
  await page.screenshot({
    path: "artifacts/screenshots/report.png",
    fullPage: true,
  });
  const reportDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "SVG page", exact: true }).click();
  await (await reportDownload).saveAs("artifacts/screenshots/report.svg");
  const archive = page.waitForEvent("download");
  await page.getByRole("button", { name: "Save project", exact: true }).click();
  const downloaded = await archive;
  expect(downloaded.suggestedFilename()).toContain(".cytoforge");
  await downloaded.saveAs(
    "artifacts/screenshots/validated-workspace.cytoforge",
  );
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "All events", exact: true }).first(),
  ).toBeVisible();
});

test("population navigation completing after a panel change preserves the selected panel", async ({
  page,
}) => {
  let release!: () => void;
  let started!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  const requested = new Promise<void>((resolve) => {
    started = resolve;
  });
  await page.route("**/plot-navigation/plan", async (route) => {
    const response = await route.fetch();
    started();
    await pending;
    await route.fulfill({ response });
  });
  await page
    .getByRole("button", { name: /CD3\+ T cells/ })
    .first()
    .click();
  await requested;
  await page.getByRole("button", { name: "Compensation", exact: true }).click();
  await expect(page.getByLabel("FITC-A to PE-A percent")).toBeVisible();
  release();
  await expect(page.locator(".busy-indicator")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Compensation", exact: true }),
  ).toHaveAttribute("aria-current", "page");
  await expect(page.getByLabel("FITC-A to PE-A percent")).toBeVisible();
  await page.getByRole("button", { name: "Analysis", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "CD3+ T cells", exact: true, level: 1 }),
  ).toBeVisible();
});

test("mixed file import, histogram and empty population", async ({ page }) => {
  await page.getByLabel("Import FCS or CSV files").setInputFiles([
    {
      name: "browser-cells.csv",
      mimeType: "text/csv",
      buffer: Buffer.from("A,B\n1,2\n3,4\n5,6\n"),
    },
    {
      name: "broken.csv",
      mimeType: "text/csv",
      buffer: Buffer.from("A,B\nno,2\n"),
    },
  ]);
  await expect(
    page.getByRole("heading", { name: "1 sample imported" }),
  ).toBeVisible();
  await expect(
    page.getByText("CSV row 2 contains a nonnumeric value"),
  ).toBeVisible();
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await page
    .getByRole("button", { name: /browser-cells.csv/ })
    .first()
    .click();
  await page.getByRole("button", { name: "Histogram", exact: true }).click();
  await expect(
    page.getByRole("img", { name: /histogram plot of A/ }),
  ).toHaveAttribute("aria-label", /3 events/);
  await page.getByRole("button", { name: "Range gate", exact: true }).click();
  const box = (await page
    .getByRole("img", { name: /histogram plot/ })
    .boundingBox())!;
  await page.mouse.move(box.x + 130, box.y + 140);
  await page.mouse.down();
  await page.mouse.move(box.x + 230, box.y + 140);
  await page.mouse.up();
  await page.getByLabel("Population name").fill("No events");
  await page.getByLabel("X minimum").fill("100");
  await page.getByLabel("X maximum").fill("200");
  await page
    .getByRole("button", { name: "Create population", exact: true })
    .click();
  await page
    .getByRole("button", { name: /No events/ })
    .first()
    .click();
  await expect(
    page.getByRole("img", { name: /histogram plot/ }),
  ).toHaveAttribute("aria-label", /0 events/);
});

test("derived parameter formula, plotting and channel statistics", async ({
  page,
}) => {
  await page
    .getByRole("button", { name: "Derived parameter", exact: true })
    .first()
    .click();
  await page.getByLabel("Parameter name").fill("CD4_CD8_ratio");
  await page
    .getByLabel("Expression", { exact: true })
    .fill('ch("PE-A") / max(abs(ch("APC-A")), 1)');
  await page
    .getByRole("button", { name: "Save parameter", exact: true })
    .click();
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await page.getByLabel("X axis channel").selectOption("CD4_CD8_ratio");
  await page.getByRole("button", { name: "Histogram", exact: true }).click();
  await expect(
    page.getByRole("img", { name: /histogram plot of CD4_CD8_ratio/ }),
  ).toHaveAttribute("aria-label", /61,795 events/);
  await page.getByRole("button", { name: "Statistics", exact: true }).click();
  await page.getByLabel("Statistics channel").selectOption("CD4_CD8_ratio");
  await expect(
    page.getByRole("columnheader", { name: "Mean", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "61,795", exact: true }).first(),
  ).toBeVisible();
});

test("background PCA, provenance, mapped parameters and job cancellation", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.getByRole("button", { name: "Discovery", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Discover cell states" }),
  ).toBeVisible();
  await page.getByLabel("Analysis name", { exact: true }).fill("Browser PCA");
  await page.getByLabel("Maximum fitted events").fill("1000");
  await page.getByRole("button", { name: "Run PCA", exact: true }).click();
  const card = page.locator(".analysis-job").filter({
    has: page.getByRole("heading", { name: "Browser PCA", exact: true }),
  });
  await expect(card.getByText("Ready", { exact: true })).toBeVisible({
    timeout: 30000,
  });
  await expect(card.getByText("61,795", { exact: true })).toHaveCount(2);
  await card.getByRole("button", { name: "Provenance", exact: true }).click();
  await expect(
    card.getByRole("columnheader", { name: "Component weight" }),
  ).toBeVisible();
  await card
    .getByRole("button", { name: "Add parameters", exact: true })
    .click();
  await expect(
    card.getByRole("button", { name: "Explore result", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "artifacts/screenshots/discovery.png",
    fullPage: true,
  });
  const provenance = page.waitForEvent("download");
  await card
    .getByRole("button", { name: "Export provenance", exact: true })
    .click();
  expect((await provenance).suggestedFilename()).toBe("Browser PCA.json");
  await card
    .getByRole("button", { name: "Explore result", exact: true })
    .click();
  await expect(page.getByLabel("X axis channel")).toHaveValue(
    /Browser PCA.*PC1/,
  );
  const plot = page.getByRole("img", { name: /density plot/ });
  await expect(plot.locator("canvas")).toHaveAttribute("data-ready", "true");
  await expect(plot).toHaveAttribute("aria-label", /61,795 events/);
  await page.screenshot({
    path: "artifacts/screenshots/pca-population.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Discovery", exact: true }).click();
  await page.getByRole("radio", { name: /t-SNE/ }).check();
  await page
    .getByLabel("Analysis name", { exact: true })
    .fill("Cancelled browser t-SNE");
  await page.getByRole("button", { name: "Run t-SNE", exact: true }).click();
  const pending = page.locator(".analysis-job").filter({
    has: page.getByRole("heading", {
      name: "Cancelled browser t-SNE",
      exact: true,
    }),
  });
  await pending
    .getByRole("button", {
      name: "Cancel Cancelled browser t-SNE",
      exact: true,
    })
    .click();
  await expect(pending.getByText("cancelled", { exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});
