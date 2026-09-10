# lecture-scribe

Personal tool for one user: it records a Korean lecture playing on this
computer and turns it into a readable `.txt` transcript with timecodes.
Everything runs locally. CLI only.

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
- **No GUI, no speaker diarization, no translation, no summarisation, no cloud
  ASR.** One voice, one language, one machine.
- **Audio files are never deleted or overwritten automatically.** A recording
  cannot be recreated; recognition can be repeated as often as wanted.
- **Do not swap `soundcard` for `sounddevice`** — `WasapiSettings` has no
  loopback parameter, so it cannot do the job.
- **Do not add dependencies** beyond `soundcard`, `soundfile`, `numpy` and
  `faster-whisper` without asking first.
- No abstractions written for a future that has not arrived: no plugins, no
  factories, no base classes with a single implementation.
- No blanket `try/except` to keep things from crashing. Catch named
  exceptions and say what to do next.
- Capture and recognition must stay runnable independently: recording on a
  laptop without the model installed, transcribing later or elsewhere.

## Layout

```
audio_capture.py   system audio -> WAV on disk, plus device discovery
transcribe.py      WAV -> list[Segment], plus model and GPU readiness
format_text.py     segments -> transcript text (pure)
config.py          config.toml -> frozen dataclasses
cli.py             argument parsing, printing and wiring, nothing else
archive.py         SQLite FTS5 index (step 5, not written yet)
```

`format_text.py` never touches the disk. `cli.py` holds no logic of its own:
each module reports about its own domain and the CLI prints.

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
uv run ruff format . && uv run ruff check .
uv run mypy
uv run pytest                # manual tests excluded
uv run pytest -m manual      # needs hardware or the model
uv run scribe doctor         # what the tool can see on this machine
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
  an error rather than a silent default.

## Recognition settings that must not drift

- `language="ko"` fixed. Auto detection confuses Korean with Japanese on the
  first seconds and then the whole file goes wrong.
- `vad_filter=True`, `vad_parameters={"min_silence_duration_ms": 500}`.
- `condition_on_previous_text=False`. On a 90 minute file one error otherwise
  drags chained repetitions to the end.
- `compute_type`: `int8_float16` on GPU, `int8` on CPU. Full float16 large-v3
  wants about 10 GB of VRAM; this machine has 8 GB.

## Roadmap

| step | contents | state |
|---|---|---|
| 0 | skeleton, config, `scribe doctor` | done |
| 1 | `audio_capture.py`, `scribe rec` | done |
| 2 | `transcribe.py`, `scribe text`, flat output | done |
| 3 | `format_text.py`: paragraphs, `[MM:SS]` timecodes, `>>>` markers | done |
| 4 | `--then-text`, important-moment hotkey, free space check | |
| 5 | `archive.py`, SQLite FTS5, `scribe find` | |
