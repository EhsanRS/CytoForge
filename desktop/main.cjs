const {
  app,
  BrowserWindow,
  dialog,
  ipcMain,
  session,
  Menu,
} = require("electron");
const { spawn } = require("node:child_process");
const { Worker } = require("node:worker_threads");
const {
  createReadStream,
  mkdirSync,
  existsSync,
  readFileSync,
  writeFileSync,
  renameSync,
  statSync,
  unlinkSync,
} = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const os = require("node:os");
const { installPlotWindows } = require("./plot-windows.cjs");
const { installComparisonWindows } = require("./comparison-windows.cjs");

const projectRoot = path.resolve(__dirname, "..");
const runtimeRoot =
  process.env.CYTOFORGE_HOME ||
  (app.isPackaged ? path.join(os.homedir(), "CytoForge") : projectRoot);
const profile = path.join(runtimeRoot, ".cache", "desktop-profile");
const logs = path.join(runtimeRoot, ".cache", "logs");
const temp = path.join(runtimeRoot, ".tmp");
const settingsDirectory = path.join(runtimeRoot, ".config");
const settingsPath = path.join(settingsDirectory, "desktop-settings.json");
for (const directory of [profile, logs, temp, settingsDirectory])
  mkdirSync(directory, { recursive: true });
app.setPath("userData", profile);
app.setPath("sessionData", profile);
app.setPath("logs", logs);
app.setPath("temp", temp);
app.commandLine.appendSwitch(
  "disk-cache-dir",
  path.join(profile, "http-cache"),
);
app.commandLine.appendSwitch("log-file", path.join(logs, "chromium.log"));

let engine,
  window,
  origin,
  quitting = false;
if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on("second-instance", () => {
    if (window) {
      if (window.isMinimized()) window.restore();
      window.focus();
    }
  });
  app
    .whenReady()
    .then(launch)
    .catch((error) => {
      console.error("CytoForge could not start:", error.message);
      if (process.env.CYTOFORGE_HEADLESS_TEST !== "1")
        dialog.showErrorBox("CytoForge could not start", error.message);
      app.quit();
    });
}
async function launch() {
  session.defaultSession.setPermissionRequestHandler(
    (_contents, _permission, callback) => callback(false),
  );
  session.defaultSession.setPermissionCheckHandler(() => false);
  const token = crypto.randomBytes(32).toString("base64url");
  const frontend = app.isPackaged
    ? path.join(process.resourcesPath, "frontend")
    : path.join(projectRoot, "frontend", "dist");
  if (!existsSync(path.join(frontend, "index.html")))
    throw new Error("Build the interface first: npm run build");
  const command = app.isPackaged
    ? path.join(
        process.resourcesPath,
        "engine",
        `cytoforge-engine${process.platform === "win32" ? ".exe" : ""}`,
      )
    : path.join(
        projectRoot,
        ".venv",
        process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
      );
  const args = app.isPackaged ? [] : ["-m", "cytoforge"];
  args.push(
    "--port",
    "0",
    "--data-dir",
    path.join(runtimeRoot, "data"),
    "--frontend-dir",
    frontend,
    "--parent-pid",
    String(process.pid),
  );
  const env = {
    ...process.env,
    CYTOFORGE_API_TOKEN: token,
    PYTHONPATH: path.join(projectRoot, "backend"),
    PYTHONPYCACHEPREFIX: path.join(runtimeRoot, ".cache/python"),
    TMPDIR: temp,
    TEMP: temp,
    TMP: temp,
    XDG_CACHE_HOME: path.join(runtimeRoot, ".cache"),
    XDG_CONFIG_HOME: path.join(runtimeRoot, ".config"),
    XDG_DATA_HOME: path.join(runtimeRoot, ".local/share"),
    XDG_STATE_HOME: path.join(runtimeRoot, ".local/state"),
    MPLCONFIGDIR: path.join(runtimeRoot, ".cache/matplotlib"),
    NUMBA_CACHE_DIR: path.join(runtimeRoot, ".cache/numba"),
    JOBLIB_TEMP_FOLDER: temp,
    OMP_NUM_THREADS: "4",
    OPENBLAS_NUM_THREADS: "4",
  };
  const stderr = [];
  origin = await new Promise((resolve, reject) => {
    engine = spawn(command, args, {
      env,
      cwd: runtimeRoot,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    const timeout = setTimeout(
      () => reject(new Error("Analysis engine startup timed out")),
      60000,
    );
    engine.on("error", (error) => {
      clearTimeout(timeout);
      reject(error);
    });
    engine.on("exit", (code) => {
      clearTimeout(timeout);
      if (!quitting) {
        reject(
          new Error(`Engine exited (${code}). ${stderr.join("").slice(-2000)}`),
        );
        if (window) {
          dialog.showErrorBox(
            "Analysis engine stopped",
            stderr.join("").slice(-2000) ||
              "Restart CytoForge to recover saved work.",
          );
          app.quit();
        }
      }
    });
    engine.stderr.on("data", (data) => {
      stderr.push(data.toString());
      if (stderr.length > 30) stderr.shift();
    });
    let output = "";
    engine.stdout.on("data", (chunk) => {
      output += chunk.toString();
      const lines = output.split("\n");
      output = lines.pop();
      for (const line of lines) {
        try {
          const message = JSON.parse(line);
          if (message.event === "cytoforge-starting") {
            clearTimeout(timeout);
            resolve(`http://127.0.0.1:${message.port}`);
          }
        } catch {
          /* Non-protocol logs are ignored. */
        }
      }
    });
  });
  const deadline = Date.now() + 60000;
  while (true) {
    try {
      const response = await fetch(`${origin}/api/health`, {
        signal: AbortSignal.timeout(1000),
      });
      if (response.ok) break;
    } catch {
      /* Wait for uvicorn to bind. */
    }
    if (Date.now() > deadline)
      throw new Error("The analysis engine did not become ready");
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  let workspaceId = null;
  try {
    if (statSync(settingsPath).size <= 16384) {
      const settings = JSON.parse(readFileSync(settingsPath, "utf8"));
      if (
        settings.version === 1 &&
        typeof settings.workspaceId === "string" &&
        /^[a-f0-9]{32}$/.test(settings.workspaceId)
      )
        workspaceId = settings.workspaceId;
    }
  } catch (error) {
    if (error.code !== "ENOENT")
      console.error("Desktop settings could not be read:", error.message);
  }
  const trustedRenderer = (event) =>
    event.sender === window?.webContents &&
    event.senderFrame === window.webContents.mainFrame &&
    new URL(event.senderFrame.url).origin === origin;
  ipcMain.handle("cytoforge:workspace:get", (event) =>
    trustedRenderer(event) ? workspaceId : null,
  );
  const plateDraftDirectory = path.join(settingsDirectory, "plate-drafts");
  const validDraftWorkspace = (value) =>
    typeof value === "string" && /^[a-f0-9]{32}$/.test(value);
  const maximumDraftBytes = 16 * 1024 * 1024;
  ipcMain.handle("cytoforge:plate-draft:get", (event, identifier) => {
    if (!trustedRenderer(event) || !validDraftWorkspace(identifier))
      return null;
    const filename = path.join(plateDraftDirectory, `${identifier}.json`);
    try {
      if (statSync(filename).size > maximumDraftBytes)
        throw new Error("Plate draft exceeds sixteen MiB");
      const value = readFileSync(filename, "utf8");
      return value;
    } catch (error) {
      if (error.code === "ENOENT") return null;
      throw error;
    }
  });
  ipcMain.handle("cytoforge:plate-draft:set", (event, identifier, value) => {
    if (!trustedRenderer(event) || !validDraftWorkspace(identifier))
      return false;
    const filename = path.join(plateDraftDirectory, `${identifier}.json`);
    if (value === null) {
      if (existsSync(filename)) unlinkSync(filename);
      return true;
    }
    if (
      typeof value !== "string" ||
      Buffer.byteLength(value, "utf8") > maximumDraftBytes
    )
      return false;
    const draft = JSON.parse(value);
    if (
      !draft?.plate ||
      !validDraftWorkspace(draft.plate.id) ||
      !Number.isInteger(draft.baseRevision) ||
      (draft.baseline !== null && typeof draft.baseline !== "string")
    )
      return false;
    mkdirSync(plateDraftDirectory, { recursive: true });
    const temporary = `${filename}.${crypto.randomBytes(6).toString("hex")}.tmp`;
    try {
      writeFileSync(temporary, value, { mode: 0o600, flag: "wx", flush: true });
      renameSync(temporary, filename);
    } finally {
      if (existsSync(temporary)) unlinkSync(temporary);
    }
    return true;
  });
  const reportDraftDirectory = path.join(settingsDirectory, "report-drafts");
  ipcMain.handle("cytoforge:report-draft:get", (event, identifier) => {
    if (!trustedRenderer(event) || !validDraftWorkspace(identifier))
      return null;
    const filename = path.join(reportDraftDirectory, `${identifier}.json`);
    try {
      if (statSync(filename).size > 2 * 1024 * 1024)
        throw new Error("Report draft exceeds two MiB");
      return readFileSync(filename, "utf8");
    } catch (error) {
      if (error.code === "ENOENT") return null;
      throw error;
    }
  });
  ipcMain.handle("cytoforge:report-draft:set", (event, identifier, value) => {
    if (!trustedRenderer(event) || !validDraftWorkspace(identifier))
      return false;
    const filename = path.join(reportDraftDirectory, `${identifier}.json`);
    if (value === null) {
      if (existsSync(filename)) unlinkSync(filename);
      return true;
    }
    if (typeof value !== "string" || Buffer.byteLength(value) > 2 * 1024 * 1024)
      return false;
    const draft = JSON.parse(value);
    if (
      !validDraftWorkspace(draft.layout?.id) ||
      !Number.isInteger(draft.baseRevision) ||
      !Array.isArray(draft.layout.elements)
    )
      return false;
    mkdirSync(reportDraftDirectory, { recursive: true });
    const temporary = `${filename}.${crypto.randomBytes(6).toString("hex")}.tmp`;
    try {
      writeFileSync(temporary, value, { mode: 0o600, flag: "wx", flush: true });
      renameSync(temporary, filename);
    } finally {
      if (existsSync(temporary)) unlinkSync(temporary);
    }
    return true;
  });
  let printing = false;
  let exportingEvents = false;
  ipcMain.handle("cytoforge:events:save", async (event, descriptor) => {
    if (
      !descriptor ||
      Object.keys(descriptor).sort().join(",") !== "exportId,workspaceId" ||
      !validDraftWorkspace(descriptor.workspaceId) ||
      !validDraftWorkspace(descriptor.exportId) ||
      plotWindows.workspaceFor(event) !== descriptor.workspaceId
    )
      throw new Error("Invalid desktop event export descriptor");
    if (exportingEvents)
      throw new Error("An event file is already being saved");
    exportingEvents = true;
    try {
      const base = `${origin}/api/workspaces/${descriptor.workspaceId}/event-exports/${descriptor.exportId}`;
      const response = await fetch(base, {
        headers: { "X-CytoForge-Token": token },
        signal: AbortSignal.timeout(15000),
      });
      if (!response.ok)
        throw new Error("The prepared event export is unavailable");
      const prepared = await response.json();
      if (
        !prepared.can_download ||
        !/^[a-f0-9]{64}$/.test(prepared.file_sha256) ||
        !Number.isSafeInteger(prepared.file_bytes) ||
        prepared.file_bytes < 1
      )
        throw new Error("Prepare the event export again before saving");
      const owner = event.sender;
      const url = `${base}/download`;
      const saved = await new Promise((resolve, reject) => {
        let item;
        const finish = (error, value) => {
          clearTimeout(timer);
          owner.session.removeListener("will-download", started);
          owner.removeListener("destroyed", destroyed);
          if (error) reject(error);
          else resolve(value);
        };
        const destroyed = () => {
          item?.cancel();
          finish(
            new Error("The desktop window closed during the event export"),
          );
        };
        const started = (_downloadEvent, download, contents) => {
          if (contents !== owner || download.getURL() !== url) return;
          item = download;
          clearTimeout(timer);
          owner.session.removeListener("will-download", started);
          download.once("done", (_doneEvent, state) => {
            if (state === "completed")
              finish(null, {
                path: download.getSavePath(),
                bytes: download.getReceivedBytes(),
              });
            else if (state === "cancelled") finish(null, { canceled: true });
            else
              finish(
                new Error(
                  "The event file download was interrupted; try saving again",
                ),
              );
          });
        };
        const timer = setTimeout(
          () =>
            finish(
              new Error("The event export did not start; try saving again"),
            ),
          120000,
        );
        owner.session.on("will-download", started);
        owner.once("destroyed", destroyed);
        try {
          owner.downloadURL(url, { headers: { "X-CytoForge-Token": token } });
        } catch (error) {
          finish(error);
        }
      });
      if (saved.canceled) return saved;
      const checksum = crypto.createHash("sha256");
      for await (const chunk of createReadStream(saved.path))
        checksum.update(chunk);
      const sha256 = checksum.digest("hex");
      if (
        saved.bytes !== prepared.file_bytes ||
        sha256 !== prepared.file_sha256
      )
        throw new Error(
          "The saved event file failed its integrity check; prepare the export again",
        );
      return { ...saved, sha256 };
    } finally {
      exportingEvents = false;
    }
  });
  ipcMain.handle("cytoforge:report:pdf", async (event, descriptor) => {
    if (!trustedRenderer(event))
      throw new Error("PDF export is only available to the desktop window");
    if (printing) throw new Error("A report is already being exported");
    if (
      !descriptor ||
      !validDraftWorkspace(descriptor.workspace) ||
      !Number.isInteger(descriptor.revision) ||
      descriptor.revision < 0 ||
      typeof descriptor.title !== "string" ||
      descriptor.title.length > 160 ||
      typeof descriptor.manifest !== "string" ||
      Buffer.byteLength(descriptor.manifest) > 16 * 1024 * 1024 ||
      !/^[a-f0-9]{64}$/.test(descriptor.reportHash) ||
      !/^[a-f0-9]{64}$/.test(descriptor.domHash) ||
      !Number.isInteger(descriptor.pageCount) ||
      descriptor.pageCount < 1 ||
      descriptor.pageCount > 1024 ||
      crypto.createHash("sha256").update(descriptor.manifest).digest("hex") !==
        descriptor.reportHash
    )
      throw new Error("Invalid PDF report descriptor");
    const manifest = JSON.parse(descriptor.manifest);
    if (
      manifest.workspace_id !== descriptor.workspace ||
      manifest.revision !== descriptor.revision ||
      manifest.pages?.length !== descriptor.pageCount ||
      !manifest.review?.definition ||
      !/^[a-f0-9]{64}$/.test(manifest.review.review_hash)
    )
      throw new Error("PDF report was not reviewed");
    async function validatePrepared() {
      const state = await window.webContents.executeJavaScript(
        `(() => { const root=document.getElementById('cytoforge-report-print'); return root ? {hash:root.dataset.reportHash,content:root.innerHTML,count:root.children.length} : null; })()`,
      );
      if (
        !state ||
        state.hash !== descriptor.reportHash ||
        state.count !== descriptor.pageCount ||
        crypto.createHash("sha256").update(state.content).digest("hex") !==
          descriptor.domHash
      )
        throw new Error("Prepared PDF pages changed; export again");
      const response = await fetch(
        `${origin}/api/workspaces/${descriptor.workspace}/reports/verify`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CytoForge-Token": token,
          },
          body: JSON.stringify({
            revision: descriptor.revision,
            definition: manifest.review.definition,
            review_hash: manifest.review.review_hash,
            proofs: manifest.pages.map((page) => ({
              page: page.page,
              data_hash: page.data_hash,
              svg_sha256: page.svg_sha256,
            })),
          }),
          signal: AbortSignal.timeout(15000),
        },
      );
      if (!response.ok)
        throw new Error(
          "Report sources changed; review the report before exporting",
        );
    }
    printing = true;
    try {
      await validatePrepared();
      mkdirSync(path.join(runtimeRoot, "exports"), { recursive: true });
      const chosen = await dialog.showSaveDialog(window, {
        title: "Export report PDF",
        defaultPath: path.join(
          runtimeRoot,
          "exports",
          `${descriptor.title.replace(/[^\p{L}\p{N}._ -]/gu, "_").slice(0, 120) || "report"}.pdf`,
        ),
        filters: [{ name: "PDF report", extensions: ["pdf"] }],
      });
      if (chosen.canceled || !chosen.filePath) return { canceled: true };
      await validatePrepared();
      const bytes = await window.webContents.printToPDF({
        printBackground: true,
        preferCSSPageSize: true,
        margins: { top: 0, bottom: 0, left: 0, right: 0 },
      });
      if (bytes.length > 128 * 1024 * 1024)
        throw new Error("PDF exceeds 128 MiB; export fewer pages");
      await validatePrepared();
      return await new Promise((resolve, reject) => {
        const worker = new Worker(path.join(__dirname, "pdf-worker.cjs"), {
          workerData: { ...descriptor, filename: chosen.filePath, bytes },
        });
        const timeout = setTimeout(() => {
          void worker.terminate();
          reject(new Error("PDF export timed out"));
        }, 60000);
        worker.once("message", (result) => {
          clearTimeout(timeout);
          if (result.error) reject(new Error(result.error));
          else resolve(result);
        });
        worker.once("error", (error) => {
          clearTimeout(timeout);
          reject(error);
        });
        worker.once("exit", (code) => {
          clearTimeout(timeout);
          if (code !== 0) reject(new Error(`PDF writer exited (${code})`));
        });
      });
    } finally {
      printing = false;
    }
  });
  ipcMain.on("cytoforge:workspace:set", (event, value) => {
    if (
      !trustedRenderer(event) ||
      !(
        value === null ||
        (typeof value === "string" && /^[a-f0-9]{32}$/.test(value))
      ) ||
      value === workspaceId
    )
      return;
    const temporary = `${settingsPath}.${crypto.randomBytes(6).toString("hex")}.tmp`;
    try {
      writeFileSync(
        temporary,
        JSON.stringify({ version: 1, workspaceId: value }),
        { mode: 0o600, flag: "wx" },
      );
      renameSync(temporary, settingsPath);
      workspaceId = value;
    } catch (error) {
      console.error("Desktop settings could not be saved:", error.message);
    } finally {
      if (existsSync(temporary)) unlinkSync(temporary);
    }
  });
  window = new BrowserWindow({
    show: process.env.CYTOFORGE_HEADLESS_TEST !== "1",
    width: 1540,
    height: 990,
    minWidth: 900,
    minHeight: 650,
    backgroundColor: "#0c1016",
    title: "CytoForge",
    icon: path.join(projectRoot, "desktop/assets/icon.png"),
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      offscreen: process.env.CYTOFORGE_HEADLESS_TEST === "1",
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      webSecurity: true,
      spellcheck: false,
    },
  });
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-navigate", (event, url) => {
    if (new URL(url).origin !== origin) event.preventDefault();
  });
  const plotWindows = installPlotWindows({
    origin,
    token,
    settingsDirectory,
    mainWindow: () => window,
    mainWorkspace: () => workspaceId,
    icon: path.join(projectRoot, "desktop/assets/icon.png"),
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      offscreen: process.env.CYTOFORGE_HEADLESS_TEST === "1",
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      webSecurity: true,
      spellcheck: false,
    },
  });
  const comparisonWindows = installComparisonWindows({
    origin,
    token,
    settingsDirectory,
    authorize: plotWindows.workspaceFor,
    icon: path.join(projectRoot, "desktop/assets/icon.png"),
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      offscreen: process.env.CYTOFORGE_HEADLESS_TEST === "1",
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      webSecurity: true,
      spellcheck: false,
    },
  });
  let closeAllowed = false,
    closingNonce = null,
    closeTimer = null,
    finishingClose = false;
  async function finishClose(saved) {
    if (finishingClose) return;
    finishingClose = true;
    clearTimeout(closeTimer);
    if (!saved) {
      if (process.env.CYTOFORGE_HEADLESS_TEST === "1") {
        console.error("Desktop draft flush did not complete before close");
        process.exitCode = 1;
      } else {
        const choice = await dialog.showMessageBox(window, {
          type: "warning",
          title: "Draft could not be saved",
          message: "Save or export the open draft before closing CytoForge.",
          buttons: ["Keep editing", "Discard draft and quit"],
          defaultId: 0,
          cancelId: 0,
        });
        if (choice.response === 0) {
          closingNonce = null;
          quitting = false;
          finishingClose = false;
          return;
        }
      }
    }
    if (!(await plotWindows.confirmQuit())) {
      closingNonce = null;
      quitting = false;
      finishingClose = false;
      return;
    }
    closeAllowed = true;
    window.close();
  }
  ipcMain.on("cytoforge:plate-draft:flushed", (event, nonce, saved) => {
    if (!trustedRenderer(event) || !closingNonce || nonce !== closingNonce)
      return;
    void finishClose(saved === true);
  });
  window.on("close", (event) => {
    if (closeAllowed) return;
    event.preventDefault();
    if (closingNonce) return;
    closingNonce = crypto.randomBytes(12).toString("hex");
    window.webContents.send("cytoforge:flush-plate-draft", closingNonce);
    closeTimer = setTimeout(() => void finishClose(false), 3000);
  });
  window.on("closed", () => {
    plotWindows.closeForQuit();
    comparisonWindows.closeForQuit();
    window = null;
  });
  session.defaultSession.on("will-download", (_event, item) => {
    item.setSaveDialogOptions({
      defaultPath: path.join(
        runtimeRoot,
        "exports",
        path.basename(item.getFilename()),
      ),
    });
  });
  Menu.setApplicationMenu(
    Menu.buildFromTemplate([
      ...(process.platform === "darwin"
        ? [
            {
              label: "CytoForge",
              submenu: [
                { role: "about" },
                { type: "separator" },
                { role: "quit" },
              ],
            },
          ]
        : []),
      {
        label: "File",
        submenu: [
          {
            label: "Export report PDF",
            accelerator: "CmdOrCtrl+P",
            click: () => window.webContents.send("cytoforge:export-report"),
          },
          { type: "separator" },
          { role: "close" },
          { role: "quit" },
        ],
      },
      {
        label: "Edit",
        submenu: [
          { role: "cut" },
          { role: "copy" },
          { role: "paste" },
          { role: "selectAll" },
        ],
      },
      {
        label: "View",
        submenu: [
          { role: "reload" },
          { role: "toggleDevTools" },
          { role: "resetZoom" },
          { role: "zoomIn" },
          { role: "zoomOut" },
          { role: "togglefullscreen" },
        ],
      },
    ]),
  );
  await window.loadURL(origin);
  await plotWindows.restore();
  await comparisonWindows.restore();
  app.on("activate", () => window?.show());
}
app.on("window-all-closed", () => app.quit());
app.on("before-quit", () => {
  quitting = true;
});
app.on("will-quit", () => {
  if (engine && !engine.killed) engine.kill("SIGTERM");
});
process.on("exit", () => {
  if (engine && !engine.killed) engine.kill();
});
