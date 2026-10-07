// Native plot windows share one private engine and keep independent view state.
const { BrowserWindow, dialog, ipcMain, screen } = require("electron");
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
const { viewMemory, MAX_SESSION_BYTES } = require("./plot-view-memory.cjs");

const identifier = (value) =>
  typeof value === "string" && /^[a-f0-9]{32}$/.test(value);
const reference = (value) => value === null || identifier(value);
const channel = (value) =>
  typeof value === "string" &&
  value.length > 0 &&
  value.length <= 256 &&
  !/[\x00-\x1f]/.test(value);
const stateKeys = new Set([
  "workspaceId",
  "sampleId",
  "gateId",
  "x",
  "y",
  "mode",
  "backgateId",
  "coordinateGateId",
  "bounds",
  "graphOptions",
  "bins",
  "threeD",
  "groupId",
  "pooled",
  "sampleFilter",
  "xTransform",
  "yTransform",
  "xDimension",
  "yDimension",
]);
const pair = (value) =>
  Array.isArray(value) && value.length === 2 && value.every(Number.isFinite);
function validThreeD(value) {
  if (value === undefined) return true;
  if (
    !value ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    !channel(value.z)
  )
    return false;
  const between = (v, a, b) => Number.isFinite(v) && v >= a && v <= b;
  const limits = (v) => v === null || (pair(v) && v[0] < v[1]);
  const checks = {
    z: channel,
    z_transform: (v) => v === null || validTransform(v),
    color_transform: (v) => v === null || validTransform(v),
    size_transform: (v) => v === null || validTransform(v),
    z_dimension: (v) =>
      v === null || (validDimension(v) && v.channel === value.z),
    color_dimension: (v) =>
      v === null || (validDimension(v) && v.channel === value.color_by),
    size_dimension: (v) =>
      v === null || (validDimension(v) && v.channel === value.size_by),
    color_by: (v) => v === null || channel(v),
    size_by: (v) => v === null || channel(v),
    color_bounds: limits,
    size_bounds: limits,
    compensation: (v) => ["coordinate", "uncompensated"].includes(v),
    all_events: (v) => typeof v === "boolean",
    show_cube: (v) => typeof v === "boolean",
    show_labels: (v) => typeof v === "boolean",
    yaw: (v) => between(v, -Math.PI, Math.PI),
    pitch: (v) => between(v, -1.5, 1.5),
    zoom: (v) => between(v, 0.1, 10),
    pan: (v) => pair(v) && v.every((n) => Math.abs(n) <= 5),
    point_size: (v) => between(v, 0.5, 12),
    opacity: (v) => between(v, 0.05, 1),
  };
  return Object.keys(value).every((key) => checks[key]?.(value[key]) === true);
}
function validTransform(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const kinds = [
    "linear",
    "log",
    "logicle",
    "hyperlog",
    "asinh",
    "gml_linear",
    "gml_log",
    "gml_asinh",
    "wsp_log",
    "wsp_biex",
  ];
  const fields = [
    "cofactor",
    "t",
    "w",
    "m",
    "a",
    "offset",
    "positive",
    "negative",
    "width",
    "top",
    "bound_min",
    "bound_max",
  ];
  return Object.keys(value).every((key) =>
    key === "kind"
      ? kinds.includes(value[key])
      : fields.includes(key) &&
        ((["bound_min", "bound_max"].includes(key) && value[key] === null) ||
          Number.isFinite(value[key])),
  );
}
function validDimension(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const optionalNumber = (v) => v === null || Number.isFinite(v);
  const checks = {
    channel,
    transform: validTransform,
    compensation_ref: (v) =>
      ["sample", "uncompensated", "FCS"].includes(v) || identifier(v),
    minimum: (v) => v === null,
    maximum: (v) => v === null,
    ratio_channels: (v) =>
      v === null || (Array.isArray(v) && v.length === 2 && v.every(channel)),
    ratio_a: Number.isFinite,
    ratio_b: Number.isFinite,
    ratio_c: Number.isFinite,
    ratio_bound_min: optionalNumber,
    ratio_bound_max: optionalNumber,
  };
  return (
    channel(value.channel) &&
    validTransform(value.transform) &&
    checks.compensation_ref(value.compensation_ref) &&
    Object.keys(value).every((key) => checks[key]?.(value[key]) === true) &&
    (value.ratio_bound_min == null ||
      value.ratio_bound_max == null ||
      value.ratio_bound_min <= value.ratio_bound_max)
  );
}
function validTypography(value) {
  if (value == null) return true;
  if (typeof value !== "object" || Array.isArray(value)) return false;
  const roles = new Set([
    "axis_labels",
    "tick_labels",
    "gate_labels",
    "statistics",
    "legend",
    "title",
  ]);
  const checks = {
    font_size_pt: (v) =>
      v === null || (Number.isFinite(v) && v >= 4 && v <= 144),
    font_family: (v) => v === null || ["sans", "serif", "mono"].includes(v),
    font_weight: (v) => v === null || ["normal", "bold"].includes(v),
    font_style: (v) => v === null || ["normal", "italic"].includes(v),
    color: (v) =>
      v === null || (typeof v === "string" && /^#[0-9a-fA-F]{6}$/.test(v)),
  };
  return Object.keys(value).every(
    (role) =>
      roles.has(role) &&
      (value[role] === null ||
        (value[role] &&
          typeof value[role] === "object" &&
          !Array.isArray(value[role]) &&
          Object.keys(value[role]).every(
            (key) =>
              Object.hasOwn(checks, key) &&
              checks[key](value[role][key]) === true,
          ))),
  );
}
function validGateStyle(value) {
  if (value == null) return true;
  if (typeof value !== "object" || Array.isArray(value)) return false;
  const checks = {
    fill_opacity: (v) => v === null || (Number.isFinite(v) && v >= 0 && v <= 1),
    fill_color: (v) =>
      v === null || (typeof v === "string" && /^#[0-9a-fA-F]{6}$/.test(v)),
    line_width_px: (v) =>
      v === null || (Number.isFinite(v) && v >= 0.25 && v <= 12),
    show_labels: (v) => v === null || typeof v === "boolean",
  };
  return Object.keys(value).every(
    (key) => Object.hasOwn(checks, key) && checks[key](value[key]) === true,
  );
}
function validGraphOptions(value) {
  if (value === undefined) return true;
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const checks = {
    smooth: (v) => v === null || typeof v === "boolean",
    sigma: (v) => Number.isFinite(v) && v >= 0 && v <= 4,
    contour_spacing: (v) => v === null || ["2", "5", "10", "log"].includes(v),
    show_outliers: (v) => typeof v === "boolean",
    palette: (v) => ["ocean", "gray", "spectrum", "viridis"].includes(v),
    axis_extent: (v) => ["robust", "full"].includes(v),
    point_limit: (v) => Number.isInteger(v) && v >= 100 && v <= 100000,
    typography: validTypography,
    gate_style: validGateStyle,
  };
  return Object.keys(value).every(
    (key) => Object.hasOwn(checks, key) && checks[key](value[key]) === true,
  );
}
function validState(value) {
  const oneDimensional = ["histogram", "cdf"].includes(value?.mode);
  return (
    value &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    Object.keys(value).every((key) => stateKeys.has(key)) &&
    identifier(value.workspaceId) &&
    identifier(value.sampleId) &&
    reference(value.gateId) &&
    channel(value.x) &&
    (value.y === null || channel(value.y)) &&
    [
      "density",
      "scatter",
      "histogram",
      "cdf",
      "contour",
      "zebra",
      "pseudocolor",
      "3d",
    ].includes(value.mode) &&
    (oneDimensional || value.y !== null) &&
    validGraphOptions(value.graphOptions) &&
    (value.groupId === undefined || reference(value.groupId)) &&
    (value.pooled === undefined || typeof value.pooled === "boolean") &&
    (value.sampleFilter === undefined ||
      (typeof value.sampleFilter === "string" &&
        value.sampleFilter.length <= 256)) &&
    [value.xTransform, value.yTransform].every(
      (v) => v === undefined || v === null || validTransform(v),
    ) &&
    [value.xDimension, value.yDimension].every(
      (v, i) =>
        v === undefined ||
        v === null ||
        (validDimension(v) && v.channel === (i === 0 ? value.x : value.y)),
    ) &&
    validThreeD(value.threeD) &&
    (value.mode !== "3d" || value.threeD !== undefined) &&
    (value.bins === undefined ||
      (Number.isInteger(value.bins) &&
        value.bins >= 16 &&
        value.bins <= 384)) &&
    (value.backgateId === undefined || reference(value.backgateId)) &&
    (value.coordinateGateId === undefined ||
      reference(value.coordinateGateId)) &&
    (value.bounds === undefined ||
      value.bounds === null ||
      (Array.isArray(value.bounds) &&
        value.bounds.length ===
          (value.mode === "3d" ? 6 : oneDimensional ? 2 : 4) &&
        value.bounds.every(Number.isFinite) &&
        value.bounds[0] < value.bounds[1] &&
        (oneDimensional || value.bounds[2] < value.bounds[3]) &&
        (value.mode !== "3d" || value.bounds[4] < value.bounds[5])))
  );
}
function trustedFrame(event, contents, origin) {
  try {
    return (
      event.sender === contents &&
      !contents.isDestroyed() &&
      event.senderFrame === contents.mainFrame &&
      new URL(event.senderFrame.url).origin === origin
    );
  } catch {
    return false;
  }
}
function visibleBounds(saved, index) {
  const displays = screen.getAllDisplays().map((display) => display.workArea);
  const fallback = screen.getPrimaryDisplay().workArea;
  const requested =
    saved &&
    ["x", "y", "width", "height"].every(
      (key) => Number.isInteger(saved[key]) && Math.abs(saved[key]) <= 100000,
    )
      ? saved
      : {
          x: fallback.x + 28 + index * 22,
          y: fallback.y + 28 + index * 22,
          width: 1100,
          height: 820,
        };
  const area =
    displays.find(
      (area) =>
        requested.x < area.x + area.width - 80 &&
        requested.x + requested.width > area.x + 80 &&
        requested.y < area.y + area.height - 80 &&
        requested.y + requested.height > area.y + 80,
    ) || fallback;
  const width = Math.max(860, Math.min(requested.width, 4096, area.width));
  const height = Math.max(650, Math.min(requested.height, 4096, area.height));
  return {
    width,
    height,
    x: Math.max(area.x, Math.min(requested.x, area.x + area.width - width)),
    y: Math.max(area.y, Math.min(requested.y, area.y + area.height - height)),
  };
}

function installPlotWindows({
  origin,
  token,
  settingsDirectory,
  mainWindow,
  mainWorkspace,
  webPreferences,
  icon,
}) {
  const filename = path.join(settingsDirectory, "plot-windows.json");
  const records = new Map();
  const knownRevisions = new Map();
  const refreshing = new Map();
  let savedSession = null;
  try {
    if (existsSync(filename)) {
      if (statSync(filename).size > MAX_SESSION_BYTES)
        throw new Error("Plot session exceeds 12 MiB");
      savedSession = JSON.parse(readFileSync(filename, "utf8"));
      if (
        ![1, 2].includes(savedSession.version) ||
        !Array.isArray(savedSession.windows) ||
        savedSession.windows.length > 32 ||
        savedSession.windows.some(
          (r) => !identifier(r.id) || !validState(r.state),
        ) ||
        new Set(savedSession.windows.map((r) => r.id)).size !==
          savedSession.windows.length
      )
        throw new Error("Invalid saved plot windows");
      for (const record of savedSession.windows) {
        if (
          (record.views || []).some(
            (v) => v.workspaceId !== record.state.workspaceId,
          )
        )
          throw new Error("Foreign remembered workspace");
        viewMemory(validState, record.views || []);
      }
      if (savedSession.main) {
        if (
          savedSession.main.state !== null &&
          !validState(savedSession.main.state)
        )
          throw new Error("Invalid main plot view");
        viewMemory(validState, savedSession.main.views || [], 192);
      }
    }
  } catch (error) {
    console.error("Plot session could not be restored:", error.message);
    savedSession = null;
  }
  const mainView = {
    id: "main",
    state: savedSession?.main?.state || null,
    generation: 0,
    active: false,
    memory: viewMemory(validState, savedSession?.main?.views || [], 192),
  };
  let navigating = false;
  let shuttingDown = false,
    mainDirty = false,
    saveTimer,
    pollTimer;
  const recordFor = (event) =>
    [...records.values()].find(
      (record) =>
        !record.window.isDestroyed() &&
        trustedFrame(event, record.window.webContents, origin),
    );
  const trustedMain = (event) =>
    trustedFrame(event, mainWindow()?.webContents, origin);
  const workspaceFor = (event) =>
    recordFor(event)?.state.workspaceId ||
    (trustedMain(event) ? mainWorkspace() : null);
  function persist() {
    clearTimeout(saveTimer);
    const data = JSON.stringify({
      version: 2,
      main: { state: mainView.state, views: mainView.memory.serialize() },
      windows: [...records.values()].map((record) => ({
        id: record.id,
        state: record.state,
        bounds: record.window.getBounds(),
        views: record.memory.serialize(),
      })),
    });
    if (Buffer.byteLength(data) > MAX_SESSION_BYTES)
      throw new Error("Plot session settings exceed 12 MiB");
    const temporary =
      filename + "." + crypto.randomBytes(6).toString("hex") + ".tmp";
    try {
      writeFileSync(temporary, data, { mode: 0o600, flag: "wx", flush: true });
      renameSync(temporary, filename);
    } finally {
      if (existsSync(temporary)) unlinkSync(temporary);
    }
  }
  function scheduleSave() {
    clearTimeout(saveTimer);
    if (!shuttingDown)
      saveTimer = setTimeout(() => {
        try {
          persist();
        } catch (error) {
          console.error(
            "Plot window settings could not be saved:",
            error.message,
          );
        }
      }, 200);
  }
  async function request(route, body) {
    const response = await fetch(origin + "/api" + route, {
      method: body ? "POST" : "GET",
      headers: {
        "X-CytoForge-Token": token,
        "Content-Type": "application/json",
      },
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(5000),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(
        typeof error.detail === "string"
          ? error.detail
          : "The plot workspace is unavailable",
      );
    }
    return response.json();
  }
  async function refresh(workspaceId) {
    if (!identifier(workspaceId) || shuttingDown) return;
    if (refreshing.has(workspaceId)) return refreshing.get(workspaceId);
    const pending = (async () => {
      const value = await request("/workspaces/" + workspaceId + "/revision");
      if (
        shuttingDown ||
        value.id !== workspaceId ||
        !Number.isSafeInteger(value.revision)
      )
        return;
      if (knownRevisions.get(workspaceId) === value.revision) return;
      knownRevisions.set(workspaceId, value.revision);
      const targets = [...records.values()]
        .filter((record) => record.state.workspaceId === workspaceId)
        .map((record) => record.window);
      if (mainWorkspace() === workspaceId && mainWindow())
        targets.push(mainWindow());
      for (const target of targets)
        if (!target.isDestroyed())
          target.webContents.send("cytoforge:workspace:changed", {
            workspaceId,
            revision: value.revision,
          });
    })().finally(() => refreshing.delete(workspaceId));
    refreshing.set(workspaceId, pending);
    return pending;
  }
  function poll() {
    const workspaces = new Set(
      [...records.values()].map((record) => record.state.workspaceId),
    );
    if (mainWorkspace()) workspaces.add(mainWorkspace());
    for (const id of workspaces) void refresh(id).catch(() => {});
  }
  function updatePolling() {
    if (shuttingDown) return;
    if (records.size && !pollTimer) {
      pollTimer = setInterval(poll, 1200);
      poll();
    }
    if (!records.size && pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }
  async function confirmDiscard(parent, count = 1) {
    const choice = await dialog.showMessageBox(parent, {
      type: "warning",
      title: "Unsaved gate edits",
      message:
        count === 1
          ? "This window has unsaved gate edits."
          : count + " windows have unsaved gate edits.",
      detail: "Keep editing to save the gates, or discard these unsaved edits.",
      buttons: ["Keep editing", "Discard unsaved edits"],
      defaultId: 0,
      cancelId: 0,
    });
    return choice.response === 1;
  }
  async function create(state, savedBounds, savedId, remembered = []) {
    if (shuttingDown) throw new Error("CytoForge is closing");
    if (records.size >= 32)
      throw new Error("Close a plot window before opening more than 32");
    const id = savedId || crypto.randomBytes(16).toString("hex");
    const child = new BrowserWindow({
      ...visibleBounds(savedBounds, records.size),
      minWidth: 860,
      minHeight: 650,
      show: process.env.CYTOFORGE_HEADLESS_TEST !== "1",
      backgroundColor: "#0c1016",
      title: "CytoForge · Plot",
      icon,
      autoHideMenuBar: true,
      webPreferences,
    });
    const record = {
      id,
      window: child,
      state: structuredClone(state),
      dirty: false,
      confirming: false,
      closeAllowed: false,
      generation: 0,
      active: true,
      memory: viewMemory(validState, remembered),
    };
    record.memory.remember(state);
    const contentsId = child.webContents.id;
    records.set(contentsId, record);
    child.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
    child.webContents.on("will-navigate", (event, url) => {
      try {
        if (new URL(url).origin !== origin) event.preventDefault();
      } catch {
        event.preventDefault();
      }
    });
    child.on("resize", scheduleSave);
    child.on("move", scheduleSave);
    child.on(
      "focus",
      () => void refresh(record.state.workspaceId).catch(() => {}),
    );
    child.on("close", (event) => {
      if (shuttingDown || record.closeAllowed || !record.dirty) return;
      event.preventDefault();
      if (record.confirming) return;
      record.confirming = true;
      void confirmDiscard(child)
        .then((discard) => {
          record.confirming = false;
          if (discard && !child.isDestroyed()) {
            record.closeAllowed = true;
            child.close();
          }
        })
        .catch(() => {
          record.confirming = false;
        });
    });
    child.on("closed", () => {
      records.delete(contentsId);
      if (!shuttingDown) scheduleSave();
      updatePolling();
    });
    updatePolling();
    try {
      await child.loadURL(origin + "/?plotWindow=" + id);
    } catch (error) {
      child.destroy();
      throw error;
    }
    scheduleSave();
    if (process.env.CYTOFORGE_HEADLESS_TEST !== "1") child.focus();
    return id;
  }
  ipcMain.handle("cytoforge:plot:context", (event) => {
    const record = recordFor(event);
    return record ? { id: record.id, ...record.state } : null;
  });
  ipcMain.handle("cytoforge:plot:session", (event, workspaceId) => {
    const record = recordFor(event);
    if (workspaceId !== undefined && !identifier(workspaceId)) return null;
    if (record)
      return workspaceId === undefined ||
        workspaceId === record.state.workspaceId
        ? {
            state: structuredClone(record.state),
            dirty: record.dirty,
            active: record.active,
            remembered: record.memory.size,
          }
        : null;
    if (!trustedMain(event)) return null;
    const target = workspaceId ?? mainWorkspace();
    const state =
      mainView.state?.workspaceId === target
        ? mainView.state
        : mainView.memory.latest(target);
    return {
      state: state ? structuredClone(state) : null,
      dirty: mainDirty,
      active: mainView.active,
      remembered: mainView.memory.size,
    };
  });
  ipcMain.handle("cytoforge:plot:open", async (event, state) => {
    if (!(trustedMain(event) || recordFor(event)) || !validState(state))
      throw new Error("Invalid plot window request");
    const owner = recordFor(event);
    if (owner && owner.state.workspaceId !== state.workspaceId)
      throw new Error("Plot windows remain in their workspace");
    if (!owner && state.workspaceId !== mainWorkspace())
      throw new Error("Open plots from the active workspace");
    const doc = await request("/workspaces/" + state.workspaceId);
    const sample = doc.samples.find((sample) => sample.id === state.sampleId);
    if (
      !sample ||
      (state.groupId && !doc.groups.some((g) => g.id === state.groupId)) ||
      [state.gateId, state.backgateId, state.coordinateGateId]
        .filter(Boolean)
        .some(
          (id) =>
            !doc.gates.some(
              (gate) => gate.id === id && gate.sample_id === sample.id,
            ),
        )
    )
      throw new Error("The selected sample or population is unavailable");
    const names = new Set(sample.channels.map((channel) => channel.name));
    const coordinates = doc.gates.find(
      (gate) => gate.id === state.coordinateGateId,
    );
    for (const dimension of coordinates?.dimensions || [])
      names.add(dimension.channel);
    if (
      !names.has(state.x) ||
      (!["histogram", "cdf"].includes(state.mode) && !names.has(state.y)) ||
      (state.mode === "3d" &&
        [state.threeD.z, state.threeD.color_by, state.threeD.size_by]
          .filter(Boolean)
          .some((name) => !names.has(name)))
    )
      throw new Error("The selected plot parameters are unavailable");
    const inherited = (owner || mainView).memory
      .serialize()
      .filter((v) => v.workspaceId === state.workspaceId)
      .slice(-96);
    return create(state, undefined, undefined, inherited);
  });
  ipcMain.handle(
    "cytoforge:plot:update",
    (event, state, dirty, active = true) => {
      const record = recordFor(event) || (trustedMain(event) ? mainView : null);
      if (
        !record ||
        !validState(state) ||
        state.workspaceId !==
          (record === mainView ? mainWorkspace() : record.state.workspaceId) ||
        typeof dirty !== "boolean" ||
        typeof active !== "boolean"
      )
        return false;
      if (
        JSON.stringify(record.state) !== JSON.stringify(state) ||
        record.active !== active ||
        (record === mainView ? mainDirty : record.dirty) !== dirty
      )
        record.generation++;
      record.state = structuredClone(state);
      record.memory.remember(state);
      record.active = active;
      if (record === mainView) mainDirty = dirty;
      else record.dirty = dirty;
      scheduleSave();
      return true;
    },
  );
  ipcMain.handle("cytoforge:plot:navigate", async (event, action) => {
    const owner = recordFor(event) || (trustedMain(event) ? mainView : null);
    const population = [
      "parent",
      "population",
      "child",
      "sibling_previous",
      "sibling_next",
      "reset_population",
    ].includes(action?.direction);
    if (
      !owner?.state ||
      !owner.active ||
      shuttingDown ||
      (owner === mainView && owner.state.workspaceId !== mainWorkspace()) ||
      !action ||
      typeof action !== "object" ||
      Array.isArray(action) ||
      Object.keys(action).some(
        (k) =>
          !["direction", "sync", "targetSampleId", "targetGateId"].includes(k),
      ) ||
      ![
        "previous",
        "next",
        "parent",
        "select",
        "population",
        "child",
        "sibling_previous",
        "sibling_next",
        "reset_population",
      ].includes(action.direction) ||
      typeof action.sync !== "boolean" ||
      (action.direction === "select"
        ? !identifier(action.targetSampleId)
        : action.targetSampleId !== undefined) ||
      (action.direction === "population"
        ? !Object.hasOwn(action, "targetGateId") ||
          !reference(action.targetGateId)
        : action.targetGateId !== undefined) ||
      (population && action.sync)
    )
      throw new Error("Invalid plot navigation request");
    if (navigating) throw new Error("Another plot navigation is still running");
    const workspaceId = owner.state.workspaceId;
    const sourceSampleId = owner.state.sampleId;
    const targets = action.sync
      ? [mainView, ...records.values()].filter(
          (record) =>
            record.active &&
            record.state?.workspaceId === workspaceId &&
            record.state.sampleId === owner.state.sampleId &&
            (record !== mainView || mainWorkspace() === workspaceId),
        )
      : [owner];
    const dirty = (record) => (record === mainView ? mainDirty : record.dirty);
    if (
      targets.some(
        (record) => dirty(record) || record.confirming || record.closeAllowed,
      )
    )
      throw new Error(
        "Save or cancel gate edits in the affected plot windows before navigating",
      );
    const snapshots = targets.map((record) => ({
      record,
      generation: record.generation,
    }));
    navigating = true;
    try {
      const before = await request("/workspaces/" + workspaceId + "/revision");
      const plan = await request(
        "/workspaces/" + workspaceId + "/plot-navigation/plan",
        {
          revision: before.revision,
          initiator: owner.id,
          direction: action.direction,
          ...(action.targetSampleId
            ? { targetSampleId: action.targetSampleId }
            : {}),
          ...(action.direction === "population"
            ? { targetGateId: action.targetGateId }
            : {}),
          ...(population
            ? {
                rememberedViews: owner.memory.candidates(
                  workspaceId,
                  sourceSampleId,
                ),
              }
            : {}),
          views: targets.map((record) => ({
            id: record.id,
            state: record.state,
          })),
        },
      );
      if (!plan.available)
        throw new Error(plan.reason || "No compatible sample");
      const latest = await request("/workspaces/" + workspaceId + "/revision");
      if (
        latest.revision !== plan.revision ||
        (action.sync &&
          [mainView, ...records.values()]
            .filter(
              (r) =>
                r.active &&
                r.state?.workspaceId === workspaceId &&
                r.state.sampleId === sourceSampleId &&
                (r !== mainView || mainWorkspace() === workspaceId),
            )
            .map((r) => r.id)
            .sort()
            .join() !==
            targets
              .map((r) => r.id)
              .sort()
              .join()) ||
        snapshots.some(
          ({ record, generation }) =>
            record.generation !== generation ||
            dirty(record) ||
            !record.active ||
            record.confirming ||
            record.closeAllowed ||
            (record === mainView
              ? mainWorkspace() !== workspaceId
              : record.window.isDestroyed()),
        )
      )
        throw new Error(
          "A plot or workspace changed while navigating. Retry the move.",
        );
      if (
        !Array.isArray(plan.views) ||
        plan.views.length !== targets.length ||
        new Set(plan.views.map((v) => v.id)).size !== targets.length
      )
        throw new Error("Invalid plot navigation plan");
      const changes = targets.map((record) => {
        const state = plan.views.find((v) => v.id === record.id)?.state;
        if (state?.threeD === null) delete state.threeD;
        if (!validState(state) || state.workspaceId !== workspaceId)
          throw new Error("Invalid plot navigation state");
        return { record, state };
      });
      // Commit every native descriptor before notifying any renderer.
      for (const { record, state } of changes) {
        record.state = structuredClone(state);
        record.memory.remember(state);
        record.generation++;
      }
      scheduleSave();
      for (const { record, state } of changes) {
        const window = record === mainView ? mainWindow() : record.window;
        if (window && !window.isDestroyed())
          window.webContents.send("cytoforge:plot:navigated", state);
      }
      return {
        moved: changes.length,
        skipped: plan.skipped,
        revision: plan.revision,
      };
    } finally {
      navigating = false;
    }
  });
  ipcMain.handle("cytoforge:plot:close", (event) => {
    const record = recordFor(event);
    if (!record) return false;
    record.window.close();
    return true;
  });
  ipcMain.on("cytoforge:gate:dirty", (event, dirty) => {
    if (typeof dirty !== "boolean") return;
    const record = recordFor(event);
    if (record) {
      if (record.dirty !== dirty) record.generation++;
      record.dirty = dirty;
    } else if (trustedMain(event)) {
      if (mainDirty !== dirty) mainView.generation++;
      mainDirty = dirty;
    }
  });
  ipcMain.on("cytoforge:workspace:notify", (event, id) => {
    if (workspaceFor(event) === id) void refresh(id).catch(() => {});
  });
  return {
    workspaceFor,
    async restore() {
      if (!savedSession) return;
      for (const record of savedSession.windows)
        await create(
          record.state,
          record.bounds,
          record.id,
          record.views || [],
        );
    },
    async confirmQuit() {
      const dirty =
        Number(mainDirty) +
        [...records.values()].filter((record) => record.dirty).length;
      return !dirty || confirmDiscard(mainWindow(), dirty);
    },
    closeForQuit() {
      if (shuttingDown) return;
      try {
        persist();
      } catch (error) {
        console.error("Plot windows could not be saved:", error.message);
      }
      shuttingDown = true;
      clearTimeout(saveTimer);
      clearInterval(pollTimer);
      for (const record of [...records.values()]) record.window.destroy();
    },
  };
}
module.exports = { installPlotWindows, validState };
