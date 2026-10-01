#!/usr/bin/env python3
"""Programmatic checks for demo-video eval runs.

Usage: check_outputs.py <run_dir>   (run_dir contains outputs/ and sits under <eval_dir>/)
Prints JSON: {"checks": [{"text", "passed", "evidence"}], "contact_sheets": [...]}
Visual assertions are left to the grader; this script produces contact sheets for them.
"""
import json
import subprocess
import sys
from pathlib import Path

SKILL_SCRIPTS = Path(__file__).resolve().parent.parent / "demo-video" / "scripts"
DURATION = {1: (8, 60), 2: (8, 60), 3: (18, 35)}
SCRIPT_EXT = {1: {".mjs", ".js", ".ts", ".py", ".sh"}, 2: {".tape", ".sh", ".py", ".cast"}, 3: {".html", ".mjs", ".js", ".py"}}


def probe(path):
    out = subprocess.run(["python3", str(SKILL_SCRIPTS / "inspect_video.py"), str(path), "--frames", "8"],
                         capture_output=True, text=True)
    info = {}
    for line in out.stdout.splitlines():
        k, _, v = line.partition(":")
        info[k.strip()] = v.strip()
    return info


def main():
    run = Path(sys.argv[1]).resolve()
    outputs = run / "outputs"
    # eval_metadata.json sits in the eval dir: <eval>/<config>/run-N/
    meta_path = next(d / "eval_metadata.json" for d in run.parents if (d / "eval_metadata.json").exists())
    meta = json.loads(meta_path.read_text())
    eid = meta["eval_id"]
    files = [p for p in outputs.rglob("*") if p.is_file() and "node_modules" not in p.parts]
    mp4s = [p for p in files if p.suffix == ".mp4" and ".plain" not in p.name]
    gifs = [p for p in files if p.suffix == ".gif"]
    checks = []

    def add(text, passed, evidence):
        checks.append({"text": text, "passed": bool(passed), "evidence": evidence})

    add("MP4 動画が出力されている", mp4s, ", ".join(p.name for p in mp4s) or "mp4 なし")
    main_mp4 = max(mp4s, key=lambda p: p.stat().st_size) if mp4s else None
    info = probe(main_mp4) if main_mp4 else {}
    sheets = [info["contact"]] if info.get("contact") else []

    if eid == 1:
        ok = gifs and all(g.stat().st_size <= 10 * 1024 * 1024 for g in gifs)
        add("GIF が出力されていて 10MB 以下", ok,
            ", ".join(f"{g.name} {g.stat().st_size/1e6:.1f}MB" for g in gifs) or "gif なし")

    lo, hi = DURATION[eid]
    try:
        dur = float(info.get("duration", "nan").rstrip("s"))
    except ValueError:
        dur = float("nan")
    add(f"動画の尺が {lo}〜{hi} 秒", lo <= dur <= hi, f"{main_mp4.name if main_mp4 else '-'}: {dur:.1f}s")

    try:
        luma = float(info.get("mean luma", "nan").split()[0])
    except ValueError:
        luma = float("nan")
    add("動画が真っ黒・真っ白一色ではない（平均輝度 5〜250）", 5 <= luma <= 250, f"mean luma {luma}")

    scripts = [p for p in files if p.suffix in SCRIPT_EXT[eid]]
    label = {1: "録り直し用のシナリオ/スクリプトが成果物と一緒に保存されている",
             2: "録り直し用の .tape またはスクリプトが成果物と一緒に保存されている",
             3: "録り直し用の HTML/スクリプトが成果物と一緒に保存されている"}[eid]
    add(label, scripts, ", ".join(p.name for p in scripts[:6]) or "なし")

    ann = [p for p in files if p.name.endswith("annotations.json")]
    add("注釈の時刻と文言が annotations.json として成果物と一緒に保存されている", ann,
        ", ".join(p.name for p in ann) or "なし")
    add("動画の解像度が 1280x720", info.get("resolution") == "1280x720", info.get("resolution", "-"))

    # requests from review round 2: click effects / zoom where useful, annotations that cover nothing
    items = []
    for a in ann:
        try:
            spec = json.loads(a.read_text())
            items += spec["items"] if isinstance(spec, dict) else spec
        except (ValueError, KeyError):
            pass
    types = [i.get("type") for i in items]
    if eid == 1:
        add("クリックエフェクトが入っている（annotations.json に click がある）", "click" in types,
            f"click x{types.count('click')}")
    if eid in (1, 2):
        # small web UI and dense terminal output are exactly what zooming is for
        add("ズームを 1 回以上使っている（小さな UI / 詰まったターミナル出力を読ませるため）", "zoom" in types,
            f"zoom x{types.count('zoom')}")
    plain = [p for p in files if p.name.endswith(".plain.mp4")]
    if ann and plain:
        sys.path.insert(0, str(SKILL_SCRIPTS))
        import annotate as A
        W, H, _, dur = A.probe(str(plain[0]))
        z = A.Zoom(items, W, H)
        _, warns, notes = A.build_layers(items, W, H, dur, A.Scene(str(plain[0]), W, H, z), z)
        add("注釈の配置警告（コンテンツへの重なり）が残っていない", not warns, "; ".join(warns)[:300] or "なし")
    else:
        add("注釈の配置警告（コンテンツへの重なり）が残っていない", False, "annotations.json / plain.mp4 なし")

    print(json.dumps({"checks": checks, "contact_sheets": sheets, "main_video": str(main_mp4) if main_mp4 else None,
                      "info": info}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
