#!/usr/bin/env python3
"""Two-phase karaoke workflow.

Phase 1 (only the vocal/acapella is used):
  python3 two_phase_karaoke.py transcribe vocal.mp3 lyrics.srt \
      --model /path/to/ggml-base.en.bin --language en

Phase 2 (only the instrumental and edited SRT are used):
  python3 two_phase_karaoke.py render instrumental.mp3 lyrics.srt karaoke.mp4

Requirements:
  - FFmpeg in PATH (or Homebrew's /opt/homebrew/bin/ffmpeg)
  - whisper.cpp's whisper-cli and a GGML Whisper model for phase 1
  - Pillow: python3 -m pip install Pillow (only phase 2)

The video renderer deliberately does not use FFmpeg's drawtext/subtitles
filters, so it works with smaller FFmpeg builds as well.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


WIDTH = 1600
HEIGHT = 900
FPS = 30
SRT_TIME = re.compile(
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s+-->\s+"
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})"
)


def existing_file(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"Файл не найден: {path}")
    return path


def output_file(value: str) -> Path:
    return Path(value).expanduser().resolve()


def find_executable(name: str, extra_paths: tuple[str, ...] = ()) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for candidate in extra_paths:
        path = Path(candidate)
        if path.is_file() and path.stat().st_mode & 0o111:
            return str(path)
    return None


def ffmpeg() -> str:
    executable = find_executable("ffmpeg", ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"))
    if not executable:
        raise RuntimeError("Не найден FFmpeg. Установите его, например: brew install ffmpeg")
    return executable


def whisper_cli() -> str:
    executable = find_executable(
        "whisper-cli", ("/opt/homebrew/bin/whisper-cli", "/usr/local/bin/whisper-cli")
    )
    if not executable:
        raise RuntimeError("Не найден whisper-cli. Установите его, например: brew install whisper-cpp")
    return executable


def srt_seconds(match: re.Match[str], start_index: int) -> float:
    hours, minutes, seconds, milliseconds = (
        int(match.group(start_index + offset)) for offset in range(4)
    )
    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000


def parse_srt(path: Path) -> list[tuple[float, float, str]]:
    """Return non-empty, non-overlapping subtitle entries from an SRT file."""
    entries: list[tuple[float, float, str]] = []
    blocks = re.split(r"\r?\n\s*\r?\n", path.read_text(encoding="utf-8-sig").strip())
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timestamp = next((line for line in lines if "-->" in line), None)
        if not timestamp:
            continue
        match = SRT_TIME.search(timestamp)
        if not match:
            raise ValueError(f"Неверный таймкод: {timestamp}")
        start = srt_seconds(match, 1)
        end = srt_seconds(match, 5)
        if end <= start:
            raise ValueError(f"Конец строки должен быть позже начала: {timestamp}")
        text = " ".join(line for line in lines if line != timestamp and not line.isdigit())
        text = re.sub(r"<[^>]+>", "", text).replace("♪", "").strip()
        if text:
            entries.append((start, end, text))

    if not entries:
        raise ValueError("В SRT не нашлось строк с текстом.")
    entries.sort(key=lambda item: item[0])
    for previous, current in zip(entries, entries[1:]):
        if current[0] < previous[1]:
            raise ValueError("В SRT есть пересекающиеся по времени строки. Уберите пересечение.")
    return entries


def resolve_font(value: str | None) -> Path:
    if value:
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise RuntimeError(f"Файл шрифта не найден: {path}")
        return path
    candidates = (
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),  # macOS
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),  # Linux
        Path("C:/Windows/Fonts/arialbd.ttf"),  # Windows
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("Не найден шрифт. Передайте его через --font /путь/к/шрифту.ttf")


def wrap_text(draw: object, text: str, font: object, max_width: int) -> list[str]:
    # Pillow types vary between releases; object keeps phase 1 free of Pillow.
    lines: list[str] = []
    line = ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if line and draw.textbbox((0, 0), candidate, font=font)[2] > max_width:
            lines.append(line)
            line = word
        else:
            line = candidate
    if line:
        lines.append(line)
    return lines


def make_caption_image(path: Path, text: str, font_path: Path) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ModuleNotFoundError as error:
        raise RuntimeError("Для фазы render нужен Pillow: python3 -m pip install Pillow") from error

    image = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(font_path, 62)
    lines = wrap_text(draw, text, font, WIDTH - 210)
    line_height = 86
    box_height = 92 + line_height * len(lines)
    top = 155
    draw.rounded_rectangle(
        (70, top, WIDTH - 70, top + box_height),
        radius=32,
        fill=(8, 15, 31, 220),
        outline=(56, 189, 248, 230),
        width=3,
    )
    y = top + (box_height - line_height * len(lines)) // 2 - 8
    for line in lines:
        left, _, right, _ = draw.textbbox((0, 0), line, font=font, stroke_width=2)
        x = (WIDTH - (right - left)) // 2
        draw.text(
            (x, y),
            line,
            font=font,
            fill=(255, 255, 255, 255),
            stroke_width=2,
            stroke_fill=(0, 0, 0, 230),
        )
        y += line_height
    image.save(path)


def quote_concat(path: Path) -> str:
    return str(path.resolve()).replace("'", "'\\''")


def build_caption_layer(entries: list[tuple[float, float, str]], temp: Path, font: Path) -> Path:
    try:
        from PIL import Image
    except ModuleNotFoundError as error:
        raise RuntimeError("Для фазы render нужен Pillow: python3 -m pip install Pillow") from error

    blank = temp / "blank.png"
    Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0)).save(blank)
    timeline: list[tuple[Path, float]] = []
    cursor = 0.0
    for index, (start, end, text) in enumerate(entries):
        if start > cursor:
            timeline.append((blank, start - cursor))
        image = temp / f"caption-{index:03d}.png"
        make_caption_image(image, text, font)
        timeline.append((image, end - start))
        cursor = end

    manifest = temp / "captions.ffconcat"
    manifest_lines = ["ffconcat version 1.0"]
    for image, duration in timeline:
        manifest_lines.extend((f"file '{quote_concat(image)}'", f"duration {duration:.3f}"))
    # The concat demuxer needs this duplicated final image to honour the last duration.
    manifest_lines.append(f"file '{quote_concat(timeline[-1][0])}'")
    manifest.write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")

    layer = temp / "captions.mov"
    subprocess.run(
        [
            ffmpeg(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(manifest),
            "-fps_mode",
            "vfr",
            "-c:v",
            "qtrle",
            "-pix_fmt",
            "argb",
            str(layer),
        ],
        check=True,
    )
    return layer


def transcribe(args: argparse.Namespace) -> None:
    if args.output.exists() and not args.overwrite:
        raise RuntimeError(f"Файл уже существует: {args.output}. Добавьте --overwrite.")
    if not args.model.is_file():
        raise RuntimeError(f"Модель Whisper не найдена: {args.model}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    prefix = args.output.parent / f".{args.output.stem}.whisper-tmp"
    for suffix in (".srt", ".txt"):
        Path(f"{prefix}{suffix}").unlink(missing_ok=True)

    subprocess.run(
        [
            whisper_cli(),
            "-m",
            str(args.model),
            "-f",
            str(args.vocal),
            "-l",
            args.language,
            "-t",
            str(args.threads),
            "-ng",  # dependable on machines where Metal/GPU access is unavailable
            "-osrt",
            "-otxt",
            "-of",
            str(prefix),
            "-np",
        ],
        check=True,
    )
    produced_srt = Path(f"{prefix}.srt")
    if not produced_srt.is_file():
        raise RuntimeError("Whisper не создал SRT-файл.")
    produced_srt.replace(args.output)
    produced_txt = Path(f"{prefix}.txt")
    if produced_txt.is_file():
        produced_txt.replace(args.output.with_suffix(".txt"))
    print(f"Черновик текста и таймингов: {args.output}")


def render(args: argparse.Namespace) -> None:
    if args.output.suffix.lower() != ".mp4":
        raise RuntimeError("Итоговый файл должен иметь расширение .mp4")
    if args.output.exists() and not args.overwrite:
        raise RuntimeError(f"Файл уже существует: {args.output}. Добавьте --overwrite.")
    entries = parse_srt(args.srt)
    font = resolve_font(args.font)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    # This phase never receives the vocal file: output audio comes solely from `instrumental`.
    with tempfile.TemporaryDirectory(prefix="karaoke-captions-") as directory:
        captions = build_caption_layer(entries, Path(directory), font)
        filter_graph = (
            "[0:a]aresample=48000,asplit=2[audio_src][wave_src];"
            "[audio_src]alimiter=limit=0.95[audio];"
            f"[wave_src]showwaves=s={WIDTH}x230:mode=cline:colors=0x38BDF8:rate={FPS},format=rgba[wave];"
            f"color=c=0x0B1020:s={WIDTH}x{HEIGHT}:r={FPS}[background];"
            "[background]drawbox=x=0:y=0:w=iw:h=120:color=0x111827:t=fill,"
            "drawbox=x=0:y=118:w=iw:h=2:color=0x334155:t=fill,"
            "drawbox=x=80:y=48:w=1440:h=12:color=0x38BDF8:t=fill[canvas];"
            "[canvas][wave]overlay=x=0:y=570:eof_action=pass[with_wave];"
            "[with_wave][1:v]overlay=x=0:y=0:eof_action=pass[video]"
        )
        subprocess.run(
            [
                ffmpeg(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y" if args.overwrite else "-n",
                "-i",
                str(args.instrumental),
                "-i",
                str(captions),
                "-filter_complex",
                filter_graph,
                "-map",
                "[video]",
                "-map",
                "[audio]",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                str(args.crf),
                "-pix_fmt",
                "yuv420p",
                "-r",
                str(FPS),
                "-c:a",
                "aac",
                "-b:a",
                "256k",
                "-movflags",
                "+faststart",
                "-shortest",
                str(args.output),
            ],
            check=True,
        )
    print(f"Караоке-видео готово: {args.output}")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Двухфазная сборка караоке: транскрибация → видео.")
    commands = parser.add_subparsers(dest="command", required=True)

    phase_one = commands.add_parser("transcribe", help="1. Получить SRT из акапеллы.")
    phase_one.add_argument("vocal", type=existing_file, help="акапелла")
    phase_one.add_argument("output", type=output_file, help="черновик .srt")
    phase_one.add_argument("--model", type=existing_file, required=True, help="GGML Whisper model (.bin)")
    phase_one.add_argument("--language", default="en", help="код языка, например en или ru")
    phase_one.add_argument("--threads", type=int, default=8, help="число потоков Whisper")
    phase_one.add_argument("--overwrite", action="store_true")

    phase_two = commands.add_parser("render", help="2. Собрать видео из минусовки и отредактированного SRT.")
    phase_two.add_argument("instrumental", type=existing_file, help="минусовка")
    phase_two.add_argument("srt", type=existing_file, help="исправленный SRT")
    phase_two.add_argument("output", type=output_file, help="итоговый .mp4")
    phase_two.add_argument("--font", help="путь к .ttf/.otf; по умолчанию ищется системный Arial Bold")
    phase_two.add_argument("--crf", type=int, default=22, help="качество H.264: 18–28, меньше = лучше")
    phase_two.add_argument("--overwrite", action="store_true")

    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    try:
        if args.command == "transcribe":
            transcribe(args)
        else:
            render(args)
    except (RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
