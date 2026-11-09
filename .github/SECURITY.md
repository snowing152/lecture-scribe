# Security policy

## What there is to protect

`lecture-scribe` runs entirely on your own computer. It has no server, no
account and no network service. The only thing it downloads is the speech
model, once, from Hugging Face through `faster-whisper`. Recordings and
transcripts are written to a local folder and never sent anywhere.

What could still go wrong, and is worth reporting:

- a recording or transcript written somewhere other than the configured
  folder, or readable by other users of the machine when it should not be;
- any network traffic beyond the model download;
- a crafted WAV file or `config.toml` that makes the tool run code, or
  overwrite or delete files;
- the desktop menu entry (`scribe launcher`) or the Windows shortcut running
  something other than the installed `scribe`.

## Supported versions

Only the latest commit on `main` is supported. There are no release branches.

## Reporting a vulnerability

Please **do not open a public issue.** Report it privately instead:

1. Open the [**Security** tab](https://github.com/snowing152/lecture-scribe/security)
   of this repository.
2. Press **Report a vulnerability**
   ([direct link](https://github.com/snowing152/lecture-scribe/security/advisories/new)).
3. Describe what happens, on which system (Linux or Windows), and how to
   reproduce it.

Only the maintainer can read the report. This is a one-person project
maintained in spare time, so expect an answer within a couple of weeks
rather than days. Once the problem is fixed, the advisory is published with
credit to you unless you ask otherwise.
