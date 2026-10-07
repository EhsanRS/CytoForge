import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { extractFile } from "@electron/asar";

export async function verifyDesktopArchive(
  root,
  archive,
  { archivePaths = path, readArchiveFile = extractFile } = {},
) {
  let count = 0;
  async function visit(prefix = "") {
    for (const entry of await readdir(path.join(root, "desktop", prefix), {
      withFileTypes: true,
    })) {
      const relative = path.join(prefix, entry.name);
      if (entry.isDirectory()) await visit(relative);
      else if (entry.isFile()) {
        // ASAR traverses directories using the host's path separator.
        const archived = readArchiveFile(
          archive,
          archivePaths.join("desktop", relative),
        );
        if (
          !archived.equals(await readFile(path.join(root, "desktop", relative)))
        )
          throw new Error(`Packaged desktop source differs: ${relative}`);
        count++;
      } else throw new Error(`Unexpected desktop entry: ${relative}`);
    }
  }
  await visit();
  return count;
}
