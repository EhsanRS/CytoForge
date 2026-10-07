const { contextBridge, ipcRenderer } = require("electron");
let pendingDraft = Promise.resolve(true);
let pendingReportDraft = Promise.resolve(true);
const workspaceListeners = new Set();
const navigationListeners = new Set();
ipcRenderer.on("cytoforge:plot:navigated", (_event, state) => {
  if (
    !state ||
    !/^[a-f0-9]{32}$/.test(state.workspaceId) ||
    !/^[a-f0-9]{32}$/.test(state.sampleId)
  )
    return;
  for (const listener of navigationListeners) listener(state);
});
ipcRenderer.on("cytoforge:workspace:changed", (_event, value) => {
  if (
    !value ||
    !/^[a-f0-9]{32}$/.test(value.workspaceId) ||
    !Number.isSafeInteger(value.revision) ||
    value.revision < 0
  )
    return;
  for (const listener of workspaceListeners) listener(value);
});
ipcRenderer.on("cytoforge:export-report", () =>
  window.dispatchEvent(new Event("cytoforge:export-report")),
);
ipcRenderer.on("cytoforge:flush-plate-draft", (_event, nonce) => {
  window.dispatchEvent(new Event("cytoforge:flush-plate-draft"));
  window.dispatchEvent(new Event("cytoforge:flush-report-draft"));
  void Promise.all([pendingDraft, pendingReportDraft]).then((saved) =>
    ipcRenderer.send(
      "cytoforge:plate-draft:flushed",
      nonce,
      saved.every(Boolean),
    ),
  );
});

// Expose validated workspace identifiers and bounded plate draft persistence.
// Filesystem access stays in the isolated main process; the renderer stays sandboxed.
contextBridge.exposeInMainWorld("cytoforgeDesktop", {
  getComparisonWindow: () => ipcRenderer.invoke("cytoforge:comparison:context"),
  openComparisonWindow: (state) =>
    ipcRenderer.invoke("cytoforge:comparison:open", state),
  updateComparisonWindow: (state) =>
    ipcRenderer.invoke("cytoforge:comparison:update", state),
  getPlotWindow: () => ipcRenderer.invoke("cytoforge:plot:context"),
  getPlotSession: (workspaceId) =>
    ipcRenderer.invoke("cytoforge:plot:session", workspaceId),
  openPlotWindow: (state) => ipcRenderer.invoke("cytoforge:plot:open", state),
  updatePlotWindow: (state, dirty, active = true) =>
    ipcRenderer.invoke("cytoforge:plot:update", state, dirty, active),
  navigatePlot: (action) =>
    ipcRenderer.invoke("cytoforge:plot:navigate", action),
  onPlotNavigation: (listener) => {
    if (typeof listener !== "function") return () => {};
    navigationListeners.add(listener);
    return () => navigationListeners.delete(listener);
  },
  closePlotWindow: () => ipcRenderer.invoke("cytoforge:plot:close"),
  setGateDraftDirty: (value) => {
    if (typeof value === "boolean")
      ipcRenderer.send("cytoforge:gate:dirty", value);
  },
  notifyWorkspaceChanged: (value) => {
    if (typeof value === "string" && /^[a-f0-9]{32}$/.test(value))
      ipcRenderer.send("cytoforge:workspace:notify", value);
  },
  onWorkspaceChanged: (listener) => {
    if (typeof listener !== "function") return () => {};
    workspaceListeners.add(listener);
    return () => workspaceListeners.delete(listener);
  },
  exportReportPdf: (descriptor) =>
    ipcRenderer.invoke("cytoforge:report:pdf", descriptor),
  saveEventExport: (descriptor) =>
    ipcRenderer.invoke("cytoforge:events:save", descriptor),
  getReportDraft: (workspace) =>
    /^[a-f0-9]{32}$/.test(workspace)
      ? ipcRenderer.invoke("cytoforge:report-draft:get", workspace)
      : Promise.resolve(null),
  saveReportDraft(workspace, value) {
    if (
      typeof workspace !== "string" ||
      !/^[a-f0-9]{32}$/.test(workspace) ||
      !(value === null || typeof value === "string")
    )
      return Promise.resolve(false);
    const result = ipcRenderer.invoke(
      "cytoforge:report-draft:set",
      workspace,
      value,
    );
    pendingReportDraft = result.catch(() => false);
    return result;
  },
  getWorkspace: () => ipcRenderer.invoke("cytoforge:workspace:get"),
  getPlateDraft(workspace) {
    if (typeof workspace !== "string" || !/^[a-f0-9]{32}$/.test(workspace))
      return Promise.resolve(null);
    return ipcRenderer.invoke("cytoforge:plate-draft:get", workspace);
  },
  savePlateDraft(workspace, value) {
    if (
      typeof workspace !== "string" ||
      !/^[a-f0-9]{32}$/.test(workspace) ||
      !(value === null || typeof value === "string")
    )
      return Promise.resolve(false);
    const result = ipcRenderer.invoke(
      "cytoforge:plate-draft:set",
      workspace,
      value,
    );
    pendingDraft = result.catch(() => false);
    return result;
  },
  saveWorkspace(value) {
    if (
      value === null ||
      (typeof value === "string" && /^[a-f0-9]{32}$/.test(value))
    ) {
      ipcRenderer.send("cytoforge:workspace:set", value);
    }
  },
});
