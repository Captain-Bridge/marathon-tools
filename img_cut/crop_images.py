from __future__ import annotations

from pathlib import Path

from PIL import Image


INPUT_DIR = Path("input")
OUTPUT_DIR = Path("output")
TARGET_WIDTH = 670
TARGET_HEIGHT = 670
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}


def crop_image(image_path: Path, output_path: Path) -> str:
    with Image.open(image_path) as img:
        width, height = img.size

        top = 0
        bottom = min(height, TARGET_HEIGHT)

        if width >= TARGET_WIDTH:
            left = (width - TARGET_WIDTH) // 2
            right = left + TARGET_WIDTH
        else:
            left = 0
            right = width

        cropped = img.crop((left, top, right, bottom))
        cropped.save(output_path)

        notes: list[str] = []
        if height < TARGET_HEIGHT:
            notes.append(
                f"height kept at {height}px because it is smaller than {TARGET_HEIGHT}px"
            )
        if width < TARGET_WIDTH:
            notes.append(
                f"width kept at {width}px because it is smaller than {TARGET_WIDTH}px"
            )

        if notes:
            return "; ".join(notes)
        return "cropped to 670x670"


def main() -> None:
    INPUT_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)

    image_files = sorted(
        path for path in INPUT_DIR.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )

    if not image_files:
        print("No supported images found in the input folder.")
        return

    for image_path in image_files:
        output_path = OUTPUT_DIR / image_path.name
        try:
            result = crop_image(image_path, output_path)
            print(f"Processed: {image_path.name} -> {output_path} ({result})")
        except Exception as exc:
            print(f"Skipped: {image_path.name} ({exc})")


if __name__ == "__main__":
    main()
