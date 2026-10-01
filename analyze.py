"""Reference-video and footage analysis.

Turns a video into numbers + contact sheets that Claude can reason about:
cuts/pacing, beat sync, energy curve, color look, speech, and per-shot frames.
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".mxf", ".m4v", ".webm", ".mts", ".braw", ".r3d"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".heic"}
AUDIO_EXT = {".mp3", ".wav", ".aac", ".m4a", ".flac", ".ogg"}

SCENE_THRESHOLD = 0.28
MIN_SHOT = 0.12          # seconds; shorter "shots" are flashes, merged into neighbours
TILES_PER_SHEET = 24
TILE_W = 320


def _ffmpeg():
    return shutil.which("ffmpeg") or "ffmpeg"


def _ffprobe():
    return shutil.which("ffprobe") or "ffprobe"


def _run(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", **kw)


def probe(path):
    r = _run([_ffprobe(), "-v", "error", "-print_format", "json",
              "-show_format", "-show_streams", str(path)])
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}: {r.stderr[:300]}")
    data = json.loads(r.stdout)
    v = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    info = {"duration": float(data["format"].get("duration", 0) or 0), "has_audio": a is not None}
    if v:
        num, den = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "0/1").split("/")
        fps = float(num) / float(den) if float(den) else 0
        w, h = int(v.get("width", 0)), int(v.get("height", 0))
        # phone footage is often stored landscape with a rotation tag
        rot = 0
        for sd in v.get("side_data_list", []) or []:
            rot = int(sd.get("rotation", 0) or 0)
        rot = rot or int((v.get("tags") or {}).get("rotate", 0) or 0)
        if abs(rot) in (90, 270):
            w, h = h, w
        info.update(width=w, height=h, fps=round(fps, 3), codec=v.get("codec_name"),
                    aspect=_aspect_name(w, h))
        if not info["duration"]:
            info["duration"] = float(v.get("duration", 0) or 0)
    return info


def _aspect_name(w, h):
    if not w or not h:
        return "unknown"
    r = w / h
    for name, val in [("9:16 vertical", 9 / 16), ("4:5 portrait", 4 / 5), ("1:1 square", 1),
                      ("16:9 landscape", 16 / 9), ("2.39:1 cinemascope", 2.39), ("4:3", 4 / 3)]:
        if abs(r - val) < 0.04:
            return name
    return f"{r:.2f}:1"


def detect_cuts(path, duration):
    r = _run([_ffmpeg(), "-hide_banner", "-i", str(path), "-an",
              "-vf", f"scale=320:-2,select='gt(scene,{SCENE_THRESHOLD})',showinfo",
              "-f", "null", "-"])
    times = sorted(float(m) for m in re.findall(r"pts_time:([\d.]+)", r.stderr))
    bounds = [0.0]
    for t in times:
        if t - bounds[-1] >= MIN_SHOT and duration - t >= MIN_SHOT:
            bounds.append(t)
    bounds.append(duration)
    return bounds


def grab_frame(path, t, out, width=TILE_W):
    _run([_ffmpeg(), "-y", "-ss", f"{max(t, 0):.3f}", "-i", str(path), "-frames:v", "1",
          "-vf", f"scale={width}:-2", "-q:v", "3", str(out)])
    return Path(out).exists()


def color_stats(img_path):
    im = Image.open(img_path).convert("RGB")
    a = np.asarray(im.resize((64, 64))).astype(np.float32) / 255
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    mx, mn = a.max(-1), a.min(-1)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    shadows = a[luma < np.percentile(luma, 20)].mean(0) if luma.size else np.zeros(3)
    highs = a[luma > np.percentile(luma, 80)].mean(0) if luma.size else np.zeros(3)
    return {
        "brightness": round(float(luma.mean()), 3),
        "contrast": round(float(luma.std()), 3),
        "saturation": round(float(sat.mean()), 3),
        "warmth": round(float((r - b).mean()), 3),          # >0 warm, <0 cool
        "shadow_tint": [round(float(x), 2) for x in shadows],
        "highlight_tint": [round(float(x), 2) for x in highs],
    }


def contact_sheets(tiles, out_dir, prefix):
    """tiles: list of (image_path, label). Writes grids of labelled frames; returns paths."""
    try:
        font = ImageFont.load_default(size=18)
    except TypeError:
        font = ImageFont.load_default()
    sheets = []
    for s in range(0, len(tiles), TILES_PER_SHEET):
        chunk = tiles[s:s + TILES_PER_SHEET]
        imgs = [Image.open(p).convert("RGB") for p, _ in chunk]
        th = max(int(TILE_W * im.height / im.width) for im in imgs)
        cols = 6 if th < TILE_W else 8          # vertical video -> more columns
        rows = (len(imgs) + cols - 1) // cols
        label_h = 26
        sheet = Image.new("RGB", (cols * TILE_W, rows * (th + label_h)), (18, 18, 18))
        d = ImageDraw.Draw(sheet)
        for i, (im, (_, label)) in enumerate(zip(imgs, chunk)):
            x, y = (i % cols) * TILE_W, (i // cols) * (th + label_h)
            im = im.resize((TILE_W, int(TILE_W * im.height / im.width)))
            sheet.paste(im, (x, y + label_h))
            d.text((x + 6, y + 3), label, fill=(255, 220, 90), font=font)
        p = Path(out_dir) / f"{prefix}_{s // TILES_PER_SHEET + 1:02d}.jpg"
        sheet.save(p, quality=85)
        sheets.append(p)
    return sheets


def audio_analysis(path, work_dir, cut_times):
    wav = Path(work_dir) / "ref_audio.wav"
    _run([_ffmpeg(), "-y", "-i", str(path), "-vn", "-ac", "1", "-ar", "22050", str(wav)])
    if not wav.exists():
        return None
    import librosa
    y, sr = librosa.load(str(wav), sr=22050, mono=True)
    if y.size == 0 or float(np.abs(y).max()) < 1e-4:
        return {"silent": True}
    tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="time")
    tempo = float(np.atleast_1d(tempo)[0])
    rms = librosa.feature.rms(y=y, hop_length=sr // 4)[0]        # 4 values/sec
    per_sec = [round(float(x), 3) for x in rms.reshape(-1)[: len(rms) // 4 * 4].reshape(-1, 4).mean(1)]
    peak = max(per_sec) or 1
    energy = [round(x / peak, 2) for x in per_sec]
    # moments where energy jumps sharply = drops / hits
    jumps = [i for i in range(1, len(energy)) if energy[i] - energy[i - 1] > 0.3]
    on_beat = 0
    inner_cuts = cut_times[1:-1]
    if len(beats) and inner_cuts:
        on_beat = sum(1 for c in inner_cuts if np.min(np.abs(beats - c)) < 0.09) / len(inner_cuts)
    return {
        "silent": False,
        "tempo_bpm": round(tempo, 1),
        "beat_times": [round(float(b), 3) for b in beats],
        "cuts_on_beat_ratio": round(float(on_beat), 2),
        "energy_per_second": energy,
        "energy_jumps_at_sec": jumps,
        "wav": str(wav),
    }


_whisper = {}


def _whisper_model(name):
    from faster_whisper import WhisperModel
    if name not in _whisper:
        _whisper[name] = WhisperModel(name, device="cpu", compute_type="int8")
    return _whisper[name]


LANGUAGE_NAMES = {"en": "English", "sw": "Kiswahili"}


def spoken_languages(choice):
    """UI choice -> language codes. "auto" = let Whisper decide once; "en+sw" = mixed speech."""
    return [c for c in str(choice or "auto").split("+") if c in LANGUAGE_NAMES]


def whisper_model_for(model, langs):
    """The tiny/base models are poor at Kiswahili; use at least `small` when it's spoken."""
    return "small" if "sw" in langs and model in ("tiny", "base") else model


def transcribe(wav_path, words=False, model="base", offset=0.0, hint=None, langs=None):
    """Speech -> segments (and word timings when words=True), times shifted by `offset` seconds.
    langs: [] = auto-detect once, one code = that language, several = mixed speech (see _transcribe_mixed)."""
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return None
    import librosa
    # pass decoded samples, not a path: faster-whisper's own PyAV decoding breaks on newer PyAV
    audio, _ = librosa.load(str(wav_path), sr=16000, mono=True)
    langs = langs or []
    wm = _whisper_model(whisper_model_for(model, langs))
    prompt = (hint or None) and hint[:400]
    out, ws = [], []

    def take(segs, shift, lang):
        for s in segs:
            out.append({"start": round(float(s.start) + shift, 2), "end": round(float(s.end) + shift, 2),
                        "text": s.text.strip(), "lang": lang})
            for w in (s.words or []) if words else []:
                ws.append([round(float(w.start) + shift, 3), round(float(w.end) + shift, 3), w.word.strip()])

    if len(langs) > 1:
        used = _transcribe_mixed(wm, audio, langs, prompt, words, lambda segs, t0, lang: take(segs, t0 + offset, lang))
        language = "+".join(used) or "+".join(langs)
    else:
        segs, info = wm.transcribe(audio, vad_filter=True, word_timestamps=words, initial_prompt=prompt,
                                   language=langs[0] if langs else None)
        take(segs, offset, None)
        language = info.language
        for seg in out:
            seg["lang"] = language
    if not out:
        return None
    res = {"language": language, "segments": out}
    if words:
        res["words"] = ws
    return res


def _transcribe_mixed(wm, audio, langs, prompt, words, emit, sr=16000):
    """Speech that switches between languages (e.g. English and Kiswahili). Whisper commits to one
    language per file, so split the audio at pauses, pick the most likely of `langs` for each
    passage, and transcribe that passage in that language. A switch in the middle of a sentence
    is still transcribed in the passage's main language (a Whisper limit)."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    chunks = get_speech_timestamps(audio, VadOptions(min_silence_duration_ms=350, speech_pad_ms=120))
    # merge neighbours into passages: long enough to identify the language, short enough to follow switches
    passages = []
    for c in chunks:
        if passages and c["start"] - passages[-1][1] < 0.6 * sr and c["end"] - passages[-1][0] < 9 * sr:
            passages[-1][1] = c["end"]
        else:
            passages.append([c["start"], c["end"]])
    used = []
    for a, b in passages:
        clip = audio[a:b]
        if len(clip) < 0.25 * sr:
            continue
        kw = dict(vad_filter=False, word_timestamps=words, initial_prompt=prompt, condition_on_previous_text=False)
        try:
            pr = dict(wm.detect_language(audio=clip)[2])
        except Exception:
            pr = {}
        best = max(langs, key=lambda code: pr.get(code, 0.0))
        if pr.get(best, 0.0) >= 0.5:
            lang, segs = best, list(wm.transcribe(clip, language=best, **kw)[0])
        else:
            # Unsure (Whisper rarely recognises Kiswahili as such): transcribe in each language and
            # keep the one the model is more confident in. Ties go to the non-English language.
            tries = []
            for code in sorted(langs, key=lambda c: c == "en"):
                sg = list(wm.transcribe(clip, language=code, **kw)[0])
                score = sum(x.avg_logprob for x in sg) / len(sg) if sg else -99.0
                tries.append((score, code, sg))
            _, lang, segs = max(tries, key=lambda t: round(t[0], 2))
        emit(segs, a / sr, lang)
        if lang not in used:
            used.append(lang)
    return used


def analyze_reference(path, work_dir, progress=lambda msg, pct: None, do_transcribe=True):
    work = Path(work_dir)
    frames = work / "ref_frames"
    frames.mkdir(parents=True, exist_ok=True)

    progress("Reading video info", 5)
    info = probe(path)
    dur = info["duration"]

    progress("Detecting cuts", 15)
    bounds = detect_cuts(path, dur)
    shots = []
    for i in range(len(bounds) - 1):
        s, e = bounds[i], bounds[i + 1]
        shots.append({"n": i + 1, "start": round(s, 3), "end": round(e, 3), "dur": round(e - s, 3)})

    progress(f"Grabbing frames from {len(shots)} shots", 35)
    tiles = []
    for sh in shots:
        f = frames / f"shot_{sh['n']:03d}.jpg"
        if grab_frame(path, (sh["start"] + sh["end"]) / 2, f):
            sh["color"] = color_stats(f)
            tiles.append((f, f"#{sh['n']}  {sh['start']:.2f}s  ({sh['dur']:.2f}s)"))
    sheets = contact_sheets(tiles, work, "ref_sheet")

    audio = None
    if info.get("has_audio"):
        progress("Analyzing music & beats", 60)
        audio = audio_analysis(path, work, bounds)

    speech = None
    if do_transcribe and audio and not audio.get("silent"):
        progress("Transcribing speech", 75)
        try:
            speech = transcribe(audio["wav"])
        except Exception as e:  # whisper is a nice-to-have
            speech = {"error": str(e)}

    durs = np.array([s["dur"] for s in shots]) if shots else np.array([dur])
    colors = [s["color"] for s in shots if "color" in s]
    avg_color = {k: round(float(np.mean([c[k] for c in colors])), 3)
                 for k in ("brightness", "contrast", "saturation", "warmth")} if colors else {}
    # pacing curve: avg shot length per quarter of the video
    quarters = []
    for q in range(4):
        lo, hi = dur * q / 4, dur * (q + 1) / 4
        qs = [s["dur"] for s in shots if lo <= s["start"] < hi]
        quarters.append(round(float(np.mean(qs)), 2) if qs else None)

    progress("Reference analysis done", 85)
    return {
        "video": info,
        "shot_count": len(shots),
        "pacing": {
            "avg_shot_sec": round(float(durs.mean()), 2),
            "median_shot_sec": round(float(np.median(durs)), 2),
            "shortest_sec": round(float(durs.min()), 2),
            "longest_sec": round(float(durs.max()), 2),
            "cuts_per_minute": round(len(shots) / max(dur, 0.01) * 60, 1),
            "avg_shot_by_quarter": quarters,
        },
        "look": avg_color,
        "shots": shots,
        "audio": {k: v for k, v in (audio or {}).items() if k != "wav"} or None,
        "speech": speech,
        "contact_sheets": [p.name for p in sheets],
    }


def dense_frames(path, work_dir, duration, per_sec=4, max_frames=144):
    """Kinetic type lives *inside* shots, so sample evenly (several frames/sec) instead of per cut."""
    work = Path(work_dir)
    d = work / "dense_frames"
    d.mkdir(parents=True, exist_ok=True)
    rate = min(per_sec, max_frames / max(duration, 0.1))
    _run([_ffmpeg(), "-y", "-i", str(path), "-vf", f"fps={rate:.4f},scale={TILE_W}:-2", "-q:v", "3",
          str(d / "f_%04d.jpg")])
    files = sorted(d.glob("f_*.jpg"))
    tiles = [(f, f"{(i + 0.5) / rate:.2f}s") for i, f in enumerate(files)]
    return [p.name for p in contact_sheets(tiles, work, "dense_sheet")] if tiles else []


def analyze_music(path, work_dir):
    """Beat grid + energy for a music file the kinetic edit should sync to."""
    info = probe(path)
    a = audio_analysis(path, work_dir, [0, info["duration"]]) or {}
    a.pop("wav", None)
    a.pop("cuts_on_beat_ratio", None)
    a["duration"] = round(info["duration"], 2)
    return a


def analyze_footage(folder, work_dir, progress=lambda msg, pct: None):
    folder = Path(folder)
    if not folder.is_dir():
        raise RuntimeError(f"Footage folder not found: {folder}")
    work = Path(work_dir)
    thumbs = work / "footage_frames"
    thumbs.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in list(folder.glob("*")) + list(folder.glob("*/*"))
                   if p.is_file() and p.suffix.lower() in VIDEO_EXT | IMAGE_EXT | AUDIO_EXT)
    clips, tiles = [], []
    for i, p in enumerate(files):
        progress(f"Scanning footage {i + 1}/{len(files)}: {p.name}", 86 + int(8 * i / max(len(files), 1)))
        ext = p.suffix.lower()
        kind = "video" if ext in VIDEO_EXT else "image" if ext in IMAGE_EXT else "audio"
        entry = {"id": f"C{i + 1:03d}", "name": p.name, "path": str(p.resolve()), "kind": kind}
        try:
            if kind != "image":
                entry.update(probe(p))
        except Exception as e:
            entry["error"] = str(e)[:200]
        if kind == "video" and entry.get("duration"):
            # 3 frames per clip (start / middle / end) so Claude sees what happens in it
            d = entry["duration"]
            for j, t in enumerate((d * 0.1, d * 0.5, d * 0.9)):
                f = thumbs / f"{entry['id']}_{j}.jpg"
                if grab_frame(p, t, f):
                    tiles.append((f, f"{entry['id']} @{t:.1f}s"))
            mid = thumbs / f"{entry['id']}_1.jpg"
            if mid.exists():
                entry["color"] = color_stats(mid)
        elif kind == "image":
            f = thumbs / f"{entry['id']}_0.jpg"
            try:
                im = Image.open(p).convert("RGB")
                im.thumbnail((TILE_W, TILE_W * 2))
                im.save(f)
                entry.update(width=im.width, height=im.height)
                tiles.append((f, f"{entry['id']} (image)"))
            except Exception as e:
                entry["error"] = str(e)[:200]
        clips.append(entry)
    sheets = contact_sheets(tiles, work, "footage_sheet") if tiles else []
    return {"folder": str(folder.resolve()), "clips": clips, "contact_sheets": [p.name for p in sheets]}


def download_url(url, work_dir):
    import yt_dlp
    out = Path(work_dir) / "reference.%(ext)s"
    opts = {"outtmpl": str(out), "format": "bv*[height<=1080]+ba/b[height<=1080]/b",
            "merge_output_format": "mp4", "quiet": True, "noplaylist": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    found = sorted(Path(work_dir).glob("reference.*"))
    if not found:
        raise RuntimeError("Download failed")
    return found[0]


# ─── talking videos (story mode) ─────────────────────────────────────────────

def phrases(words, gap=0.45):
    """Group words into phrases (break on pauses and sentence ends) — what Claude cuts between."""
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        if nxt is None or nxt[0] - w[1] > gap or w[2][-1:] in ".?!":
            out.append({"start": float(cur[0][0]), "end": float(cur[-1][1]), "text": " ".join(x[2] for x in cur),
                        "pause_after": round(float(nxt[0] - w[1]), 2) if nxt else None})
            cur = []
    return out


def analyze_talk(items, work_dir, progress=lambda msg, pct: None, do_transcribe=True, model="base", hint=None,
                 langs=None):
    """items: [{path, name?, range?: [in, out] seconds in the file, timeline_start?}] — the clips on the
    user's Resolve timeline (or plain files). Per source: probe, a 540p preview proxy of the used
    range, the transcript with word timings, and sampled frames so Claude can see what's on screen.
    hint: the user's context, given to Whisper so product names ("Mali Daftari") are spelled right."""
    work = Path(work_dir)
    frames_dir = work / "talk_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    sources, tiles = {}, []
    n = len(items)
    for i, it in enumerate(items):
        sid = f"S{i + 1}"
        p = Path(it["path"])
        if not p.is_file():
            raise RuntimeError(f"Footage file not found: {p}")
        base = 5 + int(80 * i / max(n, 1))
        span = 80 / max(n, 1)
        progress(f"{sid} · reading {p.name}", base)
        info = probe(p)
        lo, hi = it.get("range") or (0.0, info["duration"])
        lo, hi = max(0.0, float(lo)), min(float(hi), info["duration"] or float(hi))
        src = {"id": sid, "path": str(p.resolve()), "name": it.get("name") or p.name, "range": [round(lo, 3), round(hi, 3)],
               "timeline_start": it.get("timeline_start"), "track": it.get("track", 1),
               **{k: info.get(k) for k in ("width", "height", "fps", "duration", "has_audio", "aspect")}}
        dur = hi - lo
        progress(f"{sid} · making a preview proxy", base + int(span * 0.1))
        proxy = work / f"proxy_{sid}.mp4"
        r = _run([_ffmpeg(), "-y", "-ss", f"{lo:.3f}", "-t", f"{dur:.3f}", "-i", str(p), "-vf", "scale=-2:540",
                  "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-g", "15", "-c:a", "aac", "-b:a", "96k",
                  "-movflags", "+faststart", str(proxy)])
        if r.returncode == 0 and proxy.exists():
            src["proxy"] = proxy.name
        # what's on screen: ~1 frame / 10 s, 6–24 per source
        k = max(6, min(24, int(dur / 10)))
        for j in range(k):
            t = lo + dur * (j + 0.5) / k
            f = frames_dir / f"{sid}_{j:02d}.jpg"
            if grab_frame(p, t, f):
                tiles.append((f, f"{sid} @{t:.1f}s"))
        if do_transcribe and info.get("has_audio"):
            progress(f"{sid} · transcribing {dur / 60:.1f} min of speech", base + int(span * 0.3))
            wav = work / f"talk_{sid}.wav"
            _run([_ffmpeg(), "-y", "-ss", f"{lo:.3f}", "-t", f"{dur:.3f}", "-i", str(p), "-vn", "-ac", "1",
                  "-ar", "16000", str(wav)])
            if wav.exists():
                try:
                    tr = transcribe(wav, words=True, model=model, offset=lo, hint=hint, langs=langs)
                except Exception as e:
                    tr = {"error": str(e)}
                if tr and tr.get("words"):
                    src["language"] = tr["language"]
                    src["words"] = tr["words"]
                    src["phrases"] = phrases(tr["words"])
                    for ph in src["phrases"]:          # which language each phrase was heard in
                        seg = next((g for g in tr["segments"] if g["start"] - 0.05 <= ph["start"] <= g["end"]), None)
                        ph["lang"] = seg["lang"] if seg else None
                elif tr and tr.get("error"):
                    src["transcribe_error"] = tr["error"][:300]
        sources[sid] = src
    progress("Making contact sheets", 88)
    sheets = contact_sheets(tiles, work, "talk_sheet") if tiles else []
    return {"sources": sources, "contact_sheets": [x.name for x in sheets]}
