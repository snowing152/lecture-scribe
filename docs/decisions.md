# Decisions

Each decision with its date, its reason, and what it ruled out. The binding
list is *Settled decisions* in [`../CLAUDE.md`](../CLAUDE.md). This file is
the history behind it: why each choice was made, so it is not reopened by
accident. A reversed decision stays here with a note saying what replaced it.

Format: **D-n · date · status**, then the decision, why, and what was
rejected.

---

### D-1 · 2026-09-10 · active, relaxed by D-23: Record first, recognise afterwards

No real-time transcription. The whole WAV is recorded, then decoded in a
batch.
**Why:** batch decoding sees whole sentences and is more accurate. It also
keeps capture and recognition separable (D-2).
**Rejected:** streaming ASR.

### D-2 · 2026-09-10 · active: Capture and recognition run independently

Recording needs neither the model nor `faster-whisper`. Transcribing can
happen later or on another machine.
**Why:** record on a laptop without a GPU or the model, decode elsewhere.

### D-3 · 2026-09-10 · local only still active; the rest reversed by D-23: Local only, one voice, one language

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

### D-9 · 2026-09-10 · active, refined by D-21, D-25 and D-26: Recognition settings

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

### D-23 · 2026-11-09 · active: Real time, speakers, translation and summaries are open, if local

Opened at the user's request while writing `CONTRIBUTING.md`. A real-time
mode may be added beside the batch path (D-1 stays the default, D-2 still
holds). Speaker diarization, translation and summarisation may be added
(reverses that part of D-3). None of them is built.
**Why:** the user saw no reason to refuse them, as long as nothing leaves the
machine.
**Still rejected:** cloud ASR, upload, telemetry; real time replacing batch;
any of these as a new dependency without asking first (D-22).

### D-24 · 2026-11-10 · active: Marks are saved in a file beside the recording

A mark used to live only in the window's memory, so closing the window, or
transcribing the recording again, lost it. Each mark is now appended to
`<recording>.marks` when pressed, and both front ends read it.
- **File:** the WAV's own name with `.marks`, so the pair survives a renamed
  folder. Plain text, one offset in seconds per line, editable by hand.
- **Code:** `audio_capture.py`, since marks belong to the recording and
  recording must work without the model (D-2).
- **A line that is not a non-negative number is an error**, as an unknown
  config key is (D-8): a mark that silently went missing leaves nothing in
  the transcript to show it.
- **Append only:** the file is never deleted or rewritten automatically (D-7).
**Not done:** pressing marks from `scribe rec`; it needs raw keyboard reading
in a terminal on two platforms. A line can be added by hand.

### D-25 · 2026-11-10 · active: Terms of the course go in as `hotwords`

`asr.hotwords` (and `scribe text --hotwords`) hands the decoder a list of
terms to lean towards. Empty by default, so nothing changes unless it is set.
- **`hotwords`, not `initial_prompt`:** faster-whisper adds hotwords to the
  prompt of every decoding window; an initial prompt reaches only the first
  once the previous text is off (D-9). Checked in the installed code.
- **Not part of D-9's fixed settings:** it is a setting of the course, like
  the language (D-21), so the window reads it from `config.toml` and has no
  field for it.
**Measured once** on 10 minutes of one lecture: technical terms came out
right, a little filler was cut, segments halved. See `CLAUDE.md`. Anyone
setting it should compare on one lecture first.

### D-26 · 2026-11-26 · active: Skip hallucinations between pauses, drop hotword echoes

Two failures in the 컴넷 6-2 lecture, both over audible speech: from 28:07
the decoder counted "1, 2, 3 ... 59" for about 80 seconds, and on unclear
audio it wrote out the hotword list itself.
- **`word_timestamps=True` with `hallucination_silence_threshold=2.0`** is
  added to D-9's fixed settings. Compared on eight minutes around the fault,
  against the current settings, a longer hotword list, `repetition_penalty`
  1.1 and a stricter VAD: only this one removed both the counting and the
  echo, and on a clean eight minutes from 5-2 it changed nothing for the
  worse. The stricter VAD produced Greek and Turkish fragments instead.
  Confirmed on the whole of 6-2.
- **Echoes are dropped after decoding**, since the silence threshold missed
  one that was not between pauses: a segment of five words or more, 80% of
  them hotwords. Not 100%, because the echo merges neighbouring terms.
- **Cost accepted by the user:** decoding takes twice as long, 5.0 minutes
  for 50 minutes on the GPU. Retries on bad audio are what it pays for.
Measured once, on one lecture. The counting fault itself is fragile: cut
from the file at a different offset, the same audio decoded cleanly.

### D-27 · 2026-11-27 · active: The window queues recordings and recognises them one by one

The user sat waiting for each transcription before recording the next
lecture. Recording needs almost no processor and decoding runs on the GPU,
so the two now overlap in the window. Settled with the user one by one:
- **The window only.** The CLI already did this with two terminals, or with
  `scribe text <folder>` once at the end (step F).
- **A recording is queued the moment it stops**, unless it is silent;
  **transcribe** queues the recording in view, such as one opened.
- **One after another, one model for the whole queue.** Two copies would fit
  in 8 GB but decode slower each and leave little margin.
- **The language is fixed when a recording is queued.** The course, device
  and language fields are locked only while recording.
- **Recognition gets a line of its own** under the status line; the clock,
  meter and pane belong to a recording whenever one runs.
- **stop clears the whole queue**; closing asks first and drops it. The
  recordings stay on disk for `scribe text`.
- **No automatic queue of old recordings at startup**, and no notification
  when a transcript is ready, for now.
- **A failed recording does not end the queue**, except a GPU that cannot
  decode or a model that cannot load, which would fail every one in turn.

### D-28 · 2026-11-27 · active: Models are offered by name, with a note on each

The user asked for a Korean model, and for the window to say which model
suits what. Measured first, on the user's own lectures rather than on the
read speech the model cards report:
- **Four candidates on three eight minute slices:** `large-v3`,
  `BuzzASR/korean` (a large-v3 fine-tune, MIT, converted once with
  `uvx` so that PyTorch never enters the project), `ghost613`'s Korean
  turbo and plain `large-v3-turbo`. The last two were dropped: one invents
  news, the other loops and spells English terms in Hangul.
- **Then whole lectures:** `large-v3` on all nine, `buzz-korean` on four
  (2-1, 2-2, 5-1, 6-2) before the user stopped the rest for time.
  `buzz-korean`: 4-15% more words, 20-40% more English terms in Latin, no
  counting, 1.5-1.9 times slower. Both still make mistakes of their own.
- **`[models.<label>]` tables** in `config.toml`, each a `model` (name or
  directory) and a `note`. The label is what `asr.model`, `--model`, the
  window's list and the transcript header use; `config.model_source`
  resolves it.
- **The model is fixed when a recording is queued**, like the language
  (D-27). The worker keeps a model for as long as consecutive recordings ask
  for it, and lets it go before loading another: two would crowd 8 GB.
- **The user's own `config.toml` now names `buzz-korean`**; the built-in
  default and `config.example.toml` stay `large-v3`, since the Korean model
  has to be converted by hand first.

### D-29 · 2026-11-27 · active: Whisper's counting is cut from the text

D-26 did not hold: with the hotword list of 2026-11-27, `large-v3` counted
"3, 4, 5 ... 59" over a minute of 컴넷 6-2 again, inside a run of speech the
silence threshold cannot see. A run of ten numbers or more separated by
commas or full stops is now cut from the segment, and the rest kept, since
that segment began and ended in the lecturer's words. Ten, because a
lecturer listing scenarios or packets says five or six.

### D-30 · 2026-11-27 · active: The window is redesigned around the queue

The window had grown one control at a time down a single column. Three
directions were mocked up as interactive pages; the user chose the one with
the queue as a pipeline, and its colours after Monokai Pro. Settled with the
user point by point:
- **Layout:** a bar of options (course, device, language and model as pills,
  the model's note beside it), a recording strip (record button that pulses
  while live, clock, the last ten seconds of real levels, mark), the status
  line, then the queue as three columns: Waiting, Transcribing, Transcripts.
  Below 900 px the columns stack.
- **Colours** each mean one thing: yellow what can be pressed or is in
  progress, pink-red a live recording, orange a mark, green done. Dark only.
- **No live text while decoding**: the user rarely reads transcripts, so the
  card shows progress, and a transcript is read in a view that takes the
  place of the columns, with a way back.
- **Animations** run only while something moves: a card unfolds and fades in
  where it arrives, the progress stripes move while a recording is decoded,
  the record button pulses while live. An idle window redraws nothing.
- **System fonts** only; no font files in the repository.
- **Step 2, still to come:** the Transcripts column lists every lecture in
  the folder with search, recordings without a transcript get a Transcribe
  button, marks become jump links in the reading view, and `scribe list`
  prints the same list from a new `library.py`.

