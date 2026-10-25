#!/usr/bin/env bash
# Build a small demo tree exercising every P0 case, then run the loop on it.
# Requires ffmpeg + mutagen. Usage: bash scripts/make_demo.sh[/tmp/mlib_demo]
set -euo pipefail

DEMO=${1:-/tmp/mlib_demo}
FF="ffmpeg -v error -y -nostdin"

rm -rf "$DEMO"
mkdir -p "$DEMO/Stevie Wonder/Songs In The Key Of Life" \
         "$DEMO/The Doors/Unknown Album" \
         "$DEMO/B.B. King/His Definitive Greatest Hits"

# cover image used across the demo
$FF -f lavfi -i color=c=steelblue:s=96x96 -frames:v 1 -q:v 2 /tmp/mlib_cover.jpg

# 1) conformant album: tagged, filed, cover.jpg present
for i in 1 2 3; do
  $FF -f lavfi -i "sine=frequency=$((330+i*40)):duration=2" -c:a libmp3lame -b:a 128k \
    -metadata title="Song $i" -metadata artist="Stevie Wonder" \
    -metadata albumartist="Stevie Wonder" \
    -metadata album="Songs In The Key Of Life" -metadata date=1976 \
    -metadata genre="Funk/Soul" -metadata track="$i/3" \
    "$DEMO/Stevie Wonder/Songs In The Key Of Life/0$i Song $i.mp3"
done
cp /tmp/mlib_cover.jpg "$DEMO/Stevie Wonder/Songs In The Key Of Life/cover.jpg"

# 2) broken album: untagged mp3, tagged flac (conversion pending),
#    image with the wrong name, junk file
$FF -f lavfi -i "sine=frequency=220:duration=2" -c:a libmp3lame -b:a 128k \
  "$DEMO/The Doors/Unknown Album/Track 1.mp3"
$FF -f lavfi -i "sine=frequency=262:duration=2" -c:a flac \
  -metadata title="Break On Through" -metadata artist="The Doors" -metadata track=1/1 \
  "$DEMO/The Doors/Unknown Album/break.flac"
$FF -f lavfi -i color=c=red:s=64x64 -frames:v 1 -q:v 2 \
  "$DEMO/The Doors/Unknown Album/folder.png"
echo "liner notes" > "$DEMO/The Doors/Unknown Album/notes.txt"

# 3) fully tagged mp3 with EMBEDDED cover but off-layout filename
mp3="$DEMO/B.B. King/His Definitive Greatest Hits/Track 8.mp3"
$FF -f lavfi -i "sine=frequency=392:duration=2" -c:a libmp3lame -b:a 128k \
  -metadata title="To Know You Is to Love You (single version)" \
  -metadata artist="B.B. King" -metadata album="His Definitive Greatest Hits" \
  -metadata date=1998 -metadata genre="Blues" -metadata track=8/15 "$mp3"
python - "$mp3" <<'PY'
import sys
from mutagen.id3 import ID3, APIC
p = sys.argv[1]
tags = ID3(p)  # load first, then add — a fresh ID3() would wipe the text frames
with open("/tmp/mlib_cover.jpg", "rb") as f:
    tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover (front)", data=f.read()))
tags.save(p)
PY

echo "demo tree at $DEMO — try:"
echo "  mlib catalog \"$DEMO\" && mlib validate \"$DEMO\" && mlib diff \"$DEMO/mlib_plan.json\""