if (location.search) history.replaceState(null, "", "/");
const canvas = document.getElementById("desktop");
const context = canvas.getContext("2d");
const status = document.getElementById("status");
const choose = document.getElementById("choose");
const files = document.getElementById("files");
const exports = document.getElementById("exports");
const windows = document.getElementById("windows");
let state = null,
  awaitingWindowId = null,
  lastStateAt = 0,
  inputs = [],
  sending = false,
  move = null,
  lastPointerDown = null,
  pointerClickCount = 1;
const pressedKeys = new Set();
function resize() {
  const bounds = canvas.parentElement.getBoundingClientRect();
  const scale = Math.min(
    bounds.width / canvas.width,
    bounds.height / canvas.height,
  );
  canvas.style.width = `${Math.floor(canvas.width * scale)}px`;
  canvas.style.height = `${Math.floor(canvas.height * scale)}px`;
}
new ResizeObserver(resize).observe(canvas.parentElement);
resize();
async function poll() {
  try {
    if (!document.hidden) {
      const response = await fetch("/frame.jpg", { cache: "no-store" });
      if (!response.ok)
        throw new Error("Reconnect using the desktop session link");
      const image = await createImageBitmap(await response.blob());
      const windowId = response.headers.get("X-CytoForge-Window");
      if (awaitingWindowId === null || windowId === String(awaitingWindowId)) {
        context.drawImage(image, 0, 0, canvas.width, canvas.height);
        canvas.dataset.frameNumber = String(
          Number(canvas.dataset.frameNumber || 0) + 1,
        );
        canvas.dataset.windowId = windowId || "";
        if (awaitingWindowId !== null) {
          awaitingWindowId = null;
          canvas.removeAttribute("aria-busy");
          windows.disabled = false;
        }
      }
      image.close();
      canvas.dataset.ready = "true";
      status.textContent = "Connected to the packaged desktop app";
      if (Date.now() - lastStateAt > 1000) {
        const response = await fetch("/state");
        if (!response.ok) throw new Error("The desktop session disconnected");
        state = await response.json();
        if (state.windows) {
          if (
            awaitingWindowId !== null &&
            !state.windows.some((item) => item.id === awaitingWindowId)
          )
            awaitingWindowId = state.selectedWindowId;
          const signature = JSON.stringify(state.windows);
          windows.hidden = false;
          if (windows.dataset.signature !== signature) {
            windows.replaceChildren(
              ...state.windows.map(
                (item) =>
                  new Option(
                    item.kind === "main" ? "Main workspace" : item.title,
                    String(item.id),
                  ),
              ),
            );
            windows.value = String(state.selectedWindowId);
            windows.dataset.signature = signature;
          }
        }
        choose.hidden = !state.fileChooser;
        const ready = state.downloads.filter(
          (item) => item.state === "completed",
        );
        const signature = JSON.stringify(ready);
        if (exports.dataset.signature !== signature) {
          exports.replaceChildren(
            new Option("Desktop exports", ""),
            ...ready.map((item) => new Option(item.name, item.id)),
          );
          exports.dataset.signature = signature;
        }
        lastStateAt = Date.now();
      }
    }
  } catch (error) {
    status.textContent = error.message;
  }
  setTimeout(poll, 250);
}
void poll();
windows.addEventListener("change", async () => {
  lastPointerDown = null;
  const requestedWindowId = Number(windows.value);
  windows.disabled = true;
  try {
    // Keyboard blur releases modifiers before changing the native input target.
    while (sending || inputs.length)
      await new Promise((resolve) => setTimeout(resolve, 25));
    awaitingWindowId = requestedWindowId;
    canvas.setAttribute("aria-busy", "true");
    const response = await fetch("/window", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: requestedWindowId }),
    });
    if (!response.ok) throw new Error((await response.json()).error);
    lastStateAt = 0;
  } catch (error) {
    status.textContent = error.message;
    awaitingWindowId = null;
    canvas.removeAttribute("aria-busy");
    windows.disabled = false;
  }
});
async function deliver() {
  if (sending || !inputs.length) return;
  sending = true;
  try {
    while (inputs.length) {
      const events = inputs.splice(0, 64);
      const response = await fetch("/input", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(events),
      });
      if (!response.ok)
        throw new Error(
          (await response.json()).error || "Desktop input failed",
        );
    }
  } catch (error) {
    status.textContent = error.message;
    inputs = [];
  } finally {
    sending = false;
  }
}
function enqueue(event) {
  if (awaitingWindowId !== null && event.type !== "keyUp") return;
  inputs.push(event);
  void deliver();
}
function point(event) {
  const bounds = canvas.getBoundingClientRect();
  return {
    x: Math.max(
      0,
      Math.min(
        1539,
        ((event.clientX - bounds.left) * canvas.width) / bounds.width,
      ),
    ),
    y: Math.max(
      0,
      Math.min(
        989,
        ((event.clientY - bounds.top) * canvas.height) / bounds.height,
      ),
    ),
  };
}
const button = (value) => ["left", "middle", "right"][value];
canvas.addEventListener("pointerdown", (event) => {
  if (!button(event.button)) return;
  event.preventDefault();
  canvas.focus();
  canvas.setPointerCapture(event.pointerId);
  const position = point(event);
  const now = performance.now();
  pointerClickCount =
    lastPointerDown &&
    lastPointerDown.button === event.button &&
    now - lastPointerDown.at < 450 &&
    Math.hypot(position.x - lastPointerDown.x, position.y - lastPointerDown.y) <
      6 &&
    lastPointerDown.count === 1
      ? 2
      : 1;
  lastPointerDown = {
    ...position,
    at: now,
    button: event.button,
    count: pointerClickCount,
  };
  enqueue({
    type: "down",
    ...position,
    button: button(event.button),
    clickCount: pointerClickCount,
  });
});
canvas.addEventListener("pointerup", (event) => {
  if (canvas.hasPointerCapture(event.pointerId))
    canvas.releasePointerCapture(event.pointerId);
  if (button(event.button))
    enqueue({
      type: "up",
      ...point(event),
      button: button(event.button),
      clickCount: pointerClickCount,
    });
});
canvas.addEventListener("pointermove", (event) => {
  move = point(event);
});
setInterval(() => {
  if (move) {
    enqueue({ type: "move", ...move });
    move = null;
  }
}, 40);
canvas.addEventListener("contextmenu", (event) => event.preventDefault());
canvas.addEventListener(
  "wheel",
  (event) => {
    event.preventDefault();
    enqueue({ type: "move", ...point(event) });
    const factor = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? 400 : 1;
    enqueue({
      type: "wheel",
      x: Math.max(-2400, Math.min(2400, event.deltaX * factor)),
      y: Math.max(-2400, Math.min(2400, event.deltaY * factor)),
    });
  },
  { passive: false },
);
canvas.addEventListener("keydown", (event) => {
  if (event.key === "F11") return;
  if (event.key.toLowerCase() === "v" && (event.ctrlKey || event.metaKey))
    return;
  event.preventDefault();
  if (event.key === "Dead" || event.key === "Unidentified") return;
  pressedKeys.add(event.key);
  enqueue({ type: "keyDown", key: event.key });
});
canvas.addEventListener("paste", (event) => {
  event.preventDefault();
  const text = event.clipboardData?.getData("text/plain");
  if (text?.length > 4096)
    status.textContent = "Paste up to 4096 characters at once.";
  else if (text) enqueue({ type: "text", text });
});
canvas.addEventListener("keyup", (event) => {
  event.preventDefault();
  if (pressedKeys.delete(event.key)) enqueue({ type: "keyUp", key: event.key });
});
canvas.addEventListener("blur", () => {
  for (const key of pressedKeys) enqueue({ type: "keyUp", key });
  pressedKeys.clear();
});
document.getElementById("fullscreen").addEventListener("click", () => {
  if (!document.fullscreenElement)
    void document.documentElement.requestFullscreen();
  else void document.exitFullscreen();
});
choose.addEventListener("click", () => files.click());
files.addEventListener("change", async () => {
  const selected = [...files.files];
  if (!selected.length || !state?.fileChooser) return;
  choose.disabled = true;
  try {
    for (let i = 0; i < selected.length; i++) {
      const file = selected[i];
      status.textContent = `Transferring ${file.name} to the desktop…`;
      const query = new URLSearchParams({
        chooser: state.fileChooser,
        name: file.name,
        last: i === selected.length - 1 ? "1" : "0",
      });
      const response = await fetch(`/upload?${query}`, {
        method: "POST",
        body: file,
      });
      if (!response.ok) throw new Error((await response.json()).error);
    }
    choose.hidden = true;
  } catch (error) {
    status.textContent = error.message;
  } finally {
    files.value = "";
    choose.disabled = false;
  }
});
exports.addEventListener("change", () => {
  if (exports.value) {
    const link = document.createElement("a");
    link.href = `/downloads/${exports.value}`;
    link.click();
    exports.value = "";
  }
});
