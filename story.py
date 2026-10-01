"""Talking videos: long footage (dev logs, feature walkthroughs) -> a tight edit.

Claude writes an EDIT PLAN (JSON): an ordered timeline of `clip` items (ranges of your footage) and
`card` items (kinetic scenes on their own, e.g. a hook or a section title), plus `overlays`
(callouts, lower thirds, Mage reactions) anchored to a timeline item. This module turns the plan
plus the word-level transcript into frames:

    V1  clips (cuts snapped to word boundaries, optional punch-in zoom) and cards (Fusion comps)
    V2  overlay text (transparent Fusion comps)
    V3  characters (Mage) from cards and overlays (transparent PNG sequences)
    V4  burned captions (one transparent Fusion comp per caption, editable Text+)

With motion B-roll (broll.py) the clips take V2, directly above the footage, and the other tracks move up one.

The same compiled timeline drives the browser preview (static/story.js) and the Resolve build.
"""
import copy
from pathlib import Path

import kinetic
import mage

PAD_IN, PAD_OUT = 0.06, 0.12     # breathing room around the first / last word of a cut (s)
SNAP_REACH = 0.6                  # only snap when a word boundary is this close (s)
TRACK_V1 = 1


def tracks(has_broll, has_cutaways=False):
    """Upper-track numbers, bottom to top: canvas + cutaways of your own B-roll, motion B-roll, then
    text, Mage and captions. Tracks that aren't needed don't exist."""
    order = (["canvas", "cutaway"] if has_cutaways else []) + (["broll"] if has_broll else []) + ["text", "char", "caps"]
    return {name: i + 2 for i, name in enumerate(order)}


# ─── spec ────────────────────────────────────────────────────────────────────

def normalize(spec, sources):
    s = kinetic.normalize({k: v for k, v in spec.items() if k != "scenes"} | {"scenes": []})
    st = s["style"]
    items, ids = [], set()
    for i, it in enumerate(spec.get("timeline") or []):
        it = copy.deepcopy(it)
        it["type"] = "card" if it.get("type") == "card" else "clip"
        it["id"] = str(it.get("id") or f"t{i + 1}")
        while it["id"] in ids:
            it["id"] += "b"
        ids.add(it["id"])
        it.setdefault("name", f"{'Card' if it['type'] == 'card' else 'Clip'} {i + 1}")
        if it["type"] == "card":
            kinetic.normalize_scene(it, st)
        else:
            src = sources.get(it.get("source"))
            if not src:
                continue                          # unknown source: drop rather than break the build
            lo, hi = src["range"]
            a = min(max(float(it.get("in", lo)), lo), hi)
            b = min(max(float(it.get("out", hi)), lo), hi)
            if b - a < 0.2:
                continue
            it["in"], it["out"] = round(a, 3), round(b, 3)
            it["zoom"] = min(2.0, max(1.0, float(it.get("zoom") or 1.0)))
            it["snap"] = it.get("snap", True) is not False
        items.append(it)
    s["timeline"] = items
    ovs = []
    for i, ov in enumerate(spec.get("overlays") or []):
        ov = copy.deepcopy(ov)
        if ov.get("anchor") not in ids:
            continue
        ov.setdefault("name", f"Overlay {i + 1}")
        ov["at"] = max(0.0, float(ov.get("at", 0)))
        kinetic.normalize_scene(ov, st)
        ovs.append(ov)
    s["overlays"] = ovs
    cuts = []
    for i, c in enumerate(spec.get("cutaways") or []):       # your own B-roll shown over the voice
        src = sources.get(c.get("source"))
        if c.get("anchor") not in ids or not src:
            continue
        lo, hi = src["range"]
        a = min(max(float(c.get("in", lo)), lo), hi)
        b = min(max(float(c.get("out", hi)), lo), hi)
        if b - a < 0.25:
            continue
        inset = c.get("inset") if isinstance(c.get("inset"), dict) else None
        if inset:
            inset = {"scale": min(1.0, max(0.2, float(inset.get("scale", 0.62)))),
                     "canvas": str(inset.get("canvas") or "#F4F3EF"), "y": min(0.8, max(0.2, float(inset.get("y", 0.47))))}
        cuts.append({"name": str(c.get("name") or f"Cutaway {i + 1}"), "anchor": c["anchor"],
                     "at": max(0.0, float(c.get("at", 0))), "source": c["source"], "in": round(a, 3), "out": round(b, 3),
                     "inset": inset})
    s["cutaways"] = cuts
    s["chapters"] = [{"title": str(c["title"]).strip(), "anchor": c["anchor"]} for c in spec.get("chapters") or []
                     if isinstance(c, dict) and c.get("anchor") in ids and str(c.get("title") or "").strip()]
    cap = spec.get("captions") or {}
    subtle = cap.get("style") == "subtle"                    # small, quiet subtitles (long-form) vs big pop captions
    s["captions"] = {"style": "subtle" if subtle else "pop",
                     "y": float(cap.get("y", 0.9 if subtle else 0.8)), "size": float(cap.get("size", 0.021 if subtle else 0.042)),
                     "max_words": int(cap.get("max_words", 8 if subtle else 3)),
                     "weight": cap.get("weight", "Medium" if subtle else "Bold"), "color": cap.get("color", "color")}
    fixes = []
    for f in spec.get("fixes") or []:
        try:
            fx = {"source": f["source"], "start": float(f["start"]), "end": float(f["end"]), "text": str(f["text"]).strip()}
        except (KeyError, TypeError, ValueError):
            continue
        if fx["source"] in sources and fx["end"] > fx["start"] and fx["text"]:
            fixes.append(fx)
    s["fixes"] = fixes
    s.setdefault("notes", [])
    s.setdefault("questions", [])
    return s


def fixed_words(src, sid, fixes):
    """A source's words with the plan's corrections applied: the misheard words in each fix's span
    are replaced by the corrected text, spread over the same stretch of time by word length."""
    words = list(src.get("words") or [])
    for f in fixes:
        if f["source"] != sid:
            continue
        hit = [w for w in words if w[0] >= f["start"] - 0.06 and w[1] <= f["end"] + 0.06]
        if not hit:
            continue
        a, b = hit[0][0], hit[-1][1]
        new, total, t = f["text"].split(), 0, a
        total = sum(len(x) + 1 for x in new)
        rep = []
        for x in new:
            d = (b - a) * (len(x) + 1) / total
            rep.append([round(t, 3), round(t + d, 3), x])
            t += d
        i = words.index(hit[0])
        words[i:i + len(hit)] = rep
    return words


# ─── word snapping ───────────────────────────────────────────────────────────

def _snap_in(t, words, lo):
    """Start the cut just before the first word at t (never mid-word)."""
    i = next((k for k, w in enumerate(words) if w[1] > t + 0.02), None)
    if i is None:
        return t
    if words[i][0] < t - 0.25 and i + 1 < len(words):    # t is well inside word i: start at the next one
        i += 1
    start = words[i][0]
    if abs(start - t) > SNAP_REACH:
        return t
    prev_end = words[i - 1][1] if i > 0 else lo
    return min(start, max(lo, prev_end, start - PAD_IN))


def _snap_out(t, words, hi):
    """End the cut just after the last word at t (keep a word that's nearly finished)."""
    j = next((k for k in range(len(words) - 1, -1, -1) if words[k][0] < t - 0.02), None)
    if j is None:
        return t
    if words[j][1] > t + 0.25 and j > 0:                  # t is well before word j ends: drop it
        j -= 1
    end = words[j][1]
    if abs(end - t) > SNAP_REACH:
        return t
    nxt = words[j + 1][0] if j + 1 < len(words) else hi
    return max(end, min(hi, nxt, end + PAD_OUT))


def snapped_range(it, src):
    words = src.get("words") or []
    a, b = it["in"], it["out"]
    if it.get("snap") and words:
        lo, hi = src["range"]
        a, b = _snap_in(a, words, lo), _snap_out(b, words, hi)
        if b - a < 0.2:
            a, b = it["in"], it["out"]
    return a, b


# ─── compile ─────────────────────────────────────────────────────────────────

def compile_story(spec, sources, captions="burned", broll=None):
    """Plan -> frame-exact timeline. Returns the normalised spec and the compiled layers.
    broll: motion B-roll clips ({anchor, at, dur, frames, ...}), each pinned to a timeline item."""
    spec = normalize(spec, sources)
    if spec["fixes"]:                                  # captions use the corrected words; cuts keep the real timings
        sources = {sid: src | {"said": fixed_words(src, sid, spec["fixes"])} for sid, src in sources.items()}
    fps = spec["format"]["fps"]
    st = spec["style"]
    v1, starts, rec = [], {}, 0
    for it in spec["timeline"]:
        starts[it["id"]] = rec
        if it["type"] == "card":
            sd = kinetic.compile_scene(it, fps)
            v1.append({"kind": "card", "id": it["id"], "name": it["name"], "start": rec, "frames": sd["frames"],
                       "scene": sd})
        else:
            src = sources[it["source"]]
            a, b = snapped_range(it, src)
            frames = max(1, int(round((b - a) * fps)))
            v1.append({"kind": "clip", "id": it["id"], "name": it["name"], "start": rec, "frames": frames,
                       "source": it["source"], "src_in": round(a, 3), "src_out": round(a + frames / fps, 3),
                       "zoom": it["zoom"]})
        rec += v1[-1]["frames"]
    total = rec
    by_id = {x["id"]: x for x in v1}

    overlays = []
    for ov in spec["overlays"]:
        host = by_id[ov["anchor"]]
        start = host["start"] + int(round(ov["at"] * fps))
        if start >= total:
            continue
        sd = kinetic.compile_scene(ov, fps)
        sd["frames"] = min(sd["frames"], total - start)
        overlays.append({"name": ov["name"], "start": start, "frames": sd["frames"], "scene": sd, "layer": "overlay"})

    fmt = spec["format"]
    caps = caption_chunks(v1, sources, fps, spec["captions"]["max_words"] if captions == "burned" else 7,
                          kinetic.caption_max_chars(spec["captions"]["size"], fmt["width"], fmt["height"])
                          if captions == "burned" else 42)
    if captions == "burned":
        c = spec["captions"]
        cards = [(o["start"], o["start"] + o["frames"]) for o in overlays if o["scene"].get("background")]
        light = [(host["start"] + int(round(cw["at"] * fps)), host["start"] + int(round((cw["at"] + cw["out"] - cw["in"]) * fps)))
                 for cw in spec["cutaways"] for host in [by_id[cw["anchor"]]]
                 if cw["inset"] and sum(kinetic._hex_rgb(cw["inset"]["canvas"])) > 1.5]
        over = lambda spans, ch: any(a < ch["start"] + ch["frames"] / 2 < b for a, b in spans)
        for ch in caps:
            if over(cards, ch):                   # a text card is on screen: it already shows the words
                continue
            dark = over(light, ch)                # on a light inset canvas: dark subtitles
            sc = kinetic.normalize_scene({"name": "Caption", "dur": ch["frames"] / fps, "elements": [
                {"text": ch["text"].lower() if c["style"] == "subtle" else ch["text"], "x": 0.5, "y": c["y"],
                 "size": c["size"], "weight": c["weight"], "color": "#111111" if dark else c["color"], "tracking": 0 if c["style"] == "subtle" else -0.01,
                 "in": {"type": "fade", "at": 0, "dur": 0.1} if c["style"] == "subtle"
                 else {"type": "grow", "at": 0, "dur": 0.12, "ease": "expo_out"}, "out": None}]}, st)
            sd = kinetic.compile_scene(sc, fps)
            overlays.append({"name": "Caption", "start": ch["start"], "frames": sd["frames"], "scene": sd,
                             "layer": "caption"})
    cutaways = []
    for c in spec["cutaways"]:
        host = by_id[c["anchor"]]
        start = host["start"] + int(round(c["at"] * fps))
        frames = min(int(round((c["out"] - c["in"]) * fps)), total - start)
        if frames > 0:
            cutaways.append({"name": c["name"], "start": start, "frames": frames, "source": c["source"],
                             "src_in": c["in"], "inset": c["inset"]})
    chapters = [{"title": c["title"], "start": by_id[c["anchor"]]["start"]} for c in spec["chapters"]]
    chapters.sort(key=lambda c: c["start"])
    clips = []
    for b in broll or []:
        host = by_id.get(b.get("anchor"))
        if not host:
            continue                              # its timeline item was deleted
        start = host["start"] + int(round(float(b.get("at", 0)) * fps))
        frames = min(int(round(float(b["dur"]) * fps)), int(b.get("frames") or 10 ** 9), total - start)
        if frames > 0:
            clips.append({k: b.get(k) for k in ("id", "title", "line", "kind", "file", "preview")}
                         | {"start": start, "frames": frames, "name": b.get("title") or "B-roll"})
    return spec, {"fps": fps, "total": total, "v1": v1, "overlays": overlays, "captions": caps, "broll": clips,
                  "cutaways": cutaways, "chapters": chapters}


def chapters_text(chapters, fps):
    """YouTube description chapters: "0:00 Title" lines (YouTube needs the first one at 0:00)."""
    out = []
    for i, c in enumerate(chapters):
        sec = 0 if i == 0 else int(c["start"] / fps)
        out.append((f"{sec // 3600}:{sec // 60 % 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}")
                   + " " + c["title"])
    return "\n".join(out)


def speech_key(tl, sources, job_dir, sr=48000):
    """The edit's speech as one mono track (from the analysis wavs), used to duck music under the voice."""
    import numpy as np
    import librosa
    fps = tl["fps"]
    out = np.zeros(int(tl["total"] / fps * sr) + 1, np.float32)
    cache = {}
    for x in tl["v1"]:
        if x["kind"] != "clip":
            continue
        wav = Path(job_dir) / f"talk_{x['source']}.wav"
        if not wav.is_file():
            continue
        if x["source"] not in cache:
            cache[x["source"]] = librosa.load(str(wav), sr=sr, mono=True)[0]
        y, lo = cache[x["source"]], sources[x["source"]]["range"][0]
        a = int((x["src_in"] - lo) * sr)
        seg = y[max(a, 0):max(a, 0) + int(x["frames"] / fps * sr)]
        o = int(x["start"] / fps * sr)
        out[o:o + len(seg)] = seg[:len(out) - o]
    return out


def music_bed(tl, sources, job_dir, music_path):
    """Music for the whole edit, lowered under speech. Cached by the cut it was made for."""
    import hashlib
    import soundfile as sf
    import mascot
    key = hashlib.sha1(repr([(x["kind"], x["start"], x["frames"], x.get("source"), x.get("src_in")) for x in tl["v1"]]
                            + [str(music_path)]).encode()).hexdigest()[:12]
    out = Path(job_dir) / "audio" / f"music_bed_{key}.wav"
    if not out.is_file():
        out.parent.mkdir(parents=True, exist_ok=True)
        bed = mascot.duck(music_path, speech_key(tl, sources, job_dir), tl["total"] / tl["fps"], bed_db=-17.0, under_db=-27.0)
        sf.write(out, bed, 48000, subtype="PCM_16")
    return out


def timeline_words(v1, sources, fps):
    """Transcript words that survive the edit, re-timed onto the new timeline (frames)."""
    words = []
    for x in v1:
        if x["kind"] != "clip":
            continue
        end_f = x["start"] + x["frames"]
        src = sources[x["source"]]
        for ws, we, w in src.get("said") or src.get("words") or []:
            if ws >= x["src_in"] - 0.02 and we <= x["src_out"] + 0.08:
                s = x["start"] + int(round((ws - x["src_in"]) * fps))
                e = min(end_f, x["start"] + int(round((we - x["src_in"]) * fps)))
                words.append({"s": max(x["start"], s), "e": max(e, s + 1), "w": w.strip(), "end_f": end_f, "id": x["id"]})
    return words


def caption_chunks(v1, sources, fps, max_words, max_chars=42):
    """The edit's words grouped into short caption chunks (break on pauses, sentence ends and long chunks)."""
    words = timeline_words(v1, sources, fps)
    chunks, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        wide = nxt is not None and len(" ".join(x["w"] for x in cur)) + 1 + len(nxt["w"]) > max_chars
        brk = (nxt is None or len(cur) >= max_words or wide or w["w"][-1:] in ".?!," or nxt["s"] - w["e"] > 0.3 * fps
               or w["e"] - cur[0]["s"] > 1.6 * fps or nxt["end_f"] != w["end_f"])
        if brk:
            start = cur[0]["s"]
            hold = w["e"] + int(0.25 * fps)
            end = min(nxt["s"] if nxt else hold, hold, w["end_f"])
            if end > start and cur:
                chunks.append({"start": start, "frames": end - start, "text": " ".join(x["w"] for x in cur)})
            cur = []
    return chunks


def srt(caps, fps):
    def tc(f):
        ms = int(round(f / fps * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    return "\n".join(f"{i}\n{tc(c['start'])} --> {tc(c['start'] + c['frames'])}\n{c['text']}\n"
                     for i, c in enumerate(caps, 1))


# ─── preview + build ─────────────────────────────────────────────────────────

def preview_payload(spec, sources, jid, captions, broll=None, music_path=None, job_dir=None):
    spec, tl = compile_story(spec, sources, captions, broll)
    tl["chapters_text"] = chapters_text(tl["chapters"], tl["fps"])
    tl["music_url"] = None
    if music_path and job_dir and Path(music_path).is_file():
        tl["music_url"] = f"/api/jobs/{jid}/file/audio/{music_bed(tl, sources, job_dir, music_path).name}"
    for b in tl["broll"]:
        b["url"] = f"/api/jobs/{jid}/file/{b.pop('preview')}" if b.get("preview") else None
        b.pop("file", None)
    return {"spec": spec, **tl, "easings": kinetic.EASINGS, "cap_height_em": kinetic.CAP_HEIGHT_EM,
            "weights": kinetic.FONT_WEIGHTS,
            "sources": {k: {"url": f"/api/jobs/{jid}/file/{s['proxy']}", "offset": s["range"][0], "name": s["name"]}
                        for k, s in sources.items() if s.get("proxy")}}


def write_build(spec, sources, job_dir, text_scale, captions, broll=None, music_path=None):
    """Write comps, character sequences and captions; return the bridge body minus the placeholder."""
    spec, tl = compile_story(spec, sources, captions, broll)
    tr = tracks(bool(tl["broll"]), bool(tl["cutaways"]))
    fmt, fps = spec["format"], spec["format"]["fps"]
    W, H = fmt["width"], fmt["height"]
    out = Path(job_dir) / "build"
    comps, seqs = out / "comps", out / "characters"
    comps.mkdir(parents=True, exist_ok=True)
    step = spec["style"]["step_frames"] if spec["style"]["motion"] == "stepped" else 1

    def safe(name):
        return "".join(ch if ch.isalnum() else "_" for ch in name)[:30]

    v1, layers, markers, n = [], [], [], 0

    def add_scene(sd, start, name, on_v1, text_track):
        nonlocal n
        n += 1
        has_text = any(e["el"]["kind"] == "text" for e in sd["elements"])
        chars = kinetic.characters(sd)
        if on_v1 or has_text:
            p = comps / f"{n:03d}_{safe(name)}.comp"
            kinetic.write_scene_comp(spec, sd, n, text_scale, p, transparent=not on_v1 and not sd.get("background"))
            entry = {"kind": "comp", "comp": str(p.resolve()), "frames": sd["frames"], "name": name, "color": "Yellow"}
            (v1.append(entry) if on_v1 else layers.append(entry | {"track": text_track, "start": start}))
        if chars:
            seq = mage.render_sequence(chars, W, H, sd["frames"], seqs / f"{n:03d}_{safe(name)}", step)
            layers.append({"kind": "sequence", "track": tr["char"], "start": start, "name": name + " · Mage"} | seq)

    for x in tl["v1"]:
        if x["kind"] == "card":
            add_scene(x["scene"], x["start"], x["name"], True, TRACK_V1)
            markers.append({"frame": x["start"], "color": "Blue", "name": x["name"]})
        else:
            src = sources[x["source"]]
            v1.append({"kind": "clip", "path": src["path"], "src_in": x["src_in"], "src_out": x["src_out"],
                       "frames": x["frames"], "zoom": x["zoom"], "name": x["name"]})
    for ov in tl["overlays"]:
        if ov["layer"] == "caption":
            n += 1
            p = comps / f"{n:03d}_caption.comp"
            kinetic.write_scene_comp(spec, ov["scene"], n, text_scale, p, transparent=True)
            layers.append({"kind": "comp", "track": tr["caps"], "start": ov["start"], "comp": str(p.resolve()),
                           "frames": ov["frames"], "name": "Caption", "color": "Tan"})
        else:
            add_scene(ov["scene"], ov["start"], ov["name"], False, tr["text"])
            markers.append({"frame": ov["start"], "color": "Yellow", "name": ov["name"]})
    for c in tl["cutaways"]:
        if c["inset"]:                                # footage shrunk onto a plain canvas
            n += 1
            p = comps / f"{n:03d}_canvas.comp"
            kinetic.write_scene_comp(spec, {"name": "Canvas", "frames": c["frames"], "background": c["inset"]["canvas"],
                                            "elements": []}, n, text_scale, p)
            layers.append({"kind": "comp", "track": tr["canvas"], "start": c["start"], "comp": str(p.resolve()),
                           "frames": c["frames"], "name": "Canvas", "color": "Beige"})
        layers.append({"kind": "clip", "track": tr["cutaway"], "start": c["start"], "frames": c["frames"],
                       "path": sources[c["source"]]["path"], "src_in": c["src_in"], "name": c["name"],
                       "zoom": c["inset"]["scale"] if c["inset"] else 1.0,
                       "tilt": round((0.5 - c["inset"]["y"]) * H) if c["inset"] else 0})
    for ch in tl["chapters"]:
        markers.append({"frame": ch["start"], "color": "Green", "name": "Chapter · " + ch["title"]})
    for b in tl["broll"]:
        layers.append({"kind": "media", "track": tr["broll"], "start": b["start"], "frames": b["frames"],
                       "path": b["file"], "alpha": b["kind"] == "panel", "name": b["name"]})
        markers.append({"frame": b["start"], "color": "Purple", "name": "B-roll · " + b["name"]})
    names = {tr["text"]: "Callouts", tr["char"]: "Mage", tr["caps"]: "Captions"} | (
        {tr["broll"]: "Motion B-roll"} if tl["broll"] else {}) | (
        {tr["canvas"]: "Canvas", tr["cutaway"]: "B-roll"} if tl["cutaways"] else {})
    audio = []
    if music_path and Path(music_path).is_file():
        audio.append({"path": str(music_bed(tl, sources, job_dir, music_path).resolve()), "track": 2, "name": "Music bed"})
    srt_path = None
    if captions == "srt" and tl["captions"]:
        srt_path = out / "captions.srt"
        srt_path.write_text(srt(tl["captions"], fps), encoding="utf-8")
        srt_path = str(srt_path.resolve())
    return spec, {"format": fmt, "v1": v1, "layers": layers, "markers": markers, "srt": srt_path,
                  "track_names": {str(k): v for k, v in names.items()}, "audio": audio,
                  "chapters_text": chapters_text(tl["chapters"], fps),
                  "counts": {"broll": len(tl["broll"]), "cutaways": len(tl["cutaways"]), "chapters": len(tl["chapters"]),"clips": sum(x["kind"] == "clip" for x in v1), "cards": sum(x["kind"] == "comp" for x in v1),
                             "overlays": sum(1 for o in tl["overlays"] if o["layer"] == "overlay"),
                             "captions": len(tl["captions"]), "characters": sum(l["kind"] == "sequence" for l in layers),
                             "seconds": round(tl["total"] / fps, 2)}}
