"""Kinetic typography: motion script (JSON) -> animation tracks -> Fusion .comp files.

The same tracks drive the browser preview (static/kinetic.js), so what you see in RefCut
is what the Fusion page gets. Node graph per text element:

    Text+ (optionally fed by a Text Follower for per-letter cascade)
      -> Blur (XBlurSize)  -> Transform (Size / Center via XYPath / Angle)  -> Merge (Blend = opacity)

Format details (BezierSpline handles are absolute {frame, value}, XYPath, GlobalOut on every
generator, UseFrameFormatSettings) were checked against real Resolve-saved comps/settings.
"""
import copy
import math
from pathlib import Path

import mage

# CSS-style cubic-bezier easings. Fusion handles map 1:1 onto these control points.
EASINGS = {
    "expo_out": (0.16, 1.0, 0.3, 1.0),     # Apple's signature: fast in, long soft landing
    "quart_out": (0.25, 1.0, 0.5, 1.0),
    "back_out": (0.34, 1.56, 0.64, 1.0),   # slight overshoot — "punch"
    "in_out": (0.65, 0.0, 0.35, 1.0),
    "in": (0.55, 0.0, 1.0, 0.45),          # for exits
    "linear": (1 / 3, 1 / 3, 2 / 3, 2 / 3),   # straight line, expressed as 1/3-point handles
}

DEFAULTS = {"opacity": 1.0, "blur": 0.0, "scale": 1.0, "dx": 0.0, "dy": 0.0, "tracking": 0.0, "rot": 0.0}

# Start values for entrances (animate from these to DEFAULTS). dx/dy are fractions of the frame
# (dy positive = down), blur is px at 1080p, tracking is extra letter-spacing in em.
IN_PRESETS = {
    "fade": {"opacity": 0},
    "rise": {"opacity": 0, "dy": 0.035},
    "drop": {"opacity": 0, "dy": -0.045},
    "blur_in": {"opacity": 0, "blur": 18, "scale": 1.06},
    "punch": {"opacity": 0, "scale": 1.35},
    "grow": {"opacity": 0, "scale": 0.82},
    "slide_left": {"opacity": 0, "dx": 0.06},
    "slide_right": {"opacity": 0, "dx": -0.06},
    "track_in": {"opacity": 0, "tracking": 0.35, "blur": 6},
    "tilt_in": {"opacity": 0, "rot": -6, "dy": 0.03},
    "cascade": {"dy": 0.02},        # per-letter opacity via Text Follower; line rises slightly
    "cut": {},                      # hard on — stop-motion / beat cut
    "pop": {"opacity": 0, "scale": 0.35},     # springs in (defaults to back_out); made for characters
    "peek": {"opacity": 0, "dy": 0.3},        # rises in from low in the frame
}
# End values for exits (animate from rest to these).
OUT_PRESETS = {
    "fade": {"opacity": 0},
    "rise": {"opacity": 0, "dy": -0.035},
    "drop": {"opacity": 0, "dy": 0.045},
    "blur_out": {"opacity": 0, "blur": 18, "scale": 0.96},
    "shrink": {"opacity": 0, "scale": 0.85},
    "punch_out": {"opacity": 0, "scale": 1.25},
    "slide_left": {"opacity": 0, "dx": -0.06},
    "slide_right": {"opacity": 0, "dx": 0.06},
    "track_out": {"opacity": 0, "tracking": 0.35, "blur": 6},
    "cut": {},
    "pop_out": {"opacity": 0, "scale": 0.35},
    "sink": {"opacity": 0, "dy": 0.3},
}

CAP_HEIGHT_EM = 0.70   # cap height as a fraction of the em for typical grotesk fonts
FONT_WEIGHTS = {"thin": 100, "extralight": 200, "light": 300, "regular": 400, "medium": 500,
                "semibold": 600, "bold": 700, "extrabold": 800, "black": 900, "heavy": 900}


def caption_max_chars(size, width, height, fill=0.86):
    """How many characters of caption text fit across the frame at this size (average glyph ≈ 0.56 em)."""
    return max(8, int(fill * width / (size * height / CAP_HEIGHT_EM * 0.56)))


# ─── spec normalisation ──────────────────────────────────────────────────────

def _hex_rgb(h, fallback=(1, 1, 1)):
    try:
        h = h.lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except Exception:
        return fallback


def normalize(spec):
    """Fill defaults and clamp values so both preview and comp writer get a complete spec."""
    s = copy.deepcopy(spec)
    fmt = s.setdefault("format", {})
    fmt.setdefault("width", 1920)
    fmt.setdefault("height", 1080)
    fmt["fps"] = int(round(float(fmt.get("fps", 30))))
    st = s.setdefault("style", {})
    st.setdefault("background", "#000000")
    st.setdefault("color", "#F5F5F7")
    st.setdefault("accent", "#2997FF")
    st.setdefault("font", "Segoe UI")
    st.setdefault("weight", "Semibold")
    st.setdefault("motion", "smooth")
    st["step_frames"] = max(1, int(st.get("step_frames") or (2 if st["motion"] == "stepped" else 1)))
    st.setdefault("easing", "expo_out")
    for i, sc in enumerate(s.setdefault("scenes", [])):
        sc.setdefault("name", f"Scene {i + 1}")
        normalize_scene(sc, st)
    return s


def normalize_scene(sc, st):
    """Defaults and clamps for one scene (a kinetic scene, a story card or an overlay)."""
    sc["dur"] = max(0.2, float(sc.get("dur", 1.5)))
    for el in sc.setdefault("elements", []):
        if el.get("kind") == "mage":
            mage.normalize(el)
        else:
            el["kind"] = "text"
            el["text"] = str(el.get("text", ""))
            el["size"] = max(0.01, float(el.get("size", 0.08)))
            el.setdefault("weight", st["weight"])
            el.setdefault("font", st["font"])
            c = el.get("color") or "color"
            el["rgb"] = _hex_rgb(st["accent"] if c == "accent" else st["color"] if c == "color" else c)
            el["tracking"] = float(el.get("tracking", -0.01))
        el["x"] = float(el.get("x", 0.5))
        el["y"] = float(el.get("y", 0.5))
        i_ = el.get("in") or {}
        typ = i_.get("type") if i_.get("type") in IN_PRESETS else ("pop" if el["kind"] == "mage" else "blur_in")
        ease = i_.get("ease") or ("back_out" if typ == "pop" else st["easing"])
        el["in"] = {"type": typ, "at": max(0.0, float(i_.get("at", 0))), "dur": max(0.0, float(i_.get("dur", 0.6))),
                    "ease": ease if ease in EASINGS else "expo_out",
                    "stagger": max(0.0, float(i_.get("stagger", 0.03 if typ == "cascade" else 0)))}
        o = el.get("out")
        if o and o.get("type") in OUT_PRESETS:
            el["out"] = {"type": o["type"], "dur": max(0.0, float(o.get("dur", 0.35))),
                         "ease": o.get("ease", "in") if o.get("ease", "in") in EASINGS else "in"}
            el["out"]["at"] = float(o.get("at", sc["dur"] - el["out"]["dur"]))
        else:
            el["out"] = None
        d = el.get("drift") or {}
        el["drift"] = {"scale": float(d.get("scale", 1.0)), "dy": float(d.get("dy", 0.0))}
    return sc


# ─── tracks ──────────────────────────────────────────────────────────────────
# A track is a list of keys {f, v, ease}; `ease` shapes the segment *leaving* that key.

def element_tracks(el, fps, scene_frames):
    fi = lambda sec: int(round(sec * fps))
    in_s = fi(el["in"]["at"])
    in_e = in_s + max(1, fi(el["in"]["dur"])) if el["in"]["type"] != "cut" else in_s
    if el["out"]:
        out_s = min(fi(el["out"]["at"]), scene_frames - 1)
        out_e = min(out_s + max(1, fi(el["out"]["dur"])), scene_frames - 1) if el["out"]["type"] != "cut" else out_s
        out_s = max(out_s, in_e)
        out_e = max(out_e, out_s)
    else:
        out_s = out_e = None
    start_vals = IN_PRESETS[el["in"]["type"]]
    end_vals = OUT_PRESETS[el["out"]["type"]] if el["out"] else {}
    rest_end = dict(DEFAULTS, scale=el["drift"]["scale"], dy=el["drift"]["dy"])
    tracks = {}
    for ch, base in DEFAULTS.items():
        keys = []
        if el["in"]["type"] == "cut" and ch == "opacity" and in_s > 0:
            keys += [{"f": in_s - 1, "v": 0.0, "ease": "linear"}, {"f": in_s, "v": base, "ease": "linear"}]
        elif ch in start_vals:
            keys += [{"f": in_s, "v": float(start_vals[ch]), "ease": el["in"]["ease"]},
                     {"f": in_e, "v": base, "ease": "linear"}]
        rest_to = rest_end[ch]
        if rest_to != base:
            if not keys:
                keys.append({"f": in_e, "v": base, "ease": "linear"})
            keys.append({"f": out_s if out_s is not None else scene_frames - 1, "v": rest_to, "ease": "linear"})
        if out_s is not None:
            if el["out"]["type"] == "cut" and ch == "opacity":
                keys += [{"f": out_s - 1, "v": rest_to, "ease": "linear"}, {"f": out_s, "v": 0.0, "ease": "linear"}]
            elif ch in end_vals:
                keys += [{"f": out_s, "v": rest_to, "ease": el["out"]["ease"]},
                         {"f": out_e, "v": float(end_vals[ch]), "ease": "linear"}]
        # drop duplicates at the same frame (keep the later one), keep order
        dedup = {}
        for k in keys:
            dedup[k["f"]] = k
        keys = [dedup[f] for f in sorted(dedup)]
        if len(keys) >= 2 or (keys and keys[0]["v"] != base):
            tracks[ch] = keys
    follower = None
    if el["in"]["type"] == "cascade":
        follower = {"delay": el["in"]["stagger"] * fps,
                    "opacity": [{"f": in_s, "v": 0.0, "ease": el["in"]["ease"]},
                                {"f": in_e, "v": 1.0, "ease": "linear"}]}
    return tracks, follower


def _bezier_y(x, e):
    """Solve a CSS cubic-bezier for y at progress x (0..1)."""
    x1, y1, x2, y2 = EASINGS[e]
    if e == "linear":
        return x
    lo, hi = 0.0, 1.0
    for _ in range(40):
        t = (lo + hi) / 2
        bx = 3 * (1 - t) ** 2 * t * x1 + 3 * (1 - t) * t ** 2 * x2 + t ** 3
        lo, hi = (t, hi) if bx < x else (lo, t)
    t = (lo + hi) / 2
    return 3 * (1 - t) ** 2 * t * y1 + 3 * (1 - t) * t ** 2 * y2 + t ** 3


def evaluate(keys, f):
    if f <= keys[0]["f"]:
        return keys[0]["v"]
    for a, b in zip(keys, keys[1:]):
        if f <= b["f"]:
            p = (f - a["f"]) / max(b["f"] - a["f"], 1e-9)
            return a["v"] + (b["v"] - a["v"]) * _bezier_y(p, a["ease"])
    return keys[-1]["v"]


def bake_stepped(keys, step, end_frame):
    """Stop-motion: hold each value for `step` frames (animation 'on twos/threes')."""
    first, last = keys[0]["f"], keys[-1]["f"]
    out = []
    f = first // step * step          # grid-aligned so every element steps on the same frames
    while f <= last + step:
        v = evaluate(keys, min(f, last))
        out.append({"f": f, "v": v, "ease": "linear"})
        hold_end = min(f + step - 1, end_frame)
        if hold_end > f:
            out.append({"f": hold_end, "v": v, "ease": "linear"})
        f += step
    return out


def compile_scene(sc, fps):
    """A normalised scene -> what the preview and the writers need: frames, per-element tracks,
    and for characters one pose per frame."""
    frames = max(1, int(round(sc["dur"] * fps)))
    els = []
    for el in sc["elements"]:
        tracks, follower = element_tracks(el, fps, frames)
        item = {"el": el, "tracks": tracks, "follower": follower}
        if el["kind"] == "mage":
            item["poses"] = mage.simulate(el, tracks, fps, frames)
        els.append(item)
    return {"name": sc["name"], "frames": frames, "background": sc.get("background"), "elements": els}


def scene_tracks(spec):
    """Everything the preview needs, per scene."""
    spec = normalize(spec)
    fps = spec["format"]["fps"]
    return spec, [compile_scene(sc, fps) for sc in spec["scenes"]]


def characters(scene_data):
    """(poses, variant) for every character in a compiled scene — input for mage.render_sequence."""
    return [(it["poses"], it["el"]["variant"]) for it in scene_data["elements"] if it["el"]["kind"] == "mage"]


# ─── Fusion comp writer ──────────────────────────────────────────────────────

def _num(v):
    return f"{v:.6g}"


def _lua_str(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _spline_lua(keys, transform=lambda v: v, color=(255, 128, 0)):
    """keys -> BezierSpline body. Handles are absolute {frame, value} (verified format)."""
    rows = []
    n = len(keys)
    for i, k in enumerate(keys):
        f, v = k["f"], transform(k["v"])
        parts = [_num(v)]
        if i > 0:                                   # left handle from previous segment's easing
            a = keys[i - 1]
            x1, y1, x2, y2 = EASINGS[a["ease"]]
            av, dt = transform(a["v"]), f - a["f"]
            parts.append(f"LH = {{ {_num(a['f'] + x2 * dt)}, {_num(av + y2 * (v - av))} }}")
        if i < n - 1:                               # right handle from this key's easing
            b = keys[i + 1]
            x1, y1, x2, y2 = EASINGS[k["ease"]]
            bv, dt = transform(b["v"]), b["f"] - f
            parts.append(f"RH = {{ {_num(f + x1 * dt)}, {_num(v + y1 * (bv - v))} }}")
        rows.append(f"\t\t\t\t[{f}] = {{ " + ", ".join(parts) + " }")
    return ("BezierSpline {\n"
            f"\t\t\tSplineColor = {{ Red = {color[0]}, Green = {color[1]}, Blue = {color[2]} }},\n"
            "\t\t\tNameSet = true,\n"
            "\t\t\tKeyFrames = {\n" + ",\n".join(rows) + "\n\t\t\t}\n\t\t}")


class _Comp:
    def __init__(self, width, height, fps, frames):
        self.w, self.h, self.fps, self.frames = width, height, fps, frames
        self.tools = []
        self.y = 0

    def add(self, name, body, x=0, pos=True):
        vi = f"\n\t\t\tViewInfo = OperatorInfo {{ Pos = {{ {x * 110}, {self.y * 33} }} }}," if pos else ""
        self.tools.append(f"\t\t{name} = {body.rstrip()}{vi}\n\t\t}}")
        if pos:
            self.y += 1
        return name

    def spline(self, name, keys, transform=lambda v: v):
        self.tools.append(f"\t\t{name} = " + _spline_lua(keys, transform))
        return f'Input {{ SourceOp = "{name}", Source = "Value", }},'

    def text(self):
        return ("Composition {\n"
                "\tCurrentTime = 0,\n"
                f"\tRenderRange = {{ 0, {self.frames - 1} }},\n"
                f"\tGlobalRange = {{ 0, {self.frames - 1} }},\n"
                "\tCurrentID = 1,\n"
                "\tHiQ = true,\n"
                "\tPlaybackUpdateMode = 0,\n"
                "\tVersion = \"DaVinci Resolve 19\",\n"
                "\tSavedOutputs = 1,\n"
                "\tHeldTools = 0,\n"
                "\tDisabledTools = 0,\n"
                "\tLockedTools = 0,\n"
                "\tAudioOffset = 0,\n"
                "\tResumable = true,\n"
                "\tOutputClips = {\n\t},\n"
                "\tTools = ordered() {\n" + ",\n".join(self.tools) + "\n\t},\n"
                "\tPrefs = {\n\t\tComp = {\n\t\t\tFrameFormat = {\n"
                f"\t\t\t\tWidth = {self.w},\n\t\t\t\tHeight = {self.h},\n\t\t\t\tRate = {self.fps},\n"
                "\t\t\t},\n\t\t},\n\t},\n}\n")


def write_scene_comp(spec, scene_data, index, text_scale, out_path, transparent=False):
    """text_scale: Fusion Text+ Size per (em px / frame width). Calibrated in settings.
    transparent: no background (alpha 0) so the comp sits over footage on an upper track.
    Character elements are skipped here; they are rendered separately (mage.render_sequence)."""
    W, H, fps = spec["format"]["width"], spec["format"]["height"], spec["format"]["fps"]
    st = spec["style"]
    frames = scene_data["frames"]
    go = frames - 1
    step = st["step_frames"] if st["motion"] == "stepped" else 1
    c = _Comp(W, H, fps, frames)
    gen = (f"\t\t\t\tGlobalOut = Input {{ Value = {go}, }},\n"
           f"\t\t\t\tWidth = Input {{ Value = {W}, }},\n\t\t\t\tHeight = Input {{ Value = {H}, }},\n"
           "\t\t\t\tUseFrameFormatSettings = Input { Value = 1, },\n")
    bg = (0, 0, 0) if transparent else _hex_rgb(scene_data.get("background") or st["background"], (0, 0, 0))
    last = c.add("Background1", "Background {\n\t\t\tInputs = {\n" + gen +
                 f"\t\t\t\tTopLeftRed = Input {{ Value = {_num(bg[0])}, }},\n"
                 f"\t\t\t\tTopLeftGreen = Input {{ Value = {_num(bg[1])}, }},\n"
                 f"\t\t\t\tTopLeftBlue = Input {{ Value = {_num(bg[2])}, }},\n"
                 f"\t\t\t\tTopLeftAlpha = Input {{ Value = {0 if transparent else 1}, }},\n\t\t\t}},")

    def maybe_step(keys):
        return bake_stepped(keys, step, go) if step > 1 else keys

    for n, item in enumerate((it for it in scene_data["elements"] if it["el"]["kind"] == "text"), 1):
        el, tracks, follower = item["el"], item["tracks"], item["follower"]
        T = f"T{n}"
        em_px = el["size"] * H / CAP_HEIGHT_EM
        size = em_px / W * text_scale
        tr = lambda ch, v: DEFAULTS[ch] if ch not in tracks else v

        # Text+ (tracking maps to CharacterSpacing: 1 = normal)
        cs_line = f"\t\t\t\tCharacterSpacing = Input {{ Value = {_num(1 + el['tracking'])}, }},\n"
        if "tracking" in tracks:
            cs_line = "\t\t\t\tCharacterSpacing = " + c.spline(f"{T}Spacing", maybe_step(tracks["tracking"]),
                                                               lambda v, b=el["tracking"]: 1 + b + v) + "\n"
        if follower:
            c.spline(f"{T}FollowOpacity", maybe_step(follower["opacity"]))
            c.add(f"{T}Follower", "StyledTextFollower {\n\t\t\tInputs = {\n"
                  f"\t\t\t\tDelay = Input {{ Value = {_num(follower['delay'])}, }},\n"
                  f"\t\t\t\tText = Input {{ Value = StyledText {{ Value = {_lua_str(el['text'])} }}, }},\n"
                  f"\t\t\t\tOpacity1 = Input {{ SourceOp = \"{T}FollowOpacity\", Source = \"Value\", }},\n"
                  "\t\t\t},", pos=False)
            text_in = f"\t\t\t\tStyledText = Input {{ SourceOp = \"{T}Follower\", Source = \"StyledText\", }},\n"
        else:
            text_in = f"\t\t\t\tStyledText = Input {{ Value = {_lua_str(el['text'])}, }},\n"
        r, g, b = el["rgb"]
        txt = c.add(f"{T}Text", "TextPlus {\n\t\t\tInputs = {\n" + gen + text_in +
                    f"\t\t\t\tFont = Input {{ Value = {_lua_str(el['font'])}, }},\n"
                    f"\t\t\t\tStyle = Input {{ Value = {_lua_str(el['weight'])}, }},\n"
                    f"\t\t\t\tSize = Input {{ Value = {_num(size)}, }},\n" + cs_line +
                    f"\t\t\t\tRed1 = Input {{ Value = {_num(r)}, }},\n"
                    f"\t\t\t\tGreen1 = Input {{ Value = {_num(g)}, }},\n"
                    f"\t\t\t\tBlue1 = Input {{ Value = {_num(b)}, }},\n\t\t\t}},", x=n * 3)
        src = txt

        # Blur (px at 1080p -> px at this height)
        if "blur" in tracks:
            blur_in = c.spline(f"{T}BlurSize", maybe_step(tracks["blur"]), lambda v: v * H / 1080)
            src = c.add(f"{T}Blur", "Blur {\n\t\t\tInputs = {\n"
                        "\t\t\t\tFilter = Input { Value = FuID { \"Fast Gaussian\" }, },\n"
                        f"\t\t\t\tXBlurSize = {blur_in}\n"
                        f"\t\t\t\tInput = Input {{ SourceOp = \"{src}\", Source = \"Output\", }},\n\t\t\t}},",
                        x=n * 3)

        # Transform: text is laid out at frame centre, so Size/Angle pivot on the text itself;
        # Center then places it at (x, y) + animated offset. Fusion y is bottom-up.
        cx, cy = el["x"], 1 - el["y"]
        if "dx" in tracks or "dy" in tracks:
            xs = tracks.get("dx") or [{"f": 0, "v": 0.0, "ease": "linear"}]
            ys = tracks.get("dy") or [{"f": 0, "v": 0.0, "ease": "linear"}]
            xin = c.spline(f"{T}PathX", maybe_step(xs), lambda v, cx=cx: cx + v)
            yin = c.spline(f"{T}PathY", maybe_step(ys), lambda v, cy=cy: cy - v)
            c.add(f"{T}Path", "XYPath {\n\t\t\tShowKeyPoints = false,\n\t\t\tDrawMode = \"ModifyOnly\",\n"
                  f"\t\t\tInputs = {{\n\t\t\t\tX = {xin}\n\t\t\t\tY = {yin}\n\t\t\t}},", pos=False)
            center = f"Input {{ SourceOp = \"{T}Path\", Source = \"Value\", }},"
        else:
            center = f"Input {{ Value = {{ {_num(cx)}, {_num(cy)} }}, }},"
        size_in = c.spline(f"{T}Scale", maybe_step(tracks["scale"])) if "scale" in tracks else "Input { Value = 1, },"
        ang_in = c.spline(f"{T}Angle", maybe_step(tracks["rot"]), lambda v: -v) if "rot" in tracks else "Input { Value = 0, },"
        src = c.add(f"{T}Move", "Transform {\n\t\t\tInputs = {\n"
                    f"\t\t\t\tCenter = {center}\n\t\t\t\tSize = {size_in}\n\t\t\t\tAngle = {ang_in}\n"
                    f"\t\t\t\tInput = Input {{ SourceOp = \"{src}\", Source = \"Output\", }},\n\t\t\t}},", x=n * 3)

        # Merge onto the stack; Blend = element opacity
        blend = c.spline(f"{T}Opacity", maybe_step(tracks["opacity"])) if "opacity" in tracks else "Input { Value = 1, },"
        last = c.add(f"{T}Merge", "Merge {\n\t\t\tInputs = {\n"
                     f"\t\t\t\tBackground = Input {{ SourceOp = \"{last}\", Source = \"Output\", }},\n"
                     f"\t\t\t\tForeground = Input {{ SourceOp = \"{src}\", Source = \"Output\", }},\n"
                     f"\t\t\t\tBlend = {blend}\n"
                     "\t\t\t\tPerformDepthMerge = Input { Value = 0, },\n\t\t\t},", x=0)

    c.add("MediaOut1", "MediaOut {\n\t\t\tInputs = {\n\t\t\t\tIndex = Input { Value = \"0\", },\n"
          f"\t\t\t\tInput = Input {{ SourceOp = \"{last}\", Source = \"Output\", }},\n\t\t\t}},", x=0)
    Path(out_path).write_text(c.text(), encoding="utf-8")
    return out_path


def write_all(spec, out_dir, text_scale=0.5):
    spec, scenes = scene_tracks(spec)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for i, sd in enumerate(scenes, 1):
        safe = "".join(ch if ch.isalnum() else "_" for ch in sd["name"])[:30]
        p = out_dir / f"scene_{i:02d}_{safe}.comp"
        write_scene_comp(spec, sd, i, text_scale, p)
        files.append({"path": str(p.resolve()), "frames": sd["frames"], "name": sd["name"]})
    return spec, files


def preview_payload(spec):
    spec, scenes = scene_tracks(spec)
    return {"spec": spec, "scenes": scenes, "easings": EASINGS,
            "cap_height_em": CAP_HEIGHT_EM, "weights": FONT_WEIGHTS}
