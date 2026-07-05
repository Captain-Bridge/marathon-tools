# img_axis — 图片坐标定位器

在地图上点击标记 POI 坐标的桌面工具，支持 SVG 图标标记、缩放平移、撤销重做，标记结果可导出为 JSON。

## 功能

- 打开本地图片，点击即可标记像素坐标（左上角为原点）
- 支持 SVG 图标标记（从 `icons/poi-icons.json` 加载配置）
- 鼠标滚轮缩放（带防抖）+ 拖拽平移
- 每个标记可附带标题、描述和来源信息
- Ctrl+Z 撤销 / Ctrl+Y 重做
- 中键删除标记
- Ctrl+S 导出标记为 JSON 文件
- 支持命令行传参直接打开图片

## 依赖

- Python 3.x
- Pillow
- pyperclip
- cairosvg（可选，用于 SVG 图标渲染；需要 Cairo 系统库）

```bash
pip install Pillow pyperclip cairosvg
```

## 使用

```bash
# 直接启动 GUI
python img_axis.py

# 命令行指定图片
python img_axis.py cyro_archive_basemap_4x.png
```

启动后：

1. 通过菜单或命令行打开一张地图/图片
2. 在左侧工具栏选择图标类型（或默认十字线）
3. 点击图片上的位置添加标记
4. 右键可编辑标记的标题、描述、来源
5. Ctrl+S 将所有标记保存为 JSON

## 输出格式

保存的 JSON 文件结构：

```json
{
  "image": "原始图片文件名",
  "marks": [
    {
      "id": "mark_1",
      "x": 1234,
      "y": 567,
      "icon": "extract-active",
      "title": "撤离点 A",
      "description": "地图北侧撤离点",
      "source": "官方地图"
    }
  ]
}
```

## 图标配置

图标定义在 `icons/poi-icons.json`，支持排序和分类。SVG 图标文件需放在 `icons/` 目录下对应路径。

## 快捷键

| 快捷键 | 操作 |
|--------|------|
| 滚轮 | 缩放图片 |
| 拖拽 | 平移画布 |
| Ctrl+Z | 撤销上一个标记 |
| Ctrl+Y | 重做被撤销的标记 |
| 中键点击 | 删除最近标记 |
| Ctrl+S | 保存标记为 JSON |
