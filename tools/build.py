"""Build extension (Blender 4.2+) and legacy add-on (Blender 3.6+) ZIPs."""

from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "shapekey_inference"


def build():
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    for legacy in (False, True):
        filename = "topology_shape_keys-1.0.0" + ("-legacy" if legacy else "") + ".zip"
        with zipfile.ZipFile(output / filename, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(PACKAGE.iterdir()):
                if path.suffix not in {".py", ".toml"}:
                    continue
                if legacy and path.name == "blender_manifest.toml":
                    continue
                archive.write(path, ("shapekey_inference/" if legacy else "") + path.name)
            for name in ("LICENSE", "README.md"):
                path = ROOT / name
                if path.exists():
                    archive.write(path, ("shapekey_inference/" if legacy else "") + name)
        print(output / filename)


if __name__ == "__main__":
    build()
