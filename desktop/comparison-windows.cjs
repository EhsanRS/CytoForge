// Saved comparison figures have independent native windows and presentation state.
const { BrowserWindow, ipcMain, screen } = require("electron");
const {
  existsSync,
  readFileSync,
  writeFileSync,
  renameSync,
  unlinkSync,
  statSync,
} = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

const identifier = (value) =>
  typeof value === "string" && /^[a-f0-9]{32}$/.test(value);
const color = (value) =>
  typeof value === "string" && /^#[a-f0-9]{6}$/i.test(value);
const keys = new Set([
  "workspaceId",
  "resultId",
  "parameterId",
  "targetIndex",
  "mode",
  "smoothing",
  "controlColor",
  "targetColor",
  "differenceScale",
  "showIndividuals",
]);
function validState(value) {
  return (
    !!value &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    Object.keys(value).length === keys.size &&
    Object.keys(value).every((k) => keys.has(k)) &&
    identifier(value.workspaceId) &&
    identifier(value.resultId) &&
    (value.parameterId === null || identifier(value.parameterId)) &&
    Number.isInteger(value.targetIndex) &&
    value.targetIndex >= 0 &&
    value.targetIndex <= 127 &&
    ["histogram", "cdf", "difference"].includes(value.mode) &&
    Number.isFinite(value.smoothing) &&
    value.smoothing >= 0 &&
    value.smoothing <= 16 &&
    Number.isFinite(value.differenceScale) &&
    value.differenceScale >= 0.1 &&
    value.differenceScale <= 10 &&
    color(value.controlColor) &&
    color(value.targetColor) &&
    typeof value.showIndividuals === "boolean"
  );
}
function trusted(event, contents, origin) {
  try {
    return (
      contents &&
      !contents.isDestroyed() &&
      event.sender === contents &&
      event.senderFrame === contents.mainFrame &&
      new URL(event.senderFrame.url).origin === origin
    );
  } catch {
    return false;
  }
}
function visibleBounds(value) {
  const areas = screen.getAllDisplays().map((d) => d.workArea);
  const valid =
    value &&
    [value.x, value.y, value.width, value.height].every(Number.isFinite);
  const area = valid
    ? (areas.find(
        (a) =>
          value.x + value.width > a.x &&
          value.x < a.x + a.width &&
          value.y + value.height > a.y &&
          value.y < a.y + a.height,
      ) ?? areas[0])
    : areas[0];
  const width = Math.min(area.width, Math.max(700, valid ? value.width : 1120));
  const height = Math.min(
    area.height,
    Math.max(500, valid ? value.height : 800),
  );
  return {
    width,
    height,
    x: Math.round(
      Math.max(
        area.x,
        Math.min(area.x + area.width - width, valid ? value.x : area.x + 50),
      ),
    ),
    y: Math.round(
      Math.max(
        area.y,
        Math.min(area.y + area.height - height, valid ? value.y : area.y + 50),
      ),
    ),
  };
}

function installComparisonWindows({
  origin,
  token,
  settingsDirectory,
  authorize,
  icon,
  webPreferences,
}) {
  const records = new Map(),
    filename = path.join(settingsDirectory, "comparison-windows.json");
  let closing = false,
    timer;
  const recordFor = (event) =>
    [...records.values()].find((r) =>
      trusted(event, r.window.webContents, origin),
    );
  const workspaceFor = (event) =>
    recordFor(event)?.state.workspaceId ?? authorize(event);
  const persist = () => {
    clearTimeout(timer);
    const data = JSON.stringify({
      version: 1,
      windows: [...records.values()].map((r) => ({
        id: r.id,
        state: r.state,
        bounds: r.window.getBounds(),
      })),
    });
    if (Buffer.byteLength(data) > 256 * 1024)
      throw new Error("Comparison window session is too large");
    const temporary = filename + ".partial";
    try {
      writeFileSync(temporary, data, { mode: 0o600 });
      renameSync(temporary, filename);
    } finally {
      if (existsSync(temporary)) unlinkSync(temporary);
    }
  };
  const schedule = () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      try {
        persist();
      } catch {
        console.error("Comparison window session could not be saved");
      }
    }, 200);
  };
  async function request(url) {
    const response = await fetch(origin + url, {
      headers: { "X-CytoForge-Token": token },
      signal: AbortSignal.timeout(10000),
    });
    if (!response.ok) throw new Error("The saved comparison is unavailable");
    return response.json();
  }
  async function savedResult(state) {
    const saved = await request(
      `/api/workspaces/${state.workspaceId}/population-comparison`,
    );
    if (!saved.some((r) => r.id === state.resultId))
      throw new Error("Save this comparison before opening its native window");
    return request(
      `/api/workspaces/${state.workspaceId}/population-comparison/${state.resultId}`,
    );
  }
  async function create(
    state,
    bounds,
    identifierValue = crypto.randomBytes(16).toString("hex"),
  ) {
    if (
      closing ||
      records.size >= 24 ||
      !validState(state) ||
      !identifier(identifierValue) ||
      records.has(identifierValue)
    )
      throw new Error("Invalid comparison window request");
    const result = await savedResult(state);
    if (
      state.targetIndex >= result.request.inputs.length ||
      (state.parameterId &&
        !result.request.parameters.some((p) => p.id === state.parameterId))
    )
      throw new Error(
        "The selected comparison parameter or target is unavailable",
      );
    if (closing || records.size >= 24)
      throw new Error("Comparison windows are closing or at their limit");
    const window = new BrowserWindow({
      ...visibleBounds(bounds),
      show: process.env.CYTOFORGE_HEADLESS_TEST !== "1",
      minWidth: 700,
      minHeight: 500,
      backgroundColor: "#0c1016",
      title: `CytoForge · ${result.request.name}`,
      icon,
      autoHideMenuBar: true,
      webPreferences,
    });
    const record = {
      id: identifierValue,
      state: structuredClone(state),
      window,
      parameters: new Set(result.request.parameters.map((p) => p.id)),
      targets: result.request.inputs.length,
    };
    records.set(record.id, record);
    window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
    window.webContents.on("will-navigate", (event, url) => {
      try {
        if (new URL(url).origin !== origin) event.preventDefault();
      } catch {
        event.preventDefault();
      }
    });
    window.on("resize", schedule);
    window.on("move", schedule);
    window.on("closed", () => {
      records.delete(record.id);
      if (!closing) schedule();
    });
    try {
      await window.loadURL(`${origin}/?comparisonWindow=${record.id}`);
      schedule();
      return record.id;
    } catch (error) {
      records.delete(record.id);
      window.destroy();
      throw error;
    }
  }
  ipcMain.handle("cytoforge:comparison:context", (event) => {
    const record = recordFor(event);
    return record ? { id: record.id, ...structuredClone(record.state) } : null;
  });
  ipcMain.handle("cytoforge:comparison:open", (event, state) => {
    if (!validState(state) || workspaceFor(event) !== state.workspaceId)
      throw new Error("Invalid comparison window owner");
    return create(state);
  });
  ipcMain.handle("cytoforge:comparison:update", (event, state) => {
    const record = recordFor(event);
    if (
      !record ||
      !validState(state) ||
      state.workspaceId !== record.state.workspaceId ||
      state.resultId !== record.state.resultId ||
      closing ||
      state.targetIndex >= record.targets ||
      (state.parameterId && !record.parameters.has(state.parameterId))
    )
      return false;
    record.state = structuredClone(state);
    schedule();
    return true;
  });
  ipcMain.on("cytoforge:workspace:notify", (event, workspaceId) => {
    if (!identifier(workspaceId) || workspaceFor(event) !== workspaceId) return;
    void request(`/api/workspaces/${workspaceId}`)
      .then((doc) => {
        for (const record of records.values())
          if (
            record.state.workspaceId === workspaceId &&
            !record.window.isDestroyed()
          )
            record.window.webContents.send("cytoforge:workspace:changed", {
              workspaceId,
              revision: doc.revision,
            });
      })
      .catch(() => {});
  });
  return {
    workspaceFor,
    async restore() {
      try {
        if (!existsSync(filename) || statSync(filename).size > 256 * 1024)
          return;
        const data = JSON.parse(readFileSync(filename, "utf8"));
        if (
          data.version !== 1 ||
          !Array.isArray(data.windows) ||
          data.windows.length > 24
        )
          return;
        for (const record of data.windows)
          if (identifier(record.id) && validState(record.state)) {
            try {
              await create(record.state, record.bounds, record.id);
            } catch {
              console.error("A saved comparison window could not be restored");
            }
          }
      } catch {
        console.error("Comparison window session could not be restored");
      }
    },
    closeForQuit() {
      if (closing) return;
      try {
        persist();
      } catch {
        console.error("Comparison window session could not be saved");
      }
      closing = true;
      clearTimeout(timer);
      for (const record of records.values())
        if (!record.window.isDestroyed()) record.window.destroy();
      records.clear();
    },
  };
}
module.exports = { installComparisonWindows, validState };
