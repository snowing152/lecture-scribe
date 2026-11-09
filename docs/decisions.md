# Decisions

Each decision with its date, its reason, and what it ruled out. The binding
list is *Settled decisions* in [`../CLAUDE.md`](../CLAUDE.md). This file is
the history behind it: why each choice was made, so it is not reopened by
accident. A reversed decision stays here with a note saying what replaced it.

Format: **D-n · date · status**, then the decision, why, and what was
rejected.

---

### D-1 · 2026-09-10 · active: Record first, recognise afterwards

No real-time transcription. The whole WAV is recorded, then decoded in a
batch.
**Why:** batch decoding sees whole sentences and is more accurate. It also
keeps capture and recognition separable (D-2).
**Rejected:** streaming ASR.

### D-2 · 2026-09-10 · active: Capture and recognition run independently

Recording needs neither the model nor `faster-whisper`. Transcribing can
happen later or on another machine.
**Why:** record on a laptop without a GPU or the model, decode elsewhere.

### D-3 · 2026-09-10 · active: Local only, one voice, one language

No diarization, translation, summarisation or cloud ASR.
**Why:** one lecturer, one machine. Every extra stage is a place to be wrong.

### D-4 · 2026-09-10 · active: Find the loopback device by exact id, never by name

**Why:** the user's USB microphone has a headphone jack and appears twice
under one name. `soundcard`'s name matching returns whichever comes first,
which would silently record the microphone.

### D-5 · 2026-09-10 · active: Keep `soundcard`, never `sounddevice`

**Why:** `sounddevice`'s `WasapiSettings` has no loopback parameter, so it
cannot record system output on Windows.

### D-6 · 2026-09-10 · active: Capture two channels, write one

Mix down with `np.mean`.
**Why:** WASAPI returns garbage when asked for one channel. Summing would
clip past 1.0.

### D-7 · 2026-09-10 · active: Never delete or overwrite audio automatically

**Why:** a recording cannot be recreated, but recognition can be repeated.
Every cleanup path (for example `rmdir` of an empty folder in `scribe rec`)
must be unable to touch audio.

### D-8 · 2026-09-10 · active: Strict `config.toml` from the current directory

Unknown keys are an error. Since 2026-11-09, out-of-range values are too.
**Why:** a typo should not silently fall back to a default. This is also why
the menu entry pins `Path=` (D-14).

### D-9 · 2026-09-10 · active, refined by D-21: Recognition settings

`vad_filter=True` with 500 ms silence, `condition_on_previous_text=False`,
`int8_float16` on GPU and `int8` on CPU.
**Why:** on a 90-minute file, carried context drags one error through every
later segment. Full float16 large-v3 wants about 10 GB of VRAM and this
machine has 8 GB. `int8_float16` peaked at 3365 MiB.

### D-10 · 2026-09-10 · active: Plain `.txt` output only

Glossary, `.srt` and `segments.json` were cut (`3344ee3`).
**Why:** the need is a clear text file to read.

### D-11 · 2026-09-10 · active: Qt (PySide6) for the window, not tkinter

Reverses an earlier "no GUI" line, at the user's request.
**Why:** the Tk in uv's managed CPython has no Xft or fontconfig. It sees one
bitmap font and measures `한국어 강의` at 6 px. Qt sees 846 families.
Fusion style is forced so that one stylesheet works on both platforms.

### D-12 · 2026-09-10 · active: The window is a second front end, never the only one

`cli.py` and `gui.py` are peers. Neither holds logic the other needs.
**Why:** everything stays scriptable, and the two cannot drift apart.

### D-13 · 2026-09-10 · active: Paragraphs by target length, not only by pause

`paragraph_target` ends a paragraph at the next sentence end.
`paragraph_max` ends it regardless.
**Why:** Whisper emits segments back to back. 92 of 97 gaps were zero, so a
pause rule alone produced a 15-minute paragraph.

### D-14 · 2026-09-10 · active: The menu entry pins the working directory

`scribe launcher` writes `Path.cwd()` into the entry. On Windows the `.lnk`
is written through PowerShell's `WScript.Shell`.
**Why:** a menu starts programs in `$HOME`, where no `config.toml` exists.
Using PowerShell avoids a COM dependency.

### D-15 · 2026-09-10 · active: GPU support as the optional `cuda` extra

`nvidia-cublas-cu12` only, never a plain dependency, and preloaded by full
path.
**Why:** recording needs none of it, and it costs a gigabyte.
`ctranslate2` 4.8.2 needs cuBLAS and no cuDNN. Measured 21x realtime against
about 3.5x on the CPU.

### D-16 · 2026-09-14 · active: Keep the desktop awake while working

Uses `org.freedesktop.ScreenSaver.Inhibit` over a private D-Bus connection
on Linux, `SetThreadExecutionState` on Windows, and `notify-send` for the
notice.
**Why:** this machine suspends after 25 idle minutes, which kills a
recording or a CPU transcription. `UnInhibit` and `Notify` cannot be called
correctly from PySide6 because of signedness, so the code closes the
connection and shells out instead.

### D-17 · 2026-09-14 · active: Files are named after the lecture folder

`<folder>.wav` and `<folder>.txt` replace `audio.wav` and `transcript.txt`.
**Why:** a copied-out file should still say which lecture it is. Renaming
the folder before transcribing gives the text the new name.
**Consequence:** two WAVs in one folder share a transcript name (see
`bugs.md`).

### D-18 · 2026-09-28 · active: No search index

The SQLite FTS5 `scribe find` step was dropped.
**Why:** `grep` finds a word with its timecode in 6 ms across 39
transcripts. FTS5 splits on spaces (`프로토콜의` ≠ `프로토콜`), and trigram
needs three characters where most Korean words here have two.

### D-19 · 2026-09-28 · active: Dark window with two signal colours

Greys plus red for a live recording and amber for a mark. No light theme.
**Why:** colour carries meaning, never decoration. Replaces the original
greys-only rule from 2026-09-10.

### D-20 · 2026-09-28 → 2026-10-27 · reversed: Upload transcripts to Google Drive

This was added through rclone (`db323cd`) and removed at the user's request
(`863c355`).
**Why removed:** the rclone token expired after seven days while the Google
app was in testing. The user chose to keep transcripts on this machine,
like the recordings. An `[upload]` section in `config.toml` is now an error.

### D-21 · 2026-11-09 · active: Choose the language, never detect it

The window offers Korean and English, and `scribe text --language` takes any
code. Neither writes back to `config.toml`. Both go through `merge_cli`.
**Why:** faster-whisper 1.2.1 guesses once from one 30-second window for the
whole file, and has taken Korean for Japanese. `en` on Korean speech
produces a loose English translation, while `ko` keeps English terms
readable.
**Rejected:** an "auto" entry in the list.

### D-22 · ongoing: No abstractions for a future that has not arrived

No plugins, factories or single-implementation base classes. No blanket
`try/except`: catch named exceptions and say what to do next. No new
dependencies without asking.
