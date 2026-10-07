import { test, expect, type APIRequestContext } from "@playwright/test";
import { mkdirSync, readFileSync } from "node:fs";

async function dnaExperiment(request: APIRequestContext) {
  const { token } = await (await request.get("/api/bootstrap")).json();
  const headers = { "X-CytoForge-Token": token };
  let doc = await (
    await request.post("/api/workspaces", {
      headers,
      data: { name: "DNA model validation" },
    })
  ).json();
  let seed = 6519;
  const uniform = () => {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    return (seed + 0.5) / 4294967296;
  };
  const normal = () =>
    Math.sqrt(-2 * Math.log(uniform())) * Math.cos(2 * Math.PI * uniform());
  for (const mean of [100, 125]) {
    const rows = ["DNA,Time"];
    for (let i = 0; i < 6000; i++) {
      const phase = i % 10;
      const truth =
        phase < 5
          ? mean
          : phase < 8
            ? mean * (1 + 0.98 * uniform())
            : 1.98 * mean;
      rows.push(`${truth + 0.04 * truth * normal()},${i}`);
    }
    rows.push("NaN,6000");
    const imported = await request.post(
      `/api/workspaces/${doc.id}/import?revision=${doc.revision}`,
      {
        headers,
        multipart: {
          files: {
            name: `DNA ${mean}.csv`,
            mimeType: "text/csv",
            buffer: Buffer.from(rows.join("\n")),
          },
        },
      },
    );
    expect(imported.ok(), await imported.text()).toBeTruthy();
    doc = (await imported.json()).workspace;
  }
  return { doc, headers };
}

test("DJF batch fits independent DNA peaks, reviews assignments, exports and reopens saved models", async ({
  page,
  request,
}) => {
  mkdirSync("artifacts/screenshots", { recursive: true });
  const { doc, headers } = await dnaExperiment(request);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(
    (id) => localStorage.setItem("cytoforge.workspace", id),
    doc.id,
  );
  await page.goto("/");
  await page.getByRole("button", { name: "Cell cycle", exact: true }).click();
  await page.getByLabel("Model name", { exact: true }).fill("DNA browser");
  await page.getByRole("button", { name: "All samples", exact: true }).click();
  await page.getByLabel("DNA parameter", { exact: true }).selectOption("DNA");
  await page
    .getByText("Peak constraints and initial guesses", { exact: true })
    .click();
  await page
    .getByLabel("G2/G1 ratio constraint", { exact: true })
    .selectOption("fixed");
  await page
    .getByLabel("G2/G1 ratio fixed value", { exact: true })
    .fill("1.98");
  await page
    .getByLabel("G1 CV (%) constraint", { exact: true })
    .selectOption("fixed");
  await page.getByLabel("G1 CV (%) fixed value", { exact: true }).fill("4");
  await page
    .getByLabel("Link peak CVs", { exact: true })
    .selectOption("g2_to_g1");
  await page
    .getByRole("button", { name: "Fit 2 samples", exact: true })
    .click();
  await expect(
    page.getByRole("img", { name: /full-event DNA histogram/ }),
  ).toBeVisible();
  const save = page.getByRole("button", {
    name: "Save fit to workspace",
    exact: true,
  });
  await expect(save).toBeDisabled();
  const reportDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "JSON report", exact: true }).click();
  await (
    await reportDownload
  ).saveAs("artifacts/cellcycle-browser-report.json");
  const report = JSON.parse(
    readFileSync("artifacts/cellcycle-browser-report.json", "utf8"),
  );
  expect(report.fits).toHaveLength(2);
  for (const [index, mean] of [100, 125].entries()) {
    const fit = report.fits[index];
    expect(fit.parameters.g1_mean).toBeCloseTo(mean, 0);
    expect(fit.parameters.peak_ratio).toBe(1.98);
    expect(fit.parameters.g1_cv).toBe(4);
    expect(fit.parameters.g2_cv).toBe(4);
    expect(fit.data.fitted_count).toBe(6000);
    expect(
      fit.assigned_counts.reduce(
        (sum: number, count: number) => sum + count,
        0,
      ),
    ).toBe(6000);
    for (const [i, fraction] of [0.5, 0.3, 0.2].entries())
      expect(Math.abs(fit.fractions[i] - fraction)).toBeLessThan(0.025);
  }
  await page
    .getByLabel("Review cell-cycle sample")
    .selectOption(doc.samples[1].id);
  const figureDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "SVG figure", exact: true }).click();
  await (await figureDownload).saveAs("artifacts/cellcycle-browser-figure.svg");
  expect(
    readFileSync("artifacts/cellcycle-browser-figure.svg", "utf8"),
  ).toContain("DNA 125");
  const eventsDownload = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "Event probabilities CSV", exact: true })
    .click();
  await (await eventsDownload).saveAs("artifacts/cellcycle-browser-events.csv");
  const rows = readFileSync("artifacts/cellcycle-browser-events.csv", "utf8")
    .trim()
    .split("\n");
  expect(rows).toHaveLength(6002);
  expect(rows.at(-1)).toBe("6000,nan,nan,nan,nan");
  await page
    .getByLabel("I reviewed the phase curves, constraints and fit warnings.")
    .check();
  await save.click();
  await expect(
    page.getByRole("button", { name: "Explore S", exact: true }),
  ).toBeVisible();
  let saved = await (
    await request.get(`/api/workspaces/${doc.id}`, { headers })
  ).json();
  expect(saved.cell_cycle_results).toHaveLength(1);
  expect(saved.gates).toHaveLength(6);
  await page.screenshot({
    path: "artifacts/screenshots/cell-cycle.png",
    fullPage: true,
  });
  await page
    .getByRole("img", { name: /full-event DNA histogram/ })
    .screenshot({ path: "artifacts/screenshots/cell-cycle-fit.png" });
  await page.getByRole("button", { name: "Explore S", exact: true }).click();
  await expect(
    page.getByRole("heading", {
      name: "DNA browser · S",
      exact: true,
      level: 1,
    }),
  ).toBeVisible();
  await expect(page.getByRole("img", { name: /density plot/ })).toBeVisible();
  const exported = await request.get(
    `/api/workspaces/${doc.id}/export/project`,
    { headers },
  );
  expect(exported.ok()).toBeTruthy();
  const restored = await request.post("/api/import/project", {
    headers,
    multipart: {
      file: {
        name: "dna.cytoforge",
        mimeType: "application/zip",
        buffer: await exported.body(),
      },
    },
  });
  expect(restored.ok(), await restored.text()).toBeTruthy();
  saved = await restored.json();
  await page.evaluate(
    (id) => localStorage.setItem("cytoforge.workspace", id),
    saved.id,
  );
  await page.reload();
  await page.getByRole("button", { name: "Cell cycle", exact: true }).click();
  await expect(
    page.getByRole("img", { name: /full-event DNA histogram/ }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Save fit to workspace", exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "Use settings for refit", exact: true })
    .click();
  await expect(page.getByLabel("Model name", { exact: true })).toHaveValue(
    "DNA browser refit",
  );
  await expect(
    page.getByLabel("G2/G1 ratio constraint", { exact: true }),
  ).toHaveValue("fixed");
  expect(errors).toEqual([]);
});
