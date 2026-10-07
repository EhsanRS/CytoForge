import { test, expect, type APIRequestContext } from "@playwright/test";
import { mkdirSync, readFileSync } from "node:fs";

async function experiment(request: APIRequestContext) {
  const { token } = await (await request.get("/api/bootstrap")).json();
  const headers = { "X-CytoForge-Token": token };
  let doc = await (
    await request.post("/api/workspaces", {
      headers,
      data: { name: "Dye dilution validation" },
    })
  ).json();
  let seed = 7951;
  const uniform = () => {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    return (seed + 0.5) / 4294967296;
  };
  const normal = () =>
    Math.sqrt(-2 * Math.log(uniform())) * Math.cos(2 * Math.PI * uniform());
  const known: Record<string, number[]> = {};
  for (const [name, probabilities] of [
    ["Stimulated A", [0.15, 0.3, 0.35, 0.2]],
    ["Stimulated B", [0.4, 0.3, 0.2, 0.1]],
    ["Undivided control", [1]],
    ["Unstained control", []],
  ] as [string, number[]][]) {
    const rows = ["CFSE,Time"],
      counts = probabilities.map(() => 0);
    for (let i = 0; i < 6000; i++) {
      let generation = 0;
      if (probabilities.length) {
        let u = uniform();
        while (
          generation < probabilities.length - 1 &&
          u > probabilities[generation]
        )
          u -= probabilities[generation++];
        counts[generation]++;
      }
      const dye = probabilities.length
        ? 1024 *
          0.5 ** generation *
          Math.exp(Math.sqrt(Math.log1p(0.2 ** 2)) * normal())
        : 0;
      rows.push(`${20 + dye + 2 * normal()},${i}`);
    }
    rows.push("NaN,6000");
    const imported = await request.post(
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
    expect(imported.ok(), await imported.text()).toBeTruthy();
    doc = (await imported.json()).workspace;
    known[`${name}.csv`] = counts;
  }
  return { doc, headers, known };
}

test("proliferation calibrates controls, reviews two samples, exports identities and restores the model", async ({
  page,
  request,
}) => {
  mkdirSync("artifacts/screenshots", { recursive: true });
  const { doc, headers, known } = await experiment(request);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.addInitScript(
    (id) => localStorage.setItem("cytoforge.workspace", id),
    doc.id,
  );
  await page.goto("/");
  await page
    .getByRole("button", { name: "Proliferation", exact: true })
    .click();
  await page
    .getByLabel("Proliferation model name", { exact: true })
    .fill("CFSE browser");
  await page
    .getByRole("button", { name: "Fit proliferation", exact: true })
    .click();
  await expect(
    page.getByText(/Select an undivided control or provide/),
  ).toBeVisible();
  await page.getByRole("button", { name: "All samples", exact: true }).click();
  await page
    .getByLabel("Fit proliferation Undivided control.csv", { exact: true })
    .uncheck();
  await page
    .getByLabel("Fit proliferation Unstained control.csv", { exact: true })
    .uncheck();
  const undivided = doc.samples.find(
    (s: { name: string }) => s.name === "Undivided control.csv",
  );
  const unstained = doc.samples.find(
    (s: { name: string }) => s.name === "Unstained control.csv",
  );
  await page
    .getByLabel("Undivided control", { exact: true })
    .selectOption(undivided.id);
  await page
    .getByLabel("Undivided calibration", { exact: true })
    .selectOption("fix_mean_cv");
  await page
    .getByLabel("Unstained control", { exact: true })
    .selectOption(unstained.id);
  await page.getByLabel("Last generation", { exact: true }).fill("3");
  await page
    .getByRole("button", { name: "Fit proliferation", exact: true })
    .click();
  await expect(
    page.getByRole("img", {
      name: "Generation model histogram and residuals",
      exact: true,
    }),
  ).toBeVisible();
  const save = page.getByRole("button", {
    name: "Save proliferation model",
    exact: true,
  });
  await expect(save).toBeDisabled();
  const jsonDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "Model JSON", exact: true }).click();
  await (
    await jsonDownload
  ).saveAs("artifacts/proliferation-browser-report.json");
  const report = JSON.parse(
    readFileSync("artifacts/proliferation-browser-report.json", "utf8"),
  );
  expect(report.fits).toHaveLength(2);
  expect(report.calibration.undivided.undivided_mean).toBeGreaterThan(1020);
  expect(report.calibration.undivided.undivided_mean).toBeLessThan(1070);
  expect(report.calibration.background).toBeCloseTo(20, 0);
  for (const fit of report.fits) {
    const sample = doc.samples.find(
      (s: { id: string }) => s.id === fit.sample_id,
    );
    fit.fractions.forEach((fraction: number, i: number) =>
      expect(Math.abs(fraction - known[sample.name][i] / 6000)).toBeLessThan(
        0.025,
      ),
    );
    expect(fit.parameters.peak_ratio).toBe(0.5);
    expect(fit.data.fitted_count).toBe(6000);
    expect(fit.statistics.precursor_frequency).toBeLessThan(
      fit.statistics.observed_divided_fraction,
    );
  }
  const slider = page.getByLabel("Inspect generation histogram bin", {
    exact: true,
  });
  await slider.focus();
  await slider.press("ArrowRight");
  await page.getByRole("button", { name: "G2", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "G2", exact: true }),
  ).toHaveAttribute("aria-pressed", "false");
  await page.getByRole("button", { name: "G2", exact: true }).click();
  await page
    .locator(".population-chart")
    .screenshot({ path: "artifacts/screenshots/proliferation-fit.png" });
  const csvDownload = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "Event probabilities", exact: true })
    .click();
  await (
    await csvDownload
  ).saveAs("artifacts/proliferation-browser-events.csv");
  const rows = readFileSync(
    "artifacts/proliferation-browser-events.csv",
    "utf8",
  )
    .trim()
    .split("\n");
  expect(rows).toHaveLength(6002);
  expect(rows.at(-1)).toMatch(/^6000,nan,nan,nan,nan,nan$/);
  const svgDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "Figure SVG", exact: true }).click();
  await (await svgDownload).saveAs("artifacts/proliferation-browser.svg");
  expect(readFileSync("artifacts/proliferation-browser.svg", "utf8")).toContain(
    "Precursor frequency",
  );
  await page
    .getByLabel("I reviewed the proliferation fit", { exact: true })
    .check();
  await save.click();
  await expect(
    page.getByRole("button", { name: "Explore generation 2", exact: true }),
  ).toBeVisible();
  const saved = await (
    await request.get(`/api/workspaces/${doc.id}`, { headers })
  ).json();
  expect(saved.proliferation_results).toHaveLength(1);
  expect(saved.gates).toHaveLength(8);
  await page
    .getByRole("button", { name: "Expand review", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Fit setup", exact: true }),
  ).toBeHidden();
  await page.locator(".population-chart").screenshot({
    path: "artifacts/screenshots/proliferation-fit-expanded.png",
  });
  await page
    .getByRole("button", { name: "Refit and replace", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Fit setup", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByLabel("Proliferation model name", { exact: true }),
  ).toHaveValue("CFSE browser");
  await page
    .getByRole("button", { name: "Fit proliferation", exact: true })
    .click();
  await expect(save).toBeVisible();
  await expect(save).toBeDisabled();
  await page
    .getByLabel("I reviewed the proliferation fit", { exact: true })
    .check();
  await save.click();
  await expect(
    page.getByRole("button", { name: "Refit and replace", exact: true }),
  ).toBeEnabled();
  const replacement = await (
    await request.get(`/api/workspaces/${doc.id}`, { headers })
  ).json();
  expect(replacement.proliferation_results).toHaveLength(2);
  expect(replacement.gates.map((g: { id: string }) => g.id)).toEqual(
    saved.gates.map((g: { id: string }) => g.id),
  );
  expect(
    replacement.samples.map((s: { computed_parameters: { name: string }[] }) =>
      s.computed_parameters.map((p) => p.name),
    ),
  ).toEqual(
    saved.samples.map((s: { computed_parameters: { name: string }[] }) =>
      s.computed_parameters.map((p) => p.name),
    ),
  );
  await page.getByRole("button", { name: "Rename model", exact: true }).click();
  await page
    .getByLabel("Saved biological model name", { exact: true })
    .fill("CFSE reviewed");
  await page
    .getByRole("button", { name: "Save model name", exact: true })
    .click();
  await expect(
    page.getByRole("button", {
      name: "Review proliferation CFSE reviewed",
      exact: true,
    }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Remove model", exact: true }).click();
  const removal = page.getByRole("region", {
    name: "Review biological model removal",
    exact: true,
  });
  await expect(
    removal.getByText(
      "8 populations, including descendants and Boolean dependents",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(
    removal.getByRole("button", { name: "Confirm model removal", exact: true }),
  ).toBeDisabled();
  await removal
    .getByLabel("Remove dependent biological objects", { exact: true })
    .check();
  await removal
    .getByRole("button", { name: "Confirm model removal", exact: true })
    .click();
  await expect(removal).toBeHidden();
  expect(
    (await (await request.get(`/api/workspaces/${doc.id}`, { headers })).json())
      .gates,
  ).toHaveLength(0);
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await expect
    .poll(
      async () =>
        (
          await (
            await request.get(`/api/workspaces/${doc.id}`, { headers })
          ).json()
        ).gates.length,
    )
    .toBe(8);
  const archive = await request.get(
    `/api/workspaces/${doc.id}/export/project`,
    { headers },
  );
  expect(archive.ok()).toBeTruthy();
  const imported = await request.post("/api/import/project", {
    headers,
    multipart: {
      file: {
        name: "proliferation.cytoforge",
        mimeType: "application/zip",
        buffer: await archive.body(),
      },
    },
  });
  expect(imported.ok(), await imported.text()).toBeTruthy();
  const restored = await imported.json();
  await page.evaluate(
    (id) => localStorage.setItem("cytoforge.workspace", id),
    restored.id,
  );
  await page.reload();
  await page
    .getByRole("button", { name: "Proliferation", exact: true })
    .click();
  await expect(
    page.getByRole("table", { name: "Generation counts", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("Saved model", { exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: "Refit settings", exact: true })
    .click();
  await expect(page.getByLabel("Last generation", { exact: true })).toHaveValue(
    "3",
  );
  await expect(
    page.getByLabel("Undivided control", { exact: true }),
  ).toHaveValue(undivided.id);
  await page
    .getByRole("button", { name: "Explore generation 2", exact: true })
    .click();
  await expect(page.getByLabel("X axis channel", { exact: true })).toHaveValue(
    "CFSE",
  );
  await expect(page.locator('canvas[data-ready="true"]')).toBeVisible();
  expect(errors).toEqual([]);
});
