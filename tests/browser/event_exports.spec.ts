import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";

test("a recovered preparation can be resumed and discarded from its sample dialog", async ({
  page,
  request,
}) => {
  const { token } = await (await request.get("/api/bootstrap")).json();
  const headers = { "X-CytoForge-Token": token };
  const doc = await (
    await request.post("/api/demo", { headers, data: {} })
  ).json();
  const job = await (
    await request.post(`/api/workspaces/${doc.id}/event-exports`, {
      headers,
      data: {
        revision: doc.revision,
        sample_id: doc.samples[0].id,
        format: "csv",
        values: "compensated",
      },
    })
  ).json();
  await expect
    .poll(
      async () =>
        (
          await (
            await request.get(
              `/api/workspaces/${doc.id}/event-exports/${job.id}`,
              { headers },
            )
          ).json()
        ).status,
    )
    .toBe("ready");
  await page.addInitScript(
    (id) => localStorage.setItem("cytoforge.workspace", id),
    doc.id,
  );
  await page.goto("/");
  await page
    .getByRole("button", { name: "Export events", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "Export events",
    exact: true,
  });
  await dialog
    .getByRole("button", { name: "Resume CSV export", exact: true })
    .click();
  await expect(
    dialog.getByRole("button", { name: "Save event file", exact: true }),
  ).toBeEnabled();
  await expect(dialog).toContainText("Compensated / unmixed values");
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  const cancelled = await (
    await request.get(`/api/workspaces/${doc.id}/event-exports/${job.id}`, {
      headers,
    })
  ).json();
  expect(cancelled.status).toBe("cancelled");
  expect(
    (await (await request.get(`/api/workspaces/${doc.id}`, { headers })).json())
      .revision,
  ).toBe(doc.revision);
});

test("event export reviews the current population and saves precise values without changing history", async ({
  page,
  request,
}) => {
  const { token } = await (await request.get("/api/bootstrap")).json();
  const headers = { "X-CytoForge-Token": token };
  let doc = await (
    await request.post("/api/workspaces", {
      headers,
      data: { name: "Event save precision" },
    })
  ).json();
  const imported = await request.post(
    `/api/workspaces/${doc.id}/import?revision=${doc.revision}`,
    {
      headers,
      multipart: {
        files: {
          name: "Precise.csv",
          mimeType: "text/csv",
          buffer: Buffer.from(
            "X,Y\n1.00000000000001,2.00000000000001\n3.00000000000001,4.00000000000001\n",
          ),
        },
      },
    },
  );
  expect(imported.ok()).toBeTruthy();
  doc = (await imported.json()).workspace;
  const before = JSON.stringify(doc);
  await page.addInitScript(
    (id) => localStorage.setItem("cytoforge.workspace", id),
    doc.id,
  );
  await page.goto("/");
  await page
    .getByRole("button", { name: "Export events", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "Export events",
    exact: true,
  });
  await expect(dialog.getByLabel("Export population")).toHaveValue("");
  await dialog.getByLabel("Event export format").selectOption("csv");
  await dialog
    .getByRole("button", { name: "Prepare export", exact: true })
    .click();
  const save = dialog.getByRole("button", {
    name: "Save event file",
    exact: true,
  });
  await expect(save).toBeEnabled();
  await expect(dialog).toContainText("2 events");
  const received = page.waitForEvent("download");
  await save.click();
  const file = await received;
  const filename = "artifacts/event-export-browser.csv";
  await file.saveAs(filename);
  expect(readFileSync(filename, "utf8").trim().split(/\r?\n/)).toEqual([
    "X,Y",
    "1.00000000000001,2.00000000000001",
    "3.00000000000001,4.00000000000001",
  ]);
  await expect(dialog).toHaveCount(0);
  expect(
    JSON.stringify(
      await (
        await request.get(`/api/workspaces/${doc.id}`, { headers })
      ).json(),
    ),
  ).toBe(before);
});

test("a concurrent revision blocks saving prepared events and cancellation closes the dialog", async ({
  page,
  request,
}) => {
  const { token } = await (await request.get("/api/bootstrap")).json();
  const headers = { "X-CytoForge-Token": token };
  const doc = await (
    await request.post("/api/demo", { headers, data: {} })
  ).json();
  await page.addInitScript(
    (id) => localStorage.setItem("cytoforge.workspace", id),
    doc.id,
  );
  await page.goto("/");
  await page
    .getByRole("button", { name: "Export events", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "Export events",
    exact: true,
  });
  await dialog
    .getByRole("button", { name: "Prepare export", exact: true })
    .click();
  await expect(
    dialog.getByRole("button", { name: "Save event file", exact: true }),
  ).toBeEnabled();
  const changed = await request.patch(
    `/api/workspaces/${doc.id}/samples/${doc.samples[0].id}`,
    {
      headers,
      data: { revision: doc.revision, name: "Changed during export", tags: {} },
    },
  );
  expect(changed.ok()).toBeTruthy();
  await expect(
    dialog.getByRole("button", { name: "Save event file", exact: true }),
  ).toBeDisabled();
  await expect(dialog.getByRole("alert")).toContainText("workspace changed");
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(dialog).toHaveCount(0);
});
