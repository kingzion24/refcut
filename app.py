"""RefCut — reference-driven editing for DaVinci Resolve. Local web UI."""
import json
import os
import shutil
import subprocess
import urllib.request
import threading
import time
import traceback
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import analyze
import brain
import broll
import kinetic
import mascot
import skillset
import story
import voice

ROOT = Path(__file__).resolve().parent
JOBS = ROOT / "jobs"
JOBS.mkdir(exist_ok=True)

SETTINGS = ROOT / "settings.json"
DEFAULT_SETTINGS = {"text_scale": 0.5, "font": "Segoe UI", "whisper_model": "base", "voice_engine": "builtin",
                    "voice_url": voice.DEFAULT_URL}
WHISPER_MODELS = ("tiny", "base", "small", "medium", "large-v3-turbo")

app = FastAPI(title="RefCut")
_lock = threading.Lock()
_jobs = {}


def _state_path(jid):
    return JOBS / jid / "state.json"


def load(jid):
    with _lock:
        if jid not in _jobs:
            p = _state_path(jid)
            if not p.exists():
                raise HTTPException(404, "job not found")
            _jobs[jid] = json.loads(p.read_text(encoding="utf-8"))
        return _jobs[jid]


def update(jid, **kw):
    with _lock:
        st = _jobs[jid]
        st.update(kw)
        st["updated"] = time.time()
        _state_path(jid).write_text(json.dumps(st, indent=1), encoding="utf-8")
        return st


def append_log(jid, kind, text):
    with _lock:
        _jobs[jid].setdefault("build_log", []).append({"kind": kind, "text": text, "t": time.time()})
    if kind in ("done", "err"):
        update(jid)


def settings():
    try:
        return DEFAULT_SETTINGS | json.loads(SETTINGS.read_text(encoding="utf-8"))
    except Exception:
        return dict(DEFAULT_SETTINGS)


def _analyze_and_plan(jid, ref_path, url, opts):
    job_dir = JOBS / jid
    prog = lambda msg, pct: update(jid, progress=msg, pct=pct)
    kinetic_mode = opts["mode"] == "kinetic"
    try:
        if opts["mode"] == "story":
            return _plan_story(jid, opts, prog)
        if opts["mode"] == "mascot":
            return _plan_mascot(jid, opts, prog)
        if url and not ref_path:
            prog("Downloading reference", 2)
            ref_path = analyze.download_url(url, job_dir)
        reference = None
        if ref_path:
            reference = analyze.analyze_reference(ref_path, job_dir, prog, opts["transcribe"])
            if kinetic_mode:
                prog("Sampling frames to read the text animation", 80)
                reference["dense_sheets"] = analyze.dense_frames(ref_path, job_dir, reference["video"]["duration"])
            update(jid, reference=reference)

        if kinetic_mode:
            music = None
            if opts.get("music_path"):
                prog("Finding the beat in your music", 88)
                music = analyze.analyze_music(opts["music_path"], job_dir)
                update(jid, music=music)
            fmt = {"width": opts["width"], "height": opts["height"], "fps": opts["fps"]}
            prog("Claude is writing the motion script…", 94)
            spec, sid = brain.make_motion_script(job_dir, opts["context"], reference, music, fmt,
                                                 settings()["font"], opts["motion"], opts["mage"])
            spec = kinetic.normalize(spec)
            update(jid, blueprint=spec, session_id=sid, status="ready", progress="Motion script ready", pct=100)
            mark_claude_ok()
            return

        footage = None
        if opts["footage_dir"]:
            footage = analyze.analyze_footage(opts["footage_dir"], job_dir, prog)
            update(jid, footage=footage)
        prog("Claude is studying the reference and your footage…", 95)
        bp, sid = brain.make_blueprint(job_dir, opts["context"], reference, footage)
        update(jid, blueprint=bp, session_id=sid, status="ready", progress="Blueprint ready", pct=100)
        mark_claude_ok()
    except Exception as e:
        traceback.print_exc()
        update(jid, status="error", error=str(e))


def _story_items(opts):
    """What to edit: the clips on the open Resolve timeline, or files / a folder the user typed."""
    if opts["source"] == "resolve":
        tm = _bridge_get("/refcut/timeline-media")
        if tm.get("error"):
            raise RuntimeError(f"Couldn't read the Resolve timeline: {tm['error']}")
        items = [it for it in tm.get("items", []) if it.get("enabled") is not False]
        if not items:
            raise RuntimeError(f"The timeline “{tm.get('timeline')}” has no clips from files. Put your footage on it, "
                               "or switch to “Files / folder”.")
        return items, tm
    paths = [x.strip().strip('"') for x in opts["footage_dir"].splitlines() if x.strip()]
    files = []
    for p in map(Path, paths):
        if p.is_dir():
            files += sorted(f for f in p.iterdir() if f.suffix.lower() in analyze.VIDEO_EXT)
        elif p.is_file():
            files.append(p)
        else:
            raise RuntimeError(f"Not found: {p}")
    if not files:
        raise RuntimeError("Give the footage: a folder or one file path per line")
    return [{"path": str(f), "name": f.name} for f in files], None


def _plan_story(jid, opts, prog):
    job_dir = JOBS / jid
    prog("Reading your footage", 2)
    items, tm = _story_items(opts)
    if tm:
        update(jid, resolve_timeline={k: tm.get(k) for k in ("project", "timeline", "fps", "width", "height")})
    langs = analyze.spoken_languages(opts["spoken"])
    talk = analyze.analyze_talk(items, job_dir, prog, opts["transcribe"], settings()["whisper_model"], opts["context"],
                                langs)
    talk["spoken"] = langs
    update(jid, talk=talk)
    first = next(iter(talk["sources"].values()))
    fps = opts["fps"] if opts["fps_set"] else int(round((tm or {}).get("fps") or first.get("fps") or 30))
    if opts["aspect"] == "match":
        w, h = ((tm or {}).get("width") or first.get("width") or 1920), ((tm or {}).get("height") or first.get("height") or 1080)
    else:
        w, h = FORMATS.get(opts["aspect"], FORMATS["16:9"])
    fmt = {"width": int(w), "height": int(h), "fps": int(fps)}
    reference = None
    if opts.get("ref_path") or opts.get("url"):
        ref_path = opts.get("ref_path")
        if not ref_path:
            prog("Downloading the reference video", 89)
            ref_path = analyze.download_url(opts["url"], job_dir)
        reference = analyze.analyze_reference(ref_path, job_dir, lambda msg, pct: prog("Reference · " + msg, 90), True)
        update(jid, reference=reference)
    prog("Claude is watching your footage and writing the edit…", 92)
    spec, sid = brain.make_story(job_dir, opts["context"], talk, fmt, settings()["font"], opts["target"],
                                 opts["mage"], opts["captions"], opts["style"], reference)
    spec["format"] = spec.get("format") or fmt
    update(jid, blueprint=spec, session_id=sid, status="ready", progress="Edit plan ready", pct=100)
    mark_claude_ok()


def _plan_mascot(jid, opts, prog):
    """Mascot video: Claude writes the script and scenes, then VoiceStudio speaks each line."""
    fmt = {"width": opts["width"], "height": opts["height"], "fps": opts["fps"]}
    prog("Claude is writing the script and directing Mage…", 20)
    spec, sid = brain.make_mascot(JOBS / jid, opts["context"], fmt, settings()["font"], opts["target"], opts["spoken"])
    spec["voice"] = {"id": opts["voice"] or "default", "name": opts["voice_name"], "speed": opts["speed"]}
    spec["language"] = opts["spoken"] if opts["spoken"] in voice.LANG_NAMES else None
    update(jid, blueprint=spec, session_id=sid, progress="Script ready", pct=60)
    mark_claude_ok()
    _voice_job(jid)


def _voice_job(jid):
    """Generate the voice for every line that doesn't have one yet (runs in the caller's thread)."""
    st = load(jid)
    update(jid, status="voicing", error=None, progress="Generating the voice")
    try:
        n = mascot.make_voices(st["blueprint"], JOBS / jid, settings()["whisper_model"], settings(),
                               lambda msg, pct: update(jid, progress=msg, pct=60 + int(pct * 0.4)))
        update(jid, status="ready", progress=f"Voice ready ({n} lines)", pct=100)
    except Exception as e:
        update(jid, status="ready", pct=100, progress="Script ready — voice not generated",
               error=f"{e} The script and scenes are ready; click “Generate voice” when the voice is set up.")


def _voice_missing(st):
    return mascot.compile_spec(st["blueprint"], JOBS / st["id"])[1]["missing"]


def _bridge_get(path):
    try:
        with urllib.request.urlopen("http://127.0.0.1:9876" + path, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"error": "Resolve is running an older CursorBridge. Re-run setup, restart Resolve and start "
                             "Workspace → Scripts → CursorBridge again."}
        return {"error": f"bridge HTTP {e.code}"}
    except Exception:
        return {"error": "Can't reach DaVinci Resolve. Open it, then Workspace → Scripts → CursorBridge."}


@app.get("/api/resolve/timeline")
def resolve_timeline():
    """Summary of the open timeline, so the UI can show what story mode will edit."""
    tm = _bridge_get("/refcut/timeline-media")
    if tm.get("error"):
        return {"ok": False, "error": tm["error"]}
    items = tm.get("items", [])
    return {"ok": True, "project": tm.get("project"), "timeline": tm.get("timeline"), "fps": tm.get("fps"),
            "width": tm.get("width"), "height": tm.get("height"), "clips": len(items),
            "seconds": round(sum(it["range"][1] - it["range"][0] for it in items), 1),
            "names": [it["name"] for it in items][:12]}


FORMATS = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350),
           "4k": (3840, 2160)}


@app.post("/api/jobs")
async def create_job(reference: UploadFile | None = File(None), url: str = Form(""),
                     footage_dir: str = Form(""), context: str = Form(""),
                     transcribe: bool = Form(True), mode: str = Form("footage"),
                     music: UploadFile | None = File(None), music_path: str = Form(""),
                     aspect: str = Form("16:9"), fps: str = Form("30"), motion: str = Form("auto"),
                     mage: bool = Form(False), source: str = Form("resolve"), target: str = Form(""),
                     captions: str = Form("burned"), spoken: str = Form("auto"), voice_id: str = Form(""),
                     voice_name: str = Form(""), speed: float = Form(1.0), style: str = Form("short")):
    has_ref = bool(reference and reference.filename) or bool(url.strip())
    if mode == "story":
        if source == "resolve" and _resolve_status()["state"] != "connected":
            raise HTTPException(503, _resolve_status()["detail"])
    elif mode == "mascot":
        if not context.strip():
            raise HTTPException(400, "Say what the video is about, or paste the script")
    elif mode == "kinetic":
        if not has_ref and not context.strip():
            raise HTTPException(400, "Give a reference video, or describe what you're making")
    elif not has_ref:
        raise HTTPException(400, "Give a reference video file or URL")
    jid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    job_dir = JOBS / jid
    job_dir.mkdir(parents=True)
    ref_path = None
    if reference and reference.filename:
        ref_path = job_dir / ("reference" + Path(reference.filename).suffix.lower())
        with open(ref_path, "wb") as f:
            shutil.copyfileobj(reference.file, f)
    music_path = music_path.strip().strip('"')
    if music and music.filename:
        mp = job_dir / ("music" + Path(music.filename).suffix.lower())
        with open(mp, "wb") as f:
            shutil.copyfileobj(music.file, f)
        music_path = str(mp.resolve())
    if music_path and not Path(music_path).is_file():
        raise HTTPException(400, f"Music file not found: {music_path}")
    w, h = FORMATS.get(aspect, FORMATS["16:9"])
    fps_set = fps.isdigit()
    captions = captions if captions in ("burned", "srt", "off") else "burned"
    opts = {"mode": mode if mode in ("kinetic", "footage", "story", "mascot") else "footage", "context": context,
            "voice": voice_id.strip(), "voice_name": voice_name.strip(), "speed": min(2.0, max(0.5, speed)),
            "footage_dir": footage_dir.strip() if mode == "story" else footage_dir.strip().strip('"'),
            "transcribe": transcribe, "music_path": music_path, "width": w, "height": h,
            "fps": int(fps) if fps_set else 30, "fps_set": fps_set, "motion": motion, "mage": mage,
            "source": "files" if source == "files" else "resolve", "target": target.strip(), "aspect": aspect,
            "captions": captions, "spoken": spoken, "style": "youtube" if style == "youtube" else "short",
            "ref_path": str(ref_path) if ref_path else None, "url": url.strip()}
    st = {"id": jid, "status": "analyzing", "progress": "Starting", "pct": 0, "created": time.time(),
          "mode": opts["mode"], "context": context, "footage_dir": opts["footage_dir"], "url": url.strip(),
          "music_path": music_path, "captions": captions, "mage": mage,
          "ref_name": reference.filename if ref_path else (url.strip() or context.strip()[:60]
                                                           or ("Resolve timeline" if mode == "story" else ""))}
    with _lock:
        _jobs[jid] = st
    update(jid)
    threading.Thread(target=_analyze_and_plan, args=(jid, ref_path, url.strip(), opts), daemon=True).start()
    return {"id": jid}


@app.get("/api/jobs")
def list_jobs():
    out = []
    for p in sorted(JOBS.glob("*/state.json"), reverse=True)[:30]:
        try:
            s = json.loads(p.read_text(encoding="utf-8"))
            out.append({k: s.get(k) for k in ("id", "status", "ref_name", "context", "created", "mode")}
                       | {"title": (s.get("blueprint") or {}).get("title")})
        except Exception:
            pass
    return out


@app.get("/api/jobs/{jid}")
def get_job(jid: str, log_from: int = 0):
    st = dict(load(jid))
    st["build_log"] = st.get("build_log", [])[log_from:]
    return st


@app.get("/api/jobs/{jid}/file/{name:path}")
def job_file(jid: str, name: str):
    base = (JOBS / jid).resolve()
    p = (base / name).resolve()
    if not p.is_relative_to(base) or not p.is_file() or p.name in ("state.json", "mcp.json"):
        raise HTTPException(404)
    return FileResponse(p)


class Refine(BaseModel):
    message: str


@app.post("/api/jobs/{jid}/refine")
def refine(jid: str, body: Refine):
    st = load(jid)
    if not st.get("session_id"):
        raise HTTPException(400, "No blueprint session to refine")
    update(jid, status="refining", progress="Claude is revising the blueprint…")
    history = st.get("chat", []) + [{"role": "user", "text": body.message}]

    def run():
        try:
            bp, sid = brain.refine_blueprint(JOBS / jid, st["session_id"], body.message)
            if st.get("mode") == "mascot":                 # keep the voice / language / caption choices
                bp = {k: st["blueprint"][k] for k in ("voice", "language", "captions") if k in st["blueprint"]} | bp
            update(jid, blueprint=bp, session_id=sid, status="ready", progress="Blueprint updated",
                   chat=history + [{"role": "claude", "text": "Updated the blueprint."}])
            if st.get("mode") == "mascot":
                _voice_job(jid)
        except Exception as e:
            update(jid, status="ready", error=str(e), chat=history + [{"role": "claude", "text": f"Error: {e}"}])
    threading.Thread(target=run, daemon=True).start()
    return {"ok": True}


@app.put("/api/jobs/{jid}/blueprint")
def put_blueprint(jid: str, blueprint: dict):
    st = load(jid)
    if st.get("mode") == "kinetic":
        blueprint = kinetic.normalize(blueprint)
    update(jid, blueprint=blueprint)
    return {"ok": True}


@app.post("/api/jobs/{jid}/voice")
def make_voice(jid: str):
    """Mascot video: (re)generate the voice for lines that changed or have none yet."""
    st = load(jid)
    if st.get("mode") != "mascot" or not st.get("blueprint"):
        raise HTTPException(400, "Only mascot videos have a generated voice")
    if st.get("status") in ("voicing", "building", "refining"):
        raise HTTPException(409, "Busy — wait for the current step to finish")
    vs = voice.status(settings())
    if vs["state"] != "connected":
        raise HTTPException(503, vs["detail"])
    update(jid, status="voicing", progress="Generating the voice")
    threading.Thread(target=_voice_job, args=(jid,), daemon=True).start()
    return {"ok": True}


class VoiceDesign(BaseModel):
    name: str = "Mage"
    instruct: str = "male, young adult, moderate pitch"
    text: str = ""


@app.post("/api/voices")
def design_voice(body: VoiceDesign):
    """Make (or remake) a voice from a description; progress shows in /api/status → voice.task."""
    if voice.engine(settings()) != "builtin":
        raise HTTPException(400, "Voices are made in VoiceStudio while it is the selected engine")
    allowed = {a for group in voice.ATTRIBUTES.values() for a in group if a}
    parts = [p.strip().lower() for p in body.instruct.split(",") if p.strip()]
    if not parts or any(p not in allowed for p in parts):
        raise HTTPException(400, "Describe the voice with the listed options (gender, age, pitch, accent)")
    try:
        voice.design_async(body.name[:40], ", ".join(parts), body.text[:300], settings())
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@app.delete("/api/voices/{voice_id}")
def delete_voice(voice_id: str):
    voice.delete(voice_id)
    return {"ok": True}


@app.get("/api/voices/{voice_id}/sample.wav")
def voice_sample(voice_id: str):
    p = (voice.VOICES / voice_id / "sample.wav").resolve()
    if p.parent.parent != voice.VOICES.resolve() or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, headers={"Cache-Control": "no-store"})


def _mascot_compiled(st, with_audio=True):
    """(kinetic spec, info, audio paths) for a mascot job."""
    spec, info = mascot.compile_spec(st["blueprint"], JOBS / st["id"])
    audio = mascot.write_audio(info, JOBS / st["id"], st.get("music_path")) if with_audio and info["placements"] else {}
    return spec, info, audio


@app.get("/api/jobs/{jid}/audio")
def job_audio(jid: str):
    st = load(jid)
    if st.get("mode") != "mascot":
        raise HTTPException(404)
    audio = _mascot_compiled(st)[2]
    p = audio.get("mix") or audio.get("voice")
    if not p:
        raise HTTPException(404)
    return FileResponse(p)


@app.get("/api/jobs/{jid}/preview")
def preview(jid: str):
    st = load(jid)
    if not st.get("blueprint"):
        raise HTTPException(404, "No motion script yet")
    if st.get("mode") == "story":
        return story.preview_payload(st["blueprint"], st["talk"]["sources"], jid, st.get("captions", "burned"),
                                     st.get("broll"), st.get("music_path"), JOBS / jid)
    if st.get("mode") == "mascot":
        spec, info, _ = _mascot_compiled(st, with_audio=False)
        return kinetic.preview_payload(spec) | {
            "has_music": False, "voice": {k: info[k] for k in ("missing", "lines", "seconds")},
            "audio_url": f"/api/jobs/{jid}/audio?k={info['audio_key']}" if info["placements"] else None}
    return kinetic.preview_payload(st["blueprint"]) | {"has_music": bool(st.get("music_path"))}


class Options(BaseModel):
    captions: str | None = None


@app.put("/api/jobs/{jid}/options")
def put_options(jid: str, body: Options):
    load(jid)
    if body.captions in ("burned", "srt", "off"):
        update(jid, captions=body.captions)
    return {"ok": True}


class Broll(BaseModel):
    density: str = "medium"
    notes: str = ""


@app.post("/api/jobs/{jid}/broll")
def make_broll(jid: str, body: Broll):
    """Motion-graphic B-roll for a talking video: Claude follows the bundled motion-broll skill."""
    st = load(jid)
    if st.get("mode") != "story" or not st.get("blueprint"):
        raise HTTPException(400, "Motion B-roll needs a talking-video edit plan")
    if st.get("status") in ("building", "refining", "broll"):
        raise HTTPException(409, "Busy — wait for the current step to finish")
    rt = broll.runtime_status()
    if rt["state"] == "no_node":
        raise HTTPException(503, rt["detail"])
    update(jid, status="broll", build_log=[], progress="Making motion B-roll", error=None)

    def run():
        try:
            gen = broll.run(JOBS / jid, st["blueprint"], st["talk"]["sources"], st.get("captions", "burned"),
                            st.get("context", ""), body.density, body.notes)
            clips = []
            while True:
                try:
                    kind, text = next(gen)
                except StopIteration as stop:
                    clips = stop.value or []
                    break
                append_log(jid, kind, text)
            update(jid, status="ready", progress="Motion B-roll ready" if clips else "No B-roll made",
                   **({"broll": clips} if clips else {}))
        except Exception as e:
            traceback.print_exc()
            append_log(jid, "err", str(e))
            update(jid, status="ready", progress="Motion B-roll failed")
    threading.Thread(target=run, daemon=True).start()
    return {"ok": True}


@app.delete("/api/jobs/{jid}/broll/{clip_id}")
def delete_broll(jid: str, clip_id: str):
    st = load(jid)
    update(jid, broll=[c for c in st.get("broll") or [] if c["id"] != clip_id])
    return {"ok": True}


@app.get("/api/jobs/{jid}/captions.srt")
def captions_srt(jid: str):
    st = load(jid)
    if st.get("mode") != "story" or not st.get("blueprint"):
        raise HTTPException(404)
    spec, tl = story.compile_story(st["blueprint"], st["talk"]["sources"], "srt")
    p = JOBS / jid / "captions.srt"
    p.write_text(story.srt(tl["captions"], tl["fps"]), encoding="utf-8")
    return FileResponse(p, filename=f"{(spec.get('title') or jid)[:40]}.srt")


@app.get("/api/jobs/{jid}/music")
def job_music(jid: str):
    p = load(jid).get("music_path")
    if not p or not Path(p).is_file():
        raise HTTPException(404)
    return FileResponse(p)


@app.get("/api/jobs/{jid}/comps.zip")
def comps_zip(jid: str):
    import zipfile
    st = load(jid)
    bp = _mascot_compiled(st, with_audio=False)[0] if st.get("mode") == "mascot" else st["blueprint"]
    _, files = kinetic.write_all(bp, JOBS / jid / "comps", settings()["text_scale"])
    zp = JOBS / jid / "fusion_comps.zip"
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f["path"], Path(f["path"]).name)
    return FileResponse(zp, filename=f"{(st['blueprint'].get('title') or jid)[:40]} - fusion comps.zip")


@app.get("/api/settings")
def get_settings():
    return settings()


@app.put("/api/settings")
def put_settings(body: dict):
    s = settings()
    if "text_scale" in body:
        s["text_scale"] = min(3.0, max(0.05, float(body["text_scale"])))
    if body.get("font"):
        s["font"] = str(body["font"])[:80]
    if body.get("whisper_model") in WHISPER_MODELS:
        s["whisper_model"] = body["whisper_model"]
    if body.get("voice_engine") in ("builtin", "voicestudio"):
        s["voice_engine"] = body["voice_engine"]
    if str(body.get("voice_url") or "").startswith("http"):
        s["voice_url"] = str(body["voice_url"]).rstrip("/")[:200]
    SETTINGS.write_text(json.dumps(s, indent=1), encoding="utf-8")
    return s


class Build(BaseModel):
    project: str = ""


@app.post("/api/jobs/{jid}/build")
def build(jid: str, body: Build):
    st = load(jid)
    if not st.get("blueprint"):
        raise HTTPException(400, "No blueprint yet")
    if st.get("status") == "building":
        raise HTTPException(409, "Already building")
    rs = _resolve_status()
    if rs["state"] != "connected":
        raise HTTPException(503, rs["detail"])
    project = body.project.strip() or st["blueprint"].get("title") or f"RefCut {jid}"
    update(jid, status="building", build_log=[], progress="Building in Resolve")

    if st.get("mode") == "story":
        project = body.project.strip() or st["blueprint"].get("title") or "RefCut edit"
        events = lambda: brain.build_story(JOBS / jid, st["blueprint"], st["talk"]["sources"], project,
                                           settings()["text_scale"], st.get("captions", "burned"), st.get("broll"),
                                           st.get("music_path"))
    elif st.get("mode") == "mascot":
        spec_c, info, audio = _mascot_compiled(st)
        if info["missing"]:
            update(jid, status="ready")
            raise HTTPException(400, f"{info['missing']} line(s) have no voice yet — click “Generate voice” first")
        tracks = [{"path": audio["voice"], "track": 1, "name": "Voice"}] + (
            [{"path": audio["music"], "track": 2, "name": "Music bed"}] if audio.get("music") else [])
        events = lambda: brain.build_kinetic(JOBS / jid, spec_c, project, None, settings()["text_scale"], tracks)
    elif st.get("mode") == "kinetic":
        events = lambda: brain.build_kinetic(JOBS / jid, st["blueprint"], project, st.get("music_path"),
                                             settings()["text_scale"])
    else:
        events = lambda: brain.build_in_resolve(JOBS / jid, st["blueprint"], st.get("footage"), project)

    def run():
        try:
            for kind, text in events():
                append_log(jid, kind, text)
            update(jid, status="built", progress="Built in Resolve")
        except Exception as e:
            append_log(jid, "err", str(e))
            update(jid, status="ready", progress="Build failed")
    threading.Thread(target=run, daemon=True).start()
    return {"ok": True}


# ---- connection status -------------------------------------------------------
_claude = {"state": "checking", "detail": "Checking…", "version": None, "checked": 0}


def _check_claude(verify=True):
    """installed? -> version; verify=True also makes a tiny call to prove the login works."""
    try:
        exe = brain.claude_bin()
    except Exception:
        _claude.update(state="missing", detail="Claude Code isn't installed on this machine.", checked=time.time())
        return
    try:
        v = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=20,
                           encoding="utf-8", errors="replace").stdout.strip()
        _claude["version"] = v.split()[0] if v else None
    except Exception:
        pass
    if not verify:
        return
    _claude.update(state="checking", detail="Checking login…")
    try:
        r = subprocess.run([exe, "-p", "--output-format", "json", "--max-turns", "1"],
                           input="Reply with exactly: OK", capture_output=True, text=True, timeout=90,
                           encoding="utf-8", errors="replace")
        out = json.loads(r.stdout or "{}")
        if out.get("is_error") or r.returncode:
            msg = str(out.get("result") or r.stderr or "unknown error")[:200]
            logged_out = any(w in msg.lower() for w in ("login", "log in", "auth", "api key", "credential"))
            _claude.update(state="logged_out" if logged_out else "error", detail=msg)
        else:
            _claude.update(state="ok", detail="Logged in and responding")
    except subprocess.TimeoutExpired:
        _claude.update(state="error", detail="Claude didn't respond within 90s")
    except Exception as e:
        _claude.update(state="error", detail=str(e)[:200])
    _claude["checked"] = time.time()


def mark_claude_ok():
    _claude.update(state="ok", detail="Logged in and responding", checked=time.time())


def _resolve_running():
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Resolve.exe", "/NH"], capture_output=True,
                                 text=True, timeout=5, creationflags=0x08000000).stdout
            return "Resolve.exe" in out
        # exact name: the Resolve binary is "resolve" (Linux) / "Resolve" (macOS); avoids systemd-resolved
        return any(subprocess.run(["pgrep", "-x", n], capture_output=True, timeout=5).returncode == 0
                   for n in ("resolve", "Resolve"))
    except Exception:
        return None


def _resolve_status():
    try:
        with urllib.request.urlopen("http://127.0.0.1:9876/status", timeout=2) as r:
            st = json.loads(r.read().decode("utf-8"))
    except Exception:
        running = _resolve_running()
        if running:
            return {"state": "bridge_off", "detail": "Resolve is open, but the bridge isn't running. "
                                                     "In Resolve: Workspace → Scripts → CursorBridge."}
        if running is False:
            return {"state": "not_running", "detail": "DaVinci Resolve isn't running. Open it, then start "
                                                      "Workspace → Scripts → CursorBridge."}
        return {"state": "bridge_off", "detail": "Can't reach the bridge. In Resolve: Workspace → Scripts → CursorBridge."}
    if not st.get("connected"):
        return {"state": "bridge_off", "detail": "Bridge is running but can't talk to Resolve — restart the script."}
    proj = {}
    try:
        with urllib.request.urlopen("http://127.0.0.1:9876/project", timeout=2) as r:
            proj = json.loads(r.read().decode("utf-8"))
    except Exception:
        pass
    ver = " ".join(x for x in (st.get("product"), st.get("version")) if x)
    return {"state": "connected", "product": ver, "project": proj.get("name"),
            "detail": f"{ver} · project: {proj.get('name') or 'none open'}"}


_skills = []


@app.get("/api/status")
def status():
    return {"claude": dict(_claude), "resolve": _resolve_status(), "ffmpeg": bool(shutil.which("ffmpeg")),
            "skills": _skills, "broll_runtime": broll.runtime_status(), "voice": voice.status(settings())}


@app.post("/api/status/claude")
def recheck_claude():
    threading.Thread(target=_check_claude, daemon=True).start()
    return {"ok": True}


@app.on_event("startup")
def _startup_checks():
    threading.Thread(target=_check_claude, daemon=True).start()
    try:                                   # bundled skills -> this machine's Claude (~/.claude/skills)
        _skills[:] = skillset.install()
    except Exception as e:
        _skills[:] = [{"name": "bundled skills", "state": "error", "detail": str(e), "description": ""}]


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=7860)
