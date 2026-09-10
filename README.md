# lecture-scribe

Records what your computer is playing — a Korean lecture in a browser tab —
and turns it into a readable transcript with timecodes.

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
again with a better model. The WAV is the part that cannot be recreated.

## Quick start

```bash
sudo pacman -S uv                     # or: curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync                               # environment with a managed Python 3.12
cp config.example.toml config.toml    # optional, the defaults work as they are
uv run scribe doctor                  # first command to run on a new machine
```

## Commands

| | |
|---|---|
| `scribe doctor` | show devices, model and GPU; writes nothing |
| `scribe rec --course os` | record system audio until Ctrl+C |
| `scribe text audio.wav` | transcribe one or more recordings, writing `transcript.txt` |
| `scribe gui` | the same recording and transcribing in one window |
| `scribe find "기말고사"` | search every transcript — *step 5* |

Prefix them with `uv run`, or run `uv tool install --editable .` once to get a
plain `scribe` on your `PATH` — editable, so `git pull` picks up changes
without reinstalling. While recording, one line keeps updating:

```
  0:12:34    -27.8 dBFS  [######------]    23.7 MB
```

If the first ten seconds are silent it says so and **keeps recording** — a
false alarm should never cost a lecture.

## The window

`scribe gui` opens the same tool as a window, for when a terminal is not
where you want to be while a lecture runs. It is monochrome on purpose:
greys only, and loudness reads as brightness rather than colour.

```
  LECTURE SCRIBE

  COURSE   [ os                                        ]
  DEVICE   [ Ryzen HD Audio Controller Speaker  ▾      ]

                       0:12:34
       ▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▊▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁
                 -27.8 dBFS  ·  23.7 MB

           [ stop ]   [ mark ]   [ transcribe ]
  ──────────────────────────────────────────────────────
  marked [12:30] (2 in this lecture)

  [00:47] 먼저 문맥 교환이 무엇인지 봅시다. …
```

Type a course name, press **record**, and press **mark** (or `Ctrl+M`) at a
moment worth finding again — those become the `>>>` paragraphs. **stop**
closes the WAV, **transcribe** runs the model and shows the transcript in
the pane; it is written to `transcript.txt` either way.

Recording and recognition each run on their own thread, so the meter keeps
moving and the window keeps responding. Closing during recognition asks
first, because a model run cannot be resumed part way through.

On Windows there is a second launcher, `scribe-gui`, which opens the window
with no console behind it.

## Typical workflow

Once per machine, or after moving the tool to a new one:

```bash
scribe doctor
```

Get the lecture audio playing (a browser tab is enough), then record until
it ends:

```bash
scribe rec --course os          # Ctrl+C when the lecture ends
```

Transcribe afterwards, on the same machine or a faster one, whenever
convenient — recording and transcription never have to happen back to back:

```bash
scribe text ~/lectures/2026-09-09_os/audio.wav
```

`text` takes more than one file and loads the model once for all of them,
which is the way to catch up on a week of recordings in one go:

```bash
scribe text ~/lectures/*/audio.wav
scribe text --model large-v3 audio.wav      # override without touching config.toml
```

`scribe gui` does both halves in one window, marks included. On the command
line they stay two separate commands: `rec --then-text` and the
important-moment hotkey are not wired up yet (see the roadmap in
`CLAUDE.md`).

## What you get

One lecture, one folder under `output.dir`:

```
~/lectures/2026-09-09_os/
├── audio.wav        the recording, never deleted automatically
└── transcript.txt   the thing to read
```

A second lecture on the same subject and day goes to `2026-09-09_os-2`;
nothing is ever written on top of an existing recording.

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

```bash
uv run ruff format . && uv run ruff check .
uv run mypy
uv run pytest                # tests needing hardware or the model are skipped
uv run pytest -m manual      # run those instead
```
