import { copyFileSync, readFileSync } from "node:fs";
import path from "node:path";
import { plateFixtures } from "./plate_fixture.mjs";

export async function desktopPlateSmoke(desktop, window, root, profile) {
  await window.setViewportSize({ width: 1540, height: 1050 });
  const workspace = await window.evaluate(async (fixtures) => {
    const { token } = await (await fetch("/api/bootstrap")).json();
    const headers = { "X-CytoForge-Token": token };
    let response = await fetch("/api/workspaces", {
      method: "POST",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify({ name: "Desktop plate independent truth" }),
    });
    if (!response.ok) throw new Error(await response.text());
    let doc = await response.json();
    for (const fixture of fixtures) {
      const form = new FormData();
      form.append(
        "files",
        new File([fixture.csv], `${fixture.name}.csv`, { type: "text/csv" }),
      );
      response = await fetch(
        `/api/workspaces/${doc.id}/import?revision=${doc.revision}`,
        { method: "POST", headers, body: form },
      );
      if (!response.ok) throw new Error(await response.text());
      doc = (await response.json()).workspace;
      response = await fetch(
        `/api/workspaces/${doc.id}/samples/${doc.samples.at(-1).id}`,
        {
          method: "PATCH",
          headers: { ...headers, "Content-Type": "application/json" },
          body: JSON.stringify({
            revision: doc.revision,
            name: fixture.name,
            tags: fixture.tags,
          }),
        },
      );
      if (!response.ok) throw new Error(await response.text());
      doc = await response.json();
    }
    globalThis.cytoforgeDesktop.saveWorkspace(doc.id);
    localStorage.setItem("cytoforge.workspace", doc.id);
    return doc.id;
  }, plateFixtures());
  await window.reload();
  await window.getByRole("button", { name: "Plates", exact: true }).click();
  await window.getByRole("tab", { name: "Import & titration" }).click();
  await window.getByLabel("Include plate mapping replicates").check();
  await window
    .getByRole("button", { name: "Preview keyword mapping", exact: true })
    .click();
  await window.getByLabel("I reviewed unresolved plate items").check();
  const savedPlates = window.waitForResponse(
    (response) =>
      response.url().endsWith("/plates/save-batch") &&
      response.request().method() === "POST",
  );
  await window
    .getByRole("button", { name: "Save discovered plates", exact: true })
    .click();
  const savedResponse = await savedPlates;
  if (!savedResponse.ok()) throw new Error(await savedResponse.text());
  const savedDocument = await savedResponse.json();
  await window.getByRole("dialog").waitFor({ state: "hidden" });
  await window
    .getByRole("button", {
      name: `Revision ${savedDocument.revision} · History`,
      exact: true,
    })
    .waitFor();
  await window.locator('.plate-grid-status[data-ready="true"]').waitFor();
  await window.getByLabel("Plate annotation CSV file").setInputFiles({
    name: "desktop-plate.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(
      "Well ID,Treatment,Dose\nA1,treated,1\nA02,vehicle,0.5\nB03,planned,0\nC1,duplicate,1\nC01,duplicate,2\nA0,invalid,0\n",
    ),
  });
  await window.getByLabel("I reviewed unresolved plate items").check();
  await window
    .getByRole("button", { name: "Stage reviewed plan", exact: true })
    .click();
  await window.getByRole("tab", { name: "Well & annotations" }).click();
  await window
    .getByRole("button", { name: "Review annotation changes", exact: true })
    .click();
  const review = window.getByRole("dialog");
  if (
    !(await review.innerText()).includes(
      "6 keyword changes across 3 acquisitions",
    )
  )
    throw new Error("Desktop plate review lost replicate changes");
  await window
    .getByRole("button", { name: "Apply reviewed annotations", exact: true })
    .click();
  await window.locator('.plate-grid-status[data-ready="true"]').waitFor();
  await window.getByRole("tab", { name: "Measurements", exact: true }).click();
  await window
    .getByRole("button", { name: "Add measurement", exact: true })
    .click();
  await window
    .getByLabel("Plate measurement name", { exact: true })
    .fill("Signal");
  await window
    .getByLabel("Plate measurement statistic", { exact: true })
    .selectOption("median");
  await window
    .getByLabel("Plate measurement parameter", { exact: true })
    .selectOption("X");
  await window
    .getByLabel("Plate primary measure", { exact: true })
    .selectOption({ label: "Signal" });
  await window.locator('.plate-grid-status[data-ready="true"]').waitFor();
  if (
    (await window
      .locator('[data-well="A01"] .plate-well-value')
      .innerText()) !== "76.25"
  )
    throw new Error(
      "Desktop plate pooled events instead of acquisition medians",
    );
  if (
    (await window
      .locator('[data-well="B01"] .plate-well-value')
      .innerText()) !== "0"
  )
    throw new Error("Desktop plate lost a measured zero");
  await window
    .locator(".main-content")
    .evaluate((element) => (element.scrollTop = 0));
  await window.screenshot({
    path: path.join(root, "artifacts/screenshots/desktop-plate-heatmap.png"),
  });
  await window.getByLabel("Plate view", { exact: true }).selectOption("faces");
  await window.locator('.plate-grid-status[data-ready="true"]').waitFor();
  await window.getByRole("button", { name: "Save plate", exact: true }).click();
  await window.locator('.plate-grid-status[data-ready="true"]').waitFor();
  await window.screenshot({
    path: path.join(root, "artifacts/screenshots/desktop-plate-faces.png"),
  });
  await desktop.evaluate(
    ({ session }, target) => {
      globalThis.cytoforgeSmokeDownload = new Promise((resolve, reject) => {
        session.defaultSession.once("will-download", (_event, item) => {
          item.setSavePath(target);
          item.once("done", (_event, state) =>
            state === "completed"
              ? resolve(item.getSavePath())
              : reject(new Error(`Desktop download ${state}`)),
          );
        });
      });
    },
    path.join(profile, "exports/plate.svg"),
  );
  await window
    .getByRole("button", { name: "Export plate", exact: true })
    .click();
  const filename = await desktop.evaluate(
    () => globalThis.cytoforgeSmokeDownload,
  );
  const svg = readFileSync(filename, "utf8");
  if (!svg.includes("Head height: Signal"))
    throw new Error("Desktop plate export omitted its face legend");
  copyFileSync(filename, path.join(root, "artifacts/desktop-plate.svg"));
  const document = await window.evaluate(async (id) => {
    const { token } = await (await fetch("/api/bootstrap")).json();
    return (
      await fetch(`/api/workspaces/${id}`, {
        headers: { "X-CytoForge-Token": token },
      })
    ).json();
  }, workspace);
  if (
    document.samples[0].tags.Treatment !== "treated" ||
    document.samples[1].tags.Treatment !== "treated" ||
    document.plates[0].annotations.B03.Dose !== "0"
  )
    throw new Error(
      "Desktop plate annotations or empty-well plan were not persisted",
    );
  await window
    .getByLabel("Plate name", { exact: true })
    .fill("Recovered desktop plate draft");
  // Close immediately in the caller to exercise the native draft-flush handshake.
  return {
    workspace,
    plate_id: document.plates[0].id,
    equal_acquisition_median: 76.25,
    measured_zero_preserved: true,
    invalid_duplicate_rows_reviewed: true,
    annotations_applied: true,
    empty_well_plan_persisted: true,
    native_svg_downloaded: true,
    headless_download_dialog_redirected: true,
  };
}
