#!/usr/bin/env bash
# Concatenate videos into one MP4, scaling/padding each to a common size.
#
# Usage: concat.sh <out.mp4> <in1> <in2> [...]   (env: W=1280 H=720 FPS=30)
set -euo pipefail
out="$1"; shift
W="${W:-1280}"; H="${H:-720}"; FPS="${FPS:-30}"
inputs=(); filters=""; labels=""
i=0
for f in "$@"; do
  inputs+=(-i "$f")
  filters+="[$i:v]scale=${W}:${H}:force_original_aspect_ratio=decrease,pad=${W}:${H}:(ow-iw)/2:(oh-ih)/2:color=black,fps=${FPS},setsar=1,format=yuv420p[v$i];"
  labels+="[v$i]"
  i=$((i+1))
done
ffmpeg -v error -y "${inputs[@]}" -filter_complex "${filters}${labels}concat=n=${i}:v=1:a=0[out]" \
  -map "[out]" -c:v libx264 -crf 20 -movflags +faststart "$out"
echo "wrote $out"
