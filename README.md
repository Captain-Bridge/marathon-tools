# Marathon Tools

Bungie《马拉松》（Marathon）相关的工具集合，涵盖图像处理、OCR 识别、超分辨率、音频处理、新闻抓取与翻译等。

## 目录结构

```
marathon_tools/
├── OCR/                  # 中文 OCR 文字识别工具
├── SISR/                 # 图像超分辨率（Real-ESRGAN + ONNX/DirectML）
├── audio_cut/            # 音频/视频静音裁剪
├── img2svg/              # 图片转 AVG JSON + SVG 矢量
├── img_axis/             # 图片坐标定位器（地图 POI 标记）
├── img_cut/              # 图片批量裁剪
├── news/                 # Marathon 新闻抓取与翻译管线
│   ├── src/              # 抓取脚本
│   ├── trans/            # 翻译管线（DeepSeek / OpenAI 兼容）
│   └── sample-*.json     # 生成的新闻数据样例
├── trans/                # 本地翻译运行时（Helsinki-NLP）
├── marathon/             # 游戏素材资源
├── 地图/                  # 游戏地图资源
└── 阵营/                  # 阵营数据
```

---

## 子项目说明

### OCR — 中文 OCR 文字识别工具

基于 Python 的桌面 OCR 工具，以中文识别为主，兼顾中英混排。

- **图形界面**：支持剪切板粘贴、本地图片打开、识别结果可编辑复制
- **双引擎**：优先使用本机 Tesseract（`chi_sim + eng`），自动回退到 Windows 原生 OCR
- **环境**：Windows 10 / 11

```bash
cd OCR
pip install -r requirements.txt
python app.py
```

详见 [OCR/README.md](OCR/README.md)

---

### SISR — 图像超分辨率

基于 Real-ESRGAN + ONNX Runtime + DirectML 的超分辨率工具，支持 AMD/NVIDIA/Intel GPU 加速。

- 4× 和 2× 两种放大倍率
- 分块处理 + feather 缝合，大图不爆显存
- GPU 不可用时自动回退 CPU

```bash
cd SISR
pip install -r requirements.txt
# 需先手动下载 ONNX 模型到 models/ 目录
python -m src.main -i input/your_photo.jpg
```

详见 [SISR/README.md](SISR/README.md)

---

### img2svg — 图片转 SVG 矢量

将深色背景、浅色前景的简单图标/图形转换为 AVG JSON 及 SVG 文件。

- 输出统一为 128×128 正方形画板，主体居中缩放
- 支持单图、批量、递归处理
- 多种分割模式：激进（默认）、Otsu、手动阈值

```bash
cd img2svg
pip install opencv-python numpy
python img2svg.py input                    # 批量处理 input/ 目录
python img2svg.py input/world.png          # 单张图片
python img2svg.py input --recursive        # 递归处理
```

详见 [img2svg/README.md](img2svg/README.md)

---

### img_axis — 图片坐标定位器

在地图上点击标记 POI 像素坐标的桌面工具。

- tkinter 图形界面，支持滚轮缩放、拖拽平移
- SVG 图标标记（从 `icons/poi-icons.json` 加载配置）
- 每个标记可附带标题、描述和来源
- Ctrl+Z 撤销 / Ctrl+Y 重做，Ctrl+S 导出为 JSON

```bash
cd img_axis
pip install Pillow pyperclip cairosvg
python img_axis.py
```

详见 [img_axis/README.md](img_axis/README.md)

---

### img_cut — 图片批量裁剪

将图片统一裁剪到指定尺寸，居中处理。

- 目标尺寸：670×670 像素
- 保留上方、底部裁切、左右居中
- 支持的格式：JPG、PNG、BMP、WebP、TIFF

```bash
cd img_cut
pip install Pillow
python crop_images.py
```

详见 [img_cut/README.md](img_cut/README.md)

---

### audio_cut — 音频/视频静音裁剪

自动检测并裁剪视频/音频文件头尾的静音部分，输出 Opus 格式音频。

- 支持常见视频格式（MP4、MKV、MOV、AVI、WebM 等）
- 静音检测阈值和时长可调
- 依赖 `ffmpeg` / `ffprobe`

```bash
cd audio_cut
pip install numpy
python process_audio.py --input input/ --output outputs/
```

---

### news — Marathon 新闻抓取

通过 Bungie 站点的公开 ContentStack GraphQL API 抓取 Marathon 新闻结构化数据。

- 支持英文和简体中文
- 一键生成多种格式：英文全量、中文全量、中英对照、翻译对齐

```bash
cd news
npm install
npm run fetch:marathon-news            # 抓取英文新闻
npm run fetch:marathon-news:zh-chs     # 抓取中文新闻
npm run generate:marathon-news:all     # 生成全部产物
```

详见 [news/README.md](news/README.md)

---

### news/trans — 新闻翻译管线

将 Marathon 英文新闻翻译为简体中文，保留 HTML 结构、链接和占位符。

- 支持多种后端：DeepSeek API、OpenAI 兼容接口、本地模型、Mock 模式
- 术语管理和对齐工具

```bash
cd news/trans
pip install -r requirements.txt
python translate_news.py translate-bilingual-json --provider deepseek
```

配置方式：
- 推荐使用环境变量（`DEEPSEEK_API_KEY` 等）
- 或复制 `local_config.example.json` 为 `local_config.json` 并填入 key

详见 [news/trans/README.md](news/trans/README.md)

---

### trans — 本地翻译运行时

基于 Helsinki-NLP 模型的本地英译中翻译服务，作为上述翻译管线的离线回退方案。

- 默认模型：`Helsinki-NLP/opus-mt-en-zh`
- 自动保留 HTML 标签、URL、占位符 token

```bash
cd trans
pip install -r requirements.txt
```

详见 [trans/README.md](trans/README.md)

---

### marathon / 地图 / 阵营

游戏相关的素材资源、地图图片和阵营数据文件。

---

## 环境要求

| 模块 | 依赖 |
|------|------|
| OCR | Python 3.x, Pillow, winrt, Tesseract（可选） |
| SISR | Python 3.x, numpy, Pillow, onnxruntime-directml |
| img2svg | Python 3.10+, opencv-python, numpy |
| img_axis | Python 3.x, Pillow, pyperclip, cairosvg（可选） |
| img_cut | Python 3.x, Pillow |
| audio_cut | Python 3.x, numpy, ffmpeg |
| news | Node.js |
| news/trans | Python 3.x, openai |
| trans | Python 3.x, torch, transformers, sentencepiece |

---

## 配置隐私提醒

- 本项目使用 `.gitignore` 排除了 `local_config.json` 等包含 API key 的配置文件
- 如需配置 API key，请复制 `news/trans/local_config.example.json` 为 `local_config.json`，然后填入自己的 key
- 推荐使用环境变量而非硬编码，详细说明见 [news/trans/README.md](news/trans/README.md)

---

## 许可证

供个人使用。
