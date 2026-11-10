# Review: the improvement plan, steps 1 to 7

- **Date:** 2026-11-10
- **Range:** `3959028..0e78633` on the `ci` branch (seven stacked branches:
  `offline-model-load`, `failed-start-cleanup`, `wrap-by-columns`,
  `marks-on-disk`, `hotwords`, `text-on-folders`, `ci`)
- **Reviewer:** a cloud review (`/ultrareview`), before anything reached `main`
- **Checks before the review:** `ruff check` clean, `mypy --strict` clean,
  198 tests pass (2 manual deselected)

**Resolution:** all six findings were confirmed against the code and fixed
the same day in `7bba0ef` "Fix what the review of the improvement plan
found". See [`../bugs.md`](../bugs.md), B-12 to B-17.

## Verdict

Good to keep. Two findings came from one change (the offline model load
trusted a looser check than huggingface_hub's own), two from the new folder
search (it did not ask whether a recording was finished, or how its name was
spelled), and two were small. None touched a recording or a transcript that
already existed.

## Findings

| id | severity | where | finding |
|---|---|---|---|
| B-12 | normal | `transcribe.load_model` | A failed load told the user to delete the weights folder, also for a local model directory and for CUDA or compute-type errors |
| B-13 | nit | `transcribe.load_model`, `hub_cache_dir` | A cache wrongly reported as on disk made the offline-only load fail where it used to repair itself online; the legacy `HUGGINGFACE_HUB_CACHE` was ignored |
| B-14 | normal | `transcribe.pending_recordings` | A recording still being written was picked up, and its partial transcript then marked it as done |
| B-15 | normal | `transcribe.pending_recordings` | `*.wav` matched case-sensitively on Linux, so `LECTURE.WAV` was never found |
| B-16 | nit | `transcribe.pending_recordings` | In a folder of several WAVs, one folder-named transcript covered all of them |
| B-17 | nit | `gui._open_recording` | A status line re-sent for the mark count lost its problem colour |

## What was decided

- **B-13:** a failed offline load is retried once online rather than making
  `model_status` as strict as huggingface_hub. The pre-check stays cheap, and
  the retry is what every load did before the offline one existed.
- **B-14:** "still being recorded" is a file written to within the last 30
  seconds, not a lock file. No new file for the recorder to keep in step, and
  a recording killed outright becomes eligible half a minute later.
- **B-16:** a recording that shares its folder counts as done only by its own
  `<name>.txt`, so `scribe text` falls through to the existing clash report
  instead of saying nothing is left.
