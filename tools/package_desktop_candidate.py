"""Package a verified Linux engine and current desktop source in an isolated candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def owned_path(value):
    path = (ROOT / value).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError("Candidate inputs, outputs and caches must stay inside this checkout")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", required=True, type=Path)
    parser.add_argument("--engine-proof", required=True, type=Path)
    parser.add_argument("--source-proof", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    packaging_mode = parser.add_mutually_exclusive_group()
    packaging_mode.add_argument(
        "--reuse-unpacked",
        action="store_true",
        help="Build an AppImage from byte-verified unpacked files",
    )
    packaging_mode.add_argument(
        "--directory-only",
        action="store_true",
        help="Build unpacked files first so identical engine copies can share storage",
    )
    args = parser.parse_args()
    engine, engine_proof, source_proof, output = (
        owned_path(value)
        for value in (args.engine, args.engine_proof, args.source_proof, args.output_dir)
    )
    candidates = ROOT / "artifacts/candidates"
    if output == candidates or not output.is_relative_to(candidates):
        parser.error("Choose a new output directory below artifacts/candidates")
    if output.exists() and any(output.iterdir()) and not args.reuse_unpacked:
        parser.error("Candidate output already contains files; choose another directory")
    reused = output / "linux-unpacked"
    if args.reuse_unpacked:
        from compact_desktop_candidate import inventory

        if not reused.is_dir() or (output / "CytoForge-0.1.0.AppImage").exists():
            parser.error("Reuse requires an unpacked build and no existing AppImage")
        assert inventory(engine.parent) == inventory(reused / "resources/engine")
        assert inventory(ROOT / "frontend/dist") == inventory(reused / "resources/frontend")
        subprocess.run(
            ["node", "--input-type=module", "-", str(ROOT), str(reused / "resources/app.asar")],
            input="""
import { extractFile } from '@electron/asar';
import { readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
const [root, archive] = process.argv.slice(2);
function visit(directory) {
  for (const entry of readdirSync(path.join(root, directory), { withFileTypes: true })) {
    const relative = directory + '/' + entry.name;
    if (entry.isDirectory()) visit(relative);
    else if (!extractFile(archive, relative).equals(readFileSync(path.join(root, relative))))
      throw new Error('Unpacked desktop source changed: ' + relative);
  }
}
visit('desktop');
""",
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    if not engine.is_file() or not (engine.parent / "_internal").is_dir():
        parser.error("Choose a complete PyInstaller onedir engine")
    frozen = json.loads(engine_proof.read_text())
    checked = json.loads(source_proof.read_text())
    expected = digest(engine)
    if frozen.get("status") != "passed" or frozen.get("engine_sha256") != expected:
        parser.error("The frozen-worker proof must match this candidate engine")
    if checked.get("full_python_regression", {}).get("status") != "passed":
        parser.error("Complete the source regression checkpoint before packaging")
    for check in ("typecheck", "ui_build", "ruff", "prettier"):
        if checked.get(check, {}).get("status") != "passed":
            parser.error(f"The source checkpoint is missing a passed {check}")
    for name, sha256 in checked["current_source_sha256"].items():
        if digest(owned_path(Path(name))) != sha256:
            parser.error(f"Source changed after its checkpoint: {name}")
    for name, sha256 in frozen["source_sha256"].items():
        if digest(owned_path(Path(name))) != sha256:
            parser.error(f"Frozen-worker source changed after its checkpoint: {name}")
    electron = ROOT / "node_modules/electron/dist"
    if not (electron / "electron").is_file():
        parser.error("Install the cached Linux Electron distribution first")
    configuration = json.loads((ROOT / "package.json").read_text())["build"]
    configuration["directories"]["output"] = str(output)
    configuration["electronDist"] = str(electron)
    configuration["npmRebuild"] = False
    configuration["extraResources"] = [
        {"from": "frontend/dist", "to": "frontend"},
        {"from": str(engine.parent), "to": "engine", "filter": ["**/*"]},
    ]
    environment = os.environ.copy()
    environment.update(
        ELECTRON_CACHE=str(ROOT / ".cache/electron"),
        ELECTRON_BUILDER_CACHE=str(ROOT / ".cache/electron-builder"),
        npm_config_cache=str(ROOT / ".cache/npm"),
        TMPDIR=str(ROOT / ".tmp"),
        TEMP=str(ROOT / ".tmp"),
        TMP=str(ROOT / ".tmp"),
    )
    with tempfile.TemporaryDirectory(prefix="package-candidate-", dir=ROOT / ".tmp") as owned:
        config = Path(owned) / "electron-builder.json"
        config.write_text(json.dumps(configuration, indent=2) + "\n")
        subprocess.run(
            [
                "node",
                str(ROOT / "tools/desktop_candidate_builder.cjs"),
                "--config",
                str(config),
                "--linux",
                *(["dir"] if args.directory_only else ["AppImage"]),
                *(
                    ["--prepackaged", str(reused)]
                    if args.reuse_unpacked
                    else []
                    if args.directory_only
                    else ["dir"]
                ),
                "--publish",
                "never",
            ],
            cwd=ROOT,
            env=environment,
            check=True,
        )
    print(f"Candidate packaged: {output.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
