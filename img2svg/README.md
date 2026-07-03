# img2svg

把深色背景、浅色前景的简单图片转换为 Alexa Vector Graphics (AVG) JSON，同时输出一个 SVG 文件。

当前输出会统一规范到：

- 正方形画板 `128×128`
- 视觉主体等比缩放后居中
- 主体最大宽或高约为 `100`

当前版本针对这个项目里的输入风格做了更激进的优化，默认假设：

- 背景颜色稳定，并且和图片边缘区域一致
- 前景颜色稳定，且明显区别于背景
- 图形主要由块状区域、粗线条、矩形轮廓和少量斜线组成
- 输入噪声较少，或只有少量散点

默认分割模式会直接从图片边缘采样背景颜色，再把和背景差异足够大的像素视为前景。这比原来的 Otsu 阈值更激进，但更适合这个仓库当前这批图。

## 依赖

- Python 3.10+
- `opencv-python`
- `numpy`

## 单张图片

```bash
python img2svg.py input/career.png
```

默认输出：

- `input/career.avg.json`
- `input/career.svg`

## 整个文件夹批量转换

```bash
python img2svg.py input
```

默认会把批量结果写到 `output/`：

- `output/career.avg.json`
- `output/career.svg`
- `output/partys.avg.json`
- `output/partys.svg`
- ...

如果需要递归扫描子目录：

```bash
python img2svg.py input --recursive
```

## 常用参数

```bash
python img2svg.py input/world.png --approx-epsilon 0.006 --min-area 20
python img2svg.py input --output output_avg --svg output_svg
python img2svg.py input/world.png --mode otsu
python img2svg.py input/world.png --mode manual --threshold 180
```

## 参数说明

- `input`: 输入图片路径，或者包含多张图片的目录
- `-o, --output`: 单图时表示 AVG 输出文件；目录模式下表示 AVG 输出目录
- `--svg` 或 `--preview-svg`: 单图时表示 SVG 输出文件；目录模式下表示 SVG 输出目录
- `--mode`: 分割模式，默认 `aggressive`
- `--threshold`: 手动阈值，仅在 `--mode manual` 时使用
- `--min-area`: 去掉小连通域的最小面积
- `--approx-epsilon`: 轮廓简化强度，越小越贴近原图，越大越规整
- `--fill`: 输出填充色，默认 `#898C99`，也就是 RGB `(137, 140, 153)`
- `--no-blur`: 禁用分割前的轻微模糊
- `--bg-sample-width`: 激进模式下用于采样背景的边缘宽度
- `--bg-margin`: 激进模式下，像素与背景颜色的最小差异阈值增量
- `--close-kernel`: 分割后闭运算核大小
- `--close-iterations`: 分割后闭运算迭代次数
- `--recursive`: 输入为目录时递归处理子目录
- `--canvas-size`: 输出正方形画板尺寸，默认 `128`
- `--subject-size`: 主体最大宽或高的目标尺寸，默认 `100`

## 调参建议

- 如果前景边缘被吃掉了，先减小 `--bg-margin`
- 如果有细小断裂，增大 `--close-kernel` 或 `--close-iterations`
- 如果轮廓太碎，增大 `--approx-epsilon`
- 如果有散点噪声，增大 `--min-area`
- 如果遇到不符合当前输入风格的图片，可以切回 `--mode otsu`
