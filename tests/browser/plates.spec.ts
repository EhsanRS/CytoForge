import {
  expect,
  test,
  type Page,
  type APIRequestContext,
} from "@playwright/test";
import { mkdirSync, readFileSync } from "node:fs";
import { plateFixtures } from "../../tools/plate_fixture.mjs";

async function setup(page: Page, request: APIRequestContext) {
  const { token } = await (await request.get("/api/bootstrap")).json();
  const headers = { "X-CytoForge-Token": token };
  let doc = await (
    await request.post("/api/workspaces", {
      headers,
      data: { name: "Plate independent validation" },
    })
  ).json();
  for (const fixture of plateFixtures()) {
    const imported = await request.post(
      `/api/workspaces/${doc.id}/import?revision=${doc.revision}`,
      {
        headers,
        multipart: {
          files: {
            name: `${fixture.name}.csv`,
            mimeType: "text/csv",
            buffer: Buffer.from(fixture.csv),
          },
        },
      },
    );
    expect(imported.ok(), await imported.text()).toBeTruthy();
    doc = (await imported.json()).workspace;
    const sample = doc.samples.at(-1);
    const tagged = await request.patch(
      `/api/workspaces/${doc.id}/samples/${sample.id}`,
      {
        headers,
        data: {
          revision: doc.revision,
          name: fixture.name,
          tags: fixture.tags,
        },
      },
    );
    expect(tagged.ok(), await tagged.text()).toBeTruthy();
    doc = await tagged.json();
  }
  await page.addInitScript(
    (id) => localStorage.setItem("cytoforge.workspace", id),
    doc.id,
  );
  await page.goto("/");
  await page.getByRole("button", { name: "Plates", exact: true }).click();
  const ready = () =>
    expect(page.locator(".plate-grid-status")).toHaveAttribute(
      "data-ready",
      "true",
    );
  const read = async () =>
    (await request.get(`/api/workspaces/${doc.id}`, { headers })).json();
  await ready();
  return { doc, headers, read, ready };
}
async function map(page: Page) {
  await page.getByRole("tab", { name: "Import & titration" }).click();
  await page
    .getByRole("button", { name: "Preview keyword mapping", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toContainText("Duplicate well");
  await expect(
    page.getByRole("button", { name: "Save discovered plates" }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "Keep editing", exact: true }).click();
  await page.getByLabel("Include plate mapping replicates").check();
  await page
    .getByRole("button", { name: "Preview keyword mapping", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toContainText("Invalid well");
  await page.getByLabel("I reviewed unresolved plate items").check();
  await page
    .getByRole("button", { name: "Save discovered plates", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
}

test("plate annotations, exact replicate statistics, formulas, dilution, groups and draft conflicts", async ({
  page,
  request,
}) => {
  test.setTimeout(120000);
  mkdirSync("artifacts/screenshots", { recursive: true });
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const { headers, read, ready } = await setup(page, request);
  await map(page);
  await ready();
  let doc = await read();
  expect(doc.plates).toHaveLength(1);
  expect(doc.plates[0].assignments.A01).toHaveLength(2);
  expect(doc.plates[0].assignments.A00).toBeUndefined();
  await page.getByLabel("Plate CSV plate column").fill("Plate ID");
  await page.getByLabel("Plate annotation CSV file").setInputFiles({
    name: "planned.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(
      '\ufeffPlate ID,Well ID,Treatment,Dose\nPlate P1,A1,"Drug, A",1\nPlate P1,A02,vehicle,0.5\nPlate P1,C01,duplicate,2\nPlate P1,C1,conflict,3\nPlate P1,B03,planned,0\nPlate P1,A0,bad,0\nAnother plate,A03,other,9\n',
    ),
  });
  await expect(page.getByRole("dialog")).toContainText("3 valid well rows");
  await expect(page.getByRole("dialog")).toContainText("1 rows skipped");
  await expect(page.getByRole("dialog")).toContainText(
    "Duplicate normalized well rows",
  );
  await expect(
    page.getByRole("button", { name: "Stage reviewed plan" }),
  ).toBeDisabled();
  await page.getByLabel("I reviewed unresolved plate items").check();
  await page
    .getByRole("button", { name: "Stage reviewed plan", exact: true })
    .click();
  expect((await read()).samples[0].tags.Treatment).toBe("original");
  await page.getByRole("tab", { name: "Well & annotations" }).click();
  await page
    .getByRole("button", { name: "Review annotation changes", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toContainText(
    "6 keyword changes across 3 acquisitions",
  );
  await expect(page.getByRole("dialog")).toContainText("B03");
  await page
    .getByRole("button", { name: "Apply reviewed annotations", exact: true })
    .click();
  await ready();
  doc = await read();
  expect(doc.samples[0].tags.Treatment).toBe("Drug, A");
  expect(doc.samples[1].tags.Treatment).toBe("Drug, A");
  expect(doc.samples[2].tags.Dose).toBe("0.5");
  expect(doc.plates[0].annotations.B03.Dose).toBe("0");
  expect(doc.plates[0].annotations.C01).toBeUndefined();
  await page.getByRole("tab", { name: "Measurements", exact: true }).click();
  async function add(name: string) {
    await page
      .getByRole("button", { name: "Add measurement", exact: true })
      .click();
    await page.getByLabel("Plate measurement name", { exact: true }).fill(name);
  }
  await add("MFI");
  await page
    .getByLabel("Plate measurement statistic", { exact: true })
    .selectOption("median");
  await page
    .getByLabel("Plate measurement parameter", { exact: true })
    .selectOption("X");
  await page
    .getByLabel("Plate primary measure", { exact: true })
    .selectOption({ label: "MFI" });
  await ready();
  await expect(page.locator('[data-well="A01"] .plate-well-value')).toHaveText(
    "76.25",
  );
  await expect(page.locator('[data-well="A02"] .plate-well-value')).toHaveText(
    "20",
  );
  await expect(page.locator('[data-well="B01"] .plate-well-value')).toHaveText(
    "0",
  );
  await expect(page.locator('[data-well="B02"] .plate-well-value')).toHaveText(
    "—",
  );
  const zero = await page
    .locator('[data-well="B01"]')
    .evaluate((e) => getComputedStyle(e).backgroundColor);
  const missing = await page
    .locator('[data-well="B02"]')
    .evaluate((e) => getComputedStyle(e).backgroundColor);
  expect(zero).not.toBe(missing);
  await page
    .getByText("Measurement scaling and replicate policy", { exact: true })
    .click();
  await page
    .getByLabel("Plate bounds measure", { exact: true })
    .selectOption({ label: "MFI" });
  await page.getByLabel("Plate display minimum").fill("5e-324");
  await page.getByLabel("Plate display maximum").fill("1e-323");
  await page.getByRole("button", { name: "Apply display bounds" }).click();
  await ready();
  await expect(page.locator(".plate-numeric-legend")).toContainText("E-324");
  await expect(page.locator(".plate-numeric-legend")).toContainText("E-323");
  await expect(page.locator('[data-well="A01"] .plate-well-value')).toHaveText(
    "76.25",
  );
  await expect(page.locator(".error-state")).toHaveCount(0);
  await page.getByRole("button", { name: "Use automatic bounds" }).click();
  await ready();
  await add("Dose");
  await page
    .getByLabel("Plate measurement type", { exact: true })
    .selectOption("metadata");
  await page
    .getByLabel("Plate measurement keyword", { exact: true })
    .fill("Dose");
  await page.getByLabel("Numeric plate keyword", { exact: true }).check();
  await add("Normalized");
  await page
    .getByLabel("Plate measurement type", { exact: true })
    .selectOption("formula");
  await page
    .getByLabel("Plate measurement formula", { exact: true })
    .fill('col("MFI")/col("Dose")');
  await ready();
  await page
    .locator(".plate-column-list")
    .getByRole("button", { name: "MFI", exact: true })
    .click();
  await page
    .getByLabel("Plate measurement name", { exact: true })
    .fill("Signal");
  await ready();
  await expect(page.locator(".error-state")).toHaveCount(0);
  await page.getByLabel("Plate view", { exact: true }).selectOption("split");
  await page
    .getByLabel("Plate secondary measure", { exact: true })
    .selectOption({ label: "Normalized" });
  await ready();
  await page.getByRole("button", { name: "Save plate", exact: true }).click();
  await ready();
  doc = await read();
  const result = await (
    await request.get(
      `/api/workspaces/${doc.id}/plates/${doc.plates[0].id}/evaluate`,
      { headers },
    )
  ).json();
  const normalized = doc.plates[0].columns.find((c) => c.name === "Normalized");
  expect(result.wells[0].values[normalized.id]).toBe(76.25);
  expect(result.wells[1].values[normalized.id]).toBe(40);
  expect(Object.keys(normalized.formula_refs)).toContain("MFI");
  await page.getByLabel("Plate view", { exact: true }).selectOption("faces");
  await ready();
  await expect(
    page.locator('[data-well="A01"] svg ellipse').first(),
  ).toBeVisible();
  await expect(page.locator(".plate-legend")).toContainText(
    "Head width: Events",
  );
  await page.locator(".main-content").evaluate((e) => (e.scrollTop = 0));
  await page.screenshot({ path: "artifacts/screenshots/plate-faces.png" });
  const file = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export plate", exact: true }).click();
  const downloaded = await file;
  await downloaded.saveAs("artifacts/browser-plate.svg");
  expect(readFileSync("artifacts/browser-plate.svg", "utf8")).toContain(
    "Signal",
  );
  await page.getByRole("tab", { name: "Import & titration" }).click();
  await page.getByLabel("Plate series keyword").fill("Dose");
  await page.getByLabel("Plate series steps").fill("3");
  await page.getByLabel("Plate series replicates").fill("2");
  await page.getByLabel("Plate series starting value").fill("8");
  await page.getByLabel("Plate series unit", { exact: true }).fill("nM");
  await page
    .getByRole("button", { name: "Preview dilution series", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toContainText("6 planned well values");
  await page
    .getByRole("button", { name: "Stage reviewed plan", exact: true })
    .click();
  expect((await read()).samples[0].tags.Dose).toBe("1");
  await page.getByRole("tab", { name: "Well & annotations" }).click();
  await page
    .getByRole("button", { name: "Review annotation changes", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Apply reviewed annotations", exact: true })
    .click();
  await ready();
  doc = await read();
  expect(doc.samples[0].tags.Dose).toBe("8");
  expect(doc.samples[2].tags.Dose).toBe("4");
  expect(doc.samples[3].tags.Dose).toBe("8");
  await page.getByLabel("Plate view", { exact: true }).selectOption("heatmap");
  await page
    .getByLabel("Plate primary measure", { exact: true })
    .selectOption({ label: "Normalized" });
  await ready();
  await expect(page.locator('[data-well="A01"] .plate-well-value')).toHaveText(
    "9.53",
  );
  await expect(page.locator('[data-well="A02"] .plate-well-value')).toHaveText(
    "5",
  );
  await page.locator('[data-well="A01"]').click();
  await page.locator('[data-well="B02"]').click({ modifiers: ["Shift"] });
  await expect(page.locator('.plate-well[aria-pressed="true"]')).toHaveCount(4);
  await page.getByLabel("Plate selection group name").fill("Dose rectangle");
  await page
    .getByRole("button", {
      name: "Create group from selected wells",
      exact: true,
    })
    .click();
  await ready();
  doc = await read();
  expect(
    doc.groups.find((g) => g.name === "Dose rectangle").sample_ids,
  ).toHaveLength(4);
  await expect(
    page.locator(".plate-notice").filter({ hasText: "changed in another" }),
  ).toHaveCount(0);
  await page
    .getByLabel("Plate name", { exact: true })
    .fill("Unsaved plate review");
  await page.getByRole("button", { name: "Analysis", exact: true }).click();
  await page.getByRole("button", { name: "Plates", exact: true }).click();
  await expect(page.getByLabel("Plate name", { exact: true })).toHaveValue(
    "Unsaved plate review",
  );
  doc = await read();
  const externallyEdited = await request.post(
    `/api/workspaces/${doc.id}/plates/save`,
    {
      headers,
      data: {
        revision: doc.revision,
        plate: { ...doc.plates[0], name: "External plate revision" },
      },
    },
  );
  expect(externallyEdited.ok(), await externallyEdited.text()).toBeTruthy();
  await page.reload();
  await page.getByRole("button", { name: "Plates", exact: true }).click();
  await ready();
  await expect(page.getByLabel("Plate name", { exact: true })).toHaveValue(
    "Unsaved plate review",
  );
  await expect(
    page.getByText("This saved plate changed in another action.", {
      exact: false,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Save plate", exact: true }),
  ).toBeDisabled();
  await page
    .getByRole("button", { name: "Reload saved plate", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Discard draft and open", exact: true })
    .click();
  await ready();
  await expect(page.getByLabel("Plate name", { exact: true })).toHaveValue(
    "External plate revision",
  );
  expect(errors).toEqual([]);
});

test("plate format changes, keyboard selection and plan undo preserve outside wells until reviewed", async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const { ready, read } = await setup(page, request);
  await map(page);
  await ready();
  await page.getByLabel("Plate format", { exact: true }).selectOption("1536");
  await page
    .getByRole("button", { name: "Stage reviewed plan", exact: true })
    .click();
  await ready();
  await expect(page.locator(".plate-well")).toHaveCount(1536);
  await page
    .getByRole("button", { name: "Select plate row AF", exact: true })
    .click();
  await expect(page.locator('.plate-well[aria-pressed="true"]')).toHaveCount(
    48,
  );
  await page.locator('[data-well="AF48"]').click();
  await page.getByRole("tab", { name: "Well & annotations" }).click();
  await page.getByLabel("Plate annotation value").fill("outside plan");
  await page
    .getByRole("button", { name: "Stage annotation", exact: true })
    .click();
  await page.getByRole("button", { name: "Save plate", exact: true }).click();
  await ready();
  expect((await read()).plates[0].annotations.AF48.Treatment).toBe(
    "outside plan",
  );
  await page.getByLabel("Plate format", { exact: true }).selectOption("96");
  await expect(page.getByRole("dialog")).toContainText(
    "1 planned annotation keys removed",
  );
  expect((await read()).plates[0].format).toBe(1536);
  await page
    .getByRole("button", { name: "Stage reviewed plan", exact: true })
    .click();
  await ready();
  await expect(page.locator(".plate-well")).toHaveCount(96);
  await page
    .getByRole("button", { name: "Undo plate draft", exact: true })
    .click();
  await ready();
  await expect(page.locator(".plate-well")).toHaveCount(1536);
  await page
    .getByRole("button", { name: "Redo plate draft", exact: true })
    .click();
  await ready();
  await expect(page.locator(".plate-well")).toHaveCount(96);
  await page.locator('[data-well="A01"]').click();
  await page.locator('[data-well="A01"]').press("ArrowRight");
  await expect(page.locator('[data-well="A02"]')).toBeFocused();
  await page.locator('[data-well="A02"]').press("Shift+ArrowDown");
  await expect(page.locator('.plate-well[aria-pressed="true"]')).toHaveCount(2);
  await page.getByRole("button", { name: "Save plate", exact: true }).click();
  await ready();
  expect((await read()).plates[0].annotations.AF48).toBeUndefined();
  await page.setViewportSize({ width: 1050, height: 800 });
  const overflow = await page
    .locator(".plate-workbench")
    .evaluate((e) => e.scrollWidth > e.clientWidth);
  expect(overflow).toBeFalsy();
  expect(errors).toEqual([]);
});
