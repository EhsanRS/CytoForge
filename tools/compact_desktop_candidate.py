"""Remove reproducible candidate build copies only after comparing every byte with its AppImage."""

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def inventory(directory):
    records = {}
    for path in directory.rglob("*"):
        relative = path.relative_to(directory).as_posix()
        if path.is_symlink():
            if not path.resolve().is_relative_to(directory.resolve()):
                raise ValueError("Build links must remain self-contained")
            records[relative] = {"link": str(path.readlink())}
        elif path.is_file():
            records[relative] = {"sha256": digest(path), "bytes": path.stat().st_size}
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidate",
        choices=[
            "report-templates",
            "publication-controls",
            "graph-typography",
            "table-geometry",
            "custom-plates",
            "graph-gates",
            "graph-fonts",
            "backgate-reports",
            "fcs-packed",
            "autospread",
            "spectral-autospill",
            "autospill-parents",
            "multiaf",
            "phenograph",
            "population-snapshots",
        ],
        required=True,
    )
    parser.add_argument("--proof", type=Path, required=True)
    args = parser.parse_args()
    candidate = ROOT / "artifacts/candidates" / args.candidate
    desktop = "desktop-final" if args.candidate == "graph-fonts" else "desktop"
    binary = candidate / desktop / "CytoForge-0.1.0.AppImage"
    if candidate.is_symlink() or not candidate.resolve().is_relative_to(ROOT):
        parser.error("Candidate build copies must resolve inside this checkout")
    if binary.is_symlink() or not binary.resolve().is_relative_to(candidate.resolve()):
        parser.error("The retained AppImage must resolve inside its candidate directory")
    proof_path = (ROOT / args.proof).resolve()
    if not proof_path.is_relative_to(ROOT):
        parser.error("Validation evidence must stay inside this checkout")
    proof = json.loads(proof_path.read_text())
    assert proof["status"] == "passed" and digest(binary) == proof["appimage_sha256"]
    engine = candidate / "engine/cytoforge-engine"
    unpacked = candidate / desktop / "linux-unpacked"
    for directory in [engine, unpacked]:
        assert directory.is_dir() and not directory.is_symlink()
        assert directory.resolve().is_relative_to(candidate.resolve())
    # Inspect only executable paths matching the selected build copies; no command lines or signals.
    for process in Path("/proc").iterdir():
        if not process.name.isdecimal():
            continue
        try:
            executable = (process / "exe").resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if executable.is_relative_to(engine) or executable.is_relative_to(unpacked):
            raise RuntimeError(
                "The selected unpacked candidate is running; retain its build copies"
            )
    free_before = shutil.disk_usage(ROOT).free
    with tempfile.TemporaryDirectory(prefix="compact-candidate-", dir=ROOT / ".tmp") as owned:
        temporary = Path(owned)
        subprocess.run(
            [str(binary), "--appimage-extract"],
            cwd=temporary,
            stdout=subprocess.DEVNULL,
            check=True,
        )
        extracted = temporary / "squashfs-root"
        engine_files = inventory(engine)
        assert engine_files == inventory(extracted / "resources/engine")
        unpacked_files = inventory(unpacked)
        extracted_files = inventory(extracted)
        # AppImage packaging adds AppRun and desktop/icon entries beside the complete Electron tree.
        assert all(extracted_files.get(name) == record for name, record in unpacked_files.items())
        assert digest(binary) == proof["appimage_sha256"]
        manifest = ROOT / "artifacts" / f"{args.candidate}-compacted-build-manifest.json"
        record = {
            "status": "verified_before_compaction",
            "checked_at": datetime.now(UTC).isoformat(),
            "candidate": args.candidate,
            "appimage": str(binary.relative_to(ROOT)),
            "appimage_sha256": proof["appimage_sha256"],
            "engine": engine_files,
            "unpacked": unpacked_files,
            "scope": "Reproducible build copies only; executable and validation evidence retained",
            "recovery": "Extract the retained AppImage; restore engine from resources/engine and "
            "the listed Electron files from squashfs-root",
        }
        manifest.write_text(json.dumps(record, indent=2) + "\n")
        shutil.rmtree(engine)
        shutil.rmtree(unpacked)
    assert digest(binary) == proof["appimage_sha256"]
    record.update(
        status="compacted",
        free_bytes_before=free_before,
        free_bytes_after=shutil.disk_usage(ROOT).free,
    )
    manifest.write_text(json.dumps(record, indent=2) + "\n")
    print(
        json.dumps(
            {
                "candidate": args.candidate,
                "status": "compacted",
                "engine_files_verified": len(engine_files),
                "unpacked_files_verified": len(unpacked_files),
                "free_bytes_after": record["free_bytes_after"],
            }
        )
    )


if __name__ == "__main__":
    main()
