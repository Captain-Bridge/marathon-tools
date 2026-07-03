from __future__ import annotations

import argparse
import math
import subprocess
import sys
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np


VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".flv"}


@dataclass
class ProcessingOptions:
    input_dir: Path
    output_dir: Path
    silence_threshold_db: float
    min_silence_duration: float
    audio_bitrate: str
    audio_sample_rate: int
    keep_stereo: bool


def run_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "Unknown command error"
        raise RuntimeError(message)
    return result


def ensure_dependency(command_name: str) -> None:
    try:
        run_command([command_name, "-version"])
    except FileNotFoundError as exc:
        raise RuntimeError(f"Missing dependency: {command_name}. Please add it to PATH.") from exc
    except RuntimeError:
        return


def has_audio_stream(file_path: Path) -> bool:
    result = run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(file_path),
        ]
    )
    return bool(result.stdout.strip())


def decode_audio_to_wav(source_path: Path, wav_path: Path) -> None:
    run_command(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-i",
            str(source_path),
            "-vn",
            "-map",
            "0:a:0",
            "-acodec",
            "pcm_s16le",
            str(wav_path),
        ]
    )


def read_wave_mono_samples(wav_path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(wav_path), "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        frame_rate = wav_file.getframerate()
        frame_count = wav_file.getnframes()
        pcm_bytes = wav_file.readframes(frame_count)

    if sample_width != 2:
        raise RuntimeError(f"Unsupported sample width: {sample_width * 8} bit")

    samples = np.frombuffer(pcm_bytes, dtype=np.int16)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)

    normalized = samples.astype(np.float32) / 32768.0
    return normalized, frame_rate


def find_trim_range(samples: np.ndarray, sample_rate: int, silence_threshold_db: float, min_silence_duration: float) -> tuple[float, float]:
    if samples.size == 0:
        return 0.0, 0.0

    window_ms = 20
    window_size = max(1, int(sample_rate * window_ms / 1000))
    window_count = int(math.ceil(samples.size / window_size))
    padded_size = window_count * window_size

    if padded_size != samples.size:
        samples = np.pad(samples, (0, padded_size - samples.size))

    windows = samples.reshape(window_count, window_size)
    rms = np.sqrt(np.mean(np.square(windows), axis=1))
    threshold = 10 ** (silence_threshold_db / 20.0)
    non_silent = rms > threshold

    min_silence_windows = max(1, int(math.ceil(min_silence_duration / (window_size / sample_rate))))

    first_non_silent_window = 0
    while first_non_silent_window < window_count and not non_silent[first_non_silent_window]:
        first_non_silent_window += 1

    if first_non_silent_window == window_count:
        return 0.0, 0.0

    last_non_silent_window = window_count - 1
    while last_non_silent_window >= 0 and not non_silent[last_non_silent_window]:
        last_non_silent_window -= 1

    start_window = max(0, first_non_silent_window)
    end_window = min(window_count, last_non_silent_window + 1)

    leading_silence_windows = first_non_silent_window
    trailing_silence_windows = window_count - end_window

    if leading_silence_windows < min_silence_windows:
        start_window = 0
    if trailing_silence_windows < min_silence_windows:
        end_window = window_count

    start_time = start_window * window_size / sample_rate
    end_time = min(samples.size, end_window * window_size) / sample_rate
    return start_time, end_time


def encode_output(source_path: Path, output_path: Path, start_time: float, end_time: float, options: ProcessingOptions) -> None:
    channels = "2" if options.keep_stereo else "1"
    args = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-ss",
        f"{start_time:.3f}",
        "-to",
        f"{end_time:.3f}",
        "-i",
        str(source_path),
        "-vn",
        "-map",
        "0:a:0",
        "-c:a",
        "libopus",
        "-b:a",
        options.audio_bitrate,
        "-vbr",
        "on",
        "-compression_level",
        "10",
        "-ac",
        channels,
        "-ar",
        str(options.audio_sample_rate),
        str(output_path),
    ]
    run_command(args)


def process_file(file_path: Path, options: ProcessingOptions) -> None:
    if not has_audio_stream(file_path):
        print(f"Skipping {file_path.name}: no audio stream found.", file=sys.stderr)
        return

    with tempfile.TemporaryDirectory(prefix="audio_cut_") as temp_dir:
        wav_path = Path(temp_dir) / f"{file_path.stem}.wav"
        decode_audio_to_wav(file_path, wav_path)
        samples, sample_rate = read_wave_mono_samples(wav_path)
        start_time, end_time = find_trim_range(
            samples=samples,
            sample_rate=sample_rate,
            silence_threshold_db=options.silence_threshold_db,
            min_silence_duration=options.min_silence_duration,
        )

    if end_time - start_time < 0.15:
        print(f"Skipping {file_path.name}: trimmed duration is too short after silence detection.", file=sys.stderr)
        return

    output_path = options.output_dir / f"{file_path.stem}.opus"
    encode_output(file_path, output_path, start_time, end_time, options)
    print(f"Done: {file_path.name} -> {output_path.name} | trim {start_time:.3f}s to {end_time:.3f}s")


def parse_args() -> ProcessingOptions:
    parser = argparse.ArgumentParser(description="Extract audio from videos, trim leading/trailing silence, and export small Opus files.")
    parser.add_argument("--input-dir", default="input")
    parser.add_argument("--output-dir", default="outputs")
    parser.add_argument("--silence-threshold-db", type=float, default=-45.0)
    parser.add_argument("--min-silence-duration", type=float, default=0.30)
    parser.add_argument("--audio-bitrate", default="32k")
    parser.add_argument("--audio-sample-rate", type=int, default=24000)
    parser.add_argument("--keep-stereo", action="store_true")
    args = parser.parse_args()

    return ProcessingOptions(
        input_dir=Path(args.input_dir).resolve(),
        output_dir=Path(args.output_dir).resolve(),
        silence_threshold_db=args.silence_threshold_db,
        min_silence_duration=args.min_silence_duration,
        audio_bitrate=args.audio_bitrate,
        audio_sample_rate=args.audio_sample_rate,
        keep_stereo=args.keep_stereo,
    )


def main() -> int:
    try:
        options = parse_args()
        ensure_dependency("ffmpeg")
        ensure_dependency("ffprobe")

        if not options.input_dir.exists():
            raise RuntimeError(f"Input directory not found: {options.input_dir}")

        options.output_dir.mkdir(parents=True, exist_ok=True)
        files = sorted(
            file_path
            for file_path in options.input_dir.iterdir()
            if file_path.is_file() and file_path.suffix.lower() in VIDEO_EXTENSIONS
        )

        if not files:
            print(f"No supported video files found in {options.input_dir}")
            return 0

        for file_path in files:
            process_file(file_path, options)
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
