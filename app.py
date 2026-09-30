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
import kinetic

ROOT = Path(__file__).resolve().parent
JOBS = ROOT / "jobs"
JOBS.mkdir(exist_ok=True)

SETTINGS = ROOT / "settings.json"
DEFAULT_SETTINGS = {"text_scale": 0.5, "font": "Segoe UI"}

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
                                                 settings()["font"], opts["motion"])
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


FORMATS = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350),
           "4k": (3840, 2160)}


@app.post("/api/jobs")
async def create_job(reference: UploadFile | None = File(None), url: str = Form(""),
                     footage_dir: str = Form(""), context: str = Form(""),
                     transcribe: bool = Form(True), mode: str = Form("footage"),
                     music: UploadFile | None = File(None), music_path: str = Form(""),
                     aspect: str = Form("16:9"), fps: int = Form(30), motion: str = Form("auto")):
    has_ref = bool(reference and reference.filename) or bool(url.strip())
    if mode == "kinetic":
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
    opts = {"mode": mode if mode in ("kinetic", "footage") else "footage", "context": context,
            "footage_dir": footage_dir.strip().strip('"'), "transcribe": transcribe,
            "music_path": music_path, "width": w, "height": h, "fps": int(fps), "motion": motion}
    st = {"id": jid, "status": "analyzing", "progress": "Starting", "pct": 0, "created": time.time(),
          "mode": opts["mode"], "context": context, "footage_dir": opts["footage_dir"], "url": url.strip(),
          "music_path": music_path,
          "ref_name": reference.filename if ref_path else (url.strip() or context.strip()[:60])}
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
            update(jid, blueprint=bp, session_id=sid, status="ready", progress="Blueprint updated",
                   chat=history + [{"role": "claude", "text": "Updated the blueprint."}])
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


@app.get("/api/jobs/{jid}/preview")
def preview(jid: str):
    st = load(jid)
    if not st.get("blueprint"):
        raise HTTPException(404, "No motion script yet")
    return kinetic.preview_payload(st["blueprint"]) | {"has_music": bool(st.get("music_path"))}


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
    _, files = kinetic.write_all(st["blueprint"], JOBS / jid / "comps", settings()["text_scale"])
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

    if st.get("mode") == "kinetic":
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


@app.get("/api/status")
def status():
    return {"claude": dict(_claude), "resolve": _resolve_status(), "ffmpeg": bool(shutil.which("ffmpeg"))}


@app.post("/api/status/claude")
def recheck_claude():
    threading.Thread(target=_check_claude, daemon=True).start()
    return {"ok": True}


@app.on_event("startup")
def _startup_checks():
    threading.Thread(target=_check_claude, daemon=True).start()


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=7860)
