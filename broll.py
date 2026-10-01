"""Motion-graphic B-roll for talking videos, made by Claude following the bundled `motion-broll` skill
(skills/motion-broll, from github.com/Barty-Bart/motion-graphics).

The skill is written for an interactive Claude Code session: it interviews the user, shows a plan,
waits for approval. RefCut already knows all of that, so it runs the skill headless:

    prepare()  jobs/<id>/motion/ with a small review copy of the edit (work/source.mp4), real word
               timings on the edit's timeline (work/words.txt) and brief.md (the interview answers)
    run()      `claude -p` in that folder, told to follow the skill and finish with plan.json
    collect()  plan.json -> clips pinned to timeline items, with browser-playable previews

The clips then sit on their own track in the preview and in the Resolve build (story.py).
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import skillset
import story

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "motion-runtime"          # Playwright + Chromium, shared by every job
SKILL = "motion-broll"
DENSITY = {"light": "Light: only the 1–2 strongest moments per 30 s.",
           "medium": "Medium: about 2–3 clips per 30 s.",
           "heavy": "Heavy: most lines covered, with short gaps of face between clips."}


def _ffmpeg():
    return shutil.which("ffmpeg") or "ffmpeg"


def _run(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)


# ─── runtime (Node + Playwright) ─────────────────────────────────────────────

def runtime_status():
    node = shutil.which("node")
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    ready = (RUNTIME / "node_modules" / "playwright").is_dir() and (RUNTIME / ".chromium-installed").is_file()
    if not node or not npm:
        return {"state": "no_node", "detail": "Node.js isn't installed. Install the LTS from nodejs.org (or re-run setup), then try again."}
    return {"state": "ready" if ready else "not_installed",
            "detail": "Ready" if ready else "First run downloads the renderer (Playwright + Chromium, ~150 MB)."}


def ensure_runtime():
    """Install Playwright + Chromium once. Generator of (kind, text) log events; raises on failure."""
    st = runtime_status()
    if st["state"] == "ready":
        return
    if st["state"] == "no_node":
        raise RuntimeError(st["detail"])
    RUNTIME.mkdir(exist_ok=True)
    pkg = RUNTIME / "package.json"
    if not pkg.exists():
        pkg.write_text('{"private": true}', encoding="utf-8")
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    yield "tool", "Installing the renderer (Playwright) — first run only"
    r = _run([npm, "install", "--silent", "playwright"], cwd=RUNTIME)
    if r.returncode:
        raise RuntimeError("npm install playwright failed: " + (r.stderr or r.stdout)[-400:])
    yield "tool", "Downloading Chromium for rendering (~150 MB)"
    r = _run([npx, "playwright", "install", "chromium"], cwd=RUNTIME)
    if r.returncode:
        raise RuntimeError("Chromium download failed: " + (r.stderr or r.stdout)[-400:])
    (RUNTIME / ".chromium-installed").write_text("ok", encoding="utf-8")
    yield "say", "Renderer installed"


# ─── inputs for the skill ────────────────────────────────────────────────────

def _review_copy(tl, spec, sources, job_dir, out):
    """The edit as one small silent video (proxies cut together, cards as plain colour) so Claude can
    look at the footage on the edit's own clock."""
    W, H, fps = spec["format"]["width"], spec["format"]["height"], spec["format"]["fps"]
    k = 960 / max(W, H)
    w, h = int(W * k) // 2 * 2, int(H * k) // 2 * 2
    parts = Path(out).parent / "parts"
    parts.mkdir(parents=True, exist_ok=True)
    enc = ["-r", str(fps), "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p"]
    names = []
    for i, x in enumerate(tl["v1"]):
        p = parts / f"{i:03d}.mp4"
        dur = x["frames"] / fps
        src = sources.get(x.get("source")) if x["kind"] == "clip" else None
        if src and src.get("proxy"):
            z = x["zoom"]
            vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
                  f"scale=iw*{z}:ih*{z},crop={w}:{h}")
            r = _run([_ffmpeg(), "-y", "-ss", f"{max(0, x['src_in'] - src['range'][0]):.3f}", "-t", f"{dur:.3f}",
                      "-i", str(Path(job_dir) / src["proxy"]), "-vf", vf, "-frames:v", str(x["frames"]), *enc, str(p)])
        else:
            bg = (x.get("scene") or {}).get("background") or spec["style"]["background"]
            r = _run([_ffmpeg(), "-y", "-f", "lavfi", "-i", f"color=c={bg.replace('#', '0x')}:s={w}x{h}:r={fps}",
                      "-frames:v", str(x["frames"]), *enc, str(p)])
        if r.returncode or not p.exists():
            raise RuntimeError(f"Could not make the review copy ({x['name']}): {r.stderr[-300:]}")
        names.append(p)
    lst = parts / "list.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in names), encoding="utf-8")
    r = _run([_ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", "list.txt", "-c", "copy", str(Path(out).resolve())], cwd=parts)
    if r.returncode:
        raise RuntimeError("Could not join the review copy: " + r.stderr[-300:])
    shutil.rmtree(parts, ignore_errors=True)


def prepare(job_dir, spec, sources, captions, context, density, notes):
    """Write jobs/<id>/motion/{work/source.mp4, work/words.txt, brief.md}. Returns (motion_dir, spec, tl)."""
    spec, tl = story.compile_story(spec, sources, captions)
    fps, fmt = tl["fps"], spec["format"]
    motion = Path(job_dir) / "motion"
    if motion.exists():
        shutil.rmtree(motion)
    for d in ("clips", "dist", "out", "work"):
        (motion / d).mkdir(parents=True)
    _review_copy(tl, spec, sources, job_dir, motion / "work" / "source.mp4")

    words = story.timeline_words(tl["v1"], sources, fps)
    lines, cur = [], []
    for i, w in enumerate(words):                 # one line per sentence / timeline item, time:word like the skill's words.py
        cur.append(f"{w['s'] / fps:.2f}:{w['w']}")
        nxt = words[i + 1] if i + 1 < len(words) else None
        if nxt is None or w["w"][-1:] in ".?!" or nxt["id"] != w["id"]:
            lines.append(" ".join(cur))
            cur = []
    (motion / "work" / "words.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    sec = lambda f: f"{f / fps:.2f}"
    said = {}
    for w in words:
        said.setdefault(w["id"], []).append(w["w"])
    rows = []
    for x in tl["v1"]:
        what = ("CARD (text-only title card — never put B-roll here)" if x["kind"] == "card"
                else f"{x['source']} · “{' '.join(said.get(x['id'], [])) or '(no speech)'}”")
        rows.append(f"| {sec(x['start'])}–{sec(x['start'] + x['frames'])} | {x['name']} | {what} |")
    busy = [f"- {sec(o['start'])}–{sec(o['start'] + o['frames'])}: {o['name']} "
            f"({'Mage, the mascot, ' if any(e['el']['kind'] == 'mage' for e in o['scene']['elements']) else ''}"
            f"{' / '.join(e['el']['text'] for e in o['scene']['elements'] if e['el']['kind'] == 'text') or 'no text'})"
            for o in tl["overlays"] if o["layer"] == "overlay"]
    st = spec["style"]
    brief = f"""# Brief for motion B-roll (the skill's interview, already answered)

**The video:** `work/source.mp4` — a small, silent review copy of the edit ({sec(tl['total'])} s). The real edit is
**{fmt['width']}x{fmt['height']} at {fps} fps**; render every clip at that size and frame rate.
**The transcript:** `work/words.txt` — real word timings (from the audio), `seconds:word`, on the edit's timeline.

**What the video is (the creator's own words):** {context.strip() or '(not given — infer it from the transcript)'}
**Title:** {spec.get('title', '')}
**Edit intent:** {spec.get('intent', '')}

**Density:** {DENSITY.get(density, DENSITY['medium'])}
**Look:** the skill's default look, but take the accent from this video's palette: accent `{st['accent']}`,
text `{st['color']}`, background `{st['background']}`. If the context names a brand, lean toward it.
**Anything to avoid or include:** {notes.strip() or 'Nothing extra.'}

## The edit, item by item (seconds on the edit's timeline)

| In–Out | Item | What's on screen / said |
|---|---|---|
{chr(10).join(rows)}

## Already on screen (keep B-roll clear of these)

{chr(10).join(busy) or '- nothing'}
- Captions: {'burned in around y = ' + format(spec['captions']['y'], '.0%') + ' of the frame height during speech' if captions == 'burned' else 'none burned in'}.
  A full-frame cutaway covers the footage but the captions stay on top of it, so keep that band free of
  important content.
"""
    (motion / "brief.md").write_text(brief, encoding="utf-8")
    return motion, spec, tl


PROMPT = """You are running inside RefCut, a tool that edits talking videos. Nobody is watching this session:
never ask a question and never wait for approval. Make motion-graphic B-roll for the edit described in
`brief.md` by following the **motion-broll skill** at:

    {skill}

Read `{skill}/SKILL.md` and `{skill}/reference/engine-api.md` first, and open at least one example in
`{skill}/examples/opus-aoe2/` before writing your first clip. Then read `brief.md` and `work/words.txt`.

How this run differs from the skill's interactive flow:
- **Working folder:** the current folder IS the skill's `motion/` folder: `clips/`, `dist/`, `out/`,
  `work/` already exist. Use those paths without a `motion/` prefix.
- **Step 1 (setup) is done.** Do not run `setup.sh` and do not install anything. `NODE_PATH` is already set
  in this environment to `{node_path}`. Run Python scripts with `"{python}"` (there may be no `python3`
  command) and Node scripts with `node`.
- **Step 2 (interview):** the answers are in `brief.md`. Do not use AskUserQuestion.
- **Step 3:** run `inspect_video.py` on `work/source.mp4` and look at the contact sheet. It is a low-resolution
  copy, so ignore the resolution it reports: clips are **{W}x{H} at {fps} fps** (set `W`/`H` in `M.scene`
  and pass `{fps}` to `render.js`). `work/words.txt` is already made — don't run `words.py`.
- **Step 4 (plan):** write the plan table to `PLAN.md`, then continue straight away.
- Put clips only on spoken footage, never on a CARD item or over the things listed under "Already on screen".
  Keep clear of the first second of the video.
- **Length:** every clip must be at least as long as its slot (`T >= out − in`). Resolve can't hold a last frame.
- {vertical}
- **Step 8:** skip `composite.py` and `make_pages.py` (RefCut has its own preview). Do write `plan.json`
  exactly as in the reference (`"video": "work/source.mp4"`, `"fps": "{fps}"`), with `in` / `out` in seconds
  on the edit's timeline and `file` relative to this folder, plus `TIMING.md`.
- If a clip fails to render after two fixes, drop it from `plan.json` and say so. A smaller set of good
  clips beats a broken one.

When `plan.json` is written, finish with a 2–4 line summary: which clips you made and what is illustrative.
"""


def run(job_dir, spec, sources, captions, context, density="medium", notes=""):
    """Generator of (kind, text) events while Claude makes the clips."""
    import brain
    job_dir = Path(job_dir).resolve()
    skill = skillset.path(SKILL)
    yield from ensure_runtime()
    yield "say", "Preparing the footage and word timings for Claude…"
    motion, spec, tl = prepare(job_dir, spec, sources, captions, context, density, notes)
    fmt = spec["format"]
    vertical = ("**Vertical video:** the examples are 1920x1080. Here the frame is taller than wide, so choose `cam` "
                "and state sizes so each state fits the WIDTH with margin, and stack content vertically."
                if fmt["height"] > fmt["width"] else
                "**Framing:** choose `cam` so each state fills the frame, as in the examples.")
    prompt = PROMPT.format(skill=skill.as_posix(), node_path=(RUNTIME / "node_modules").as_posix(),
                           python=Path(sys.executable).as_posix(), W=fmt["width"], H=fmt["height"], fps=fmt["fps"],
                           vertical=vertical)
    env = os.environ | {"NODE_PATH": str(RUNTIME / "node_modules")}
    proc = subprocess.Popen(
        [brain.claude_bin(), "-p", "--add-dir", str(skill), "--allowedTools", "Read,Write,Edit,Glob,Grep,Bash",
         "--output-format", "stream-json", "--verbose"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=motion, env=env,
        text=True, encoding="utf-8", errors="replace")
    proc.stdin.write(prompt)
    proc.stdin.close()
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "assistant":
            for block in ev.get("message", {}).get("content", []):
                if block.get("type") == "text" and block["text"].strip():
                    yield "say", block["text"].strip()[:600]
                elif block.get("type") == "tool_use":
                    inp = block.get("input", {})
                    what = inp.get("command") or inp.get("file_path") or inp.get("pattern") or ""
                    yield "tool", f"{block['name']} {str(what).replace(chr(10), ' ')[:160]}"
        elif ev.get("type") == "result" and ev.get("is_error"):
            yield "err", str(ev.get("result"))[:400]
    proc.wait()
    if proc.returncode:
        yield "err", f"claude exited with code {proc.returncode}"
    clips = collect(job_dir, tl)
    yield ("done" if clips else "err"), (f"{len(clips)} B-roll clip{'s' if len(clips) != 1 else ''} ready — they're in the "
                                         "preview and will be placed on their own track in Resolve."
                                         if clips else "No clips were produced (no plan.json or no rendered files).")
    return clips


def collect(job_dir, tl):
    """plan.json -> clips pinned to timeline items: [{id, title, line, kind, anchor, at, dur, frames, file, preview}]."""
    motion = Path(job_dir) / "motion"
    try:
        plan = json.loads((motion / "plan.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    fps = tl["fps"]
    out = []
    for i, c in enumerate(plan.get("clips") or []):
        try:
            f = (motion / c["file"]).resolve()
            t_in, t_out = float(c["in"]), float(c["out"])
        except (KeyError, TypeError, ValueError):
            continue
        if not f.is_file() or not f.is_relative_to(motion.resolve()) or t_out <= t_in:
            continue
        start = int(round(t_in * fps))
        host = next((x for x in tl["v1"] if x["start"] <= start < x["start"] + x["frames"]), None)
        if not host:
            continue
        pr = _run([shutil.which("ffprobe") or "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                   "format=duration", "-of", "csv=p=0", str(f)])
        try:
            frames = int(round(float(pr.stdout.strip()) * fps))
        except ValueError:
            continue
        kind = "panel" if (c.get("kind") == "panel" or f.suffix.lower() == ".mov") else "full"
        preview = f
        if f.suffix.lower() != ".mp4":            # ProRes 4444 doesn't play in a browser: VP9 with alpha for the preview
            preview = f.with_suffix(".preview.webm")
            r = _run([_ffmpeg(), "-y", "-i", str(f), "-vf", "scale=-2:720", "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
                      "-b:v", "1M", "-deadline", "realtime", "-cpu-used", "8", "-an", str(preview)])
            if r.returncode:
                preview = None
        out.append({"id": str(c.get("id") or i + 1), "title": str(c.get("title") or f.stem), "line": str(c.get("line") or ""),
                    "kind": kind, "anchor": host["id"], "at": round((start - host["start"]) / fps, 3),
                    "dur": round(t_out - t_in, 3), "frames": frames, "file": str(f),
                    "preview": preview.relative_to(Path(job_dir).resolve()).as_posix() if preview else None})
    return out
