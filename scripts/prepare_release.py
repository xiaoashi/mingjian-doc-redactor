"""Create runtime resources, pinned requirements and component/license inventory."""
import importlib.metadata as metadata
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    from PIL import Image, ImageDraw, ImageFont
    image = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((40, 40, 984, 984), radius=205, fill="#236d63")
    font = ImageFont.truetype(str(ROOT / "src/mingjian/resources/fonts/NotoSansSC-Regular.ttf"), 640)
    draw.text((512, 490), "明", font=font, fill="#f6f7ed", anchor="mm", stroke_width=0)
    image.save(ROOT / "packaging" / "mingjian.png")
    image.save(ROOT / "packaging" / "mingjian.icns")
    inventory = []
    dest = ROOT / "licenses" / "components"
    dest.mkdir(parents=True, exist_ok=True)
    for dist in sorted(metadata.distributions(), key=lambda d: d.metadata["Name"].lower()):
        name = dist.metadata["Name"]
        if name.lower() == "mingjian":
            continue
        license_files = []
        for file in dist.files or []:
            if any(part.lower() in ("licenses", "license", "copying") for part in file.parts) or file.name.lower().startswith(("license", "copying", "notice")):
                source = Path(dist.locate_file(file))
                if source.is_file():
                    target = dest / name / str(file).replace("../", "")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                    license_files.append(str(target.relative_to(ROOT / "licenses")))
        inventory.append({"name": name, "version": dist.version, "license_expression": dist.metadata.get("License-Expression"),
            "license": dist.metadata.get("License"), "sources": dist.metadata.get_all("Project-URL", []),
            "license_files": license_files,
            "role": "build/test only" if name.lower() in ("pytest", "pytest-qt", "ruff", "pyinstaller", "pyinstaller-hooks-contrib", "altgraph", "macholib", "iniconfig", "pluggy", "pygments") else "runtime or transitive"})
    (ROOT / "licenses" / "dependency-inventory.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Inventoried {len(inventory)} packages; copied supplied license files.")


if __name__ == "__main__":
    main()
