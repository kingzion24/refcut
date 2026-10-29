"""Split explainer for vertical talking videos: drawings of what you're explaining fill the top half,
you're in the bottom half, captions sit on the seam.

Claude splits what you say into beats (one idea each) and draws every beat as a self-animating HTML page,
following one of three skills installed in ~/.claude/skills:

    archify             flows, systems, pipelines, sequences, timelines, data models
    anatomy             "how it works" as a physical object: isometric machines and mechanisms
    dimensional-drawing a UI component or product taken apart: spec sheet, layers, measurements

RefCut, not Claude, then renders every beat to video (render_html.cjs: headless Chrome, frame by frame)
at the exact length of its slot, so the drawings cover the speech with no gaps:

    prepare()  jobs/<id>/explainer/ with a review copy of the edit, word timings and brief.md
    run()      `claude -p` writes beats/NN/index.html + plan.json, then RefCut times and renders them
    collect()  plan.json -> beats fitted to the speech -> rendered clips pinned to timeline items

The clips have the same shape as motion B-roll clips (broll.py), so story.py places them the same way,
with `split` moving the footage into the bottom half.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import broll
import story

ROOT = Path(__file__).resolve().parent
RENDERER = ROOT / "render_html.cjs"
SKILLS = ("archify", "anatomy", "dimensional-drawing")
MIN_BEAT = 1.0                     # seconds: shorter beats are merged into the one before


def skill_dirs():
    """The drawing skills, from this machine's Claude skills folder. Raises if one is missing."""
    base = Path.home() / ".claude" / "skills"
    missing = [s for s in SKILLS if not (base / s / "SKILL.md").is_file()]
    if missing:
        raise RuntimeError(f"Missing drawing skill(s) in {base}: {', '.join(missing)}. Install them first "
                           "(see README: Split explainer).")
    return {s: base / s for s in SKILLS}


def speech_spans(tl):
    """Stretches of the edit that are your footage (not cards), as [start, end) frames. Drawings live here."""
    spans = []
    for x in tl["v1"]:
        if x["kind"] != "clip":
            continue
        if spans and spans[-1][1] == x["start"]:
            spans[-1][1] = x["start"] + x["frames"]
        else:
            spans.append([x["start"], x["start"] + x["frames"]])
    return spans


def prepare(job_dir, spec, sources, captions, context, notes, split):
    """Write jobs/<id>/explainer/{work/source.mp4, work/words.txt, brief.md}. Returns (folder, spec, tl)."""
    spec, tl = story.compile_story(spec, sources, captions, split=split)
    fps, fmt = tl["fps"], spec["format"]
    W, H = fmt["width"], fmt["height"]
    folder = Path(job_dir) / "explainer"
    if folder.exists():
        shutil.rmtree(folder)
    for d in ("beats", "check", "out", "work"):
        (folder / d).mkdir(parents=True)
    broll._review_copy(tl, spec, sources, job_dir, folder / "work" / "source.mp4")
    words = story.timeline_words(tl["v1"], sources, fps)
    (folder / "work" / "words.txt").write_text(broll.words_txt(words, fps), encoding="utf-8")

    sec = lambda f: f"{f / fps:.2f}"
    said = {}
    for w in words:
        said.setdefault(w["id"], []).append(w["w"])
    rows = [f"| {sec(x['start'])}–{sec(x['start'] + x['frames'])} | {x['name']} | "
            + ("CARD (full-screen title card: no drawing here)" if x["kind"] == "card"
               else f"“{' '.join(said.get(x['id'], [])) or '(no speech)'}”") + " |"
            for x in tl["v1"]]
    spans = "\n".join(f"- {sec(a)}–{sec(b)}" for a, b in speech_spans(tl))
    st = spec["style"]
    brief = f"""# Brief: split-screen explainer drawings

**The video:** `work/source.mp4` is a small, silent review copy of the edit ({sec(tl['total'])} s, {W}x{H} at {fps} fps).
The creator talks to camera and explains something. In the final video they sit in the BOTTOM half of the
frame and your drawings fill the TOP half, so the viewer sees what's being explained while it's said.
**The transcript:** `work/words.txt` has real word timings, `seconds:word`, on the edit's timeline.

**What the video is (the creator's own words):** {context.strip() or '(not given: infer it from the transcript)'}
**Title:** {spec.get('title', '')}
**Edit intent:** {spec.get('intent', '')}
**Anything to avoid or include:** {notes.strip() or 'Nothing extra.'}

## The panel

- Every drawing is a page of exactly **{W}x{H // 2} px** (the top half of the frame). Phone screen: keep labels
  large (at least {max(28, W // 30)} px), short (a few words) and away from the bottom {H // 40} px, where the
  captions sit on the seam.
- **Look:** one look for the whole video. Background `{st['background']}`, text `{st['color']}`, accent `{st['accent']}`
  (lean toward a brand if the context names one). The background fills the whole panel: no transparency.

## Where drawings go

Drawings cover these stretches of speech, end to end (RefCut stretches each beat to meet the next):

{spans or '- (no speech found)'}

## The edit, item by item (seconds on the edit's timeline)

| In–Out | Item | Said |
|---|---|---|
{chr(10).join(rows)}
"""
    (folder / "brief.md").write_text(brief, encoding="utf-8")
    return folder, spec, tl


PROMPT = """You are running inside RefCut, a tool that edits talking videos. Nobody is watching this session:
never ask a question and never wait for approval. Read `brief.md` and `work/words.txt` first.

Your job: draw what the creator is explaining, beat by beat, for the TOP HALF of a vertical video.

## 1. Plan the beats
Split the speech into beats: one idea each, usually 4–12 seconds, starting on the word that introduces the
idea. Cover every stretch listed under "Where drawings go"; never put a beat on a CARD item. For each beat,
pick the skill that explains it best:

- **archify** ({archify}): flows, systems, pipelines, request/response sequences, timelines, data models,
  step-by-step processes.
- **anatomy** ({anatomy}): how something works, shown as a physical object or machine (isometric).
- **dimensional-drawing** ({dimensional}): a UI component or product taken apart: layers, spacing, parts.

Read the SKILL.md of every skill you use (and the references it points to for the kind of figure you're
making) before you draw with it. Write the beat list to `PLAN.md`, then continue straight away.

## 2. Draw each beat as one HTML page: `beats/NN/index.html` (NN = 01, 02, …)
Follow the skill's craft, with these rules for video (they override the skill where they conflict):

- **Exactly {W}x{PH} px**, `margin:0`, no scrolling. Background fills the panel. No hover hints, buttons,
  "drag to rotate", readouts that ask for input or other interactive chrome: this is video.
- **Self-contained:** inline all CSS and JS (copy what you need from a skill's kit into the page). No network
  URLs, no `<script type=module src=…>` or `import` from files (pages are opened from file://, which blocks
  them). Fonts: system fonts only.
- **The time contract:** the page defines
  `window.refcut = {{ duration: <seconds>, seek(t) {{ … }} }}`.
  `seek(t)` draws the exact state at `t` seconds after the beat starts, from scratch every call: no
  requestAnimationFrame loops, timers, Date or unseeded Math.random. `t < 0` shows the opening state,
  `t > duration` holds the final state. On load, the page shows `seek(0)`.
- **Timed to the words:** build the drawing up as the creator says each part, using the word times in
  `work/words.txt` minus the beat's `in`. Ease every move; nothing pops in all at once.
- **Check your work:** render stills and look at them with Read before moving on, e.g. the page at 2.5 s:
  `node "{renderer}" beats/01/index.html check/01_2.5.png {W} {PH} {H} {fps} 1 2.5`
  Check the opening, the middle and the end of every beat. Fix overlaps, clipped labels and tiny text.

## 3. Write `plan.json`
```json
{{"beats": [{{"id": "01", "title": "short name", "skill": "archify", "line": "what is said",
             "in": 12.40, "out": 19.80, "html": "beats/01/index.html"}}]}}
```
`in` / `out` are seconds on the edit's timeline (the times in `work/words.txt`). Don't render video yourself:
RefCut renders every beat from your page once `plan.json` exists. If a beat won't come right after two fixes,
leave it out of `plan.json` and say so: the beat before it is stretched to cover the gap.

Finish with a 2–4 line summary: the beats, which skill each used, anything you left out.
"""


def fit_beats(plan, tl):
    """plan.json beats -> [(beat, start, end, shift)] in frames, end to end inside each speech stretch.
    shift: seconds the clip starts before the beat's own `in` (the page holds its opening state)."""
    fps = tl["fps"]
    beats = []
    for b in plan.get("beats") or []:
        try:
            beats.append((b, int(round(float(b["in"]) * fps))))
        except (KeyError, TypeError, ValueError):
            continue
    beats.sort(key=lambda x: x[1])
    out = []
    for a, z in speech_spans(tl):
        mine = [(b, s) for b, s in beats if a - fps <= s < z]       # a beat starting just before the stretch belongs to it
        kept = []
        for b, s in mine:
            if kept and s - max(a, kept[-1][1]) < MIN_BEAT * fps:   # too short: the previous beat covers it
                continue
            kept.append((b, max(a, s)))
        for i, (b, s) in enumerate(kept):
            start = a if i == 0 else s
            end = kept[i + 1][1] if i + 1 < len(kept) else z
            out.append((b, start, end, (int(round(float(b["in"]) * fps)) - start) / fps))
    return out


def render(page, out, fmt, frames, t0):
    """One beat page -> ProRes 4444 clip, full frame, drawing in the top half."""
    W, H, fps = fmt["width"], fmt["height"], fmt["fps"]
    env = os.environ | {"NODE_PATH": str(broll.RUNTIME / "node_modules"), "FFMPEG": broll._ffmpeg()}
    r = subprocess.run([shutil.which("node") or "node", str(RENDERER), str(page), str(out), str(W), str(H // 2), str(H),
                        str(fps), str(frames), f"{t0:.3f}"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    if r.returncode or not Path(out).is_file():
        raise RuntimeError((r.stderr or r.stdout).strip()[-400:] or "renderer failed")
    return r.stdout


def collect(job_dir, tl, fmt):
    """plan.json -> rendered beats pinned to timeline items, as (kind, text) events; returns the clips
    [{id, title, line, kind, anchor, at, dur, frames, file, preview}]."""
    folder = (Path(job_dir) / "explainer").resolve()
    try:
        plan = json.loads((folder / "plan.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        yield "err", "Claude didn't write a usable plan.json"
        return []
    fps = tl["fps"]
    fitted = fit_beats(plan, tl)
    covered = sum(e - s for _, s, e, _ in fitted)
    speech = sum(z - a for a, z in speech_spans(tl))
    if covered < speech:
        yield "say", f"{(speech - covered) / fps:.1f} s of speech have no drawing (no beat planned there)."
    clips = []
    for n, (b, start, end, shift) in enumerate(fitted, 1):
        page = (folder / str(b.get("html") or "")).resolve()
        if not page.is_file() or not page.is_relative_to(folder):
            yield "err", f"Beat {b.get('id', n)}: page {b.get('html')} not found"
            continue
        out = folder / "out" / f"{n:02d}.mov"
        yield "tool", f"Rendering beat {n}/{len(fitted)} · {b.get('title') or ''} · {(end - start) / fps:.1f}s"
        try:
            log = render(page, out, fmt, end - start, -shift)
        except RuntimeError as e:
            yield "err", f"Beat {b.get('id', n)} didn't render: {e}"
            continue
        if "mode=seek" not in log:
            yield "say", f"Beat {b.get('id', n)} has no seek(t): rendered from its CSS/SVG animations instead."
        host = next(x for x in tl["v1"] if x["start"] <= start < x["start"] + x["frames"])
        preview = broll.browser_preview(out)
        clips.append({"id": f"d{n}", "title": str(b.get("title") or f"Beat {n}"), "line": str(b.get("line") or ""),
                      "kind": "panel", "skill": str(b.get("skill") or ""), "anchor": host["id"],
                      "at": round((start - host["start"]) / fps, 3), "dur": round((end - start) / fps, 3),
                      "frames": end - start, "file": str(out),
                      "preview": preview.relative_to(Path(job_dir).resolve()).as_posix() if preview else None})
    return clips


def run(job_dir, spec, sources, captions, context, notes="", split=None):
    """Generator of (kind, text) events while Claude draws and RefCut renders; returns the clips."""
    job_dir = Path(job_dir).resolve()
    split = split or {"face_y": 0.4}
    skills = skill_dirs()
    yield from broll.ensure_runtime()
    yield "say", "Preparing the footage and word timings for Claude…"
    folder, spec, tl = prepare(job_dir, spec, sources, captions, context, notes, split)
    fmt = spec["format"]
    prompt = PROMPT.format(archify=skills["archify"].as_posix(), anatomy=skills["anatomy"].as_posix(),
                           dimensional=skills["dimensional-drawing"].as_posix(), renderer=RENDERER.as_posix(),
                           W=fmt["width"], PH=fmt["height"] // 2, H=fmt["height"], fps=fmt["fps"])
    yield from broll.claude_stream(prompt, folder, list(skills.values()) + [ROOT])
    yield "say", "Rendering the drawings to video…"
    clips = yield from collect(job_dir, tl, fmt)
    yield ("done" if clips else "err"), (f"{len(clips)} drawing{'s' if len(clips) != 1 else ''} ready: top half in the "
                                         "preview, on their own track in Resolve." if clips
                                         else "No drawings were produced.")
    return clips
