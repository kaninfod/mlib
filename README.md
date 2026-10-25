# mlib

One tool to manage a music collection to one standard — for the gonic/jukebox setup.

    mlib catalog <root>        # scan a folder (collection or new music) -> mlib_plan.json
    mlib edit <plan>           # edit the plan in your browser (editor lands in P1)
    mlib validate <plan>       # compliance report per the standard
    mlib diff <plan>           # show pending edits
    mlib apply <plan> --write  # converge files to the plan (P2)
    mlib convert <root>        # flac/opus/webm -> mp3 320 CBR (P3)
    mlib shelf <plan> --into   # apply + move new albums into the collection (P3)

The standard (see design §4): `artist/album/NN Title.mp3`; tags artist, album, title,
year, genre, track number, tracktotal (consistent per album); cover.jpg in every
album folder; all files mp3.

Documentation lives in the Obsidian vault: `documentation/music management`.
Status: P0 (catalog / validate / diff) implemented; editor P1, apply P2, acquisition P3.