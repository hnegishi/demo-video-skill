#!/usr/bin/env python3
"""csvstat: print quick per-column statistics for a CSV file."""
import argparse
import csv
import statistics
import sys


def main():
    ap = argparse.ArgumentParser(prog="csvstat", description="CSV の列ごとの統計を表示する")
    ap.add_argument("file", help="CSV ファイル")
    ap.add_argument("-c", "--columns", help="対象の列（カンマ区切り）")
    ap.add_argument("--json", action="store_true", help="JSON で出力する")
    args = ap.parse_args()

    with open(args.file, newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit("empty csv")
    cols = args.columns.split(",") if args.columns else list(rows[0].keys())

    result = {}
    for c in cols:
        vals = [r[c] for r in rows if r.get(c, "") != ""]
        try:
            nums = [float(v) for v in vals]
            result[c] = {"type": "number", "count": len(nums), "min": min(nums), "max": max(nums),
                         "mean": round(statistics.mean(nums), 2)}
        except ValueError:
            result[c] = {"type": "text", "count": len(vals), "unique": len(set(vals))}

    if args.json:
        import json
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(f"{'column':<10} {'type':<7} {'count':>5}  details")
    print("-" * 48)
    for c, s in result.items():
        d = (f"min={s['min']:g} max={s['max']:g} mean={s['mean']:g}" if s["type"] == "number"
             else f"unique={s['unique']}")
        print(f"{c:<10} {s['type']:<7} {s['count']:>5}  {d}")


if __name__ == "__main__":
    main()
