# Bugs

Open bugs first, then known limitations, then everything fixed so far with
the commit that fixed it. When a bug is fixed, move it to *Fixed* with the
date and commit. Do not delete it.

## Open

None. The next one found gets id B-11.

## Known limitations

Not bugs, but they can surprise you.

- **Transcripts of two WAVs in one folder overwrite each other across runs.**
  A transcript is named after its folder. `scribe text` refuses this within
  one batch (`shared_transcripts`), but a second run or Ctrl+O in the window
  writes the same `<folder>.txt`. Audio is never touched.
- **A transcript's title line keeps the folder name from when it was made.**
  Renaming the folder later does not change the header.
- **Recognition cannot stop in its first ~20 seconds.** The decoder scans the
  whole file before yielding a segment (21 s on an 81-minute lecture).
- **The volume slider does not affect the recording on PipeWire.** The monitor
  is tapped before volume control. WASAPI is unchecked.
- **Windows: several paths are unrun.** Keep-awake, the window refresh, the
  language choice and the device re-read have only been run on Linux.

## Fixed

Newest first. Commit hashes are on `main`.

### 2026-11-10, "Wrap transcripts by screen columns" (B-10)

- **B-10**: transcripts were wrapped at 80 *characters*, but a Korean
  syllable takes two columns, so lines ran to 107-137 columns in a terminal.
  `format_text` now wraps by display width (`unicodedata.east_asian_width`).
  English text wraps byte for byte as before. Re-wrapping two real Korean
  transcripts: widest line 137 -> 80 columns, 280 -> 434 and 144 -> 234
  lines. Transcripts already on disk change only when recognised again.
  Commit: see the log.

### 2026-11-10, "Take back the folder of a failed start" (B-8, B-9)

- **B-8**: a recording that failed to start (an unplugged device, no
  loopback input) left its empty lecture folder behind, and the next attempt
  went into `<name>-2`. `remove_if_empty` removes the folder, and only when
  it is empty, so audio is never touched (D-7). Both front ends call it. A
  failure after the WAV exists leaves the header-only file and its folder.
- **B-9**: `notify-send` on the PATH that would not start raised `OSError`
  out of `keep_awake`, and the recording never began. It is now ignored like
  a timeout. Commit: `5a5ff29`.

### 2026-11-10, "Load a cached model without touching the network" (B-7)

- **B-7**: `load_model` never passed `local_files_only`, so faster-whisper
  asked Hugging Face on every load even with the weights on disk, against
  the promise that nothing leaves the machine. Measured by counting
  `socket.connect` calls during a load of the cached large-v3: one to port
  443 before, none after. `load_model` now passes `local_files_only` when
  `model_status` finds the weights, and a cached model that will not load
  says its folder may be incomplete. Commit: `e5ef79b`.

### 2026-11-09, "Fix what the third review found" (B-1 to B-6)

Found in
[`reviews/2026-11-09-review-fixes-and-language.md`](reviews/2026-11-09-review-fixes-and-language.md).

- **B-1** (medium): a wrong language code was caught only after model load
  plus a full decode and VAD scan, once per file. `check_language` now
  compares it with `model.supported_languages` right after loading, once
  per batch in the CLI and before the read in the window. It also refuses
  `ko` on an English-only model, which faster-whisper would have quietly
  switched to `en`. Measured: `scribe text --language kr` fails in 11 s,
  once, against real large-v3.
- **B-2**: the same file named twice was reported as a folder clash and
  refused. `scribe text` now drops repeats by resolved path and keeps the
  first spelling.
- **B-3**: opening the device list said "press record again" though record
  was not pressed. That advice now appears only on the record path.
- **B-4**: the close prompt during recognition defaulted to Yes. It now
  defaults to No, like the recording prompt.
- **B-5**: a recording stopped before its first block was offered for
  transcription. The window now says there is nothing to transcribe. The
  CLI says no audio arrived (instead of "silent from end to end") and exits
  130. The empty file is kept in both.
- **B-6**: `README.md` said the language was fixed to Korean.

### 2026-11-09, `434e636` (second review)

- Unknown language code or undecodable audio (`ValueError`) crashed the CLI
  with a traceback. In the window it showed "ended without saying why". It
  is now a `TranscriptionError`.
- Closing the window during a recording stopped it without asking. It now
  asks, with No as the default.
- Closing while the model loaded left a hidden process that went on to scan
  the whole recording. The worker now checks the stop right after loading.
- `scribe text` on several WAVs in one folder wrote every transcript over
  the last. They are now refused before the model loads.
- The device list was read once at startup, so headphones plugged in later
  were never recorded. It is now re-read on dropdown and on record.
- `config.toml` accepted `sample_rate = 0`, `beam_size = 0`, negative
  paragraph lengths and `silence_rms` outside 0..1. Each is now a
  `ConfigError`.
- A recording that failed before capturing anything stayed offered for
  transcription. (The stop-before-first-block case remains; see B-5.)
- Ctrl+C between folder creation and recording start gave a traceback and
  left an empty folder.

### 2026-10-27, `863c355` (first review)

- The window counted as idle once a worker sent its result, while the
  thread was still freeing the model. A press in that gap replaced a running
  `QThread` (process abort), and the stale `finished` released the next
  job's keep-awake.
- `scribe text` hit a non-audio file as a PyAV traceback mid-batch. Files
  are now checked before the model loads.
- A full disk left a cut-off transcript that read as complete. Transcripts
  are now written to `.partial` and renamed into place.
- A failed or silently dead recognition left the meter showing progress.

### 2026-09-27, `5ec491a` and `e62f0f9`

- Desktop entry tests failed on Windows: `Path` turned `/home/u` into
  `\home\u`.
- No icon on Windows in the Start Menu or the taskbar: `.lnk` cannot use
  SVG, and the window belonged to `pythonw.exe`.

### 2026-09-10, `eadc2b7` (the 81-minute lecture)

- Closing during recognition aborted the process (exit 134, `QThread`
  destroyed while running).
- Marks pressed during a pause were dropped.
- The silence check ran once, ten seconds in. An output device that changed
  mid-lecture recorded an hour of silence with no warning.
- A lecturer who never pauses came out as 24 paragraphs, one of them 15
  minutes long. `paragraph_target` and `paragraph_max` fixed it.

### 2026-09-10, `5be94c7`

- A full disk or unplugged device mid-recording was an uncaught exception.
  The CLI printed a traceback. The window kept showing a stop button and a
  frozen clock, and the next press started a second recording. Partial
  recordings are now kept and reported.

### 2026-09-10, `061a01f`

- Recognition showed one motionless line for the whole run, because the
  segment generator was read into a list in one go.
