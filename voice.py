"""Generated voice-over, through VoiceStudio (github.com/debpalash/VoiceStudio).

VoiceStudio is a separate, local app (voice cloning, voice design, 600+ languages incl. Kiswahili).
While it is open it serves an OpenAI-style speech API on 127.0.0.1:3900; RefCut calls that, the
same way it talks to Resolve through CursorBridge. Voices you clone or design in VoiceStudio show
up in RefCut's voice list. (RefCut does not include VoiceStudio's code: it is AGPL-licensed.)

    status()            is VoiceStudio reachable, which voices and engines does it have
    line()              one spoken line -> cached wav + duration, word timings, loudness envelope
"""
import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

DEFAULT_URL = "http://127.0.0.1:3900"
LANG_NAMES = {"en": "English", "sw": "Kiswahili"}


def _get(url, timeout=3):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def status(base=DEFAULT_URL):
    try:
        d = _get(base.rstrip("/") + "/v1/audio/voices")
    except Exception:
        return {"state": "off", "voices": [], "detail": "VoiceStudio isn't running. Open the VoiceStudio app (it serves "
                                                        f"voices on {base}); install it from voicestudio.sh."}
    voices = [{"id": v.get("voice_id"), "name": v.get("name") or v.get("voice_id"), "language": v.get("language"),
               "yours": v.get("type") == "profile"} for v in d.get("voices", []) if v.get("voice_id")]
    voices.sort(key=lambda v: (not v["yours"], v["name"].lower()))      # your cloned / designed voices first
    mine = sum(v["yours"] for v in voices)
    return {"state": "connected", "voices": voices,
            "detail": f"{mine} voice{'s' if mine != 1 else ''} of your own" if mine else
                      "Connected. Clone or design a voice in VoiceStudio to use it here."}


def synthesize(text, voice_id, out_path, language=None, speed=1.0, base=DEFAULT_URL):
    """Text -> wav file via VoiceStudio. Raises RuntimeError with VoiceStudio's own message on failure."""
    body = {"model": "tts-1", "input": text, "voice": voice_id or "default", "response_format": "wav",
            "speed": float(speed)}
    if language in LANG_NAMES:
        body["language"] = language
    req = urllib.request.Request(base.rstrip("/") + "/v1/audio/speech", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=1200) as r:      # CPU-only machines can take minutes per line
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
    return out_path


def key(text, voice_id, language, speed):
    return hashlib.sha1(f"{text}|{voice_id}|{language}|{speed}".encode("utf-8")).hexdigest()[:16]


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


def line(text, voice_id, language, speed, voice_dir, fps, whisper_model="base", base=DEFAULT_URL, generate=True):
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
        tmp = voice_dir / f"{k}.part"
        synthesize(text, voice_id, tmp, language, speed, base)
        tmp.replace(wav)
    m = _measure(wav, text, language, fps, whisper_model)
    meta.write_text(json.dumps(m), encoding="utf-8")
    return m | {"file": str(wav), "key": k}
