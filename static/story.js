// Live preview of a talking-video edit plan (story.py compile_story): your footage cut together
// from 540p proxies, cards, overlays, Mage and captions — the same frames Resolve gets.
import { SceneLayer, escapeHtml } from "/static/kinetic.js";

export class StoryPlayer {
  constructor(root) {
    this.root = root;
    root.innerHTML = `
      <div class="kp-stage-wrap"><div class="kp-stage sp-stage"><div class="sp-videos"></div><div class="sp-card"></div><div class="sp-cut"></div><div class="sp-broll"></div><div class="sp-over"></div></div></div>
      <div class="kp-bar">
        <button class="kp-play" title="Play / pause (space)">▶</button>
        <input class="kp-scrub" type="range" min="0" max="1000" value="0">
        <span class="kp-time mono">0.00s</span>
      </div>
      <div class="kp-scenes sp-items"></div>
      <div class="sp-lanes"></div>`;
    this.stage = root.querySelector(".kp-stage");
    this.vids = root.querySelector(".sp-videos");
    this.cardEl = root.querySelector(".sp-card");
    this.overEl = root.querySelector(".sp-over");
    this.brollEl = root.querySelector(".sp-broll");
    this.cutEl = root.querySelector(".sp-cut");
    this.playBtn = root.querySelector(".kp-play");
    this.scrub = root.querySelector(".kp-scrub");
    this.timeEl = root.querySelector(".kp-time");
    this.itemsEl = root.querySelector(".sp-items");
    this.lanesEl = root.querySelector(".sp-lanes");
    this.f = 0; this.playing = false; this.videos = {};
    this.playBtn.onclick = () => this.toggle();
    this.scrub.oninput = () => this.seek(Math.round(this.scrub.value / 1000 * this.total));
    this.onKey = e => { if (e.code === "Space" && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) { e.preventDefault(); this.toggle(); } };
    document.addEventListener("keydown", this.onKey);
    new ResizeObserver(() => this.layout()).observe(root);
  }

  load(p) {
    const keep = this.f;
    this.p = p; this.spec = p.spec; this.fps = p.fps; this.total = Math.max(1, p.total);
    for (const [sid, s] of Object.entries(p.sources)) {
      if (this.videos[sid]?.dataset.src === s.url) continue;
      this.videos[sid]?.remove();
      const v = document.createElement("video");
      v.src = s.url; v.dataset.src = s.url; v.preload = "auto"; v.playsInline = true; v.className = "sp-video";
      this.vids.appendChild(v); this.videos[sid] = v;
    }
    // cutaways: your own B-roll over the voice (muted), optionally as an inset on a plain canvas
    this.cutEl.innerHTML = "";
    this.cuts = (p.cutaways || []).map(c => {
      const src = p.sources[c.source]; if (!src) return null;
      const box = document.createElement("div"); box.className = "sp-cutbox"; box.style.display = "none";
      const v = document.createElement("video");
      v.src = src.url; v.muted = true; v.preload = "metadata"; v.playsInline = true; v.className = "sp-video";
      if (c.inset) {
        box.style.background = c.inset.canvas;
        v.style.transform = `translateY(${(c.inset.y - 0.5) * 100}%) scale(${c.inset.scale})`;
      }
      box.appendChild(v); this.cutEl.appendChild(box);
      return { box, v, offset: src.offset || 0 };
    });
    if (p.music_url && this.music?.dataset.src !== p.music_url) {
      this.music?.pause();
      this.music = new Audio(p.music_url); this.music.dataset.src = p.music_url; this.music.preload = "auto";
    } else if (!p.music_url) { this.music?.pause(); this.music = null; }
    this.brollEl.innerHTML = "";
    this.brolls = (p.broll || []).map(b => {
      if (!b.url) return null;
      const v = document.createElement("video");
      v.src = b.url; v.muted = true; v.preload = "auto"; v.playsInline = true; v.className = "sp-video sp-bv"; v.style.display = "none";
      this.brollEl.appendChild(v); return v;
    });
    this.cardLayer?.remove(); this.cardLayer = new SceneLayer(this.cardEl, p);
    this.overEl.innerHTML = ""; this.overLayers = p.overlays.map(() => null);
    const pct = f => (f / this.total * 100) + "%";
    this.itemsEl.innerHTML = p.v1.map((x, i) =>
      `<button data-i="${i}" class="${x.kind}" style="flex:${x.frames}" title="${escapeHtml(x.name)}">${escapeHtml(x.name)}</button>`).join("");
    this.itemsEl.querySelectorAll("button").forEach(b => b.onclick = () => this.seek(p.v1[+b.dataset.i].start));
    const lane = (cls, list) => `<div class="sp-lane ${cls}">${list.map(o =>
      `<i style="left:${pct(o.start)};width:${pct(o.frames)}" title="${escapeHtml(o.name)}"></i>`).join("")}</div>`;
    const chars = p.overlays.filter(o => o.scene.elements.some(e => e.el.kind === "mage"))
      .concat(p.v1.filter(x => x.kind === "card" && x.scene.elements.some(e => e.el.kind === "mage")));
    this.lanesEl.innerHTML =
      lane("callouts", p.overlays.filter(o => o.layer === "overlay" && o.scene.elements.some(e => e.el.kind === "text"))) +
      lane("cuts", p.cutaways || []) + lane("broll", p.broll || []) +
      lane("mage", chars) + lane("caps", p.overlays.filter(o => o.layer === "caption")) +
      `<div class="sp-legend">${(p.cutaways || []).length ? `<span class="cuts">your B-roll</span>` : ""}${(p.broll || []).length ? `<span class="broll">motion B-roll</span>` : ""}<span class="callouts">callouts</span><span class="mage">Mage</span><span class="caps">captions</span></div>`;
    this.cur = null;
    this.layout();
    this.seek(Math.min(keep, this.total - 1));
  }

  layout() {
    if (!this.spec) return;
    const { width: W, height: H } = this.spec.format;
    const wrap = this.root.querySelector(".kp-stage-wrap");
    const maxW = wrap.clientWidth - 28, maxH = Math.min(window.innerHeight * 0.66, 760);
    const k = Math.min(maxW / W, maxH / H);
    this.px = { w: W * k, h: H * k };
    Object.assign(this.stage.style, { width: this.px.w + "px", height: this.px.h + "px" });
    this.render(true);
  }

  toggle() { this.playing ? this.pause() : this.play(); }
  play() {
    if (this.f >= this.total - 1) this.seek(0);
    this.playing = true; this.playBtn.textContent = "❚❚";
    this.t0 = performance.now() - this.f / this.fps * 1000;
    const tick = now => {
      if (!this.playing) return;
      const f = Math.floor((now - this.t0) / 1000 * this.fps);
      if (f >= this.total) { this.f = this.total - 1; this.render(true); this.pause(); return; }
      this.f = f; this.render(false); requestAnimationFrame(tick);
    };
    this.render(true);
    requestAnimationFrame(tick);
  }
  pause() {
    this.playing = false; this.playBtn.textContent = "▶";
    Object.values(this.videos).forEach(v => v.pause());
    (this.brolls || []).forEach(v => v?.pause());
    (this.cuts || []).forEach(c => c?.v.pause());
    this.music?.pause();
  }
  seek(f) {
    this.f = Math.max(0, Math.min(Math.round(f), this.total - 1));
    if (this.playing) this.t0 = performance.now() - this.f / this.fps * 1000;
    this.render(true);
  }

  render(hard) {
    if (!this.p || !this.px) return;
    const f = this.f, p = this.p, W = this.px.w, H = this.px.h;
    const i = Math.max(0, p.v1.findIndex(x => f < x.start + x.frames));
    const x = p.v1[i];
    // V1
    if (x.kind === "clip") {
      const v = this.videos[x.source], src = p.sources[x.source];
      Object.entries(this.videos).forEach(([sid, o]) => { if (sid !== x.source) { o.style.display = "none"; o.pause(); } });
      this.cardEl.style.display = "none";
      if (v) {
        v.style.display = "";
        v.style.transform = `scale(${x.zoom})`;
        const want = x.src_in - (src?.offset || 0) + (f - x.start) / this.fps;
        if (hard || this.cur !== i || Math.abs(v.currentTime - want) > 0.25) v.currentTime = Math.max(0, want);
        if (this.playing && v.paused) v.play().catch(() => {});
        if (!this.playing && !v.paused) v.pause();
      }
      this.stage.style.background = "#000";
    } else {
      Object.values(this.videos).forEach(o => { o.style.display = "none"; o.pause(); });
      this.cardEl.style.display = "";
      this.stage.style.background = x.scene.background || this.spec.style.background;
      this.cardLayer.set(x.scene);
      this.cardLayer.draw(f - x.start, W, H);
    }
    (p.cutaways || []).forEach((c, k) => {
      const cut = this.cuts[k]; if (!cut) return;
      const on = f >= c.start && f < c.start + c.frames;
      cut.box.style.display = on ? "" : "none";
      if (!on) { if (!cut.v.paused) cut.v.pause(); return; }
      const want = c.src_in - cut.offset + (f - c.start) / this.fps;
      if (hard || Math.abs(cut.v.currentTime - want) > 0.25) cut.v.currentTime = Math.max(0, want);
      if (this.playing && cut.v.paused) cut.v.play().catch(() => {});
      if (!this.playing && !cut.v.paused) cut.v.pause();
    });
    if (this.music) {
      const want = f / this.fps;
      if (hard || Math.abs(this.music.currentTime - want) > 0.3) this.music.currentTime = want;
      if (this.playing && this.music.paused) this.music.play().catch(() => {});
    }
    // motion B-roll: cutaways cover the footage, panels sit over it
    (p.broll || []).forEach((b, k) => {
      const v = this.brolls[k]; if (!v) return;
      const on = f >= b.start && f < b.start + b.frames;
      v.style.display = on ? "" : "none";
      if (!on) { if (!v.paused) v.pause(); return; }
      const want = (f - b.start) / this.fps;
      if (hard || Math.abs(v.currentTime - want) > 0.25) v.currentTime = want;
      if (this.playing && v.paused) v.play().catch(() => {});
      if (!this.playing && !v.paused) v.pause();
    });
    // overlays + captions + Mage
    p.overlays.forEach((o, k) => {
      const on = f >= o.start && f < o.start + o.frames;
      let L = this.overLayers[k];
      if (on && !L) { L = this.overLayers[k] = new SceneLayer(this.overEl, p); L.set(o.scene); L.el.style.background = o.scene.background || ""; }
      if (!L) return;
      L.show(on);
      if (on) L.draw(f - o.start, W, H);
    });
    this.cur = i;
    this.scrub.value = Math.round(f / this.total * 1000);
    const s = f / this.fps;
    this.timeEl.textContent = `${s.toFixed(2)}s / ${(this.total / this.fps).toFixed(1)}s  ·  ${x.name}`;
    this.itemsEl.querySelectorAll("button").forEach((b, j) => b.classList.toggle("on", j === i));
  }
  destroy() { this.pause(); document.removeEventListener("keydown", this.onKey); Object.values(this.videos).forEach(v => v.remove()); this.music?.pause(); }
}
