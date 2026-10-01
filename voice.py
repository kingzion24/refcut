"""Generated voice-over for mascot videos.

Built in: the OmniVoice model (k2-fsa/OmniVoice, Apache-2.0; 600+ languages incl. Kiswahili) runs
inside RefCut, on an NVIDIA GPU when there is one and on the CPU otherwise. No other app needed.

A voice is DESIGNED once ("male, young adult, moderate pitch"): the model speaks a short English
sample in that voice, and RefCut keeps the sample as the voice. Every line of every video then
clones that sample, so the voice stays the same from line to line, video to video and language to
language (designing works best in English; cloning is the model's most stable mode).

    voices/<id>/voice.json   name, description, sample text
    voices/<id>/sample.wav   the sample (also what you hear when you press play)
    voices/<id>/prompt.pt    the encoded sample, reused for every line

Optional alternative engine: a running VoiceStudio app (settings: voice_engine = "voicestudio").

    status()   engine state, voices, what the design task is doing
    design()   make / remake a voice from a description
    line()     one spoken line -> cached wav + duration, word timings, loudness envelope
"""
import hashlib
import importlib.util
import json
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
VOICES = ROOT / "voices"
MODEL = "k2-fsa/OmniVoice"
DEFAULT_URL = "http://127.0.0.1:3900"          # VoiceStudio's local API
LANG_NAMES = {"en": "English", "sw": "Kiswahili"}
SAMPLE_TEXT = ("Hi, I'm Mage. I keep an eye on your sales, your stock and your profit, "
               "so you always know how the shop is doing.")
# What a voice can be described with (OmniVoice's voice-design attributes).
ATTRIBUTES = {
    "gender": ["male", "female"],
    "age": ["child", "teenager", "young adult", "middle-aged", "elderly"],
    "pitch": ["very low pitch", "low pitch", "moderate pitch", "high pitch", "very high pitch"],
    "accent": ["", "american accent", "british accent"],
}

_lock = threading.Lock()                        # one generation at a time
_state = {"model": None, "device": None, "prompts": {}}
_task = {"state": "idle", "msg": "", "voice": None}     # the design job, shown in the UI


# ─── built-in engine ─────────────────────────────────────────────────────────

def _installed():
    return importlib.util.find_spec("omnivoice") is not None


def _model_downloaded():
    try:
        from huggingface_hub import try_to_load_from_cache
        return isinstance(try_to_load_from_cache(MODEL, "model.safetensors"), str)
    except Exception:
        return False


def _device():
    try:
        import torch
        return "cuda:0" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def _model(progress=lambda msg: None):
    """The OmniVoice model, loaded once (downloads ~3.3 GB the first time)."""
    if _state["model"] is None:
        if not _installed():
            raise RuntimeError("The voice engine isn't installed. Run setup again (it installs the 'omnivoice' package).")
        import torch
        from omnivoice import OmniVoice
        dev = _device()
        progress("Loading the voice model" if _model_downloaded()
                 else "Downloading the voice model (3.3 GB, first time only)…")
        _state["model"] = OmniVoice.from_pretrained(MODEL, device_map=dev,
                                                    dtype=torch.float16 if dev.startswith("cuda") else torch.float32)
        _state["device"] = dev
    return _state["model"]


def _steps(cfg):
    """Decoding steps: 32 is the model's default; 16 is about twice as fast and what a CPU gets."""
    s = int((cfg or {}).get("voice_steps") or 0)
    return s if s in (8, 16, 24, 32) else (32 if _device().startswith("cuda") else 16)


def _write(path, audio, model):
    import soundfile as sf
    sf.write(str(path), np.asarray(audio, dtype=np.float32), int(getattr(model, "sampling_rate", None) or 24000),
             subtype="PCM_16")


# ─── voice library ───────────────────────────────────────────────────────────

def voices():
    out = []
    if VOICES.is_dir():
        for d in sorted(VOICES.iterdir()):
            try:
                v = json.loads((d / "voice.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if (d / "prompt.pt").is_file():
                out.append({"id": d.name, "name": v.get("name") or d.name, "instruct": v.get("instruct", ""),
                            "made": v.get("made", 0), "yours": True})
    return out


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "voice"


def design(name, instruct, sample_text=None, cfg=None):
    """Make (or remake) a voice from a description. Each call is a new take: the same description
    gives a slightly different voice, so remake until it sounds right. Blocking; see design_async."""
    name = (name or "").strip() or "Mage"
    vid = _slug(name)
    text = (sample_text or "").strip() or SAMPLE_TEXT
    say = lambda msg: _task.update(state="working", msg=msg, voice=vid)
    with _lock:
        model = _model(say)
        say(f"Designing “{name}” ({instruct})…")
        audio = model.generate(text=text, instruct=instruct, language="en", num_step=32)
        tmp = VOICES / f".{vid}.tmp"
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)
        _write(tmp / "sample.wav", audio[0], model)
        say("Saving the voice…")
        prompt = model.create_voice_clone_prompt(ref_audio=str(tmp / "sample.wav"), ref_text=text)
        prompt.save(str(tmp / "prompt.pt"))
        (tmp / "voice.json").write_text(json.dumps({"name": name, "instruct": instruct, "sample_text": text,
                                                    "made": int(time.time())}, indent=1), encoding="utf-8")
        dst = VOICES / vid
        if dst.exists():
            shutil.rmtree(dst)
        tmp.replace(dst)
        _state["prompts"].pop(vid, None)
    return vid


def design_async(name, instruct, sample_text=None, cfg=None):
    if _task["state"] == "working":
        raise RuntimeError("A voice is already being made")
    _task.update(state="working", msg="Starting…", voice=_slug((name or "").strip() or "Mage"))

    def run():
        try:
            vid = design(name, instruct, sample_text, cfg)
            _task.update(state="done", msg="Voice ready", voice=vid)
        except Exception as e:
            _task.update(state="error", msg=str(e)[:400])
    threading.Thread(target=run, daemon=True).start()


def delete(voice_id):
    d = (VOICES / voice_id).resolve()
    if d.parent == VOICES.resolve() and d.is_dir():
        shutil.rmtree(d)
        _state["prompts"].pop(voice_id, None)


def _resolve_voice(voice_id):
    """The voice to use: the one asked for, else the first in the library."""
    have = voices()
    if not have:
        raise RuntimeError("No voice yet. Click the Voice light and design one for Mage first.")
    return next((v for v in have if v["id"] == voice_id), have[0])


def _speak_builtin(text, voice_id, out_path, language, speed, cfg):
    v = _resolve_voice(voice_id)
    with _lock:
        model = _model()
        if v["id"] not in _state["prompts"]:
            from omnivoice import VoiceClonePrompt
            _state["prompts"][v["id"]] = VoiceClonePrompt.load(str(VOICES / v["id"] / "prompt.pt"))
        audio = model.generate(text=text, language=language if language in LANG_NAMES else None,
                               voice_clone_prompt=_state["prompts"][v["id"]],
                               speed=float(speed) if abs(float(speed) - 1.0) > 0.01 else None, num_step=_steps(cfg))
        _write(out_path, audio[0], model)


# ─── VoiceStudio (optional engine) ───────────────────────────────────────────

def _voicestudio_status(base):
    try:
        with urllib.request.urlopen(base.rstrip("/") + "/v1/audio/voices", timeout=3) as r:
            d = json.loads(r.read().decode("utf-8"))
    except Exception:
        return {"state": "off", "voices": [], "detail": f"VoiceStudio isn't running (it serves voices on {base})."}
    vs = [{"id": v.get("voice_id"), "name": v.get("name") or v.get("voice_id"), "yours": v.get("type") == "profile"}
          for v in d.get("voices", []) if v.get("voice_id")]
    vs.sort(key=lambda v: (not v["yours"], v["name"].lower()))
    return {"state": "connected", "voices": vs, "detail": "VoiceStudio connected"}


def _speak_voicestudio(text, voice_id, out_path, language, speed, base):
    body = {"model": "tts-1", "input": text, "voice": voice_id or "default", "response_format": "wav",
            "speed": float(speed)}
    if language in LANG_NAMES:
        body["language"] = language
    req = urllib.request.Request(base.rstrip("/") + "/v1/audio/speech", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=1200) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8", "replace")).get("error", {}).get("message")
        except Exception:
            msg = None
        raise RuntimeError(f"VoiceStudio couldn't speak this line: {msg or 'HTTP %d' % e.code}")
    except Exception as e:
        raise RuntimeError(f"Can't reach VoiceStudio ({e}). Open the VoiceStudio app and try again.")
    if len(data) < 200:
        raise RuntimeError("VoiceStudio returned no audio for this line.")
    Path(out_path).write_bytes(data)


# ─── shared ──────────────────────────────────────────────────────────────────

def engine(cfg):
    return "voicestudio" if (cfg or {}).get("voice_engine") == "voicestudio" else "builtin"


def status(cfg=None):
    """What the Voice light shows. state: connected (ready to speak) | no_voice | not_installed | off."""
    if engine(cfg) == "voicestudio":
        return _voicestudio_status((cfg or {}).get("voice_url") or DEFAULT_URL) | {"engine": "voicestudio", "task": dict(_task)}
    vs = voices()
    gpu = _device().startswith("cuda")
    base = {"engine": "builtin", "voices": vs, "task": dict(_task), "attributes": ATTRIBUTES,
            "device": "NVIDIA GPU" if gpu else "CPU", "model_ready": _model_downloaded(), "loaded": _state["model"] is not None}
    if not _installed():
        return base | {"state": "not_installed", "detail": "The voice engine isn't installed yet. Run setup again."}
    if not vs:
        return base | {"state": "no_voice", "detail": "Design a voice for Mage to start (click here)."}
    return base | {"state": "connected",
                   "detail": f"{len(vs)} voice{'s' if len(vs) != 1 else ''} · runs on this PC's {'GPU' if gpu else 'CPU'}"
                             + ("" if gpu else " (a line takes a little while)")}


def synthesize(text, voice_id, out_path, language=None, speed=1.0, cfg=None):
    """Text -> wav file with the configured engine. Raises RuntimeError with a plain-words reason."""
    if engine(cfg) == "voicestudio":
        _speak_voicestudio(text, voice_id, out_path, language, speed, (cfg or {}).get("voice_url") or DEFAULT_URL)
    else:
        _speak_builtin(text, voice_id, out_path, language, speed, cfg)
    return out_path


def key(text, voice_id, language, speed):
    """Cache key of a line. Includes when the voice was made, so remaking a voice re-speaks its lines."""
    made = next((v["made"] for v in voices() if v["id"] == voice_id), 0)
    return hashlib.sha1(f"{text}|{voice_id}|{made}|{language}|{speed}".encode("utf-8")).hexdigest()[:16]


def _measure(wav, text, language, fps, whisper_model):
    """Duration, where the speech starts/ends, per-word timings for `text`, and loudness per video frame."""
    import librosa
    y, sr = librosa.load(str(wav), sr=16000, mono=True)
    dur = len(y) / sr
    hop = sr // 100
    rms = librosa.feature.rms(y=y, frame_length=hop * 4, hop_length=hop)[0] if len(y) > hop * 4 else np.zeros(1)
    peak = float(rms.max()) or 1.0
    loud = np.where(rms > 0.06 * peak)[0]
    t0, t1 = (float(loud[0]) / 100, float(min(dur, (loud[-1] + 2) / 100))) if len(loud) else (0.0, dur)
    # loudness 0..1 per video frame, for Mage's eyes
    env = []
    for f in range(int(np.ceil(dur * fps)) + 1):
        a, b = int(f / fps * 100), max(int((f + 1) / fps * 100), int(f / fps * 100) + 1)
        env.append(round(float(min(1.0, rms[a:b].max() / peak)) if a < len(rms) else 0.0, 3))
    script = text.split()
    words = None
    try:                                           # exact timings when recognition hears the same number of words
        import analyze
        tr = analyze.transcribe(wav, words=True, model=whisper_model, langs=[language] if language in LANG_NAMES else [])
        if tr and len(tr["words"]) == len(script):
            words = [[w[0], w[1], s] for w, s in zip(tr["words"], script)]
    except Exception:
        pass
    if words is None:                              # otherwise spread the words over the speech by length
        total = sum(len(w) + 1 for w in script) or 1
        words, t = [], t0
        for w in script:
            d = (t1 - t0) * (len(w) + 1) / total
            words.append([round(t, 3), round(t + d, 3), w])
            t += d
    return {"dur": round(dur, 3), "speech": [round(t0, 3), round(t1, 3)], "words": words, "env": env}


def line(text, voice_id, language, speed, voice_dir, fps, whisper_model="base", cfg=None, generate=True):
    """One spoken line -> {file, dur, speech, words, env}; cached by text + voice + language + speed.
    generate=False only returns it if it's already cached (None otherwise)."""
    voice_dir = Path(voice_dir)
    k = key(text, voice_id, language, speed)
    wav, meta = voice_dir / f"{k}.wav", voice_dir / f"{k}.{fps}.json"
    if wav.is_file() and meta.is_file():
        return json.loads(meta.read_text(encoding="utf-8")) | {"file": str(wav), "key": k}
    if not generate:
        return None
    voice_dir.mkdir(parents=True, exist_ok=True)
    if not wav.is_file():
        tmp = voice_dir / f"{k}.part.wav"
        synthesize(text, voice_id, tmp, language, speed, cfg)
        tmp.replace(wav)
    m = _measure(wav, text, language, fps, whisper_model)
    meta.write_text(json.dumps(m), encoding="utf-8")
    return m | {"file": str(wav), "key": k}
