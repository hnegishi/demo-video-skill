#!/usr/bin/env node
// Record a browser demo with Playwright and write an MP4.
//
// Usage:
//   node record_web.mjs <scenario.mjs | page.html> --out demo.mp4 [--width 1280] [--height 720]
//                       [--duration 60] [--keep-webm] [--style style.json] [--seed N]
//
// scenario.mjs must `export default async function ({ page, demo }) { ... }`.
// page.html (motion mode) is opened and recorded until window.__demoDone === true
// or --duration seconds elapse. If the page defines window.__demoAnnotations (same item
// format as annotate.py, times relative to page load), those are burned in too.
//
// What this script handles so scenarios don't have to:
//  - keeps frames flowing on static screens (Playwright only emits frames on repaint)
//  - draws a visible cursor in the page (headless recordings have no cursor)
//  - trims the blank lead-in before the scenario calls demo.start()
//  - converts the WebM to H.264 MP4 at constant frame rate
//  - captions / titles / highlight boxes / callouts / click effects / zooms (demo.caption(), title(),
//    box(), callout(), click(), zoomTo()) are NOT drawn in the page: their timings and element
//    rects are saved to <out>.annotations.json and rendered afterwards by annotate.py (Pillow +
//    an OS-bundled Japanese font, automatic placement that avoids covering content)

import { createRequire } from 'node:module';
import { execFile, execFileSync } from 'node:child_process';
import { promisify } from 'node:util';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const CACHE = path.join(os.homedir(), '.cache', 'demo-video-skill');

async function loadPlaywright() {
  try {
    return await import('playwright');
  } catch {}
  const req = createRequire(path.join(CACHE, 'package.json'));
  try {
    return req('playwright');
  } catch {
    console.error(`[record_web] installing playwright into ${CACHE} (one-time)`);
    fs.mkdirSync(CACHE, { recursive: true });
    if (!fs.existsSync(path.join(CACHE, 'package.json'))) {
      fs.writeFileSync(path.join(CACHE, 'package.json'), '{"name":"demo-video-cache","private":true}');
    }
    execFileSync('npm', ['install', '--silent', 'playwright@1'], { cwd: CACHE, stdio: 'inherit' });
    return req('playwright');
  }
}

function parseArgs(argv) {
  const args = { width: 1280, height: 720, duration: 60, keepWebm: false };
  const rest = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--out') args.out = argv[++i];
    else if (a === '--width') args.width = Number(argv[++i]);
    else if (a === '--height') args.height = Number(argv[++i]);
    else if (a === '--duration') args.duration = Number(argv[++i]);
    else if (a === '--keep-webm') args.keepWebm = true;
    else if (a === '--style') args.style = argv[++i];
    else if (a === '--seed') args.seed = argv[++i];
    else rest.push(a);
  }
  args.input = rest[0];
  if (!args.input || !args.out) {
    console.error('usage: record_web.mjs <scenario.mjs|page.html> --out demo.mp4 [--width W --height H --duration S]');
    process.exit(2);
  }
  return args;
}

// Injected into every page: keepalive animation, fake cursor, caption layer.
const OVERLAY = () => {
  const install = () => {
    if (document.getElementById('__demo_overlay')) return;
    const style = document.createElement('style');
    style.textContent = `
      @keyframes __demo_tick { to { opacity: .99 } }
      #__demo_tick { position: fixed; left: 0; top: 0; width: 1px; height: 1px; opacity: .01;
        animation: __demo_tick .1s infinite alternate; pointer-events: none; z-index: 2147483647; }
      #__demo_sync { position: fixed; left: 0; top: 0; width: 4px; height: 4px; background: transparent;
        pointer-events: none; z-index: 2147483647; }
      #__demo_cursor { position: fixed; left: 0; top: 0; width: 22px; height: 22px; z-index: 2147483647;
        pointer-events: none; transform: translate(-100px, -100px); transition: none; }
`;
    const root = document.createElement('div');
    root.id = '__demo_overlay';
    root.innerHTML = `<div id="__demo_tick"></div><div id="__demo_sync"></div>
      <svg id="__demo_cursor" viewBox="0 0 24 24"><path d="M3 2l7 19 2.5-7.5L20 11z"
        fill="#111" stroke="#fff" stroke-width="1.5" stroke-linejoin="round"/></svg>`;
    document.documentElement.append(style, root);
    const cursor = root.querySelector('#__demo_cursor');
    document.addEventListener('mousemove', (e) => {
      cursor.style.transform = `translate(${e.clientX - 3}px, ${e.clientY - 2}px)`;
    }, true);
    // Sync marker: a 4px magenta square in the corner on every mousedown. Its frames in the video
    // tell exactly when each click became visible, so annotation times can be lined up per run
    // (wall-clock -> video offset varies by up to ~0.2s between runs). It is painted over when the
    // video is converted, so it never shows in the output.
    const sync = root.querySelector('#__demo_sync');
    document.addEventListener('mousedown', () => {
      sync.style.background = '#ff00ff';
      setTimeout(() => { sync.style.background = 'transparent'; }, 150);
    }, true);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install);
  else install();
};

function makeDemo(page, state) {
  let mouse = { x: state.width / 2, y: state.height / 2 };
  const moveTo = async (x, y, ms = 600) => {
    const steps = Math.max(8, Math.round(ms / 16));
    await page.mouse.move(x, y, { steps });
    mouse = { x, y };
  };
  const center = async (target) => {
    const loc = typeof target === 'string' ? page.locator(target).first() : target;
    await loc.scrollIntoViewIfNeeded();
    const box = await loc.boundingBox();
    if (!box) throw new Error(`element not visible: ${target}`);
    return { loc, box, x: box.x + box.width / 2, y: box.y + box.height / 2 };
  };
  const waitVoice = async () => {
    await state.voicePending;   // the line before may still be synthesizing
    const left = state.voiceUntil - Date.now();
    if (left > 0) await page.waitForTimeout(left);
  };
  const speak = async (text, { wait }) => {
    if (!state.audio?.enabled) return;
    fs.mkdirSync(state.voiceDir, { recursive: true });
    const file = path.join(state.voiceDir, `${String(state.voiceN++).padStart(2, '0')}.wav`);
    const t = now();
    const startedAt = Date.now();
    state.annotations.push({ type: 'narration', start: t, text, file });
    // synthesize in the background so the scenario (and the caption on screen) doesn't stall for it;
    // the next caption / say() waits until this line has been spoken
    state.voicePending = promisify(execFile)('python3',
      [AUDIO_PY, '--tts', text, file, '--audio', JSON.stringify(state.audio)], { encoding: 'utf8' })
      .then(({ stdout }) => { state.voiceUntil = startedAt + (JSON.parse(stdout).duration || 0) * 1000 + 150; })
      .catch((e) => console.error(`[record_web] warning: narration "${text.slice(0, 16)}" failed: ${e.message}`));
    if (wait) await waitVoice();
  };
  // bounding box of one target or an array of targets (e.g. only the rows currently shown)
  const unionBox = async (target) => {
    const boxes = [];
    for (const tg of Array.isArray(target) ? target : [target]) boxes.push((await center(tg)).box);
    const x0 = Math.min(...boxes.map((b) => b.x)), y0 = Math.min(...boxes.map((b) => b.y));
    const x1 = Math.max(...boxes.map((b) => b.x + b.width)), y1 = Math.max(...boxes.map((b) => b.y + b.height));
    return { x: x0, y: y0, width: x1 - x0, height: y1 - y0 };
  };
  // seconds on the output video's timeline (0 = demo.start())
  const now = () => (Date.now() - (state.startAt ?? Date.now())) / 1000;
  const closeCaption = (t) => {
    for (const a of state.annotations) if (a.type === 'caption' && a.end === null) a.end = t;
  };
  const closeZoom = (t) => {
    for (const a of state.annotations) if (a.type === 'zoom' && a.end === null) a.end = t;
  };
  const rect = (b) => ({ x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width), h: Math.round(b.height) });
  return {
    // Call once the first meaningful screen is ready; everything before is trimmed.
    start() { state.startAt ??= Date.now(); },
    pause: (ms = 800) => page.waitForTimeout(ms),
    moveTo,
    async click(target, { pauseAfter = 500, effect = true } = {}) {
      const { x, y } = await center(target);
      await moveTo(x, y);
      await page.waitForTimeout(150);
      const t = now();
      state.mousedowns.push(t);
      if (effect) state.annotations.push({ type: 'click', start: t, x: Math.round(x), y: Math.round(y) });
      await page.mouse.click(x, y);
      await page.waitForTimeout(pauseAfter);
    },
    async hover(target, { pauseAfter = 600 } = {}) {
      const { x, y } = await center(target);
      await moveTo(x, y);
      await page.waitForTimeout(pauseAfter);
    },
    // Types character by character so viewers can read it.
    async type(target, text, { delay = 70, pauseAfter = 400 } = {}) {
      const { loc, x, y } = await center(target);
      await moveTo(x, y);
      const t = now();
      state.mousedowns.push(t);
      state.annotations.push({ type: 'click', start: t, x: Math.round(x), y: Math.round(y) });
      await page.mouse.click(x, y);
      await loc.pressSequentially(text, { delay });
      await page.waitForTimeout(pauseAfter);
    },
    // Caption stays until the next caption(), hideCaption(), or the end of the video.
    // opts.avoid: element(s) this caption talks about; vertical text placed on the picture keeps off them
    async caption(text, ms = 2000, opts = {}) {
      // an array = several separate things to keep clear (not one box spanning all of them)
      if (opts.avoid) {
        const list = Array.isArray(opts.avoid) ? opts.avoid : [opts.avoid];
        const avoid = [];
        for (const tg of list) avoid.push(rect(await unionBox(tg)));
        opts = { ...opts, avoid };
      }
      // when captions are read aloud, don't start a new one over the previous line
      if (text && state.narrateCaptions) await waitVoice();
      const t = now();
      closeCaption(t);
      if (text) state.annotations.push({ type: 'caption', start: t, end: null, text, ...opts });
      if (text && state.narrateCaptions) await speak(text, { wait: false });
      if (ms) await page.waitForTimeout(ms);
    },
    // Narration (text-to-speech). Synthesized now so the scenario can wait for it to finish;
    // mixed into the video afterwards by audio.py. Needs "audio.enabled" in the style.
    async say(text, { wait = true } = {}) {
      await waitVoice();
      await speak(text, { wait });
    },
    // when captions are read aloud, the caption stays until its line has been spoken
    async hideCaption() {
      if (state.narrateCaptions) await waitVoice();
      closeCaption(now());
    },
    // Red outline around an element (coordinates captured now; don't scroll while it's shown).
    async box(target, { label, ms = 1500, pad } = {}) {
      const box = await unionBox(target);
      const t = now();
      state.annotations.push({ type: 'box', start: t, end: t + ms / 1000, label, pad,
        x: Math.round(box.x), y: Math.round(box.y), w: Math.round(box.width), h: Math.round(box.height) });
      await page.waitForTimeout(ms);
    },
    // Speech-bubble note about an element; annotate.py places it beside the element, never on it.
    async callout(target, text, { ms = 2000 } = {}) {
      const box = await unionBox(target);
      const t = now();
      state.annotations.push({ type: 'callout', start: t, end: t + ms / 1000, text, ...rect(box) });
      await page.waitForTimeout(ms);
    },
    // Smoothly zoom the camera onto an element (or several: pass an array) and keep it there until
    // zoomOut() or the next zoomTo(). Waits for the zoom-in to finish. Keep the cursor's moves
    // inside the zoomed area, or zoom out first.
    // ease defaults to the style's zoom.ease (0.6s normally, 0.3s in the dopagaki preset)
    async zoomTo(target, { pad = 24, ease = state.zoomEase, maxScale = 2.5 } = {}) {
      const box = await unionBox(target);
      const t = now();
      closeZoom(t);
      state.annotations.push({ type: 'zoom', start: t, end: null, ease, pad, max_scale: maxScale, ...rect(box) });
      await page.waitForTimeout(ease * 1000);
    },
    async zoomOut({ ease = state.zoomEase } = {}) {
      closeZoom(now());
      await page.waitForTimeout(ease * 1000);
    },
    // Section label ("① ホーム") at the top-left of the picture, until the next chapter.
    // opts: its own look and place (shape, color, fill, rim, size, tilt, position, align, entrance, idle)
    async chapter(text, opts = {}) {
      state.annotations.push({ type: 'chapter', start: now(), text, ...opts });
    },
    // End card: dims the frame and shows a closing line (+ an optional sub line) to the end. Optional.
    async outro(text, { sub, ms = 1800, ...opts } = {}) {
      closeCaption(now());
      state.annotations.push({ type: 'outro', start: now(), text, ...(sub ? { sub } : {}), ...opts });
      if (ms) await page.waitForTimeout(ms);
    },
    // Short pop-up text for a key moment; look / place / "duration" (s on screen, default 1.2) from opts.
    // Placed where it covers no other text and nothing the video points at. Doesn't wait unless ms is given.
    async stamp(text, { ms = 0, angle, ...opts } = {}) {
      const t = now();
      state.annotations.push({ type: 'stamp', start: t, text, ...(angle != null ? { angle } : {}), ...opts });
      if (ms) await page.waitForTimeout(ms);
    },
    // Brief white flash over the whole frame (scene changes, the opening hook). annotate.py keeps
    // flashes at least 0.5s apart for photosensitive viewers.
    async flash({ opacity, ms } = {}) {
      state.annotations.push({ type: 'flash', start: now(), ...(opacity ? { opacity } : {}),
        ...(ms ? { duration: ms / 1000 } : {}) });
    },
    // Brief camera shake (e.g. on a result that should land hard). Rendered by annotate.py.
    async shake({ amplitude = 8, ms = 300 } = {}) {
      const t = now();
      state.annotations.push({ type: 'shake', start: t, end: t + ms / 1000, amplitude });
    },
    // Full-screen title card over a dimmed frame.
    // ms = 0: don't wait, keep it up (in the vertical layout it stays as a header until the next title)
    // badge: small pill shown with the title intro (e.g. '速報')
    async title(text, { sub, ms = 2500, badge, ...opts } = {}) {
      const t = now();
      // ms = 0 keeps it up only in the vertical layout (as the header); landscape: nothing to show
      const keep = styleOverrides?.layout === 'vertical' ? null : t;
      state.annotations.push({ type: 'title', start: t, end: ms ? t + ms / 1000 : keep, text, sub,
        ...(badge ? { badge } : {}), ...opts });
      if (ms) await page.waitForTimeout(ms);
    },
    get mouse() { return mouse; },
  };
}

async function launch(pw) {
  // Prefer system Chrome (no 150MB download); fall back to Playwright's Chromium.
  try {
    return await pw.chromium.launch({ channel: 'chrome' });
  } catch (e) {
    try {
      return await pw.chromium.launch();
    } catch {
      console.error('[record_web] no browser found. Install Google Chrome or run: npx playwright install chromium');
      throw e;
    }
  }
}

const args = parseArgs(process.argv.slice(2));
const pw = await loadPlaywright();
const browser = await launch(pw);
const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'demo-video-'));
const context = await browser.newContext({
  viewport: { width: args.width, height: args.height },
  deviceScaleFactor: 1,
  recordVideo: { dir: tmpDir, size: { width: args.width, height: args.height } },
});
await context.addInitScript(OVERLAY);
const recordStart = Date.now();
const page = await context.newPage();
// Sync flash: paint the whole screen magenta once, in the lead-in that gets trimmed away. Its frame
// in the video ties wall-clock time to video time exactly; estimating from recordStart is ~0.3s off
// (the video starts some time after the page is created), which made clicks and captions late.
await page.setContent('<body style="margin:0;background:#ff00ff"></body>');
await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
const flashAt = Date.now();
await page.waitForTimeout(300);
await page.setContent('<body style="margin:0;background:#fff"></body>');
// Styles with "themes"/"vary" (e.g. dopagaki) are resolved here, once, so recording (zoom speed,
// voice) and burning use the same draw; the resolved style (theme + seed) goes into annotations.json.
const styleOverrides = args.style ? JSON.parse(execFileSync('python3', [
  path.join(path.dirname(fileURLToPath(import.meta.url)), 'annotate.py'), '--resolve-style', args.style,
  ...(args.seed ? [String(args.seed)] : [])], { encoding: 'utf8' })) : undefined;
if (styleOverrides?.theme) console.log(`[record_web] theme: ${styleOverrides.theme}, composition: ${styleOverrides.composition ?? 'classic'} (seed ${styleOverrides.seed})`);
const AUDIO_PY = path.join(path.dirname(fileURLToPath(import.meta.url)), 'audio.py');
const audioStyle = styleOverrides?.audio;
const state = { width: args.width, height: args.height, startAt: undefined, annotations: [], mousedowns: [],
  zoomEase: Number(styleOverrides?.zoom?.ease ?? 0.6),
  audio: audioStyle, narrateCaptions: !!(audioStyle?.enabled && audioStyle?.narrate_captions),
  voiceDir: args.out.replace(/\.\w+$/, '') + '.narration', voiceN: 0, voiceUntil: 0 };
const demo = makeDemo(page, state);

let failed = null;
try {
  const input = path.resolve(args.input);
  if (input.endsWith('.html')) {
    // Mark start before goto resolves: the page's timeline begins on load, so trimming later would cut it.
    demo.start();
    await page.goto(pathToFileURL(input).href);
    await page
      .waitForFunction(() => window.__demoDone === true, null, { timeout: args.duration * 1000, polling: 100 })
      .catch(() => {});
    // window.__demoAnnotations times are relative to page load; shift onto the video timeline
    const pageAnn = await page.evaluate(() => window.__demoAnnotations || []).catch(() => []);
    const origin = await page.evaluate(() => performance.timeOrigin).catch(() => state.startAt);
    const offset = (origin - state.startAt) / 1000;
    for (const a of pageAnn) {
      state.annotations.push({ ...a, start: (a.start ?? 0) + offset, end: a.end == null ? null : a.end + offset });
    }
    await page.waitForTimeout(700); // hold the last frame briefly
  } else {
    const mod = await import(pathToFileURL(input).href);
    await mod.default({ page, demo, context, browser });
  }
  await state.voicePending;   // last narration line written before the recording ends
  await page.waitForTimeout(300);
} catch (e) {
  failed = e;
  console.error('[record_web] scenario failed:', e.message);
  await page.screenshot({ path: args.out.replace(/\.\w+$/, '') + '.error.png' }).catch(() => {});
}

const video = page.video();
await context.close();
await browser.close();
const webm = await video.path();

// first frame where the center pixel is magenta = flashAt on the video timeline
function findFlash(file) {
  const raw = execFileSync('ffmpeg', ['-v', 'error', '-i', file, '-t', '10', '-vf', 'fps=60,scale=8:8',
    '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], { maxBuffer: 64 * 1024 * 1024 });
  const fb = 8 * 8 * 3, c = (4 * 8 + 4) * 3;
  for (let i = 0; i + fb <= raw.length; i += fb) {
    const [r, g, b] = [raw[i + c], raw[i + c + 1], raw[i + c + 2]];
    if (r > 200 && g < 80 && b > 200) return i / fb / 60;
  }
  return null;
}
const flashVideoT = findFlash(webm);
const anchorVideoT = flashVideoT ?? (flashAt - recordStart) / 1000;
const flashTrim = state.startAt ? anchorVideoT + (state.startAt - flashAt) / 1000 : 0;

// Onsets of the corner sync marker (one per mousedown), sampled at 100fps.
function findMarkers(file) {
  const raw = execFileSync('ffmpeg', ['-v', 'error', '-i', file, '-vf', 'fps=100,crop=2:2:1:1',
    '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], { maxBuffer: 256 * 1024 * 1024 });
  const fb = 2 * 2 * 3, out = [];
  let on = false;
  for (let i = 0; i + fb <= raw.length; i += fb) {
    const hit = raw[i] > 200 && raw[i + 1] < 80 && raw[i + 2] > 200;
    if (hit && !on) out.push(i / fb / 100);
    on = hit;
  }
  return out;
}
// Per-run calibration: trim so that each recorded mousedown lands on its marker. Falls back to the
// startup flash when there were no clicks (e.g. motion mode).
let trim = flashTrim;
const markers = state.startAt ? findMarkers(webm) : [];
const fits = state.mousedowns.filter((a) => a >= 0).map((a) => {
  const near = markers.reduce((b, m) => (Math.abs(m - (a + flashTrim)) < Math.abs(b - (a + flashTrim)) ? m : b), Infinity);
  return Math.abs(near - (a + flashTrim)) < 0.6 ? near - a : null;
}).filter((v) => v !== null).sort((x, y) => x - y);
if (fits.length) trim = fits[Math.floor(fits.length / 2)];
else if (flashVideoT === null && state.startAt) {
  console.error('[record_web] warning: no sync marker found; annotation timing may be ~0.3s late');
}
trim = Math.max(0, trim);
fs.mkdirSync(path.dirname(path.resolve(args.out)), { recursive: true });
// keep instant / open-ended items (clicks, narration, unclosed captions); drop zero-length ones
const annotations = state.annotations.filter((a) => a.end == null || a.end > a.start);
const base = args.out.replace(/\.\w+$/, '');
const plain = annotations.length ? `${base}.plain.mp4` : args.out;
execFileSync('ffmpeg', [
  '-v', 'error', '-y', '-ss', trim.toFixed(2), '-i', webm,
  // paint over the 4px sync marker with the pixels right next to it
  '-vf', 'split[a][b];[b]crop=4:4:4:0[p];[a][p]overlay=0:0,fps=30,format=yuv420p', '-c:v', 'libx264', '-crf', annotations.length ? '14' : '20', '-preset', 'medium',
  '-movflags', '+faststart', plain,
]);
if (annotations.length) {
  // Kept next to the video so captions can be edited/retimed and re-burned without re-recording:
  //   python3 annotate.py <base>.plain.mp4 <base>.annotations.json <out>
  const annPath = `${base}.annotations.json`;
  const round = (v) => (v === null ? null : Math.round(v * 100) / 100);
  // look & feel overrides (click effect, accent, corner radius...) travel with the annotations
  const style = styleOverrides;
  fs.writeFileSync(annPath, JSON.stringify({
    ...(style ? { style } : {}),
    items: annotations.map((a) => ({ ...a, start: round(Math.max(0, a.start)), end: round(a.end ?? null) })),
  }, null, 2));
  const annotate = path.join(path.dirname(fileURLToPath(import.meta.url)), 'annotate.py');
  if (audioStyle?.enabled) {
    // picture first, then sound effects / music / narration on top
    const silent = `${base}.silent.mp4`;
    execFileSync('python3', [annotate, plain, annPath, silent], { stdio: 'inherit' });
    execFileSync('python3', [AUDIO_PY, silent, annPath, args.out], { stdio: 'inherit' });
  } else {
    execFileSync('python3', [annotate, plain, annPath, args.out], { stdio: 'inherit' });
  }
}
if (args.keepWebm) fs.copyFileSync(webm, args.out.replace(/\.\w+$/, '') + '.webm');
fs.rmSync(tmpDir, { recursive: true, force: true });
console.log(`[record_web] wrote ${args.out} (trimmed ${trim.toFixed(2)}s lead-in; ` +
  `${fits.length ? `synced on ${fits.length} clicks` : 'synced on start flash'})`);
if (failed) process.exit(1);
