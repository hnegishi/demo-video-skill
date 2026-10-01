"""Find a Japanese-capable font that ships with the OS, for Pillow.

    from jpfont import load_font
    font = load_font(32, bold=True)          # ImageFont.FreeTypeFont
    python3 jpfont.py                        # print what would be used

Search order (first hit wins; override with env DEMO_VIDEO_FONT=/path/to/font[.ttc][:index]):
  macOS   Hiragino Sans (ヒラギノ角ゴシック W6 / W3)  -> Hiragino Kaku Gothic ProN
  Windows Yu Gothic (YuGothB/YuGothM) -> Meiryo -> MS Gothic
  Linux   fc-list ':lang=ja' (prefers Noto Sans CJK JP / Noto Sans JP) -> common package paths

Never falls back to Pillow's default font: it has no Japanese glyphs and would silently
render tofu (□). Every candidate is also checked by actually rendering a kana.
"""
import functools
import glob
import os
import subprocess
import sys
import unicodedata

from PIL import Image, ImageDraw, ImageFont

PROBE = "あ漢"


class FontNotFound(RuntimeError):
    pass


def _nfc_listdir(d):
    try:
        return [(unicodedata.normalize("NFC", n), os.path.join(d, n)) for n in os.listdir(d)]
    except OSError:
        return []


def _mac_candidates(bold):
    weights = ["W6", "W7", "W5"] if bold else ["W3", "W4", "W2"]
    files = dict(_nfc_listdir("/System/Library/Fonts"))
    out = []
    for w in weights:
        p = files.get(f"ヒラギノ角ゴシック {w}.ttc")
        if p:
            out.append((p, 0))  # index 0 = "Hiragino Sans"
    for n, p in files.items():
        if n.startswith("ヒラギノ角ゴシック") and (p, 0) not in out:
            out.append((p, 0))
    return out


def _win_candidates(bold):
    d = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    names = (["YuGothB.ttc", "YuGothM.ttc", "meiryob.ttc", "meiryo.ttc"] if bold
             else ["YuGothM.ttc", "YuGothR.ttc", "meiryo.ttc", "meiryob.ttc"]) + ["msgothic.ttc"]
    return [(os.path.join(d, n), 0) for n in names if os.path.exists(os.path.join(d, n))]


def _linux_candidates(bold):
    out = []
    try:
        res = subprocess.run(["fc-list", ":lang=ja", "file", "family", "style"],
                             capture_output=True, text=True, timeout=10).stdout
        rows = []
        for line in res.splitlines():
            path, _, rest = line.partition(":")
            path = path.strip()
            if not path:
                continue
            score = 0
            if "Noto Sans CJK JP" in rest or "Noto Sans JP" in rest:
                score += 10
            if "Gothic" in rest or "Sans" in rest:
                score += 3
            if bold == ("Bold" in rest):
                score += 2
            if "Mono" in rest:
                score -= 1
            rows.append((score, path))
        out += [(p, 0) for _, p in sorted(rows, key=lambda r: -r[0])]
    except (OSError, subprocess.SubprocessError):
        pass
    pats = ["/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/**/NotoSansCJK*.tt[cf]", "/usr/share/fonts/**/NotoSansJP*.[ot]tf",
            "/usr/share/fonts/**/ipaexg.ttf", "/usr/share/fonts/**/ipag.ttf",
            "/usr/share/fonts/**/fonts-japanese-gothic.ttf"]
    for pat in pats:
        out += [(p, 0) for p in glob.glob(pat, recursive=True)]
    return out


def _renders_japanese(font):
    """True if the font draws real glyphs for kana/kanji (not .notdef boxes)."""
    def mask(s):
        im = Image.new("L", (120, 60), 0)
        ImageDraw.Draw(im).text((4, 4), s, font=font, fill=255)
        return im.tobytes()
    probe = mask(PROBE)
    return any(probe) and probe != mask("\U000F0000\U000F0001")


def _candidates(bold):
    env = os.environ.get("DEMO_VIDEO_FONT")
    if env:
        path, _, idx = env.rpartition(":") if env.rsplit(":", 1)[-1].isdigit() else (env, "", "0")
        return [(path, int(idx or 0))]
    if sys.platform == "darwin":
        return _mac_candidates(bold)
    if sys.platform.startswith("win"):
        return _win_candidates(bold)
    return _linux_candidates(bold)


@functools.lru_cache(maxsize=None)
def find_font(bold=True):
    """Return (path, index) of a usable Japanese font, or raise FontNotFound."""
    seen = set()
    for path, idx in _candidates(bold):
        if (path, idx) in seen or not os.path.exists(path):
            continue
        seen.add((path, idx))
        try:
            f = ImageFont.truetype(path, 24, index=idx)
        except OSError:
            continue
        if _renders_japanese(f):
            return path, idx
    hint = {
        "darwin": "macOS should ship Hiragino; check /System/Library/Fonts",
        "win32": "Windows should ship Yu Gothic / Meiryo in C:\\Windows\\Fonts",
    }.get(sys.platform, "install a Japanese font, e.g. `sudo apt install fonts-noto-cjk` (ask the user first)")
    raise FontNotFound(f"no Japanese-capable font found ({hint}); or set DEMO_VIDEO_FONT=/path/to/font")


@functools.lru_cache(maxsize=None)
def load_font(size, bold=True):
    path, idx = find_font(bold)
    return ImageFont.truetype(path, int(size), index=idx)


if __name__ == "__main__":
    for b in (True, False):
        p, i = find_font(b)
        print(f"{'bold   ' if b else 'regular'}: {p} (index {i}) -> {load_font(24, b).getname()}")
