# Review: second review fixes and the language choice

- **Date:** 2026-11-09
- **Range:** `92d1dd6..a55dc6b` (merges `review-fixes` and `language-choice`)
- **Commits:** `434e636` Fix what the second review found,
  `7a4d025` Let the lecture language be chosen, `a55dc6b` mark as checked
- **Checks:** `ruff check` clean, `mypy --strict` clean, 148 tests pass
  (2 manual deselected)

**Resolution:** B-1 to B-6 were fixed the same day in "Fix what the third
review found". See [`../bugs.md`](../bugs.md). The notes below were left
as they are.

## Verdict

Good to keep. The fixes do what their commit message says, each has a test,
and the window has offscreen tests for the first time. Nothing found is
severe. One finding is worth fixing soon (B-1: a mistyped language code is
only caught after the slow part of recognition); the rest are small.

## Findings

Bug ids refer to [`../bugs.md`](../bugs.md).

| id | severity | where | finding |
|---|---|---|---|
| B-1 | medium | `cli.py`, `gui.py`, `transcribe.py` | A wrong language code fails only after the model has loaded and the whole recording has been decoded and VAD-scanned, once per file |
| B-2 | low | `transcribe.shared_transcripts` | The same file given twice is reported as two recordings sharing a folder, and refused |
| B-3 | low | `gui._load_devices` | "press record again" appears when the device list is merely opened |
| B-4 | low | `gui.closeEvent` | Closing during recognition: Enter answers Yes, unlike the recording prompt |
| B-5 | low | `gui._on_recorded` | A recording stopped before its first block is still offered for transcription |
| B-6 | low | `README.md:49` | Still says "The language is fixed to Korean" |

### B-1. A wrong language code is caught late (medium)

`asr.language` and `--language` are taken as any string. faster-whisper
checks the code in `Tokenizer.__init__`, which `WhisperModel.transcribe`
reaches only after `decode_audio` and the VAD pass
(`faster_whisper/transcribe.py:876` vs `:963`). So `scribe text --language kr`
(`kr` is the country, `ko` the language) loads large-v3, decodes and scans the
whole lecture, then fails. With several files it does that once per file,
three full scans for three identical errors. The error text also lists all
~100 accepted codes.

The same check would catch an English-only model (`small.en`) given `ko`.
faster-whisper silently switches that to `en` with only a log warning.

**Suggested fix:** right after `load_model`, check
`language in model.supported_languages` (a public property, and `["en"]` for
`.en` models) and fail once, before any file, with one short line. Checking
before the model loads would need `faster_whisper.tokenizer._LANGUAGE_CODES`,
which is private, and `config.py` must not import faster-whisper.

### B-2. The same file twice reads as a folder clash (low)

Verified:

```
$ scribe text 2026-11-09_test.wav ./2026-11-09_test.wav
error: 2026-11-09_test.wav, 2026-11-09_test.wav would all be transcribed to
.../2026-11-09_test.txt, each over the one before; give each recording a
folder of its own
exit 1
```

Overlapping globs (`scribe text */*.wav 2026-11-09*/*.wav`) produce this.
**Suggested fix:** drop exact duplicates (by resolved path) before
`shared_transcripts`, keeping the first.

### B-3. Wrong advice when the device list opens (low)

`_load_devices` ends with "'X' is gone, so the list is back on 'Y'; press
record again to use it". That wording fits the record path. It is also
reached from `_on_device_list_opening`, where no record was pressed.
**Suggested fix:** let the caller word it, or drop the second clause when
called from the list.

### B-4. The close prompt during recognition defaults to Yes (low)

The new recording prompt passes `defaultButton=No`. The older recognition
prompt passes nothing. Checked offscreen: with no default, focus lands on
**Yes**, so Enter throws away the recognition in progress. Recognition can
be repeated, so it costs time rather than data. It is still inconsistent
for a one-key slip. **Suggested fix:** `defaultButton=No` there too.

### B-5. An empty recording stays offered (low)

`434e636` stopped offering a recording that never captured a block, but
only on the `failed` path. A stop pressed before the first 64 ms block
returns normally (`frames == 0`, no error), goes through `_on_recorded`,
and leaves `transcribe` enabled on a header-only WAV. The CLI has the same
window between the `stop.is_set()` check and `record_loopback`, where it
prints "silent from end to end" for a 0:00 file. Rare either way.
**Suggested fix:** in `_on_recorded`, treat `recording.duration == 0` like
the failed path.

### B-6. README contradicts itself on language (low)

`README.md:49`, under *What it does*, says "The language is fixed to
Korean". Line 169 and the troubleshooting section describe the new Korean /
English choice. **Fix:** reword to "Korean by default, English on request,
never guessed".

## Notes, not bugs

- `transcribe()` now catches `ValueError` around the whole loop, including
  the `on_progress` callback. A `ValueError` from a front end's own progress
  code would be reported as "cannot transcribe \<wav\>". It is harmless
  today because neither callback raises one. If it ever confuses, move the
  callback outside the `try`.
- `shared_transcripts` guards one batch only. A second WAV in the same
  folder, transcribed in a later run or opened with Ctrl+O, still writes the
  same `<folder>.txt`. That overwrites a transcript, not audio, so the
  settled rule holds. It is listed in `bugs.md` as a known limitation.
- `_load_devices` now runs `list_loopback_devices()` on the GUI thread on
  every dropdown and every record press. It is fast on PipeWire. WASAPI
  enumeration has not been timed.
- The Windows side of every change here is unrun: the device re-read, the
  close prompt, the language field.

## What was checked and holds up

- Config bounds: `paragraph_target = 0` and `paragraph_max = 0` are accepted
  and mean "off" (`format_text.py:226-227`), so `minimum=0.0` is right.
- `scribe rec` Ctrl+C before recording: `rmdir` can only remove an empty
  folder, so no audio can go with it. The settled rule holds.
- Device re-read: `activated` (not `currentIndexChanged`) keeps a refill from
  counting as a hand pick, and a picked device that vanished stops the
  recording rather than silently switching.
- Stop after model load: the worker checks the stop as soon as `load_model`
  returns, before the 21-second scan.
- Language: `merge_cli` is the one path for both front ends. Nothing is
  written back to `config.toml`. A configured code outside Korean/English is
  added to the list rather than dropped. An empty string does **not** fall
  through to auto-detection: faster-whisper only detects on `None`, and
  rejects `""` as an invalid code.
