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
import json
import os
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageStat

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jpfont import load_font  # noqa: E402

# Look & feel. These are only defaults: override any key with a style JSON in
# $DEMO_VIDEO_STYLE (project/personal taste) and/or "style" in annotations.json (one video).
# Sizes are pixels at 720p and scale with the video height.
DEFAULT_STYLE = {
    "accent": [255, 72, 72],          # box outline, box label, callout border/stem
    "corner_radius": 6,               # plates, labels, boxes (small: reads as a label, not a pill)
    "fade": 0.2,                      # fade in/out of captions, boxes, callouts (s)
    "caption_size": 30,
    "callout_size": 24,
    "title_size": 60,
    "title_dim": 0.85,                # darkness of the title card backdrop (0..1)
    "plate_dark": {"bg": [18, 22, 34, 232], "fg": [255, 255, 255]},   # used over bright footage
    "plate_light": {"bg": [250, 250, 252, 240], "fg": [20, 22, 30]},  # used over dark footage
    "click": {
        "style": "ring",              # "ring" (expanding ring) | "disc" (pressed dot) | "none"
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
    merge_style(STYLE, spec_style)
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


def smooth(p):
    p = min(1.0, max(0.0, p))
    return p * p * (3 - 2 * p)


class Zoom:
    """Piecewise camera: rect(t) in source coordinates, eased between keyframes."""

    def __init__(self, items, W, H):
        self.W, self.H = W, H
        full = (0.0, 0.0, float(W), float(H))
        zs = sorted((i for i in items if i.get("type") == "zoom"), key=lambda i: i["start"])
        keys = [(0.0, full)]
        for n, z in enumerate(zs):
            ease = float(z.get("ease", 0.6))
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
        self.active = len(zs) > 0

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
        ar = W / H
        room = (110 * H / 720) / H if z.get("caption_room", True) else 0.0  # share of screen height
        h = max(h / (1 - room), w / ar)
        w = h * ar
        max_scale = float(z.get("max_scale", 3.0))
        w, h = max(w, W / max_scale), max(h, H / max_scale)
        w, h = min(w, W), min(h, H)
        # put the target's center in the middle of the area above the caption band
        top = cy - h * (1 - room) / 2
        return clamp_rect((cx - w / 2, top, w, h), W, H)

    @staticmethod
    def at_keys(keys, t):
        if t <= keys[0][0]:
            return keys[0][1]
        for (t0, r0), (t1, r1) in zip(keys, keys[1:]):
            if t0 <= t <= t1:
                p = smooth((t - t0) / (t1 - t0)) if t1 > t0 else 1.0
                return tuple(a + (b - a) * p for a, b in zip(r0, r1))
        return keys[-1][1]

    def rect(self, t):
        """Source rect the camera shows at time t (zoom only; caption bands are applied after)."""
        return self.at_keys(self.keys, t)

    def is_full(self, r):
        x, y, w, h = r
        return abs(x) < 0.5 and abs(y) < 0.5 and abs(w - self.W) < 0.5 and abs(h - self.H) < 0.5

    def identity(self, t):
        return self.is_full(self.rect(t)) and (not self.insets or self.inset_at(t) <= 0)

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
            img = img.resize(img.size, resample, box=(r[0] * k, r[1] * k, (r[0] + r[2]) * k, (r[1] + r[3]) * k))
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

def wrap(text, font, max_w):
    """Wrap by measured width, char by char (Japanese has no spaces to break on)."""
    lines = []
    for para in str(text).split("\n"):
        line = ""
        for ch in para:
            if line and font.getlength(line + ch) > max_w:
                if ch in "、。，．）」』！？!?)":  # keep closing punctuation on the line (kinsoku-lite)
                    line += ch
                    continue
                lines.append(line)
                line = ""
            line += ch
        lines.append(line)
    return lines


def text_block(lines, font, gap):
    asc, desc = font.getmetrics()
    lh = asc + desc
    w = max((font.getlength(l) for l in lines), default=0)
    return int(w), lh * len(lines) + gap * (len(lines) - 1), lh


def draw_lines(d, x, y, lines, font, lh, gap, fill, center_w=None):
    for i, l in enumerate(lines):
        lx = x + (center_w - font.getlength(l)) / 2 if center_w else x
        d.text((lx, y + i * (lh + gap)), l, font=font, fill=fill)


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
        if t == "zoom":
            continue
        start = float(it.get("start", 0))
        end = start + CLICK_DUR if t == "click" else (float(it["end"]) if it.get("end") is not None else dur)
        if end <= start:
            continue
        norm.append((it, t, start, end))
        if t in ("box", "callout") and "w" in it:
            layout.targets.append(("video", (it["x"], it["y"], it["w"], it["h"]), start, end))
    # boxes first (they define targets), then bubbles/labels, captions last (most flexible)
    order = {"title": 0, "click": 1, "box": 2, "callout": 3, "caption": 4}
    for it, t, start, end in sorted(norm, key=lambda n: order.get(n[1], 9)):
        space = "screen" if t in ("caption", "title") else "video"
        L = Layer(it, start, end, space)
        if t == "title":
            L.im, L.x, L.y = render_title(it, W, H, u), 0, 0
        elif t == "click":
            L.fade = 0
            L.click = (it["x"], it["y"])
        elif t == "caption":
            L.im, L.x, L.y = render_caption(it, W, H, u, layout, start, end)
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
    return layers, layout.warnings, layout.notes


def render_title(it, W, H, u):
    big = load_font(it.get("size", STYLE["title_size"]) * u, bold=True)
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


def render_caption(it, W, H, u, layout, t0, t1):
    font = load_font(it.get("size", STYLE["caption_size"]) * u, bold=True)
    lines = wrap(it["text"], font, W * 0.78)
    probe_im = plate(lines, font, int(28 * u), int(14 * u), int(6 * u), DARK_PLATE)
    w, h = probe_im.size
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
    return plate(lines, font, int(28 * u), int(14 * u), int(6 * u), colors), int(x), int(y)


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
    frame.alpha_composite(antialiased((pad * 2, pad * 2), draw), (left, top))


def paste(frame, L, t):
    im = L.image_at(t)
    frame.alpha_composite(im, (max(0, L.x), max(0, L.y)), (max(0, -L.x), max(0, -L.y)))


# ---------------------------------------------------------------- main

def compose(buf, t, W, H, u, zoom, video_layers, screen_layers):
    """Apply click effects, video-space annotations, camera (zoom/caption band) and screen-space
    annotations to one raw RGB frame. Returns raw RGB bytes."""
    va = [l for l in video_layers if l.start <= t < l.end]
    sa = [l for l in screen_layers if l.start <= t < l.end]
    if not (va or sa or not zoom.identity(t)):
        return buf
    frame = Image.frombytes("RGB", (W, H), buf).convert("RGBA")
    for l in va:
        if getattr(l, "click", None):
            draw_click(frame, l, t, u)
        else:
            paste(frame, l, t)
    frame = zoom.apply(frame, t, Image.LANCZOS)
    for l in sa:
        paste(frame, l, t)
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
    for it in items:
        if it.get("type") in ("caption", "callout", "title") and it.get("text"):
            end = it.get("end") if it.get("end") is not None else dur
            shown = end - it["start"]
            need = 1.0 + 0.1 * len(it["text"])
            if shown < need:
                hints.append(f"{it['type']} '{it['text'][:16]}' is shown {shown:.1f}s; ~{need:.1f}s needed to read it")
    tiles = []
    font = load_font(18, bold=True)
    for t in times:
        out = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", src, "-frames:v", "1",
                              "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
        if len(out) < W * H * 3:
            continue
        frame = Image.frombytes("RGB", (W, H), compose(out[:W * H * 3], t, W, H, u, zoom, video_layers, screen_layers))
        frame = frame.resize((480, round(480 * H / W)))
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
    print(f"[annotate] check: {len(layers)} annotations, {len(notes)} caption bands, {len(warnings)} warnings, "
          f"{len(hints)} hints; preview {sheet_path}")


def main():
    if "--default-style" in sys.argv[1:]:
        print(json.dumps(DEFAULT_STYLE, indent=2))
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
                            "-s", f"{W}x{H}", "-r", f"{fps:.6f}", "-i", "-", "-i", src,
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
