"""Build refcut-windows.zip: the folder to copy onto a Windows PC (then run setup.bat).

    python make_zip.py

Same files as the repo minus development extras, with the short Windows README (windows/README.md)
in place of the full one.
"""
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "refcut-windows.zip"
FILES = ["app.py", "analyze.py", "brain.py", "kinetic.py", "story.py", "mage.py", "broll.py", "skillset.py",
         "mascot.py", "voice.py", "requirements.txt", "setup.bat", "setup.ps1", "start.bat", "LICENSE",
         "static/index.html", "static/kinetic.js", "static/story.js",
         "vendor/davinci-resolve-mcp/LICENSE", "vendor/davinci-resolve-mcp/README.md",
         "vendor/davinci-resolve-mcp/src/CursorBridge.py", "vendor/davinci-resolve-mcp/src/resolve_mcp_bridge.py"]


def main():
    files = [(ROOT / f, f) for f in FILES]
    files += [(p, p.relative_to(ROOT).as_posix()) for p in sorted((ROOT / "skills").rglob("*"))
              if p.is_file() and "__pycache__" not in p.parts]
    files.append((ROOT / "windows" / "README.md", "README.md"))
    missing = [name for path, name in files if not path.is_file()]
    if missing:
        raise SystemExit(f"Missing: {', '.join(missing)}")
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path, name in files:
            z.write(path, f"refcut/{name}")
    print(f"{OUT.name}: {len(files)} files, {OUT.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
