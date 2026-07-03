#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np

DEFAULT_CANVAS_SIZE = 128.0
DEFAULT_SUBJECT_SIZE = 100.0

SUPPORTED_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".webp",
    ".tif",
    ".tiff",
}


@dataclass
class ContourPath:
    outer: np.ndarray
    holes: list[np.ndarray]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert dark-background, light-foreground images into "
            "Alexa Vector Graphics (AVG) and SVG previews."
        )
    )
    parser.add_argument("input", type=Path, help="Source image file or directory")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help=(
            "AVG output file path for a single input file, or AVG output directory "
            "for a directory input. Defaults to <input>.avg.json or ./output."
        ),
    )
    parser.add_argument(
        "--svg",
        "--preview-svg",
        type=Path,
        help=(
            "SVG file path for a single input file, or SVG output "
            "directory for a directory input. Defaults to <input>.svg "
            "or the AVG output directory."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=("matched", "aggressive", "otsu", "manual"),
        default="matched",
        help=(
            "Segmentation mode. 'matched' is tuned for this project's consistent "
            "dark-background, light-foreground input style."
        ),
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=128,
        help="Manual grayscale threshold [0-255]. Only used when --mode manual.",
    )
    parser.add_argument(
        "--min-area",
        type=float,
        default=4.0,
        help="Drop connected components smaller than this many pixels.",
    )
    parser.add_argument(
        "--approx-epsilon",
        type=float,
        default=0.003,
        help="Polygon simplification factor as a fraction of contour perimeter.",
    )
    parser.add_argument(
        "--fill",
        default="#898C99",
        help="AVG fill color for extracted shapes.",
    )
    parser.add_argument(
        "--no-blur",
        action="store_true",
        help="Disable the light Gaussian blur used before segmentation.",
    )
    parser.add_argument(
        "--bg-sample-width",
        type=int,
        default=3,
        help="Border width used by aggressive mode to estimate the background color.",
    )
    parser.add_argument(
        "--bg-margin",
        type=float,
        default=6.0,
        help=(
            "Extra distance above the sampled border variation before pixels are "
            "treated as foreground in aggressive mode."
        ),
    )
    parser.add_argument(
        "--close-kernel",
        type=int,
        default=1,
        help="Morphological closing kernel size applied after segmentation.",
    )
    parser.add_argument(
        "--close-iterations",
        type=int,
        default=0,
        help="Morphological closing iterations applied after segmentation.",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Recursively scan subdirectories when the input is a directory.",
    )
    parser.add_argument(
        "--canvas-size",
        type=float,
        default=DEFAULT_CANVAS_SIZE,
        help="Output square canvas size. Defaults to 128.",
    )
    parser.add_argument(
        "--subject-size",
        type=float,
        default=DEFAULT_SUBJECT_SIZE,
        help="Target max width/height of the centered subject. Defaults to 100.",
    )
    return parser.parse_args()


def load_image(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(f"Unable to read image: {path}")
    return image


def to_grayscale(image: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def sample_border_gray(image: np.ndarray, border_width: int) -> np.ndarray:
    border_width = max(1, min(border_width, image.shape[0], image.shape[1]))
    top = image[:border_width, :]
    bottom = image[-border_width:, :]
    left = image[:, :border_width]
    right = image[:, -border_width:]
    return np.concatenate(
        [
            top.reshape(-1),
            bottom.reshape(-1),
            left.reshape(-1),
            right.reshape(-1),
        ]
    )


def sample_border_pixels(image: np.ndarray, border_width: int) -> np.ndarray:
    border_width = max(1, min(border_width, image.shape[0], image.shape[1]))
    top = image[:border_width, :, :]
    bottom = image[-border_width:, :, :]
    left = image[:, :border_width, :]
    right = image[:, -border_width:, :]
    return np.concatenate(
        [
            top.reshape(-1, 3),
            bottom.reshape(-1, 3),
            left.reshape(-1, 3),
            right.reshape(-1, 3),
        ],
        axis=0,
    )


def find_matched_threshold(gray: np.ndarray, border_width: int) -> int:
    border = sample_border_gray(gray, border_width)
    background_level = int(np.median(border))
    histogram = np.bincount(gray.reshape(-1), minlength=256)

    foreground_start = min(background_level + 24, 255)
    foreground_histogram = histogram[foreground_start:]
    if foreground_histogram.size == 0 or int(foreground_histogram.sum()) == 0:
        return min(background_level + 32, 255)

    foreground_peak = int(np.argmax(foreground_histogram)) + foreground_start
    if foreground_peak <= background_level + 1:
        return min(background_level + 32, 255)

    valley_region = histogram[background_level + 1 : foreground_peak]
    if valley_region.size == 0:
        return min(background_level + 32, 255)

    threshold = int(np.argmin(valley_region)) + background_level + 1
    return threshold


def threshold_image(image: np.ndarray, args: argparse.Namespace) -> np.ndarray:
    if args.mode == "aggressive":
        working = image
        if not args.no_blur:
            working = cv2.GaussianBlur(working, (3, 3), 0)

        border = sample_border_pixels(working, args.bg_sample_width).astype(np.float32)
        background_color = np.median(border, axis=0)
        distance = np.linalg.norm(working.astype(np.float32) - background_color, axis=2)
        border_distance = np.linalg.norm(border - background_color, axis=1)
        cutoff = max(
            float(np.percentile(border_distance, 95)) + float(args.bg_margin),
            float(args.bg_margin),
        )
        binary = np.where(distance >= cutoff, 255, 0).astype(np.uint8)
    else:
        gray = to_grayscale(image)
        working = gray
        if not args.no_blur:
            working = cv2.GaussianBlur(working, (3, 3), 0)

        if args.mode == "manual":
            _, binary = cv2.threshold(
                working, int(args.threshold), 255, cv2.THRESH_BINARY
            )
        elif args.mode == "matched":
            matched_threshold = find_matched_threshold(
                working, args.bg_sample_width
            )
            _, binary = cv2.threshold(
                working, matched_threshold, 255, cv2.THRESH_BINARY
            )
        else:
            _, binary = cv2.threshold(
                working, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
            )

    if args.close_kernel > 1 and args.close_iterations > 0:
        kernel = np.ones((args.close_kernel, args.close_kernel), dtype=np.uint8)
        binary = cv2.morphologyEx(
            binary,
            cv2.MORPH_CLOSE,
            kernel,
            iterations=args.close_iterations,
        )

    foreground_ratio = float(np.count_nonzero(binary)) / float(binary.size)
    if foreground_ratio > 0.5:
        binary = cv2.bitwise_not(binary)

    return binary


def remove_small_components(binary: np.ndarray, min_area: float) -> np.ndarray:
    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    cleaned = np.zeros_like(binary)
    for label in range(1, component_count):
        area = float(stats[label, cv2.CC_STAT_AREA])
        if area >= min_area:
            cleaned[labels == label] = 255
    return cleaned


def contour_area_signed(points: np.ndarray) -> float:
    pts = points.reshape(-1, 2).astype(np.float64)
    x = pts[:, 0]
    y = pts[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def simplify_contour(contour: np.ndarray, epsilon_factor: float) -> np.ndarray | None:
    perimeter = cv2.arcLength(contour, True)
    epsilon = max(perimeter * epsilon_factor, 0.5)
    approx = cv2.approxPolyDP(contour, epsilon, True)
    points = approx.reshape(-1, 2)
    if len(points) < 3:
        return None

    deduped: list[np.ndarray] = []
    for point in points:
        if not deduped or not np.array_equal(point, deduped[-1]):
            deduped.append(point)

    if len(deduped) >= 2 and np.array_equal(deduped[0], deduped[-1]):
        deduped.pop()
    if len(deduped) < 3:
        return None

    reduced: list[np.ndarray] = []
    total = len(deduped)
    for index, point in enumerate(deduped):
        prev_point = deduped[index - 1]
        next_point = deduped[(index + 1) % total]
        v1 = point - prev_point
        v2 = next_point - point
        cross = int(v1[0] * v2[1] - v1[1] * v2[0])
        if cross != 0 or total <= 3:
            reduced.append(point)

    if len(reduced) < 3:
        return None
    return np.asarray(reduced, dtype=np.int32)


def normalize_winding(points: np.ndarray, clockwise: bool) -> np.ndarray:
    signed_area = contour_area_signed(points)
    is_clockwise = signed_area < 0
    if is_clockwise != clockwise:
        return points[::-1].copy()
    return points


def extract_paths(binary: np.ndarray, epsilon_factor: float) -> list[ContourPath]:
    contours, hierarchy = cv2.findContours(
        binary, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE
    )
    if hierarchy is None:
        return []

    hierarchy = hierarchy[0]
    paths: list[ContourPath] = []

    for index, meta in enumerate(hierarchy):
        parent = meta[3]
        if parent != -1:
            continue

        outer = simplify_contour(contours[index], epsilon_factor)
        if outer is None:
            continue
        outer = normalize_winding(outer, clockwise=True)

        holes: list[np.ndarray] = []
        child = meta[2]
        while child != -1:
            hole = simplify_contour(contours[child], epsilon_factor)
            if hole is not None:
                holes.append(normalize_winding(hole, clockwise=False))
            child = hierarchy[child][0]

        paths.append(ContourPath(outer=outer, holes=holes))

    return paths


def format_coord(value: float) -> str:
    rounded = round(float(value), 2)
    if rounded.is_integer():
        return str(int(rounded))
    return f"{rounded:.2f}".rstrip("0").rstrip(".")


def points_to_path_segment(points: np.ndarray) -> str:
    coords = points.reshape(-1, 2)
    start = coords[0]
    segments = [f"M {format_coord(start[0])} {format_coord(start[1])}"]
    for point in coords[1:]:
        segments.append(f"L {format_coord(point[0])} {format_coord(point[1])}")
    segments.append("Z")
    return " ".join(segments)


def collect_path_bounds(paths: Iterable[ContourPath]) -> tuple[float, float, float, float]:
    all_points: list[np.ndarray] = []
    for path in paths:
        all_points.append(path.outer.reshape(-1, 2))
        for hole in path.holes:
            all_points.append(hole.reshape(-1, 2))

    if not all_points:
        raise ValueError("No contour points were collected.")

    stacked = np.vstack(all_points).astype(np.float64)
    min_x = float(np.min(stacked[:, 0]))
    min_y = float(np.min(stacked[:, 1]))
    max_x = float(np.max(stacked[:, 0]))
    max_y = float(np.max(stacked[:, 1]))
    return min_x, min_y, max_x, max_y


def transform_points(
    points: np.ndarray,
    scale: float,
    offset_x: float,
    offset_y: float,
) -> np.ndarray:
    transformed = points.reshape(-1, 2).astype(np.float64).copy()
    transformed[:, 0] = transformed[:, 0] * scale + offset_x
    transformed[:, 1] = transformed[:, 1] * scale + offset_y
    return transformed


def normalize_paths_to_canvas(
    paths: list[ContourPath],
    canvas_size: float,
    subject_size: float,
) -> list[ContourPath]:
    min_x, min_y, max_x, max_y = collect_path_bounds(paths)
    subject_width = max_x - min_x
    subject_height = max_y - min_y
    longest_side = max(subject_width, subject_height)
    if longest_side <= 0:
        raise ValueError("Unable to normalize a zero-sized subject.")

    target_size = min(subject_size, canvas_size)
    scale = target_size / longest_side
    scaled_width = subject_width * scale
    scaled_height = subject_height * scale
    offset_x = (canvas_size - scaled_width) / 2.0 - min_x * scale
    offset_y = (canvas_size - scaled_height) / 2.0 - min_y * scale

    normalized: list[ContourPath] = []
    for path in paths:
        outer = transform_points(path.outer, scale, offset_x, offset_y)
        holes = [
            transform_points(hole, scale, offset_x, offset_y) for hole in path.holes
        ]
        normalized.append(ContourPath(outer=outer, holes=holes))
    return normalized


def build_avg(paths: Iterable[ContourPath], width: int, height: int, fill: str) -> dict:
    items = []
    for path in paths:
        path_data_parts = [points_to_path_segment(path.outer)]
        for hole in path.holes:
            path_data_parts.append(points_to_path_segment(hole))
        items.append(
            {
                "type": "path",
                "fill": fill,
                "pathData": " ".join(path_data_parts),
            }
        )

    return {
        "type": "AVG",
        "version": "1.2",
        "width": width,
        "height": height,
        "viewportWidth": width,
        "viewportHeight": height,
        "items": items,
    }


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_svg_preview(path: Path, avg: dict) -> None:
    width = avg["width"]
    height = avg["height"]
    paths = "\n".join(
        f'  <path d="{item["pathData"]}" fill="{item.get("fill", "none")}" '
        f'stroke="{item.get("stroke", "none")}" '
        f'stroke-width="{item.get("strokeWidth", 0)}" fill-rule="nonzero" />'
        for item in avg["items"]
    )
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}">\n'
        f"{paths}\n"
        f"</svg>\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg, encoding="utf-8")


def collect_input_files(path: Path, recursive: bool) -> list[Path]:
    if path.is_file():
        return [path]

    if not path.is_dir():
        raise FileNotFoundError(f"Input path does not exist: {path}")

    pattern = "**/*" if recursive else "*"
    files = [
        file_path
        for file_path in sorted(path.glob(pattern))
        if file_path.is_file() and file_path.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    if not files:
        raise SystemExit(f"No supported image files were found in: {path}")
    return files


def resolve_output_paths(
    source: Path, input_root: Path, args: argparse.Namespace
) -> tuple[Path, Path]:
    if input_root.is_file():
        avg_output = args.output or source.with_suffix(".avg.json")
        svg_output = args.svg or source.with_suffix(".svg")
        return avg_output, svg_output

    avg_root = args.output or Path("output")
    svg_root = args.svg or avg_root
    relative = source.relative_to(input_root)
    avg_output = avg_root / relative.with_suffix(".avg.json")
    svg_output = svg_root / relative.with_suffix(".svg")
    return avg_output, svg_output


def process_image(
    source: Path,
    avg_output: Path,
    svg_output: Path,
    args: argparse.Namespace,
) -> dict:
    image = load_image(source)
    binary = threshold_image(image, args)
    cleaned = remove_small_components(binary, args.min_area)
    paths = extract_paths(cleaned, args.approx_epsilon)

    if not paths:
        raise ValueError("No vectorizable foreground shapes were found.")

    canvas_size = max(1.0, float(args.canvas_size))
    subject_size = max(1.0, min(float(args.subject_size), canvas_size))
    normalized_paths = normalize_paths_to_canvas(paths, canvas_size, subject_size)
    canvas_pixels = int(round(canvas_size))
    avg = build_avg(
        normalized_paths,
        width=canvas_pixels,
        height=canvas_pixels,
        fill=args.fill,
    )
    write_json(avg_output, avg)
    write_svg_preview(svg_output, avg)

    return {
        "input": str(source),
        "output_avg": str(avg_output),
        "output_svg": str(svg_output),
        "paths": len(avg["items"]),
        "size": [canvas_pixels, canvas_pixels],
    }


def main() -> None:
    args = parse_args()
    sources = collect_input_files(args.input, recursive=args.recursive)
    results: list[dict] = []
    failures: list[dict] = []

    for source in sources:
        avg_output, svg_output = resolve_output_paths(source, args.input, args)
        try:
            result = process_image(source, avg_output, svg_output, args)
            results.append(result)
        except Exception as exc:
            failures.append({"input": str(source), "error": str(exc)})

    payload = {
        "mode": args.mode,
        "converted": len(results),
        "failed": len(failures),
        "results": results,
        "failures": failures,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
