// Live preview of a kinetic motion script. Evaluates the same keyframe tracks the Fusion comps use,
// and draws Mage from the same per-frame poses the Resolve PNG sequences are rendered from (mage.py).

// One scene's elements (text + characters) drawn into a container at a given frame.
export class SceneLayer {
  constructor(parent, p) {
    this.p = p;                 // preview payload: easings, weights, cap_height_em, spec.style
    this.el = document.createElement("div");
    this.el.className = "kp-layer";
    parent.appendChild(this.el);
    this.scene = null;
  }

  set(scene) {
    if (this.scene === scene) return;
    this.scene = scene;
    this.el.innerHTML = "";
    this.nodes = scene.elements.map(item => {
      const el = item.el;
      if (el.kind === "mage") return null;
      const d = document.createElement("div");
      d.className = "kp-el";
      const w = this.p.weights[String(el.weight).toLowerCase().replace(/[\s-]/g, "")] || 600;
      Object.assign(d.style, {
        left: el.x * 100 + "%", top: el.y * 100 + "%",
        fontFamily: `"${el.font}", "SF Pro Display", -apple-system, "Segoe UI", Inter, sans-serif`,
        fontWeight: w, color: `rgb(${el.rgb.map(c => Math.round(c * 255)).join(",")})`,
      });
      if (item.follower) {
        d.innerHTML = [...el.text].map(ch => ch === "\n" ? "<br>" : `<span>${escapeHtml(ch)}</span>`).join("");
      } else d.textContent = el.text;
      this.el.appendChild(d);
      return d;
    });
    this.chars = scene.elements.filter(it => it.el.kind === "mage");
    this.canvas = null;
    if (this.chars.length) {             // characters sit above the text, as on the Resolve track above
      this.canvas = document.createElement("canvas");
      this.canvas.className = "kp-canvas";
      this.el.appendChild(this.canvas);
    }
  }

  draw(f, W, H) {
    const sc = this.scene; if (!sc) return;
    sc.elements.forEach((item, n) => {
      const d = this.nodes[n]; if (!d) return;
      const tr = item.tracks, el = item.el;
      const v = ch => tr[ch] ? evaluate(tr[ch], f, this.p.easings) : DEFAULTS[ch];
      const dx = v("dx") * W, dy = v("dy") * H;
      d.style.fontSize = (el.size * H / this.p.cap_height_em) + "px";
      d.style.opacity = v("opacity");
      d.style.filter = v("blur") > 0.01 ? `blur(${v("blur") * H / 1080 * 0.5}px)` : "none";
      d.style.letterSpacing = (el.tracking + v("tracking")) + "em";
      d.style.transform = `translate(-50%,-50%) translate(${dx}px,${dy}px) scale(${v("scale")}) rotate(${v("rot")}deg)`;
      if (item.follower) {
        let k = 0;
        d.querySelectorAll("span").forEach(s => {
          s.style.opacity = evaluate(item.follower.opacity, f - k * item.follower.delay, this.p.easings); k++;
        });
      }
    });
    if (this.canvas) {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const cw = Math.round(W * dpr), ch = Math.round(H * dpr);
      if (this.canvas.width !== cw || this.canvas.height !== ch) { this.canvas.width = cw; this.canvas.height = ch; }
      const g = this.canvas.getContext("2d");
      g.setTransform(1, 0, 0, 1, 0, 0);
      g.clearRect(0, 0, cw, ch);
      g.scale(dpr, dpr);
      for (const it of this.chars) {
        const pose = it.poses[Math.max(0, Math.min(f, it.poses.length - 1))];
        drawMage(g, pose, W, H, it.el.variant);
      }
    }
  }

  show(on) { this.el.style.display = on ? "" : "none"; }
  remove() { this.el.remove(); }
}

export class KineticPlayer {
  constructor(root) {
    this.root = root;
    root.innerHTML = `
      <div class="kp-stage-wrap"><div class="kp-stage"></div></div>
      <div class="kp-bar">
        <button class="kp-play" title="Play / pause (space)">▶</button>
        <input class="kp-scrub" type="range" min="0" max="1000" value="0">
        <span class="kp-time mono">0.00s</span>
      </div>
      <div class="kp-scenes"></div>`;
    this.stage = root.querySelector(".kp-stage");
    this.playBtn = root.querySelector(".kp-play");
    this.scrub = root.querySelector(".kp-scrub");
    this.timeEl = root.querySelector(".kp-time");
    this.scenesEl = root.querySelector(".kp-scenes");
    this.t = 0; this.playing = false; this.audio = null;
    this.playBtn.onclick = () => this.toggle();
    this.scrub.oninput = () => { this.seek(this.scrub.value / 1000 * this.total); };
    this.onKey = e => { if (e.code === "Space" && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) { e.preventDefault(); this.toggle(); } };
    document.addEventListener("keydown", this.onKey);
    new ResizeObserver(() => this.layout()).observe(root);
  }

  load(payload, musicUrl) {
    const keepT = this.t;
    this.p = payload; this.spec = payload.spec; this.fps = payload.spec.format.fps;
    const st = this.spec.style;
    this.step = st.motion === "stepped" ? st.step_frames : 1;
    let acc = 0;
    this.scenes = payload.scenes.map(sc => { const s = {...sc, start: acc}; acc += sc.frames / this.fps; return s; });
    this.total = acc;
    this.stage.innerHTML = "";
    this.layer = new SceneLayer(this.stage, payload);
    this.cur = null;
    this.scenesEl.innerHTML = this.scenes.map((s, i) =>
      `<button data-i="${i}" style="flex:${s.frames}">${escapeHtml(s.name)}</button>`).join("");
    this.scenesEl.querySelectorAll("button").forEach(b => b.onclick = () => this.seek(this.scenes[+b.dataset.i].start + 0.0001));
    if (musicUrl && (!this.audio || this.audio.dataset.src !== musicUrl)) {
      this.audio?.pause();
      this.audio = new Audio(musicUrl); this.audio.dataset.src = musicUrl; this.audio.preload = "auto";
    }
    this.layout();
    this.seek(Math.min(keepT, this.total - 1e-3));
  }

  layout() {
    if (!this.spec) return;
    const { width: W, height: H } = this.spec.format;
    const wrap = this.root.querySelector(".kp-stage-wrap");
    const maxW = wrap.clientWidth - 28, maxH = Math.min(window.innerHeight * 0.62, 720);
    const k = Math.min(maxW / W, maxH / H);
    this.px = { w: W * k, h: H * k };
    Object.assign(this.stage.style, { width: this.px.w + "px", height: this.px.h + "px" });
    this.renderAt();
  }

  toggle() { this.playing ? this.pause() : this.play(); }
  play() {
    if (this.t >= this.total - 1e-3) this.seek(0);
    this.playing = true; this.playBtn.textContent = "❚❚";
    this.t0 = performance.now() - this.t * 1000;
    if (this.audio) { this.audio.currentTime = this.t; this.audio.play().catch(() => {}); }
    const tick = now => {
      if (!this.playing) return;
      this.t = (now - this.t0) / 1000;
      if (this.t >= this.total) { this.t = this.total - 1e-3; this.renderAt(); this.pause(); return; }
      this.renderAt(); requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }
  pause() { this.playing = false; this.playBtn.textContent = "▶"; this.audio?.pause(); }
  seek(t) {
    this.t = Math.max(0, Math.min(t, this.total));
    if (this.playing) { this.t0 = performance.now() - this.t * 1000; if (this.audio) this.audio.currentTime = this.t; }
    this.renderAt();
  }

  renderAt() {
    if (!this.scenes?.length || !this.px) return;
    let i = this.scenes.findIndex(s => this.t < s.start + s.frames / this.fps);
    if (i < 0) i = this.scenes.length - 1;
    const sc = this.scenes[i];
    let f = Math.floor((this.t - sc.start) * this.fps + 1e-6);
    if (this.step > 1) f = Math.floor(f / this.step) * this.step;
    if (this.cur !== i) {
      this.cur = i;
      this.stage.style.background = sc.background || this.spec.style.background;
      this.layer.set(this.p.scenes[i]);
    }
    this.layer.draw(f, this.px.w, this.px.h);
    this.scrub.value = Math.round(this.t / this.total * 1000);
    this.timeEl.textContent = `${this.t.toFixed(2)}s  ·  ${sc.name}  ·  f${f}`;
    this.scenesEl.querySelectorAll("button").forEach((b, j) => b.classList.toggle("on", j === i));
  }
  destroy() { this.pause(); document.removeEventListener("keydown", this.onKey); }
}

// ─── Mage (mirrors mage.py face_shapes / draw_tile) ────────────────────────────
const MAGE_VARIANTS = { primary: ["#0077B6", "#FFFFFF"], reversed: ["#FFFFFF", "#0077B6"] };
const BROW = new Path2D("M40 86C78 84 110 90 128 102C146 90 178 84 216 86L216 104C178 102 148 108 128 120C108 108 78 102 40 104Z");
const rimPath = cx => {
  const p = new Path2D();
  p.moveTo(cx - 42, 100); p.lineTo(cx - 42, 146); p.arc(cx, 146, 42, Math.PI, 0, true);
  p.lineTo(cx + 42, 108); p.lineTo(cx + 32, 108); p.arc(cx, 146, 32, 0, Math.PI, false); p.lineTo(cx - 32, 100); p.closePath();
  return p;
};
const RIM_L = rimPath(82), RIM_R = rimPath(174);

export function drawMage(g, pose, W, H, variant = "primary") {
  const [X, Y, D, rot, op, gRot, gTy, gS, eDx, eDy, hL, hR, happy] = pose;
  const d = D * H;
  if (op <= 0.002 || d < 2) return;
  const [head, face] = MAGE_VARIANTS[variant] || MAGE_VARIANTS.primary;
  g.save();
  g.globalAlpha = op;
  g.translate(X * W, Y * H);
  g.rotate(rot * Math.PI / 180);
  g.scale(d / 256, d / 256);
  g.translate(-128, -128);
  g.fillStyle = head;
  g.beginPath(); g.arc(128, 128, 112, 0, Math.PI * 2); g.fill();
  g.save();
  g.beginPath(); g.arc(128, 128, 112, 0, Math.PI * 2); g.clip();     // ink stays on the head
  g.fillStyle = face;
  if (gS > 0.001) {
    g.save();
    g.translate(128, 104); g.rotate(gRot); g.translate(0, gTy); g.scale(gS, gS); g.translate(-128, -104);
    g.fill(BROW); g.fill(RIM_L); g.fill(RIM_R);
    g.restore();
  }
  [[82, hL], [174, hR]].forEach(([cx0, h]) => {
    const cx = cx0 + eDx;
    eye(g, cx, 146 + 22 + eDy, 32, h, 6);
    if (happy > 0.01) {
      g.save();
      g.globalAlpha = op * happy;
      g.strokeStyle = face; g.lineWidth = 14; g.lineCap = "round";
      g.beginPath(); g.arc(cx, 146 + 8 + eDy, 18, Math.PI, 2 * Math.PI); g.stroke();
      g.restore();
    }
  });
  g.restore();
  g.restore();
}

function eye(g, cx, bottom, w, h, r) {
  if (h < 1) return;
  const x0 = cx - w / 2, x1 = cx + w / 2, k = w / 2, top = bottom - h;
  g.beginPath();
  if (h < k + r) {
    const rr = h / 2;
    g.roundRect(x0, top, w, h, rr);
  } else {
    g.moveTo(x0, top + r);
    g.arc(x0 + r, top + r, r, Math.PI, 1.5 * Math.PI);
    g.arc(x1 - r, top + r, r, 1.5 * Math.PI, 2 * Math.PI);
    g.lineTo(x1, bottom - k);
    g.arc(cx, bottom - k, k, 0, Math.PI);
    g.closePath();
  }
  g.fill();
}

const DEFAULTS = { opacity: 1, blur: 0, scale: 1, dx: 0, dy: 0, tracking: 0, rot: 0 };

function bezierY(x, [x1, y1, x2, y2]) {
  let lo = 0, hi = 1, t = 0.5;
  for (let i = 0; i < 40; i++) {
    t = (lo + hi) / 2;
    const bx = 3 * (1 - t) ** 2 * t * x1 + 3 * (1 - t) * t ** 2 * x2 + t ** 3;
    if (bx < x) lo = t; else hi = t;
  }
  t = (lo + hi) / 2;
  return 3 * (1 - t) ** 2 * t * y1 + 3 * (1 - t) * t ** 2 * y2 + t ** 3;
}

export function evaluate(keys, f, easings) {
  if (f <= keys[0].f) return keys[0].v;
  for (let i = 0; i < keys.length - 1; i++) {
    const a = keys[i], b = keys[i + 1];
    if (f <= b.f) {
      const p = (f - a.f) / Math.max(b.f - a.f, 1e-9);
      return a.v + (b.v - a.v) * (a.ease === "linear" ? p : bezierY(p, easings[a.ease]));
    }
  }
  return keys[keys.length - 1].v;
}

export function escapeHtml(s) { return String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
