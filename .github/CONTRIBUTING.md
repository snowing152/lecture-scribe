# Contributing

`lecture-scribe` is a personal tool: it was built for one person recording
Korean lectures on their own computer. You are welcome to use it, report what
breaks and suggest changes, but the scope is deliberately narrow, so please
read the next section before spending time on a pull request.

## What will not be merged

These are settled, and the reasons are written down in
[`CLAUDE.md`](../CLAUDE.md) and [`docs/decisions.md`](../docs/decisions.md):

- **Anything that leaves the machine.** No cloud speech recognition, no
  upload, no telemetry.
- **Automatic language detection.** It guesses once from the first thirty
  seconds and a wrong guess spoils the whole lecture.
- **New dependencies** without asking in an issue first.
- **Logic that only one front end can reach.** The command line (`cli.py`)
  and the window (`gui.py`) are equals; both only wire and display, and
  everything the window does must stay possible from the command line.
- **Anything that deletes or overwrites a recording.** A lecture cannot be
  recorded twice.

## Open to proposals

These are welcome, as long as everything runs on the computer itself:

- **Real-time transcription**, as an extra mode. Recording the whole lecture
  first and recognising it afterwards stays the default, because the model is
  more accurate on whole sentences, and recording must keep working on a
  machine without the model.
- **Speaker separation**, for seminars and Q&A with more than one voice.
- **Translation** of a finished transcript.
- **Summaries** of a finished transcript.

Most of these need a new dependency, so open an issue to agree on the
approach before writing the code.

## Reporting a bug

Open an issue with the **Bug report** form. The most useful thing you can
attach is the output of:

```bash
scribe doctor
```

It shows the audio devices the tool can see, whether the model and the GPU
are ready, and which Python environment is running.

Never attach a lecture recording or transcript you are not allowed to share.

## Making a change

```bash
uv sync                                       # managed Python 3.12
uv run ruff format . && uv run ruff check .   # format and lint
uv run mypy                                   # strict, must pass
uv run pytest                                 # hardware and model tests are skipped
uv run pytest -m manual                       # those, on a machine that has them
```

House rules:

- Code, comments, docstrings and console output are in English.
- Full type annotations; `mypy --strict` passes.
- Document new code the way the code around it is documented.
- Comments explain *why*, never *what*.
- Paths are `pathlib.Path`; the same code runs on Linux and Windows.
- Catch named exceptions and say what to do next; no blanket `try/except`.
- Tests cover pure logic. Anything needing a sound card or the downloaded
  model is marked `@pytest.mark.manual`.
- One change per pull request, and say how you checked it by hand.
- A pull request is checked automatically on Linux and on Windows (format,
  lint, `mypy`, tests). It cannot check audio or the model, so say how you did.

## Conduct

Everyone taking part is expected to follow the
[Code of Conduct](CODE_OF_CONDUCT.md).
