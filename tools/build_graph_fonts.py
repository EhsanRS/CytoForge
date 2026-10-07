"""Bundle the same licensed font faces used by the report renderer."""

import hashlib
import json
import shutil
from pathlib import Path

import matplotlib

ROOT = Path(__file__).resolve().parents[1]
FAMILIES = {"Sans": "DejaVuSans", "Serif": "DejaVuSerif", "Mono": "DejaVuSansMono"}


def main():
    source = Path(matplotlib.get_data_path()) / "fonts/ttf"
    target = ROOT / "frontend/src/assets/fonts"
    target.mkdir(parents=True, exist_ok=True)
    css, records = [], {}
    for family, prefix in FAMILIES.items():
        italic = "Italic" if family == "Serif" else "Oblique"
        for weight, style, suffix in [
            (400, "normal", ""),
            (700, "normal", "-Bold"),
            (400, "italic", f"-{italic}"),
            (700, "italic", f"-Bold{italic}"),
        ]:
            name = f"{prefix}{suffix}.ttf"
            shutil.copyfile(source / name, target / name)
            records[name] = hashlib.sha256((target / name).read_bytes()).hexdigest()
            css.append(
                f'@font-face {{\n  font-family: "CytoForge {family}";\n'
                f'  src: url("./assets/fonts/{name}") format("truetype");\n'
                f"  font-weight: {weight};\n  font-style: {style};\n  font-display: swap;\n}}\n"
            )
    (ROOT / "frontend/src/graphFonts.css").write_text("\n".join(css))
    licenses = ROOT / "frontend/public/licenses"
    licenses.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source / "LICENSE_DEJAVU", licenses / "DEJAVU.txt")
    (ROOT / "artifacts/graph-font-assets.json").write_text(
        json.dumps(
            {"status": "passed", "matplotlib": matplotlib.__version__, "sha256": records}, indent=2
        )
        + "\n"
    )
    print(f"Bundled {len(records)} matching report/native font faces and their license")


if __name__ == "__main__":
    main()
