#!/usr/bin/env python3
"""Turn `# @...` marker comments in a VHS .tape into annotations.json (and optionally burn them in).

VHS has no way to overlay text, so captions for terminal demos are written as comments in the
tape at the moment they should appear, then burned in afterwards with annotate.py (Pillow).

Markers (each is an ordinary tape comment, so VHS ignores it):
    # @caption テキスト          caption (placed automatically where it covers nothing); stays until
                                 the next caption/title or @end. "@caption:type" / ":pop" / ":slide" sets
                                 the entrance, "@caption:type:pulse" also the idle motion (float/pulse/wiggle).
                                 Vertical layout: ":above" / ":below" / ":over" (on a free spot of
                                 the picture) and ":left" / ":right" / ":center" place this caption.
                                 **text** is emphasized; emoji are drawn in color.
                                 the next caption/title or @end
    # @caption-top テキスト      same, pinned to the top
    # @title タイトル | サブ     full-screen title card; stays until the next caption/title or @end
    # @box X Y W H ラベル        red outline around a pixel rect (label optional); until next @box or @end
    # @callout X Y W H テキスト  speech bubble beside a pixel rect; until next @callout or @end
    # @zoom X Y W H              zoom the camera onto a pixel rect; until next @zoom or @zoom-out
    # @zoom-out                  zoom back out
    # @shake [px]                brief camera shake (default 8px)
    # @chapter ① ホーム          section label until the next chapter
    # @outro テキスト | サブ     end card (dims the frame) until the end
    # @stamp テキスト            big tilted pop-up text for a key moment (~1.2s)
                                 Any marker can carry its own look / place as JSON right after it:
                                 # @chapter {"shape": "box", "fill": [20, 20, 20], "position": "below"} ① 追加
    # @flash                     brief white flash (scene change); kept >= 0.5s apart
    # @say テキスト              narration (text-to-speech) starting here; needs "audio.enabled" in the style
    # @end                       hide the caption/title/box/callout that is showing

Pixel coordinates are in the recorded video. Find them with
    python3 inspect_video.py demo.plain.mp4 --at 4.5 --grid
which writes a full-size frame with a labelled 100px grid.

Usage:
    python3 tape_annotations.py demo.tape                       # print annotations.json
    python3 tape_annotations.py demo.tape --burn                # annotate <Output> -> <Output without .plain>
    python3 tape_annotations.py demo.tape --burn --out final.mp4
    python3 tape_annotations.py demo.tape --burn --check          # placement check + preview only

With --burn the tape's `Output` should be e.g. `demo.plain.mp4`; the annotated result goes to
`demo.mp4` (or --out) and the JSON to `demo.annotations.json`.

Timing model (measured against VHS 0.12): Type = (chars-1) x TypingSpeed, key presses ~0,
Sleep = exact, time inside Hide..Show is not recorded. Accurate to ~0.1s. `Wait` takes as long as
the command does, which can't be known from the tape, so markers after a Wait drift; prefer
Sleep before a marker, and always check with inspect_video.py --at.
"""
import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

KEYS = {"Enter", "Tab", "Backspace", "Delete", "Insert", "Space", "Up", "Down", "Left", "Right",
        "PageUp", "PageDown", "Escape", "Home", "End"}


def parse_duration(s, default_unit="s"):
    m = re.fullmatch(r"([\d.]+)\s*(ms|s|m)?", s.strip())
    if not m:
        raise ValueError(f"bad duration: {s}")
    v, unit = float(m.group(1)), m.group(2) or default_unit
    return v / 1000 if unit == "ms" else v * 60 if unit == "m" else v


def build(tape_text):
    t, speed, hidden = 0.0, 0.05, False
    items, warnings, output, after_wait = [], [], None, False
    slots = {}  # one open item per slot: text (caption/title), box, callout, zoom

    def close(slot, at):
        it = slots.pop(slot, None)
        if it is not None:
            it["end"] = round(at, 2)

    def rect_args(arg, ln, need_text):
        parts = arg.split(None, 4)
        try:
            x, y, w, h = (int(float(v)) for v in parts[:4])
        except ValueError:
            warnings.append(f"line {ln}: expected X Y W H, got '{arg}'")
            return None
        text = parts[4].strip() if len(parts) > 4 else ""
        if need_text and not text:
            warnings.append(f"line {ln}: missing text")
        return {"x": x, "y": y, "w": w, "h": h}, text

    for ln, raw in enumerate(tape_text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            m = re.match(r"#\s*@(caption-top|caption|title|box|callout|zoom-out|zoom|shake|flash|stamp|chapter|outro|say|end)((?::\w+)*)\s*(.*)", line)
            if not m:
                continue
            kind, mods, arg = m.group(1), [x for x in m.group(2).split(":") if x], m.group(3).strip()
            if hidden:
                warnings.append(f"line {ln}: marker inside Hide is ignored")
                continue
            if after_wait:
                warnings.append(f"line {ln}: marker after Wait — time is a lower bound, check the frame")
            now = round(t, 2)
            extra = {}
            if arg.startswith("{"):   # per-item look / place as JSON before the text
                try:
                    extra, n = json.JSONDecoder().raw_decode(arg)
                    arg = arg[n:].strip()
                except ValueError as e:
                    warnings.append(f"line {ln}: bad JSON after the marker ({e})")
            if kind == "end":
                for slot in ("text", "box", "callout"):
                    close(slot, t)
                continue
            if kind == "zoom-out":
                close("zoom", t)
                continue
            if kind == "chapter":
                items.append({"type": "chapter", "start": now, "text": arg, **extra})
                continue
            if kind == "outro":
                close("text", t)
                main_, _, sub_ = arg.partition("|")
                it = {"type": "outro", "start": now, "text": main_.strip(), **extra}
                if sub_.strip():
                    it["sub"] = sub_.strip()
                items.append(it)
                continue
            motion = {("entrance" if m in ("fade", "pop", "slide", "type") else "idle"): m for m in mods
                      if m in ("fade", "pop", "slide", "type", "none", "float", "pulse", "wiggle")}
            if kind == "stamp":
                items.append({"type": "stamp", "start": now, "text": arg, **motion, **extra})
                continue
            if kind == "flash":
                items.append({"type": "flash", "start": now})
                continue
            if kind == "say":
                items.append({"type": "narration", "start": now, "text": arg})
                continue
            if kind == "shake":
                amp = float(arg) if arg.replace(".", "", 1).isdigit() else 8.0
                items.append({"type": "shake", "start": now, "end": round(t + 0.3, 2), "amplitude": amp})
                continue
            if kind in ("box", "callout", "zoom"):
                parsed = rect_args(arg, ln, need_text=(kind == "callout"))
                if not parsed:
                    continue
                r, text = parsed
                close(kind, t)
                it = {"type": kind, "start": now, "end": None, **r}
                if kind == "box" and text:
                    it["label"] = text
                if kind == "callout":
                    it["text"] = text
                slots[kind] = it
                items.append(it)
                continue
            close("text", t)
            if kind == "title":
                title, _, sub = arg.partition("|")
                badge = None
                bm = re.match(r"\s*\[([^\]]+)\]\s*(.*)", title)   # "# @title [速報] 本文 | サブ"
                if bm:
                    badge, title = bm.group(1), bm.group(2)
                # "\n" in a marker = line break (a tape comment can't hold a real one)
                it = {"type": "title", "start": now, "end": None, "text": title.strip().replace("\\n", "\n")}
                if badge:
                    it["badge"] = badge
                it.update(motion)
                if sub.strip():
                    it["sub"] = sub.strip()
            else:
                it = {"type": "caption", "start": now, "end": None, "text": arg}
                if kind == "caption-top":
                    it["position"] = "top"
                for mod in mods:
                    if mod in ("fade", "pop", "slide", "type"):
                        it["entrance"] = mod
                    elif mod in ("none", "float", "pulse", "wiggle"):
                        it["idle"] = mod
                    elif mod in ("above", "below", "over"):
                        it["position"] = mod
                    elif mod in ("left", "right", "center"):
                        it["align"] = mod
            it.update(extra)
            slots["text"] = it
            items.append(it)
            continue

        try:
            words = shlex.split(line, posix=True)
        except ValueError:
            words = line.split()
        cmd = words[0]
        base, _, override = cmd.partition("@")
        if base == "Output" and len(words) > 1 and output is None and not words[1].endswith(".gif"):
            output = words[1]
        elif base == "Set" and len(words) > 2 and words[1] == "TypingSpeed":
            speed = parse_duration(words[2], "ms")
        elif base == "Hide":
            hidden = True
        elif base == "Show":
            hidden = False
        elif hidden:
            continue
        elif base == "Sleep":
            t += parse_duration(words[1])
        elif base == "Type":
            sp = parse_duration(override, "ms") if override else speed
            text = " ".join(words[1:])
            t += max(0, len(text) - 1) * sp
        elif base in KEYS or base.startswith("Ctrl+") or base.startswith("Alt+") or base.startswith("Shift+"):
            n = int(words[1]) if len(words) > 1 and words[1].isdigit() else 1
            sp = parse_duration(override, "ms") if override else speed
            t += max(0, n - 1) * sp
        elif base == "Wait":
            after_wait = True
            warnings.append(f"line {ln}: Wait has unknown duration; later markers may be early")
    return {"items": items}, warnings, output, round(t, 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tape")
    ap.add_argument("--burn", action="store_true", help="run annotate.py on the tape's Output video")
    ap.add_argument("--video", help="input video (default: the tape's first non-GIF Output)")
    ap.add_argument("--out", help="annotated output (default: Output with .plain removed)")
    ap.add_argument("--style", help="style JSON (look & feel overrides) to store in annotations.json")
    ap.add_argument("--seed", type=int, help="seed for styles with themes/variations (dopagaki)")
    ap.add_argument("--check", action="store_true",
                    help="with --burn: only check placement and write a preview sheet (no encoding)")
    a = ap.parse_args()
    tape = Path(a.tape)
    spec, warnings, output, total = build(tape.read_text())
    for w in warnings:
        print(f"[tape_annotations] warning: {w}", file=sys.stderr)
    if not a.burn:
        print(json.dumps(spec, ensure_ascii=False, indent=2))
        print(f"[tape_annotations] estimated length {total}s", file=sys.stderr)
        return
    video = Path(a.video or output or sys.exit("no Output in tape; pass --video"))
    # VHS resolves Output against the cwd it was run from; fall back to the tape's directory
    if not video.is_absolute() and not video.exists():
        video = tape.parent / video
    out = Path(a.out) if a.out else video.with_name(video.name.replace(".plain", ""))
    if out == video:
        sys.exit("output would overwrite input; name the tape Output like demo.plain.mp4 or pass --out")
    ann = out.with_suffix(".annotations.json")
    if a.style:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import annotate
        style = annotate.resolve_style(json.loads(Path(a.style).read_text()), a.seed)
        if style.get("theme"):
            print(f"[tape_annotations] theme: {style['theme']}, composition: {style.get('composition', 'classic')} "
                  f"(seed {style['seed']})", file=sys.stderr)
        spec = {"style": style, **spec}
    ann.write_text(json.dumps(spec, ensure_ascii=False, indent=2))
    cmd = [sys.executable, str(Path(__file__).with_name("annotate.py")), str(video), str(ann)]
    audio_on = bool(spec.get("style", {}).get("audio", {}).get("enabled"))
    if a.check or not audio_on:
        subprocess.run(cmd + (["--check"] if a.check else [str(out)]), check=True)
        return
    # picture first, then sound effects / music / narration (audio.py warns if narration lines overlap)
    silent = out.with_name(out.stem + ".silent.mp4")
    subprocess.run(cmd + [str(silent)], check=True)
    subprocess.run([sys.executable, str(Path(__file__).with_name("audio.py")), str(silent), str(ann), str(out)],
                   check=True)


if __name__ == "__main__":
    main()
