"""Share verified identical engine build files before extracting a retained AppImage."""

import argparse
import json
import os
import shutil
import uuid
from pathlib import Path

from compact_desktop_candidate import ROOT, digest, inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--proof", required=True, type=Path)
    args = parser.parse_args()
    if args.candidate not in {
        "spectral-autospill",
        "autospill-parents",
        "multiaf",
        "phenograph",
        "population-snapshots",
    }:
        parser.error("This operation is scoped to the selected desktop build copies")
    candidate = ROOT / "artifacts/candidates" / args.candidate
    engine = candidate / "engine/cytoforge-engine"
    unpacked = candidate / "desktop/linux-unpacked"
    embedded = unpacked / "resources/engine"
    image = candidate / "desktop/CytoForge-0.1.0.AppImage"
    proof_path = (ROOT / args.proof).resolve()
    assert proof_path.is_relative_to(ROOT)
    for directory in (candidate, engine, unpacked, embedded):
        assert directory.is_dir() and not directory.is_symlink()
        assert directory.resolve().is_relative_to(candidate.resolve())
    assert candidate.resolve().is_relative_to(ROOT)
    assert not image.is_symlink()
    proof = json.loads(proof_path.read_text())
    assert proof["status"] == "passed"
    assert (ROOT / proof["engine"]).resolve() == (engine / "cytoforge-engine").resolve()
    assert digest(engine / "cytoforge-engine") == proof["engine_sha256"]
    image_digest = digest(image) if image.is_file() else None
    for process in Path("/proc").iterdir():
        if not process.name.isdecimal():
            continue
        try:
            executable = (process / "exe").resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if executable.is_relative_to(engine) or executable.is_relative_to(unpacked):
            raise RuntimeError("Selected build copies are running; retain their existing files")
    before = inventory(engine)
    assert before == inventory(embedded)
    free_before = shutil.disk_usage(ROOT).free
    linked, logical_bytes = 0, 0
    for relative, record in before.items():
        if "link" in record:
            continue
        source, destination = engine / relative, embedded / relative
        left, right = source.stat(), destination.stat()
        if (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino):
            continue
        if left.st_mode != right.st_mode:
            continue
        temporary = destination.with_name(destination.name + ".deduplicate-" + uuid.uuid4().hex)
        try:
            os.link(source, temporary)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        linked += 1
        logical_bytes += record["bytes"]
    assert inventory(engine) == before == inventory(embedded)
    assert (digest(image) if image.is_file() else None) == image_digest
    record = dict(
        status="passed",
        candidate=args.candidate,
        linked_files=linked,
        identical_file_bytes=logical_bytes,
        free_before_bytes=free_before,
        free_after_bytes=shutil.disk_usage(ROOT).free,
        inventory_unchanged=True,
        appimage_unchanged=True,
        appimage_sha256=image_digest,
        scope=(
            "Only byte-verified identical generated engine copies; "
            "acquired data, profiles and AppImages retained"
        ),
    )
    (ROOT / f"artifacts/{args.candidate}-build-deduplication.json").write_text(
        json.dumps(record, indent=2) + "\n"
    )
    print(f"Shared {linked} identical engine files; retained AppImage unchanged")


if __name__ == "__main__":
    main()
