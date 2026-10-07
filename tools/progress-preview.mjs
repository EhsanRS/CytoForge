// Show the actual packaged desktop process through a remote screen viewer.
// The viewer does not serve application HTML or expose the analysis API.
import { _electron as electron } from "playwright";
import { createServer } from "node:http";
import {
  createReadStream,
  existsSync,
  mkdirSync,
  readFileSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { createHash, randomBytes, timingSafeEqual } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { previewConfiguration } from "./desktop-preview/config.mjs";

const {
  root,
  name,
  binary,
  runtime,
  port,
  host,
  publicHost,
  tokenPath,
  sessionPath,
  cookieName,
} = previewConfiguration(
  path.resolve(path.dirname(fileURLToPath(import.meta.url)), ".."),
);
const viewer = path.join(root, "tools/desktop-preview");
for (const directory of [
  runtime,
  path.join(runtime, "imports"),
  path.join(runtime, "exports"),
  path.join(root, "artifacts"),
  path.join(root, ".config"),
  path.dirname(tokenPath),
])
  mkdirSync(directory, { recursive: true });
const token = existsSync(tokenPath)
  ? readFileSync(tokenPath, "utf8").trim()
  : randomBytes(24).toString("base64url");
if (!/^[A-Za-z0-9_-]{32}$/.test(token))
  throw new Error("The desktop preview session key is invalid");
if (!existsSync(tokenPath))
  writeFileSync(tokenPath, token, { mode: 0o600, flag: "wx" });
const noSandbox = process.env.CYTOFORGE_PREVIEW_NO_SANDBOX === "1";
const args = ["--headless", "--ozone-platform=headless", "--disable-gpu"];
if (noSandbox) args.push("--no-sandbox");
let application,
  window,
  mainWindow,
  stopping = false,
  sessionReport;
let pendingChooser = null,
  capturePromise = null,
  lastFrame = null,
  lastFrameAt = 0;
let inputQueue = Promise.resolve(),
  queuedInputs = 0;
const windows = new Map();
const registration = new Map();
let selectedWindowId = null;

function selectWindow(id) {
  const record = windows.get(id);
  if (!record || record.page.isClosed())
    throw new Error("Desktop window is unavailable");
  window = record.page;
  selectedWindowId = id;
  lastFrame = null;
  lastFrameAt = 0;
  capturePromise = null;
}
async function registerWindow(page) {
  if ([...windows.values()].some((record) => record.page === page)) return;
  if (registration.has(page)) return registration.get(page);
  const pending = (async () => {
    const origin = new URL(mainWindow.url()).origin;
    await page.waitForURL(
      (url) =>
        url.origin === origin &&
        (page === mainWindow ||
          ["plotWindow", "comparisonWindow"].some((key) =>
            /^[a-f0-9]{32}$/.test(url.searchParams.get(key) || ""),
          )),
      { timeout: 15000 },
    );
    await page.waitForLoadState("domcontentloaded");
    const url = new URL(page.url());
    if (
      url.origin !== new URL(mainWindow.url()).origin ||
      (page !== mainWindow &&
        !["plotWindow", "comparisonWindow"].some((key) =>
          /^[a-f0-9]{32}$/.test(url.searchParams.get(key) || ""),
        ))
    )
      return;
    const handle = await application.browserWindow(page);
    const id = await handle.evaluate((window) => window.id);
    await handle.dispose();
    await page.setViewportSize({ width: 1540, height: 990 });
    if (page.isClosed()) return;
    windows.set(id, {
      page,
      kind:
        page === mainWindow
          ? "main"
          : url.searchParams.has("comparisonWindow")
            ? "comparison"
            : "plot",
    });
    if (selectedWindowId === null) selectWindow(id);
    page.on("filechooser", (dialog) => {
      pendingChooser = {
        id: randomBytes(12).toString("hex"),
        windowId: id,
        dialog,
        files: [],
      };
    });
    page.on("close", () => {
      windows.delete(id);
      if (pendingChooser?.windowId === id) pendingChooser = null;
      if (selectedWindowId === id && !stopping) {
        const fallback = [...windows.entries()].find(
          ([, record]) => record.page === mainWindow,
        );
        if (fallback) selectWindow(fallback[0]);
      }
    });
  })().finally(() => registration.delete(page));
  registration.set(page, pending);
  return pending;
}
async function desktopWindows() {
  return Promise.all(
    [...windows.entries()].map(async ([id, record]) => ({
      id,
      kind: record.kind,
      title: await record.page.title().catch(() => "Desktop window"),
      selected: id === selectedWindowId,
    })),
  );
}

function matchesToken(value) {
  if (typeof value !== "string" || value.length !== token.length) return false;
  return timingSafeEqual(Buffer.from(value), Buffer.from(token));
}
function authorized(request) {
  return (request.headers.cookie || "").split(";").some((part) => {
    const [name, value] = part.trim().split("=");
    return name === cookieName && matchesToken(value);
  });
}
function reply(response, code, value, type = "application/json") {
  response.writeHead(code, {
    "Content-Type": type,
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
  });
  response.end(type === "application/json" ? JSON.stringify(value) : value);
}
async function body(request, maximum) {
  const declared = Number(request.headers["content-length"] || 0);
  if (!Number.isFinite(declared) || declared < 0 || declared > maximum)
    throw new Error("Request is too large");
  let length = 0;
  const chunks = [];
  for await (const chunk of request) {
    length += chunk.length;
    if (length > maximum) throw new Error("Request is too large");
    chunks.push(chunk);
  }
  return Buffer.concat(chunks);
}
async function frame() {
  if (lastFrame && Date.now() - lastFrameAt < 200) return lastFrame;
  if (!capturePromise) {
    const page = window;
    const pending = page
      .screenshot({ type: "jpeg", quality: 85, timeout: 10000 })
      .then((value) => {
        if (page !== window) return frame();
        lastFrame = { bytes: value, windowId: selectedWindowId };
        lastFrameAt = Date.now();
        return lastFrame;
      })
      .catch((error) => {
        if (page !== window && !window.isClosed()) return frame();
        throw error;
      })
      .finally(() => {
        if (capturePromise === pending) capturePromise = null;
      });
    capturePromise = pending;
  }
  return capturePromise;
}
const namedKeys = new Set([
  "Enter",
  "Tab",
  "Backspace",
  "Delete",
  "Escape",
  "ArrowUp",
  "ArrowDown",
  "ArrowLeft",
  "ArrowRight",
  "Home",
  "End",
  "PageUp",
  "PageDown",
  "Shift",
  "Control",
  "Alt",
  "Meta",
  " ",
]);
function validateInput(value) {
  if (!value || typeof value !== "object")
    throw new Error("Invalid input event");
  if (["move", "down", "up"].includes(value.type)) {
    if (
      !Number.isFinite(value.x) ||
      !Number.isFinite(value.y) ||
      value.x < 0 ||
      value.y < 0 ||
      value.x >= 1540 ||
      value.y >= 990
    )
      throw new Error("Pointer is outside the desktop window");
    if (
      value.type !== "move" &&
      !["left", "middle", "right"].includes(value.button)
    )
      throw new Error("Invalid mouse button");
    return {
      type: value.type,
      x: value.x,
      y: value.y,
      button: value.button,
      clickCount: value.clickCount === 2 ? 2 : 1,
    };
  }
  if (value.type === "wheel") {
    if (
      ![value.x, value.y].every(
        (number) => Number.isFinite(number) && Math.abs(number) <= 2400,
      )
    )
      throw new Error("Invalid scroll event");
    return { type: "wheel", x: value.x, y: value.y };
  }
  if (
    ["keyDown", "keyUp"].includes(value.type) &&
    typeof value.key === "string" &&
    (namedKeys.has(value.key) || [...value.key].length === 1)
  )
    return { type: value.type, key: value.key };
  if (
    value.type === "text" &&
    typeof value.text === "string" &&
    value.text.length <= 4096
  )
    return { type: "text", text: value.text };
  throw new Error("Unsupported input event");
}
async function applyInputs(events) {
  if (queuedInputs + events.length > 128)
    throw new Error("Desktop input queue is full");
  queuedInputs += events.length;
  const operation = inputQueue
    .then(async () => {
      const target = window;
      for (const event of events) {
        if (target.isClosed()) break;
        try {
          if (event.type === "move") await target.mouse.move(event.x, event.y);
          else if (event.type === "down" || event.type === "up") {
            await target.mouse.move(event.x, event.y);
            await target.mouse[event.type]({
              button: event.button,
              clickCount: event.clickCount,
            });
          } else if (event.type === "wheel")
            await target.mouse.wheel(event.x, event.y);
          else if (event.type === "keyDown")
            await target.keyboard.down(event.key);
          else if (event.type === "keyUp") await target.keyboard.up(event.key);
          else await target.keyboard.insertText(event.text);
        } catch (error) {
          if (target.isClosed()) break;
          throw error;
        }
      }
      lastFrameAt = 0;
    })
    .finally(() => {
      queuedInputs -= events.length;
    });
  inputQueue = operation.catch(() => {});
  return operation;
}
function streamFile(response, filename, downloadName) {
  const info = statSync(filename);
  response.writeHead(200, {
    "Content-Type": "application/octet-stream",
    "Content-Length": info.size,
    "Content-Disposition": `attachment; filename*=UTF-8''${encodeURIComponent(downloadName)}`,
    "X-Content-Type-Options": "nosniff",
  });
  const stream = createReadStream(filename);
  stream.on("error", () => response.destroy());
  response.on("close", () => stream.destroy());
  stream.pipe(response);
}
async function downloads() {
  const records = await application.evaluate(() =>
    [...globalThis.cytoforgePreviewDownloads.values()].map(
      ({ id, name, state, filename, nativePdf }) => ({
        id,
        name,
        state,
        filename,
        nativePdf,
      }),
    ),
  );
  return records.map((record) => ({
    id: record.id,
    name: record.name,
    state:
      record.nativePdf &&
      existsSync(path.join(runtime, "exports", record.filename))
        ? "completed"
        : record.state,
  }));
}
const server = createServer(async (request, response) => {
  try {
    const url = new URL(request.url, "http://preview.local");
    if (request.method === "GET" && url.pathname === "/health")
      return reply(response, 200, {
        running: !!window && !stopping,
        desktop: true,
        packaged: sessionReport?.packaged ?? false,
      });
    if (request.method === "GET" && url.pathname === "/app")
      return streamFile(response, binary, path.basename(binary));
    const sessionLink =
      url.pathname === "/" &&
      request.method === "GET" &&
      matchesToken(url.searchParams.get("session"));
    // Render the authenticated landing directly. A cross-site redirect would
    // omit a newly set Strict cookie on its first request.
    if (sessionLink)
      response.setHeader(
        "Set-Cookie",
        `${cookieName}=${token}; HttpOnly; SameSite=Strict; Path=/`,
      );
    if (!sessionLink && !authorized(request))
      return reply(
        response,
        401,
        "The desktop app is running. Open the desktop session link provided by Codex.\n",
        "text/plain; charset=utf-8",
      );
    if (
      request.method === "POST" &&
      request.headers.origin !== `http://${request.headers.host}`
    )
      return reply(response, 403, {
        error: "Input must come from the desktop viewer",
      });
    const staticFiles = {
      "/": ["index.html", "text/html; charset=utf-8"],
      "/viewer.mjs": ["viewer.mjs", "text/javascript; charset=utf-8"],
      "/viewer.css": ["viewer.css", "text/css; charset=utf-8"],
    };
    if (request.method === "GET" && staticFiles[url.pathname]) {
      const [filename, type] = staticFiles[url.pathname];
      response.setHeader(
        "Content-Security-Policy",
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
      );
      response.setHeader("Referrer-Policy", "no-referrer");
      return reply(
        response,
        200,
        readFileSync(path.join(viewer, filename)),
        type,
      );
    }
    if (request.method === "GET" && url.pathname === "/frame.jpg") {
      const captured = await frame();
      response.setHeader("X-CytoForge-Window", String(captured.windowId));
      return reply(response, 200, captured.bytes, "image/jpeg");
    }
    if (request.method === "GET" && url.pathname === "/state")
      return reply(response, 200, {
        width: 1540,
        height: 990,
        packaged: true,
        windows: await desktopWindows(),
        selectedWindowId,
        fileChooser: pendingChooser?.id ?? null,
        downloads: await downloads(),
      });
    if (request.method === "POST" && url.pathname === "/window") {
      if (request.headers["content-type"] !== "application/json")
        return reply(response, 415, { error: "JSON input is required" });
      const value = JSON.parse((await body(request, 256)).toString("utf8"));
      if (
        !value ||
        Object.keys(value).length !== 1 ||
        !Number.isSafeInteger(value.id)
      )
        return reply(response, 400, {
          error: "Choose an existing desktop window",
        });
      const operation = inputQueue.then(() => selectWindow(value.id));
      inputQueue = operation.catch(() => {});
      await operation;
      return reply(response, 200, { selectedWindowId });
    }
    if (request.method === "POST" && url.pathname === "/input") {
      if (request.headers["content-type"] !== "application/json")
        return reply(response, 415, { error: "JSON input is required" });
      const values = JSON.parse((await body(request, 16384)).toString("utf8"));
      if (!Array.isArray(values) || !values.length || values.length > 64)
        return reply(response, 400, {
          error: "Send between one and 64 input events",
        });
      await applyInputs(values.map(validateInput));
      return reply(response, 200, { delivered: values.length });
    }
    if (request.method === "POST" && url.pathname === "/upload") {
      const chooser = pendingChooser;
      if (!chooser || url.searchParams.get("chooser") !== chooser.id)
        return reply(response, 409, {
          error: "Open an import dialog in the desktop app first",
        });
      const name = url.searchParams.get("name");
      if (
        !name ||
        name.length > 240 ||
        path.basename(name) !== name ||
        /[\x00-\x1f\\/]/.test(name) ||
        !/\.(fcs|csv|cytoforge|json|xml|wsp)$/i.test(name)
      )
        return reply(response, 400, {
          error: "Choose a supported cytometry, annotation, or project file",
        });
      const bytes = await body(request, 128 * 1024 * 1024);
      const directory = path.join(
        runtime,
        "imports",
        chooser.id,
        randomBytes(8).toString("hex"),
      );
      mkdirSync(directory, { recursive: true });
      const filename = path.join(directory, name);
      writeFileSync(filename, bytes, { mode: 0o600, flag: "wx" });
      chooser.files.push(filename);
      if (url.searchParams.get("last") === "1") {
        await chooser.dialog.setFiles(chooser.files);
        if (pendingChooser === chooser) pendingChooser = null;
      }
      return reply(response, 200, { uploaded: name });
    }
    if (
      request.method === "GET" &&
      /^\/downloads\/[a-f0-9]{24}$/.test(url.pathname)
    ) {
      const id = url.pathname.split("/").at(-1);
      const item = await application.evaluate(
        (_electron, identifier) =>
          globalThis.cytoforgePreviewDownloads.get(identifier),
        id,
      );
      if (
        !item ||
        (item.state !== "completed" &&
          !(
            item.nativePdf &&
            existsSync(path.join(runtime, "exports", item.filename))
          ))
      )
        return reply(response, 404, { error: "Download is not ready" });
      return streamFile(
        response,
        path.join(runtime, "exports", item.filename),
        item.name,
      );
    }
    return reply(response, 404, { error: "Not found" });
  } catch (error) {
    if (!response.headersSent) reply(response, 400, { error: error.message });
    else response.destroy();
  }
});
server.requestTimeout = 120000;
server.headersTimeout = 10000;
server.maxConnections = 32;
async function shutdown(code = 0) {
  if (stopping) return;
  stopping = true;
  server.closeAllConnections();
  server.close();
  try {
    await application?.close();
  } catch {
    application?.process().kill("SIGTERM");
  }
  if (sessionReport)
    writeFileSync(
      sessionPath,
      JSON.stringify(
        {
          ...sessionReport,
          status: "stopped",
          stopped_at: new Date().toISOString(),
        },
        null,
        2,
      ),
      { mode: 0o600 },
    );
  process.exit(code);
}
process.on("SIGINT", () => void shutdown());
process.on("SIGTERM", () => void shutdown());
try {
  const hasher = createHash("sha256");
  for await (const bytes of createReadStream(binary)) hasher.update(bytes);
  const binarySha256 = hasher.digest("hex");
  application = await electron.launch({
    executablePath: binary,
    chromiumSandbox: !noSandbox,
    args,
    cwd: root,
    env: {
      ...process.env,
      APPIMAGE_EXTRACT_AND_RUN: "1",
      CYTOFORGE_HOME: runtime,
      CYTOFORGE_HEADLESS_TEST: "1",
    },
    timeout: 60000,
  });
  mainWindow = await application.firstWindow({ timeout: 60000 });
  window = mainWindow;
  await mainWindow.waitForLoadState("domcontentloaded");
  application.on(
    "window",
    (page) =>
      void registerWindow(page).catch((error) =>
        console.error("Desktop window could not be shown:", error.message),
      ),
  );
  await registerWindow(mainWindow);
  for (const page of application.windows())
    if (page !== mainWindow) await registerWindow(page);
  // File transfer for the server preview only; local app downloads use native dialogs.
  await application.evaluate(
    ({ session, dialog }, { directory, prefix }) => {
      let sequence = 0;
      globalThis.cytoforgePreviewDownloads = new Map();
      dialog.showSaveDialog = async (_window, options) => {
        const id = `${prefix}${(++sequence).toString(16).padStart(8, "0")}`;
        const name = options.defaultPath.split(/[/\\]/).at(-1);
        const filename = `${id}-${name}`;
        globalThis.cytoforgePreviewDownloads.set(id, {
          id,
          name,
          filename,
          nativePdf: true,
          state: "progressing",
        });
        return { canceled: false, filePath: `${directory}/${filename}` };
      };
      session.defaultSession.on("will-download", (_event, item) => {
        const id = `${prefix}${(++sequence).toString(16).padStart(8, "0")}`;
        const name = item.getFilename().split(/[/\\]/).at(-1);
        const filename = `${id}-${name}`;
        const record = { id, name, filename, state: "progressing" };
        globalThis.cytoforgePreviewDownloads.set(id, record);
        item.setSavePath(`${directory}/${filename}`);
        item.on("done", (_event, state) => {
          record.state = state;
        });
      });
    },
    {
      directory: path.join(runtime, "exports"),
      prefix: randomBytes(8).toString("hex"),
    },
  );
  const demo = window.getByRole("button", { name: /Explore the PBMC demo/ });
  await demo
    .or(window.getByRole("button", { name: "Discovery", exact: true }))
    .waitFor({ timeout: 60000 });
  if (await demo.isVisible()) await demo.click();
  await window
    .locator('canvas[data-ready="true"]')
    .first()
    .waitFor({ timeout: 60000 });
  const evidence = await application.evaluate(({ app, BrowserWindow }) => ({
    packaged: app.isPackaged,
    electron: process.versions.electron,
    desktop_pid: process.pid,
    window_count: BrowserWindow.getAllWindows().length,
    sandbox_exception: app.commandLine.hasSwitch("no-sandbox"),
    renderer_context_isolation:
      BrowserWindow.getAllWindows()[0].webContents.getLastWebPreferences()
        .contextIsolation,
    renderer_node_integration:
      BrowserWindow.getAllWindows()[0].webContents.getLastWebPreferences()
        .nodeIntegration,
    private_engine_pid: process
      ._getActiveHandles()
      .find((handle) => handle.spawnargs?.includes("--parent-pid"))?.pid,
  }));
  if (
    !evidence.packaged ||
    evidence.sandbox_exception !== noSandbox ||
    !evidence.renderer_context_isolation ||
    evidence.renderer_node_integration ||
    !evidence.private_engine_pid
  )
    throw new Error(
      "Desktop process isolation or packaged-engine checks failed",
    );
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, host, resolve);
  });
  sessionReport = {
    status: "running",
    started_at: new Date().toISOString(),
    host,
    port,
    binary: path.basename(binary),
    binary_path: path.relative(root, binary),
    binary_sha256: binarySha256,
    preview_name: name,
    runtime: path.relative(root, runtime),
    ...evidence,
    url: `http://${publicHost}:${port}/?session=${token}`,
  };
  writeFileSync(sessionPath, JSON.stringify(sessionReport, null, 2), {
    mode: 0o600,
  });
  console.log(
    `Packaged CytoForge desktop preview listening on ${host}:${port}. Session link: ${path.relative(root, sessionPath)}`,
  );
  mainWindow.on("close", () => {
    if (!stopping) void shutdown(1);
  });
  application.on("close", () => {
    if (!stopping) void shutdown(1);
  });
} catch (error) {
  console.error(`Desktop preview could not start: ${error.message}`);
  await shutdown(1);
}
