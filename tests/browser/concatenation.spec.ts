import { test, expect } from "@playwright/test";

test("reviewed merging prevents duplicate saves and exposes exact source event origins", async ({
  page,
  request,
}) => {
  const { token } = await (await request.get("/api/bootstrap")).json();
  const headers = { "X-CytoForge-Token": token };
  let doc = await (
    await request.post("/api/workspaces", {
      headers,
      data: { name: "Merge save guard" },
    })
  ).json();
  for (const [name, csv] of [
    ["First.csv", "X,Y\n1,2\n3,4\n"],
    ["Second.csv", "Y,X\n6,5\n8,7\n"],
  ]) {
    const imported = await request.post(
      `/api/workspaces/${doc.id}/import?revision=${doc.revision}`,
      {
        headers,
        multipart: {
          files: { name, mimeType: "text/csv", buffer: Buffer.from(csv) },
        },
      },
    );
    expect(imported.ok(), await imported.text()).toBeTruthy();
    doc = (await imported.json()).workspace;
  }
  await page.addInitScript(
    (id) => localStorage.setItem("cytoforge.workspace", id),
    doc.id,
  );
  await page.goto("/");
  await page.getByRole("button", { name: "Samples", exact: true }).click();
  await page.getByLabel("Select all visible samples", { exact: true }).check();
  await page
    .getByRole("button", { name: "Concatenate populations", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "Concatenate populations",
    exact: true,
  });
  await dialog
    .getByLabel("Output sample name", { exact: true })
    .fill("Save guard merged");
  const parameter = dialog.getByLabel("Output parameter 2", { exact: true });
  await parameter.fill("");
  await parameter.pressSequentially("Signal Y");
  await expect(parameter).toHaveValue("Signal Y");
  await expect(parameter).toBeFocused();
  await dialog
    .getByRole("button", { name: "Prepare merge", exact: true })
    .click();
  const create = dialog.getByRole("button", {
    name: "Create samples",
    exact: true,
  });
  await expect(create).toBeEnabled();
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  let entered!: () => void;
  const received = new Promise<void>((resolve) => {
    entered = resolve;
  });
  await page.route("**/concatenations/*/apply", async (route) => {
    entered();
    await held;
    await route.continue();
  });
  try {
    await create.click();
    await received;
    await expect(
      dialog.getByRole("button", { name: "Creating samples…", exact: true }),
    ).toBeDisabled();
    await expect(
      dialog.getByRole("button", { name: "Discard / cancel", exact: true }),
    ).toBeDisabled();
    const unchanged = await (
      await request.get(`/api/workspaces/${doc.id}`, { headers })
    ).json();
    expect(unchanged.revision).toBe(doc.revision);
    expect(unchanged.samples).toEqual(doc.samples);
  } finally {
    release();
  }
  await expect(dialog).toHaveCount(0);
  await page
    .getByRole("button", { name: "Origins for Save guard merged", exact: true })
    .click();
  const origins = page.getByRole("dialog", {
    name: "Merged event origins",
    exact: true,
  });
  await expect(origins.locator(".concat-origins tbody tr")).toHaveCount(4);
  const cells = await origins
    .locator(".concat-origins tbody tr")
    .allTextContents();
  expect(cells[0]).toContain("First");
  expect(cells[2]).toContain("Second");
  const after = await (
    await request.get(`/api/workspaces/${doc.id}`, { headers })
  ).json();
  expect(after.revision).toBe(doc.revision + 1);
  expect(after.samples).toHaveLength(3);
  expect(after.samples.slice(0, 2)).toEqual(doc.samples);
});
