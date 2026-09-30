"""Claude Code (headless `claude -p`) wrappers: blueprint, refine, build."""
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MCP_SERVER = ROOT / "vendor" / "davinci-resolve-mcp" / "src" / "resolve_mcp_bridge.py"
BRIDGE_STATUS = "http://127.0.0.1:9876/status"

BLUEPRINT_SCHEMA = """{
  "title": "short name for this edit",
  "intent": "2-4 sentences: what the user is building and the feel of the reference, in plain words",
  "style": {
    "format": "e.g. 1080x1920 @ 30fps vertical",
    "width": 1080, "height": 1920, "fps": 30,
    "pacing": "how fast / how the rhythm evolves",
    "cutting": "on-beat? match cuts? jump cuts? speed ramps?",
    "look": "color/grade description in words",
    "text": "on-screen text style (font vibe, placement, size, animation) or 'none'",
    "transitions": "hard cuts / whips / dissolves ..."
  },
  "structure": [{"section": "Hook", "start": 0.0, "end": 2.5, "purpose": "..."}],
  "timeline": [
    {"slot": 1, "start": 0.0, "dur": 0.8, "ref_shot": 1,
     "clip_id": "C003", "clip_in": 4.2,
     "why": "short reason this clip fits this slot", "text": null,
     "cdl": null}
  ],
  "text_overlays": [{"at": 0.0, "dur": 2.0, "text": "...", "style": "..."}],
  "grade": {"description": "...", "cdl": {"Slope": "1 1 1", "Offset": "0 0 0", "Power": "1 1 1", "Saturation": "1.0"}},
  "music": {"clip_id": "C010 or null", "start_offset": 0.0, "notes": "..."},
  "manual_steps": ["things the Resolve scripting API cannot do that the user must finish by hand"],
  "gaps": ["footage the user is missing to match the reference well"],
  "questions": ["only questions that genuinely change the edit; [] if none"]
}"""

BLUEPRINT_PROMPT = """You are a senior video editor. The user wants to make an edit that feels like a
reference video, using their own footage in DaVinci Resolve. They will NOT explain much —
your job is to infer what they're going for from the reference itself.

## User's context (may be very short)
{context}

## Reference analysis (machine-measured)
```json
{reference}
```
Reference contact sheets (one frame per shot, labelled #shot start (duration)) — READ THESE IMAGES:
{ref_sheets}

## User's footage
```json
{footage}
```
Footage contact sheets (3 frames per clip: 10% / 50% / 90%, labelled with clip id) — READ THESE IMAGES:
{footage_sheets}

## What to do
1. Read every contact sheet image with the Read tool. Look for: subject matter, framing
   (close/wide), camera movement cues, on-screen text (read it!), color look, and the story arc.
2. Combine what you see with the measured pacing, beat sync and energy curve to work out the
   editing recipe: structure (hook / build / payoff), shot rhythm, when cuts land on beats or
   energy jumps, text treatment, and grade.
3. Map the user's clips onto a new timeline that recreates that recipe. Match the reference's
   shot durations and rhythm (snap cuts to beat_times when the reference cuts on beat).
   Pick the clip and in-point (clip_in, seconds) that best matches each reference shot's role.
   Never exceed a clip's duration (clip_in + dur <= clip duration). Reuse clips only if needed.
   Output format should match the reference's aspect ratio and fps unless the context says otherwise.
   If there is no footage, still produce the timeline with clip_id null and describe the shot needed in "why".
4. Put a CDL in "grade" that approximates the reference's overall look relative to neutral footage
   (use the look stats: warmth>0 = warm, saturation, contrast). Keep it plausible.
   If individual reference shots have a clearly different look (per-shot color stats), give that
   timeline slot its own "cdl" (same keys) — it replaces the global grade on that clip. Else null.
   Reframing to the output aspect is handled automatically (scale-to-fill), so don't list it as manual.
5. The scripting API cannot set keyframes, add transitions, or set the text content of titles —
   list anything like that in manual_steps.

Respond with ONLY one JSON object in a ```json fence, following this schema:
{schema}
"""

REFINE_PROMPT = """The user wants changes to the blueprint:

{message}

Apply them and respond with the COMPLETE updated blueprint as ONE JSON object in a ```json fence
(same schema as before). No other text."""

BUILD_PROMPT = """You are driving DaVinci Resolve through the davinci-resolve MCP tools.
Build this edit from the blueprint below. Work carefully and verify as you go.

## Blueprint
```json
{blueprint}
```

## Clip id -> absolute file path
```json
{paths}
```

## Steps
1. get_resolve_status. If the bridge is unreachable, stop and say so.
2. create_project named "{project}" (if it already exists, load_project it) and set project
   settings: timelineResolutionWidth={w}, timelineResolutionHeight={h}, timelineFrameRate={fps}
   and timelineInputResMismatchBehavior=scaleToCrop (so footage of another aspect fills the frame)
   (set these BEFORE creating the timeline; ignore failures on frame rate if media already exists).
3. import_media with the absolute paths of every clip the timeline uses (plus music).
   Then get_media_pool to learn the exact media-pool clip names.
4. create_timeline named "{project} - v1".
5. For each timeline slot in order, insert_to_timeline on track 1:
   record_frame = timeline start frame + round(slot.start * {fps}),
   start_frame = round(clip_in * clip_fps), end_frame = start_frame + round(dur * clip_fps) - 1
   (clip_fps = that clip's own frame rate). get_timeline_info first to learn the timeline start frame
   (usually 86400). Skip slots whose clip_id is null but add a red marker there with the "why".
6. If music has a clip_id, insert it on audio (mediaType 2) at the timeline start.
7. add_marker for each structure section (blue) at its start, note = purpose.
   add_marker (yellow) at each text overlay with the text in the note.
8. set_cdl on every video clip on track 1 (NodeIndex "1"): use the slot's own "cdl" if it has one,
   otherwise the blueprint grade CDL. clip_index is the 0-based order on the track.
9. save_project. Then get_timeline_clips to verify the clip count and order.
10. Finish with a short report: what was built, anything that failed, and the manual_steps list.

Keep going on individual failures (note them) — don't stop the whole build for one clip.
"""


KINETIC_PROMPT = """You are a motion designer who makes Apple-style kinetic typography: text-only launch /
product videos (think apple.com keynote reels and polished SaaS launch films). You write a MOTION
SCRIPT (JSON) that RefCut turns into keyframed Fusion comps in DaVinci Resolve and a live preview.
The user explains very little — infer intent, tone and pacing from the reference and context.

## User's context
{context}

## Output format
{fmt}

## Reference video analysis
{reference}
{sheets}

## Music to sync to
{music}

## Apple kinetic-type craft rules (defaults — the reference overrides them)
- One idea per scene. 1–5 words is typical; a scene is usually 0.6–2.2 s. Short punchy words can be
  0.3–0.5 s beat-cuts ("Fast." "Private." "Yours.").
- Big, confident type: hero words size 0.08–0.16 (cap height as fraction of frame height), supporting
  lines 0.025–0.045. Tight tracking (-0.02 to 0) for big type, slightly looser for small text.
- Near-black background (#000000) with off-white text (#F5F5F7), secondary text grey (#86868B), one
  accent colour used sparingly (e.g. #2997FF) — unless the reference/brand says otherwise.
- Motion: entrances ease out (expo_out), exits ease in and are quicker than entrances. Signature
  moves: blur_in (soft focus pull + slight scale-down), rise, cascade (letters reveal one after another),
  punch on beats, and hard cuts for rhythm. A subtle drift (scale 1.02–1.05 across the hold) keeps
  static text alive. Build → peak → resolve; end on the product name / call to action, held longer.
- "stepped" motion (stop-motion feel, animation on twos/threes) only if the reference looks choppy or the
  user asks for stop motion; otherwise "smooth".
- If music is given, put scene changes and punch/cut entrances on beat_times; align the biggest moment
  with the largest energy jump.
- Read on-screen text in the reference frames and mirror its structure and rhythm, but write NEW copy
  for the user's product unless they ask to copy it.

## Motion vocabulary (use only these)
in.type:  fade | rise | drop | blur_in | punch | grow | slide_left | slide_right | track_in | tilt_in | cascade | cut
out.type: fade | rise | drop | blur_out | shrink | punch_out | slide_left | slide_right | track_out | cut   (or out = null to hold to scene end)
ease:     expo_out | quart_out | back_out | in_out | in | linear
Times are seconds relative to the scene start. cascade uses in.stagger (seconds between letters, 0.015–0.05).
Coordinates: x, y are the text centre as fractions of the frame (0,0 = top-left). Multi-line text: use "\\n".
Multiple elements in one scene can be staggered with different in.at values (e.g. a headline, then a sub-line).

Respond with ONLY one JSON object in a ```json fence:
{{
  "title": "short name",
  "intent": "2-3 sentences: what this video is and the feel you're going for",
  "format": {{"width": {w}, "height": {h}, "fps": {fps}}},
  "style": {{"background": "#000000", "color": "#F5F5F7", "accent": "#2997FF",
            "font": "{font}", "weight": "Semibold", "motion": "smooth", "step_frames": 2, "easing": "expo_out"}},
  "scenes": [
    {{"name": "Hook", "dur": 1.6, "background": null,
      "elements": [
        {{"text": "Meet Flowdesk.", "x": 0.5, "y": 0.5, "size": 0.11, "weight": "Bold", "color": "color",
          "tracking": -0.02,
          "in": {{"type": "blur_in", "at": 0.0, "dur": 0.7, "ease": "expo_out", "stagger": 0}},
          "out": {{"type": "blur_out", "at": 1.3, "dur": 0.3, "ease": "in"}},
          "drift": {{"scale": 1.03}} }}
      ]}}
  ],
  "notes": ["anything the user should know, e.g. fonts to install, where the beat sync matters"],
  "questions": []
}}
("color" can be "color", "accent", or a hex value. "weight" must be a style name the font actually has.)
"""


def make_motion_script(job_dir, context, reference, music, fmt, font, motion_pref):
    if reference:
        ref = {k: v for k, v in reference.items() if k not in ("contact_sheets", "dense_sheets")}
        ref_txt = "```json\n" + json.dumps(ref, indent=1) + "\n```"
        sheets = ("Frames sampled evenly through the reference (labelled with time) — READ THESE IMAGES "
                  "with the Read tool, in order, to see how text enters, holds and exits:\n" +
                  "\n".join(f"- {s}" for s in reference.get("dense_sheets") or reference["contact_sheets"]))
    else:
        ref_txt, sheets = "(no reference — use the Apple craft rules)", ""
    if motion_pref in ("smooth", "stepped"):
        context = (context or "") + f"\n(User explicitly wants motion: {motion_pref}.)"
    prompt = KINETIC_PROMPT.format(
        context=(context or "").strip() or "(none — infer from the reference)",
        fmt=f"{fmt['width']}x{fmt['height']} @ {fmt['fps']} fps",
        reference=ref_txt, sheets=sheets,
        music=("```json\n" + json.dumps(music, indent=1) + "\n```") if music else "(no music given)",
        w=fmt["width"], h=fmt["height"], fps=fmt["fps"], font=font)
    out = _run_claude(["--output-format", "json", "--allowedTools", "Read"], prompt, job_dir)
    return _extract_json(out["result"]), out.get("session_id")


def claude_bin():
    exe = shutil.which("claude") or shutil.which("claude.cmd") or shutil.which("claude.exe")
    if not exe:
        raise RuntimeError("Claude Code CLI not found. Install it and run `claude` once to log in.")
    return exe


def bridge_status():
    try:
        with urllib.request.urlopen(BRIDGE_STATUS, timeout=3) as r:
            return {"ok": True, "status": json.loads(r.read().decode("utf-8"))}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _extract_json(text):
    m = re.search(r"```json\s*(\{.*\})\s*```", text, re.S)
    raw = m.group(1) if m else text[text.find("{"): text.rfind("}") + 1]
    return json.loads(raw)


def _run_claude(args, prompt, cwd):
    r = subprocess.run([claude_bin(), "-p", *args], input=prompt, cwd=cwd, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    try:
        out = json.loads(r.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"claude failed (exit {r.returncode}): {(r.stderr or r.stdout)[:800]}")
    if out.get("is_error"):
        raise RuntimeError(f"claude error: {out.get('result')}")
    return out


def make_blueprint(job_dir, context, reference, footage):
    ref = {k: v for k, v in reference.items() if k != "contact_sheets"}
    foot = {"folder": footage["folder"],
            "clips": [{k: v for k, v in c.items() if k != "path"} for c in footage["clips"]]} if footage else None
    prompt = BLUEPRINT_PROMPT.format(
        context=context.strip() or "(none given — infer everything from the reference)",
        reference=json.dumps(ref, indent=1),
        ref_sheets="\n".join(f"- {s}" for s in reference["contact_sheets"]),
        footage=json.dumps(foot, indent=1) if foot else "(no footage folder given)",
        footage_sheets="\n".join(f"- {s}" for s in (footage or {}).get("contact_sheets", [])) or "(none)",
        schema=BLUEPRINT_SCHEMA,
    )
    out = _run_claude(["--output-format", "json", "--allowedTools", "Read"], prompt, job_dir)
    return _extract_json(out["result"]), out.get("session_id")


def refine_blueprint(job_dir, session_id, message):
    out = _run_claude(["--resume", session_id, "--output-format", "json", "--allowedTools", "Read"],
                      REFINE_PROMPT.format(message=message), job_dir)
    return _extract_json(out["result"]), out.get("session_id") or session_id


def build_kinetic(job_dir, spec, project_name, music_path, text_scale):
    """Deterministic: write one .comp per scene, then one bridge call lays them out in Resolve."""
    import kinetic
    job_dir = Path(job_dir).resolve()
    spec, files = kinetic.write_all(spec, job_dir / "comps", text_scale)
    fmt = spec["format"]
    yield "say", f"Wrote {len(files)} Fusion comps ({sum(f['frames'] for f in files)} frames total)"

    # Placeholder clip each scene is trimmed from; its Fusion comp replaces the picture entirely.
    secs = max(f["frames"] for f in files) / fmt["fps"] + 1
    ph = job_dir / f"refcut_placeholder_{fmt['width']}x{fmt['height']}_{fmt['fps']}.mp4"
    if not ph.exists():
        ff = shutil.which("ffmpeg") or "ffmpeg"
        r = subprocess.run([ff, "-y", "-f", "lavfi", "-i",
                            f"color=c=black:s={fmt['width']}x{fmt['height']}:r={fmt['fps']}",
                            "-t", f"{secs:.2f}", "-c:v", "libx264", "-tune", "stillimage",
                            "-pix_fmt", "yuv420p", str(ph)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode:
            yield "err", "ffmpeg could not make the placeholder clip: " + r.stderr[-300:]
            return
    yield "tool", "refcut/kinetic → Resolve (project, timeline, clips, comps)"
    body = {"project": project_name, "format": fmt, "placeholder": str(ph),
            "scenes": [{"name": f["name"], "frames": f["frames"], "comp": f["path"]} for f in files],
            "music": music_path or None}
    req = urllib.request.Request("http://127.0.0.1:9876/refcut/kinetic", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            res = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        hint = " — restart Resolve and re-run CursorBridge so it picks up the RefCut endpoint" if e.code == 404 else ""
        yield "err", f"Bridge HTTP {e.code}: {detail}{hint}"
        return
    except Exception as e:
        yield "err", f"Bridge call failed: {e}"
        return
    if res.get("error"):
        yield "err", res["error"]
    for line in res.get("log", []):
        yield "say", line
    for sc in res.get("scenes", []):
        yield ("tool" if sc["comp_imported"] else "err"), f"{sc['scene']}: comp {'attached' if sc['comp_imported'] else 'FAILED'}"
    if res.get("success"):
        yield "done", (f"Built {res.get('clips')} scenes. Resolve is on the Fusion page — select a clip on the "
                       "timeline to see its node graph; every keyframe is editable in the Spline editor.")
    else:
        yield "err", "Some comps failed to attach. You can import them by hand: Fusion page → File → Import → "\
                     "Composition, using the files from 'Download comps'."


def build_in_resolve(job_dir, blueprint, footage, project_name):
    """Generator of (kind, text) events while Claude drives Resolve."""
    job_dir = Path(job_dir).resolve()
    cfg = job_dir / "mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {"davinci-resolve": {
        "command": sys.executable, "args": [str(MCP_SERVER)]}}}), encoding="utf-8")
    paths = {c["id"]: c["path"] for c in (footage or {}).get("clips", [])}
    st = blueprint.get("style", {})
    prompt = BUILD_PROMPT.format(blueprint=json.dumps(blueprint, indent=1), paths=json.dumps(paths, indent=1),
                                 project=project_name, w=st.get("width", 1920), h=st.get("height", 1080),
                                 fps=st.get("fps", 30))
    proc = subprocess.Popen(
        [claude_bin(), "-p", "--mcp-config", str(cfg), "--strict-mcp-config",
         "--allowedTools", "mcp__davinci-resolve", "--output-format", "stream-json", "--verbose"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=job_dir,
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
            yield "log", line
            continue
        t = ev.get("type")
        if t == "assistant":
            for block in ev.get("message", {}).get("content", []):
                if block.get("type") == "text" and block["text"].strip():
                    yield "say", block["text"]
                elif block.get("type") == "tool_use":
                    name = block["name"].replace("mcp__davinci-resolve__", "")
                    yield "tool", f"{name} {json.dumps(block.get('input', {}))[:200]}"
        elif t == "user":
            for block in ev.get("message", {}).get("content", []):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    c = block.get("content")
                    txt = c if isinstance(c, str) else json.dumps(c)
                    if block.get("is_error") or '"error"' in txt:
                        yield "err", txt[:300]
        elif t == "result":
            yield "done", ev.get("result", "")
    proc.wait()
    if proc.returncode:
        yield "err", f"claude exited with code {proc.returncode}"
