# SISR — Single Image Super Resolution

ONNX Runtime + DirectML 超分辨率工具，支持 AMD GPU / CPU。

## 特性

- AMD GPU 原生加速 (DirectML)，无需 ROCm
- 分块处理 + feather 缝合，大图不爆显存
- GPU 不可用时自动回退 CPU
- 纯 Python，依赖少

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 获取模型

需要手动下载 Real-ESRGAN x4plus 的 ONNX 模型文件，放到 `models/realesrgan_x4plus.onnx`。

**推荐来源（浏览器打开下载）：**

| 来源 | 说明 |
|------|------|
| [HuggingFace — shm007/Real-ESRGAN-x4plus-onnx](https://huggingface.co/shm007/Real-ESRGAN-x4plus-onnx) | 知名社区 ONNX 导出 |
| [GitHub — xinntao/Real-ESRGAN Releases](https://github.com/xinntao/Real-ESRGAN/releases) | 官方 .pth 权重（需自行转换） |

> 如果你有代理或 VPN 能访问 HuggingFace，推荐第一个。否则可以尝试在搜索引擎搜索
> "Real-ESRGAN x4plus onnx 下载" 寻找国内镜像或网盘分享。

### 3. 运行超分

```bash
# 输入图像放到 input/ 目录
python -m src.main -i input/your_photo.jpg

# 输出默认保存到 output/your_photo_4x.png
```

## 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `-i, --input` | 输入图像路径 | (必填) |
| `-o, --output` | 输出图像路径 | `output/<文件名>_4x.png` |
| `-m, --model` | ONNX 模型路径 | `models/realesrgan_x4plus.onnx` |
| `--tile-size` | tile 边长 (像素) | `256` |
| `--overlap` | tile 重叠像素 | `32` |
| `--cpu` | 强制使用 CPU | 关闭 |
| `--device-id` | GPU 设备 ID | `0` |
| `--verbose` | 详细日志 | 关闭 |

## 项目结构

```
SISR/
├── src/
│   ├── engine.py          # ONNX Runtime 推理引擎
│   ├── processor.py       # 图像加载/分块/缝合
│   └── main.py            # CLI 主入口
├── models/                # 放 ONNX 模型文件
├── input/                 # 输入图像
├── output/                # 输出图像
├── requirements.txt
└── README.md
```

## 技术细节

- **DirectML** 是微软硬件加速库，原生支持 AMD / Intel / NVIDIA GPU
- 大图分块 (tile) + 高斯 feather 混合，消除边界伪影
- 模型：Real-ESRGAN x4plus，4× 超分，擅长处理 JPEG 压缩、模糊、噪声
