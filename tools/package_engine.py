"""Build on each target OS/architecture; PyInstaller cannot cross-compile."""

import argparse
import os
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output-dir", type=Path, default=root / "artifacts/engine")
parser.add_argument("--build-dir", type=Path, default=root / ".cache/pyinstaller/build")
args = parser.parse_args()
output = (root / args.output_dir).resolve()
build = (root / args.build_dir).resolve()
if not output.is_relative_to(root) or not build.is_relative_to(root):
    parser.error("Build outputs and caches must stay inside this checkout")
os.environ["PYINSTALLER_CONFIG_DIR"] = str(root / ".cache/pyinstaller")
subprocess.run(
    [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        "cytoforge-engine",
        "--paths",
        str(root / "backend"),
        "--add-data",
        f"{root / 'backend/cytoforge/resources'}{os.pathsep}cytoforge/resources",
        "--add-data",
        f"{root / 'licenses'}{os.pathsep}licenses",
        "--additional-hooks-dir",
        str(root / "tools/pyinstaller-hooks"),
        "--collect-all",
        "flowutils",
        "--collect-all",
        "flowio",
        "--collect-all",
        "umap",
        "--collect-all",
        "flowsom",
        "--recursive-copy-metadata",
        "flowsom",
        "--recursive-copy-metadata",
        "umap-learn",
        "--copy-metadata",
        "scikit-learn",
        "--copy-metadata",
        "igraph",
        "--exclude-module",
        "pytest",
        "--exclude-module",
        "flowkit",
        "--exclude-module",
        "bokeh",
        "--exclude-module",
        "pyarrow",
        "--distpath",
        str(output),
        "--workpath",
        str(build),
        "--specpath",
        str(build.parent),
        str(root / "tools/engine_entry.py"),
    ],
    cwd=root,
    check=True,
)
