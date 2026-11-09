# Accessibility

`lecture-scribe` exists to make spoken lectures readable as text, so that
someone who cannot follow fast speech, or cannot hear it, can read it back at
their own pace. This file says honestly how accessible the tool itself is.
It is a one-person project and has not been through an accessibility audit.

## Supported environments

- **Linux**, Wayland or X11, with PipeWire or PulseAudio. This is where it is
  developed and tested.
- **Windows 11.** The Start Menu shortcut has been checked there. The current
  window, the language choice and keeping the computer awake have only been
  run on Linux so far.
- Python 3.12 or newer. An NVIDIA GPU is optional and only makes
  transcription faster.

## What works

- **Everything the window does can be done from the command line.** `scribe
  rec`, `scribe text` and `scribe doctor` print plain text, so a screen reader
  or a braille display that copes with a terminal can use the whole tool
  without the window.
- **The transcript is a plain `.txt` file** with a `[MM:SS]` timecode on each
  paragraph. It opens in any editor or reader, at any font size, and `grep`
  finds a word with its time.
- **Marks are written as text.** A marked paragraph starts with `>>>` in the
  file, not only coloured in the window.
- **Colour is never the only signal.** The red recording state also says
  `recording` in the status chip, and the buttons change their label.
- **Keyboard:** <kbd>Ctrl</kbd>+<kbd>M</kbd> marks, <kbd>Ctrl</kbd>+<kbd>O</kbd>
  opens a recording, and <kbd>Tab</kbd> moves between the controls.
- **Main text contrast** is 15:1 (`#ececee` on `#18181b`); secondary text is
  5.3:1. Both pass WCAG 2.2 AA.
- **The computer stays awake** while recording or transcribing, so a long
  lecture does not need you at the keyboard.

## Known limitations

- **Dark theme only.** There is no light or high-contrast theme, and the
  window does not follow the system's contrast setting. This is a design
  decision for now, not an oversight.
- **Small captions and timecodes are too faint**: 2.6:1 (`#5a5a62` on
  `#18181b`), below the WCAG 4.5:1 minimum.
- **White on the red record button is 3.1:1**, enough for large bold text
  only.
- **The keyboard focus is hard to see**: it is a slightly lighter border,
  2.1:1 against the button (`#55555e` on `#232327`), below the 3:1 WCAG asks
  for.
- **Screen readers have not been tried** with the window (Orca on Linux,
  NVDA or Narrator on Windows). Some custom-drawn parts, such as the level
  meter and the status chip, may not be announced.
- **Record, stop and transcribe have no keyboard shortcuts** of their own;
  reach them with <kbd>Tab</kbd> and <kbd>Space</kbd>.
- **The window has no zoom control**, and whether it follows the system's
  text size setting has not been checked.
- **Recognition quality** depends on the recording. Overlapping speech,
  music or a quiet microphone in the lecture produce gaps or wrong words,
  and nothing in the transcript marks which words are uncertain.

## Reporting a barrier

If something in the tool keeps you from using it, please
[open an issue](https://github.com/snowing152/lecture-scribe/issues/new/choose)
with the **Bug report** form and choose *The window* or *The command line*.
Say what assistive technology you use, if any. Barriers are treated as bugs,
not as feature requests.
