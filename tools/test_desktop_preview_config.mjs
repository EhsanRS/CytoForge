import assert from "node:assert/strict";
import { after, test } from "node:test";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import path from "node:path";
import { previewConfiguration } from "./desktop-preview/config.mjs";

const owned = mkdtempSync(path.join(process.cwd(), ".tmp/preview-config-"));
const root = path.join(owned, "workspace");
mkdirSync(path.join(root, "artifacts/installers"), { recursive: true });
mkdirSync(path.join(root, "artifacts/candidates/latest"), { recursive: true });
const released = path.join(
  root,
  "artifacts/installers/CytoForge-0.1.0.AppImage",
);
const candidate = path.join(
  root,
  "artifacts/candidates/latest/CytoForge-0.1.0.AppImage",
);
writeFileSync(released, "Released image");
writeFileSync(candidate, "Candidate image");
const selection = {
  CYTOFORGE_PREVIEW_NAME: "latest",
  CYTOFORGE_PREVIEW_BINARY:
    "artifacts/candidates/latest/CytoForge-0.1.0.AppImage",
};
after(() => rmSync(owned, { recursive: true, force: true }));

test("default preview retains its existing workspace, cookie and session paths", () => {
  const config = previewConfiguration(root, {});
  assert.equal(config.binary, released);
  assert.equal(config.runtime, path.join(root, ".tmp/desktop-progress"));
  assert.equal(
    config.tokenPath,
    path.join(root, ".config/desktop-preview-token"),
  );
  assert.equal(
    config.sessionPath,
    path.join(root, "artifacts/desktop-preview-session.json"),
  );
  assert.equal(config.cookieName, "cytoforge_desktop_preview");
  assert.equal(config.host, "0.0.0.0");
  assert.equal(config.port, 8001);
});

test("candidate selection isolates data, keys, cookies and session reports without writing", () => {
  const config = previewConfiguration(root, selection);
  const legacy = previewConfiguration(root, {});
  assert.equal(config.binary, candidate);
  assert.equal(path.basename(config.binary), path.basename(legacy.binary));
  for (const key of ["runtime", "tokenPath", "sessionPath", "cookieName"])
    assert.notEqual(config[key], legacy[key]);
  assert.equal(config.runtime, path.join(root, ".tmp/desktop-preview/latest"));
  assert.equal(
    config.sessionPath,
    path.join(root, "artifacts/desktop-preview-latest-session.json"),
  );
  assert(!existsSync(config.runtime));
  assert(!existsSync(config.sessionPath));
  assert(!existsSync(config.tokenPath));
  assert.equal(readFileSync(released, "utf8"), "Released image");
});

test("a custom binary cannot reuse the original progress profile", () => {
  assert.throws(
    () => previewConfiguration(root, { CYTOFORGE_PREVIEW_BINARY: candidate }),
    /CYTOFORGE_PREVIEW_NAME/,
  );
});

for (const name of ["../old", "UPPER", "a b", "_hidden", "a".repeat(61)])
  test(`reject invalid preview name ${JSON.stringify(name)}`, () => {
    assert.throws(
      () =>
        previewConfiguration(root, {
          ...selection,
          CYTOFORGE_PREVIEW_NAME: name,
        }),
      /lowercase/,
    );
  });

for (const port of ["0", "65536", "1.5", "no-port"])
  test(`reject invalid listener port ${JSON.stringify(port)}`, () => {
    assert.throws(
      () =>
        previewConfiguration(root, {
          ...selection,
          CYTOFORGE_PREVIEW_PORT: port,
        }),
      /preview port/,
    );
  });

for (const host of [
  "example.com/path",
  "http://example.com",
  "user@example.com",
  "host:1234",
])
  test(`reject malformed public host ${JSON.stringify(host)}`, () => {
    assert.throws(
      () =>
        previewConfiguration(root, {
          ...selection,
          CYTOFORGE_PREVIEW_PUBLIC_HOST: host,
        }),
      /hostname or IP/,
    );
  });

test("explicit public IPv4 and bracketed IPv6 hosts retain the requested listener", () => {
  for (const host of ["192.0.2.10", "[::1]"]) {
    const config = previewConfiguration(root, {
      ...selection,
      CYTOFORGE_PREVIEW_PUBLIC_HOST: host,
      CYTOFORGE_PREVIEW_PORT: "8002",
    });
    assert.equal(config.publicHost, host);
    assert.equal(config.port, 8002);
    assert.equal(config.host, "0.0.0.0");
  }
});

test("external AppImages and links outside the workspace are rejected", () => {
  const external = path.join(owned, "external.AppImage");
  writeFileSync(external, "Outside the simulated workspace");
  assert.throws(
    () =>
      previewConfiguration(root, {
        ...selection,
        CYTOFORGE_PREVIEW_BINARY: external,
      }),
    /inside this workspace/,
  );
  const link = path.join(root, "external-link.AppImage");
  symlinkSync(external, link);
  assert.throws(
    () =>
      previewConfiguration(root, {
        ...selection,
        CYTOFORGE_PREVIEW_BINARY: link,
      }),
    /resolves inside/,
  );
});

test("links to an owned image retain its actual file identity", () => {
  const link = path.join(root, "owned-link.AppImage");
  symlinkSync(candidate, link);
  assert.equal(
    previewConfiguration(root, { ...selection, CYTOFORGE_PREVIEW_BINARY: link })
      .binary,
    candidate,
  );
});

test("a candidate profile cannot alias the original profile, even through a dangling link", () => {
  const parent = path.join(root, ".tmp/desktop-preview");
  mkdirSync(parent, { recursive: true });
  const target = path.join(root, ".tmp/desktop-progress");
  const link = path.join(parent, "alias");
  symlinkSync(target, link);
  assert.throws(
    () =>
      previewConfiguration(root, {
        ...selection,
        CYTOFORGE_PREVIEW_NAME: "alias",
      }),
    /symbolic links/,
  );
});
