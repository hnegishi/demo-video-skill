#!/usr/bin/env python3
"""Burn annotations, click effects and zooms into a video with Pillow.

Usage:
    python3 annotate.py in.mp4 annotations.json out.mp4
    python3 annotate.py in.mp4 annotations.json --check [preview.png]

    python3 annotate.py --default-style                 # print the default look & feel

--check does everything except encoding (a few seconds): prints placement warnings, caption-band
notes and reading-time hints, and writes a contact sheet showing every annotation at its midpoint
(default <annotations>.check.png). Use it to iterate on wording/timing before the real burn.

annotations.json:
    {
      "items": [
        {"type": "title",   "start": 0,   "end": 2.5, "text": "完了フィルタ", "sub": "v1.4 の新機能"},
        {"type": "caption", "start": 2.5, "end": 5.0, "text": "タスクを追加する"},
        {"type": "click",   "start": 4.1, "x": 640, "y": 360},
        {"type": "zoom",    "start": 5.0, "end": 8.0, "x": 80, "y": 300, "w": 480, "h": 200},
        {"type": "box",     "start": 6,   "end": 8,   "x": 100, "y": 320, "w": 300, "h": 48, "label": "ここ"},
        {"type": "callout", "start": 8,   "end": 10,  "x": 600, "y": 280, "w": 80, "h": 30, "text": "件数も更新"}
      ]
    }

Coordinates are input-video pixels; times are seconds; "end" may be omitted (= end of video).

  caption  text strip. Placed automatically where the picture is emptiest during its whole display
           time (bottom-center preferred, then top-center, then corners). "position": "bottom" |
           "top" | "bottom-left" | ... pins it.
  title    full-screen card over a dimmed frame.
  box      outline around a rect (x, y, w, h); optional "label" goes outside the box on the
           emptiest side.
  callout  speech bubble about a target rect (x, y, w, h) - or a point (x, y) - placed beside the
           target on the emptiest side, never on top of it, with a stem pointing at it.
  click    thin white ring at (x, y) that expands and fades (~0.5s).
  zoom     smoothly zooms into rect (x, y, w, h) from "start", holds, and zooms back out after "end".
           The rect is expanded to the video's aspect ratio. "ease" (default 0.6s) sets the
           transition length. Consecutive zooms pan directly from one rect to the next.

Look & feel (click effect, accent color, corner radius, sizes, plate colors) are defaults only:
override them per video with "style": {...} next to "items" in annotations.json, or for a whole
project/user with a JSON file named by $DEMO_VIDEO_STYLE. Example:
    {"style": {"click": {"style": "disc", "color": [255, 196, 0]}, "corner_radius": 3}, "items": [...]}

Readability rules applied automatically:
  - Colors: each annotation measures the brightness of the picture under it and uses a dark
    plate on bright footage, a light plate on dark footage (so plates never blend into the video).
  - Overlap: captions/labels/bubbles avoid busy regions (text, UI, edges, plus a ~24px margin) of
    the actual frames during their display time, the rects they point at, and each other.
    If a caption has no free spot anywhere (e.g. while zoomed in), the picture is eased down to
    make a band at the bottom for it (an automatic slight zoom-out), so it still covers nothing.
    Labels/bubbles with no free spot print a warning; fix those in the recording.
  - box / callout / click live in video space (they zoom with the content); caption / title are
    screen space (fixed size on screen).

Text uses an OS-bundled Japanese font (jpfont.py). Sizes scale with frame height.
Pipeline: ffmpeg -> raw RGB -> Pillow -> ffmpeg (H.264); audio is copied.
"""
import contextlib
import functools
import json
import math
import re
import os
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageStat

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jpfont import find_emoji_font, load_font  # noqa: E402

# Look & feel. These are only defaults: override any key with a style JSON in
# $DEMO_VIDEO_STYLE (project/personal taste) and/or "style" in annotations.json (one video).
# Sizes are pixels at 720p and scale with the video height.
DEFAULT_STYLE = {
    "theme": None,                    # set by resolve_style() when the style has "themes"
    "seed": None,                     # the seed that picked the theme / variations (reproducible)
    "resolved": False,
    "background_colors": [[24, 10, 48], [6, 18, 40]],   # "gradient" background: top, bottom
    "accent": [255, 72, 72],          # box outline, box label, callout border/stem
    "corner_radius": 6,               # plates, labels, boxes (small: reads as a label, not a pill)
    "fade": 0.2,                      # fade in/out of captions, boxes, callouts (s)
    "caption_size": 30,
    "callout_size": 24,
    "title_size": 60,
    "title_dim": 0.85,                # darkness of the title card backdrop (0..1)
    "plate_dark": {"bg": [18, 22, 34, 232], "fg": [255, 255, 255]},   # used over bright footage
    "plate_light": {"bg": [250, 250, 252, 240], "fg": [20, 22, 30]},  # used over dark footage
    "caption": {
        "style": "plate",             # "plate" (text on a rounded plate) | "outline" (big outlined text, no plate)
                                      # | "tag" (vertical: one solid color strip per line, dark text)
        "align": "center",            # vertical: "center" | "left" | "right" | "stagger" (alternate left/right)
        "tilt": 0,                    # vertical: caption angle in degrees (stagger flips it every caption)
        "weight": "bold",             # "bold" | "heavy" (W8-W9 class fonts)
        "color": [255, 255, 255],     # outline style: text color
        "outline_color": [0, 0, 0],   # outline style: stroke color
        "outline_width": 6,           # outline style: stroke width
        "pop": False,                 # legacy switch for entrance "pop"
        "entrance": "fade",           # "fade" | "pop" (bounce in) | "slide" (from the side) | "type" (letter by letter)
        "idle": "none",               # motion while shown: "none" | "float" | "pulse" | "wiggle"
        "emphasis_color": [255, 196, 0],   # color of **emphasized** words
        "emphasis_style": "color",    # "color" (colored text) | "marker" (white text on a colored band)
        "emoji_sticker": False,       # trailing emoji leave the caption and pop up as a big sticker above it
    },
    "zoom": {
        "ease": 0.6,                  # s for each zoom transition
        "overshoot": 0.0,             # >0: zoom-ins go a little past the target and settle (punchier)
    },
    "shake_on_click": 0,              # px: brief camera shake on every click (0 = off)
    "drift": 0.0,                     # slow "breathing" push-in/out of the camera (0.03 = 3%), keeps the
                                      # picture alive between actions; 0 = off
    "flash": {"color": [255, 255, 255], "opacity": 0.7, "duration": 0.18},   # "flash" items
    "punch": 0.0,                     # jump-cut punch-in: on every new caption the picture snaps to
                                      # 1+punch zoom or back (alternating). Instant, so it reads as a cut
    "progress": {"enabled": False, "color": [255, 70, 150], "track": [0, 0, 0], "height": 8,
                 "position": "top"},   # playback bar: "top" | "bottom" edge
    "stamp_size": 72,                 # "stamp" items: big tilted pop-up text
    "background": "blur",             # vertical: "blur" (blurred copy of the video) | "black" | "gradient"
    "header_two_tone": False,         # vertical header: 1st line white, following lines in caption color
    "title_intro": 0.0,               # vertical: show the first title big over the (dimmed) video for N s,
                                      # then move it up into the header
    "badge_color": [232, 36, 60],     # "速報"-style title badge and chapter pills
    "outro_dim": 0.6,                 # "outro" end card: how dark the video gets behind the text
    "reading": {"base": 1.0, "per_char": 0.1},   # --check hint: seconds a caption needs on screen
    "layout": "landscape",            # "landscape" (as recorded) | "vertical" (short-video canvas)
    "composition": "classic",         # vertical: the picture is always in the middle; this is where text goes
                                      #   "classic": title above the picture, captions below (on the picture
                                      #              only when the bands are full)
                                      #   "overlay": captions on the picture first, keeping off what the
                                      #              video points at (zoom target, box, click, "avoid")
    "chapter_side": "left",           # vertical: chapter pill on the "left" or "right"
    "canvas": [1080, 1920],           # vertical: output size; the recording sits in the middle
    "audio": {                        # used by audio.py
        "enabled": False,
        "sfx": True,                  # tick on clicks, whoosh on zoom-ins, pop on captions, impact on shakes/titles
        "sfx_volume": 0.5,
        "bgm": "synth",               # "synth" (generated loop) | path to an audio file | null
        "bgm_volume": 0.18,           # ducked automatically under narration
        "bgm_bpm": 124,               # synth bgm tempo
        "bgm_key": 0,                 # synth bgm transpose in semitones
        "bgm_energy": "light",        # synth bgm (no genre): "light" | "hype"
        "bgm_genre": None,            # music.py genre: edm | trap | lofi | chiptune | funk | random (null = old loop)
        "bgm_seed": 1,                # music.py seed (tempo inside the genre's range, pattern details)
        "bgm_bpm_fixed": None,        # force a tempo instead of picking one inside the genre's range
        "narration": True,            # speak "narration" items
        "narrate_captions": False,    # no narration items? read the captions aloud instead
        "engine": "say",              # "say" (macOS, default) | "voicevox" (only when asked for) | "auto"
        "voicevox_speaker": "ずんだもん",   # VOICEVOX speaker ("name" or "name:style")
        "voicevox_speed": 1.25,
        "voicevox_intonation": 1.3,
        "voicevox_pitch": 0.0,
        "voice_pitch": 1.0,           # say only: >1 raises the pitch (short-video TTS feel)
        "voice_tempo": 1.0,           # say only: >1 speeds the voice up after pitching
        "credit": True,               # VOICEVOX: show "VOICEVOX:<name>" small at the bottom (required credit)
        "voice": "Kyoko",             # macOS `say` voice (default; use another only when the user asks)
        "rate": 270,                  # words per minute for `say`
        "voice_volume": 1.0,
    },
    "click": {
        "style": "ring",              # "ring" (expanding ring) | "burst" (ring + rays) | "disc" | "none"
        "color": [255, 255, 255],
        "opacity": 0.92,
        "duration": 0.5,              # s
        "radius_from": 6,             # ring: start radius; disc: radius
        "radius_to": 36,              # ring: end radius
        "width": 2,                   # ring stroke
        "shadow": True,               # faint dark edge so a white effect stays visible on white UIs
    },
}
STYLE = json.loads(json.dumps(DEFAULT_STYLE))


def resolve_style(spec, seed=None):
    """Turn a style with "themes" / "vary" into a concrete one, reproducibly.

    "themes": {name: overrides}, picked by "theme" (or at random when it is "random"/missing);
    "vary":   {"dotted.key": {"choice": [...]} | {"range": [lo, hi]} | {"int": [lo, hi]}}.
    The result records "theme", "seed" and "resolved": true, so re-burning it gives the same look."""
    import random
    spec = json.loads(json.dumps(spec or {}))
    if spec.get("resolved") or not (spec.get("themes") or spec.get("vary")):
        return spec
    if seed is None:
        seed = spec.get("seed")
    if seed is None:
        seed = random.SystemRandom().randrange(1, 100000)
    # own stream per purpose: roll_ideas.py uses the same seed, and sharing one stream made the
    # theme and the rolled hook move together
    rng = random.Random(f"{int(seed)}:style")
    themes = spec.pop("themes", {}) or {}
    vary = spec.pop("vary", {}) or {}
    name = spec.get("theme")
    if themes and (not name or name == "random" or name not in themes):
        name = rng.choice(sorted(themes))
    out = json.loads(json.dumps(spec))
    if name in themes:
        def deep(b, o):
            for k, v in o.items():
                if isinstance(v, dict) and isinstance(b.get(k), dict):
                    deep(b[k], v)
                else:
                    b[k] = v
        deep(out, themes[name])
    for key, rule in sorted(vary.items()):
        if "choice" in rule:
            val = rng.choice(rule["choice"])
        elif "int" in rule:
            val = rng.randint(*rule["int"])
        else:
            lo, hi = rule["range"]
            val = round(rng.uniform(lo, hi), 3)
        d = out
        parts = key.split(".")
        for k in parts[:-1]:
            d = d.setdefault(k, {})
        d[parts[-1]] = val
    out.update({"theme": name, "seed": int(seed), "resolved": True})
    return out


def merge_style(base, over):
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            merge_style(base[k], v)
        elif k in base:
            base[k] = v
        else:
            print(f"[annotate] warning: unknown style key '{k}' ignored", file=sys.stderr)
    return base


def load_style(spec_style, use_env=True):
    """defaults <- $DEMO_VIDEO_STYLE file <- annotations.json "style"; refreshes the globals below."""
    global STYLE, ACCENT, FADE, CLICK_DUR, CORNER, DARK_PLATE, LIGHT_PLATE
    STYLE = json.loads(json.dumps(DEFAULT_STYLE))
    env = os.environ.get("DEMO_VIDEO_STYLE") if use_env else None
    if env:
        merge_style(STYLE, json.loads(Path(env).read_text()))
    merge_style(STYLE, resolve_style(spec_style))
    ACCENT = tuple(STYLE["accent"][:3]) + (255,)
    FADE = float(STYLE["fade"])
    CLICK_DUR = float(STYLE["click"]["duration"])
    CORNER = float(STYLE["corner_radius"])
    pd, pl = STYLE["plate_dark"], STYLE["plate_light"]
    rgba = lambda c, a=255: tuple(c[:3]) + ((c[3],) if len(c) > 3 else (a,))
    DARK_PLATE = (rgba(pd["bg"]), rgba(pd["fg"]), (255, 255, 255, 70))
    LIGHT_PLATE = (rgba(pl["bg"]), rgba(pl["fg"]), (0, 0, 0, 60))


ACCENT = FADE = CLICK_DUR = CORNER = DARK_PLATE = LIGHT_PLATE = None
SAMPLE_FPS = 4
SAMPLE_DIV = 4          # layout analysis runs on 1/4-size frames
BUSY_LIMIT = 0.8        # % of edge pixels (incl. 24px margin) above which a spot is "not free"
WARN_LIMIT = 12.0       # above this the annotation really sits on content, not just inside the margin

load_style({}, use_env=False)  # plain defaults until main() reads the env/spec styles


# ---------------------------------------------------------------- video io

def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,avg_frame_rate:format=duration", "-of", "json", path],
        capture_output=True, text=True, check=True).stdout
    j = json.loads(out)
    s = j["streams"][0]
    num, _, den = s["avg_frame_rate"].partition("/")
    fps = float(num) / float(den or 1) if float(num) else 30.0
    return int(s["width"]), int(s["height"]), fps, float(j["format"]["duration"])


def read_frames(path, w, h, fps=None):
    vf = ["-vf", f"fps={fps},scale={w}:{h}"] if fps else []
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", path, *vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         stdout=subprocess.PIPE)
    size = w * h * 3
    while True:
        buf = p.stdout.read(size)
        if len(buf) < size:
            break
        yield buf
    p.wait()


# ---------------------------------------------------------------- geometry

def clamp_rect(r, W, H):
    x, y, w, h = r
    w, h = min(w, W), min(h, H)
    return (min(max(0, x), W - w), min(max(0, y), H - h), w, h)


def intersects(a, b, margin=0):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw + margin and bx < ax + aw + margin and ay < by + bh + margin and by < ay + ah + margin


def overlap_area(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    w = min(ax + aw, bx + bw) - max(ax, bx)
    h = min(ay + ah, by + bh) - max(ay, by)
    return max(0, w) * max(0, h)


def back_out(p, s):
    """Ease-out with overshoot: passes 1 by ~s*10% and settles back."""
    p = min(1.0, max(0.0, p)) - 1
    k = 1.70158 * s
    return 1 + p * p * ((k + 1) * p + k)


def smooth(p):
    p = min(1.0, max(0.0, p))
    return p * p * (3 - 2 * p)


class Zoom:
    """Piecewise camera: rect(t) in source coordinates, eased between keyframes."""

    def __init__(self, items, W, H, base=None, ar=None):
        """base: the rect shown when not zoomed (vertical layout: the content area); ar: output aspect."""
        self.W, self.H = W, H
        self.ar = ar or W / H
        full = tuple(float(v) for v in base) if base else (0.0, 0.0, float(W), float(H))
        self.full = full
        self.overshoot = float(STYLE["zoom"]["overshoot"])
        zs = sorted((i for i in items if i.get("type") == "zoom"), key=lambda i: i["start"])
        keys = [(0.0, full)]
        for n, z in enumerate(zs):
            ease = float(z.get("ease") or STYLE["zoom"]["ease"])
            rect = self.fit(z)
            start = float(z["start"])
            end = float(z["end"]) if z.get("end") is not None else 1e9
            keys.append((start, self.at_keys(keys, start)))
            keys.append((start + ease, rect))
            keys.append((end, rect))
            nxt = zs[n + 1]["start"] if n + 1 < len(zs) else None
            if nxt is None or nxt > end + ease:
                keys.append((end + ease, full))
        self.keys = keys
        self.insets = []      # (start, end, room): shrink the picture upward to free a caption band
        # camera shakes: explicit "shake" items, plus one per click when style shake_on_click > 0
        self.shakes = [(float(i["start"]), float(i.get("end") or float(i["start"]) + 0.3),
                        float(i.get("amplitude", 8))) for i in items if i.get("type") == "shake"]
        amp = float(STYLE["shake_on_click"])
        if amp > 0:
            self.shakes += [(float(i["start"]), float(i["start"]) + 0.25, amp) for i in items if i.get("type") == "click"]
        # jump-cut punch-ins: alternate between wide and punched-in at every new caption
        p = float(STYLE.get("punch", 0) or 0)
        starts = sorted(float(i["start"]) for i in items if i.get("type") == "caption")
        self.punches = [(s0, 1 + p * ((n + 1) % 2)) for n, s0 in enumerate(starts)] if p > 0 else []
        self.active = len(zs) > 0 or bool(self.shakes) or bool(self.punches)

    def add_inset(self, start, end, room, ease=0.35):
        self.insets.append((start, end, room, ease))
        self.active = True

    def inset_at(self, t):
        best = 0.0
        for s0, s1, room, ease in self.insets:
            if s0 - ease <= t <= s1 + ease:
                k = min(1.0, (t - (s0 - ease)) / ease, ((s1 + ease) - t) / ease)
                best = max(best, room * smooth(k))
        return best

    def fit(self, z):
        """Rect to show for a zoom target: target + pad, at the frame's aspect ratio, inside the frame.

        Unless "caption_room" is false, the bottom ~15% of the screen is kept free of the target so a
        caption can sit there without covering what the zoom is showing.
        """
        W, H = self.W, self.H
        pad = float(z.get("pad", 24))
        x, y, w, h = z["x"] - pad, z["y"] - pad, z["w"] + 2 * pad, z["h"] + 2 * pad
        cx, cy = x + w / 2, y + h / 2
        ar = self.ar
        # vertical layout: captions sit outside the picture, so no room needs to be kept for them
        room = (110 * H / 720) / H if z.get("caption_room", True) and not CANVAS else 0.0
        h = max(h / (1 - room), w / ar)
        w = h * ar
        max_scale = float(z.get("max_scale", 3.0))
        w, h = max(w, self.full[2] / max_scale), max(h, self.full[3] / max_scale)
        w, h = min(w, W), min(h, H)
        # put the target's center in the middle of the area above the caption band
        top = cy - h * (1 - room) / 2
        return clamp_rect((cx - w / 2, top, w, h), W, H)

    def at_keys(self, keys, t):
        if t <= keys[0][0]:
            return keys[0][1]
        for (t0, r0), (t1, r1) in zip(keys, keys[1:]):
            if t0 <= t <= t1:
                q = (t - t0) / (t1 - t0) if t1 > t0 else 1.0
                if self.overshoot > 0 and r1 != self.full and r1[2] < r0[2]:
                    p = back_out(q, self.overshoot)    # punchy zoom-in: overshoot, then settle
                else:
                    p = smooth(q)
                return tuple(a + (b - a) * p for a, b in zip(r0, r1))
        return keys[-1][1]

    def rect(self, t):
        """Source rect the camera shows at time t (zoom only; caption bands are applied after)."""
        x, y, w, h = self.at_keys(self.keys, t)
        if self.punches:
            level = 1.0
            for s0, lv in self.punches:
                if t >= s0:
                    level = lv
            if level != 1.0:
                k = 1 / level
                x, y, w, h = x + w * (1 - k) / 2, y + h * (1 - k) / 2, w * k, h * k
        d = float(STYLE.get("drift", 0) or 0)
        if d > 0:
            # slow breathing push-in (6 s period): small enough not to read as camera motion
            k = 1 - d * (0.5 - 0.5 * math.cos(2 * math.pi * t / 6.0))
            x, y, w, h = x + w * (1 - k) / 2, y + h * (1 - k) / 2, w * k, h * k
        return clamp_rect((x, y, w, h), self.W, self.H)

    def shake_at(self, t):
        """(dx, dy) camera offset in px, decaying over each shake."""
        dx = dy = 0.0
        for s0, s1, amp in self.shakes:
            if s0 <= t < s1:
                k = 1 - (t - s0) / (s1 - s0)
                dx += amp * k * math.sin(2 * math.pi * 13 * (t - s0))
                dy += amp * k * 0.6 * math.cos(2 * math.pi * 11 * (t - s0))
        return dx, dy

    def is_full(self, r):
        return all(abs(a - b) < 0.5 for a, b in zip(r, (0, 0, self.W, self.H)))

    def identity(self, t):
        return (not STYLE.get("drift") and self.is_full(self.rect(t)) and (not self.insets or self.inset_at(t) <= 0)
                and self.shake_at(t) == (0.0, 0.0))

    def _band_xform(self, t):
        """(scale, offset_x) of the caption-band shrink: the camera view is drawn into the top
        (1 - room) of the screen, centered, leaving a free band at the bottom."""
        room = self.inset_at(t) if self.insets else 0.0
        s = 1 - room
        return s, self.W * (1 - s) / 2

    def to_screen(self, t, rect):
        zx, zy, zw, zh = self.rect(t)
        s, ox = self._band_xform(t)
        sx, sy = self.W / zw * s, self.H / zh * s
        x, y, w, h = rect
        return ((x - zx) * sx + ox, (y - zy) * sy, w * sx, h * sy)

    def from_screen(self, t, rect):
        zx, zy, zw, zh = self.rect(t)
        s, ox = self._band_xform(t)
        sx, sy = self.W / zw * s, self.H / zh * s
        x, y, w, h = rect
        return ((x - ox) / sx + zx, y / sy + zy, w / sx, h / sy)

    def apply(self, img, t, resample=Image.BICUBIC):
        r = self.rect(t)
        if not self.is_full(r):
            k = img.width / self.W
            size = (img.width, max(1, round(img.width / self.ar)))   # == img.size unless vertical
            img = img.resize(size, resample, box=(r[0] * k, r[1] * k, (r[0] + r[2]) * k, (r[1] + r[3]) * k))
        dx, dy = self.shake_at(t)
        if dx or dy:
            # shake: zoom in a hair so the shifted frame never shows an empty edge
            k = img.width / self.W
            # smallest zoom-in that keeps the shifted crop inside the frame
            m = 1.002 / (1 - 2 * max(abs(dx) / self.W, abs(dy) / self.H))
            hw, hh = img.width / (2 * m), img.height / (2 * m)
            cx = min(max(img.width / 2 + dx * k, hw), img.width - hw)
            cy = min(max(img.height / 2 + dy * k, hh), img.height - hh)
            img = img.resize(img.size, resample, box=(cx - hw, cy - hh, cx + hw, cy + hh))
        s, ox = self._band_xform(t)
        if s >= 0.999:
            return img
        # caption band: shrink the (zoomed) picture into the top of the screen onto a matte
        cw, ch = max(1, round(img.width * s)), max(1, round(img.height * s))
        matte = Image.new(img.mode, img.size, self.matte_color(img))
        matte.paste(img.resize((cw, ch), resample), (round(ox * img.width / self.W), 0))
        return matte

    @staticmethod
    def matte_color(img):
        """Color of the frame's bottom edge, so the band reads as an extension of the picture."""
        strip = img.crop((0, img.height - 2, img.width, img.height)).resize((1, 1), Image.BOX)
        return strip.getpixel((0, 0))


# ---------------------------------------------------------------- analysis

class Scene:
    """Low-res samples of the source (for video-space layout) and of the zoomed view (screen space)."""

    def __init__(self, src, W, H, zoom):
        self.W, self.H = W, H
        self.k = 1 / SAMPLE_DIV
        sw, sh = max(2, W // SAMPLE_DIV // 2 * 2), max(2, H // SAMPLE_DIV // 2 * 2)
        self.src, self.scr = [], []
        for n, buf in enumerate(read_frames(src, sw, sh, SAMPLE_FPS)):
            t = n / SAMPLE_FPS
            im = Image.frombytes("RGB", (sw, sh), buf)
            self.src.append((t, self._prep(im)))
            self.scr.append((t, self._prep(zoom.apply(im, t)) if zoom.active else self.src[-1][1]))

    @staticmethod
    def _prep(im):
        g = im.convert("L")
        # binary edge map: a region's score is the share of pixels on a visible edge (text, borders,
        # buttons). A mean of raw edge strength would let a few UI elements hide in a large empty area.
        # dilated so annotations keep a little breathing room around content, not just avoid its pixels;
        # the low threshold catches faint UI (light-gray buttons, hairline borders)
        edges = g.filter(ImageFilter.FIND_EDGES).point(lambda v: 255 if v > 24 else 0)
        # FIND_EDGES marks the image border itself as an edge; clear it so the frame's own edge
        # doesn't make every bottom/top placement look occupied
        ImageDraw.Draw(edges).rectangle((0, 0, edges.width - 1, edges.height - 1), outline=0, width=2)
        return g, edges.filter(ImageFilter.MaxFilter(13))  # ~24px margin at full size

    def content_bbox(self, pad=16):
        """Union of everything that ever shows edges in the recording, in source px (or None)."""
        acc = None
        for _, (_, e) in self.src:
            acc = e if acc is None else ImageChops.lighter(acc, e)
        if acc is None:
            return None
        # columns / rows with only a few edge pixels (a stray marker, a hairline at the border) don't
        # count as content: keep the span where the edge density is meaningful
        w, h = acc.size
        cols = [sum(1 for yy in range(0, h, 2) if acc.getpixel((xx, yy))) for xx in range(w)]
        rows = [sum(1 for xx in range(0, w, 2) if acc.getpixel((xx, yy))) for yy in range(h)]
        cmin, rmin = max(2, max(cols) * 0.04), max(2, max(rows) * 0.04)
        xs = [i for i, v in enumerate(cols) if v >= cmin]
        ys = [i for i, v in enumerate(rows) if v >= rmin]
        if not xs or not ys:
            return None
        box = (xs[0], ys[0], xs[-1] + 1, ys[-1] + 1)
        k = 1 / self.k
        x0, y0, x1, y1 = box[0] * k - pad, box[1] * k - pad, box[2] * k + pad, box[3] * k + pad
        return (max(0, x0), max(0, y0), min(self.W, x1) - max(0, x0), min(self.H, y1) - max(0, y0))

    def _samples(self, space, t0, t1):
        frames = self.scr if space == "screen" else self.src
        sel = [f for f in frames if t0 - 0.01 <= f[0] <= t1 + 0.01]
        if not sel and frames:
            sel = [min(frames, key=lambda f: abs(f[0] - t0))]
        return sel

    def _crop(self, rect):
        x, y, w, h = rect
        k = self.k
        return (int(max(0, x * k)), int(max(0, y * k)), int(min(self.W * k, (x + w) * k)), int(min(self.H * k, (y + h) * k)))

    def busy(self, space, rect, t0, t1):
        box = self._crop(rect)
        if box[2] - box[0] < 1 or box[3] - box[1] < 1:
            return 0.0
        return max((ImageStat.Stat(e.crop(box)).mean[0] / 2.55 for _, (_, e) in self._samples(space, t0, t1)),
                   default=0.0)

    def line_busy(self, space, p0, p1, t0, t1, steps=24):
        """% of points along a segment that land on content (for callout stems)."""
        k = self.k
        pts = [(p0[0] + (p1[0] - p0[0]) * i / steps, p0[1] + (p1[1] - p0[1]) * i / steps) for i in range(steps + 1)]
        worst = 0.0
        for _, (_, e) in self._samples(space, t0, t1):
            hit = 0
            for x, y in pts:
                xi, yi = int(x * k), int(y * k)
                if 0 <= xi < e.width and 0 <= yi < e.height and e.getpixel((xi, yi)):
                    hit += 1
            worst = max(worst, 100 * hit / len(pts))
        return worst

    def luma(self, space, rect, t0, t1):
        box = self._crop(rect)
        if box[2] - box[0] < 1 or box[3] - box[1] < 1:
            return 128.0
        vals = [ImageStat.Stat(g.crop(box)).mean[0] for _, (g, _) in self._samples(space, t0, t1)]
        return sum(vals) / len(vals) if vals else 128.0


def plate_for(luma):
    return DARK_PLATE if luma >= 110 else LIGHT_PLATE


# ---------------------------------------------------------------- drawing

# ---- rich text: Japanese font + color emoji, **emphasis** markup
EMOJI_RE = re.compile(
    "(?:[\U0001F1E6-\U0001F1FF]{2}"                                        # flags
    "|[#*0-9]\uFE0F?\u20E3"                                                # keycaps
    "|[\u2600-\u27BF\u2B00-\u2BFF\u2190-\u21FF\u2300-\u23FF\u3297\u3299\u00A9\u00AE\u203C\u2049\u2122\u2139"
    "\U0001F000-\U0001FAFF]\uFE0F?[\U0001F3FB-\U0001F3FF]?"
    "(?:\u200D[\u2600-\u27BF\U0001F000-\U0001FAFF]\uFE0F?[\U0001F3FB-\U0001F3FF]?)*)")


def strip_markup(text, drop_emoji=False):
    """Plain text for speech / logs: no **markup**, optionally no emoji."""
    text = str(text).replace("**", "")
    return EMOJI_RE.sub("", text) if drop_emoji else text


def atoms(text):
    """Split text into (piece, is_emoji, emphasized) atoms; one atom per character / emoji sequence."""
    out, emph, i, text = [], False, 0, str(text)
    while i < len(text):
        if text.startswith("**", i):
            emph = not emph
            i += 2
            continue
        m = EMOJI_RE.match(text, i)
        if m and m.end() > i:
            out.append((m.group(0), True, emph))
            i = m.end()
        else:
            out.append((text[i], False, emph))
            i += 1
    return out


def atom_w(a, font):
    return font.size * 1.12 if a[1] else font.getlength(a[0])


def marker_on():
    return STYLE["caption"].get("emphasis_style") == "marker"


def marker_gap(font):
    """Space added on each side of a marker-emphasized run: just the band's overhang, so spacing
    stays natural. Neighbors' outlines are drawn *under* the band (see draw_rich), so they may meet."""
    return round(font.size * 0.08)


def marked(line):
    """Indices of the **emphasized** atoms that get a marker band: only runs that stand apart (at the
    start / end of the line, or next to a space), because a band in the middle of a word runs into
    the neighboring characters. Other emphasized runs change color instead."""
    out, n = set(), 0
    while n < len(line):
        if not line[n][2]:
            n += 1
            continue
        j = n
        while j + 1 < len(line) and line[j + 1][2]:
            j += 1
        if (n == 0 or line[n - 1][0].isspace()) and (j == len(line) - 1 or line[j + 1][0].isspace()):
            out.update(range(n, j + 1))
        n = j + 1
    return out


def layout_x(line, font):
    """x offset of each atom (marker gaps included) and the total width."""
    m = marked(line) if marker_on() else set()
    xs, x, gap = [], 0.0, marker_gap(font) if m else 0
    for n, a in enumerate(line):
        if gap and n in m and (n == 0 or n - 1 not in m):
            x += gap
        xs.append(x)
        x += atom_w(a, font)
        if gap and n in m and (n == len(line) - 1 or n + 1 not in m):
            x += gap
    return xs, x


def line_w(line, font):
    return layout_x(line, font)[1]


def as_line(l):
    return atoms(l) if isinstance(l, str) else l


WORD_RE = re.compile(r"[A-Za-z0-9_\-.]|[\u30A0-\u30FF\uFF66-\uFF9F]")   # Latin words, katakana runs


def chunks(atom_list):
    """Group atoms that shouldn't be split across lines: Latin words ("ToDo"), katakana runs ("アプリ")
    and short **emphasized** runs (a marker band broken over two lines reads as two words)."""
    out = []
    for a in atom_list:
        kind = "emph" if a[2] and not a[1] else ("word" if not a[1] and WORD_RE.match(a[0]) else None)
        if kind and out and out[-1][1] == kind and len(out[-1][0]) < (10 if kind == "emph" else 12):
            out[-1][0].append(a)
        else:
            out.append(([a], kind))
    return [c for c, _ in out]


def wrap(text, font, max_w):
    """Wrap by measured width (Japanese has no spaces). Words and katakana runs stay together;
    closing punctuation stays on the line."""
    lines = []
    for para in str(text).split("\n"):
        line, w = [], 0.0
        for ch in chunks(atoms(para)):
            cw = sum(atom_w(a, font) for a in ch)
            if line and w + cw > max_w:
                if len(ch) == 1 and not ch[0][1] and ch[0][0] in "、。，．）」』！？!?)":
                    line += ch
                    w += cw
                    continue
                lines.append(line)
                line, w = [], 0.0
            line += ch
            w += cw
        lines.append(line)
    return lines


def fit_wrap(text, size, bold, max_w, min_k=0.8, max_lines=None):
    """Wrap at the given size, shrinking (down to min_k) when that leaves a line holding only an
    emoji or punctuation (a title whose trailing 🎉 would sit alone), or more than max_lines lines."""
    k = 1.0
    while True:
        font = load_font(size * k, bold=bold)
        lines = wrap(text, font, max_w)
        too_many = max_lines is not None and len(lines) > max_lines
        if not (is_orphan(lines) or too_many) or k <= min_k:
            if is_orphan(lines):
                lines = balanced(text, font, max_w, len(lines)) or lines
            return lines, font
        k = round(k - 0.05, 2)


def is_orphan(lines):
    """Last line holds only an emoji / punctuation, or just one or two characters."""
    if len(lines) < 2:
        return False
    last = [a for a in as_line(lines[-1]) if a[0].strip()]
    return all(a[1] or a[0] in "、。！？!?）」』" for a in last) or len(last) <= 2


def balanced(text, font, max_w, n):
    """Narrowest wrap that still fits in n lines: evens out the line lengths."""
    total = sum(atom_w(a, font) for l in wrap(text, font, 1e9) for a in as_line(l))
    w = total / n
    while w < max_w:
        lines = wrap(text, font, w)
        if len(lines) <= n and not is_orphan(lines):
            return lines
        w += font.size * 0.5
    return None


def text_block(lines, font, gap):
    asc, desc = font.getmetrics()
    lh = asc + desc
    w = max((line_w(as_line(l), font) for l in lines), default=0)
    return int(w), lh * len(lines) + gap * (len(lines) - 1), lh


@functools.lru_cache(maxsize=512)
def emoji_image(seq, px, outline=0, outline_rgb=(0, 0, 0), part="all"):
    """A color emoji scaled to px (height), optionally with an outline ring (sticker look).
    part: "all" | "ring" (outline only) | "glyph" (emoji only, same size/offset as "all")."""
    found = find_emoji_font()
    if not found:
        return None
    path, native = found
    f = ImageFont.truetype(path, native)
    im = Image.new("RGBA", (native * 3, native * 2), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((native // 2, native // 4), seq, font=f, embedded_color=True)
    box = im.getbbox()
    if not box:
        return None
    im = im.crop(box)
    k = px / max(1, native)
    im = im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.LANCZOS)
    if outline:
        pad = outline + 1
        canvas = Image.new("RGBA", (im.width + 2 * pad, im.height + 2 * pad), (0, 0, 0, 0))
        a = Image.new("L", canvas.size, 0)
        a.paste(im.getchannel("A"), (pad, pad))
        ring = a.filter(ImageFilter.MaxFilter(outline * 2 + 1))
        if part != "glyph":
            canvas.paste(Image.new("RGBA", canvas.size, tuple(outline_rgb) + (255,)), (0, 0), ring)
        if part != "ring":
            canvas.alpha_composite(im, (pad, pad))
        im = canvas
    return im


def draw_rich(img, x, y, line, font, fill, stroke=0, stroke_fill=None, emph_fill=None, upto=None, emoji=True,
              marker=None):
    """Draw one line of atoms onto img at (x, y). upto: only the first N atoms (typewriter).
    marker: emphasized runs that stand apart (see marked()) get a colored band behind them; the
    others are drawn in the emphasis color."""
    d = ImageDraw.Draw(img)
    asc, desc = font.getmetrics()
    lh = asc + desc
    if marker is None:
        marker = marker_on()
    xs, _ = layout_x(line, font)
    x0 = x
    M = marked(line) if (marker and emph_fill) else set()
    if M and stroke:
        # 1) outlines of the plain text first, so the marker band covers where they meet it
        for n, a in enumerate(line):
            if upto is not None and n >= upto:
                break
            if a[1] and emoji:
                ring = emoji_image(a[0], round(font.size * 1.05), stroke, tuple(stroke_fill[:3]), "ring")
                if ring is not None:
                    aw = atom_w(a, font)
                    img.alpha_composite(ring, (int(x0 + xs[n] + (aw - ring.width) / 2), int(y + (lh - ring.height) / 2)))
            elif not a[1] and n not in M:
                d.text((x0 + xs[n], y), a[0], font=font, fill=stroke_fill, stroke_width=stroke,
                       stroke_fill=stroke_fill)
        plain_outlined = True
    else:
        plain_outlined = False
    if M:
        # bands behind each marked run, drawn first
        run = None
        for n, a in enumerate(line):
            if upto is not None and n >= upto:
                break
            aw = atom_w(a, font)
            bx = x0 + xs[n]
            if n in M and run is None:
                run = bx
            last = n == len(line) - 1 or (upto is not None and n == upto - 1) or n + 1 not in M
            if n in M and last:
                end = bx + aw
                # band around the glyph box (not the line box) so it frames the word evenly; it overhangs
                # by less than the marker gap, so neighboring outlines never reach it
                gtop, gbot = font.getbbox("国")[1], font.getbbox("国")[3]
                pad = max(3, round(font.size * 0.08))
                bw_, bh_ = end - run + 2 * pad, gbot - gtop + 2 * pad
                band = antialiased((bw_, bh_), lambda dd, s, w=bw_, h=bh_: dd.rounded_rectangle(
                    (0, 0, w * s - 1, h * s - 1), radius=max(2, CORNER) * s, fill=emph_fill))
                band = band.rotate(-2, resample=Image.BICUBIC, expand=True)   # hand-drawn marker tilt
                img.alpha_composite(band, (int(run - pad - (band.width - bw_) / 2),
                                           int(y + gtop - pad - (band.height - bh_) / 2)))
                run = None
    for n, a in enumerate(line):
        if upto is not None and n >= upto:
            break
        aw = atom_w(a, font)
        x = x0 + xs[n]
        if a[1]:
            if not emoji:
                x += aw
                continue
            e = emoji_image(a[0], round(font.size * 1.05), stroke,
                            tuple(stroke_fill[:3]) if stroke_fill else (0, 0, 0),
                            "glyph" if plain_outlined else "all")
            if e is not None:
                img.alpha_composite(e, (int(x + (aw - e.width) / 2), int(y + (lh - e.height) / 2)))
            else:   # a symbol in an emoji range that the emoji font doesn't have (→, ⌘ ...): plain text
                d.text((x, y), a[0], font=font, fill=fill, stroke_width=stroke,
                       stroke_fill=stroke_fill if stroke else None)
        else:
            if n in M:
                # on the marker band: white text with a thin dark edge, so the band stays visible
                sw = max(2, stroke // 3) if stroke else 0
                d.text((x, y), a[0], font=font, fill=(255, 255, 255, 255), stroke_width=sw,
                       stroke_fill=(30, 20, 40, 255) if sw else None)
            else:
                color = emph_fill if (a[2] and emph_fill) else fill
                sw = 0 if plain_outlined else stroke     # outline already drawn under the band
                d.text((x, y), a[0], font=font, fill=color, stroke_width=sw,
                       stroke_fill=stroke_fill if sw else None)
        x += aw


def draw_lines(d, x, y, lines, font, lh, gap, fill, center_w=None):
    img = d._image
    emph = tuple(STYLE["caption"].get("emphasis_color", STYLE["accent"]))[:3] + (255,)
    for i, l in enumerate(lines):
        l = as_line(l)
        lx = x + (center_w - line_w(l, font)) / 2 if center_w else x
        draw_rich(img, lx, y + i * (lh + gap), l, font, fill, emph_fill=emph)


SS = 4  # supersampling factor for shapes: ImageDraw has no anti-aliasing, so draw big and shrink


def antialiased(size, draw):
    """Anti-aliased shapes: call draw(d, s) on an SS-times larger canvas (scale coordinates by s),
    then downsample. Premultiplied resize avoids dark fringes on semi-transparent edges."""
    w, h = max(1, int(size[0])), max(1, int(size[1]))
    big = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    draw(ImageDraw.Draw(big), SS)
    return big.convert("RGBa").resize((w, h), Image.LANCZOS).convert("RGBA")


def plate(lines, font, pad_x, pad_y, gap, colors, radius=None, border=None):
    bg, fg, edge = colors
    tw, th, lh = text_block(lines, font, gap)
    w, h = tw + pad_x * 2, th + pad_y * 2
    rad = radius if radius is not None else max(2, round(CORNER * font.size / 30))
    im = antialiased((w, h), lambda d, s: d.rounded_rectangle((0, 0, w * s - 1, h * s - 1), radius=rad * s, fill=bg,
                                                          outline=border or edge, width=2 * s))
    draw_lines(ImageDraw.Draw(im), pad_x, pad_y, lines, font, lh, gap, fg, center_w=tw)
    return im


class Layer:
    def __init__(self, item, start, end, space):
        self.item, self.start, self.end, self.space = item, start, end, space
        self.im, self.x, self.y = None, 0, 0
        self.fade = float(item.get("fade", FADE))
        self.cache = {}

    def alpha_at(self, t):
        if self.fade <= 0:
            return 1.0
        return max(0.0, min(1.0, (t - self.start) / self.fade, (self.end - t) / self.fade))

    def image_at(self, t):
        k = round(self.alpha_at(t) * 10) / 10
        if k >= 1:
            return self.im
        if k not in self.cache:
            out = self.im.copy()
            out.putalpha(self.im.getchannel("A").point(lambda v: int(v * k)))
            self.cache[k] = out
        return self.cache[k]


class Layout:
    def __init__(self, scene, W, H, zoom):
        self.scene, self.W, self.H, self.zoom = scene, W, H, zoom
        self.placed = []      # (space, rect, t0, t1)
        self.targets = []     # (space, rect, t0, t1): things annotations point at; never cover
        self.focus = []       # (space, rect, t0, t1): what the video is about right now (zoom targets,
                              # clicks, captions' "avoid"); vertical text over the picture keeps off it
        self.warnings = []
        self.bands = []
        self.notes = []
        self.last_score = 0.0

    def band_luma(self, t):
        frames = self.scene.src
        g = min(frames, key=lambda f: abs(f[0] - t))[1][0] if frames else None
        if g is None:
            return 128.0
        return ImageStat.Stat(g.crop((0, g.height - 2, g.width, g.height))).mean[0]

    def score(self, space, rect, t0, t1, prefer=0.0):
        x, y, w, h = rect
        if x < 0 or y < 0 or x + w > self.W or y + h > self.H:
            return float("inf")
        if CANVAS and space == "video":
            # vertical: only part of the recording is shown (and the camera moves); off-view = unseen
            for t in (t0, (t0 + t1) / 2, t1):
                vx, vy, vw, vh = self.zoom.rect(t)
                if x < vx or y < vy or x + w > vx + vw or y + h > vy + vh:
                    return float("inf")
        s = self.scene.busy(space, rect, t0, t1) + prefer
        for sp, r, a0, a1 in self.placed + self.targets:
            if a0 < t1 and t0 < a1:
                r2 = r if sp == space else self._convert(r, sp, space, max(a0, t0))
                if overlap_area(rect, r2) > 0:
                    s += 1000
        return s

    def _convert(self, rect, from_space, to_space, t):
        if from_space == "video" and to_space == "screen":
            return self.zoom.to_screen(t, rect)
        if from_space == "screen" and to_space == "video":
            return self.zoom.from_screen(t, rect)
        return rect

    def choose(self, space, candidates, size, t0, t1, what, warn=True):
        w, h = size
        best = None
        for (x, y), prefer in candidates:
            r = (x, y, w, h)
            s = self.score(space, r, t0, t1, prefer)
            if best is None or s < best[0]:
                best = (s, r)
        s, r = best
        if s == float("inf"):
            r = clamp_rect(r, self.W, self.H)
        self.last_score = s
        if s >= WARN_LIMIT and warn:
            self.warnings.append(f"{what} at {t0:.1f}-{t1:.1f}s overlaps content wherever it goes "
                                 f"(score {s:.1f}); leave free space in the recording or shorten the text")
        self.placed.append((space, r, t0, t1))
        return r


def build_layers(items, W, H, dur, scene, zoom):
    u = H / 720
    layout = Layout(scene, W, H, zoom)
    layers = []
    norm = []
    for it in items:
        t = it.get("type", "caption")
        if t in ("zoom", "shake", "narration", "flash"):   # camera / audio / whole-frame only
            continue
        start = float(it.get("start", 0))
        end = start + CLICK_DUR if t == "click" else (float(it["end"]) if it.get("end") is not None else dur)
        if t == "stamp" and it.get("end") is None:
            end = start + float(it.get("duration", 1.2))
        if t == "title" and CANVAS:
            # vertical: the title is a header that stays until the next title (or the end)
            later = [float(o["start"]) for o in items if o.get("type") == "title" and float(o["start"]) > start]
            end = min(later) if later else dur
        if t == "chapter":
            later = [float(o["start"]) for o in items if o.get("type") in ("chapter", "outro")
                     and float(o["start"]) > start]
            end = min(later) if later else dur
        if t == "outro" and it.get("end") is None:
            end = dur
        outro = min((float(o["start"]) for o in items if o.get("type") == "outro"), default=None)
        if outro is not None and t not in ("outro", "click"):
            if start >= outro:
                print(f"[annotate] warning: {t} at {start:.1f}s starts after the outro card; dropped", file=sys.stderr)
                continue
            end = min(end, outro)   # the end card takes over the whole screen
        if end <= start:
            continue
        norm.append((it, t, start, end))
        if t in ("box", "callout") and "w" in it:
            layout.targets.append(("video", (it["x"], it["y"], it["w"], it["h"]), start, end))
    if CANVAS:
        r = 40 * u
        for it in items:
            t = it.get("type")
            s0 = float(it.get("start", 0))
            if t == "zoom" and "w" in it:
                s1 = float(it["end"]) if it.get("end") is not None else dur
                layout.focus.append(("video", (it["x"], it["y"], it["w"], it["h"]), s0, s1))
            elif t == "click":
                layout.focus.append(("video", (it["x"] - r, it["y"] - r, 2 * r, 2 * r), s0 - 0.3, s0 + 0.8))
        for it, t, s0, s1 in norm:
            a = it.get("avoid")
            if t == "caption" and a:
                for r in (a if isinstance(a, list) else [a]):   # each element kept clear on its own
                    layout.focus.append(("video", (r["x"], r["y"], r["w"], r["h"]), s0, s1))
    # boxes first (they define targets), then bubbles/labels, captions last (most flexible)
    # vertical: pinned text (titles, chapter pills) claims its place before captions and stamps look for room
    order = {"title": 0, "click": 1, "box": 2, "callout": 3, "caption": 4, "stamp": 5, "chapter": 6, "outro": 7}
    if CANVAS:
        order["chapter"] = 0.5
    cap_idx = {id(n[0]): k for k, n in enumerate(sorted((n for n in norm if n[1] == "caption"), key=lambda n: n[2]))}
    for it, t, start, end in sorted(norm, key=lambda n: order.get(n[1], 9)):
        space = "screen" if t in ("caption", "title", "stamp", "chapter", "outro") else "video"
        OW, OH = (CANVAS["W"], CANVAS["H"]) if CANVAS else (W, H)
        if t == "title" and CANVAS and float(STYLE.get("title_intro") or 0) > 0 and not any(
                o.get("type") == "title" and float(o["start"]) < start for o in items):
            # intro: big title (and badge) over the dimmed picture first, then the header
            intro = min(float(STYLE["title_intro"]), end - start)
            Hl = Layer(it, start, start + intro, "screen")
            Hl.im, Hl.x, Hl.y = render_hero(it, OW, OH), 0, 0
            Hl.entrance, Hl.idle, Hl.u, Hl.fade = "pop", "none", CANVAS["u"], 0.12
            layers.append(Hl)
            if it.get("badge"):
                B = Layer(it, start, start + intro, "screen")
                B.im, B.x, B.y = render_badge(it, OW, OH)
                B.entrance, B.idle, B.u = it.get("badge_entrance") or "pop", it.get("badge_idle") or "none", CANVAS["u"]
                layers.append(B)
            start = start + intro
        if t == "caption" and CANVAS:
            layers.extend(vertical_caption(it, start, end, layout, cap_idx.get(id(it), 0)))
            continue
        L = Layer(it, start, end, space)
        if t == "title":
            if CANVAS:
                with item_look(it):
                    L.im, L.x, L.y = render_header(it, start, end)
                if STYLE["caption"]["style"] in ("outline", "tag"):
                    L.entrance, L.idle, L.u = it.get("entrance") or "pop", it.get("idle") or "none", CANVAS["u"]
                motion = (getattr(L, "entrance", "fade"), getattr(L, "idle", "none"))
                mr = motion_rect(L.x, L.y, L.im.width, L.im.height, *motion)
                vy, vb = CANVAS["vy"], CANVAS["vy"] + CANVAS["vh"]
                if intersects(mr, picture_rect()):   # keep the moving header clear of the picture
                    gap = int(8 * CANVAS["u"])
                    L.y += (vb - mr[1] + gap) if L.y >= vy else -(mr[1] + mr[3] - vy + gap)
                    mr = motion_rect(L.x, L.y, L.im.width, L.im.height, *motion)
                claim("title", mr, start, end)
            else:
                L.im, L.x, L.y = render_title(it, W, H, u), 0, 0
        elif t == "chapter":
            L.im, L.x, L.y = render_chapter(it, OW, OH)
            L.entrance, L.idle = it.get("entrance") or "pop", it.get("idle") or "none"
            L.u, L.fade = (CANVAS["u"] if CANVAS else u), 0.1
            if CANVAS:
                motion = (L.entrance, L.idle)
                align = it.get("align") or ("right" if STYLE.get("chapter_side") == "right" else "left")
                spot = caption_spot(L.im, dict(it, position=it.get("position", "above")), start, end, layout,
                                    align, motion)
                over = False
                if spot:
                    L.x, L.y, over = spot
                else:
                    layout.warnings.append(f"chapter '{it.get('text', '')[:12]}' at {start:.1f}s found no free spot")
                claim("chapter", motion_rect(L.x, L.y, L.im.width, L.im.height, *motion), start, end, over)
        elif t == "outro":
            dim = Layer(it, start, end, "screen")
            dim.im = Image.new("RGBA", (OW, OH), (0, 0, 0, int(255 * float(it.get("dim", STYLE.get("outro_dim", 0.6))))))
            dim.x = dim.y = 0
            dim.fade = 0.35
            layers.append(dim)
            L.im, L.x, L.y = render_outro(it, OW, OH), 0, 0
            L.entrance, L.idle = it.get("entrance") or "pop", it.get("idle") or "none"
            L.u, L.fade = (CANVAS["u"] if CANVAS else u), 0.12
        elif t == "stamp":
            L.im, L.x, L.y = render_stamp(it, W, H, u)
            motion = (it.get("entrance") or "pop", it.get("idle") or "none")
            if CANVAS:
                L.im, L.x, L.y = stamp_spot(L.im, start, end, layout, f"stamp '{it.get('text', '')[:12]}'", motion)
            L.entrance, L.idle, L.u = motion[0], motion[1], (CANVAS["u"] if CANVAS else u)
            L.fade = 0.1
        elif t == "click":
            L.fade = 0
            L.click = (it["x"], it["y"])
        elif t == "caption":
            L.im, L.x, L.y = render_caption(it, W, H, u, layout, start, end)
            c = STYLE["caption"]
            L.entrance = it.get("entrance") or ("pop" if c["pop"] else c.get("entrance", "fade"))
            L.idle = it.get("idle") or c.get("idle", "none")
            L.u = u if not CANVAS else CANVAS["u"]
            if L.entrance != "fade":
                L.fade = min(L.fade, 0.12)
        elif t == "box":
            L.im, L.x, L.y, fb = render_box(it, u, layout, start, end)
            if fb:
                C = Layer(fb, start, end, "video")
                C.im, C.x, C.y = render_callout(fb, u, layout, start, end)
                layers.append(C)
        elif t == "callout":
            L.im, L.x, L.y = render_callout(it, u, layout, start, end)
        else:
            raise ValueError(f"unknown annotation type: {t}")
        layers.append(L)
    if CANVAS:
        layout.warnings.extend(canvas_audit())
    return layers, layout.warnings, layout.notes


# ---------------------------------------------------------------- vertical canvas placement
# Everything drawn around (or on) the picture registers its rect and time span in CANVAS["placed"],
# so a later piece of text can go somewhere else instead of covering it. Positions differ by
# composition and caption alignment; the rules (nothing covers content or other text) don't.

def picture_rect():
    return (0, CANVAS["vy"], CANVAS["W"], CANVAS["vh"])


def reserved_strips():
    """Canvas strips no text may cover: the progress bar and the voice credit."""
    c, u = CANVAS, CANVAS["u"]
    out = []
    if STYLE["progress"]["enabled"]:
        h = int(max(20, float(STYLE["progress"]["height"]) + 10) * u)
        out.append((0, c["H"] - h if STYLE["progress"].get("position") == "bottom" else 0, c["W"], h))
    if CREDIT:
        out.append((0, c["H"] - int(48 * u), c["W"], int(48 * u)))
    return out


def motion_rect(x, y, w, h, entrance="fade", idle="none"):
    """Room a piece of text needs while it pops in and then floats / pulses / wiggles."""
    k = POP_SCALE if entrance == "pop" else (1.04 if idle == "pulse" else 1.0)
    dx, dy = w * (k - 1) / 2, h * (k - 1) / 2
    if idle == "float":
        dy += 6 * CANVAS["u"]
    elif idle == "wiggle":   # +-2.5 degrees
        dx, dy = dx + h * 0.022, dy + w * 0.022
    return (x - dx, y - dy, w + 2 * dx, h + 2 * dy)


def canvas_hits(rect, t0, t1, over=False):
    """What rect would cover during [t0, t1): the canvas edge, the picture (unless over), the
    reserved strips, or other text placed earlier."""
    c = CANVAS
    x, y, w, h = rect
    hits = []
    if x < 0 or y < 0 or x + w > c["W"] or y + h > c["H"]:
        hits.append(("edge", None))
    if not over and intersects(rect, picture_rect()):
        hits.append(("picture", picture_rect()))
    for r in reserved_strips():
        if intersects(rect, r):
            hits.append(("strip", r))
    for kind, r, a0, a1 in c["placed"]:
        if a0 < t1 and t0 < a1 and intersects(rect, r):
            hits.append((kind, r))
    return hits


def claim(kind, rect, t0, t1, over=False, owner=None):
    """owner: index of the claim this one belongs to (a sticker on its caption may overlap it)."""
    CANVAS["placed"].append((kind, rect, t0, t1))
    if owner is not None:
        CANVAS.setdefault("pairs", set()).add((owner, len(CANVAS["placed"]) - 1))
    if over:
        CANVAS.setdefault("over", []).append((rect, t0, t1))


FOCUS_HIT = 1000.0   # over_busy() score for covering what the video is pointing at


def changed_mask(scene, t0, t1, layout):
    """Where the recording changes while a piece of text is up (a row that appears, a count that
    updates, the cursor at work): that is what the scene is about, so text on the picture keeps off
    it. Low-res, source space, cached per time span."""
    key = (round(t0, 2), round(t1, 2))
    cache = layout.__dict__.setdefault("changed", {})
    if key not in cache:
        sel = [g for t, (g, _) in scene.src if t0 - 0.01 <= t <= t1 + 0.3]
        m = None
        for g in sel[1:]:
            d = ImageChops.difference(sel[0], g).point(lambda v: 255 if v > 24 else 0)
            m = d if m is None else ImageChops.lighter(m, d)
        cache[key] = m.filter(ImageFilter.MaxFilter(9)) if m is not None else None
    return cache[key]


def over_busy(rect, t0, t1, layout):
    """Score of a canvas rect over the picture, following the camera (zooms, punch-ins, drift)
    through [t0, t1): FOCUS_HIT or more when it would cover what the video is pointing at (zoom
    target, box / callout and its target, a click, a caption's "avoid" rect, anything that changes
    on screen meanwhile); otherwise how busy
    the recording under it is (0-100), so emptier spots win. Covering other content is allowed."""
    c, scene, zoom = CANVAS, layout.scene, layout.zoom
    x, y, w, h = rect
    vx, vy, vw, vh = picture_rect()
    if x < vx or y < vy or x + w > vx + vw or y + h > vy + vh:
        return float("inf")
    frames = [f for f in scene.src if t0 - 0.01 <= f[0] <= t1 + 0.01]
    if not frames and scene.src:
        frames = [min(scene.src, key=lambda f: abs(f[0] - t0))]
    changed = changed_mask(scene, t0, t1, layout)
    worst = 0.0
    for t, (_, e) in frames:
        zx, zy, zw, zh = zoom.rect(t)
        sx, sy = zw / vw, zh / vh
        r = (zx + (x - vx) * sx, zy + (y - vy) * sy, w * sx, h * sy)
        k = scene.k
        box = (int(max(0, r[0] * k)), int(max(0, r[1] * k)), int(min(e.width, (r[0] + r[2]) * k)),
               int(min(e.height, (r[1] + r[3]) * k)))
        if box[2] - box[0] >= 1 and box[3] - box[1] >= 1:
            worst = max(worst, ImageStat.Stat(e.crop(box)).mean[0] / 2.55)
            if changed is not None and ImageStat.Stat(changed.crop(box)).mean[0] > 2.55 * 0.5:
                return FOCUS_HIT   # something appears / changes there while the text is up
        for sp, tr, a0, a1 in layout.placed + layout.targets + layout.focus:
            if sp == "video" and a0 <= t < a1 and overlap_area(r, tr) > 0:
                return FOCUS_HIT   # boxes / callouts / their targets / zoom targets / clicks
    return worst


def free_gaps(t0, t1):
    """Vertical spans of the canvas with nothing in them during [t0, t1) (full width)."""
    c = CANVAS
    spans = sorted((r[1], r[1] + r[3]) for r in [picture_rect()] + reserved_strips() +
                   [r for _, r, a0, a1 in c["placed"] if a0 < t1 and t0 < a1])
    gaps, y = [], 0
    for a, b in spans:
        if a > y:
            gaps.append((y, a))
        y = max(y, b)
    if y < c["H"]:
        gaps.append((y, c["H"]))
    return gaps


def slide_into_band(size, x, t0, t1, side, moving):
    """y for a w x h piece of text next to the picture: start right above / below it and step away
    past anything already there. None when it runs out of room."""
    c, u = CANVAS, CANVAS["u"]
    w, h = size
    gap = int(22 * u)
    y = c["vy"] + c["vh"] + gap if side == "below" else c["vy"] - gap - h
    for _ in range(12):
        mr = motion_rect(x, y, w, h, *moving)
        hits = canvas_hits(mr, t0, t1)
        if not hits:
            return y
        others = [r for kind, r in hits if r is not None and kind != "picture"]
        if not others or any(k == "edge" for k, _ in hits):
            return None
        if side == "below":
            y = max(r[1] + r[3] for r in others) + gap + (y - mr[1])
        else:
            y = min(r[1] for r in others) - gap - h - (mr[1] + mr[3] - y - h)
    return None


def caption_look(it, idx):
    c = STYLE["caption"]
    align = it.get("align") or c.get("align") or "center"
    tilt = float(it.get("tilt", c.get("tilt", 0)) or 0)
    if align == "stagger":
        align = "left" if idx % 2 == 0 else "right"
        tilt = abs(tilt) if idx % 2 == 0 else -abs(tilt)
    return align, tilt


def vertical_caption_image(text, it, align, tilt):
    c = STYLE["caption"]
    uc = CANVAS["u"]
    style = c["style"]
    lines, font = fit_wrap(text, it.get("size", STYLE["caption_size"]) * uc,
                           "heavy" if c["weight"] == "heavy" else True,
                           CANVAS["W"] * (0.8 if align == "center" else 0.72))

    def render(upto=None):
        if style == "tag":
            im = tagged(lines, font, uc, upto=upto, align=align)
        elif style == "outline":
            im = outlined(lines, font, uc, upto=upto, align=align)
        else:
            im = plate(lines, font, int(28 * uc), int(14 * uc), int(6 * uc), DARK_PLATE)
        return im.rotate(tilt, resample=Image.BICUBIC, expand=True) if tilt else im

    im = render()
    if style in ("outline", "tag"):
        im.info["rich"] = (lines, font, uc)
        im.info["render"] = render
    return im


def caption_spot(im, it, t0, t1, layout, align, moving):
    """(x, y, over) for a caption on the vertical canvas, or None. Tries the zones in the
    composition's order (a caption's own "position" goes first): next to the picture, or on a free
    spot of the picture itself."""
    c, u = CANVAS, CANVAS["u"]
    W = c["W"]
    w, h = im.size
    m = int(40 * u)

    side = int(max(m, -motion_rect(0, 0, w, h, *moving)[0] + 8 * u))   # pop-in grows sideways too

    def xs(a):
        return {"left": side, "right": W - side - w}.get(a, (W - w) // 2)

    order = {"classic": ["below", "above", "over"], "overlay": ["over", "below", "above"]}[c["comp"]]
    want = {"top": "above", "bottom": "below"}.get(it.get("position"), it.get("position"))
    if want in order:
        order = [want] + [z for z in order if z != want]
    if STYLE["caption"]["style"] == "plate":
        order = [z for z in order if z != "over"]   # a plate's color is picked for the band, not the picture
    for zone in order:
        if zone == "over":
            g = int(28 * u)
            best = None
            for a in [align] + [b for b in ("center", "left", "right") if b != align]:
                for f in (0, 1, 0.5, 0.25, 0.75):   # top / bottom of the picture first, then its middle
                    y = round(c["vy"] + g + (c["vh"] - 2 * g - h) * f)
                    mr = motion_rect(xs(a), y, w, h, *moving)
                    if canvas_hits(mr, t0, t1, over=True):
                        continue
                    sc = over_busy(mr, t0, t1, layout)
                    if sc < FOCUS_HIT and (best is None or sc < best[0]):
                        best = (sc, xs(a), y)
                if best:
                    break
            if best:
                return best[1], best[2], True
        else:
            y = slide_into_band((w, h), xs(align), t0, t1, zone, moving)
            if y is not None:
                return xs(align), y, False
    return None


def vertical_caption(it, t0, t1, layout, idx):
    """Caption layer (and its emoji sticker) on the vertical canvas."""
    c, uc = STYLE["caption"], CANVAS["u"]
    W = CANVAS["W"]
    align, tilt = caption_look(it, idx)
    entrance = it.get("entrance") or ("pop" if c["pop"] else c.get("entrance", "fade"))
    idle = it.get("idle") or c.get("idle", "none")
    moving = (entrance, idle)
    text = it.get("text", "")
    body, emo = split_sticker(text) if c.get("emoji_sticker") else (text, None)
    stk = None
    if emo and body.strip():
        f = load_font(STYLE["caption_size"] * uc)
        sw = max(2, round(float(c["outline_width"]) * uc * 0.6))
        stk = emoji_image(emo[:8], round(f.size * 1.6), sw, (255, 255, 255))
        if stk is not None:
            stk = stk.rotate(12 if align == "right" else -12, resample=Image.BICUBIC, expand=True)
    what = f"caption '{text[:16]}'"
    tries = [(body, stk), (text, None)] if stk is not None else [(text, None)]
    for txt, sticker in tries:
        im = vertical_caption_image(txt, it, align, tilt)
        spot = caption_spot(im, it, t0, t1, layout, align, moving)
        if not spot:
            continue
        x, y, over = spot
        w, h = im.size
        sxy = None
        if sticker is not None:
            sw_, sh_ = sticker.size
            # beside the caption's corner, overlapping only its transparent outline margin
            sides = [x + w - sw_ * 0.06, x - sw_ * 0.94]
            if align == "right":
                sides.reverse()
            cands = [(sx, sy) for sy in (y - sh_ * 0.4, y + (h - sh_) / 2, y + h - sh_ * 0.6) for sx in sides]
            for sx, sy in cands:
                mr = motion_rect(int(sx), int(sy), sw_, sh_, "pop", it.get("idle") or "none")
                on_pic = intersects(mr, picture_rect())
                if canvas_hits(mr, t0, t1, over=over and on_pic):
                    continue
                if on_pic and over_busy(mr, t0, t1, layout) >= FOCUS_HIT:
                    continue
                sxy = (int(sx), int(sy), mr, on_pic)
                break
            if sxy is None:
                continue   # no room for the sticker: keep the emoji in the caption instead
        claim("caption", motion_rect(x, y, w, h, *moving), t0, t1, over)
        out = []
        L = Layer(it, t0, t1, "screen")
        L.im, L.x, L.y = im, int(x), int(y)
        L.entrance, L.idle, L.u = entrance, idle, uc
        if L.entrance != "fade":
            L.fade = min(L.fade, 0.12)
        out.append(L)
        if sxy:
            claim("sticker", sxy[2], t0 + 0.12, t1, sxy[3], owner=len(CANVAS["placed"]) - 1)
            S = Layer({"type": "sticker"}, t0 + 0.12, t1, "screen")
            S.im, S.x, S.y = sticker, sxy[0], sxy[1]
            S.entrance, S.idle, S.u, S.fade = "pop", it.get("idle") or "none", uc, 0.1
            out.append(S)
        return out
    # nowhere free: under the picture anyway, and say so
    im = vertical_caption_image(text, it, align, tilt)
    x, y = (W - im.width) // 2, CANVAS["vy"] + CANVAS["vh"] + int(22 * uc)
    layout.warnings.append(f"{what} at {t0:.1f}-{t1:.1f}s found no free spot around the picture "
                           f"(too much text at once?); shorten it or drop a stamp/chapter there")
    claim("caption", motion_rect(x, y, im.width, im.height, *moving), t0, t1)
    L = Layer(it, t0, t1, "screen")
    L.im, L.x, L.y = im, x, y
    L.entrance, L.idle, L.u = entrance, idle, uc
    return [L]


def canvas_audit():
    """Pairs of text that overlap in time and space, text over the picture that wasn't placed on a
    free spot, text under the bars. Should be empty."""
    c = CANVAS
    over = c.get("over", [])
    out = []
    pl = c["placed"]
    for i, (k1, r1, a0, a1) in enumerate(pl):
        if intersects(r1, picture_rect()) and not any(r == r1 and b0 == a0 for r, b0, _ in over):
            out.append(f"{k1} at {a0:.1f}s covers the picture")
        for j, (k2, r2, b0, b1) in enumerate(pl[i + 1:], i + 1):
            if (i, j) in c.get("pairs", ()):
                continue
            if a0 < b1 and b0 < a1 and intersects(r1, r2):
                out.append(f"{k1} at {a0:.1f}s and {k2} at {b0:.1f}s overlap")
    return out


def render_header(it, t0, t1):
    """Vertical: the title as a header in the band above the picture. Returns (image, x, y)."""
    c, uc, CW = CANVAS, CANVAS["u"], CANVAS["W"]
    heavy = "heavy" if STYLE["caption"]["weight"] == "heavy" else True
    tl, big = fit_wrap(it["text"], it.get("size", STYLE["title_size"] * 0.8) * uc, heavy, CW * 0.82,
                       min_k=0.6, max_lines=2)
    small = load_font(it.get("sub_size", 26) * uc, bold=True)
    fills = [[255, 255, 255], STYLE["caption"]["color"]] if STYLE.get("header_two_tone") else None
    head = outlined(tl, big, uc, fills=fills) if STYLE["caption"]["style"] in ("outline", "tag") else None
    if head is None:
        tw, th, lh = text_block(tl, big, int(10 * uc))
        head = Image.new("RGBA", (tw + 4, th + 4), (0, 0, 0, 0))
        draw_lines(ImageDraw.Draw(head), 2, 2, tl, big, lh, int(10 * uc), (255, 255, 255, 255), center_w=tw)
    sub = None
    if it.get("sub"):
        sl = wrap(it["sub"], small, CW * 0.9)
        sw, sh, slh = text_block(sl, small, int(6 * uc))
        sub = Image.new("RGBA", (sw + 4, sh + 4), (0, 0, 0, 0))
        draw_lines(ImageDraw.Draw(sub), 2, 2, sl, small, slh, int(6 * uc), (235, 238, 245, 255), center_w=sw)
    total = head.height + (sub.height + int(12 * uc) if sub else 0)
    im = Image.new("RGBA", (max(head.width, sub.width if sub else 0), total), (0, 0, 0, 0))
    im.alpha_composite(head, ((im.width - head.width) // 2, 0))
    if sub:
        im.alpha_composite(sub, ((im.width - sub.width) // 2, head.height + int(12 * uc)))
    top = int(24 * uc) if STYLE["progress"]["enabled"] else int(12 * uc)
    pill_room = int(64 * uc)   # a chapter pill goes between the header and the picture
    room = c["vy"] - top - pill_room
    if im.height > room > 0:   # never into the picture: shrink a header that doesn't fit its band
        k = max(0.5, room / im.height)
        im = im.resize((round(im.width * k), round(im.height * k)), Image.LANCZOS)
    y0 = max(top, top + (c["vy"] - pill_room - top - im.height) // 2)
    return im, (CW - im.width) // 2, int(y0)


LOOK_KEYS = ("color", "outline_color", "outline_width", "emphasis_color", "emphasis_style", "style", "weight")


@contextlib.contextmanager
def item_look(look):
    """Draw with an item's own text look ("color", "outline_color", ...) over STYLE["caption"]."""
    c = STYLE["caption"]
    over = {k: look[k] for k in LOOK_KEYS if look and k in look}
    saved = {k: c[k] for k in over}
    c.update(over)
    try:
        yield
    finally:
        c.update(saved)


def sub_look(it, prefix):
    """{"shape": ..., "color": ...} from an item's "<prefix>_shape", "<prefix>_color", ... keys."""
    n = len(prefix) + 1
    return {k[n:]: v for k, v in it.items() if k.startswith(prefix + "_")}


def render_label(text, look, uc, size, fill):
    """A short label (chapter, badge, ...) in whatever shape the item asks for:
    "shape": "pill" (default) | "box" (small corners) | "outline" (outlined text, no plate) | "plain";
    "color" (text), "fill" (plate), "rim" (border / outline color, null = none), "size", "tilt"."""
    shape = look.get("shape", "pill")
    color = tuple(look.get("color", [255, 255, 255])[:3]) + (255,)
    font = load_font(float(look.get("size", size)) * uc, bold="heavy")
    lines = [atoms(text)]
    if shape in ("outline", "plain"):
        with item_look({"color": list(color[:3]), "outline_color": look.get("rim") or [0, 0, 0],
                        "outline_width": 6 if shape == "outline" else 2}):
            im = outlined(lines, font, uc)
    else:
        tw, th, lh = text_block(lines, font, 0)
        px, py = int(18 * uc), int(6 * uc)
        w, h = tw + 2 * px, lh + 2 * py
        rad = (h // 2) if shape == "pill" else max(2, round(CORNER * uc))
        rim = look.get("rim", [255, 255, 255])
        fl = tuple(look.get("fill", fill)[:3]) + (255,)
        im = antialiased((w, h), lambda d, s: d.rounded_rectangle(
            (0, 0, w * s - 1, h * s - 1), radius=rad * s, fill=fl,
            outline=tuple(rim[:3]) + (255,) if rim else None, width=max(2, round(2 * uc)) * s if rim else 0))
        draw_rich(im, px, py, lines[0], font, color)
    tilt = float(look.get("tilt", 0) or 0)
    return im.rotate(tilt, resample=Image.BICUBIC, expand=True) if tilt else im


def render_hero(it, OW, OH):
    """Title intro: the title big in the middle of the picture, over a dimmed frame."""
    uc = CANVAS["u"] if CANVAS else OH / 720
    heavy = "heavy" if STYLE["caption"]["weight"] == "heavy" else True
    # leave room for the outline: the text box must stay inside 86% of the width
    lines, font = fit_wrap(it["text"], STYLE["title_size"] * 0.85 * uc, heavy, OW * 0.8, min_k=0.55, max_lines=3)
    fills = [[255, 255, 255], STYLE["caption"]["color"]] if STYLE.get("header_two_tone") else None
    text = outlined(lines, font, uc * 1.2, fills=fills)
    if text.width > OW * 0.94:
        text = text.resize((int(OW * 0.94), int(text.height * OW * 0.94 / text.width)), Image.LANCZOS)
    im = Image.new("RGBA", (OW, OH), (0, 0, 0, 0))
    vy, vh = (CANVAS["vy"], CANVAS["vh"]) if CANVAS else (0, OH)
    ImageDraw.Draw(im).rectangle((0, vy, OW, vy + vh), fill=(0, 0, 0, 90))   # dim the picture: title card
    ty = vy + (vh - text.height) // 2
    im.alpha_composite(text, ((OW - text.width) // 2, ty))
    if CANVAS:
        CANVAS["hero_top"], CANVAS["hero_bottom"] = ty, ty + text.height
    return im


def render_badge(it, OW, OH):
    """Small label with the intro title (on the dimmed picture, part of the title card). Its look comes
    from the item's "badge_*" keys (see render_label); "badge_position": "above" (default) | "below"."""
    uc = CANVAS["u"] if CANVAS else OH / 720
    look = sub_look(it, "badge")
    b = render_label(it["badge"], look, uc, 30, STYLE["badge_color"])
    top = CANVAS.get("hero_top", OH // 3) if CANVAS else OH // 3
    if look.get("position") == "below":
        y = min(OH - b.height - int(30 * uc), CANVAS.get("hero_bottom", OH // 2) + int(16 * uc) if CANVAS else OH // 2)
    else:
        y = max(int(30 * uc), top - b.height - int(16 * uc))
    return b, (OW - b.width) // 2, y


def render_chapter(it, OW, OH):
    """Section label. Look from the item (see render_label). Vertical: placed by build_layers like a
    caption ("position", "align"; default right above the picture). Landscape: top-left corner."""
    uc = CANVAS["u"] if CANVAS else OH / 720
    b = render_label(it["text"], it, uc, 24, STYLE["badge_color"])
    if CANVAS:
        c = CANVAS
        x = c["W"] - b.width - int(16 * uc) if STYLE.get("chapter_side") == "right" else int(16 * uc)
        # just above the picture, so it never covers the recording
        y = c["vy"] - b.height - int(8 * uc)
        return b, x, y
    return b, int(16 * uc), int(14 * uc)


def render_outro(it, OW, OH):
    """End card: the whole frame dims, a big line (and an optional sub line) on top."""
    uc = CANVAS["u"] if CANVAS else OH / 720
    with item_look(it):
        heavy = "heavy" if STYLE["caption"]["weight"] == "heavy" else True
        lines, font = fit_wrap(it["text"], float(it.get("size", STYLE["title_size"] * 0.85)) * uc, heavy, OW * 0.88,
                               min_k=0.6, max_lines=3)
        text = outlined(lines, font, uc) if STYLE["caption"]["style"] in ("outline", "tag") else \
            plate(lines, font, int(28 * uc), int(14 * uc), int(6 * uc), DARK_PLATE)
    sub = None
    if it.get("sub"):
        sf = load_font(34 * uc, bold=True)
        sl = wrap(it["sub"], sf, OW * 0.88)
        sw, sh, slh = text_block(sl, sf, int(6 * uc))
        sub = Image.new("RGBA", (sw + 8, sh + 8), (0, 0, 0, 0))
        sub_rgb = it.get("sub_color", it.get("color", STYLE["caption"]["color"]))
        draw_lines(ImageDraw.Draw(sub), 4, 4, sl, sf, slh, int(6 * uc), tuple(sub_rgb[:3]) + (255,), center_w=sw)
    total = text.height + (sub.height + int(18 * uc) if sub else 0)
    # "position": "center" (default) | "top" | "bottom" of the frame
    y = {"top": int(OH * 0.14), "bottom": OH - total - int(OH * 0.14)}.get(it.get("position"), (OH - total) // 2)
    im = Image.new("RGBA", (OW, OH), (0, 0, 0, 0))
    im.alpha_composite(text, ((OW - text.width) // 2, y))
    if sub:
        im.alpha_composite(sub, ((OW - sub.width) // 2, y + text.height + int(18 * uc)))
    return im


TRAILING_EMOJI = re.compile("(?:" + EMOJI_RE.pattern + r"|\s)+$")


def split_sticker(text):
    """("caption text", "🔥") when the caption ends with emoji, else (text, None)."""
    m = TRAILING_EMOJI.search(str(text))
    if not m or not EMOJI_RE.search(m.group(0)):
        return text, None
    return text[:m.start()], "".join(EMOJI_RE.findall(m.group(0)))


def render_stamp(it, W, H, u):
    """Big tilted pop-up text for key moments. Vertical: in the empty band under the captions
    (never over the recording); landscape: upper middle of the frame."""
    c = STYLE["caption"]
    uu = CANVAS["u"] if CANVAS else u
    OW = CANVAS["W"] if CANVAS else W
    if it.get("shape") in ("pill", "box"):
        im = render_label(it["text"], dict(it, tilt=0, size=it.get("size", STYLE["stamp_size"] * 0.6)), uu, 40,
                          it.get("fill", c.get("emphasis_color", STYLE["accent"])))
    else:
        lines, font = fit_wrap(it["text"], it.get("size", STYLE["stamp_size"]) * uu, "heavy", OW * 0.8, min_k=0.6,
                               max_lines=2)
        look = {"color": list(c.get("emphasis_color", STYLE["accent"]))[:3], "emphasis_style": "color"}
        look.update({k: it[k] for k in LOOK_KEYS if k in it})
        with item_look(look):
            im = outlined(lines, font, uu * 1.3)
    im = im.rotate(float(it.get("angle", 7)), resample=Image.BICUBIC, expand=True)
    if CANVAS:
        return im, None, None   # placed by stamp_spot() once the other text is known
    else:
        y = int(H * 0.12)
    return im, (OW - im.width) // 2, y


def stamp_spot(im, t0, t1, layout, what, motion=("pop", "none")):
    """(image, x, y) for a stamp on the vertical canvas: the biggest empty span at that time (shrunk
    to fit if needed), else a free spot on the picture, else the biggest span with a warning."""
    c, u = CANVAS, CANVAS["u"]
    g = int(16 * u)
    gaps = sorted(free_gaps(t0, t1), key=lambda ab: ab[1] - ab[0], reverse=True)
    a, b = gaps[0] if gaps else (0, c["H"])
    need = motion_rect(0, 0, im.width, im.height, *motion)[3] + 2 * g
    if need > b - a and (b - a) / need >= 0.6:
        k = (b - a) / need
        im = im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.LANCZOS)
        need = b - a
    if need <= b - a:
        x, y = (c["W"] - im.width) // 2, int(a + (b - a - im.height) / 2)
        mr = motion_rect(x, y, im.width, im.height, *motion)
        if not canvas_hits(mr, t0, t1):
            claim("stamp", mr, t0, t1)
            return im, x, y
    for y in (c["vy"] + g, c["vy"] + c["vh"] - g - im.height, c["vy"] + (c["vh"] - im.height) // 2):
        x = (c["W"] - im.width) // 2
        mr = motion_rect(x, y, im.width, im.height, *motion)
        if not canvas_hits(mr, t0, t1, over=True) and over_busy(mr, t0, t1, layout) < FOCUS_HIT:
            claim("stamp", mr, t0, t1, over=True)
            return im, x, y
    layout.warnings.append(f"{what} at {t0:.1f}-{t1:.1f}s has no free room; shorten it or move it off "
                           f"a busy moment")
    x, y = (c["W"] - im.width) // 2, int(a + max(0, (b - a - im.height) / 2))
    claim("stamp", motion_rect(x, y, im.width, im.height, *motion), t0, t1)
    return im, x, y


def draw_progress(frame, t):
    p = STYLE["progress"]
    h = max(2, int(p["height"] * (CANVAS["u"] if CANVAS else frame.height / 720)))
    w = int(frame.width * min(1.0, max(0.0, t / max(0.01, DUR))))
    y = frame.height - h if p.get("position") == "bottom" else 0
    d = ImageDraw.Draw(frame)
    d.rectangle((0, y, frame.width, y + h), fill=tuple(p.get("track", [0, 0, 0])[:3]) + (255,))
    d.rectangle((0, y, w, y + h), fill=tuple(p["color"][:3]) + (255,))


DUR = 0.0
CREDIT = None   # e.g. "VOICEVOX:ずんだもん" when the narration uses VOICEVOX


def draw_credit(frame):
    u = frame.height / (1920 if CANVAS else 720)
    f = load_font(max(12, 22 * u * (1.6 if CANVAS else 1)), bold=True)
    w = f.getlength(CREDIT)
    x, y = frame.width - w - int(14 * u), frame.height - f.size - int(14 * u)
    ImageDraw.Draw(frame).text((x, y), CREDIT, font=f, fill=(255, 255, 255, 210), stroke_width=max(1, int(2 * u)),
                               stroke_fill=(0, 0, 0, 200))


def render_title(it, W, H, u):
    big = load_font(it.get("size", STYLE["title_size"]) * u, bold="heavy" if STYLE["caption"]["weight"] == "heavy" else True)
    small = load_font(it.get("sub_size", 28) * u, bold=False)
    im = Image.new("RGBA", (W, H), (10, 14, 28, int(255 * it.get("dim", STYLE["title_dim"]))))
    d = ImageDraw.Draw(im)
    tl = wrap(it["text"], big, W * 0.85)
    sl = wrap(it.get("sub", ""), small, W * 0.85) if it.get("sub") else []
    tw, th, tlh = text_block(tl, big, int(10 * u))
    sw, sh, slh = text_block(sl, small, int(6 * u)) if sl else (0, 0, 0)
    gap = int(20 * u) if sl else 0
    y0 = (H - th - gap - sh) // 2
    draw_lines(d, 0, y0, tl, big, tlh, int(10 * u), (255, 255, 255, 255), center_w=W)
    if sl:
        draw_lines(d, 0, y0 + th + gap, sl, small, slh, int(6 * u), (200, 210, 225, 255), center_w=W)
    return im


def outlined(lines, font, u, upto=None, fills=None, align="center"):
    """Big outlined caption text (no plate): fill color + thick stroke + soft drop shadow.
    Emoji are drawn in color with the same outline; **emphasis** uses caption.emphasis_color.
    upto: render only the first N atoms (typewriter entrance). align: lines within the block."""
    c = STYLE["caption"]
    sw = max(2, round(float(c["outline_width"]) * u))
    gap = int(6 * u)
    tw, th, lh = text_block(lines, font, gap)
    pad = sw + int(6 * u)
    fill = tuple(c["color"][:3]) + (255,)
    stroke = tuple(c["outline_color"][:3]) + (255,)
    emph = tuple(c.get("emphasis_color", STYLE["accent"]))[:3] + (255,)
    im = Image.new("RGBA", (tw + pad * 2, th + pad * 2), (0, 0, 0, 0))
    left = upto
    rows = []
    for i, l in enumerate(lines):
        l = as_line(l)
        n = None if left is None else max(0, min(len(l), left))
        if left is not None:
            left -= len(l)
        slack = tw - line_w(l, font)
        rows.append((pad + {"left": 0, "right": slack}.get(align, slack / 2), pad + i * (lh + gap), l, n))
    shadow = Image.new("RGBA", im.size, (0, 0, 0, 0))
    off = max(2, int(4 * u))
    for x, y, l, n in rows:
        draw_rich(shadow, x + off, y + off, l, font, (0, 0, 0, 120),
                  sw, (0, 0, 0, 120), (0, 0, 0, 120), upto=n, emoji=False)
    im.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(max(1, 3 * u))))
    marker = c.get("emphasis_style") == "marker"
    for i, (x, y, l, n) in enumerate(rows):
        f = tuple(fills[min(i, len(fills) - 1)][:3]) + (255,) if fills else fill
        draw_rich(im, x, y, l, font, f, sw, stroke, emph, upto=n, marker=marker)
    return im


def tagged(lines, font, u, upto=None, align="center"):
    """Caption as solid strips in the caption color, one per line, with dark text (the short-video
    "tag" look). A dark rim keeps a strip from melting into whatever is behind it."""
    c = STYLE["caption"]
    col = tuple(c["color"][:3])
    ink = (20, 20, 28, 255) if 0.299 * col[0] + 0.587 * col[1] + 0.114 * col[2] > 140 else (255, 255, 255, 255)
    emph = tuple(c.get("emphasis_color", STYLE["accent"]))[:3] + (255,)
    px, py, gap, rim = int(16 * u), int(6 * u), int(8 * u), max(2, round(3 * u))
    asc, desc = font.getmetrics()
    sh = asc + desc + 2 * py + 2 * rim
    rows = [as_line(l) for l in lines]
    ws = [round(line_w(l, font)) + 2 * px + 2 * rim for l in rows]
    bw = max(ws)
    im = Image.new("RGBA", (bw, len(rows) * sh + (len(rows) - 1) * gap), (0, 0, 0, 0))
    left = upto
    for i, (l, sw) in enumerate(zip(rows, ws)):
        n = None if left is None else max(0, min(len(l), left))
        if left is not None:
            left -= len(l)
        if n == 0:
            continue
        x = {"left": 0, "right": bw - sw}.get(align, (bw - sw) // 2)
        y = i * (sh + gap)
        strip = antialiased((sw, sh), lambda d, s, w=sw: d.rounded_rectangle(
            (0, 0, w * s - 1, sh * s - 1), radius=max(2, CORNER) * s, fill=col + (255,),
            outline=(16, 16, 22, 255), width=rim * s))
        im.alpha_composite(strip, (x, y))
        draw_rich(im, x + rim + px, y + rim + py, l, font, ink, emph_fill=emph, upto=n, marker=False)
    return im


POP_SCALE = 1.15  # peak size of a pop-in caption; placement reserves this much room

CANVAS = None  # vertical layout geometry (set in main), None for landscape
FLASHES = []   # (start, end, opacity, rgb)
FLASH_MIN_GAP = 0.5   # s between flashes: at most 2 per second (photosensitivity guideline is <= 3)


def setup_flashes(items):
    """Collect "flash" items, dropping any that come too soon after the previous one."""
    global FLASHES
    f = STYLE["flash"]
    FLASHES, warns, last = [], [], -1e9
    for it in sorted((i for i in items if i.get("type") == "flash"), key=lambda i: float(i["start"])):
        s0 = float(it["start"])
        if s0 - last < FLASH_MIN_GAP:
            warns.append(f"flash at {s0:.2f}s dropped: less than {FLASH_MIN_GAP}s after the previous one")
            continue
        dur = float(it.get("duration", f["duration"]))
        FLASHES.append((s0, s0 + dur, min(0.85, float(it.get("opacity", f["opacity"]))), tuple(f["color"][:3])))
        last = s0
    return warns


def flash_at(t):
    for s0, s1, op, rgb in FLASHES:
        if s0 <= t < s1:
            return rgb, op * (1 - (t - s0) / (s1 - s0)) ** 1.5   # bright at once, quick fall-off
    return None


def setup_canvas(W, H):
    """Vertical layout: the recording fills the canvas width a bit above center."""
    global CANVAS
    if STYLE["layout"] != "vertical":
        CANVAS = None
        return W, H
    CW, CH = (int(v) for v in STYLE["canvas"])
    k = CW / W
    comp = STYLE.get("composition") or "classic"
    if comp not in COMPOSITIONS:
        print(f"[annotate] warning: unknown composition '{comp}', using classic", file=sys.stderr)
        comp = "classic"
    CANVAS = {"W": CW, "H": CH, "k": k, "u": CW / 720, "comp": comp, "placed": []}
    place_picture(round(H * k))
    return CW, CH


# the subject sits in the middle of the canvas in every composition ("low" / "top" pushed it to an
# edge under or over a big empty band); compositions differ in where the text goes
COMPOSITIONS = ("classic", "overlay")


def place_picture(vh):
    """Vertical: the picture in the middle of the canvas."""
    c = CANVAS
    c["vh"] = vh
    c["vy"] = round((c["H"] - vh) / 2)


def vertical_base(bbox, W, H):
    """Rect of the recording to show in the vertical canvas: the content area, at an aspect that
    keeps the picture between 30% and 62% of the canvas height. Updates CANVAS["vh"/"vy"]."""
    c = CANVAS
    if not bbox:
        bbox = (0, 0, W, H)
    bx, by, bw, bh = bbox
    tall = 0.62
    lo, hi = c["W"] / (tall * c["H"]), c["W"] / (0.30 * c["H"])
    ar = min(max(bw / max(1, bh), lo), hi)
    w, h = (bw, bw / ar) if bw / max(1, bh) >= ar else (bh * ar, bh)
    if w > W:
        w, h = W, W / ar
    if h > H:
        w, h = H * ar, H
    cx, cy = bx + bw / 2, by + bh / 2
    rect = clamp_rect((cx - w / 2, cy - h / 2, w, h), W, H)
    place_picture(round(c["W"] / ar))
    return rect, ar


def to_canvas(frame, backdrop=None):
    """Place the camera frame on the vertical canvas over a blurred, darkened copy of the scene."""
    c = CANVAS
    CW, CH = c["W"], c["H"]
    if STYLE.get("background") in ("black", "gradient"):
        bg = solid_backdrop(CW, CH, STYLE.get("background"), json.dumps(STYLE.get("background_colors"))).copy()
        bg.paste(frame.resize((CW, c["vh"]), Image.LANCZOS), (0, c["vy"]))
        return bg
    src = backdrop or frame
    # background: cover-scale at 1/8 size, blur, darken, upscale (cheap and smooth)
    sw, sh = max(1, CW // 8), max(1, CH // 8)
    cov = max(sw / src.width, sh / src.height)
    small = src.resize((max(sw, round(src.width * cov)), max(sh, round(src.height * cov))), Image.BILINEAR)
    l, t = (small.width - sw) // 2, (small.height - sh) // 2
    bg = small.crop((l, t, l + sw, t + sh)).filter(ImageFilter.GaussianBlur(3))
    bg = bg.point(lambda v: int(v * 0.45)).resize((CW, CH), Image.BILINEAR)
    bg.paste(frame.resize((CW, c["vh"]), Image.LANCZOS), (0, c["vy"]))
    return bg


@functools.lru_cache(maxsize=4)
def solid_backdrop(CW, CH, kind, colors_json):
    if kind != "gradient":
        return Image.new("RGBA", (CW, CH), (0, 0, 0, 255))
    (r0, g0, b0), (r1, g1, b1) = [c[:3] for c in json.loads(colors_json)]
    col = Image.new("RGBA", (1, 256))
    for i in range(256):
        k = i / 255
        col.putpixel((0, i), (round(r0 + (r1 - r0) * k), round(g0 + (g1 - g0) * k), round(b0 + (b1 - b0) * k), 255))
    return col.resize((CW, CH), Image.BILINEAR)


def canvas_background(raw, zoom, t):
    """Blurred backdrop from the frame *before* annotations (so boxes don't smear into it)."""
    return zoom.apply(raw, t, Image.BILINEAR)


def render_caption(it, W, H, u, layout, t0, t1):
    c = STYLE["caption"]
    weight = "heavy" if c["weight"] == "heavy" else True
    font = load_font(it.get("size", STYLE["caption_size"]) * u, bold=weight)
    lines = wrap(it["text"], font, W * 0.78)
    out_style = c["style"] == "outline"
    probe_im = outlined(lines, font, u) if out_style else plate(lines, font, int(28 * u), int(14 * u), int(6 * u), DARK_PLATE)
    w, h = probe_im.size
    if c["pop"] or c.get("entrance", "fade") != "fade" or c.get("idle", "none") != "none" \
            or it.get("entrance") or it.get("idle"):
        w, h = int(w * POP_SCALE), int(h * POP_SCALE)   # keep moving text clear of content too
    m = int(28 * u)
    lo = int(10 * u)  # closer to the bottom edge: buys distance from content that ends just above
    pos = {
        "bottom": (((W - w) / 2, H - h - m), 0.0),
        "bottom-low": (((W - w) / 2, H - h - lo), 0.2),
        "top": (((W - w) / 2, m), 0.6),
        "bottom-left": ((m, H - h - m), 1.0),
        "bottom-right": ((W - w - m, H - h - m), 1.0),
        "top-left": ((m, m), 1.4),
        "top-right": ((W - w - m, m), 1.4),
    }
    want = it.get("position")
    cands = [pos[want]] if want in pos else list(pos.values())
    what = f"caption '{it['text'][:16]}'"
    x, y, _, _ = layout.choose("screen", cands, (w, h), t0, t1, what, warn=bool(want))
    if not want and layout.last_score >= BUSY_LIMIT:
        # nowhere free: shrink the picture into the top of the screen for this caption's duration
        # and put the caption in the band that opens up below it (never covers content)
        room = (h + 2 * m) / H
        layout.zoom.add_inset(t0, t1, room)
        x, y = (W - w) / 2, H - h - m
        layout.placed[-1] = ("screen", (x, y, w, h), t0, t1)
        layout.bands.append((t0, t1))
        layout.notes.append(f"band: {what} at {t0:.1f}-{t1:.1f}s had no free spot; picture shrunk to open a "
                            f"caption band (check this time with inspect_video.py --at)")
        colors = plate_for(layout.band_luma(t0))
    else:
        colors = plate_for(layout.scene.luma("screen", (x, y, w, h), t0, t1))
    if out_style:
        im = probe_im   # outline text reads on any background; no plate color to pick
        im.info["rich"] = (lines, font, u)
    else:
        im = plate(lines, font, int(28 * u), int(14 * u), int(6 * u), colors)
    # center the real image inside the reserved (possibly pop-enlarged) slot
    return im, int(x + (w - im.width) / 2), int(y + (h - im.height) / 2)


def render_box(it, u, layout, t0, t1):
    pad, stroke = int(it.get("pad", 6) * u), max(2, int(4 * u))
    x0, y0 = it["x"] - pad, it["y"] - pad
    bw, bh = it["w"] + pad * 2, it["h"] + pad * 2
    label = it.get("label")
    tag = None
    if label:
        font = load_font(22 * u, bold=True)
        tag = plate([label], font, int(12 * u), int(6 * u), 0, (ACCENT, (255, 255, 255, 255), (255, 255, 255, 200)),
                    radius=max(2, int(4 * u)))
        g = int(6 * u)
        cands = [((x0, y0 - tag.height - g), 0.0), ((x0, y0 + bh + g), 0.3),
                 ((x0 + bw - tag.width, y0 - tag.height - g), 0.5), ((x0 + bw - tag.width, y0 + bh + g), 0.8),
                 ((x0 + bw + g, y0), 1.2), ((x0 - tag.width - g, y0), 1.2)]
        tx, ty, _, _ = layout.choose("video", cands, tag.size, t0, t1, f"box label '{label}'", warn=False)
        if layout.last_score >= BUSY_LIMIT:
            # no free spot right next to the box: drop the tag and say it with a callout instead,
            # which can sit further away in free space and points back with a stem
            layout.placed.pop()
            tag = None
    fx0 = min(x0, tx) if tag else x0
    fy0 = min(y0, ty) if tag else y0
    fx1 = max(x0 + bw, tx + tag.width) if tag else x0 + bw
    fy1 = max(y0 + bh, ty + tag.height) if tag else y0 + bh
    ox, oy = stroke - fx0, stroke - fy0
    rad = max(2, CORNER * u)

    def outline(d, s):
        r = ((x0 + ox) * s, (y0 + oy) * s, (x0 + bw + ox) * s, (y0 + bh + oy) * s)
        # dark halo under the red stroke keeps it visible on red/pink UIs too
        d.rounded_rectangle(r, radius=rad * s, outline=(0, 0, 0, 110), width=(stroke + 2) * s)
        d.rounded_rectangle(r, radius=rad * s, outline=ACCENT, width=stroke * s)
    im = antialiased((int(fx1 - fx0) + stroke * 2, int(fy1 - fy0) + stroke * 2), outline)
    if tag:
        im.alpha_composite(tag, (int(tx + ox), int(ty + oy)))
    fallback = None
    if label and tag is None:
        fallback = {"type": "callout", "text": label, "x": it["x"], "y": it["y"], "w": it["w"], "h": it["h"]}
    return im, int(fx0 - stroke), int(fy0 - stroke), fallback


def render_callout(it, u, layout, t0, t1):
    tx, ty = it["x"], it["y"]
    tw, th = it.get("w", 0), it.get("h", 0)
    font = load_font(it.get("size", STYLE["callout_size"]) * u, bold=True)
    lines = wrap(it["text"], font, layout.W * 0.35)
    probe_im = plate(lines, font, int(18 * u), int(10 * u), int(4 * u), DARK_PLATE, radius=max(2, int(CORNER * u)))
    bw, bh = probe_im.size
    g = int(40 * u)
    cx, cy = tx + tw / 2, ty + th / 2
    cands = [
        ((cx - bw / 2, ty - g - bh), 0.0),            # above
        ((cx - bw / 2, ty + th + g), 0.2),            # below
        ((tx + tw + g, cy - bh / 2), 0.4),            # right
        ((tx - g - bw, cy - bh / 2), 0.4),            # left
        ((tx + tw + g * 0.6, ty - g * 0.6 - bh), 0.6),  # above-right
        ((tx - g * 0.6 - bw, ty - g * 0.6 - bh), 0.6),  # above-left
        ((tx + tw + g * 0.6, ty + th + g * 0.6), 0.8),  # below-right
        ((tx - g * 0.6 - bw, ty + th + g * 0.6), 0.8),  # below-left
    ]
    # second ring further out, for crowded screens (terminals); slightly less preferred
    G = g * 3
    cands += [((x, y), p + 1.0) for (x, y), p in [
        ((cx - bw / 2, ty - G - bh), 0.0), ((cx - bw / 2, ty + th + G), 0.2),
        ((tx + tw + G, cy - bh / 2), 0.4), ((tx - G - bw, cy - bh / 2), 0.4),
        ((tx + tw + G, ty - bh), 0.6), ((tx + tw + G, ty + th), 0.6)]]
    def stem(bx, by):
        bcx, bcy = bx + bw / 2, by + bh / 2
        px, py = min(max(bcx, tx), tx + tw), min(max(bcy, ty), ty + th)
        sx, sy = min(max(px, bx), bx + bw), min(max(py, by), by + bh)
        return (sx, sy), (px, py)

    # a stem that runs across text is as bad as a bubble on top of it: add its crossing to the score
    cands = [((x, y), p + 0.15 * layout.scene.line_busy("video", *stem(x, y), t0, t1)) for (x, y), p in cands]
    bx, by, _, _ = layout.choose("video", cands, (bw, bh), t0, t1, f"callout '{it['text'][:16]}'")
    colors = plate_for(layout.scene.luma("video", (bx, by, bw, bh), t0, t1))
    bubble = plate(lines, font, int(18 * u), int(10 * u), int(4 * u), colors, radius=max(2, int(CORNER * u)), border=ACCENT)
    # stem from bubble edge to the nearest point of the target rect
    bcx, bcy = bx + bw / 2, by + bh / 2
    px, py = min(max(bcx, tx), tx + tw), min(max(bcy, ty), ty + th)
    sx, sy = min(max(px, bx), bx + bw), min(max(py, by), by + bh)
    r = max(4, int(6 * u))
    x0, y0 = min(bx, px - r), min(by, py - r)
    x1, y1 = max(bx + bw, px + r), max(by + bh, py + r)
    def stem_and_dot(d, s):
        d.line(((sx - x0) * s, (sy - y0) * s, (px - x0) * s, (py - y0) * s), fill=ACCENT, width=round(max(2, 3 * u) * s))
        d.ellipse(((px - x0 - r) * s, (py - y0 - r) * s, (px - x0 + r) * s, (py - y0 + r) * s), fill=ACCENT,
                  outline=(255, 255, 255, 255), width=round(max(1, 2 * u) * s))
    im = antialiased((int(x1 - x0) + 2, int(y1 - y0) + 2), stem_and_dot)
    im.alpha_composite(bubble, (int(bx - x0), int(by - y0)))
    return im, int(x0), int(y0)


def draw_click(frame, L, t, u):
    """Click effect, per STYLE["click"]. Default "ring": a thin white ring centered on the click that
    expands (fast, then easing out) and fades; no fill, so whatever was clicked stays visible, and a
    faint dark edge keeps it readable on white UIs. "disc": a translucent dot that presses and fades."""
    c = STYLE["click"]
    dt = t - L.start
    if c["style"] == "none" or not 0 <= dt < CLICK_DUR:
        return
    q = dt / CLICK_DUR
    color = tuple(c["color"][:3])
    a = (1 - q) * float(c["opacity"])               # fades linearly to nothing
    if c["style"] == "disc":
        r = float(c["radius_from"]) * u * (1 - 0.15 * smooth(min(1, q * 3)))   # slight press
    else:
        r = (float(c["radius_from"]) + (float(c["radius_to"]) - float(c["radius_from"])) * (1 - (1 - q) ** 3)) * u
    w = max(1.0, float(c["width"]) * u)
    x, y = L.click
    pad = int(r + 3 * w) + 2
    left, top = int(x) - pad, int(y) - pad
    cx, cy = x - left, y - top  # sub-pixel center inside the layer, so the effect moves smoothly

    def draw(d, s):
        R, W = r * s, w * s
        box = (cx * s - R, cy * s - R, cx * s + R, cy * s + R)
        if c["shadow"]:
            d.ellipse(((cx - r - w) * s, (cy - r - w) * s, (cx + r + w) * s, (cy + r + w) * s),
                      outline=(0, 0, 0, int(60 * a)), width=round(W + 2 * s))
        if c["style"] == "disc":
            d.ellipse(box, fill=color + (int(160 * a),))
        else:
            d.ellipse(box, outline=color + (int(255 * a),), width=round(W))
        if c["style"] == "burst":
            # 8 rays shooting outward from the ring
            for i in range(8):
                ang = math.pi / 8 + i * math.pi / 4
                r0, r1 = r * 1.25, r * (1.25 + 0.55 * (1 - q))
                d.line(((cx + math.cos(ang) * r0) * s, (cy + math.sin(ang) * r0) * s,
                        (cx + math.cos(ang) * r1) * s, (cy + math.sin(ang) * r1) * s),
                       fill=color + (int(255 * a),), width=round(W * 1.2))
    if c["style"] == "burst":
        pad = int(r * 1.9 + 3 * w) + 2
        left, top = int(x) - pad, int(y) - pad
        cx, cy = x - left, y - top
    frame.alpha_composite(antialiased((pad * 2, pad * 2), draw), (left, top))


POP_DUR = 0.28
SLIDE_DUR = 0.35


def entrance_dur(L):
    if L.entrance == "type":
        rich = L.im.info.get("rich")
        n = sum(len(as_line(l)) for l in rich[0]) if rich else 0
        return min(0.9, 0.055 * n) if n else 0
    return {"pop": POP_DUR, "slide": SLIDE_DUR}.get(L.entrance, 0)


def paste(frame, L, t):
    """Composite a layer, applying caption motion: an entrance (pop / slide / type) and an idle loop
    (float / pulse / wiggle). Transforms are quantized and cached so frames stay cheap."""
    im = L.image_at(t)
    x, y = L.x, L.y
    ent, idle = getattr(L, "entrance", "fade"), getattr(L, "idle", "none")
    if ent == "fade" and idle == "none":
        frame.alpha_composite(im, (max(0, x), max(0, y)), (max(0, -x), max(0, -y)))
        return
    dt = t - L.start
    u = getattr(L, "u", 1.0)
    ed = entrance_dur(L)
    k, ang, dx, dy = 1.0, 0.0, 0.0, 0.0
    if dt < ed:
        p = dt / ed
        if ent == "pop":
            k = 0.6 + 0.4 * back_out(p, 2.2)
        elif ent == "slide":
            dx = (1 - back_out(p, 1.2)) * frame.width * 0.45
        elif ent == "type" and L.im.info.get("rich"):
            lines, font, ru = L.im.info["rich"]
            n = sum(len(as_line(l)) for l in lines)
            upto = max(1, math.ceil(n * p))
            key = ("type", upto)
            if key not in L.cache:
                render = L.im.info.get("render")
                L.cache[key] = render(upto) if render else outlined(lines, font, ru, upto=upto)
            im = L.cache[key]
    else:
        q = dt - ed
        if idle == "float":
            dy = 5 * u * math.sin(2 * math.pi * 0.9 * q)
        elif idle == "pulse":
            k = 1 + 0.035 * math.sin(2 * math.pi * 1.4 * q)
        elif idle == "wiggle":
            ang = 2.5 * math.sin(2 * math.pi * 1.1 * q)
    k, ang = round(k * 80) / 80, round(ang * 4) / 4
    if abs(k - 1) > 1e-3 or ang:
        key = ("xf", k, ang, id(im))
        if key not in L.cache:
            out = im
            if abs(k - 1) > 1e-3:
                out = out.resize((max(1, round(out.width * k)), max(1, round(out.height * k))), Image.LANCZOS)
            if ang:
                out = out.rotate(ang, resample=Image.BICUBIC, expand=True)
            L.cache[key] = out
        big = L.cache[key]
        x, y = int(x + (im.width - big.width) / 2), int(y + (im.height - big.height) / 2)
        im = big
    x, y = int(x + dx), int(y + dy)
    # crop to the frame: sliding text starts partly off-screen
    cx0, cy0 = max(0, -x), max(0, -y)
    cx1, cy1 = min(im.width, frame.width - x), min(im.height, frame.height - y)
    if cx1 > cx0 and cy1 > cy0:
        frame.alpha_composite(im.crop((cx0, cy0, cx1, cy1)), (max(0, x), max(0, y)))


# ---------------------------------------------------------------- main

def compose(buf, t, W, H, u, zoom, video_layers, screen_layers):
    """Apply click effects, video-space annotations, camera (zoom/caption band) and screen-space
    annotations to one raw RGB frame. Returns raw RGB bytes."""
    va = [l for l in video_layers if l.start <= t < l.end]
    sa = [l for l in screen_layers if l.start <= t < l.end]
    fl = flash_at(t) if FLASHES else None
    if not (va or sa or CANVAS or fl or CREDIT or STYLE["progress"]["enabled"] or not zoom.identity(t)):
        return buf
    frame = Image.frombytes("RGB", (W, H), buf).convert("RGBA")
    raw = frame.copy() if CANVAS else None
    for l in va:
        if getattr(l, "click", None):
            draw_click(frame, l, t, u)
        else:
            paste(frame, l, t)
    frame = zoom.apply(frame, t, Image.LANCZOS)
    if CANVAS:
        frame = to_canvas(frame, canvas_background(raw, zoom, t))
    for l in sa:
        paste(frame, l, t)
    if STYLE["progress"]["enabled"]:
        draw_progress(frame, t)
    if CREDIT:
        draw_credit(frame)
    if fl:
        rgb, op = fl
        frame = Image.blend(frame, Image.new("RGBA", frame.size, rgb + (255,)), op)
    return frame.convert("RGB").tobytes()


def check(src, items, layers, warnings, notes, W, H, fps, dur, zoom, sheet_path):
    """--check: report placement problems and render a contact sheet of every annotation at its
    midpoint (plus zoom/band moments) without encoding the video."""
    u = H / 720
    video_layers = [l for l in layers if l.space == "video"]
    screen_layers = [l for l in layers if l.space == "screen"]
    times = set()
    for l in layers:
        times.add(round(l.start + min(0.15, (l.end - l.start) / 2), 2) if getattr(l, "click", None)
                  else round((l.start + l.end) / 2, 2))
    for z in items:
        if z.get("type") == "zoom":
            end = z.get("end") if z.get("end") is not None else dur
            times.add(round((z["start"] + end) / 2, 2))
    times = sorted(t for t in times if 0 <= t < dur)
    hints = []
    rd = STYLE.get("reading", {})
    for it in items:
        if it.get("type") in ("caption", "callout", "title") and it.get("text"):
            if it["type"] == "title" and CANVAS:
                continue   # vertical: the title stays on as a header, so it's readable later
            end = it.get("end") if it.get("end") is not None else dur
            shown = end - it["start"]
            need = float(rd.get("base", 1.0)) + float(rd.get("per_char", 0.1)) * len(strip_markup(it["text"], True))
            if shown < need:
                hints.append(f"{it['type']} '{it['text'][:16]}' is shown {shown:.1f}s; ~{need:.1f}s needed to read it")
    tiles = []
    font = load_font(18, bold=True)
    for t in times:
        out = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", src, "-frames:v", "1",
                              "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
        if len(out) < W * H * 3:
            continue
        OW, OH = (CANVAS["W"], CANVAS["H"]) if CANVAS else (W, H)
        frame = Image.frombytes("RGB", (OW, OH), compose(out[:W * H * 3], t, W, H, u, zoom, video_layers, screen_layers))
        frame = frame.resize((480, round(480 * frame.height / frame.width)))
        tile = Image.new("RGB", (frame.width, frame.height + 26), (0, 0, 0))
        tile.paste(frame, (0, 26))
        active = [l.item.get("text") or l.item.get("label") or l.item.get("type") for l in layers if l.start <= t < l.end]
        ImageDraw.Draw(tile).text((6, 2), f"{t:.2f}s  " + " / ".join(str(a)[:14] for a in active)[:60],
                                  font=font, fill=(255, 255, 255))
        tiles.append(tile)
    if tiles:
        cols = min(4, len(tiles))
        rows = (len(tiles) + cols - 1) // cols
        tw, th = tiles[0].size
        sheet = Image.new("RGB", (cols * tw + (cols - 1) * 4, rows * th + (rows - 1) * 4), (90, 90, 90))
        for n, im in enumerate(tiles):
            sheet.paste(im, ((n % cols) * (tw + 4), (n // cols) * (th + 4)))
        sheet.save(sheet_path)
    for h in hints:
        print(f"[annotate] hint: {h}", file=sys.stderr)
    extra = ""
    if CANVAS:
        extra = f", composition {CANVAS['comp']}, {len(CANVAS.get('over', []))} on the picture"
    print(f"[annotate] check: {len(layers)} annotations, {len(notes)} caption bands, {len(warnings)} warnings, "
          f"{len(hints)} hints{extra}; preview {sheet_path}")


def main():
    if "--default-style" in sys.argv[1:]:
        print(json.dumps(DEFAULT_STYLE, indent=2))
        return
    if sys.argv[1:2] == ["--resolve-style"]:
        # python3 annotate.py --resolve-style style.json [seed]  -> concrete style JSON on stdout
        spec = json.loads(Path(sys.argv[2]).read_text())
        seed = int(sys.argv[3]) if len(sys.argv) > 3 else None
        print(json.dumps(resolve_style(spec, seed), ensure_ascii=False, indent=2))
        return
    args = [a for a in sys.argv[1:] if a != "--check"]
    checking = "--check" in sys.argv[1:]
    if (checking and len(args) not in (2, 3)) or (not checking and len(args) != 3):
        sys.exit(__doc__)
    src, spec_path = args[0], args[1]
    W, H, fps, dur = probe(src)
    spec = json.loads(Path(spec_path).read_text())
    items = spec["items"] if isinstance(spec, dict) else spec
    load_style(spec.get("style") if isinstance(spec, dict) else None)
    global DUR, CREDIT
    DUR = dur
    a = STYLE["audio"]
    if a.get("enabled") and a.get("credit", True) and (a.get("narration", True)):
        import audio as _audio   # lazy: audio.py imports this module
        CREDIT = _audio.voice_label(a)
    OW, OH = setup_canvas(W, H)
    for w in setup_flashes(items):
        print(f"[annotate] warning: {w}", file=sys.stderr)
    if CANVAS:
        # vertical: frame the content area of the recording, then zoom within it
        scene = Scene(src, W, H, Zoom([], W, H))
        base, ar = vertical_base(scene.content_bbox(), W, H)
        zoom = Zoom(items, W, H, base=base, ar=ar)
    else:
        zoom = Zoom(items, W, H)
        scene = Scene(src, W, H, zoom)
    layers, warnings, notes = build_layers(items, W, H, dur, scene, zoom)
    for w in warnings:
        print(f"[annotate] warning: {w}", file=sys.stderr)
    for nt in notes:
        print(f"[annotate] {nt}", file=sys.stderr)
    if checking:
        sheet = args[2] if len(args) == 3 else str(Path(spec_path).with_suffix("")) + ".check.png"
        check(src, items, layers, warnings, notes, W, H, fps, dur, zoom, sheet)
        return
    dst = args[2]
    u = H / 720
    video_layers = [l for l in layers if l.space == "video"]
    screen_layers = [l for l in layers if l.space == "screen"]

    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                            "-s", f"{OW}x{OH}", "-r", f"{fps:.6f}", "-i", "-", "-i", src,
                            "-map", "0:v", "-map", "1:a?", "-c:a", "copy",
                            "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
                            "-movflags", "+faststart", dst], stdin=subprocess.PIPE)
    n = 0
    for buf in read_frames(src, W, H):
        enc.stdin.write(compose(buf, n / fps, W, H, u, zoom, video_layers, screen_layers))
        n += 1
    enc.stdin.close()
    if enc.wait() != 0:
        sys.exit("ffmpeg failed")
    print(f"[annotate] wrote {dst} ({n} frames, {len(layers)} annotations, "
          f"{sum(1 for i in items if i.get('type') == 'zoom')} zooms, {len(notes)} caption bands, "
          f"{len(warnings)} warnings)")


if __name__ == "__main__":
    main()
