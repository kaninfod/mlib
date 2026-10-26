# mlib

**File, tag and cover your music collection to one standard.**

mlib is a command-line tool + minimal web editor for keeping a personal
music library healthy — especially a library served by
[gonic](https://github.com/sentriz/gonic) (Subsonic) on a NAS and consumed
by players/streaming boxes. It replaces the usual "my downloads are a mess,
my tags disagree, half my albums miss a cover and my folders lie" with a
small, safe, *iterative* workflow you drive from your Mac and execute on
the NAS.

The philosophy is human-in-the-loop: **the tool never invents metadata**.
It computes and executes everything derived from your decisions (folder
layout, filenames, conversions, covers), fills gaps from filenames/paths as
*marked suggestions you can reject*, and refuses loudly when something needs
a human (duplicates, conflicts). Nothing is deleted implicitly.

## The standard it converges to

```
artist/album/NN Title.mp3          # files + folders, zero-padded track numbers
tags per file: artist, album, title, year, genre, track n/m (total)
consistent per album: same artist, album, year, genre, tracktotal
cover.jpg present in every album folder
all audio as mp3 (lossless sources → mp3 320 kbps CBR on the way in)
```

## Quick start

Requirements: Python 3.10+, `pip install typer mutagen`, `ffmpeg` on PATH.
Remote machines need Python 3.10+ and `mutagen` only (+ ffmpeg where you
convert) — the tool ships itself there on first use.

```bash
git clone <this repo> && cd mlib
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/mlib doctor                    # checks: python/mutagen/ffmpeg/editor
```

Point it at your NAS once per shell:

```bash
export MLIB_HOST=store                   # an ssh alias (user@host works too)
```

## The loop you will actually live in

Four commands, repeated as often as you like. The catalog *updates* an
existing plan by merging (your pending edits and rejected suggestions
survive), the apply is **idempotent** (already-correct albums are no-ops),
and both are guarded by previews and verification.

```bash
# 1) catalog: snapshot a music folder into a plan (the one path you ever type)
mlib --host store catalog /mnt/store/music/import_staging --open
#    → plan lands next to the data + a local working copy in ~/.mlib/plans
#    → --open launches the editor immediately

# 2) edit: fix things in your browser (works in Firefox/Chromium/Safari)
mlib edit import_staging                 # bare plan name resolves in ~/.mlib/plans
#    edits autosave to the plan; suggestions are shown amber with ✦ and
#    a reject button; Ctrl+C stops the local editor server when done

# 3) apply: converge the files toward the plan — on the NAS, where the files are
mlib apply import_staging --validate --catalog
#    writes happen after a one-line confirm (--yes skips it):
#      convert non-mp3 → mp3 320 CBR · write tags · cover.jpg (extract/normalize/
#      embed) · rename files+folders to the layout · junk cleanup · verify each
#    --validate shows the compliance report afterwards, --catalog refreshes the
#    plan (your pending edits survive the merge) — so "repeat" costs nothing

# 4) when the batch is ready: shelve albums into your collection
mlib shelf import_staging --into /mnt/store/music/collection --rescan
#    applies anything pending, moves the converged folders into the collection
#    (same-disk rename; cross-machine rsync+verify), then asks gonic to rescan
```

`mlib validate <plan>` and `mlib diff <plan>` read the plan only — safe at
any moment. `mlib plans` lists every working copy with its root and state.

## Command reference

| Command | What it does | Example |
|---|---|---|
| `mlib catalog ROOT` | Scan a music folder → `mlib_plan.json` next to the data + a locally edited working copy. Re-runs update+merge; `--overwrite` discards pending edits (rare) | `mlib --host store catalog /mnt/store/music/collection` |
| `mlib edit PLAN` | Open the plan in the bundled single-file editor (localhost bridge; any browser) | `mlib edit import_staging` |
| `mlib validate PLAN` | Read-only compliance report — the retrofit worklist ("all albums conform ✅") | `mlib validate import_staging` |
| `mlib diff PLAN` | Show pending edits (`current → new`), including ✦ inferred suggestions | `mlib diff import_staging` |
| `mlib apply PLAN` | Converge files: convert → tag → cover → rename → folder → junk → verify + audit. Writing is the default; `--dry-run` previews; `--yes` skips the confirm; `--validate`/`--catalog` run the post-steps; `--album` scopes one album | `mlib apply import_staging --validate --catalog` |
| `mlib convert ROOT` | Plan-free bulk conversion of a folder: flac/opus/webm → mp3 320 CBR in place, tags carried, cover.jpg materialized. `--dry-run` first; originals never move unless `--remove-originals` | `mlib --host store convert /mnt/store/music/import_staging --dry-run` |
| `mlib shelf PLAN --into ROOT` | Apply pending work, then move converged album folders into a collection root (rename or verified rsync; collisions skipped) | `mlib shelf import_staging --into /mnt/store/music/collection --rescan` |
| `mlib rescan` | Trigger gonic's library rescan (Subsonic `startScan`) | uses `GONIC_URL` / `GONIC_USER` / `GONIC_PASS` |
| `mlib doctor` | Environment check (local + `--host` remote); `mlib doctor --host store --sync` deploys the tool to the remote | |
| `mlib plans` | List local plan working copies: root, albums, pending edits, ✦ inferred, issues | |

**PLAN arguments** take a file path, a bare name in `~/.mlib/plans/`
(`import_staging`), or a local root folder — and if omitted, the
last-touched plan is used.

## Remote execution (`--host`)

The plan travels; **the audio never does**. With `--host store` (or
`MLIB_HOST=store`), commands that touch files run *on that machine*: mlib
rsyncs itself to `~/mlib_src` there, copies your edited plan up, executes
disk-locally (ffmpeg, tag writes, renames at NAS speed), and pulls the
updated plan back. `--host` may be given before the command (global) or
after it — both parse. Plan-file operations (`edit`, `diff`, `validate`)
always run locally.

```
mlib --host store catalog /mnt/store/music/collection --open
mlib apply collection --validate --catalog
mlib rescan
```

## Safety properties

- **Preview + confirm**: apply/shelf write after a one-line confirm
  (`--dry-run` to preview, `--yes` to skip; `--remove-originals` on convert
  is the only delete-your-sources action and is always explicit).
- **Idempotent**: apply is skip-if-already-correct; repeated runs finish
  only what is left.
- **Atomic**: unresolved albums, name collisions and target collisions are
  skipped whole with precise guidance — never half-done.
- **Audited**: every apply records what it did (`_applied` + rename map);
  plans rotate `.bak` backups; re-catalogs merge pending edits and
  rejected-suggestion memory.
- **No implicit deletes**: audio files are never removed without an
  explicit flag; husks (originals left after conversion) stay until you
  remove them.
- **Verified**: after apply, every track is re-read and compared against
  the plan — problems are reported, not glossed over.

## Development

```bash
.venv/bin/pip install -e ".[test]"
.venv/bin/pytest -q          # 68 tests incl. real-ffmpeg end-to-end
```

Structure: `cli.py` (typer) · `catalog/model/layout/tags/convert/cover` ·
`apply/shelf/edit/rescan/remote` · `assets/editor.html` (single-file editor)
· `remote_runner.py` (the stdlib-only program that runs on remote hosts).
*MIT license. Born out of three generations of home-tooling and one 608-album
collection that deserved better.*
