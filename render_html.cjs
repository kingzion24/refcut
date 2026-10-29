// Render a self-animating HTML page to a video clip, frame by frame, for the split explainer
// (explainer.py). Run with NODE_PATH pointing at motion-runtime/node_modules (Playwright).
//
//   node render_html.cjs <page.html> <out.mov> <width> <panelHeight> <frameHeight> <fps> <frames>
//   node render_html.cjs <page.html> <still.png> <width> <panelHeight> <frameHeight> <fps> 1 <seconds>
//
// The second form saves one frame of the panel at <seconds> (a check still, no ffmpeg). With <frames> > 1,
// <seconds> is the page time of the first frame: negative when the clip starts before the page's own 0.
//
// The page is shown at width x panelHeight. For every frame the renderer sets the time to t (seconds):
//   1. window.refcut.seek(t)  if the page defines it (the contract explainer.py asks Claude for), else
//   2. every CSS / Web Animation is paused at t, and SVG SMIL is set to t.
// Frames go to ffmpeg as PNGs; the panel is padded to width x frameHeight with a transparent
// bottom, so the clip drops onto the timeline as-is (ProRes 4444 with alpha).
const { chromium } = require("playwright");
const { spawn } = require("node:child_process");
const { pathToFileURL } = require("node:url");
const path = require("node:path");

async function main() {
  const [html, out, w, ph, fh, fps, frames, at] = process.argv.slice(2);
  const W = +w, PH = +ph, FH = +fh, FPS = +fps, N = +frames, T0 = +(at || 0);
  if (!html || !out || !(W > 0 && PH > 0 && FH >= PH && FPS > 0 && N > 0 && Number.isFinite(T0))) {
    console.error("usage: node render_html.cjs <page.html> <out.mov|still.png> <width> <panelHeight> <frameHeight> <fps> <frames> [seconds]");
    process.exit(2);
  }
  const still = out.toLowerCase().endsWith(".png");
  const browser = await chromium.launch({ args: ["--ignore-gpu-blocklist", "--enable-webgl", "--hide-scrollbars"] });
  const ff = still ? null : spawn(process.env.FFMPEG || "ffmpeg", [
    "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", String(FPS), "-i", "-",
    "-vf", `format=rgba,pad=${W}:${FH}:0:0:color=black@0`,
    "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", "-r", String(FPS), out,
  ], { stdio: ["pipe", "inherit", "inherit"] });
  const ffDone = ff ? new Promise(res => ff.on("close", res)) : Promise.resolve(0);
  try {
    const page = await browser.newPage({ viewport: { width: W, height: PH }, deviceScaleFactor: 1 });
    const errors = [];
    page.on("pageerror", e => errors.push(String(e)));
    await page.goto(pathToFileURL(path.resolve(html)).href, { waitUntil: "load" });
    await page.evaluate(() => document.fonts && document.fonts.ready);
    const mode = await page.evaluate(() => (window.refcut && typeof window.refcut.seek === "function") ? "seek"
      : (document.getAnimations().length || document.querySelector("svg animate, svg animateTransform, svg animateMotion")) ? "animations" : "static");
    console.log(`mode=${mode}`);
    for (let i = 0; i < N; i++) {
      const t = T0 + i / FPS;
      await page.evaluate(async t => {
        if (window.refcut && typeof window.refcut.seek === "function") {
          await window.refcut.seek(t);
          return;
        }
        for (const a of document.getAnimations()) { a.pause(); a.currentTime = Math.max(0, t) * 1000; }
        for (const s of document.querySelectorAll("svg")) {
          if (s.pauseAnimations) { s.pauseAnimations(); s.setCurrentTime(Math.max(0, t)); }
        }
      }, t);
      const clip = { x: 0, y: 0, width: W, height: PH };
      if (still) {
        await page.screenshot({ type: "png", clip, path: out });
        break;
      }
      const png = await page.screenshot({ type: "png", clip });
      if (!ff.stdin.write(png)) await new Promise(r => ff.stdin.once("drain", r));
    }
    if (errors.length) console.log("page errors: " + errors.slice(0, 3).join(" | "));
  } finally {
    ff?.stdin.end();
    await browser.close();
  }
  const code = await ffDone;
  if (code) { console.error(`ffmpeg exited with code ${code}`); process.exit(1); }
  console.log(still ? `still at ${T0}s -> ${out}` : `rendered ${N} frames -> ${out}`);
}

main().catch(e => { console.error(e && e.stack || String(e)); process.exit(1); });
