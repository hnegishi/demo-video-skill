#!/usr/bin/env bash
# Convert a video to a compact GIF with a generated palette (much better than ffmpeg's default).
#
# Usage: to_gif.sh <in.mp4> <out.gif> [width=960] [fps=15]
set -euo pipefail
in="$1"; out="$2"; width="${3:-960}"; fps="${4:-15}"
ffmpeg -v error -y -i "$in" -vf \
  "fps=$fps,scale=$width:-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle" \
  -loop 0 "$out"
echo "wrote $out ($(du -h "$out" | cut -f1))"
