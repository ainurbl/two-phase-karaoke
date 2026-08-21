---
name: two-phase-karaoke
description: Create and revise lyric-timed karaoke videos from a vocal/acapella file and an instrumental/minus file. Use when a user gives paths to an акапелла and минусовка, asks to obtain or correct song words and SRT timings, or wants an MP4 karaoke video with on-screen lyrics and no vocal audio. Do not use to separate vocals from a single mixed song.
---

# Two-Phase Karaoke

Use the bundled `scripts/two_phase_karaoke.py`. Keep the workflow in two explicit phases so the user can inspect and change the recognised text before rendering.

## Inputs and outputs

Collect absolute paths for:

- an acapella/vocal audio file for phase 1;
- an instrumental/minus audio file for phase 2;
- a writable output folder.

Keep intermediate files next to the final deliverable unless the user chooses another location:

- `song.draft.srt` — editable words and timings;
- `song.draft.txt` — plain-text transcript;
- `song-karaoke.mp4` — final video.

Never include the acapella in phase 2. The final MP4 must map audio only from the instrumental input.

## Phase 1 — acapella to editable SRT

1. Check that `ffmpeg`, `whisper-cli` and a GGML Whisper `.bin` model are available. `Pillow` is only necessary at render time.
2. If a dependency or model is absent, explain the one-time setup and obtain any required approval before installing/downloading it. Prefer `ggml-base.en.bin` for English singing; choose a language-appropriate model and pass `--language` for other languages.
3. Run:

   ```bash
   python3 scripts/two_phase_karaoke.py transcribe \
     /absolute/vocal.mp3 /absolute/output/song.draft.srt \
     --model /absolute/ggml-base.en.bin --language en
   ```

4. Validate that the SRT and TXT were written. Tell the user that transcription is a draft and link the SRT file for review. Do not render the video until the user asks to continue, unless they explicitly request the full end-to-end result.

## Edit words and timings through the agent

When the user gives corrections, edit the existing SRT directly. Preserve UTF-8 and SRT numbering. Each block is:

```srt
12
00:01:12,000 --> 00:01:15,200
Text of the line
```

- Change only the text line when the words are wrong.
- Change the two timestamps when the timing is wrong; use `HH:MM:SS,mmm`.
- Split a block for two separately timed lines, or merge neighbouring blocks when requested.
- Keep intervals ordered and non-overlapping; the renderer intentionally rejects overlaps.

Before rendering, confirm that the requested corrections are present in the SRT. When a correction is ambiguous, quote its current timestamp and ask for the intended wording or timing.

## Phase 2 — edited SRT plus instrumental to MP4

Run the renderer only with the instrumental and the edited SRT:

```bash
python3 scripts/two_phase_karaoke.py render \
  /absolute/instrumental.mp3 /absolute/output/song.draft.srt \
  /absolute/output/song-karaoke.mp4 --lead-ms 1000
```

Use `--lead-ms 1000` by default when the singer needs to see each cue one second before its vocal onset. Adjust the value as needed; keep it at `0` only when subtitle time must exactly match the displayed word rather than act as a singing prompt. The option moves only the rendered visual layer; it does not alter the editable SRT timings.

Use `--overwrite` only when replacing a known prior draft or video. Verify the result with `ffprobe`: it must contain H.264 video, AAC audio and the expected duration. Inspect one representative frame for readable caption placement.

## First-use dependencies

On macOS, the usual setup is:

```bash
brew install ffmpeg whisper-cpp
python3 -m pip install Pillow
```

Download Whisper models outside the repository and never commit audio, video, SRT drafts, transcripts or model files. The repository's `.gitignore` must keep those generated and personal assets out of version control.
