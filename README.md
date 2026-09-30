# RefCut

**Show it a reference, add a line of context — RefCut builds it in DaVinci Resolve.**

RefCut is a local web app that connects **Claude** (the brain) to **DaVinci Resolve** (the editor):

- **Kinetic text** — Apple-style, text-only launch videos. Claude writes a motion script, you preview and
  tweak it in the browser, and RefCut builds it as **keyframed Fusion comps** (one per scene) on a Resolve
  timeline, synced to your music.
- **Edit footage** — drop a reference edit and point at your clips. RefCut measures the reference
  (cuts, beat sync, pacing, colour, text, speech), Claude maps your footage onto the same recipe, and it's
  cut into a Resolve timeline.

Works with **DaVinci Resolve Free** (no Studio licence needed) and your normal **Claude subscription**
(no API key).

```
 ┌──────────── your Windows PC ─────────────────────────────────────────────────┐
 │                                                                              │
 │   Browser ──► RefCut (localhost:7860) ──► claude -p  (your Claude login)     │
 │                    │                                                         │
 │                    └──► CursorBridge (localhost:9876, runs INSIDE Resolve)   │
 │                              └──► DaVinci Resolve API → project, timeline,   │
 │                                   clips, Fusion comps                        │
 └──────────────────────────────────────────────────────────────────────────────┘
```

Everything runs on one machine. Nothing is exposed to the network — both servers listen on `127.0.0.1` only.

---

## Contents

1. [Requirements](#1-requirements)
2. [Install DaVinci Resolve](#2-install-davinci-resolve)
3. [Install and log in to Claude Code](#3-install-and-log-in-to-claude-code)
4. [Get RefCut and run setup](#4-get-refcut-and-run-setup)
5. [Connect RefCut to DaVinci Resolve](#5-connect-refcut-to-davinci-resolve)
6. [Start RefCut and check the status lights](#6-start-refcut-and-check-the-status-lights)
7. [Make your first kinetic-text video](#7-make-your-first-kinetic-text-video)
8. [One-time calibration (text size + font)](#8-one-time-calibration-text-size--font)
9. [Edit-footage mode](#9-edit-footage-mode)
10. [Every-day workflow (cheat sheet)](#10-every-day-workflow-cheat-sheet)
11. [Troubleshooting](#11-troubleshooting)
12. [How it works](#12-how-it-works)

---

## 1. Requirements

| | |
|---|---|
| OS | Windows 10/11 (64-bit). macOS/Linux work for the web app; setup scripts are Windows-only. |
| DaVinci Resolve | 18 or newer — **Free or Studio** |
| Claude | A Claude account (Pro/Max) for Claude Code |
| Disk | ~3 GB for Python packages (PyTorch CPU, Whisper) + Resolve itself |
| Internet | Needed for setup and for Claude; editing itself is local |

Setup installs these automatically if missing: **Python 3.12**, **ffmpeg** (via `winget`).

---

## 2. Install DaVinci Resolve

1. Download from <https://www.blackmagicdesign.com/products/davinciresolve> (the free version is fine).
2. Install and open it once, then create or open any project so it finishes first-run setup.
3. Close Resolve for now — setup needs to copy a script into Resolve's folders.

---

## 3. Install and log in to Claude Code

RefCut talks to Claude through the Claude Code CLI, using your normal Claude login.

1. Open **PowerShell** and run:
   ```powershell
   irm https://claude.ai/install.ps1 | iex
   ```
2. Close and reopen PowerShell, then run:
   ```powershell
   claude
   ```
3. Follow the login prompt in your browser. When Claude Code shows its prompt, type `/exit`.

That's it — RefCut runs `claude` in the background with this login.

---

## 4. Get RefCut and run setup

**Option A — with git**
```powershell
cd $HOME\Desktop
git clone https://github.com/kingzion24/refcut.git
cd refcut
```

**Option B — without git:** on GitHub click **Code → Download ZIP**, unzip it to e.g. `Desktop\refcut`.

Then **double-click `setup.bat`** (or run `powershell -ExecutionPolicy Bypass -File setup.ps1`). It:

1. Installs Python 3.12 and ffmpeg with `winget` if they're missing.
2. Creates a private Python environment in `refcut\venv` and installs the packages
   (CPU-only PyTorch, so no multi-GB CUDA download).
3. **Copies the bridge script into Resolve**:
   `%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility\CursorBridge.py`
4. Registers the DaVinci MCP server with Claude Code (optional bonus — lets you drive Resolve from a
   `claude` terminal session too).

It takes 5–15 minutes the first time. When it prints **Done**, continue.

> If setup says ffmpeg was installed but isn't on PATH, just close the window and open a new one —
> Windows only picks up PATH changes in new terminals.

---

## 5. Connect RefCut to DaVinci Resolve

Resolve Free doesn't allow outside programs to control it, **but it does run scripts from its own
Workspace menu**. RefCut ships a small script, **CursorBridge**, that you start from inside Resolve; it
opens a local connection (`127.0.0.1:9876`) that RefCut uses to build projects, timelines and Fusion comps.

**Every time you open Resolve:**

1. Open DaVinci Resolve and open (or create) any project.
2. In the top menu: **Workspace → Scripts → CursorBridge**.
3. Resolve's Console opens and prints:
   ```
   [CursorBridge] Connected to Resolve: DaVinci Resolve 19.x
   [CursorBridge] Bridge is running (read + write)
   ```
4. You can close the Console window — the bridge keeps running until you quit Resolve.

**Don't see CursorBridge in the menu?**
- Restart Resolve (it scans the Scripts folder at startup).
- Check the file exists at `%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility\CursorBridge.py`
  (paste that path into Explorer's address bar). If not, re-run `setup.bat`.
- Resolve needs a system Python 3 to run `.py` scripts — setup installs Python 3.12. If the Console says it
  can't find Python, install Python 3.12 (64-bit) from python.org with **"Add to PATH"** ticked, then restart Resolve.

---

## 6. Start RefCut and check the status lights

**Double-click `start.bat`.** A terminal window opens (leave it running) and your browser opens
<http://localhost:7860>.

Top-right of the page are two status lights. Both should be **green** before you build:

| Light | 🟢 Green | 🟠 Amber | 🔴 Red |
|---|---|---|---|
| **Claude** | Installed, logged in, answered a test call. Shows the version. | Error talking to Claude | Not installed / not logged in |
| **DaVinci** | Bridge connected. Shows your open project name. | Resolve is open but the bridge isn't started | Resolve isn't running |

**Click a light** to see exactly what's wrong and how to fix it, plus a **Re-check** button. The lights refresh
every few seconds, so after you start CursorBridge the DaVinci light turns green on its own.

You can plan and preview with only Claude green; DaVinci only needs to be green when you click **Build it**.

---

## 7. Make your first kinetic-text video

1. Pick the **Kinetic text** tab (left panel).
2. **Reference video** *(optional)* — drop a video you like the style of, or paste a TikTok / YouTube /
   Instagram link. RefCut samples frames through it so Claude can see how text enters, holds and exits.
3. **Context** — a line or two is enough:
   > Launch video for Flowdesk, an AI inbox for small teams. ~15s, calm opening then punchy beat cuts,
   > end on "Available today."
4. **Music to sync to** *(optional)* — choose a file or paste its path. RefCut finds the tempo and beats,
   and Claude lands scene changes on them.
5. **Format / FPS / Motion** — e.g. 16:9, 30, **Auto** (or **Smooth**, or **Stop-motion** = animation on twos).
6. Click **Analyze & plan**. After ~1–2 minutes you get:
   - **Preview** — press ▶ (or space). It plays the exact keyframes and easing curves that go into Fusion,
     with your music. Click scene chips to jump.
   - **Scenes** — edit any line's text, size, entrance/exit animation, timing and colour. Add, duplicate,
     reorder or delete scenes. The preview updates as you type.
   - **Look** — background, text and accent colours, font, weight, smooth vs stop-motion.
   - **Adjust** — ask in plain words: *"make it faster"*, *"stop-motion"*, *"add a scene about security"*.
7. Make sure the **DaVinci** light is green, optionally type a project name, and click **Build it**.

What Resolve gets:
- A project (created, or reused if the name exists) set to your resolution and frame rate.
- A new timeline with **one clip per scene** (orange), a blue marker per scene, and your music on A1.
- Each clip carries its own **Fusion comp**: `Text+ → Blur → Transform → Merge`, with keyframed opacity,
  blur, scale, position, rotation and tracking (and a Text Follower for letter-by-letter cascades).
- Resolve switches to the **Fusion page** — click a clip to see its nodes; every keyframe is editable in the
  Spline editor.

Prefer to do it by hand? **Download .comp files** and in Fusion use **File → Import → Composition**.

---

## 8. One-time calibration (text size + font)

Fusion's Text+ size unit isn't documented, so RefCut starts from a best estimate. After your **first**
build, compare text size in Resolve with the RefCut preview:

- Open **Fusion settings** (bottom of the left panel).
- **Text size calibration**: if text in Resolve is 2× too big, halve the number; 20% too small, multiply by
  1.2 — then **Save** and **Build it** again. You only do this once.
- **Default font**: must be installed in Windows, and the weight names (Regular / Semibold / Bold) must
  exist in that font. Segoe UI works out of the box. For the closest Apple look install
  [Inter](https://rsms.me/inter/) (right-click the .ttf files → **Install for all users**), restart Resolve,
  then set the font to `Inter`.

(Apple's own SF Pro is licensed for Apple platforms only.)

---

## 9. Edit-footage mode

1. Pick the **Edit footage** tab.
2. Drop a reference edit (or paste a link) and enter your **footage folder** (e.g. `D:\Footage\Trip`).
3. Add context → **Analyze & plan**. You get the recipe (pacing, cutting, look, text, music), a colour-coded
   timeline with beat ticks, and a shot-by-shot table matching each reference shot to one of your clips.
4. Adjust in plain words, then **Build it**. Resolve gets project settings, imported media, every cut with
   in/out points on the beat grid, music, markers and a CDL grade per clip.

The Resolve scripting API can't add transitions, keyframes/speed ramps, or type text into titles — the
blueprint lists those under *"You'll finish by hand"*.

---

## 10. Every-day workflow (cheat sheet)

```
1. Open DaVinci Resolve → open a project
2. Workspace → Scripts → CursorBridge
3. Double-click start.bat          (browser opens localhost:7860)
4. Both lights green? → make things → Build it
```

To update RefCut later: `git pull` in the folder (or download the ZIP again), then run `setup.bat` again and
**restart Resolve** so it loads the newest CursorBridge.

---

## 11. Troubleshooting

| Problem | Fix |
|---|---|
| **Claude** light red: *not installed* | Step 3, then click the light → Re-check. |
| **Claude** light red: *not logged in* | Run `claude` in PowerShell and log in, then Re-check. |
| **DaVinci** light red | Resolve isn't open. Open it + a project. |
| **DaVinci** light amber | Resolve is open but the bridge isn't: **Workspace → Scripts → CursorBridge**. |
| Build error mentions **HTTP 404** / RefCut endpoint | Resolve is running an old CursorBridge. Re-run `setup.bat`, **restart Resolve**, start CursorBridge again. |
| "Port 9876 already in use" in Resolve Console | An old bridge is still running — restart Resolve. |
| Text in Resolve is the wrong size | [Calibrate](#8-one-time-calibration-text-size--font) once. |
| Text shows in the wrong font in Resolve | The font or weight name isn't installed in Windows. Install it for all users, restart Resolve, or pick Segoe UI. |
| Timeline frame rate didn't change | Resolve won't change fps on a project that already has media. Use a new project name. |
| "Analyze & plan" is slow | First run downloads the Whisper model. Untick *Transcribe speech* for music-only references. |
| Link download fails | Some sites block downloading — save the video and drop the file instead. |
| `start.bat` says run setup first | Run `setup.bat`; if it failed, read its output (usually winget/Python). |

Logs: the `start.bat` window shows server errors. Each job's files (frames, comps, blueprint) are in
`refcut\jobs\<job-id>\`.

---

## 12. How it works

```
refcut/
├─ app.py          FastAPI server: jobs, status lights, preview, build
├─ analyze.py      ffmpeg cut detection · beat/energy (librosa) · colour stats · Whisper · contact sheets
├─ brain.py        claude -p prompts: motion script (kinetic) / edit blueprint (footage) / refine / build
├─ kinetic.py      motion script → keyframe tracks → Fusion .comp files (same tracks drive the preview)
├─ static/         index.html (UI) · kinetic.js (live preview player)
├─ vendor/davinci-resolve-mcp/
│    ├─ src/CursorBridge.py         runs inside Resolve; HTTP API on 127.0.0.1:9876 (+ /refcut/kinetic)
│    └─ src/resolve_mcp_bridge.py   MCP server used for footage builds and Claude Code
├─ setup.bat / setup.ps1   one-time install
└─ start.bat               launch
```

- **Kinetic builds are deterministic**: RefCut writes one `.comp` per scene and a black placeholder clip,
  then a single bridge call creates the project/timeline, lays out one clip per scene and attaches each comp.
- **Footage builds** are driven by Claude through the DaVinci MCP tools (import, insert with in/out points,
  markers, CDL grades), with the build log streamed into the UI.
- Fusion comp format (keyframe splines with absolute bezier handles, XYPath, Text Follower, `GlobalOut`,
  `UseFrameFormatSettings`) was checked against comps saved by Resolve itself.

### Credits

`vendor/davinci-resolve-mcp` is [hiteshK03/davinci-resolve-mcp](https://github.com/hiteshK03/davinci-resolve-mcp)
(MIT licence), pinned to `mcp<2`, with an added `/refcut/kinetic` endpoint in `CursorBridge.py`.

## License

MIT — see [LICENSE](LICENSE). The bundled `vendor/davinci-resolve-mcp` keeps its own MIT licence ([vendor/davinci-resolve-mcp/LICENSE](vendor/davinci-resolve-mcp/LICENSE)).
