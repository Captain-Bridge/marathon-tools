"""
ONNX Runtime 推理引擎，优先使用 DirectML (AMD GPU)，回退到 CPU。

设计要点：
- 自动检测可用执行提供程序 (DirectML → CPU)
- 支持动态输入尺寸的 ONNX 模型
- 单张 tile 推理，批量处理由 processor 层的分块逻辑控制
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import onnxruntime as ort

logger = logging.getLogger(__name__)


class InferenceEngine:
    """ONNX Runtime 推理引擎封装。"""

    def __init__(
        self,
        model_path: str,
        *,
        provider: Optional[str] = None,
        device_id: int = 0,
    ) -> None:
        """
        Args:
            model_path: ONNX 模型文件路径。
            provider: 强制指定执行提供程序 ('dml' 或 'cpu')。
                      为 None 时自动选择：优先 DirectML，不可用时回退 CPU。
            device_id: GPU 设备 ID（DirectML 下生效）。
        """
        self.model_path = model_path
        self.device_id = device_id

        available = ort.get_available_providers()
        logger.info("可用执行提供程序: %s", available)

        # 选择 provider
        if provider is None:
            provider = self._auto_select_provider(available)

        self.provider = provider
        self.session = self._create_session(provider)
        self._inspect_model()

        logger.info("引擎就绪: provider=%s, model=%s", provider, model_path)

    # ------------------------------------------------------------------
    def _auto_select_provider(self, available: list[str]) -> str:
        """自动选择最优 provider。"""
        if "DmlExecutionProvider" in available:
            logger.info("检测到 DirectML — 使用 AMD GPU 加速")
            return "dml"
        if "CUDAExecutionProvider" in available:
            logger.info("检测到 CUDA — 使用 NVIDIA GPU 加速")
            return "cuda"
        logger.warning("未检测到 GPU provider，回退到 CPU")
        return "cpu"

    def _create_session(self, provider: str) -> ort.InferenceSession:
        """创建 ONNX Runtime 会话。"""
        if provider == "dml":
            opts = ort.SessionOptions()
            # 禁用图优化以避免某些 DML 兼容性问题
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
            return ort.InferenceSession(
                self.model_path,
                sess_options=opts,
                providers=["DmlExecutionProvider"],
                provider_options=[{"device_id": str(self.device_id)}],
            )
        elif provider == "cuda":
            return ort.InferenceSession(
                self.model_path,
                providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
            )
        else:
            opts = ort.SessionOptions()
            # CPU 下开启完整图优化
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            opts.intra_op_num_threads = 0  # 自动
            return ort.InferenceSession(
                self.model_path,
                sess_options=opts,
                providers=["CPUExecutionProvider"],
            )

    def _inspect_model(self) -> None:
        """读取并记录模型 I/O 信息。"""
        self.input_name = self.session.get_inputs()[0].name
        self.input_shape = self.session.get_inputs()[0].shape
        self.output_name = self.session.get_outputs()[0].name
        self.output_shape = self.session.get_outputs()[0].shape

        # 模型固定输入尺寸 (H, W)
        self.model_h = self.input_shape[2]
        self.model_w = self.input_shape[3]

        logger.info("模型输入:  name=%s  shape=%s", self.input_name, self.input_shape)
        logger.info("模型输出:  name=%s  shape=%s", self.output_name, self.output_shape)

    # ------------------------------------------------------------------
    def run(self, tile: np.ndarray) -> np.ndarray:
        """对单张 tile 执行推理，自动适配模型输入尺寸。

        如果 tile 尺寸与模型固定输入不一致，先用双线性插值缩放，
        推理后再按放大倍数缩放回目标尺寸。

        Args:
            tile: 形状为 (1, C, H, W) 的 float32 numpy 数组，值域 [0, 1]。

        Returns:
            形状为 (1, C, H*scale, W*scale) 的 float32 numpy 数组。
        """
        _, _, th, tw = tile.shape

        # 如果 tile 尺寸与模型输入一致，直接推理
        if th == self.model_h and tw == self.model_w:
            outputs = self.session.run(
                [self.output_name],
                {self.input_name: tile},
            )
            return outputs[0]

        # 不一致时，缩放 → 推理 → 缩放回
        from PIL import Image

        sf = self.scale_factor

        # Step 1: 缩放到模型输入尺寸
        img_in = tile[0].transpose(1, 2, 0)  # (H, W, C)
        img_in = (img_in * 255).clip(0, 255).astype("uint8")
        pil_in = Image.fromarray(img_in)
        pil_in = pil_in.resize((self.model_w, self.model_h), Image.BILINEAR)
        arr_in = np.array(pil_in, dtype=np.float32) / 255.0
        arr_in = np.transpose(arr_in, (2, 0, 1))[np.newaxis, ...]  # (1, C, H, W)

        # Step 2: 推理
        outputs = self.session.run(
            [self.output_name],
            {self.input_name: arr_in},
        )
        sr = outputs[0]  # (1, C, model_h*sf, model_w*sf)

        # Step 3: 缩放回目标尺寸
        sr_img = sr[0].transpose(1, 2, 0)
        sr_img = (sr_img * 255).clip(0, 255).astype("uint8")
        target_h, target_w = th * sf, tw * sf
        pil_out = Image.fromarray(sr_img)
        pil_out = pil_out.resize((target_w, target_h), Image.BILINEAR)
        arr_out = np.array(pil_out, dtype=np.float32) / 255.0
        arr_out = np.transpose(arr_out, (2, 0, 1))[np.newaxis, ...]

        return arr_out

    @property
    def scale_factor(self) -> int:
        """从模型输出/输入尺寸比例推断放大倍数。

        优先用静态 shape 计算，遇到动态维度时通过一次 dummy 推理确定。
        """
        try:
            return self.output_shape[2] // self.input_shape[2]
        except TypeError:
            # 动态输出维度 — 用 dummy 输入跑一次推理
            dummy = np.zeros(
                (1, 3, self.model_h, self.model_w), dtype=np.float32
            )
            outputs = self.session.run(
                [self.output_name], {self.input_name: dummy}
            )
            return outputs[0].shape[2] // self.model_h
