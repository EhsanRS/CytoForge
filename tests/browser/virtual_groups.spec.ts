import {
  test,
  expect,
  type Page,
  type APIRequestContext,
} from "@playwright/test";

async function setup(request: APIRequestContext, page: Page) {
  const { token } = await (await request.get("/api/bootstrap")).json();
  const headers = { "X-CytoForge-Token": token };
  const send = async (route: string, data: unknown) => {
    const response = await request.post(`/api${route}`, { headers, data });
    expect(response.ok(), await response.text()).toBeTruthy();
    return response.json();
  };
  let doc = await send("/workspaces", { name: "Pooled parameter truth" });
  for (const [name, csv] of [
    ["Panel A.csv", "FL1,FL2\n1,2\n3,4\n5,6\n7,8\n"],
    ["Panel B.csv", "B2,B1\n2,1\n4,3\n6,5\n8,7\n"],
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
  const binding = {
    revision: doc.revision,
    mappings: doc.samples.map((s: { id: string }, i: number) => ({
      sample_id: s.id,
      bindings: [
        { name: "CD3", source: i ? "B1" : "FL1" },
        { name: "CD4", source: i ? "B2" : "FL2" },
      ],
    })),
  };
  const review = await send(
    `/workspaces/${doc.id}/channel-aliases/preview`,
    binding,
  );
  doc = await send(`/workspaces/${doc.id}/channel-aliases/apply`, {
    ...binding,
    review_hash: review.review_hash,
  });
  const group = {
    name: "Pooled panels",
    sample_ids: doc.samples.map((s: { id: string }) => s.id),
  };
  doc = await send(`/workspaces/${doc.id}/groups`, {
    revision: doc.revision,
    group,
  });
  const gates = doc.samples.map((s: { id: string }, i: number) => ({
    sample_id: s.id,
    name: "Positive",
    kind: "range",
    x: "CD3",
    bounds: i ? [4, 8] : [0, 4],
  }));
  doc = await send(`/workspaces/${doc.id}/gates/batch`, {
    revision: doc.revision,
    gates,
  });
  await page.addInitScript(
    (id) => localStorage.setItem("cytoforge.workspace", id),
    doc.id,
  );
  await page.goto("/");
  await page
    .getByLabel("Plot sample group", { exact: true })
    .selectOption(doc.groups[0].id);
  await page.getByLabel("Pool plot group", { exact: true }).click();
  await expect(
    page.locator('.primary-plot canvas[data-ready="true"]').first(),
  ).toBeVisible();
  await expect(page.locator(".metric-row strong").first()).toHaveText("8");
  return { doc, headers };
}

async function draft(page: Page, name: string) {
  await page.getByRole("button", { name: "Histogram", exact: true }).click();
  await expect(
    page.locator('.primary-plot canvas[data-ready="true"]').first(),
  ).toBeVisible();
  await page.getByRole("button", { name: /^Range gate/ }).click();
  const box = await page
    .locator('.primary-plot canvas[data-ready="true"]')
    .first()
    .boundingBox();
  expect(box).toBeTruthy();
  await page.mouse.move(
    box!.x + 64 + (box!.width - 88) * 0.25,
    box!.y + box!.height * 0.5,
  );
  await page.mouse.down();
  await page.mouse.move(
    box!.x + 64 + (box!.width - 88) * 0.75,
    box!.y + box!.height * 0.5,
    { steps: 12 },
  );
  await page.mouse.up();
  const dialog = page.getByRole("dialog", {
    name: "Create population",
    exact: true,
  });
  await dialog.getByLabel("Population name", { exact: true }).fill(name);
  await dialog
    .getByRole("button", { name: "Create population", exact: true })
    .click();
  const review = page.getByRole("dialog", {
    name: "Apply gates to pooled group",
    exact: true,
  });
  await expect(
    review.getByRole("button", { name: "Apply to 2 samples", exact: true }),
  ).toBeEnabled();
  return { dialog, review };
}

test("pooled controls, source populations and atomic gate review", async ({
  request,
  page,
}) => {
  const { doc, headers } = await setup(request, page);
  await expect(
    page.getByLabel("Pool sample group", { exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await page
    .getByRole("button", { name: /^Positive/ })
    .first()
    .click();
  await expect(page.locator(".metric-row strong").first()).toHaveText("4");
  const sources = page.getByLabel("Pooled source samples", { exact: true });
  await sources.locator("summary").click();
  await expect(sources).toContainText("Panel A.csv: 2 of 4 events");
  await expect(sources).toContainText("Panel B.csv: 2 of 4 events");
  await page.getByLabel("View all events", { exact: true }).click();
  const pending = await draft(page, "Shared reviewed range");
  const before = await (
    await request.get(`/api/workspaces/${doc.id}`, { headers })
  ).json();
  expect(before.revision).toBe(doc.revision);
  let applied = 0;
  page.on("request", (r) => {
    if (r.url().endsWith("/virtual-groups/gates/apply")) applied++;
  });
  await pending.review
    .getByRole("button", { name: "Apply to 2 samples", exact: true })
    .click();
  await expect(pending.dialog).toHaveCount(0);
  const after = await (
    await request.get(`/api/workspaces/${doc.id}`, { headers })
  ).json();
  expect(after.revision).toBe(doc.revision + 1);
  expect(
    after.gates.filter(
      (g: { name: string }) => g.name === "Shared reviewed range",
    ),
  ).toHaveLength(2);
  expect(after.samples).toEqual(doc.samples);
  expect(applied).toBe(1);
});

test("cancel returns to the draft and a concurrent revision blocks application", async ({
  request,
  page,
}) => {
  const { doc, headers } = await setup(request, page);
  const pending = await draft(page, "Pending shared range");
  await request.patch(`/api/workspaces/${doc.id}`, {
    headers,
    data: { revision: doc.revision, name: "Revision during group review" },
  });
  await expect(pending.review.getByRole("alert")).toContainText(
    "Workspace changed",
  );
  await expect(
    pending.review.getByRole("button", {
      name: "Apply to 2 samples",
      exact: true,
    }),
  ).toBeDisabled();
  await pending.review
    .getByRole("button", { name: "Return to draft", exact: true })
    .click();
  await expect(pending.dialog).toBeVisible();
  await pending.dialog
    .getByRole("button", { name: "Cancel", exact: true })
    .click();
  const after = await (
    await request.get(`/api/workspaces/${doc.id}`, { headers })
  ).json();
  expect(after.gates).toEqual(doc.gates);
  expect(after.samples).toEqual(doc.samples);
});

test("Escape closes only the pooled review and preserves the editable draft", async ({
  request,
  page,
}) => {
  const { doc, headers } = await setup(request, page);
  const pending = await draft(page, "Escape preserves draft");
  await page.keyboard.press("Escape");
  await expect(pending.review).toHaveCount(0);
  await expect(pending.dialog).toBeVisible();
  await expect(
    pending.dialog.getByLabel("Population name", { exact: true }),
  ).toHaveValue("Escape preserves draft");
  await pending.dialog
    .getByRole("button", { name: "Cancel", exact: true })
    .click();
  const after = await (
    await request.get(`/api/workspaces/${doc.id}`, { headers })
  ).json();
  expect(after.gates).toEqual(doc.gates);
});
