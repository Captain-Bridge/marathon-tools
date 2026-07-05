"""
SISR 主入口 — 单图双倍率超分辨率 pipeline。

每次运行自动使用 x2 和 x4 模型，分别输出到 output_x2/ 和 output_x4/。

用法:
    python -m src.main -i input/photo.jpg

    # 自定义 tile 参数
    python -m src.main -i input/photo.jpg --tile-size 256 --overlap 32

    # 强制使用 CPU
    python -m src.main -i input/photo.jpg --cpu
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import numpy as np

from .engine import InferenceEngine
from .processor import (
    DEFAULT_TILE_OVERLAP,
    DEFAULT_TILE_SIZE,
    TileMerger,
    _pad_for_tiling,
    load_image,
    save_image,
    split_tiles,
)

logger = logging.getLogger(__name__)

# 固定模型映射
MODELS: dict[int, str] = {
    2: "models/Real-ESRGAN_x2plus.onnx",
    4: "models/Real-ESRGAN-x4plus.onnx",
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="SISR — 单图双倍率超分辨率 (x2 + x4, AMD GPU / CPU)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("-i", "--input", required=True, help="输入图像路径")
    p.add_argument("--tile-size", type=int, default=DEFAULT_TILE_SIZE,
                   help=f"tile 边长 (默认: {DEFAULT_TILE_SIZE})")
    p.add_argument("--overlap", type=int, default=DEFAULT_TILE_OVERLAP,
                   help=f"tile 重叠像素 (默认: {DEFAULT_TILE_OVERLAP})")
    p.add_argument("--cpu", action="store_true", help="强制使用 CPU")
    p.add_argument("--device-id", type=int, default=0, help="GPU 设备 ID (默认: 0)")
    p.add_argument("--verbose", action="store_true", help="详细日志")
    return p.parse_args(argv)


def _run_single(
    img: np.ndarray,
    model_path: str,
    output_dir: Path,
    input_stem: str,
    *,
    provider: str | None,
    device_id: int,
    tile_size: int,
    overlap: int,
) -> None:
    """对单张图像执行一个模型的完整超分 pipeline。

    推理结果直接保存到 output_dir / <input_stem>.png。
    """
    engine = InferenceEngine(str(model_path), provider=provider, device_id=device_id)
    sf = engine.scale_factor

    # 拼接输出路径
    output_path = output_dir / f"{input_stem}.png"

    # ── 分块推理 ──
    padded, (orig_h, orig_w), (pad_h, pad_w) = _pad_for_tiling(
        img, tile_size, overlap, sf
    )
    merger = TileMerger(pad_h * sf, pad_w * sf, orig_h * sf, orig_w * sf)

    t_start = time.perf_counter()
    tile_count = 0

    for tile, y, x, ye, xe in split_tiles(
        img, tile_size=tile_size, overlap=overlap, scale=sf
    ):
        sr_tile = engine.run(tile)
        merger.add_tile(sr_tile, y * sf, x * sf, ye * sf, xe * sf, overlap * sf)
        tile_count += 1

        if tile_count % 10 == 0 or tile_count == 1:
            logger.info("    [%d×] 已处理 %d tile...", sf, tile_count)

    result = merger.finalize()
    elapsed = time.perf_counter() - t_start

    logger.info(
        "    [%d×] 推理完成: %d tile, 耗时 %.2fs (%.1f tile/s)",
        sf, tile_count, elapsed,
        tile_count / elapsed if elapsed > 0 else 0,
    )

    # ── 保存 ──
    save_image(result, str(output_path))
    logger.info("    [%d×] 输出: %s  (%dx%d)", sf, output_path, result.shape[1], result.shape[0])


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # 日志
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # ── 验证输入 ──
    input_path = Path(args.input)
    if not input_path.is_file():
        logger.error("输入文件不存在: %s", input_path)
        return 1

    # 验证模型
    for scale, model_rel in MODELS.items():
        model_path = Path(model_rel)
        if not model_path.is_file():
            logger.error(
                "模型文件不存在 (x%d): %s\n"
                "请将 ONNX 模型放到 models/ 目录下。",
                scale, model_path,
            )
            return 1

    provider: str | None = "cpu" if args.cpu else None

    # ── 加载图像（仅一次） ──
    logger.info("加载图像...")
    img = load_image(str(input_path))
    logger.info("图像尺寸: %dx%d", img.shape[1], img.shape[0])

    total_start = time.perf_counter()

    # ── 依次执行 x2 → x4 ──
    for scale, model_rel in MODELS.items():
        output_dir = Path("output") / f"x{scale}"
        output_dir.mkdir(parents=True, exist_ok=True)

        logger.info("── 开始 %d× 超分 ──", scale)
        _run_single(
            img,
            model_rel,
            output_dir,
            input_path.stem,
            provider=provider,
            device_id=args.device_id,
            tile_size=args.tile_size,
            overlap=args.overlap,
        )

    total_elapsed = time.perf_counter() - total_start
    logger.info("全部完成，总耗时 %.2fs", total_elapsed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
