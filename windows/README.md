# RefCut

Show it a reference, add a line of context. RefCut works out the recipe and builds it in
DaVinci Resolve (Free). Three modes, chosen at the top of the left panel (plus *Match a reference edit* under the mode bar):

- **Kinetic text**: Apple-style text-only videos, built as keyframed **Fusion comps** (one per scene).
- **Talking video**: dev logs and feature walkthroughs. Put long footage on a Resolve timeline and
  describe the video; Claude cuts it tight with a hook, cards, callouts and captions, on a new timeline.
- **Mascot video**: faceless explainers. Claude writes the script, the built-in voice speaks it, Mage hosts.
- **Match a ref**: recreate a reference edit's pacing, beat sync, structure and look with your clips.

Tick **Feature Mage** to add Mali Daftari's mascot as a motion character in kinetic and talking videos.
**Add motion B-roll** (talking videos) has Claude design animated cutaways timed to your words, using the
bundled `motion-broll` skill.

```
Browser UI (localhost:7860)
  ├─ analyze.py   cuts · beats/energy · colour · Whisper · contact sheets (dense frames for kinetic refs)
  ├─ brain.py     claude -p → motion script / edit blueprint → chat to adjust
  ├─ kinetic.py   motion script → keyframe tracks → Fusion .comp files (+ the live browser preview)
  ├─ story.py     talking videos: edit plan + transcript → word-snapped cuts, cards, overlays, captions
  ├─ mage.py      Mage: moods, blinks, glasses → transparent PNG sequences
  ├─ mascot.py    mascot videos: voiced script → scenes timed to the voice
  ├─ voice.py     the built-in voice (OmniVoice): design a voice once, speak each line
  ├─ broll.py     motion B-roll: Claude + the bundled motion-broll skill → clips on their own track
  ├─ skills/      skills RefCut installs into this machine's Claude (skillset.py)
  └─ build        CursorBridge inside Resolve → project, timeline, clips, comps
```

## Setup (Windows)

1. Install DaVinci Resolve 18+ (Free is fine) and Claude Code (`irm https://claude.ai/install.ps1 | iex`,
   then run `claude` once to log in).
2. Copy this `refcut` folder to the machine and double-click **setup.bat**.
3. Double-click **start.bat**. The UI opens at http://localhost:7860.

**Updating from an older RefCut:** copy the new folder over the old one, run **setup.bat** again (it copies
the new CursorBridge into Resolve), then in Resolve run **Workspace → Scripts → CursorBridge** again.
Talking videos need this new bridge.

## Status lights (top right)

| Light | Green means | If not |
|---|---|---|
| **Claude** | Claude Code is installed, logged in, and answered a test call | click it for the fix (install / log in), then *Re-check* |
| **DaVinci** | Resolve is open and the bridge is running (shows the open project) | amber = Resolve open but bridge not started; red = Resolve not running |
| **Voice** | A voice is designed (only needed for mascot videos) | amber = click it and design a voice; red = run setup.bat again |

Start the bridge each Resolve session: **Workspace → Scripts → CursorBridge**.

## Kinetic text

1. Optional: drop a reference (file or TikTok/YouTube/Instagram link) and/or a music track to sync to.
2. Describe it in a line ("launch video for Flowdesk, an AI inbox. 15s, end on 'Available today.'").
3. Pick format / fps / motion (Auto, Smooth, or Stop-motion = animation on twos) → **Analyze & plan**.
4. Watch the live preview, edit scenes inline (text, size, in/out animation, timing, colours), or ask
   for changes in **Adjust**.
5. **Build it** → Resolve gets a project + timeline with one clip per scene, each carrying its Fusion comp,
   and opens the Fusion page. Every keyframe is editable in the Spline editor.
   **Download .comp files** gives you the comps to import by hand (Fusion → File → Import) if needed.

Animations: blur_in, rise, drop, fade, punch, grow, slide, track_in, tilt_in, cascade (per-letter via
Text Follower), cut — each with Apple-style easing (expo-out in, ease-in out).

**First run — calibrate once:** Fusion's Text+ size unit isn't documented, so compare the first build to
the preview. If text in Resolve is bigger/smaller, open *Fusion settings* in the sidebar and scale
"Text size calibration" by that ratio (2× too big → halve it). Also set your default font there
(it must be installed on Windows, e.g. Inter or Segoe UI; weights must match the font's style names).

## Talking video

1. In Resolve, put your raw footage (talking head and/or screen recordings) on a timeline.
2. In RefCut, open **Talking video**. *My Resolve timeline* shows what it will read (or use *Files / folder*).
3. Describe it ("Day 12 of building Mali Daftari: offline sync. TikTok short, Mage happy when it works"),
   pick format / length / captions, tick **Feature Mage** → **Analyze & plan**.
4. Preview plays your footage with every cut, card, callout, Mage and caption. Edit cuts in **Timeline**,
   or ask in **Adjust**.
5. **Build it** → a new timeline in the open project: V1 cuts + cards, V2 callouts, V3 Mage, V4 captions
   (editable Text+; with motion B-roll, that takes V2 and the rest move up), audio on A1. Your original timeline isn't touched.

If Mage shows a black box, set its clip's **Clip Attributes → Alpha mode → Straight**.

## YouTube story (long-form talking video)

In **Talking video** choose **YouTube story**. Put B-roll clips on the timeline as well as your talking
footage, optionally paste a **reference video** link to mirror and choose a **music bed**. Claude plans a cold
open, cutaways of your B-roll over your voice (some as inset frames on a canvas), text cards, chapters and
quiet subtitles. The build adds Canvas and B-roll tracks and puts the music on A2, already lowered under
your voice. RefCut shows the YouTube chapter list to paste into the description.

## English + Kiswahili

In **Talking video** set **You speak** to *English + Kiswahili, mixed*. For the best Kiswahili, open
*Fusion settings → Speech recognition* and pick *Best for Kiswahili* (1.6 GB download). Claude corrects
misheard phrases for the captions; burned captions stay editable in Resolve.

## Mascot video and the built-in voice

The voice is built into RefCut (the OmniVoice model, 3.3 GB, downloaded by setup; runs on your CPU). Nothing
else to install or keep open.

One time: click the **Voice** light, name the voice, pick gender / age / pitch, **Create voice**. Press ▶ to
listen and ↻ for another take until it's right. Every video then uses exactly that voice, in English and
Kiswahili.

Per video: **Mascot video** tab → describe the video or paste a script → pick voice, language and length →
**Analyze & plan**. Each scene shows the line it says; edit a line and click **Generate voice** to redo only
that line. **Build it** puts the scenes on V1, Mage on V2, the voice on A1 and music on A2. A line takes a
little while on a CPU. Write numbers as words.

## Motion B-roll and bundled skills

`setup.bat` (and every start of RefCut) installs the skills in `skills\` into this machine's Claude
(`%USERPROFILE%\.claude\skills\`), so RefCut and Claude Code can both use them. Your own skill of the
same name is never overwritten. Click the **Claude** light to see them.

In a talking video, once the cut is right: choose a density, click **Add motion B-roll**, and wait several
minutes (first run downloads a ~150 MB renderer). Claude plans, builds, checks and renders each clip; they
show in the preview and **Build it** puts them on V2, with callouts, Mage and captions above.
Needs Node.js, which setup installs. During this step Claude runs Node, Python and ffmpeg on this PC.

`skills/motion-broll` is from [Barty-Bart/motion-graphics](https://github.com/Barty-Bart/motion-graphics) (MIT).

Also bundled: `agent-reach` ([Panniantong/Agent-Reach](https://github.com/Panniantong/Agent-Reach), MIT), a guide that
helps Claude Code read the internet when you research a video. RefCut itself doesn't use it. See `skills/README.md`.

## Match a ref

Drop a reference, point at your footage folder, add context → review the blueprint → **Build it**.
Automatic: project settings, media import, cuts with in/out points on the beat grid, music, markers,
per-clip CDL grade. By hand: transitions, keyframes/speed ramps, title text.

## Notes

- Jobs live in `jobs/`, settings in `settings.json`. Delete old jobs freely.
- `vendor/davinci-resolve-mcp` is [hiteshK03/davinci-resolve-mcp](https://github.com/hiteshK03/davinci-resolve-mcp) (MIT),
  pinned to `mcp<2`, with added `/refcut/*` endpoints in `CursorBridge.py`.
