#!/usr/bin/env python3
"""Print a video's duration/resolution/codec/brightness and write a timestamped contact sheet,
so the result can be checked with a single image read.

Usage:
    python3 inspect_video.py <video> [--frames 8] [--at 1.5,4,9.2]

Writes <video filename>.contact.png next to the video (e.g. demo.mp4.contact.png, demo.gif.contact.png,
so checking an MP4 and its GIF never overwrite each other). --at samples specific times instead of
evenly spaced ones — useful for checking that a caption appears exactly where intended — and writes
<video filename>.at.contact.png so the evenly spaced sheet is kept. --out-dir puts sheets elsewhere.
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jpfont import load_font  # noqa: E402


def ffprobe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "format=duration,size:stream=codec_name,width,height,avg_frame_rate",
                          "-select_streams", "v:0", "-of", "json", str(path)],
                         capture_output=True, text=True)
    if out.returncode:
        sys.exit(f"ERROR: ffprobe failed on {path}: {out.stderr.strip()}")
    j = json.loads(out.stdout)
    s, f = j["streams"][0], j["format"]
    return {"duration": float(f.get("duration", 0)), "size": int(f.get("size", 0)),
            "codec": s["codec_name"], "width": s["width"], "height": s["height"], "fps": s["avg_frame_rate"]}


def grab(path, t, dest, width=480):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
                    "-vf", f"scale={width}:-2", str(dest)], check=False)
    return dest if dest.exists() else None


def mean_luma(path):
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf",
                          "fps=1,scale=64:-2,signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-",
                          "-f", "null", "-"], capture_output=True, text=True).stdout
    vals = [float(l.split("=")[1]) for l in out.splitlines() if "YAVG" in l]
    return sum(vals) / len(vals) if vals else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--frames", type=int, default=8)
    ap.add_argument("--at", help="comma-separated times in seconds")
    ap.add_argument("--out-dir", help="where to write the contact sheet (default: next to the video)")
    ap.add_argument("--grid", action="store_true",
                    help="also write a full-size frame per --at time with a labelled 100px grid, "
                         "for reading pixel coordinates (zoom/box/callout in .tape markers)")
    a = ap.parse_args()
    v = Path(a.video)
    if not v.exists() or v.stat().st_size == 0:
        sys.exit(f"ERROR: {v} is missing or empty")
    info = ffprobe(v)
    dur = info["duration"]
    if a.at:
        times = [float(x) for x in a.at.split(",")]
        late = [t for t in times if t > dur]
        if late:
            print(f"warning: {late} past the end ({dur:.2f}s); showing the last frame instead", file=sys.stderr)
    else:
        n = max(1, a.frames)
        times = [dur * (i + 0.5) / n for i in range(n)]  # mid-points: avoids black first/last frames

    font = load_font(18, bold=True)
    tiles = []
    with tempfile.TemporaryDirectory() as td:
        for i, t in enumerate(times):
            p = grab(v, min(t, max(0, dur - 0.05)), Path(td) / f"{i}.png")
            if not p:
                continue
            frame = Image.open(p).convert("RGB")
            # timestamp goes in a strip above the frame so it never hides content in the corner
            im = Image.new("RGB", (frame.width, frame.height + 26), (0, 0, 0))
            im.paste(frame, (0, 26))
            ImageDraw.Draw(im).text((6, 2), f"{t:.2f}s", font=font, fill=(255, 255, 255))
            tiles.append(im)
    if not tiles:
        sys.exit("ERROR: could not extract any frame")
    cols = min(4, len(tiles))
    rows = (len(tiles) + cols - 1) // cols
    tw, th = tiles[0].size
    pad = 4
    sheet = Image.new("RGB", (cols * tw + (cols - 1) * pad, rows * th + (rows - 1) * pad), (90, 90, 90))
    for i, im in enumerate(tiles):
        sheet.paste(im, ((i % cols) * (tw + pad), (i // cols) * (th + pad)))
    out_dir = Path(a.out_dir) if a.out_dir else v.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / (v.name + (".at" if a.at else "") + ".contact.png")
    sheet.save(out)

    if a.grid:
        small = load_font(14, bold=True)
        with tempfile.TemporaryDirectory() as td:
            for t in times:
                p = grab(v, min(t, max(0, dur - 0.05)), Path(td) / "g.png", width=info["width"])
                if not p:
                    continue
                im = Image.open(p).convert("RGB")
                d = ImageDraw.Draw(im, "RGBA")
                for gx in range(0, im.width, 100):
                    d.line((gx, 0, gx, im.height), fill=(255, 0, 255, 110), width=1)
                    d.text((gx + 2, 2), str(gx), font=small, fill=(255, 0, 255, 255))
                for gy in range(0, im.height, 100):
                    d.line((0, gy, im.width, gy), fill=(255, 0, 255, 110), width=1)
                    d.text((2, gy + 2), str(gy), font=small, fill=(255, 0, 255, 255))
                gp = out_dir / f"{v.name}.grid-{t:.2f}.png"
                im.save(gp)
                print(f"grid:       {gp}")

    luma = mean_luma(v)
    print(f"file:       {v}")
    print(f"duration:   {dur:.2f}s")
    print(f"resolution: {info['width']}x{info['height']}")
    print(f"codec:      {info['codec']}  fps: {info['fps']}  size: {info['size'] / 1e6:.2f}MB")
    print(f"mean luma:  {luma:.1f} (0=black, 255=white; <5 or >250 suggests a blank recording)")
    print(f"contact:    {out}")


if __name__ == "__main__":
    main()
