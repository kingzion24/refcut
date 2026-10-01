"""Mage — Mali Daftari's mascot — as a motion character.

A port of the app's `MageFace` (Mali-Daftari-Mobile/mali_daftari/lib/presentation/widgets/
animated_icons/mage_face.dart, spec in docs/ui/08_MAGE.md): a brand-blue round head with eyes only
and browline glasses. Same 256-grid geometry, moods, blinks, glances, glasses on/off and the nerdy
glasses push, simulated frame by frame so every run of a script gives the same animation.

`simulate()` turns a character element (position, entrance/exit, moves, beats) into one pose per
frame. The browser preview (static/kinetic.js `drawMage`) and `render_sequence()` (transparent PNG
sequence for an upper Resolve track) both draw those poses, so the preview matches what Resolve gets.

Pose per frame (list of 13 numbers):
    X, Y      head centre, fractions of the frame (0,0 = top-left)
    D         head diameter, fraction of frame height
    rot       whole-head rotation, degrees
    op        opacity 0..1
    gRot      glasses rotation (radians, about the spine point 128,104)
    gTy       glasses vertical offset (grid units, + = down)
    gS        glasses scale (0 = off)
    eDx, eDy  eye offset (grid units)
    hL, hR    eye heights (grid units; 44 = open)
    happy     0..1, upturned arcs instead of eyes
"""
import math
import os
import random
import shutil
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

VARIANTS = {"primary": ("#0077B6", "#FFFFFF"), "reversed": ("#FFFFFF", "#0077B6")}
MOODS = ("idle", "thinking", "talking", "happy", "focused", "confused", "searching")
ACTIONS = ("blink", "glasses_off", "glasses_on", "signature", "hop", "glance", "push")

# (dx, dy, hL, hR, happy, tilt, slip) — mage_face.dart `_poses`
POSES = {
    "idle": (0, 0, 1, 1, 0, 0, 0),
    "thinking": (6, -8, 0.9, 0.9, 0, 0, -3),
    "talking": (0, 0, 0.84, 0.84, 0, 0, 0),
    "happy": (0, 0, 1, 1, 1, 0, 0),
    "focused": (0, 0, 0.55, 0.55, 0, 0, 0),
    "confused": (0, 0, 0.6, 1, 0, -7 * math.pi / 180, 8),
    "searching": (0, -2, 0.9, 0.9, 0, 0, 0),
}
REACT_SECS = 0.5
PUSH_EVERY = 2.6


def _cubic(x1, y1, x2, y2):
    def f(x):
        if x <= 0:
            return 0.0
        if x >= 1:
            return 1.0
        lo, hi = 0.0, 1.0
        for _ in range(30):
            t = (lo + hi) / 2
            bx = 3 * (1 - t) ** 2 * t * x1 + 3 * (1 - t) * t ** 2 * x2 + t ** 3
            lo, hi = (t, hi) if bx < x else (lo, t)
        t = (lo + hi) / 2
        return 3 * (1 - t) ** 2 * t * y1 + 3 * (1 - t) * t ** 2 * y2 + t ** 3
    return f


ease_in_out = _cubic(0.42, 0, 0.58, 1)      # Flutter Curves.easeInOut
ease_out = _cubic(0, 0, 0.58, 1)            # Flutter Curves.easeOut


def _push_slip(t):
    if t < 0.35:
        return 6 * ease_in_out(t / 0.35)
    if t < 0.45:
        return 6
    if t < 0.55:
        return 6 - 8 * ease_out((t - 0.45) / 0.1)
    if t < 0.8:
        return -2 + 2 * ease_in_out((t - 0.55) / 0.25)
    return 0


# ─── spec ────────────────────────────────────────────────────────────────────

def normalize(el):
    """Character-specific fields. The shared transform fields (in / out / drift) are normalised by
    kinetic.normalize, which calls this for elements with kind == "mage"."""
    el["variant"] = el.get("variant") if el.get("variant") in VARIANTS else "primary"
    el["mood"] = el.get("mood") if el.get("mood") in MOODS else "idle"
    el["glasses"] = el.get("glasses", True) is not False
    beats = []
    for b in el.get("beats") or []:
        if not isinstance(b, dict):
            continue
        at = max(0.0, float(b.get("at", 0)))
        if b.get("mood") in MOODS:
            beats.append({"at": at, "mood": b["mood"]})
        if b.get("do") in ACTIONS:
            beats.append({"at": at, "do": b["do"], "dir": -1 if float(b.get("dir", 1)) < 0 else 1})
    el["beats"] = sorted(beats, key=lambda b: b["at"])
    moves = []
    for m in el.get("moves") or []:
        if not isinstance(m, dict):
            continue
        mv = {"at": max(0.0, float(m.get("at", 0))), "dur": max(0.0, float(m.get("dur", 0.6))),
              "ease": m.get("ease", "expo_out")}
        for k in ("x", "y", "size"):
            if m.get(k) is not None:
                mv[k] = float(m[k])
        moves.append(mv)
    el["moves"] = sorted(moves, key=lambda m: m["at"])
    el["size"] = max(0.02, float(el.get("size", 0.3)))
    el.setdefault("seed", 7)
    return el


# ─── simulation ──────────────────────────────────────────────────────────────

def _move_track(el, key, fps, easings):
    """Base value of x / y / size over time: starts at the element's value, each move eases to its target."""
    import kinetic
    v, keys = el[key], []
    for m in el["moves"]:
        if key not in m:
            continue
        f0, f1 = int(round(m["at"] * fps)), int(round((m["at"] + m["dur"]) * fps))
        keys.append({"f": f0, "v": v, "ease": m["ease"] if m["ease"] in easings else "expo_out"})
        keys.append({"f": max(f1, f0 + 1), "v": m[key], "ease": "linear"})
        v = m[key]
    if not keys:
        return lambda f: el[key]
    return lambda f: kinetic.evaluate(keys, f)


def simulate(el, tracks, fps, frames):
    """One pose per frame for a normalised mage element. `tracks` are its kinetic transform tracks
    (opacity / scale / dx / dy / rot from the in / out / drift presets)."""
    import kinetic
    rng = random.Random(f"{el['seed']}:{el['x']:.3f}:{el['y']:.3f}")
    dt = 1 / fps
    k = 1 - math.exp(-dt / 0.06)
    ev = lambda ch, f: kinetic.evaluate(tracks[ch], f) if ch in tracks else kinetic.DEFAULTS[ch]
    bx, by, bs = (_move_track(el, key, fps, kinetic.EASINGS) for key in ("x", "y", "size"))

    mood = el["mood"]
    dx, dy, hl, hr, happy, tilt, slip = POSES[mood]
    glasses_on = el["glasses"]
    g = 1.0 if glasses_on else 0.0
    blink = talk = glance = react = scan_dx = scan_tilt = push = 0.0
    blink_t = glance_t = react_t = push_t = -1.0
    glance_dir = 1
    clock = 0.0
    next_blink = 0.8 + rng.random() * 2.5
    double_at = None
    next_glance = 7 + rng.random() * 3
    timers = []            # (time, fn) for delayed actions such as the signature's glasses-back
    env = el.get("voice_env")       # loudness of the generated voice per frame (mascot videos)
    beats = list(el["beats"])
    out = []

    for f in range(frames):
        t = f * dt
        # beats scheduled for this frame
        while beats and beats[0]["at"] <= t + 1e-6:
            b = beats.pop(0)
            if "mood" in b:
                mood = b["mood"]
            else:
                a = b["do"]
                if a == "blink":
                    blink_t = 0
                elif a in ("glasses_off", "glasses_on"):
                    want = a == "glasses_on"
                    if want != glasses_on:
                        glasses_on, blink_t = want, 0
                elif a == "signature":
                    if glasses_on:
                        glasses_on, blink_t = False, 0
                        timers.append((t + 1.4, "glasses_on"))
                elif a == "hop":
                    react_t = 0
                elif a == "glance":
                    glance_t, glance_dir = 0, b["dir"]
                elif a == "push":
                    push_t = 0
        for tm in [x for x in timers if x[0] <= t + 1e-6]:
            timers.remove(tm)
            if tm[1] == "glasses_on" and not glasses_on:
                glasses_on, blink_t = True, 0
        # idle life: blinks every 3–6 s (sometimes double), a glance every 7–10 s while idle
        if t >= next_blink:
            blink_t = 0
            double_at = t + 0.26 if rng.random() < 0.15 else None
            next_blink = t + 3 + rng.random() * 3
        if double_at is not None and t >= double_at:
            blink_t, double_at = 0, None
        if t >= next_glance:
            if mood == "idle" and glance_t < 0:
                glance_t, glance_dir = 0, 1 if rng.random() < 0.5 else -1
            next_glance = t + 7 + rng.random() * 3

        p = POSES[mood]
        dx += (p[0] - dx) * k
        dy += (p[1] - dy) * k
        hl += (p[2] - hl) * k
        hr += (p[3] - hr) * k
        happy += (p[4] - happy) * k
        tilt += (p[5] - tilt) * k
        slip += (p[6] - slip) * k
        g_to = 1.0 if glasses_on else 0.0
        g = min(g_to, g + dt / 0.28) if g < g_to else max(g_to, g - dt / 0.28)
        talking, searching = mood == "talking", mood == "searching"
        if talking and env:                 # eyes squash with each syllable of the real voice
            talk += (-0.32 * (env[f] if f < len(env) else 0.0) - talk) * 0.6
        elif talking:
            talk = math.sin(clock * 2 * math.pi * 5) * 0.08
        else:
            talk += (0 - talk) * k
        if searching:
            s = max(-1.0, min(1.0, 1.6 * math.sin(clock * 2 * math.pi / 1.8)))
            scan_dx += (12 * s - scan_dx) * k
            scan_tilt += (3 * math.pi / 180 * s - scan_tilt) * k
        else:
            scan_dx += (0 - scan_dx) * k
            scan_tilt += (0 - scan_tilt) * k
        if talking:
            push = _push_slip(clock % PUSH_EVERY)
        elif push_t >= 0:
            push = _push_slip(push_t)
            push_t = push_t + dt if push_t + dt < 0.8 else -1
        else:
            push += (0 - push) * k
        if react_t >= 0:
            react_t += dt
            react = math.sin(math.pi * react_t / REACT_SECS) if react_t < REACT_SECS else 0
            if react_t >= REACT_SECS:
                react_t = -1
        if blink_t >= 0:
            blink_t += dt
            bt = blink_t
            blink = bt / 0.06 if bt < 0.06 else (1 - (bt - 0.06) / 0.08 if bt < 0.14 else 0)
            if bt >= 0.14:
                blink_t = -1
        if glance_t >= 0:
            glance_t += dt
            gt = glance_t
            env = gt / 0.15 if gt < 0.15 else (1.0 if gt < 0.75 else max(0.0, 1 - (gt - 0.75) / 0.15))
            glance = 4 * glance_dir * ease_in_out(env)
            if gt >= 0.9:
                glance_t, glance = -1, 0.0
        clock += dt

        ge = ease_in_out(max(0.0, min(1.0, g)))
        hap = max(0.0, min(1.0, max(happy, react)))

        def eye_h(hx):
            h = 44 * hx * (1 + talk) * (1 - hap)
            return h + (8 - h) * max(0.0, min(1.0, blink)) if h > 8 else h

        out.append([
            round(bx(f) + ev("dx", f), 4), round(by(f) + ev("dy", f), 4),
            round(bs(f) * ev("scale", f), 4), round(ev("rot", f), 2),
            round(max(0.0, min(1.0, ev("opacity", f))), 3),
            round(tilt + scan_tilt, 4), round(slip + push - 8 * react - 10 * (1 - ge), 2), round(ge, 3),
            round(dx + glance + scan_dx, 2), round(dy, 2),
            round(eye_h(hl), 2), round(eye_h(hr), 2), round(hap, 3),
        ])
    return out


# ─── drawing (256 grid → polygons) ───────────────────────────────────────────

def _bez(p0, p1, p2, p3, n=14):
    return [((1 - t) ** 3 * p0[0] + 3 * (1 - t) ** 2 * t * p1[0] + 3 * (1 - t) * t ** 2 * p2[0] + t ** 3 * p3[0],
             (1 - t) ** 3 * p0[1] + 3 * (1 - t) ** 2 * t * p1[1] + 3 * (1 - t) * t ** 2 * p2[1] + t ** 3 * p3[1])
            for t in (i / n for i in range(1, n + 1))]


def _arc(cx, cy, r, a0, a1, n=20):
    return [(cx + r * math.cos(a0 + (a1 - a0) * i / n), cy + r * math.sin(a0 + (a1 - a0) * i / n))
            for i in range(n + 1)]


BROW = ([(40, 86)] + _bez((40, 86), (78, 84), (110, 90), (128, 102)) + _bez((128, 102), (146, 90), (178, 84), (216, 86))
        + [(216, 104)] + _bez((216, 104), (178, 102), (148, 108), (128, 120)) + _bez((128, 120), (108, 108), (78, 102), (40, 104)))


def _rim(cx):
    """10-unit stroke of a U hanging from the brow (outer r 42, inner r 32), as one polygon."""
    return ([(cx - 42, 100), (cx - 42, 146)] + _arc(cx, 146, 42, math.pi, 0, 24)
            + [(cx + 42, 108), (cx + 32, 108)] + _arc(cx, 146, 32, 0, math.pi, 24) + [(cx - 32, 100)])


RIM_L = _rim(82)
RIM_R = [(256 - x, y) for x, y in RIM_L]


def _eye(cx, bottom, w, h, r=6):
    if h < 1:
        return None
    x0, x1, k = cx - w / 2, cx + w / 2, w / 2
    if h < k + r:
        rr = h / 2
        top = bottom - h
        return (_arc(x0 + rr, top + rr, rr, math.pi, 1.5 * math.pi, 6) + _arc(x1 - rr, top + rr, rr, 1.5 * math.pi, 2 * math.pi, 6)
                + _arc(x1 - rr, bottom - rr, rr, 0, 0.5 * math.pi, 6) + _arc(x0 + rr, bottom - rr, rr, 0.5 * math.pi, math.pi, 6))
    top = bottom - h
    return (_arc(x0 + r, top + r, r, math.pi, 1.5 * math.pi, 6) + _arc(x1 - r, top + r, r, 1.5 * math.pi, 2 * math.pi, 6)
            + _arc(cx, bottom - k, k, 0, math.pi, 20))


def _happy_arc(cx, cy, rad=18, sw=7):
    """Upturned arc (top half circle), 14-unit stroke with round caps."""
    return (_arc(cx, cy, rad + sw, math.pi, 2 * math.pi, 20) + _arc(cx + rad, cy, sw, 0, math.pi, 8)
            + _arc(cx, cy, rad - sw, 2 * math.pi, math.pi, 20) + _arc(cx - rad, cy, sw, 0, math.pi, 8))


def face_shapes(pose):
    """(polygon in 256-grid, alpha 0..1) list for the white 'ink' of one pose."""
    gRot, gTy, gS, eDx, eDy, hL, hR, happy = pose[5:13]
    shapes = []
    if gS > 0.001:
        c, s = math.cos(gRot), math.sin(gRot)

        def gx(pt):   # translate(128,104) rotate translate(0,gTy) scale translate(-128,-104)
            x, y = (pt[0] - 128) * gS, (pt[1] - 104) * gS + gTy
            return (128 + x * c - y * s, 104 + x * s + y * c)
        for poly in (BROW, RIM_L, RIM_R):
            shapes.append(([gx(p) for p in poly], 1.0))
    for cx0, h in ((82, hL), (174, hR)):
        cx = cx0 + eDx
        e = _eye(cx, 146 + 22 + eDy, 32, h)
        if e:
            shapes.append((e, 1.0))
        if happy > 0.01:
            shapes.append((_happy_arc(cx, 146 + 8 + eDy), happy))
    return shapes


def draw_tile(pose, W, H, variant="primary", ss=3):
    """Render one pose to an RGBA tile. Returns (tile, x, y) where (x, y) is its top-left in the frame."""
    X, Y, D, rot, op = pose[:5]
    d = D * H
    if op <= 0.002 or d < 2:
        return None
    cxp, cyp = X * W, Y * H
    size = int(math.ceil(d)) + 4
    ox, oy = int(math.floor(cxp - size / 2)), int(math.floor(cyp - size / 2))
    fx, fy = cxp - ox, cyp - oy                 # head centre inside the tile (sub-pixel)
    S = size * ss
    k = d / 256 * ss
    c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))

    def px(pt):
        x, y = (pt[0] - 128) * k, (pt[1] - 128) * k
        return (fx * ss + x * c - y * s, fy * ss + x * s + y * c)

    head_rgb, face_rgb = (_rgb(v) for v in VARIANTS[variant])
    mask_head = Image.new("L", (S, S), 0)
    r = 112 * k
    ImageDraw.Draw(mask_head).ellipse([fx * ss - r, fy * ss - r, fx * ss + r, fy * ss + r], fill=255)
    mask_ink = Image.new("L", (S, S), 0)
    di = ImageDraw.Draw(mask_ink)
    for poly, a in face_shapes(pose):
        if a >= 0.999:
            di.polygon([px(p) for p in poly], fill=255)
        else:
            layer = Image.new("L", (S, S), 0)
            ImageDraw.Draw(layer).polygon([px(p) for p in poly], fill=int(255 * a))
            mask_ink = Image.fromarray(np.maximum(np.asarray(mask_ink), np.asarray(layer)))
            di = ImageDraw.Draw(mask_ink)
    tile = Image.new("RGBA", (S, S), head_rgb + (0,))
    tile.putalpha(mask_head)
    ink = Image.new("RGBA", (S, S), face_rgb + (255,))
    ink.putalpha(Image.fromarray((np.asarray(mask_ink, dtype=np.uint16) * np.asarray(mask_head) // 255).astype(np.uint8)))
    tile = Image.alpha_composite(tile, ink).resize((size, size), Image.LANCZOS)
    if op < 0.999:
        a = np.asarray(tile.getchannel("A"), dtype=np.float32) * op
        tile.putalpha(Image.fromarray(a.astype(np.uint8)))
    return tile, ox, oy


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def compose_frame(W, H, characters, f):
    """characters: list of (poses, variant). Returns an RGBA frame with every character at frame f."""
    frame = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for poses, variant in characters:
        if f >= len(poses):
            continue
        t = draw_tile(poses[f], W, H, variant)
        if not t:
            continue
        tile, x, y = t
        # clip the tile to the frame (alpha_composite needs a non-negative destination)
        cx0, cy0 = max(0, -x), max(0, -y)
        cx1, cy1 = min(tile.width, W - x), min(tile.height, H - y)
        if cx1 <= cx0 or cy1 <= cy0:
            continue
        frame.alpha_composite(tile.crop((cx0, cy0, cx1, cy1)), dest=(x + cx0, y + cy0))
    return frame


def render_sequence(characters, W, H, frames, out_dir, step=1):
    """Transparent PNG sequence (frame_00000.png …) with every character, for an upper Resolve
    track. Frames that repeat an earlier pose (holds, empty stretches, stop-motion) are hard links.
    step > 1 holds each pose for `step` frames (stop-motion 'on twos')."""
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    seen = {}
    for f in range(frames):
        src = f // step * step
        key = tuple(tuple(p[src]) if src < len(p) else None for p, _ in characters)
        path = out_dir / f"frame_{f:05d}.png"
        if key in seen:
            try:
                os.link(seen[key], path)
            except OSError:
                shutil.copyfile(seen[key], path)
            continue
        compose_frame(W, H, characters, src).save(path, compress_level=1)
        seen[key] = path
    return {"dir": str(out_dir.resolve()), "pattern": str((out_dir / "frame_%05d.png").resolve()),
            "first": 0, "last": frames - 1, "frames": frames}
