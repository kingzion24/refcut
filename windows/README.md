# RefCut

Describe a video; Claude plans it, you preview and adjust it in the browser, and RefCut builds it in
DaVinci Resolve (the free version is fine). Three modes, chosen at the top of the left panel:

- **Talking video**: dev logs, feature updates and long YouTube stories. Put your footage on a Resolve
  timeline and describe the video; Claude cuts it, with cards, callouts and captions, on a new timeline.
- **Kinetic text**: Apple-style text-only videos, built as editable Fusion animations.
- **Mascot video**: faceless explainers. Claude writes the script, the built-in voice speaks it, Mage hosts.

(*Match a reference edit* is a link under the mode bar.)

## Install on Windows

You need Windows 10 or 11, an internet connection, and about 10 GB of free disk space. The whole install
takes 20–40 minutes, most of it downloads.

**1. Install DaVinci Resolve** (version 18 or newer; the free one is fine) from
<https://www.blackmagicdesign.com/products/davinciresolve>. Open it once, create any project, then close it.

**2. Install Claude Code.** Open **PowerShell** (Start menu → type "PowerShell") and run:

```powershell
irm https://claude.ai/install.ps1 | iex
```

Close PowerShell, open it again, and run `claude`. Log in with your Claude account in the browser window
that opens. When Claude Code shows its prompt, type `/exit`. If it asks for **Git for Windows**, install that
from <https://git-scm.com/download/win> (default options) and run `claude` again.

**3. Put this folder somewhere permanent.** Unzip `refcut-windows.zip` so you have a `refcut` folder, for
example `C:\Users\<you>\Desktop\refcut`. Don't run it from inside the zip.

**4. Run setup.** Make sure Resolve is closed, open the `refcut` folder and double-click **`setup.bat`**.
If Windows shows "Windows protected your PC", click **More info → Run anyway**. Setup installs, when missing:

| What | Size |
|---|---|
| Python 3.12, ffmpeg, Node.js (through `winget`, may ask for permission) | small |
| RefCut's Python packages, in `refcut\venv` | ~3 GB |
| The script that lets RefCut control Resolve (CursorBridge) | tiny |
| RefCut's bundled Claude skills | tiny |
| The motion B-roll renderer | ~150 MB |
| The voice model for mascot videos | 3.3 GB |

Wait until it prints **Done**, then press a key to close the window. If it says ffmpeg or Node.js was
installed but can't be found, close the window and double-click `setup.bat` once more.

**5. Connect Resolve.** Open DaVinci Resolve and a project. In the top menu choose
**Workspace → Scripts → CursorBridge**. A console window says `Bridge is running`; you can close that
console. Do this each time you open Resolve. If CursorBridge isn't in the menu, restart Resolve.

**6. Start RefCut.** Double-click **`start.bat`**. A black window opens (leave it open) and your browser
opens <http://localhost:7860>. Top right, the **Claude** and **DaVinci** lights should turn green within a
few seconds. Click a light to see what's wrong if it doesn't.

**7. For mascot videos, make Mage's voice (once).** Click the **Voice** light, choose gender, age and pitch,
and click **Create voice**. Press ▶ to listen and ↻ for another take.

That's it. Each later session is: open Resolve → **Workspace → Scripts → CursorBridge** → `start.bat`.

### If something goes wrong

| Problem | Fix |
|---|---|
| `setup.bat` says `winget` isn't recognised | Install **App Installer** from the Microsoft Store, then run `setup.bat` again |
| `start.bat` says "RefCut isn't set up yet" | `setup.bat` didn't finish. Run it again and read the last lines it prints |
| **Claude** light red | Run `claude` in PowerShell and log in, then click the light → **Re-check** |
| **DaVinci** light amber | Resolve is open but the bridge isn't: **Workspace → Scripts → CursorBridge** |
| **DaVinci** light red | Resolve isn't open |
| **Voice** light red | The voice engine didn't install: run `setup.bat` again, then restart `start.bat` |
| Page won't open | Is the black `start.bat` window still open? Open <http://localhost:7860> yourself |

### Updating later

Unzip the new `refcut-windows.zip` over the old `refcut` folder (your jobs, voices and settings are kept),
run **`setup.bat`** again, and in Resolve run **Workspace → Scripts → CursorBridge** again so it loads the
new bridge.

### Removing it

Delete the `refcut` folder. To also remove the bundled skills from Claude, delete `motion-broll` and
`agent-reach` in `%USERPROFILE%\.claude\skills\`. The voice model is in
`%USERPROFILE%\.cache\huggingface\hub\models--k2-fsa--OmniVoice`.

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

## How it's put together

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

