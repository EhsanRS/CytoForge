"""Export application source and published/generated test fixtures for Windows trials."""

import argparse
import hashlib
import json
import re
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDERS = ("backend", "frontend/src", "desktop", "tests", "tools", "docs", "licenses", ".github")
TOP_LEVEL = (
    ".gitignore",
    ".gitattributes",
    ".python-version",
    "README.md",
    "pyproject.toml",
    "uv.lock",
    "package.json",
    "package-lock.json",
    "start.sh",
    "start.ps1",
    "Start-CytoForge.cmd",
    "playwright.config.ts",
    "frontend/package.json",
    "frontend/tsconfig.json",
    "frontend/tsconfig.app.json",
    "frontend/tsconfig.node.json",
    "frontend/vite.config.ts",
    "frontend/index.html",
    "docs/WINDOWS.md",
)
SECRET_PATTERNS = (
    rb"https://hooks\.slack\.com/services/[A-Za-z0-9/]+",
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    rb"\b(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}",
    rb"\bAKIA[A-Z0-9]{16}\b",
)


def digest_bytes(data):
    return hashlib.sha256(data).hexdigest()


def source_files():
    folders = [name for name in FOLDERS if (ROOT / name).is_dir()]
    result = subprocess.run(
        ["rg", "--files", "--hidden", *folders],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    files = set(result.stdout.splitlines())
    files.update(name for name in TOP_LEVEL if (ROOT / name).is_file())
    selected = []
    for relative in sorted(files):
        path = ROOT / relative
        parts = path.relative_to(ROOT).parts
        if any(
            p in {"__pycache__", ".git", ".cache", ".tmp", ".venv", "node_modules"} for p in parts
        ):
            continue
        if path.suffix in {".pyc", ".tsbuildinfo", ".pem", ".key", ".pfx", ".p12"}:
            continue
        if path.name.startswith(".env"):
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT):
            raise ValueError(f"Source export cannot follow external links: {relative}")
        data = path.read_bytes()
        if len(data) > 50 * 1024 * 1024:
            raise ValueError(f"Large source file needs explicit review: {relative}")
        if any(re.search(pattern, data) for pattern in SECRET_PATTERNS):
            raise ValueError(f"Credential pattern found; export stopped: {relative}")
        selected.append((relative, data, path.stat().st_mode))
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "artifacts/CytoForge-Windows-x64-trial.zip"
    )
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "artifacts") or output.exists():
        parser.error("Choose a new archive under this checkout's artifacts directory")
    files = source_files()
    names = {name for name, _, _ in files}
    assert {
        "Start-CytoForge.cmd",
        "tools/windows/run.mjs",
        "tools/windows/toolchain.json",
        ".github/workflows/windows-release.yml",
    } <= names
    assert not any(
        name.startswith(("data/", "artifacts/", ".cache/", ".tmp/", ".codex/", ".aws/", ".agents/"))
        for name in names
    )
    manifest = {name: digest_bytes(data) for name, data, _ in files}
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, data, mode in files:
            entry = zipfile.ZipInfo(f"CytoForge/{name}")
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = (mode & 0xFFFF) << 16
            archive.writestr(entry, data)
        archive.writestr("CytoForge/SOURCE_SHA256.json", json.dumps(manifest, indent=2) + "\n")
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == len(files) + 1
        for name, expected in manifest.items():
            assert digest_bytes(archive.read(f"CytoForge/{name}")) == expected, name
    record = dict(
        status="passed",
        archive=str(output.relative_to(ROOT)),
        sha256=digest_bytes(output.read_bytes()),
        bytes=output.stat().st_size,
        source_files=len(files),
        source_sha256=manifest,
        scope="Windows x64 source trial; first launch downloads locked local dependencies",
        packaged_windows_exe=False,
        native_windows_validation_executed=False,
        credential_pattern_check_passed=True,
        original_user_datasets_and_profiles_excluded=True,
    )
    output.with_suffix(".validation.json").write_text(json.dumps(record, indent=2) + "\n")
    output.with_suffix(".sha256").write_text(f"{record['sha256']}  {output.name}\n")
    print(
        json.dumps(
            {
                k: record[k]
                for k in (
                    "status",
                    "archive",
                    "bytes",
                    "source_files",
                    "native_windows_validation_executed",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
