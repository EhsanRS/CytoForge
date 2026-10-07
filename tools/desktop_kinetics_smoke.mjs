import { copyFileSync, readFileSync } from "node:fs";
import path from "node:path";

export async function desktopKineticsSmoke(desktop, window, root, profile) {
  const workspace = await window.evaluate(async () => {
    const { token } = await (await fetch("/api/bootstrap")).json();
    const headers = { "X-CytoForge-Token": token };
    let response = await fetch("/api/workspaces", {
      method: "POST",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify({ name: "Desktop kinetics independent truth" }),
    });
    if (!response.ok) throw new Error(await response.text());
    const doc = await response.json(),
      rows = ["Time,Signal"];
    for (let i = 0; i < 8; i++)
      for (let j = 0; j < 3; j++) rows.push(`${i + 0.5},${4 * (i + 0.5) + 8}`);
    const form = new FormData();
    form.append(
      "files",
      new File([rows.join("\n")], "desktop-time-response.csv", {
        type: "text/csv",
      }),
    );
    response = await fetch(`/api/workspaces/${doc.id}/import?revision=0`, {
      method: "POST",
      headers,
      body: form,
    });
    if (!response.ok) throw new Error(await response.text());
    globalThis.cytoforgeDesktop.saveWorkspace(doc.id);
    localStorage.setItem("cytoforge.workspace", doc.id);
    return doc.id;
  });
  await window.reload();
  await window.getByRole("button", { name: "Kinetics", exact: true }).click();
  await window
    .getByLabel("Kinetics analysis name", { exact: true })
    .fill("Desktop time response");
  await window.getByLabel("Kinetics time minimum", { exact: true }).fill("0");
  await window.getByLabel("Kinetics time maximum", { exact: true }).fill("8");
  await window.getByLabel("Kinetics time bins", { exact: true }).fill("8");
  await window
    .getByLabel("Kinetics statistic", { exact: true })
    .selectOption("mean");
  await window
    .getByLabel("Kinetics absolute threshold", { exact: true })
    .fill("20");
  await window
    .getByLabel("Create kinetics responder populations", { exact: true })
    .check();
  await window
    .getByRole("button", { name: "Calculate kinetics", exact: true })
    .click();
  await window
    .getByRole("img", {
      name: "Kinetics time course with measured gaps",
      exact: true,
    })
    .waitFor();
  await window
    .getByLabel("Kinetics review confirmation", { exact: true })
    .check();
  await window
    .getByRole("button", { name: "Save kinetics analysis", exact: true })
    .click();
  await window.getByText("Saved analysis", { exact: true }).waitFor();
  for (const [button, file] of [
    ["Analysis JSON", "desktop-kinetics.json"],
    ["Figure SVG", "desktop-kinetics.svg"],
    ["Time series CSV", "desktop-kinetics-series.csv"],
  ]) {
    await desktop.evaluate(
      ({ session }, target) => {
        globalThis.cytoforgeSmokeDownload = new Promise((resolve, reject) =>
          session.defaultSession.once("will-download", (_event, item) => {
            item.setSavePath(target);
            item.once("done", (_event, state) =>
              state === "completed"
                ? resolve(item.getSavePath())
                : reject(new Error(`Desktop download ${state}`)),
            );
          }),
        );
      },
      path.join(profile, "exports", file),
    );
    await window.getByRole("button", { name: button, exact: true }).click();
    copyFileSync(
      await desktop.evaluate(() => globalThis.cytoforgeSmokeDownload),
      path.join(root, "artifacts", file),
    );
  }
  const result = JSON.parse(
    readFileSync(path.join(root, "artifacts/desktop-kinetics.json"), "utf8"),
  );
  const summary = result.fits[0].ranges[0];
  if (
    summary.peak !== 38 ||
    summary.peak_time !== 7.5 ||
    Math.abs(summary.slope - 4) > 1e-12 ||
    Math.abs(summary.auc - 168) > 1e-12 ||
    summary.responder_count !== 15
  )
    throw new Error(
      "Desktop kinetics differs from its independent linear reference",
    );
  const counts = await window.evaluate(async (id) => {
    const { token } = await (await fetch("/api/bootstrap")).json(),
      headers = { "X-CytoForge-Token": token };
    const doc = await (
      await fetch(`/api/workspaces/${id}`, { headers })
    ).json();
    const response = await fetch(
      `/api/workspaces/${id}/samples/${doc.samples[0].id}/counts`,
      { headers },
    );
    if (!response.ok) throw new Error(await response.text());
    return (await response.json()).map((g) => g.count).sort((a, b) => a - b);
  }, workspace);
  if (JSON.stringify(counts) !== JSON.stringify([15, 24, 24]))
    throw new Error("Desktop kinetics gates lost original event identities");
  await window
    .getByRole("button", { name: "Expand review", exact: true })
    .click();
  await window.locator(".main-content").evaluate((e) => {
    e.scrollTop = 0;
  });
  await window.screenshot({
    path: path.join(root, "artifacts/screenshots/desktop-kinetics.png"),
  });
  return {
    workspace,
    result_id: result.id,
    known_slope: 4,
    known_auc: 168,
    responders: 15,
    original_events: 24,
    native_exports: ["JSON", "SVG", "time-series CSV"],
    headless_download_dialog_redirected: true,
  };
}
