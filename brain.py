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


CHARACTER_VOCAB = """## Mage, the Mali Daftari mascot (a motion character)
Mage is Mali Daftari's AI assistant: a round brand-blue (#0077B6) head with ONLY eyes and nerdy browline
glasses. Personality: sharp, professional, a little nerdy charm. No mouth, no body, no limbs — all
expression is eyes + glasses + how the head moves. Motion is small, quick and precise (not bouncy cartoon).
Add Mage as an element with "kind": "mage" (text fields don't apply). It can share a scene with text.
{{"kind": "mage", "x": 0.5, "y": 0.5, "size": 0.32,          // head diameter as a fraction of frame height
  "variant": "primary",                                       // "reversed" = white head, only on brand-blue backgrounds
  "mood": "idle",                                             // starting mood
  "in": {{"type": "pop", "at": 0.0, "dur": 0.45}},            // pop | peek | rise | drop | fade | grow | slide_left | slide_right | cut
  "out": {{"type": "pop_out", "at": 2.6, "dur": 0.3}},        // pop_out | sink | fade | shrink | drop | slide_left | slide_right | cut | null
  "beats": [{{"at": 0.5, "mood": "happy"}}, {{"at": 1.2, "do": "signature"}}],
  "moves": [{{"at": 1.8, "dur": 0.5, "x": 0.25, "y": 0.5, "size": 0.2, "ease": "expo_out"}}]}}
Moods: idle | thinking (eyes up-right, waiting/pondering) | talking (eyes squash, pushes glasses up
periodically — use while a line of text "is Mage speaking") | happy (upturned arcs: success, a sale, profit
up, "it works") | focused (squint: reading, crunching numbers) | confused (glasses slip and tilt: a bug,
an error, offline) | searching (eyes dart side to side: loading, looking for something).
Beat actions ("do"): blink | hop (quick happy squint + glasses hop, a reaction) | glance (with "dir": -1 left /
1 right, look toward text) | push (push glasses up the nose) | glasses_off | glasses_on | signature (glasses
lift off, blink, come back ~1.4 s later — Mage's signature moment; use at most once per video).
Mage blinks and glances by itself. Moves glide Mage to a new x / y / size (e.g. from centre to a corner
to make room for a headline). Keep Mage inside the frame and clear of text (size 0.18–0.45 in a card,
0.12–0.2 as a corner reaction over footage). Never call it "Mali AI"; the name is Mage.
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
in.type:  fade | rise | drop | blur_in | punch | grow | pop | peek | slide_left | slide_right | track_in | tilt_in | cascade | cut
out.type: fade | rise | drop | blur_out | shrink | punch_out | pop_out | sink | slide_left | slide_right | track_out | cut   (or out = null to hold to scene end)
ease:     expo_out | quart_out | back_out | in_out | in | linear
Times are seconds relative to the scene start. cascade uses in.stagger (seconds between letters, 0.015–0.05).
Coordinates: x, y are the text centre as fractions of the frame (0,0 = top-left). Multi-line text: use "\\n".
Multiple elements in one scene can be staggered with different in.at values (e.g. a headline, then a sub-line).
Stacked lines: keep centres at least 0.7 × (size_a + size_b) + 0.02 apart in y so descenders never touch the next line.

{characters}
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


def _characters_block(use_mage):
    if use_mage:
        return CHARACTER_VOCAB.format() + ("Feature Mage as the guide of the video: give it a short intro, react to the "
                                           "key moments with moods / beats, and bring it back for the ending.\n")
    return "(No characters in this video — text only, no \"kind\": \"mage\" elements.)\n"


def make_motion_script(job_dir, context, reference, music, fmt, font, motion_pref, use_mage=False):
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
        w=fmt["width"], h=fmt["height"], fps=fmt["fps"], font=font, characters=_characters_block(use_mage))
    out = _run_claude(["--output-format", "json", "--allowedTools", "Read"], prompt, job_dir)
    return _extract_json(out["result"]), out.get("session_id")


MASCOT_PROMPT = """You write and direct short FACELESS explainer videos hosted by a mascot: there is no camera
footage and no human on screen. A generated voice narrates; Mage (the mascot) is on screen as the one
speaking; kinetic text, timed to the spoken words, carries the key points. Think of the best app-explainer
shorts: one clear idea, said simply, shown as it is said. RefCut generates the voice from your script,
previews it live and builds it in DaVinci Resolve. The user explains very little — infer the rest.

## User's context (may include a full script — if it does, use their words, split into scenes)
{context}

## Output format
{fmt} · target length: {target}

## Narration
{language}
- Write for the EAR: short sentences, plain words, contractions. One idea per scene, one or two
  sentences (roughly 5–16 words, 2–6 s). No lists read aloud, no "in this video".
- The voice reads exactly what you write in "say": spell things the way they should be spoken (numbers as
  words, no symbols, no emoji, no markdown; "Mali Daftari", not "MD").
- Shape: hook (a problem or a surprising claim, first line) → what it is → how it works / why it matters
  (2–5 scenes) → proof or example → what to do next. About 2.6 words per second.
- Never invent facts, numbers, prices or user quotes about the product. Use only what the context gives.

## Picture
- Every spoken scene has Mage (`"kind": "mage"`, `"speaks": true`): RefCut makes its eyes move with the
  voice. Give Mage a mood that fits the line as a beat on the word where the feeling lands (confused at the
  problem, happy at the payoff, thinking, searching…), and move it around: centre stage for the hook,
  gliding to a side or corner when text needs the room.
- On-screen text is NOT a transcript (captions are added automatically, low in the frame — keep y between
  0.12 and 0.72 for your own elements). Show 1–5 key words: the claim, the feature name, the number. Bring
  each one in on the word where it is spoken.
- Timing is by WORD, not seconds: inside an element's "in" / "out", a Mage beat or a Mage move, write
  `"word": n` = when the n-th word of this scene's "say" is spoken (0-based; words are split on spaces).
  Omit "out" to hold to the end of the scene. Do not write "dur" for spoken scenes (the voice sets it).
- A scene without "say" is a silent beat: give it "dur" (0.6–1.5 s) and use seconds ("at") in it.
- "pause": silence after the line (default 0.4 s; 0.15 to keep momentum, 0.8 to let a point land).
- Backgrounds: one palette for the whole video (brand colours from the context if given); change the
  background only to mark a new section.

## Kinetic element format
{{"text": "Works offline", "x": 0.5, "y": 0.3, "size": 0.07, "weight": "Bold", "color": "color", "tracking": -0.02,
 "in": {{"type": "rise", "word": 4, "dur": 0.5, "ease": "expo_out"}}, "out": {{"type": "fade", "word": 9, "dur": 0.3, "ease": "in"}}}}
size = cap height as a fraction of frame height (headline 0.06–0.12, supporting 0.03–0.045). x, y = centre as
fractions of the frame (0,0 top-left). "color": "color" | "accent" | hex. Multi-line text: "\n".
Stacked lines: keep centres at least 0.7 × (size_a + size_b) + 0.02 apart in y. Keep text clear of Mage
(head radius = size / 2).
in.type:  fade | rise | drop | blur_in | punch | grow | pop | peek | slide_left | slide_right | track_in | tilt_in | cascade | cut
out.type: fade | rise | drop | blur_out | shrink | punch_out | pop_out | sink | slide_left | slide_right | track_out | cut | null
ease:     expo_out | quart_out | back_out | in_out | in | linear

{characters}
In Mage beats and moves you may write "word": n instead of "at".

Respond with ONLY one JSON object in a ```json fence:
{{
  "title": "short name",
  "intent": "2-3 sentences: what this video says, who it's for, the tone",
  "format": {{"width": {w}, "height": {h}, "fps": {fps}}},
  "style": {{"background": "#000000", "color": "#F5F5F7", "accent": "#2997FF", "font": "{font}", "weight": "Semibold",
            "motion": "smooth", "easing": "expo_out"}},
  "scenes": [
    {{"name": "Hook", "say": "Your shop loses sales every time the internet drops.", "lang": "en", "pause": 0.3,
      "background": null,
      "elements": [
        {{"kind": "mage", "speaks": true, "x": 0.5, "y": 0.36, "size": 0.3, "mood": "idle",
          "in": {{"type": "pop", "at": 0.0, "dur": 0.45}},
          "beats": [{{"word": 7, "mood": "confused"}}]}},
        {{"text": "Internet down?", "x": 0.5, "y": 0.62, "size": 0.07, "weight": "Bold",
          "in": {{"type": "rise", "word": 6, "dur": 0.5}}}}
      ]}}
  ],
  "notes": ["anything the user should know"],
  "questions": []
}}
("lang" per scene: "en" or "sw" — the language that line is spoken in.)
"""


def make_mascot(job_dir, context, fmt, font, target, language):
    lang_rule = {
        "en": "Language: English.",
        "sw": "Language: Kiswahili — natural, everyday Kiswahili as spoken in East Africa, not textbook-formal. "
              "On-screen text in Kiswahili too. Set \"lang\": \"sw\" on every scene.",
        "en+sw": "Language: a natural mix of English and Kiswahili, the way East African creators talk: each scene's line "
                 "is in ONE language (set its \"lang\"), and the video moves between the two. Keep product and tech "
                 "terms in English. On-screen text follows the line's language.",
    }.get(language, "Language: the language of the user's context (English if unclear).")
    prompt = MASCOT_PROMPT.format(
        context=(context or "").strip() or "(none)", fmt=f"{fmt['width']}x{fmt['height']} @ {fmt['fps']} fps",
        target=target or "auto — 20 to 45 seconds", language=lang_rule, characters=CHARACTER_VOCAB.format(),
        w=fmt["width"], h=fmt["height"], fps=fmt["fps"], font=font)
    out = _run_claude(["--output-format", "json", "--allowedTools", "Read"], prompt, job_dir)
    return _extract_json(out["result"]), out.get("session_id")


YOUTUBE_STYLE = """
## Style: long-form YouTube story (documentary vlog) — this overrides the short-form advice above
This is a watch-time video (several minutes, 16:9), not a short: a founder / builder telling a story, like the
best "how we built it" channel videos. Let it breathe, but never let the picture sit still for long.
- **Cold open (first 15–40 s):** the one or two strongest lines of the whole video as voice, covered by a fast
  montage of the most visual B-roll (each cutaway 1–2.5 s), then a title card. Then start the story.
- **Spine:** the talking footage, cut for story. Whole sentences, natural breaths kept, filler and retakes out.
  Zoom stays 1.0 here; cutaways hide the jump cuts instead of punch-ins.
- **Cutaways** (`"cutaways"`): the creator's own B-roll shown OVER the voice. The talking clip's audio keeps
  playing underneath, so a cutaway never changes the timing. Take B-roll from the sources with no speech (and
  from any moment of a source that shows what is being described). Each one 1.5–5 s, usually two to four in a row,
  starting on the sentence they illustrate. Cover roughly 35–55 % of the talking; leave personal, emotional and
  punch-line sentences on the face. Never reuse the same B-roll range twice.
  {{"name": "Office — typing", "anchor": "t4", "at": 1.2, "source": "S3", "in": 14.0, "out": 17.5, "inset": null}}
  ("at" = seconds after the anchor item starts; in/out = seconds in the B-roll source.)
- **Inset frames:** `"inset": {{"scale": 0.62, "canvas": "#F4F3EF", "y": 0.47}}` shows the cutaway as a framed
  picture on a plain canvas instead of full screen. Use it in runs for flashbacks, old footage and screen
  recordings, so they read as "evidence"; keep normal cutaways full screen.
- **Text cards over the voice:** an overlay with `"background": "#F4F3EF"` is opaque: the screen becomes a plain
  canvas with words on it while the creator keeps talking. Put 2–6 key words of the sentence on it, each
  appearing as it is spoken: element "in"."at" = (the word's time in the source − the clip's "in") − the
  overlay's "at". Dark text (#111111) on the light canvas, size 0.035–0.06, "cut" or "fade" entrances.
  Use a few per chapter, on the lines that are ideas rather than events.
- **Big words over footage:** a normal (transparent) overlay with one short phrase at size 0.07–0.1 over a wide
  shot, for a number or a turning point ("15 terabytes…").
- **Chapters** (`"chapters"`): 4–8 for a 6–15 minute video, [{{"title": "The origin of the idea", "anchor": "t9"}}],
  the first on the first timeline item. Open each chapter (not the first) with a short chapter card (0.8–1.6 s)
  or a big title over B-roll. Titles are short, plain and curiosity-led; they become the YouTube chapter list.
- **Captions:** `"captions": {{"style": "subtle"}}` — small, quiet subtitles, not big pop captions.
- **Music:** a music bed is added under the whole video and lowered under speech automatically; don't plan it.
- **Rhythm:** {pacing}
"""


STORY_PROMPT = """You are a senior editor for creator-style tech content: dev logs ("building my app in public"),
feature walkthroughs and SaaS product videos. The user recorded long, rough footage (talking to camera
and/or screen recordings) and put it on a DaVinci Resolve timeline. Your job: turn it into a tight,
watchable edit. Write an EDIT PLAN (JSON) that RefCut builds in Resolve and previews live. The user
explains very little — infer what the video is about from the transcript and the frames.

## User's context
{context}

## Output
{fmt} · target length: {target}

## Footage (sources)
Each source is one clip from the user's timeline. Times are SECONDS IN THE SOURCE FILE; only the listed
range is usable. Phrases come from speech recognition (typos possible) with the pause after each; every
word is written word@start-time, so you can cut exactly before or after any word (e.g. to drop a leading
"Um, so," start the clip at the next word's time).
{sources}

Frames sampled through each source (labelled "S1 @12.0s") — READ THESE IMAGES with the Read tool to see what's
on screen (face cam vs screen recording, which app screens are shown):
{sheets}

## Editing craft
- HOOK first (first 1–3 s): the most striking line or result, as a clip cut out of order, or a card
  (big claim / question) — then get into it. No slow intros, no "hey guys, so today…".
- Cut hard: drop false starts, repeats, filler ("um", "so yeah", "basically"), dead air and tangents.
  Keep sentences whole. One timeline clip = one continuous range; a clip usually runs 2–12 s. Put in/out
  at phrase boundaries from the list; RefCut snaps each cut to the nearest word boundary.
- Talking head: alternate "zoom" between 1.0 and 1.08–1.15 on consecutive clips of the SAME source so
  jump cuts read as intentional punch-ins. Screen recordings: zoom 1.0 unless a detail must be read.
- Structure: hook → context/problem → what I built / how it works (the meat) → result/demo → takeaway / CTA.
  For a dev log, each "chapter" can open with a short card ("Day 12 · Offline sync").
- Cards are kinetic-typography scenes on their own (Apple-style: black or brand background, big
  confident type, one idea per card, 0.8–2.5 s). Use them for the hook, chapter titles and the ending.
- Overlays sit ON TOP of a clip: callouts naming a feature ("Offline-first sync"), key numbers, lower
  thirds, and Mage reactions. Anchor each to a timeline item id with "at" = seconds after that item
  starts. Keep them short (1.2–3 s) and away from the caption zone (captions sit at y≈{cap_y}).
  Overlays have a transparent background — write text with enough size/contrast to read over video.
- Don't overdo it: roughly one overlay every 5–10 s at most.

## Kinetic element format (cards and overlays), same as RefCut's kinetic mode
{{"text": "Offline-first", "x": 0.5, "y": 0.2, "size": 0.05, "weight": "Bold", "color": "color", "tracking": -0.01,
 "in": {{"type": "rise", "at": 0.0, "dur": 0.5, "ease": "expo_out"}}, "out": {{"type": "fade", "at": 2.0, "dur": 0.3, "ease": "in"}}}}
size = cap height as a fraction of frame height (captions ≈ 0.04, callouts 0.035–0.06, card headlines 0.07–0.14).
Stacked lines: keep centres at least 0.7 × (size_a + size_b) + 0.02 apart in y so descenders never touch the next line. Same for Mage: keep text clear of the head (head radius = size / 2).
x, y = centre as fractions of the frame (0,0 top-left). "color": "color" | "accent" | hex.
in.type:  fade | rise | drop | blur_in | punch | grow | pop | peek | slide_left | slide_right | track_in | tilt_in | cascade | cut
out.type: fade | rise | drop | blur_out | shrink | punch_out | pop_out | sink | slide_left | slide_right | track_out | cut | null
ease:     expo_out | quart_out | back_out | in_out | in | linear

{characters}{style}
Respond with ONLY one JSON object in a ```json fence:
{{
  "title": "short name",
  "intent": "2-3 sentences: what this video is, who it's for, and the edit's shape",
  "format": {{"width": {w}, "height": {h}, "fps": {fps}}},
  "style": {{"background": "#000000", "color": "#F5F5F7", "accent": "#2997FF", "font": "{font}", "weight": "Semibold",
            "motion": "smooth", "easing": "expo_out"}},
  "timeline": [
    {{"id": "t1", "type": "clip", "name": "Hook — it finally works", "source": "S1", "in": 184.2, "out": 188.9, "zoom": 1.1,
      "why": "strongest line, used out of order as the hook"}},
    {{"id": "t2", "type": "card", "name": "Title", "dur": 1.6, "background": null, "elements": [ ... ]}},
    {{"id": "t3", "type": "clip", "name": "The problem", "source": "S1", "in": 12.3, "out": 19.8, "zoom": 1.0, "why": "..."}}
  ],
  "overlays": [
    {{"name": "Callout — offline sync", "anchor": "t3", "at": 1.0, "dur": 2.4, "elements": [ ... ]}}
  ],
  "cutaways": [],
  "chapters": [],
  "captions": {{"y": {cap_y}, "size": 0.042, "max_words": 3}},
  "notes": ["anything the user should know: B-roll or screen recordings worth adding, lines worth re-recording"],
  "questions": []
}}
"""


def _sources_text(sources):
    out = []
    for sid, s in sources.items():
        lo, hi = s["range"]
        head = (f"### {sid} — {s['name']}  (usable {lo:.2f}–{hi:.2f}s, {(hi - lo) / 60:.1f} min · "
                f"{s.get('width')}x{s.get('height')} @ {s.get('fps')} fps"
                + (f" · on the user's timeline at {s['timeline_start']:.2f}s, track V{s.get('track', 1)}" if s.get("timeline_start") is not None else "")
                + ")")
        out.append(head)
        if s.get("phrases"):
            words = s.get("words") or []
            if len(words) > 2500:        # long footage: phrase-level times only (cuts still snap to words)
                words = []
            mixed = len({ph.get("lang") for ph in s["phrases"] if ph.get("lang")}) > 1
            for ph in s["phrases"]:
                pa = f"   (pause {ph['pause_after']:.1f}s)" if ph.get("pause_after") and ph["pause_after"] >= 0.6 else ""
                ws = [w for w in words if ph["start"] - 1e-3 <= w[0] <= ph["end"]]
                txt = " ".join(f"{w[2]}@{w[0]:.2f}" for w in ws) if ws else ph["text"]
                tag = f" ({ph['lang']})" if mixed and ph.get("lang") else ""
                out.append(f"[{ph['start']:.2f}–{ph['end']:.2f}]{tag} {txt}{pa}")
        else:
            out.append("(no speech found — B-roll or a silent screen recording; use it visually)"
                       + (f" [transcription error: {s['transcribe_error']}]" if s.get("transcribe_error") else ""))
        out.append("")
    return "\n".join(out)


def _reference_text(reference):
    """What the reference video measures as, for the edit to mirror."""
    if not reference:
        return "", ("about 14–18 cuts per minute overall; B-roll shots around 2–3 s; the cold open faster; "
                    "the last chapter slower.")
    p, a = reference.get("pacing", {}), reference.get("audio") or {}
    speech = (reference.get("speech") or {}).get("segments") or []
    txt = ("\n## Reference video to mirror (the user wants THEIR video to feel like this one)\n"
           f"{reference['video'].get('duration', 0) / 60:.1f} min · {reference.get('shot_count')} shots · "
           f"{p.get('cuts_per_minute')} cuts/min · median shot {p.get('median_shot_sec')} s · average shot per quarter "
           f"{p.get('avg_shot_by_quarter')} s" + (f" · music ~{a.get('tempo_bpm')} bpm" if a.get("tempo_bpm") else "") + "\n"
           "Contact sheets, one frame per shot (labelled #shot start (duration)) — READ THESE IMAGES to see how it uses "
           "B-roll, inset frames, text cards, titles and captions, then use the same devices with the user's footage:\n"
           + "\n".join(f"- {s}" for s in reference.get("contact_sheets", [])[:8])
           + ("\nHow it opens (its first lines): " + " ".join(s["text"] for s in speech[:12]) if speech else "") + "\n"
           "Mirror its structure, pacing and devices. Do not copy its words, topic or branding.\n")
    return txt, (f"match the reference: about {p.get('cuts_per_minute')} cuts per minute, median shot "
                 f"{p.get('median_shot_sec')} s, and the same change of pace from opening to ending.")


def make_story(job_dir, context, talk, fmt, font, target, use_mage, captions, style="short", reference=None):
    cap_y = 0.8 if fmt["height"] > fmt["width"] else 0.86
    ref_txt, pacing = _reference_text(reference)
    style_txt = (YOUTUBE_STYLE.format(pacing=pacing) if style == "youtube" else "") + ref_txt
    prompt = STORY_PROMPT.format(
        context=(context or "").strip() or "(none — infer it from the footage)",
        fmt=f"{fmt['width']}x{fmt['height']} @ {fmt['fps']} fps",
        target=target or ("auto — as long as the story holds attention (usually 4–12 minutes)" if style == "youtube"
                          else "auto — as short as the story allows (a dev log short: 30–90 s)"),
        style=style_txt,
        sources=_sources_text(talk["sources"]),
        sheets="\n".join(f"- {x}" for x in talk.get("contact_sheets", [])) or "(none)",
        characters=_characters_block(use_mage), cap_y=cap_y,
        w=fmt["width"], h=fmt["height"], fps=fmt["fps"], font=font)
    if captions == "off":
        prompt += "\n(Captions are off for this video, so the bottom of the frame is free for overlays.)\n"
    spoken = talk.get("spoken") or []
    if "sw" in spoken:
        prompt += ("\n## Language\nThe speaker talks in " + ("a mix of English and Kiswahili, switching between them freely"
                   if len(spoken) > 1 else "Kiswahili") + ". Speech recognition of Kiswahili is rough: words get split, "
                   "joined or misspelled (\"Teda anawesa\" for \"Mteja anaweza\"). Read through that to what was said.\n"
                   "- Add a `\"fixes\"` list to your JSON with the corrected text for every phrase YOU USE that was clearly "
                   "misheard: {\"source\": \"S1\", \"start\": <first word's time>, \"end\": <last word's end>, \"text\": "
                   "\"what was really said\"}. Keep the speaker's own words and language (never translate, never polish); "
                   "only fix recognition errors. Captions are made from these.\n"
                   "- Write cards and callouts in the language that suits the line they sit on; short English or Kiswahili, "
                   "the way the speaker would write it.\n")
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


def build_kinetic(job_dir, spec, project_name, music_path, text_scale, audio=None):
    """Deterministic: write one .comp per scene, then one bridge call lays them out in Resolve."""
    import kinetic
    import mage
    job_dir = Path(job_dir).resolve()
    spec, files = kinetic.write_all(spec, job_dir / "comps", text_scale)
    fmt = spec["format"]
    yield "say", f"Wrote {len(files)} Fusion comps ({sum(f['frames'] for f in files)} frames total)"
    # characters: one transparent PNG sequence per scene that has any, on V2 above that scene
    _, scenes = kinetic.scene_tracks(spec)
    step = spec["style"]["step_frames"] if spec["style"]["motion"] == "stepped" else 1
    layers, rec = [], 0
    for i, sd in enumerate(scenes, 1):
        chars = kinetic.characters(sd)
        if chars:
            yield "tool", f"Rendering Mage for scene {i} ({sd['frames']} frames)"
            seq = mage.render_sequence(chars, fmt["width"], fmt["height"], sd["frames"],
                                       job_dir / "characters" / f"scene_{i:02d}", step)
            layers.append({"kind": "sequence", "track": 2, "start": rec, "name": f"{sd['name']} · Mage"} | seq)
        rec += sd["frames"]

    try:
        ph = placeholder_clip(job_dir, fmt, max(f["frames"] for f in files))
    except RuntimeError as e:
        yield "err", str(e)
        return
    yield "tool", "refcut/kinetic → Resolve (project, timeline, clips, comps)"
    body = {"project": project_name, "format": fmt, "placeholder": str(ph),
            "scenes": [{"name": f["name"], "frames": f["frames"], "comp": f["path"]} for f in files],
            "layers": layers, "music": None if audio else (music_path or None),
            "audio": audio or []}     # mascot videos: [{path, track}] voice on A1, music bed on A2
    res = yield from _bridge_post("/refcut/kinetic", body)
    if res is None:
        return
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


def _bridge_post(path, body):
    """POST to CursorBridge; yields error events and returns the JSON (None on failure)."""
    req = urllib.request.Request("http://127.0.0.1:9876" + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            res = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        hint = " — restart Resolve and re-run CursorBridge so it picks up the RefCut endpoint" if e.code == 404 else ""
        yield "err", f"Bridge HTTP {e.code}: {detail}{hint}"
        return None
    except Exception as e:
        yield "err", f"Bridge call failed: {e}"
        return None
    if res.get("error"):
        yield "err", res["error"]
    return res


def placeholder_clip(job_dir, fmt, frames):
    """Black clip that scene clips are trimmed from; their Fusion comps replace the picture."""
    secs = frames / fmt["fps"] + 1
    ph = Path(job_dir) / f"refcut_placeholder_{fmt['width']}x{fmt['height']}_{fmt['fps']}_{int(secs)}s.mp4"
    if not ph.exists():
        ff = shutil.which("ffmpeg") or "ffmpeg"
        r = subprocess.run([ff, "-y", "-f", "lavfi", "-i",
                            f"color=c=black:s={fmt['width']}x{fmt['height']}:r={fmt['fps']}",
                            "-t", f"{secs:.2f}", "-c:v", "libx264", "-tune", "stillimage",
                            "-pix_fmt", "yuv420p", str(ph)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode:
            raise RuntimeError("ffmpeg could not make the placeholder clip: " + r.stderr[-300:])
    return ph


def build_story(job_dir, spec, sources, timeline_name, text_scale, captions, broll=None, music_path=None):
    """Deterministic: write comps / Mage sequences / captions, then one bridge call builds a new
    timeline in the project that's open in Resolve."""
    import story
    job_dir = Path(job_dir).resolve()
    yield "say", "Writing Fusion comps and rendering Mage…"
    spec, body = story.write_build(spec, sources, job_dir, text_scale, captions, broll, music_path)
    c = body.pop("counts")
    chapters = body.pop("chapters_text")
    yield "say", (f"{c['clips']} clips · {c['cards']} cards · {c['cutaways']} cutaways · {c['overlays']} overlays · "
                  f"{c['characters']} Mage clips · {c['broll']} motion B-roll clips · {c['captions']} captions · "
                  f"{c['chapters']} chapters · {c['seconds']}s")
    comps = [x for x in body["v1"] if x["kind"] == "comp"] + [x for x in body["layers"] if x["kind"] == "comp"]
    try:
        body["placeholder"] = str(placeholder_clip(job_dir, body["format"], max([x["frames"] for x in comps] or [1])))
    except RuntimeError as e:
        yield "err", str(e)
        return
    body["timeline"] = timeline_name
    yield "tool", "refcut/story → Resolve (timeline, cuts, cards, overlays, Mage, captions)"
    res = yield from _bridge_post("/refcut/story", body)
    if res is None:
        return
    for line in res.get("log", []):
        yield "say", line
    for line in res.get("failed", []):
        yield "err", line
    if res.get("success"):
        order = ", ".join(f"V{k} = {v}" for k, v in sorted(body["track_names"].items(), key=lambda kv: int(kv[0])))
        yield "done", (f"Built timeline “{res.get('timeline')}” in project “{res.get('project')}”. V1 = your cuts and cards, "
                       f"{order}. Text is live Text+ — fix any caption typo on the Fusion page."
                       + (" The music bed is on A2, already lowered under your voice." if body.get("audio") else ""))
        if chapters:
            yield "say", "YouTube chapters (paste into the description):\n" + chapters
    else:
        yield "err", "Some parts failed (see above). Everything that worked is on the new timeline."


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
