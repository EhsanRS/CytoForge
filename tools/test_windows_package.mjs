import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";
import { createPackage, extractFile, getRawHeader } from "@electron/asar";
import { verifyDesktopArchive } from "./windows/package_integrity.mjs";

const temp = path.join(process.cwd(), ".tmp");
await mkdir(temp, { recursive: true });

async function scratch(fn) {
  const directory = await mkdtemp(path.join(temp, "windows-package-test-"));
  try {
    const root = path.join(directory, "source with spaces");
    await mkdir(path.join(root, "desktop/assets"), { recursive: true });
    await writeFile(path.join(root, "desktop/main.cjs"), "desktop entry point");
    await writeFile(path.join(root, "desktop/assets/icon.png"), "icon bytes");
    const archive = path.join(directory, "app.asar");
    await createPackage(root, archive);
    await fn(root, archive);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
}

// Run the installed ASAR directory traversal with Windows path semantics,
// including on Linux, without changing the host platform or filesystem.
async function windowsReader(archive) {
  const filename = createRequire(import.meta.url).resolve(
    "@electron/asar/lib/filesystem.js",
  );
  const requireAsar = createRequire(filename);
  const exports = {};
  vm.runInNewContext(
    await readFile(filename, "utf8"),
    {
      exports,
      require: (name) => (name === "path" ? path.win32 : requireAsar(name)),
    },
    { filename },
  );
  const filesystem = new exports.Filesystem("C:\\CytoForge\\app.asar");
  const header = getRawHeader(archive);
  filesystem.setHeader(header.header, header.headerSize);
  return (_, relative) => {
    filesystem.getFile(relative);
    return extractFile(archive, relative.split(path.win32.sep).join(path.sep));
  };
}

test("desktop ASAR verification includes icons and nested files on the host", async () =>
  scratch(async (root, archive) => {
    assert.equal(await verifyDesktopArchive(root, archive), 2);
  }));

test("Windows ASAR verification uses native separators for nested assets", async () =>
  scratch(async (root, archive) => {
    const readArchiveFile = await windowsReader(archive);
    assert.throws(
      () => readArchiveFile(archive, "desktop/assets/icon.png"),
      /was not found in this archive/,
    );
    assert.equal(
      await verifyDesktopArchive(root, archive, {
        archivePaths: path.win32,
        readArchiveFile,
      }),
      2,
    );
  }));

test("Windows ASAR verification still rejects missing and altered desktop files", async () =>
  scratch(async (root, archive) => {
    const options = {
      archivePaths: path.win32,
      readArchiveFile: await windowsReader(archive),
    };
    await writeFile(path.join(root, "desktop/assets/icon.png"), "altered");
    await assert.rejects(
      verifyDesktopArchive(root, archive, options),
      /Packaged desktop source differs/,
    );
    await writeFile(path.join(root, "desktop/assets/icon.png"), "icon bytes");
    await writeFile(path.join(root, "desktop/assets/missing.png"), "missing");
    await assert.rejects(
      verifyDesktopArchive(root, archive, options),
      /was not found in this archive/,
    );
  }));
