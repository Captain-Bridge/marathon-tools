"""
图像前/后处理模块。

核心功能：
- 加载任意格式图像 → float32 tensor (NCHW, [0,1])
- 分块 (tiling)：将大图切分为带重叠的 tile
- 缝合 (merging)：tile 按 feather 权重合并回完整图像
- 保存结果图像

为什么需要分块：
- GPU 显存有限，超大图像无法一次推理
- 重叠 + feather 混合消除 tile 边界的接缝伪影
"""

from __future__ import annotations

import logging
import math
from typing import Iterator, Tuple

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


# ── 公共常量 ──────────────────────────────────────────────────────────
DEFAULT_TILE_SIZE = 128          # tile 边长 (像素)，用于网络输入
DEFAULT_TILE_OVERLAP = 16        # tile 重叠像素数
FEATHER_SIGMA_SCALE = 0.15       # feather 高斯核 sigma = overlap * 此值


# ── 图像加载 / 保存 ───────────────────────────────────────────────────
def load_image(path: str) -> np.ndarray:
    """加载图像，返回 float32 RGB numpy 数组 (H, W, 3)，值域 [0, 1]。

    自动处理灰度图（扩展为 RGB）和 RGBA（丢弃 alpha）。
    """
    img = Image.open(path)
    logger.info("加载图像: %s  mode=%s  size=%s", path, img.mode, img.size)

    if img.mode == "RGBA":
        img = img.convert("RGB")
    elif img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    elif img.mode == "L":
        img = img.convert("RGB")

    arr = np.array(img, dtype=np.float32) / 255.0
    return arr


def save_image(arr: np.ndarray, path: str) -> None:
    """保存 float32 (H, W, 3) [0,1] 数组为 8-bit 图像。"""
    arr = np.clip(arr, 0.0, 1.0)
    arr = (arr * 255.0).round().astype(np.uint8)
    img = Image.fromarray(arr, mode="RGB")
    img.save(path)
    logger.info("保存图像: %s  size=%s", path, img.size)


# ── 分块 (Tiling) ─────────────────────────────────────────────────────
def _pad_for_tiling(
    arr: np.ndarray, tile_size: int, overlap: int, scale: int
) -> Tuple[np.ndarray, Tuple[int, int], Tuple[int, int]]:
    """对图像做镜像填充，使其能被有效的 tile stride 整除。

    Returns:
        (padded_arr, original_hw, padded_hw)
    """
    stride = tile_size - overlap  # 有效步长
    h, w = arr.shape[:2]

    # 目标尺寸：最接近的能完整覆盖的尺寸
    target_h = math.ceil(h / stride) * stride + overlap
    target_w = math.ceil(w / stride) * stride + overlap

    # 确保至少有一个完整 tile
    target_h = max(target_h, tile_size)
    target_w = max(target_w, tile_size)

    pad_h = target_h - h
    pad_w = target_w - w

    if pad_h > 0 or pad_w > 0:
        arr = np.pad(arr, ((0, pad_h), (0, pad_w), (0, 0)), mode="reflect")

    logger.debug("填充: %dx%d → %dx%d (pad +%d, +%d)", h, w, target_h, target_w, pad_h, pad_w)
    return arr, (h, w), (target_h, target_w)


def _tile_coords(
    h: int, w: int, tile_size: int, overlap: int
) -> Iterator[Tuple[int, int, int, int]]:
    """生成所有 tile 的坐标 (y_start, x_start, y_end, x_end)。"""
    stride = tile_size - overlap
    for y in range(0, h - overlap, stride):
        for x in range(0, w - overlap, stride):
            ye = min(y + tile_size, h)
            xe = min(x + tile_size, w)
            # 边缘 tile 对齐到底部/右侧
            if ye == h:
                y = h - tile_size
            if xe == w:
                x = w - tile_size
            yield (y, x, ye, xe)


def split_tiles(
    arr: np.ndarray,
    *,
    tile_size: int = DEFAULT_TILE_SIZE,
    overlap: int = DEFAULT_TILE_OVERLAP,
    scale: int = 4,
) -> Iterator[Tuple[np.ndarray, int, int, int, int]]:
    """将 (H,W,C) 图像切分为 NCHW tile 迭代器。

    Yields:
        (tile_nchw, y, x, ye, xe) — tile 为 (1,C,H,W) float32。
    """
    arr, (orig_h, orig_w), (pad_h, pad_w) = _pad_for_tiling(arr, tile_size, overlap, scale)

    for y, x, ye, xe in _tile_coords(pad_h, pad_w, tile_size, overlap):
        tile = arr[y:ye, x:xe, :]                          # (th, tw, 3)
        tile = np.transpose(tile, (2, 0, 1))                # (3, th, tw)
        tile = np.expand_dims(tile, axis=0).astype(np.float32)  # (1, 3, th, tw)
        yield (tile, y, x, ye, xe)


# ── 缝合 (Merging) ────────────────────────────────────────────────────
def _feather_kernel_1d(size: int, sigma: float) -> np.ndarray:
    """生成一维高斯权重，边缘平滑过渡。"""
    half = size / 2
    xs = np.arange(size, dtype=np.float32)
    weights = np.exp(-((xs - half + 0.5) ** 2) / (2 * sigma**2))
    return weights


def _feather_weight(
    tile_h: int, tile_w: int, overlap: int
) -> np.ndarray:
    """为 tile 生成 feather 权重图 (H, W)，边缘权重低、中心权重高。

    Feather 宽度等于 overlap，在 tile 四条边上做一个高斯衰减，
    使得相邻 tile 重叠区域的混合权重之和 ≈ 1。
    """
    sigma = max(overlap * FEATHER_SIGMA_SCALE, 0.5)

    wy = np.ones(tile_h, dtype=np.float32)
    wx = np.ones(tile_w, dtype=np.float32)

    if overlap > 0:
        k = _feather_kernel_1d(overlap * 2, sigma)
        wy[:overlap] = k[:overlap]
        wy[-overlap:] = k[overlap:]
        wx[:overlap] = k[:overlap]
        wx[-overlap:] = k[overlap:]

    weight = np.outer(wy, wx)
    return weight[:, :, np.newaxis]  # (H, W, 1) 便于广播


class TileMerger:
    """将推理后的 tile 缝合回完整图像，使用 feather 混合消除接缝。"""

    def __init__(
        self,
        padded_h: int,
        padded_w: int,
        original_h: int,
        original_w: int,
        channels: int = 3,
    ) -> None:
        self.accum = np.zeros((padded_h, padded_w, channels), dtype=np.float64)
        self.weight_sum = np.zeros((padded_h, padded_w, channels), dtype=np.float64)
        self.original_h = original_h
        self.original_w = original_w

    def add_tile(
        self, tile: np.ndarray, y: int, x: int, ye: int, xe: int, overlap: int
    ) -> None:
        """将一个推理后的 tile 累加到累加器。

        Args:
            tile: 形状 (1, C, H', W')，值域 [0, 1]。
            y, x, ye, xe: tile 在 padded 图像中的位置。
        """
        tile = tile[0].transpose(1, 2, 0)  # (H', W', C)

        th, tw = tile.shape[:2]

        # 限制在 accum 边界内
        ah, aw = self.accum.shape[:2]
        ey = min(y + th, ah)
        ex = min(x + tw, aw)

        # 裁剪 tile 和 weight 匹配有效区域
        th_eff = ey - y
        tw_eff = ex - x
        tile = tile[:th_eff, :tw_eff, :]
        weight = _feather_weight(th_eff, tw_eff, overlap)

        self.accum[y:ey, x:ex] += tile * weight
        self.weight_sum[y:ey, x:ex] += weight

    def finalize(self) -> np.ndarray:
        """归一化并裁剪回原始尺寸，返回 (H, W, C) float32。"""
        # 避免除零
        mask = self.weight_sum > 0
        self.accum[mask] /= self.weight_sum[mask]
        # 裁剪回原始尺寸
        result = self.accum[: self.original_h, : self.original_w, :]
        return result.astype(np.float32)
