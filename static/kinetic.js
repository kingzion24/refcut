// Live preview of a kinetic motion script. Evaluates the same keyframe tracks the Fusion comps use.
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
    const maxW = wrap.clientWidth, maxH = Math.min(window.innerHeight * 0.62, 720);
    const k = Math.min(maxW / W, maxH / H);
    this.px = { w: W * k, h: H * k };
    Object.assign(this.stage.style, { width: this.px.w + "px", height: this.px.h + "px" });
    this.renderScene(true);
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
    if (!this.scenes?.length) return;
    let i = this.scenes.findIndex(s => this.t < s.start + s.frames / this.fps);
    if (i < 0) i = this.scenes.length - 1;
    const sc = this.scenes[i];
    let f = Math.floor((this.t - sc.start) * this.fps + 1e-6);
    if (this.step > 1) f = Math.floor(f / this.step) * this.step;
    this.frame = f;
    if (this.cur !== i) { this.cur = i; this.renderScene(true); } else this.renderScene(false);
    this.scrub.value = Math.round(this.t / this.total * 1000);
    this.timeEl.textContent = `${this.t.toFixed(2)}s  ·  ${sc.name}  ·  f${f}`;
    this.scenesEl.querySelectorAll("button").forEach((b, j) => b.classList.toggle("on", j === i));
  }

  renderScene(rebuild) {
    const sc = this.scenes?.[this.cur ?? 0]; if (!sc) return;
    const st = this.spec.style, H = this.px.h, W = this.px.w;
    if (rebuild) {
      this.stage.style.background = sc.background || st.background;
      this.stage.innerHTML = "";
      this.nodes = sc.elements.map(item => {
        const el = item.el, d = document.createElement("div");
        d.className = "kp-el";
        const w = this.p.weights[String(el.weight).toLowerCase().replace(/[\s-]/g, "")] || 600;
        Object.assign(d.style, {
          left: el.x * 100 + "%", top: el.y * 100 + "%",
          fontFamily: `"${el.font}", "SF Pro Display", -apple-system, "Segoe UI", Inter, sans-serif`,
          fontWeight: w, fontSize: (el.size * H / this.p.cap_height_em) + "px",
          color: `rgb(${el.rgb.map(c => Math.round(c * 255)).join(",")})`,
        });
        if (item.follower) {
          d.innerHTML = [...el.text].map(ch => ch === "\n" ? "<br>" : `<span>${escapeHtml(ch)}</span>`).join("");
        } else d.textContent = el.text;
        this.stage.appendChild(d);
        return d;
      });
    }
    const f = this.frame ?? 0;
    sc.elements.forEach((item, n) => {
      const d = this.nodes[n], tr = item.tracks, el = item.el;
      const v = ch => tr[ch] ? evaluate(tr[ch], f, this.p.easings) : DEFAULTS[ch];
      const dx = v("dx") * W, dy = v("dy") * H;
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
  }
  destroy() { this.pause(); document.removeEventListener("keydown", this.onKey); }
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

function escapeHtml(s) { return String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
