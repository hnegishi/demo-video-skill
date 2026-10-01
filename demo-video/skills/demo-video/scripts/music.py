#!/usr/bin/env python3
"""Generated background music for short-video ("dopagaki") demos. Pure Python, no numpy.

    python3 music.py out.wav --seconds 25 [--genre edm|trap|lofi|chiptune|funk|random]
                     [--bpm N] [--key SEMITONES] [--drop SECONDS] [--seed N]

Each genre is a small arrangement synthesized from scratch (no samples, no copyrighted material):

  edm       140-150 bpm  four-on-the-floor, pumping detuned-saw chords, 16th pluck arpeggio
  trap      135-150 bpm  half-time, 808 bass, rolling hats, dark pad, bell melody
  lofi       78-90 bpm   swung boom-bap, electric-piano 7th chords, vinyl crackle
  chiptune  150-170 bpm  square-wave lead, triangle bass, noise drums (8-bit)
  funk      112-122 bpm  four-on-the-floor + open hats, octave bass, choppy chord stabs

Only a 4-bar loop is synthesized sample by sample (a few seconds of CPU), as stems; ffmpeg loops it.
The track is arranged around the video's opening title: until --drop the intro plays muffled chords
with a rising noise sweep, then the full loop comes in. --bpm omitted = picked inside the genre's
range from --seed, so the same seed gives the same track. Loops are cached in
~/.cache/demo-video-skill/audio.
"""
import argparse
import array
import math
import random
import subprocess
import wave
from pathlib import Path

SR = 32000            # plenty for a background bed; keeps pure-Python synthesis fast
CACHE = Path.home() / ".cache" / "demo-video-skill" / "audio"
GENRES = {"edm": (140, 150), "trap": (135, 150), "lofi": (78, 90), "chiptune": (150, 170), "funk": (112, 122)}


def hz(n):
    return 440.0 * 2 ** ((n - 69) / 12)


def saw(phase):
    return 2.0 * (phase - math.floor(phase + 0.5))


def square(phase, duty=0.5):
    return 1.0 if (phase % 1.0) < duty else -1.0


def tri(phase):
    return 4.0 * abs((phase % 1.0) - 0.5) - 1.0


def write_wav(path, samples, sr=SR):
    peak = max(1e-9, max(abs(s) for s in samples))
    k = 0.89 / peak
    data = array.array("h", (int(max(-1.0, min(1.0, s * k)) * 32767) for s in samples))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data.tobytes())


class Loop:
    """A 4-bar buffer set with helpers to place sounds (wrapping at the end for seamless looping)."""

    def __init__(self, bpm, seed):
        self.beat = 60.0 / bpm
        self.bar = 4 * self.beat
        self.n = int(round(4 * self.bar * SR))
        self.rng = random.Random(seed)
        self.stems = {k: [0.0] * self.n for k in ("drums", "bass", "chords", "lead")}
        self.noise = [self.rng.uniform(-1, 1) for _ in range(SR)]

    def add(self, stem, start, length, fn):
        buf, n = self.stems[stem], self.n
        i0 = int(start * SR)
        for i in range(int(length * SR)):
            buf[(i0 + i) % n] += fn(i / SR)

    def nz(self, t, off=0):
        return self.noise[(int(t * SR) + off) % SR]

    # ---- instruments
    def kick(self, t0, f0=48, punch=110, decay=7.5, vol=1.0):
        self.add("drums", t0, 0.35, lambda t: math.sin(2 * math.pi * (f0 * t + punch * (1 - math.exp(-t * 32)) / 32))
                 * math.exp(-t * decay) * vol)

    def snare(self, t0, vol=0.5, tone=190):
        self.add("drums", t0, 0.22, lambda t: (self.nz(t, 777) * 0.8 + math.sin(2 * math.pi * tone * t) * 0.5)
                 * math.exp(-t * 18) * vol)

    def clap(self, t0, vol=0.45):
        self.add("drums", t0, 0.18, lambda t: self.nz(t, 333) * (math.exp(-t * 28) + 0.5 * math.exp(-max(0, t - 0.012) * 35)) * vol)

    def hat(self, t0, open_=False, vol=0.12):
        d, dur = (22, 0.12) if open_ else (70, 0.05)
        self.add("drums", t0, dur, lambda t: (self.nz(t) - self.nz(t, 1)) * math.exp(-t * d) * vol)

    def pad(self, t0, length, notes, kind="supersaw", vol=0.4, pump=None, cutoff=0.18):
        fs = [hz(m) for m in notes]
        lp = [0.0]

        def fn(t):
            if kind == "supersaw":
                v = sum(saw(f * (1 + d) * t + d * 7) for f in fs for d in (-0.011, 0.0, 0.012)) / (3 * len(fs))
            elif kind == "epiano":   # sine + bell partial, soft attack, tremolo
                v = sum(math.sin(2 * math.pi * f * t) + 0.25 * math.sin(2 * math.pi * 3 * f * t) * math.exp(-t * 6)
                        for f in fs) / len(fs) * (1 - math.exp(-t * 30)) * math.exp(-t * 0.6) * (0.85 + 0.15 * math.sin(2 * math.pi * 4.5 * t))
            else:                    # dark pad: saw, slow attack
                v = sum(saw(f * t) for f in fs) / len(fs) * min(1.0, t / 0.4)
            lp[0] += cutoff * (v - lp[0])
            g = pump(t0 + t) if pump else 1.0
            return lp[0] * g * vol
        self.add("chords", t0, length, fn)

    def tone(self, stem, t0, length, note, wave_="saw", decay=14.0, vol=0.16, glide_to=None):
        f0 = hz(note)
        f1 = hz(glide_to) if glide_to else f0
        ph = [0.0]

        def fn(t):
            f = f0 + (f1 - f0) * min(1.0, t / max(0.001, length * 0.6))
            ph[0] += f / SR
            p = ph[0]
            w = {"saw": saw(p), "square": square(p), "pulse": square(p, 0.25), "tri": tri(p),
                 "sine": math.sin(2 * math.pi * p),
                 "bell": math.sin(2 * math.pi * p) * 0.7 + math.sin(2 * math.pi * p * 2.76) * 0.3 * math.exp(-t * 8),
                 "pluck": saw(p) * 0.6 + square(p) * 0.4}[wave_]
            return w * (1 - math.exp(-t * 400)) * math.exp(-t * decay) * vol
        self.add(stem, t0, length, fn)


def pump_fn(beat):
    return lambda t: min(1.0, 0.25 + ((t % beat) / beat) * 2.2)


def g_edm(L, key):
    prog = [(57, 60, 64), (53, 57, 60), (48, 52, 55), (55, 59, 62)]          # Am F C G
    roots = [45, 41, 36, 43]
    b, B = L.beat, L.bar
    for i in range(16):
        L.kick(i * b)
        if i % 2:
            L.clap(i * b)
    for s in range(64):
        L.hat(s * b / 4, open_=(s % 4 == 2), vol=0.2 if s % 4 == 2 else 0.09)
    for bi in range(4):
        for hit, ln in ((0, 1.4), (1.5, 0.45), (2.5, 0.45), (3, 0.9)):
            L.tone("bass", bi * B + hit * b, ln * b, roots[bi] + key, "sine", decay=2.2, vol=0.55)
        L.pad(bi * B, B, [m + key + 12 for m in prog[bi]], "supersaw", 0.42, pump_fn(b))
        pat = [prog[bi][0] + 24, prog[bi][1] + 24, prog[bi][2] + 24, prog[bi][0] + 36]
        for s in range(16):
            L.tone("lead", bi * B + s * b / 4, b / 4 * 0.95, pat[s % 4] + key, "pluck", 14, 0.15)


def g_trap(L, key):
    prog = [(57, 60, 64), (57, 60, 64), (53, 57, 60), (52, 56, 59)]          # Am Am F E
    roots = [33, 33, 29, 28]
    b, B = L.beat, L.bar
    for bi in range(4):
        L.kick(bi * B, f0=42, decay=5)
        L.kick(bi * B + 2.5 * b, f0=42, decay=5, vol=0.8)
        L.snare(bi * B + 2 * b, vol=0.55)                                    # half-time snare on 3
        for s in range(16):
            L.hat(bi * B + s * b / 4, vol=0.09)
        if bi % 2 == 1:                                                      # hat roll (32nds) at the bar end
            for r in range(8):
                L.hat(bi * B + 3 * b + r * b / 8, vol=0.06 + 0.01 * r)
        L.tone("bass", bi * B, 2.2 * b, roots[bi] + key + 12, "sine", decay=0.9, vol=0.7,
               glide_to=roots[bi] + key + 12 - (5 if bi == 3 else 0))
        L.tone("bass", bi * B + 2.5 * b, 1.4 * b, roots[bi] + key + 12, "sine", decay=1.5, vol=0.6)
        L.pad(bi * B, B, [m + key for m in prog[bi]], "dark", 0.3, None, 0.06)
        mel = [prog[bi][2] + 12, prog[bi][1] + 12, prog[bi][0] + 12, prog[bi][1] + 12]
        for k, step in enumerate((0, 3, 6, 10)):
            L.tone("lead", bi * B + step * b / 4, b * 0.7, mel[k] + key, "bell", 4.5, 0.2)


def g_lofi(L, key):
    prog = [(53, 57, 60, 64), (52, 55, 59, 62), (50, 53, 57, 60), (48, 52, 55, 59)]   # Fmaj7 Em7 Dm7 Cmaj7
    roots = [41, 40, 38, 36]
    b, B = L.beat, L.bar
    sw = b / 4 * 0.18                                                         # swing
    for bi in range(4):
        t = bi * B
        L.kick(t, f0=52, decay=9, vol=0.8)
        L.kick(t + 1.75 * b + sw, f0=52, decay=9, vol=0.6)
        L.kick(t + 2.5 * b, f0=52, decay=9, vol=0.7)
        L.snare(t + 1 * b, vol=0.35, tone=170)
        L.snare(t + 3 * b, vol=0.35, tone=170)
        for s in range(8):
            L.hat(t + s * b / 2 + (sw if s % 2 else 0), vol=0.06)
        L.tone("bass", t, 1.8 * b, roots[bi] + key, "tri", decay=1.2, vol=0.45)
        L.tone("bass", t + 2.5 * b, 1.2 * b, roots[bi] + key + 7, "tri", decay=1.6, vol=0.35)
        L.pad(t, B, [m + key for m in prog[bi]], "epiano", 0.5, None, 0.5)
        L.pad(t + 2.5 * b + sw, 1.4 * b, [m + key for m in prog[bi][1:]], "epiano", 0.3, None, 0.5)
    # vinyl crackle
    for i in range(int(L.n / SR * 18)):
        L.add("chords", L.rng.uniform(0, 4 * B), 0.004, lambda t: L.rng.uniform(-1, 1) * 0.15)


def g_chiptune(L, key):
    prog = [(60, 64, 67), (55, 59, 62), (57, 60, 64), (53, 57, 60)]          # C G Am F
    roots = [48, 43, 45, 41]
    melody = [0, 4, 7, 12, 11, 7, 4, 7, 2, 7, 11, 14, 12, 9, 7, 4]           # steps relative to chord root
    b, B = L.beat, L.bar
    for bi in range(4):
        t = bi * B
        for i in range(4):
            L.add("drums", t + i * b, 0.08, lambda x: square(L.nz(x, 9) * 0.5 + 0.5) * math.exp(-x * 40) * (0.3 if i % 2 == 0 else 0.18))
        for s in range(8):
            L.tone("bass", t + s * b / 2, b / 2 * 0.9, roots[bi] + key + (12 if s % 2 else 0), "tri", 3, 0.4)
        for s in range(16):
            L.tone("lead", t + s * b / 4, b / 4 * 0.9, prog[bi][0] + key + 12 + melody[(s + bi * 3) % 16], "square", 6, 0.12)
        L.pad(t, B, [m + key for m in prog[bi]], "dark", 0.12, None, 0.3)


def g_funk(L, key):
    prog = [(57, 60, 64, 67), (62, 66, 69, 72), (57, 60, 64, 67), (62, 66, 69, 72)]   # Am7 D7 Am7 D7
    roots = [45, 50, 45, 50]
    b, B = L.beat, L.bar
    for i in range(16):
        L.kick(i * b, f0=50, decay=9, vol=0.85)
        if i % 2:
            L.clap(i * b, vol=0.4)
        L.hat(i * b + b / 2, open_=True, vol=0.16)
    for bi in range(4):
        t = bi * B
        for step, octv in ((0, 0), (3, 12), (6, 0), (8, 12), (10, 0), (11, 12), (14, 0)):
            L.tone("bass", t + step * b / 4, b / 4 * 0.9, roots[bi] + key - 12 + octv, "pluck", 9, 0.35)
        for step in (2, 6, 10, 13):                                          # choppy chord stabs
            for m in prog[bi]:
                L.tone("chords", t + step * b / 4, b / 4 * 0.6, m + key, "pluck", 22, 0.07)


GEN = {"edm": g_edm, "trap": g_trap, "lofi": g_lofi, "chiptune": g_chiptune, "funk": g_funk}


def pick(genre, bpm, seed):
    rng = random.Random(f"{seed}:music")
    if not genre or genre == "random":
        genre = rng.choice(sorted(GENRES))
    if not bpm:
        lo, hi = GENRES[genre]
        bpm = rng.randint(lo, hi)
    return genre, int(bpm)


def build(out, seconds, genre="random", bpm=None, key=0, drop=2.0, seed=1):
    genre, bpm = pick(genre, bpm, seed)
    CACHE.mkdir(parents=True, exist_ok=True)
    tag = f"bgm-{genre}-{bpm}-k{key}-s{seed}"
    stems = {name: CACHE / f"{tag}-{name}.wav" for name in ("drums", "bass", "chords", "lead")}
    if not all(p.exists() for p in stems.values()):
        L = Loop(bpm, seed)
        GEN[genre](L, int(key))
        for name, p in stems.items():
            write_wav(p, L.stems[name])
    full = CACHE / f"{tag}-full.wav"
    if not full.exists():
        subprocess.run(["ffmpeg", "-v", "error", "-y", *sum((["-i", str(stems[k])] for k in ("drums", "bass", "chords", "lead")), []),
                        "-filter_complex", "[0]volume=1.0[d];[1]volume=0.85[b];[2]volume=0.75[c];[3]volume=0.6[l];"
                        "[d][b][c][l]amix=inputs=4:normalize=0,alimiter=limit=0.9", str(full)], check=True)
    drop = max(0.0, min(drop, seconds))
    body = max(0.5, seconds - drop)
    flt = (f"[1]aloop=loop=-1:size=2e9,atrim=0:{body:.3f},afade=t=out:st={max(0, body - 1.2):.3f}:d=1.2[main];")
    if drop > 0.05:
        flt = (f"[0]aloop=loop=-1:size=2e9,atrim=0:{drop:.3f},lowpass=f=700,volume=0.8,"
               f"afade=t=in:d={min(0.4, drop / 2):.3f}[intro];"
               f"anoisesrc=d={drop:.3f}:c=white:a=0.4:r={SR},highpass=f=1500,"
               f"volume='pow(t/{drop:.3f},2.2)':eval=frame[riser];"
               f"[intro][riser]amix=inputs=2:normalize=0[pre];" + flt +
               "[pre][main]concat=n=2:v=0:a=1,alimiter=limit=0.92[out]")
    else:
        flt += "[main]alimiter=limit=0.92[out]"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(stems["chords"]), "-stream_loop", "-1", "-i", str(full),
                    "-filter_complex", flt, "-map", "[out]", "-ar", "44100", str(out)], check=True)
    return genre, bpm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--seconds", type=float, required=True)
    ap.add_argument("--genre", default="random", choices=["random", *GENRES])
    ap.add_argument("--bpm", type=int)
    ap.add_argument("--key", type=int, default=0)
    ap.add_argument("--drop", type=float, default=2.0)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    genre, bpm = build(a.out, a.seconds, a.genre, a.bpm, a.key, a.drop, a.seed)
    print(f"[music] wrote {a.out} ({genre}, {bpm} bpm, key {a.key:+d})")


if __name__ == "__main__":
    main()
