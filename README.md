<h1 align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./.github/assets/lecture-scribe-dark.png">
    <img src="./.github/assets/lecture-scribe-light.png" width="420" alt="lecture scribe">
  </picture>
</h1>

<p align="center">
  <i align="center">Record the Korean lecture your computer is playing, and read it back as text 🎧</i>
</p>

<h4 align="center">
  <a href="./LICENSE">
    <img src="https://img.shields.io/badge/license-MIT-555?style=flat-square" alt="license" style="height: 20px;">
  </a>
  <a href="./pyproject.toml">
    <img src="https://img.shields.io/badge/python-3.12+-555?style=flat-square" alt="python" style="height: 20px;">
  </a>
  <a href="#introduction">
    <img src="https://img.shields.io/badge/platform-linux%20%7C%20windows-555?style=flat-square" alt="platform" style="height: 20px;">
  </a>
  <a href="#introduction">
    <img src="https://img.shields.io/badge/offline-no%20cloud%20ASR-555?style=flat-square" alt="offline" style="height: 20px;">
  </a>
</h4>

<p align="center">
    <img src="./.github/assets/window-recording.png" alt="the window while recording"/>
</p>

## Introduction

`lecture-scribe` records what your computer is playing — a Korean lecture in a
browser tab — and turns it into a readable transcript with timecodes.

No microphone, no speakers in the loop, no cloud. The audio never leaves the
machine, and the only network request in the tool's life is downloading the
speech model once.

```
   system audio  ──▶  audio.wav  ──▶   segments   ──▶  transcript.txt
     loopback          16 kHz          whisper
    scribe rec                       scribe text
                     └──────── scribe gui ────────┘
```

Recording and recognition are separate on purpose: a lecture can be captured
on a laptop that has no model installed, and transcribed later, elsewhere, or
again with a better model. The WAV is the part that cannot be recreated, so it
is never deleted or overwritten automatically.

<details open>
<summary>
 Features
</summary> <br />

**Records the output, not the room.** The loopback input is found by exact
device identifier rather than by name, so a headset that appears twice under
one name can never be recorded by mistake.

**Korean, fixed.** Auto-detection confuses Korean with Japanese in the opening
seconds and then the whole file goes wrong, so the language is pinned.

**Readable, not a wall of text.** Paragraphs break on a pause, timecodes are
`[MM:SS]`, and moments you flagged during the lecture are prefixed with `>>>`.

**Survives being killed.** Samples stream through an open file handle block by
block, so whatever was recorded is already on disk — verified with `kill -9`.

<p align="center">
    <img src="./.github/assets/window-transcript.png" alt="a finished transcript in the window"/>
</p>

</details>

## Usage

```bash
sudo pacman -S uv                     # or: curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync                               # environment with a managed Python 3.12
cp config.example.toml config.toml    # optional, the defaults work as they are
uv run scribe doctor                  # first command to run on a new machine
```

| | |
|---|---|
| `scribe doctor` | show devices, model and GPU; writes nothing |
| `scribe rec --course os` | record system audio until Ctrl+C |
| `scribe text audio.wav` | transcribe one or more recordings, writing `transcript.txt` |
| `scribe gui` | the same recording and transcribing in one window |
| `scribe launcher` | put that window in this machine's application menu |
| `scribe find "기말고사"` | search every transcript — *step 5* |

Prefix them with `uv run`, or run `uv tool install --editable .` once to get a
plain `scribe` on your `PATH` — editable, so `git pull` picks up changes
without reinstalling.

<details open>
<summary>
The window
</summary> <br />

`scribe gui` opens the same tool as a window, for when a terminal is not where
you want to be while a lecture runs. It is monochrome on purpose: greys only,
and loudness reads as brightness rather than colour.

Type a course name, press **record**, and press **mark** (or `Ctrl+M`) at a
moment worth finding again — those become the `>>>` paragraphs. **stop** closes
the WAV, **transcribe** runs the model and shows the transcript in the pane; it
is written to `transcript.txt` either way.

Recording and recognition each run on their own thread, so the meter keeps
moving and the window keeps responding. Closing during recognition asks first,
because a model run cannot be resumed part way through.

**Opening it without a terminal:**

```bash
uv tool install --editable . --force   # once, so scribe-gui is on the PATH
scribe launcher                        # from the directory holding config.toml
```

That adds lecture-scribe to the application menu — the Super launcher on a
tiling setup, the Start Menu on Windows — with an icon. `scribe launcher
--remove` takes it back out.

Run it **from the directory your `config.toml` lives in**. A menu starts a
program from your home directory, where there is no config file to read, so the
entry pins the working directory to wherever you ran it; it says so if it finds
no `config.toml` there.

On Windows there is a second launcher, `scribe-gui`, which opens the window
with no console behind it.

</details>

<details>
<summary>
The command line
</summary> <br />

Get the lecture audio playing (a browser tab is enough), then record until it
ends:

```bash
scribe rec --course os          # Ctrl+C when the lecture ends
```

While recording, one line keeps updating:

```
  0:12:34    -27.8 dBFS  [######------]    23.7 MB
```

If the first ten seconds are silent it says so and **keeps recording** — a
false alarm should never cost a lecture. If the disk fills up or the device
disappears part way through, the recording stops there and says why; the audio
captured up to that point is kept, and can be transcribed as it is.

Transcribe afterwards, on the same machine or a faster one, whenever
convenient — recording and transcription never have to happen back to back:

```bash
scribe text ~/lectures/2026-09-09_os/audio.wav
```

`text` takes more than one file and loads the model once for all of them, which
is the way to catch up on a week of recordings in one go:

```bash
scribe text ~/lectures/*/audio.wav
scribe text --model large-v3 audio.wav      # override without touching config.toml
```

`scribe gui` does both halves in one window, marks included. On the command
line they stay two separate commands: `rec --then-text` and the
important-moment hotkey are not wired up yet.

</details>

## What you get

One lecture, one folder under `output.dir`:

```
~/lectures/2026-09-09_os/
├── audio.wav        the recording, never deleted automatically
└── transcript.txt   the thing to read
```

A second lecture on the same subject and day goes to `2026-09-09_os-2`; nothing
is ever written on top of an existing recording.

The transcript reads like this:

```
[00:47] 먼저 문맥 교환이 무엇인지 봅시다. 프로세스가 바뀔 때마다 CPU 는
        레지스터 상태를 저장하고 복원해야 합니다.

>>> [12:30] 이 부분은 기말고사에 나옵니다.
```

Paragraphs break on a pause, and `>>>` marks a moment flagged during the
lecture — from the window's **mark** button, or `Ctrl+M`.

## Configuration

`config.toml` next to where you run the tool, all of it optional:

| key | default | meaning |
|---|---|---|
| `audio.device` | `"auto"` | substring of the output device, or the current one |
| `audio.sample_rate` | `16000` | capture rate in Hz |
| `audio.silence_rms` | `0.001` | level below which a block counts as silence |
| `asr.model` | `"large-v3"` | faster-whisper model |
| `asr.gpu` | `false` | run on CUDA instead of the CPU |
| `asr.language` | `"ko"` | spoken language, never auto-detected |
| `asr.beam_size` | `5` | decoder beam width |
| `output.dir` | `"~/lectures"` | one folder per lecture lands here |
| `output.paragraph_gap` | `1.2` | seconds of pause that start a paragraph |

A typo in a key is an error, not a silently ignored line.

## Development

<details open>
<summary>
Pre-requisites
</summary> <br />

- [uv](https://docs.astral.sh/uv/), which brings its own Python 3.12
- A working sound stack: PipeWire or PulseAudio on Linux, WASAPI on Windows
- Roughly 3 GB of disk for `large-v3`, downloaded on the first `scribe text`

A GPU is optional. `asr.gpu = true` switches the decoder to CUDA at
`int8_float16`; full float16 `large-v3` wants about 10 GB of VRAM, which most
laptop GPUs do not have.

</details>

<details open>
<summary>
Working on it
</summary> <br />

```bash
uv sync                      # environment, managed Python 3.12
uv run ruff format . && uv run ruff check .
uv run mypy                  # strict, and it has to pass
uv run pytest                # tests needing hardware or the model are skipped
uv run pytest -m manual      # run those instead
```

The layout is one module per domain, with the two front ends holding no logic
of their own:

```
audio_capture.py   system audio -> WAV on disk, plus device discovery
transcribe.py      WAV -> list[Segment], plus model and GPU readiness
format_text.py     segments -> transcript text (pure)
config.py          config.toml -> frozen dataclasses
cli.py             argument parsing, printing and wiring, nothing else
gui.py             the desktop window (Qt), wiring and drawing, nothing else
launcher.py        desktop menu entry, both platforms
```

`CLAUDE.md` carries the settled decisions and the non-obvious facts behind
them — why `soundcard` rather than `sounddevice`, why two channels are captured
when one is written, and why tkinter could not be the GUI.

</details>

## Roadmap

| step | contents | state |
|---|---|---|
| 0 | skeleton, config, `scribe doctor` | done |
| 1 | `audio_capture.py`, `scribe rec` | done |
| 2 | `transcribe.py`, `scribe text` | done |
| 3 | `format_text.py`: paragraphs, timecodes, `>>>` markers | done |
| 4 | `--then-text`, important-moment hotkey on the command line | in progress |
| G | `gui.py`, `scribe gui`: record and transcribe in one window | done |
| L | `launcher.py`, `scribe launcher`: menu entry on both platforms | done |
| 5 | `archive.py`, SQLite FTS5, `scribe find` | |

## License

[MIT](./LICENSE).
