#!/usr/bin/env python3
"""Add sound to an annotated demo video: sound effects, background music and narration (TTS).

Usage:
    python3 audio.py in.mp4 annotations.json out.mp4          # mix and mux an audio track
    python3 audio.py --tts "テキスト" out.wav [--voice Kyoko]  # synthesize one line, print its duration (s)

What gets sound (per STYLE["audio"], see annotate.py --default-style):
  sfx        click -> "tick", zoom-in -> "whoosh", caption appears -> "pop", shake / title -> "impact".
             All synthesized with ffmpeg (no sound files are bundled).
  bgm        "synth" = generated here (with "bgm_genre": edm/trap/lofi/chiptune/funk/random via music.py),
             a path to your own audio file (looped and trimmed), or a folder of tracks (one picked per
             video from the seed). Leveled, then ducked automatically under narration.
  narration  "narration" items ({"type": "narration", "start": s, "text": "..."}) are spoken with
             macOS `say` (default voice Kyoko, rate 270). Another `say` voice, or VOICEVOX
             ("engine": "voicevox", engine at http://127.0.0.1:50021; credit "VOICEVOX:<name>" is drawn
             on the video), only when the user asks for it. With "narrate_captions": true and no narration items,
             caption texts are read aloud instead.

Text-to-speech: macOS `say` (Japanese voices: Kyoko, etc.). Elsewhere set DEMO_VIDEO_TTS to a command
template, e.g. 'my-tts --text {text} --out {out}' (must write a WAV/AIFF file to {out}); without one,
narration is skipped with a warning.
"""
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import annotate  # noqa: E402  (style defaults + loader)

CACHE = Path.home() / ".cache" / "demo-video-skill" / "audio"
SR = 44100

# name -> (aevalsrc expression, duration, extra filter)
SFX = {
    "tick": ("0.55*sin(2*PI*2200*t)*exp(-90*t)+0.25*(random(0)*2-1)*exp(-160*t)", 0.08, ""),
    "pop": ("0.6*sin(2*PI*(700+900*exp(-18*t))*t)*exp(-16*t)", 0.18, ""),
    "whoosh": ("0.5*(random(0)*2-1)*sin(PI*t/0.32)", 0.32, ",highpass=f=900,lowpass=f=5000"),
    "impact": ("0.95*sin(2*PI*(48+40*exp(-20*t))*t)*exp(-5*t)+0.35*(random(0)*2-1)*exp(-35*t)", 0.7,
               ",lowpass=f=2500"),
}


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"{cmd[0]} failed: {r.stderr.strip()[-500:]}")
    return r.stdout


def duration(path):
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                      "default=nw=1:nk=1", str(path)]).strip() or 0)


def has_audio(path):
    return bool(run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                     "-of", "csv=p=0", str(path)]).strip())


def sfx_file(name):
    CACHE.mkdir(parents=True, exist_ok=True)
    expr, dur, extra = SFX[name]
    out = CACHE / f"sfx-{name}-{hashlib.md5((expr + extra).encode()).hexdigest()[:8]}.wav"
    if not out.exists():
        run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"aevalsrc='{expr}':s={SR}:d={dur}",
             "-af", f"afade=t=out:st={dur * 0.7}:d={dur * 0.3}{extra}", str(out)])
    return out


def synth_bgm(seconds, bpm=124, energy="light", key=0):
    """Generated loop (not sampled). "light": kick, offbeat hats, soft pad.
    "hype": adds a pumping offbeat bass, 16th hats and a clap on 2 and 4 for a short-video feel."""
    CACHE.mkdir(parents=True, exist_ok=True)
    out = CACHE / f"bgm-synth-{energy}-{bpm}-k{key}-{int(seconds) + 1}.wav"
    tr = 2 ** (int(key) / 12)   # transpose
    if out.exists():
        return out
    b = 60.0 / bpm
    kick = f"0.9*sin(2*PI*(45+90*exp(-30*mod(t,{b})))*mod(t,{b}))*exp(-9*mod(t,{b}))"
    hat = f"0.10*(random(0)*2-1)*exp(-70*mod(t+{b / 2},{b}))"
    bar = 4 * b
    # A minor <-> F major, two bars each
    a, c, e, f = 220 * tr, 261.6 * tr, 329.6 * tr, 174.6 * tr
    pad = (f"0.05*if(lt(mod(t,{4 * bar}),{2 * bar}),"
           f"sin(2*PI*{a}*t)+sin(2*PI*{c}*t)+sin(2*PI*{e}*t),"
           f"sin(2*PI*{f}*t)+sin(2*PI*{a}*t)+sin(2*PI*{c}*t))")
    expr = f"{kick}+{hat}+{pad}"
    if energy == "hype":
        root = f"if(lt(mod(t,{4 * bar}),{2 * bar}),{55 * tr},{43.65 * tr})"
        # offbeat bass that ducks under the kick ("pumping")
        bass = f"0.32*sin(2*PI*{root}*t)*(1-exp(-12*mod(t+{b / 2},{b})))*min(1,mod(t,{b})*8)"
        hat16 = f"0.06*(random(0)*2-1)*exp(-90*mod(t,{b / 4}))"
        clap = f"0.22*(random(0)*2-1)*exp(-28*mod(t+{b},{2 * b}))"
        expr += f"+{bass}+{hat16}+{clap}"
    run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", f"aevalsrc='{expr}':s={SR}:d={int(seconds) + 1}",  # quoted: the expression has commas
         "-af", "highpass=f=30,alimiter=limit=0.9", str(out)])
    return out


VOICEVOX_URL = os.environ.get("VOICEVOX_URL", "http://127.0.0.1:50021")


def voicevox_up():
    import urllib.request
    try:
        with urllib.request.urlopen(VOICEVOX_URL + "/version", timeout=1.5) as r:
            return r.status == 200
    except OSError:
        return False


def voicevox_style_id(name):
    """Style id for a speaker name like "ずんだもん" or "ずんだもん:あまあま" (default style: ノーマル)."""
    import urllib.request
    who, _, style = name.partition(":")
    with urllib.request.urlopen(VOICEVOX_URL + "/speakers", timeout=5) as r:
        speakers = json.load(r)
    for sp in speakers:
        if sp["name"] == who:
            styles = sp["styles"]
            for st in styles:
                if st["name"] == (style or "ノーマル"):
                    return st["id"]
            return styles[0]["id"]
    raise RuntimeError(f"VOICEVOX speaker '{who}' not found")


def tts_engine(audio):
    """Which TTS will be used: "voicevox" (engine running and allowed), "custom" ($DEMO_VIDEO_TTS),
    "say" (macOS) or None."""
    eng = (audio or {}).get("engine", "say")
    if eng in ("auto", "voicevox") and voicevox_up():
        return "voicevox"
    if os.environ.get("DEMO_VIDEO_TTS"):
        return "custom"
    if shutil.which("say"):
        return "say"
    return None


def tts_available(audio=None):
    return tts_engine(audio) is not None


def voice_label(audio):
    """Speaker name for credits (VOICEVOX requires 'VOICEVOX:<name>' in the video or its description)."""
    eng = tts_engine(audio)
    if eng == "voicevox":
        return "VOICEVOX:" + str(audio.get("voicevox_speaker", "ずんだもん")).split(":")[0]
    return None


def tts(text, out, audio=None, rate=None):
    """Synthesize one line to a WAV file and return its duration (0 if no TTS is available).

    audio: the style's "audio" dict (engine, voice, rate, voicevox_speaker, voice_pitch, voice_tempo).
    For backward compatibility a plain voice name may be passed instead (with rate)."""
    if isinstance(audio, str) or audio is None:
        audio = {"voice": audio or "Kyoko", "rate": rate or 270}
    out = Path(out)
    text = annotate.strip_markup(text, drop_emoji=True).strip()   # don't read "**" or emoji names aloud
    if not text:
        return 0.0
    eng = tts_engine(audio)
    with tempfile.TemporaryDirectory() as td:
        raw = Path(td) / "tts.wav"
        if eng == "voicevox":
            import urllib.parse
            import urllib.request
            sid = voicevox_style_id(str(audio.get("voicevox_speaker", "ずんだもん")))
            q = urllib.request.Request(f"{VOICEVOX_URL}/audio_query?speaker={sid}&text={urllib.parse.quote(text)}",
                                       method="POST")
            with urllib.request.urlopen(q, timeout=30) as r:
                query = json.load(r)
            query["speedScale"] = float(audio.get("voicevox_speed", 1.25))
            query["intonationScale"] = float(audio.get("voicevox_intonation", 1.3))
            query["pitchScale"] = float(audio.get("voicevox_pitch", 0.0))
            syn = urllib.request.Request(f"{VOICEVOX_URL}/synthesis?speaker={sid}", data=json.dumps(query).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(syn, timeout=60) as r:
                raw.write_bytes(r.read())
            fx = ""                                   # VOICEVOX voices already sound like short-video TTS
        elif eng == "custom":
            # split the template first, then substitute per argument: no shell, no injection via {text}
            tmpl = os.environ["DEMO_VIDEO_TTS"]
            argv = [tok.replace("{text}", text).replace("{out}", str(raw)) for tok in shlex.split(tmpl)]
            subprocess.run(argv, check=True)
            fx = ""
        elif eng == "say":
            # pass the text through a file: a line starting with "-" (e.g. "-c で列指定") would
            # otherwise be parsed as a say option
            txt = Path(td) / "line.txt"
            txt.write_text(text, encoding="utf-8")
            raw = Path(td) / "tts.aiff"
            run(["say", "-v", str(audio.get("voice", "Kyoko")), "-r", str(int(audio.get("rate", 200))),
                 "-o", str(raw), "-f", str(txt)])
            # short-video voice: pitch up, a bit faster, presence boost, compressed (the plain macOS voice
            # reads like an announcement)
            p = float(audio.get("voice_pitch", 1.0))
            tmp = float(audio.get("voice_tempo", 1.0))
            fx = ""
            if p != 1.0 or tmp != 1.0:
                fx = f"asetrate={SR * p:.0f},aresample={SR},atempo={max(0.5, min(2.0, tmp / p)):.4f},"
            if p != 1.0:
                fx += ("highpass=f=140,equalizer=f=3200:t=q:w=1.2:g=5,"
                       "acompressor=threshold=-20dB:ratio=4:attack=4:release=60:makeup=4,")
        else:
            return 0.0
        run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-af", f"aresample={SR},{fx}anull", "-ar", str(SR),
             "-ac", "1", str(out)])
    return duration(out)


def tts_cache_key(text, audio):
    keys = ("engine", "voice", "rate", "voice_pitch", "voice_tempo", "voicevox_speaker", "voicevox_speed",
            "voicevox_intonation", "voicevox_pitch")
    sig = "|".join(str(audio.get(k)) for k in keys) + "|" + str(tts_engine(audio))
    return hashlib.md5(f"{sig}|{text}".encode()).hexdigest()[:12]


def plan(items, dur, audio):
    """List of (start_seconds, wav_path, kind) to place, plus warnings."""
    events, warns = [], []
    if audio.get("sfx", True):
        for it in items:
            t, s = it.get("type"), float(it.get("start", 0))
            if t == "click":
                events.append((s, sfx_file("tick"), "sfx"))
            elif t == "zoom":
                events.append((s, sfx_file("whoosh"), "sfx"))
            elif t == "caption":
                events.append((s, sfx_file("pop"), "sfx"))
            elif t in ("shake", "title", "stamp"):
                events.append((s, sfx_file("impact"), "sfx"))
            elif t == "chapter":
                events.append((s, sfx_file("whoosh"), "sfx"))
            elif t == "outro":
                events.append((s, sfx_file("impact"), "sfx"))
            elif t == "flash":
                events.append((s, sfx_file("whoosh"), "sfx"))
    lines = [it for it in items if it.get("type") == "narration" and it.get("text")]
    if not lines and audio.get("narrate_captions"):
        lines = [{"start": float(it.get("start", 0)) + 0.1, "text": it["text"], "end": it.get("end")}
                 for it in items if it.get("type") == "caption" and it.get("text")]
    if lines and audio.get("narration", True):
        if not tts_available(audio):
            warns.append("no text-to-speech available (VOICEVOX, macOS `say` or $DEMO_VIDEO_TTS); narration skipped")
        else:
            lines.sort(key=lambda l: float(l["start"]))
            for n, l in enumerate(lines):
                wav = l.get("file")
                if not wav or not Path(wav).exists():
                    wav = CACHE / f"tts-{tts_cache_key(l['text'], audio)}.wav"
                    CACHE.mkdir(parents=True, exist_ok=True)
                    if not wav.exists():
                        tts(l["text"], wav, audio)
                d = duration(wav)
                s = float(l["start"])
                nxt = float(lines[n + 1]["start"]) if n + 1 < len(lines) else dur
                if s + d > nxt + 0.05:
                    warns.append(f"narration '{l['text'][:16]}' lasts {d:.1f}s from {s:.1f}s and runs into the next "
                                 f"line at {nxt:.1f}s; shorten the text, raise 'rate', or leave more time")
                events.append((s, Path(wav), "voice"))
    return events, warns


def drop_at(items):
    """When the music should 'drop': the end of the opening title intro (vertical), else 0."""
    titles = sorted(float(i["start"]) for i in items if i.get("type") == "title")
    intro = float(annotate.STYLE.get("title_intro") or 0)
    if titles and intro > 0 and annotate.STYLE.get("layout") == "vertical":
        return titles[0] + intro
    return 0.0


def mix(src, items, audio, dst):
    dur = duration(src)
    events, warns = plan(items, dur, audio)
    for w in warns:
        print(f"[audio] warning: {w}", file=sys.stderr)
    inputs, chains, sfx_labels, voice_labels = ["-i", str(src)], [], [], []
    n = 1
    for s, wav, kind in events:
        inputs += ["-i", str(wav)]
        ms = max(0, int(s * 1000))
        vol = float(audio.get("sfx_volume", 0.5)) if kind == "sfx" else float(audio.get("voice_volume", 1.0))
        chains.append(f"[{n}:a]aresample={SR},aformat=channel_layouts=stereo,adelay={ms}|{ms},volume={vol}[e{n}]")
        (sfx_labels if kind == "sfx" else voice_labels).append(f"[e{n}]")
        n += 1
    bgm = audio.get("bgm")
    bgm_label = None
    if bgm:
        if bgm == "synth" and audio.get("bgm_genre"):
            # genre-based generator (music.py); the drop lands when the opening title hands over
            import music
            path = CACHE / f"bgm-{audio.get('bgm_genre')}-{audio.get('bgm_seed', 1)}-{dur:.1f}.wav"
            g, bpm = music.build(path, dur + 1, audio.get("bgm_genre"), audio.get("bgm_bpm_fixed"),
                                 int(audio.get("bgm_key", 0)), drop_at(items), int(audio.get("bgm_seed", 1)))
            print(f"[audio] bgm: {g} {bpm} bpm (key {int(audio.get('bgm_key', 0)):+d})", file=sys.stderr)
        elif bgm != "synth" and Path(os.path.expanduser(bgm)).is_dir():
            # a folder of your own (royalty-free) tracks: pick one per video, reproducibly from the seed
            import random
            files = sorted(p for p in Path(os.path.expanduser(bgm)).iterdir()
                           if p.suffix.lower() in (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"))
            if not files:
                print(f"[audio] warning: no audio files in bgm folder '{bgm}'; skipped", file=sys.stderr)
                path = Path("/nonexistent")
            else:
                path = random.Random(f"{audio.get('bgm_seed', 1)}:bgmfile").choice(files)
                print(f"[audio] bgm: {path.name} (from {bgm})", file=sys.stderr)
        else:
            path = (synth_bgm(dur + 1, int(audio.get("bgm_bpm", 124)), audio.get("bgm_energy", "light"),
                              int(audio.get("bgm_key", 0)))
                    if bgm == "synth" else Path(os.path.expanduser(bgm)))
        if not Path(path).exists():
            print(f"[audio] warning: bgm '{bgm}' not found; skipped", file=sys.stderr)
        else:
            inputs += ["-stream_loop", "-1", "-i", str(path)]
            # level the track first so every genre / file sits at the same place under the voice
            chains.append(f"[{n}:a]aresample={SR},aformat=channel_layouts=stereo,atrim=0:{dur:.3f},"
                          f"loudnorm=I=-16:TP=-1.5:LRA=11,aresample={SR},"
                          f"afade=t=in:d=0.2,afade=t=out:st={max(0, dur - 1):.3f}:d=1,"
                          f"volume={float(audio.get('bgm_volume', 0.18))}[bgm0]")
            bgm_label = "[bgm0]"
            n += 1
    if not (sfx_labels or voice_labels or bgm_label):
        shutil.copyfile(src, dst)
        print("[audio] nothing to add; copied video as is", file=sys.stderr)
        return
    parts = []
    voice = None
    if voice_labels:
        chains.append("".join(voice_labels) + f"amix=inputs={len(voice_labels)}:normalize=0,apad=whole_dur={dur:.3f}[voice]")
        voice = "[voice]"
    if bgm_label and voice:
        # duck the music while someone is speaking
        chains.append("[voice]asplit=2[vkey][vmix]")
        chains.append(f"{bgm_label}[vkey]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=350[bgmd]")
        parts += ["[bgmd]", "[vmix]"]
    else:
        parts += [p for p in (bgm_label, voice) if p]
    parts += sfx_labels
    chains.append("".join(parts) + f"amix=inputs={len(parts)}:normalize=0:duration=longest,"
                  f"atrim=0:{dur:.3f},loudnorm=I={float(audio.get('loudness', -16))}:TP=-1.5:LRA=11,"
                  f"aresample={SR}[aout]")   # short-video loudness (~-16 LUFS)
    run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(chains),
         "-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
         "-shortest", "-movflags", "+faststart", str(dst)])
    kinds = [k for _, _, k in events]
    print(f"[audio] wrote {dst} ({kinds.count('sfx')} sfx, {kinds.count('voice')} narration lines, "
          f"bgm: {bgm or 'none'}, {len(warns)} warnings)")


def main():
    a = sys.argv[1:]
    if a[:1] == ["--tts"]:
        # python3 audio.py --tts TEXT OUT.wav [--audio '<style audio JSON>'] [--voice V --rate R]
        text, out = a[1], a[2]
        if "--audio" in a:
            audio = json.loads(a[a.index("--audio") + 1])
        else:
            audio = {"voice": a[a.index("--voice") + 1] if "--voice" in a else "Kyoko",
                     "rate": int(a[a.index("--rate") + 1]) if "--rate" in a else 200}
        print(json.dumps({"duration": tts(text, out, audio), "engine": tts_engine(audio)}))
        return
    if len(a) != 3:
        sys.exit(__doc__)
    src, spec_path, dst = a
    spec = json.loads(Path(spec_path).read_text())
    items = spec["items"] if isinstance(spec, dict) else spec
    annotate.load_style(spec.get("style") if isinstance(spec, dict) else None)
    audio = annotate.STYLE["audio"]
    if not audio.get("enabled"):
        shutil.copyfile(src, dst)
        print("[audio] audio disabled in style; copied video as is", file=sys.stderr)
        return
    mix(src, items, audio, dst)


if __name__ == "__main__":
    main()
