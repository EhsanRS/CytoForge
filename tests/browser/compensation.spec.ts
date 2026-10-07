import { test, expect, type APIRequestContext } from "@playwright/test";
import { mkdirSync } from "node:fs";

async function controls(request: APIRequestContext, spectral = false) {
  const { token } = await (await request.get("/api/bootstrap")).json();
  const headers = { "X-CytoForge-Token": token };
  let doc = await (
    await request.post("/api/workspaces", {
      headers,
      data: {
        name: spectral
          ? "Spectral control validation"
          : "Single-stain validation",
      },
    })
  ).json();
  const detectors = spectral ? ["D1", "D2", "D3", "D4"] : ["X", "Y"];
  const spectra = spectral
    ? [
        [1, 0.2, 0.1, 0.05],
        [0.1, 1, 0.3, 0.2],
        [0.3, 0.2, 0.5, 1],
      ]
    : [
        [1, 0.2],
        [0.1, 1],
      ];
  const background = spectral ? [2, 3, 4, 5] : [10, 5];
  for (const [index, label] of [
    "Unstained",
    "Stain 1",
    "Stain 2",
    "Mixture",
  ].entries()) {
    const rows = Array.from({ length: 120 }, (_, i) => {
      const jitter = (i - 59.5) * 0.06;
      const trueValues =
        index === 0
          ? [0, 0, spectral ? 30 + jitter : 0]
          : index === 1
            ? [100 + jitter, 0, spectral ? 30 : 0]
            : index === 2
              ? [0, 200 + jitter, spectral ? 30 : 0]
              : [10 + jitter, 20 + jitter, spectral ? 30 + jitter : 0];
      return background
        .map(
          (b, j) =>
            b +
            trueValues
              .slice(0, spectra.length)
              .reduce((v, t, k) => v + t * spectra[k][j], 0) +
            (spectral ? 0 : jitter / 10),
        )
        .join(",");
    });
    const imported = await request.post(
      `/api/workspaces/${doc.id}/import?revision=${doc.revision}`,
      {
        headers,
        multipart: {
          files: {
            name: `${label}.csv`,
            mimeType: "text/csv",
            buffer: Buffer.from(`${detectors.join(",")}\n${rows.join("\n")}\n`),
          },
        },
      },
    );
    expect(imported.ok(), await imported.text()).toBeTruthy();
    doc = (await imported.json()).workspace;
  }
  return { doc, headers, spectra };
}

for (const spectral of [false, true]) {
  test(
    spectral
      ? "spectral control wizard extracts AF and exposes usable output parameters"
      : "single-stain wizard calculates, reviews and applies conventional spillover",
    async ({ page, request }) => {
      mkdirSync("artifacts/screenshots", { recursive: true });
      const { doc, headers, spectra } = await controls(request, spectral);
      const errors: string[] = [];
      page.on("pageerror", (e) => errors.push(e.message));
      await page.addInitScript(
        (id) => localStorage.setItem("cytoforge.workspace", id),
        doc.id,
      );
      await page.goto("/");
      await page
        .getByRole("button", { name: "Compensation", exact: true })
        .click();
      await page
        .getByRole("button", { name: "Control wizard", exact: true })
        .click();
      const wizard = page.getByRole("dialog");
      if (spectral) {
        await wizard.getByLabel("Correction method").selectOption("spectral");
        await wizard
          .getByLabel("Control 1 name", { exact: true })
          .fill("Fluor 1");
        await wizard
          .getByLabel("Control 2 name", { exact: true })
          .fill("Fluor 2");
        await wizard
          .getByLabel("Electronic background", { exact: true })
          .fill("2, 3, 4, 5");
        await wizard
          .getByLabel("Detector weights", { exact: true })
          .fill("1, 2, 0.5, 3");
        await wizard
          .getByLabel("Extract autofluorescence as a separate output")
          .check();
        await wizard
          .getByLabel("Autofluorescence reference sample")
          .selectOption(doc.samples[0].id);
      }
      await wizard
        .getByText("Reuse a negative reference", { exact: true })
        .click();
      await wizard
        .getByLabel("Shared negative sample", { exact: true })
        .selectOption(doc.samples[0].id);
      await wizard
        .getByRole("button", { name: "Use for all controls", exact: true })
        .click();
      await wizard
        .getByLabel("Control 1 positive sample", { exact: true })
        .selectOption(doc.samples[1].id);
      await wizard
        .getByLabel("Control 2 positive sample", { exact: true })
        .selectOption(doc.samples[2].id);
      await wizard
        .getByRole("button", { name: "Calculate & review", exact: true })
        .click();
      await expect(wizard.getByText(/Rank [23] · condition/)).toBeVisible();
      await expect(
        wizard.getByRole("cell", { name: "20%", exact: true }).first(),
      ).toBeVisible();
      await expect(wizard.getByRole("img").first()).toBeVisible();
      await wizard
        .locator(".control-targets")
        .getByLabel(doc.samples[0].name, { exact: true })
        .uncheck();
      await wizard
        .locator(".control-targets")
        .getByLabel(doc.samples[3].name, { exact: true })
        .check();
      await wizard.evaluate((element) => (element.scrollTop = 0));
      await page.screenshot({
        path: `artifacts/screenshots/${spectral ? "spectral" : "single-stain"}-control-review.png`,
        fullPage: true,
      });
      await wizard
        .getByRole("button", {
          name: "Save & apply control matrix",
          exact: true,
        })
        .click();
      await expect(wizard).not.toBeVisible();
      const state = await (
        await request.get(`/api/workspaces/${doc.id}`, { headers })
      ).json();
      const matrix = state.compensations[0];
      matrix.matrix.forEach((row: number[], i: number) =>
        row.forEach((value, j) => expect(value).toBeCloseTo(spectra[i][j], 10)),
      );
      expect(state.samples[3].compensation_id).toBe(matrix.id);
      expect(matrix.provenance.samples).toHaveLength(3);
      if (spectral) {
        expect(state.samples[3].unmixed_parameters).toEqual([
          "Fluor 1",
          "Fluor 2",
          "Autofluorescence",
        ]);
        const sample = page
          .locator(".sidebar-samples")
          .getByRole("button")
          .filter({ hasText: doc.samples[3].name });
        await sample.click();
        await page
          .getByLabel("X axis channel", { exact: true })
          .selectOption("Fluor 1");
        await page
          .getByLabel("Y axis channel", { exact: true })
          .selectOption("Fluor 2");
        await expect(page.locator('canvas[data-ready="true"]')).toBeVisible();
        await expect(
          page.getByRole("img", { name: /density plot/ }),
        ).toHaveAttribute("aria-label", /120 events/);
        const stats = await (
          await request.get(
            `/api/workspaces/${doc.id}/samples/${doc.samples[3].id}/statistics?channel=Fluor%201`,
            { headers },
          )
        ).json();
        expect(stats.median).toBeCloseTo(10, 10);
        expect(stats.finite_count).toBe(120);
        await page.screenshot({
          path: "artifacts/screenshots/spectral-population.png",
          fullPage: true,
        });
      }
      expect(errors).toEqual([]);
    },
  );
}
