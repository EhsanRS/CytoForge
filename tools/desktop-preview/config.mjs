import { existsSync, lstatSync, realpathSync, statSync } from "node:fs";
import path from "node:path";

function inside(root, candidate) {
  const relative = path.relative(root, candidate);
  return (
    relative !== "" &&
    !relative.startsWith(`..${path.sep}`) &&
    relative !== ".." &&
    !path.isAbsolute(relative)
  );
}

function writableLocation(root, candidate) {
  if (!inside(root, candidate))
    throw new Error("Preview files must stay inside the workspace");
  let current = root;
  for (const part of path.relative(root, candidate).split(path.sep)) {
    current = path.join(current, part);
    try {
      if (lstatSync(current).isSymbolicLink())
        throw new Error(
          "Preview profiles and session files cannot use symbolic links",
        );
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
  }
  return candidate;
}

export function previewConfiguration(workspace, environment = process.env) {
  const root = realpathSync(workspace);
  const name = environment.CYTOFORGE_PREVIEW_NAME || "";
  if (name && !/^[a-z][a-z0-9-]{0,59}$/.test(name))
    throw new Error(
      "Use a short lowercase name for the isolated desktop preview",
    );
  const selectedBinary = environment.CYTOFORGE_PREVIEW_BINARY;
  if (selectedBinary && !name)
    throw new Error(
      "Set CYTOFORGE_PREVIEW_NAME when selecting a candidate AppImage",
    );
  const chosen = path.resolve(
    root,
    selectedBinary || "artifacts/installers/CytoForge-0.1.0.AppImage",
  );
  if (!inside(root, chosen))
    throw new Error("Choose a desktop AppImage inside this workspace");
  if (!existsSync(chosen))
    throw new Error("Build the desktop AppImage before starting its preview");
  const binary = realpathSync(chosen);
  if (
    !inside(root, binary) ||
    !statSync(binary).isFile() ||
    !binary.endsWith(".AppImage")
  )
    throw new Error(
      "Choose an AppImage file that resolves inside this workspace",
    );
  const port = Number(environment.CYTOFORGE_PREVIEW_PORT || 8001);
  if (!Number.isInteger(port) || port < 1 || port > 65535)
    throw new Error("Invalid preview port");
  const host = environment.CYTOFORGE_PREVIEW_HOST || "0.0.0.0";
  const publicHost = environment.CYTOFORGE_PREVIEW_PUBLIC_HOST || "127.0.0.1";
  let publicURL;
  try {
    publicURL = new URL(`http://${publicHost}:${port}/`);
  } catch {
    throw new Error("Use a hostname or IP address for the preview public host");
  }
  if (
    publicURL.username ||
    publicURL.password ||
    publicURL.pathname !== "/" ||
    publicURL.search ||
    publicURL.hash ||
    Number(publicURL.port || 80) !== port
  )
    throw new Error("Use a hostname or IP address for the preview public host");
  return {
    root,
    name,
    binary,
    port,
    host,
    publicHost,
    runtime: writableLocation(
      root,
      path.join(
        root,
        name ? `.tmp/desktop-preview/${name}` : ".tmp/desktop-progress",
      ),
    ),
    sessionPath: writableLocation(
      root,
      path.join(
        root,
        "artifacts",
        name
          ? `desktop-preview-${name}-session.json`
          : "desktop-preview-session.json",
      ),
    ),
    tokenPath: writableLocation(
      root,
      path.join(
        root,
        ".config",
        name ? `desktop-preview/${name}-token` : "desktop-preview-token",
      ),
    ),
    cookieName: `cytoforge_desktop_preview${name ? `_${name}` : ""}`,
  };
}
