# lecture-scribe

Personal tool for one user: it records a Korean lecture playing on this
computer and turns it into a readable `.txt` transcript with timecodes.
Everything runs locally. A command line and a small desktop window over
one core.

## Working agreement

- Answer in whatever language the user's message is written in (so far:
  Russian, sometimes English).
- **Code, identifiers, docstrings, comments and console output are in English.**
- Work proceeds step by step. Finish a step, say what to verify by hand, then
  **stop and wait for confirmation**. Never start the next step unasked.
- The decisions below are settled. Say so in prose if one looks wrong, but do
  not change it in code without being told to.

## Settled decisions

- **No real time.** Record the whole WAV first, recognise afterwards. Batch
  decoding is more accurate because the model sees whole sentences.
- **No speaker diarization, no translation, no summarisation, no cloud ASR.**
  One voice, one language, one machine.
- **The window is a second front end, never the only one.** `cli.py` and
  `gui.py` are peers: everything the window does stays reachable from the
  command line, and neither holds logic the other needs.
- **The window is dark, greys plus two signal colours**, changed from greys
  only on 2026-09-28. Red means a recording is live, amber means a mark.
  Colour carries meaning, never decoration, and there is no light theme.
  Qt's Fusion style is forced on both platforms, since the native styles are
  too far apart for one stylesheet.
- **Audio files are never deleted or overwritten automatically.** A recording
  cannot be recreated; recognition can be repeated as often as wanted.
- **Do not swap `soundcard` for `sounddevice`** — `WasapiSettings` has no
  loopback parameter, so it cannot do the job.
- **Do not add dependencies** beyond `soundcard`, `soundfile`, `numpy`,
  `faster-whisper` and `PySide6-Essentials` without asking first. The one
  exception, agreed on 2026-09-10: `nvidia-cublas-cu12` as the optional
  `cuda` extra, never a plain dependency -- recording a lecture needs none
  of it and it costs a gigabyte.
- **Transcripts reach Google Drive through the external `rclone` program**,
  agreed on 2026-09-28, the same way notifications use `notify-send`: no
  Python dependency, and the OAuth token lives in rclone's own config, never
  in this project. Only the `.txt` is uploaded, never the WAV, and only when
  `[upload]` names a remote. A failed upload is a warning, never an error:
  the transcript is already safe on disk.
- No abstractions written for a future that has not arrived: no plugins, no
  factories, no base classes with a single implementation.
- No blanket `try/except` to keep things from crashing. Catch named
  exceptions and say what to do next.
- Capture and recognition must stay runnable independently: recording on a
  laptop without the model installed, transcribing later or elsewhere.
- **No search index**, dropped on 2026-09-28. The transcripts are plain
  `.txt` and `grep` finds a word with its timecode in 6 ms across 39 of
  them. SQLite FTS5 would search Korean worse than that: its tokenizer
  splits on spaces, so `프로토콜의` is not `프로토콜`, and the trigram
  tokenizer needs three characters where most words here have two.

## Layout

```
audio_capture.py   system audio -> WAV on disk, plus device discovery
transcribe.py      WAV -> list[Segment], plus model and GPU readiness
format_text.py     segments -> transcript text (pure)
config.py          config.toml -> frozen dataclasses
cli.py             argument parsing, printing and wiring, nothing else
gui.py             the desktop window (Qt), wiring and drawing, nothing else
launcher.py        desktop menu entry, both platforms
keep_awake.py      keep the desktop from locking or sleeping while working
upload.py          transcript -> Google Drive via rclone, plus doctor probe
```

`format_text.py` never touches the disk. Neither `cli.py` nor `gui.py` holds
logic of its own: each module reports about its own domain, and the front end
prints it or draws it.

## Conventions

- Full type annotations; `mypy --strict` must pass.
- Google style docstrings on every module, class and public function.
- Inline comments explain **why**, never what. `# increment counter` is noise;
  `# two channels because WASAPI returns garbage for one` is not.
- Paths are always `pathlib.Path`. This is cross-platform correctness, not
  style: the tool has to run on Linux and on Windows from one codebase.
- No global mutable state.
- Tests cover pure logic and edge cases. Anything needing real audio hardware
  or a downloaded model is marked `@pytest.mark.manual` and skipped by default.

## Commands

```bash
uv sync                      # environment, managed Python 3.12
uv sync --extra cuda         # ... plus cuBLAS, for asr.gpu = true
uv run ruff format . && uv run ruff check .
uv run mypy
uv run pytest                # manual tests excluded
uv run pytest -m manual      # needs hardware or the model
uv run scribe doctor         # what the tool can see on this machine
uv run scribe gui            # the window; scribe-gui skips the console on Windows
scribe launcher              # put the window in the application menu (--remove undoes)

uv tool install --editable ".[cuda]" --force   # the scribe on the PATH, GPU included
```

## Non-obvious facts, verified on this machine

- **Find the loopback input by exact identifier, never by name.** PulseAudio
  calls it `<output id>.monitor`; WASAPI reuses the output device's own id.
  The user's USB microphone has a headphone jack, so it appears twice under
  one name — as an output and as a real microphone. `soundcard`'s documented
  name matching returns whichever comes first in the list, which would
  silently record the microphone.
- **Record two channels even though one is written.** Asking WASAPI for a
  single channel returns garbage. Mix down with `np.mean(block, axis=1)`;
  summing clips past 1.0.
- **The PipeWire monitor is tapped before the volume control.** Measured: the
  recorded level is identical at 40%, 30% and 0% system volume. WASAPI has not
  been checked and may differ.
- A killed process still leaves a valid WAV: streaming through an open
  `soundfile.SoundFile` keeps the header in step with the samples. Verified
  with `kill -9`.
- `soundcard` raises `IndexError` on import under `python -c "..."` with no
  extra arguments — its own bug in program name detection. Harmless for the
  CLI, annoying when probing by hand; pass a dummy argument.
- `config.toml` is read from the current directory only, and unknown keys are
  an error rather than a silent default. This is why the desktop entry pins
  `Path=`: a menu starts a program from the home directory, where the window
  would silently run on the defaults. `scribe launcher` therefore writes
  `Path.cwd()`, the same directory the CLI itself read its config from.
- **tkinter cannot be the GUI here, which is why Qt is a dependency.** The Tk
  bundled with uv's managed CPython is built without Xft or fontconfig:
  `libtcl9tk9.0.so` has no `Xft`/`Fc` symbols, `tkinter.font.families()`
  returns only `fixed`, and `한국어 강의` measures 6 px, i.e. it does not
  render. The system Python has no `tk` installed at all. Qt sees 846
  families on the same machine and measures the same string at 59 px.
- Qt stops drawing a combo box's native arrow as soon as `::drop-down` is
  styled at all, and the CSS border-triangle trick renders as a rectangle.
  `_Combo` paints the arrow itself.
- `QFont.setFeature` takes a `QFont.Tag`, not a string, whatever the stubs
  say: they allow `str` and mypy passes, but PySide 6.11 raises `ValueError`
  on `setFeature("tnum", 1)` at runtime.
- In Qt rich text a `<td width="68">` is only a hint: rows of different
  lengths came out with different gutters. The transcript pane gives the
  text cell `width="100%"` and the timecode cell `white-space: nowrap`,
  without which Qt breaks `00:47` one character per line.
- The pane copies the file's own text on select all, not what it shows: a
  table copies one cell per line. `_Transcript.createMimeDataFromSelection`.
- Qt stylesheets have no `letter-spacing`; it only exists as
  `QFont.setLetterSpacing`, hence `_track()`.
- Frozen dataclasses cross `QThread` signal boundaries intact and arrive on
  the GUI thread. `Progress`, `Recording` and `Transcription` are passed
  whole rather than unpacked into primitives.
- **A transcript's title line is the folder name as it was when the
  transcript was made.** Renaming a lecture folder afterwards leaves the old
  name in the header of the `.txt` -- both recordings in `~/lectures` on this
  machine still say `2026-09-10_os` inside, under folders since renamed to
  lecture titles.
- Marks belong to the recording they were pressed during, never to a file
  opened afterwards. `_open_recording` clears them, or `>>>` would land on
  unrelated sentences.
- **`model.transcribe()` hands back its info object before it decodes a
  single segment**, so the length of the audio is known up front and real
  progress can be shown rather than a spinner. The segments themselves are a
  generator: iterating it is what does the decoding.
- VAD restores segment timestamps to positions in the original audio, so a
  segment's end is a true position in the lecture and can be compared with
  `info.duration`. It can land a shade past it, hence the clamp in
  `Decoding.fraction`.
- A recording that dies part way through returns rather than raises: leaving
  the `with` closes the WAV, so the audio already captured stays valid, and
  `Recording.interrupted` carries the reason. Only a recording that never
  started at all is an `AudioDeviceError`.
- **A `QThread` whose `run()` raises emits none of its own signals, but
  `finished` arrives either way.** The window therefore clears its recording
  state in the `finished` slot rather than only in the result slots. Before
  that, an unreported death left it showing a stop button and a frozen clock,
  with the status line still saying `ready` -- and the next press started a
  second recording into a new folder.
- Wayland pairs a window with its menu entry by name, not by process: the
  entry's `StartupWMClass` and `QApplication.setDesktopFileName` both say
  `launcher.DESKTOP_ID`, or the window appears without its icon.
- **On Windows the window belongs to `pythonw.exe`, not to `scribe-gui.exe`.**
  uv's `scribe-gui.exe` is a trampoline that starts Python and waits, so
  without `SetCurrentProcessExplicitAppUserModelID` the taskbar files the
  window under Python and shows Python's icon. A `.lnk` cannot take an SVG
  and the trampoline carries no icon resource, hence the `.ico` beside the
  SVG, rendered from it.
- `Categories=` takes exactly one main category. `AudioVideo;Audio;Utility;`
  is valid but lists the window twice in the menu; `desktop-file-validate`
  reports it as a hint rather than an error.
- A `.lnk` is a binary format, so the Windows shortcut is written by
  PowerShell's `WScript.Shell` rather than by hand. That keeps a Start Menu
  entry from costing a dependency.
- **Noctalia serves `org.freedesktop.ScreenSaver` and obeys it.** Its log
  reads `idle behavior 'lock' suppressed (screensaver inhibit locks=1)`, and
  the same for `screen-off` and `suspend`. The idle config on this machine
  suspends after 25 minutes, which would end a recording or a CPU
  transcription -- hence `keep_awake` around both, in both front ends.
- **`UnInhibit` cannot be called from PySide6.** The cookie goes back as a
  signed `i` where `u` is wanted, and QtDBus refuses numpy and ctypes
  integers outright. `keep_awake` inhibits over a private named connection
  and closes it to let go -- which is what the desktop also sees on
  `kill -9`; Noctalia logs `cleared 1 inhibit(s) after client disconnect`.
- `QDBusConnection.disconnectFromBus` closes nothing while a Python
  `QDBusConnection` for that name is still alive: the inhibit stayed in force
  until the process exited. Hence the `del bus`.
- QtDBus works in the CLI with no `QCoreApplication` and no event loop.
- **Notifications go through `notify-send`, not QtDBus.** Noctalia refuses
  `Notify` from PySide6 with `Invalid arguments 'sisssava{sv}i' ... expecting
  'susssasa{sv}i'`: the unsigned id goes out signed and the empty string array
  as a variant array, and `QDBusInterface` does not convert them either.
  `notify-send` 0.8.8 takes 7 ms, so it runs synchronously.
- **`upload.remote` must contain a colon.** Without one rclone reads the
  destination as a local path. Measured: `rclone copyto x.txt
  gdrive/Lectures/x.txt` exits 0 and leaves `./gdrive/Lectures/x.txt` in the
  current directory. `config.py` refuses such a remote for that reason.
- **The rclone remote uses scope `drive.file` and a client ID of the user's
  own.** rclone's shared client ID is being retired during 2026 (its own
  docs, and its config prompt says so). The Google app is published, not
  left in testing, where the sign-in expires after seven days.
- **With `drive.file` rclone sees only what it created.** Measured:
  `rclone lsd gdrive:` listed nothing on a Drive holding 67 GB, until
  `rclone mkdir gdrive:Lectures`. A folder made by hand in the browser is
  therefore invisible to it, and an upload would create a second folder of
  the same name beside it.
- `rclone copyto` creates every missing folder on the way to the
  destination, and Hangul names and contents arrive intact (read back with
  `rclone cat`).
- **The window uploads before it reports the transcript, not after.** The
  `transcribed` slot frees the buttons; a second transcription started
  while the first thread still uploaded would replace a running `QThread`,
  which aborts the process.

## Recognition settings that must not drift

- `language="ko"` fixed. Auto detection confuses Korean with Japanese on the
  first seconds and then the whole file goes wrong.
- `vad_filter=True`, `vad_parameters={"min_silence_duration_ms": 500}`.
- `condition_on_previous_text=False`. On a 90 minute file one error otherwise
  drags chained repetitions to the end.
- `compute_type`: `int8_float16` on GPU, `int8` on CPU. Full float16 large-v3
  wants about 10 GB of VRAM; this machine has 8 GB. Measured: `int8_float16`
  peaks at 3365 MiB on the 81 minute lecture, so the headroom is real.

## Non-obvious facts about speed and the GPU, measured on this machine

- **The decoder scans the whole file before yielding a segment.** 21 seconds
  on the 81 minute lecture: `model.transcribe()` decodes the audio and runs
  the VAD before it returns, and only then does iterating produce segments.
  A stop event can therefore do nothing for the first half minute, which is
  why the front ends say "reading the recording ..." rather than showing a
  motionless progress line.
- **Whisper emits segments back to back.** On ten minutes of the 컴넷
  lecture, 92 of the 97 gaps between segments were zero or negative and the
  largest was 2.0 seconds. Silero's `speech_pad_ms` of 400 shortens every
  gap by up to 0.8 s on top of that. So `paragraph_gap` alone cannot break
  up a lecturer who does not pause -- hence `paragraph_target`.
- **Only 18% of segments end on sentence punctuation**, the same on CPU and
  GPU. That is the supply `paragraph_target` has to work with, and why
  `paragraph_max` exists as the fallback.
- **A `QThread` destroyed while still running aborts the process** (exit
  134, `QThread: Destroyed while thread is still running`). The window
  therefore hides itself and quits from the worker's `finished` slot rather
  than waiting on the GUI thread.
- **`ctranslate2` 4.8.2 needs cuBLAS and nothing else.** Its shared object
  names `libcublas.so.12` and carries no cuDNN reference at all, whatever
  the faster-whisper documentation says. That halves what the `cuda` extra
  has to install.
- **The CUDA wheels are not on the loader's path.** They unpack into
  `site-packages/nvidia/*/lib`, where a bare `dlopen` never looks, so
  `transcribe._preload_cuda_libraries` loads them by full path first. Two
  passes, because they carry no RUNPATH and `libcublas` needs `libcublasLt`.
- **A checkout and a `uv tool install` are separate environments.** The
  `scribe` on the PATH is the second one, and installing the extra into the
  first changes nothing for the window. `scribe doctor` prints `sys.prefix`
  for exactly this reason.
- Measured on the RTX 4060 Laptop: 21x realtime, the 81 minute lecture in
  3.9 minutes against 23 on the CPU. Same words -- 95.4% agreement by word
  with the CPU transcript, the differences being spacing and dropped full
  stops.

## Roadmap

| step | contents | state |
|---|---|---|
| 0 | skeleton, config, `scribe doctor` | done |
| 1 | `audio_capture.py`, `scribe rec` | done |
| 2 | `transcribe.py`, `scribe text`, flat output | done |
| 3 | `format_text.py`: paragraphs, `[MM:SS]` timecodes, `>>>` markers | done |
| G | `gui.py`, `scribe gui`: record and transcribe in one window | done |
| L | `launcher.py`, `scribe launcher`: menu entry on both platforms | done — verified on Linux and on Windows 11 |
| A | `keep_awake.py`: no lock or suspend while recording or transcribing, announced by a notification | done — verified on Linux against Noctalia; the Windows branch is unrun |
| U | `upload.py`: transcripts to Google Drive through rclone, `[upload]` in config, doctor section, notification | done — verified on Linux, CLI and window; the Windows branch is unrun |
| R | window refresh: filled rounded surfaces, state chip, signal colours, readable transcript pane | done — verified offscreen on Linux; not yet looked at live, nor on Windows |
