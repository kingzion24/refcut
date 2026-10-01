"""Mascot videos: faceless, voiced explainers hosted by Mage.

The plan is a kinetic motion script (kinetic.py) whose scenes also carry what is SAID:

    {"name": "Hook", "say": "Your shop lost sales every time the internet dropped.", "lang": "en",
     "pause": 0.4, "elements": [
        {"kind": "mage", "speaks": true, ...},
        {"text": "Internet down?", "in": {"type": "rise", "word": 6}, ...}]}

Timing comes from the generated voice (voice.py), not from the plan: a spoken scene lasts as long
as its line, and `"word": n` inside an element's in / out (or a Mage beat / move) means "when the
n-th word of the line is spoken" (0-based). compile_spec() resolves all of that into an ordinary
kinetic spec, adds captions, makes Mage's eyes follow the voice, and lays the lines out as one
voice track. Scenes without "say" are silent beats with their own "dur".
"""
import copy
import hashlib
from pathlib import Path

import numpy as np

import kinetic
import voice

LEAD, TAIL = 0.25, 0.4            # silence before / after a line inside its scene (s)
WORDS_PER_SEC = 2.6               # to estimate a line's length before its voice exists
SR = 48000


def voice_of(spec):
    v = spec.get("voice") or {}
    return {"id": v.get("id") or "default", "name": v.get("name") or "", "speed": float(v.get("speed") or 1.0)}


def _lang(sc, spec):
    lang = sc.get("lang") or spec.get("language")
    return lang if lang in voice.LANG_NAMES else None


def spoken(spec):
    return [sc for sc in spec.get("scenes") or [] if str(sc.get("say") or "").strip()]


def make_voices(spec, job_dir, whisper_model="base", cfg=None, progress=lambda msg, pct: None):
    """Generate (or reuse) the voice for every spoken scene. Raises RuntimeError if the voice engine fails."""
    v, fps = voice_of(spec), int(round(float((spec.get("format") or {}).get("fps", 30))))
    lines = spoken(spec)
    for i, sc in enumerate(lines):
        progress(f"Voicing line {i + 1} of {len(lines)}: “{sc['say'].strip()[:60]}”", int(100 * i / max(len(lines), 1)))
        voice.line(sc["say"].strip(), v["id"], _lang(sc, spec), v["speed"], Path(job_dir) / "voice", fps,
                   whisper_model, cfg)
    return len(lines)


def _estimate(say):
    words = say.split()
    dur = max(0.8, len(words) / WORDS_PER_SEC)
    total, t, out = sum(len(w) + 1 for w in words) or 1, 0.0, []
    for w in words:
        d = dur * (len(w) + 1) / total
        out.append([t, t + d, w])
        t += d
    return {"dur": dur, "speech": [0.0, dur], "words": out, "env": None, "file": None}


def _caption_chunks(words, max_words, max_chars):
    chunks, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        too_wide = nxt is not None and len(" ".join(x[2] for x in cur)) + 1 + len(nxt[2]) > max_chars
        if nxt is None or len(cur) >= max_words or too_wide or w[2][-1:] in ".?!,;:" or nxt[0] - w[1] > 0.3:
            chunks.append({"start": cur[0][0], "end": nxt[0] if nxt else w[1] + 0.25, "text": " ".join(x[2] for x in cur)})
            cur = []
    return chunks


def compile_spec(spec, job_dir):
    """Voiced plan -> (ordinary kinetic spec, info). info: where each line sits on the timeline,
    how many lines still have no voice, and a key that changes whenever the audio does."""
    s = copy.deepcopy(spec)
    fps = int(round(float((s.get("format") or {}).get("fps", 30))))
    v = voice_of(s)
    cap = s.get("captions") or {}
    cap_on = cap.get("on", True) is not False
    tall = (s.get("format") or {}).get("height", 1080) > (s.get("format") or {}).get("width", 1920)
    placements, missing, t0 = [], 0, 0.0
    for sc in s.get("scenes") or []:
        say = str(sc.get("say") or "").strip()
        if not say:
            t0 += max(0.2, float(sc.get("dur", 1.5)))
            continue
        ln = voice.line(say, v["id"], _lang(sc, s), v["speed"], Path(job_dir) / "voice", fps, generate=False)
        if not ln:
            ln, missing = _estimate(say), missing + 1
        words = ln["words"]
        sc["dur"] = round(LEAD + ln["dur"] + float(sc.get("pause", TAIL)), 3)
        at = lambda n: round(LEAD + words[max(0, min(int(n), len(words) - 1))][0], 3) if words else LEAD
        for el in sc.get("elements") or []:
            for side in ("in", "out"):
                d = el.get(side)
                if isinstance(d, dict) and d.get("word") is not None:
                    d["at"] = at(d.pop("word"))
            if el.get("kind") == "mage":
                for b in (el.get("beats") or []) + (el.get("moves") or []):
                    if isinstance(b, dict) and b.get("word") is not None:
                        b["at"] = at(b.pop("word"))
                if el.get("speaks", True) is not False:
                    base_mood = el.get("mood") or "idle"
                    el["beats"] = (el.get("beats") or []) + [{"at": LEAD + ln["speech"][0], "mood": "talking"},
                                                             {"at": LEAD + ln["speech"][1] + 0.05, "mood": base_mood}]
                    if ln["env"]:
                        el["voice_env"] = [0.0] * int(round(LEAD * fps)) + ln["env"]
        if cap_on:
            fmt = s.get("format") or {}
            size = float(cap.get("size", 0.03 if tall else 0.036))
            limit = kinetic.caption_max_chars(size, fmt.get("width", 1920), fmt.get("height", 1080))
            for ch in _caption_chunks(words, int(cap.get("max_words", 4)), limit):
                sc.setdefault("elements", []).append({
                    "text": ch["text"], "x": 0.5, "y": float(cap.get("y", 0.82 if tall else 0.88)),
                    "size": size, "weight": cap.get("weight", "Semibold"),
                    "color": cap.get("color", "color"), "tracking": 0, "caption": True,
                    "in": {"type": "cut", "at": round(LEAD + ch["start"], 3), "dur": 0},
                    "out": {"type": "cut", "at": round(min(LEAD + ch["end"], sc["dur"]), 3), "dur": 0}})
        if ln["file"]:
            placements.append({"start": round(t0 + LEAD, 3), "file": ln["file"], "dur": ln["dur"]})
        t0 += sc["dur"]
    key = hashlib.sha1(repr([(p["start"], p["file"]) for p in placements]).encode()).hexdigest()[:12]
    return s, {"placements": placements, "missing": missing, "seconds": round(t0, 3), "audio_key": key,
               "lines": len(spoken(spec))}


# ─── audio ───────────────────────────────────────────────────────────────────

def _load(path, sr=SR):
    import librosa
    y, _ = librosa.load(str(path), sr=sr, mono=True)
    return y.astype(np.float32)


def duck(music_path, speech, total_sec, bed_db=-15.0, under_db=-25.0, sr=SR):
    """A music bed for `total_sec`: quiet under speech, a little louder in the gaps, faded out at the end.
    speech: mono samples at `sr` (the voice track), used only as the ducking key."""
    m = _load(music_path, sr)
    n = int(total_sec * sr)
    if len(m) == 0 or n <= 0:
        return np.zeros(max(n, 0), np.float32)
    m = np.tile(m, int(np.ceil(n / len(m))))[:n]
    key = np.zeros(n, np.float32)
    key[:min(n, len(speech))] = np.abs(speech[:n])
    win = int(0.35 * sr)
    c = np.concatenate(([0.0], np.cumsum(key > 0.02, dtype=np.float64)))       # running sum: a moving average in O(n)
    idx = np.arange(n)
    talking = (c[np.minimum(idx + win // 2, n)] - c[np.maximum(idx - win // 2, 0)]) / win
    talking = np.clip(talking * 3, 0, 1).astype(np.float32)   # 0 = gap, 1 = someone is speaking
    gain = 10 ** ((bed_db + (under_db - bed_db) * talking) / 20)
    fade = int(min(1.5, total_sec / 4) * sr)
    if fade > 0:
        gain[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
    peak = float(np.abs(m).max()) or 1.0
    return (m / peak * gain).astype(np.float32)


def write_audio(info, job_dir, music_path=None):
    """voice.wav (the narration laid out on the timeline), and when there is music: music_bed.wav
    (ducked under the voice) and mix.wav (both, for the browser preview). Returns {voice, music, mix}."""
    import soundfile as sf
    out = Path(job_dir) / "audio"
    out.mkdir(parents=True, exist_ok=True)
    tag = info["audio_key"] + (hashlib.sha1(str(music_path).encode()).hexdigest()[:6] if music_path else "")
    paths = {"voice": out / f"voice_{info['audio_key']}.wav", "music": None, "mix": None}
    n = int(info["seconds"] * SR) + 1
    track = None
    if not paths["voice"].is_file():
        track = np.zeros(n, np.float32)
        for p in info["placements"]:
            y = _load(p["file"])
            a = int(p["start"] * SR)
            track[a:a + len(y)] += y[:max(0, n - a)]
        sf.write(paths["voice"], track, SR, subtype="PCM_16")
    if music_path and Path(music_path).is_file():
        paths["music"], paths["mix"] = out / f"music_bed_{tag}.wav", out / f"mix_{tag}.wav"
        if not paths["mix"].is_file():
            track = track if track is not None else _load(paths["voice"])
            bed = duck(music_path, track, info["seconds"])
            sf.write(paths["music"], bed, SR, subtype="PCM_16")
            mix = bed.copy()
            mix[:min(len(mix), len(track))] += track[:len(mix)]
            sf.write(paths["mix"], np.clip(mix, -1, 1), SR, subtype="PCM_16")
    return {k: str(p) if p else None for k, p in paths.items()}
