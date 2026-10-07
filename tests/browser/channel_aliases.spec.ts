import { test, expect } from "@playwright/test";
import type { APIRequestContext, Page } from "@playwright/test";

async function experiment(request: APIRequestContext, page: Page) {
  const { token } = await (await request.get("/api/bootstrap")).json();
  const headers = { "X-CytoForge-Token": token };
  let doc = await (
    await request.post("/api/workspaces", {
      headers,
      data: { name: "Two panel alias review" },
    })
  ).json();
  for (const [name, csv] of [
    ["Panel A.csv", "FL1,FL2,FL3\n1,2,9\n3,4,8\n5,6,7\n7,8,6\n"],
    ["Panel B.csv", "B2,B1,B3\n2,1,9\n4,3,8\n6,5,7\n8,7,6\n"],
  ]) {
    const response = await request.post(
      `/api/workspaces/${doc.id}/import?revision=${doc.revision}`,
      {
        headers,
        multipart: {
          files: { name, mimeType: "text/csv", buffer: Buffer.from(csv) },
        },
      },
    );
    expect(response.ok(), await response.text()).toBeTruthy();
    doc = (await response.json()).workspace;
  }
  await page.addInitScript(
    (id) => localStorage.setItem("cytoforge.workspace", id),
    doc.id,
  );
  await page.goto("/");
  await page.getByRole("button", { name: "Samples", exact: true }).click();
  await page.getByLabel("Select all visible samples").check();
  await page
    .getByRole("button", { name: "Harmonize panel", exact: true })
    .click();
  return { doc, headers };
}

async function map(page: Page, name: string, sources: string[]) {
  const dialog = page.getByRole("dialog", {
    name: "Harmonize panel",
    exact: true,
  });
  await dialog
    .getByRole("button", { name: "Add shared parameter", exact: true })
    .click();
  const names = dialog.locator('input[aria-label^="Shared name"]');
  await names.last().fill(name);
  const choices = dialog.locator(
    `select[aria-label^="Source for ${name} in "]`,
  );
  await expect(choices).toHaveCount(sources.length);
  for (let i = 0; i < sources.length; i++)
    await choices.nth(i).selectOption(sources[i]);
}

test("mapping is reviewed, applied once, and plotted through the shared names", async ({
  page,
  request,
}) => {
  const { doc, headers } = await experiment(request, page);
  await map(page, "CD3", ["FL1", "B1"]);
  await map(page, "CD4", ["FL2", "B2"]);
  const dialog = page.getByRole("dialog", {
    name: "Harmonize panel",
    exact: true,
  });
  await dialog
    .getByRole("button", { name: "Review mapping", exact: true })
    .click();
  await expect(dialog.getByLabel("Channel mapping review")).toContainText(
    "FL1",
  );
  await expect(dialog.getByLabel("Channel mapping review")).toContainText("B1");
  const before = await (
    await request.get(`/api/workspaces/${doc.id}`, { headers })
  ).json();
  expect(before.revision).toBe(doc.revision);
  expect(
    before.samples.every((s: { aliases?: object }) => !s.aliases),
  ).toBeTruthy();
  let applied = 0;
  page.on("request", (r) => {
    if (r.url().endsWith("/channel-aliases/apply")) applied++;
  });
  await dialog
    .getByRole("button", { name: "Apply channel aliases", exact: true })
    .click();
  await expect(dialog).toHaveCount(0);
  const after = await (
    await request.get(`/api/workspaces/${doc.id}`, { headers })
  ).json();
  expect(after.revision).toBe(doc.revision + 1);
  expect(applied).toBe(1);
  expect(after.samples.map((s: { aliases: object }) => s.aliases)).toEqual([
    { CD3: "FL1", CD4: "FL2" },
    { CD3: "B1", CD4: "B2" },
  ]);
  await page.locator(".sample-name").first().click();
  await page.getByLabel("X axis channel", { exact: true }).selectOption("CD3");
  await page.getByLabel("Y axis channel", { exact: true }).selectOption("CD4");
  await expect(page.getByLabel("X axis channel", { exact: true })).toHaveValue(
    "CD3",
  );
  await expect(page.getByLabel("Y axis channel", { exact: true })).toHaveValue(
    "CD4",
  );
  await expect(
    page.locator('.primary-plot canvas[data-ready="true"]').first(),
  ).toBeVisible();
});

test("invalid mapping keeps the dialog editable and leaves all samples unchanged", async ({
  page,
  request,
}) => {
  const { doc, headers } = await experiment(request, page);
  await map(page, "CD3", ["FL1", "B1"]);
  const dialog = page.getByRole("dialog", {
    name: "Harmonize panel",
    exact: true,
  });
  await dialog.locator('input[aria-label^="Shared name"]').fill("B2");
  await dialog
    .getByRole("button", { name: "Review mapping", exact: true })
    .click();
  await expect(dialog.getByRole("alert")).toContainText("B2 already exists");
  await expect(
    dialog.locator('input[aria-label^="Shared name"]'),
  ).toBeEnabled();
  const after = await (
    await request.get(`/api/workspaces/${doc.id}`, { headers })
  ).json();
  expect(after).toEqual(doc);
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(dialog).toHaveCount(0);
});

test("a concurrent edit disables applying the reviewed mapping", async ({
  page,
  request,
}) => {
  const { doc, headers } = await experiment(request, page);
  await map(page, "CD3", ["FL1", "B1"]);
  const dialog = page.getByRole("dialog", {
    name: "Harmonize panel",
    exact: true,
  });
  await dialog
    .getByRole("button", { name: "Review mapping", exact: true })
    .click();
  await expect(
    dialog.getByRole("button", { name: "Apply channel aliases", exact: true }),
  ).toBeEnabled();
  const updated = await request.patch(`/api/workspaces/${doc.id}`, {
    headers,
    data: { revision: doc.revision, name: "Changed during alias review" },
  });
  expect(updated.ok(), await updated.text()).toBeTruthy();
  await expect(dialog.getByRole("alert")).toContainText("Workspace changed");
  await expect(
    dialog.getByRole("button", { name: "Apply channel aliases", exact: true }),
  ).toBeDisabled();
  const after = await (
    await request.get(`/api/workspaces/${doc.id}`, { headers })
  ).json();
  expect(
    after.samples.every((s: { aliases?: object }) => !s.aliases),
  ).toBeTruthy();
});
