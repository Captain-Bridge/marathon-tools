# 中文 OCR 文字识别工具

一个基于 Python 的桌面 OCR 工具，优先使用本机 `Tesseract` 中文模型，必要时回退到 Windows 原生 OCR，适合以中文为主的图片文字识别场景。

## 功能

- 图形界面操作
- 支持从剪切板读取图片
- 支持选择本地图片文件
- OCR 识别结果可直接复制
- 识别结果展示在可编辑文本框中，方便二次整理
- 优先走 `chi_sim + eng` 中文识别，兼顾中英混排

## 安装依赖

```bash
pip install -r requirements.txt
```

## 运行

```bash
python app.py
```

## 使用方式

1. 点击“打开图片”选择本地图片，或点击“读取剪切板”导入截图/复制的图片。
2. 点击“开始识别”执行 OCR。
3. 在右侧文本框查看结果，点击“复制结果”即可复制文字。

## 说明

- 推荐在 Windows 环境下使用，`Ctrl+V` 可直接尝试从剪切板导入图片。
- 如果本机已安装 `C:\Program Files\Tesseract-OCR\tesseract.exe`，程序会优先使用 `chi_sim + eng` 模型进行识别。
- 如果没有安装 Tesseract，程序会自动回退到 Windows 原生 OCR；如果系统缺少中文 OCR 支持，请在 Windows 语言设置中安装简体中文相关语言功能。
- 由于使用的是 Windows 原生 OCR，本工具更适合 Windows 10 / 11 环境。
