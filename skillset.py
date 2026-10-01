"""Skills that ship with RefCut (the `skills/` folder).

Each one is installed into this machine's Claude skills folder (~/.claude/skills/<name>) so RefCut's
own `claude -p` calls and Claude Code on the machine can use it. Run `python skillset.py` to install
by hand; setup and RefCut's startup do the same.
"""
import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "skills"
MARKER = ".refcut-bundled"          # written into installed copies; holds the content hash


def claude_skills_dir():
    return Path.home() / ".claude" / "skills"


def _meta(skill_dir):
    """name / description from SKILL.md front matter."""
    out = {"name": skill_dir.name, "description": ""}
    try:
        text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    except OSError:
        return out
    if text.startswith("---"):
        for line in text.split("---", 2)[1].splitlines():
            k, _, v = line.partition(":")
            if k.strip() in out and v.strip():
                out[k.strip()] = v.strip()
    return out


def _digest(skill_dir):
    h = hashlib.sha256()
    for f in sorted(p for p in skill_dir.rglob("*") if p.is_file() and p.name != MARKER and "__pycache__" not in p.parts):
        h.update(f.relative_to(skill_dir).as_posix().encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


def bundled():
    """Skills in the bundle: [{name, description, path}]."""
    if not ROOT.is_dir():
        return []
    return [_meta(d) | {"path": str(d)} for d in sorted(ROOT.iterdir()) if (d / "SKILL.md").is_file()]


def path(name):
    """Folder of a bundled skill — RefCut points Claude at this copy, so it works even if install failed."""
    d = ROOT / name
    if not (d / "SKILL.md").is_file():
        raise RuntimeError(f"Bundled skill '{name}' is missing from {ROOT}")
    return d


def install():
    """Copy every bundled skill into ~/.claude/skills. A skill of the same name that RefCut didn't
    put there is left alone. Returns [{name, description, state, detail}]."""
    out = []
    for sk in bundled():
        src, dst = Path(sk["path"]), claude_skills_dir() / sk["name"]
        res = {"name": sk["name"], "description": sk["description"]}
        try:
            want = _digest(src)
            marker = dst / MARKER
            if dst.exists() and not marker.is_file():
                res |= {"state": "yours", "detail": f"You already have your own '{sk['name']}' skill; RefCut uses its bundled copy."}
            elif marker.is_file() and marker.read_text(encoding="utf-8").strip() == want:
                res |= {"state": "installed", "detail": str(dst)}
            else:
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
                marker.write_text(want, encoding="utf-8")
                res |= {"state": "installed", "detail": str(dst)}
        except OSError as e:
            res |= {"state": "error", "detail": f"Could not install to {dst}: {e}"}
        out.append(res)
    return out


if __name__ == "__main__":
    results = install()
    for r in results:
        print(f"[skills] {r['name']}: {r['state']} — {r['detail']}")
    if not results:
        print("[skills] nothing to install")
    sys.exit(1 if any(r["state"] == "error" for r in results) else 0)
