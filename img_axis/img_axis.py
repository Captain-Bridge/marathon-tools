"""
图片坐标定位器 — 点击图片获取像素坐标 (左上角为原点)
支持: 菜单打开 / 命令行传参 / 滚轮缩放(带防抖) / 拖拽平移 / 一键复制坐标
      Ctrl+Z 撤销 / 中键删除标记 / 图标标记
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk
import pyperclip
import os
import sys
import math
import json
import io


# 标记附近的命中距离（图片像素，非屏幕像素）
MARK_HIT_RADIUS = 18
# 图标基础大小（scale=1.0 时的 canvas 像素）
ICON_BASE_SIZE = 48
# 图标最小/最大渲染尺寸
ICON_MIN_SIZE = 8
ICON_MAX_SIZE = 256


class ImageAxisApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("图片坐标定位器")
        self.root.geometry("1100x750")
        self.root.minsize(600, 400)

        # ---------- 状态 ----------
        self.pil_image: Image.Image | None = None
        self.tk_image: ImageTk.PhotoImage | None = None
        self.scale = 1.0
        self.offset_x = 0
        self.offset_y = 0
        self.canvas_img_id = None

        # 标记列表: [ { "x":int, "y":int, "ids":[...], "icon":str|None, "id":str, "title":str, "description":str, "source":str }, ... ]
        self.marks: list[dict] = []
        self._mark_counter: int = 0   # 全局标记序号，用于生成持久 id

        # 防抖: 延迟重绘的 after id
        self._redraw_after_id: str | None = None

        # 资源路径
        if getattr(sys, 'frozen', False):
            self.base_dir = os.path.dirname(sys.executable)
        else:
            self.base_dir = os.path.dirname(os.path.abspath(__file__))

        # ---------- 图标 ----------
        self.icons_config: list[dict] = self._load_icons_config()
        self._cairosvg_available: bool = self._check_cairosvg()
        # 保持 PhotoImage 引用防止 GC
        self._icon_images: list[ImageTk.PhotoImage] = []
        # 当前选中的图标 id（None = 十字线）
        self._current_icon_id: str | None = None
        # 当前标题、描述和来源（点击标记时使用）
        self._current_title: str = ""
        self._current_description: str = ""
        self._current_source: str = ""

        # 选中状态
        self._selected_mark: dict | None = None
        self._suppress_sync: bool = False
        self._redo_stack: list[dict] = []  # 被撤销的标记，供 Ctrl+Y 重做
        self._save_path: str | None = None  # Ctrl+S 保存路径

        # ---------- 界面 ----------
        self._build_toolbar()
        self._build_left_panel()
        self._build_canvas()
        self._build_statusbar()
        self._bind_events()

        # 允许从命令行参数打开图片
        if len(sys.argv) > 1:
            path = sys.argv[1]
            if os.path.isfile(path):
                self.root.after(200, lambda: self.load_image(path))

    # ==================== 图标管理 ====================

    def _load_icons_config(self) -> list[dict]:
        """从 icons/poi-icons.json 加载图标配置"""
        json_path = os.path.join(self.base_dir, "icons", "poi-icons.json")
        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                icons = data.get("icons", [])
                icons.sort(key=lambda i: i.get("order", 999))
                return icons
            except Exception:
                pass
        return []

    def _check_cairosvg(self) -> bool:
        """检查 cairosvg + Cairo 是否可用"""
        try:
            import cairosvg  # noqa: F401
            # 验证 Cairo 库也能加载（cairocffi 可能在 import 阶段不报错，但实际调用时才报）
            cairosvg.svg2png(bytestring=b'<svg xmlns="http://www.w3.org/2000/svg"/>',
                             output_width=1, output_height=1)
            return True
        except Exception:
            return False

    def _icon_info(self, icon_id: str) -> dict | None:
        """根据 icon_id 查找图标配置"""
        for icon in self.icons_config:
            if icon["id"] == icon_id:
                return icon
        return None

    def _render_icon_to_tk(self, icon_id: str, size: int) -> ImageTk.PhotoImage | None:
        """
        将 SVG 图标渲染为指定尺寸的 PhotoImage。
        返回 None 表示渲染失败（文件不存在 / cairosvg 不可用）。
        """
        svg_path = os.path.join(self.base_dir, "icons", f"{icon_id}.svg")
        if not os.path.exists(svg_path):
            return None

        size = max(ICON_MIN_SIZE, min(ICON_MAX_SIZE, size))

        try:
            import cairosvg
            png_data = cairosvg.svg2png(url=svg_path, output_width=size, output_height=size)
            img = Image.open(io.BytesIO(png_data))
            tk_img = ImageTk.PhotoImage(img)
            return tk_img
        except Exception:
            return None

    def _draw_fallback_shape(self, cx: float, cy: float, icon_id: str,
                             color: str, size: int) -> list:
        """
        当 cairosvg 不可用时，用 Canvas 形状作为回退标记。
        返回 canvas 元素 id 列表。
        """
        r = size / 2
        ids = []

        if "extract" in icon_id:
            # 菱形
            ids.append(self.canvas.create_polygon(
                cx, cy - r, cx + r, cy, cx, cy + r, cx - r, cy,
                fill=color, outline="#ffffff", width=1, tags="mark",
            ))
        elif "vault" in icon_id:
            # 圆角方形
            ids.append(self.canvas.create_rectangle(
                cx - r, cy - r, cx + r, cy + r,
                fill=color, outline="#ffffff", width=1, tags="mark",
            ))
        elif "runner" in icon_id:
            # 三角形
            ids.append(self.canvas.create_polygon(
                cx, cy - r, cx + r, cy + r, cx - r, cy + r,
                fill=color, outline="#ffffff", width=1, tags="mark",
            ))
        else:
            # 圆形
            ids.append(self.canvas.create_oval(
                cx - r, cy - r, cx + r, cy + r,
                fill=color, outline="#ffffff", width=1, tags="mark",
            ))

        return ids

    def _sync_current_selection(self, event=None):
        """同步图标 / 标题 / 描述 / 来源"""
        if self._suppress_sync:
            return

        # 图标
        selection = self.icon_var.get()
        if selection == "无 (十字线)":
            self._current_icon_id = None
            self.icon_color_label.config(bg="#ff4444")
        else:
            for icon in self.icons_config:
                if icon["label"] == selection:
                    self._current_icon_id = icon["id"]
                    self.icon_color_label.config(bg=icon.get("accentColor", "#ffffff"))
                    break

        if self._selected_mark is not None:
            # 将输入框的值同步到选中标记（包括图标）
            self._selected_mark["icon"] = self._current_icon_id
            self._selected_mark["title"] = self.title_var.get().strip()
            self._selected_mark["description"] = self.desc_text.get("1.0", "end-1c").strip()
            self._selected_mark["source"] = self.source_var.get().strip()
            self._redraw_single_mark(self._selected_mark)
            # 短暂显示"已保存"反馈
            self.status_label.config(
                text=f"✓ 已更新 ({self._selected_mark['x']}, {self._selected_mark['y']})"
            )
        else:
            # 同步到待用变量
            self._current_title = self.title_var.get().strip()
            self._current_description = self.desc_text.get("1.0", "end-1c").strip()
            self._current_source = self.source_var.get().strip()

    def _on_icon_selected(self, event=None):
        """图标选择器变更回调 — 切换图标时自动填入图标名作为 title"""
        self._sync_current_selection()
        if self._current_icon_id:
            info = self._icon_info(self._current_icon_id)
            if info:
                # 如果 title 为空或等于已知图标 label（说明是自动填充的），则更新
                known_labels = {i["label"] for i in self.icons_config}
                current_title = self._current_title
                if not current_title or current_title in known_labels:
                    self._suppress_sync = True
                    self.title_var.set(info["label"])
                    self._current_title = info["label"]
                    self._suppress_sync = False
                    if self._selected_mark is not None:
                        self._selected_mark["title"] = info["label"]
                        self._redraw_single_mark(self._selected_mark)

    def _on_title_changed(self, event=None):
        """标题变更回调"""
        self._sync_current_selection()

    def _on_desc_changed(self, event=None):
        """描述变更回调"""
        self._sync_current_selection()

    def _on_source_changed(self, event=None):
        """来源变更回调"""
        self._sync_current_selection()

    # ==================== UI 构建 ====================

    def _build_toolbar(self):
        bar = ttk.Frame(self.root)
        bar.pack(fill=tk.X, padx=6, pady=(6, 2))

        ttk.Button(bar, text="打开图片", command=self.open_file).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(bar, text="适应窗口", command=self.fit_to_window).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(bar, text="原始大小", command=self.original_size).pack(side=tk.LEFT, padx=(0, 6))

        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)

        ttk.Label(bar, text="缩放:").pack(side=tk.LEFT)
        self.scale_var = tk.StringVar(value="100%")
        ttk.Label(bar, textvariable=self.scale_var, width=7).pack(side=tk.LEFT)

        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)
        ttk.Button(bar, text="撤销 (Ctrl+Z)", command=self.undo_last_mark).pack(side=tk.LEFT, padx=(6, 6))
        ttk.Button(bar, text="清除全部", command=self.clear_marks).pack(side=tk.LEFT)

        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)
        ttk.Button(bar, text="导出点位", command=self.export_marks).pack(side=tk.LEFT, padx=(6, 6))
        ttk.Button(bar, text="导入点位", command=self.import_marks).pack(side=tk.LEFT)

    def _build_left_panel(self):
        """左侧 meta 编辑面板"""
        panel = ttk.Frame(self.root, width=220)
        panel.pack(side=tk.LEFT, fill=tk.Y, padx=(6, 0), pady=(2, 2))
        panel.pack_propagate(False)

        # ---- 图标选择 ----
        ttk.Label(panel, text="图标").pack(anchor=tk.W)
        icon_frame = ttk.Frame(panel)
        icon_frame.pack(fill=tk.X, pady=(0, 10))
        icon_labels = ["无 (十字线)"] + [icon["label"] for icon in self.icons_config]
        self.icon_var = tk.StringVar(value="无 (十字线)")
        self.icon_combo = ttk.Combobox(
            icon_frame, textvariable=self.icon_var, state="readonly",
            values=icon_labels,
        )
        self.icon_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.icon_combo.bind("<<ComboboxSelected>>", self._on_icon_selected)
        self.icon_color_label = tk.Label(
            icon_frame, text="  ", width=3, bg="#ff4444",
            relief=tk.SUNKEN, borderwidth=1,
        )
        self.icon_color_label.pack(side=tk.LEFT, padx=(4, 0))

        # ---- 标题 ----
        ttk.Label(panel, text="标题").pack(anchor=tk.W)
        self.title_var = tk.StringVar(value="")
        self.title_entry = ttk.Entry(panel, textvariable=self.title_var)
        self.title_entry.pack(fill=tk.X, pady=(0, 10))
        self.title_entry.bind("<KeyRelease>", self._on_title_changed)
        self.title_entry.bind("<FocusOut>", self._on_title_changed)

        # ---- 描述（多行） ----
        ttk.Label(panel, text="描述").pack(anchor=tk.W)
        self.desc_text = tk.Text(panel, height=4, width=20, wrap=tk.WORD)
        self.desc_text.pack(fill=tk.X, pady=(0, 10))
        self.desc_text.bind("<KeyRelease>", self._on_desc_changed)
        self.desc_text.bind("<FocusOut>", self._on_desc_changed)

        # ---- 来源 ----
        ttk.Label(panel, text="来源").pack(anchor=tk.W)
        self.source_var = tk.StringVar(value="")
        self.source_entry = ttk.Entry(panel, textvariable=self.source_var)
        self.source_entry.pack(fill=tk.X, pady=(0, 10))
        self.source_entry.bind("<KeyRelease>", self._on_source_changed)
        self.source_entry.bind("<FocusOut>", self._on_source_changed)

        # ---- 坐标 ----
        self.coord_display = ttk.Label(panel, text="坐标: —")
        self.coord_display.pack(anchor=tk.W)

        # ---- 保存按钮 ----
        ttk.Button(panel, text="保存 (Ctrl+S)", command=self.save_marks).pack(
            anchor=tk.W, pady=(12, 0), ipadx=8,
        )

    def _build_canvas(self):
        frame = ttk.Frame(self.root)
        frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=2)

        self.canvas = tk.Canvas(frame, bg="#2b2b2b", cursor="crosshair", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

    def _build_statusbar(self):
        bar = ttk.Frame(self.root)
        bar.pack(fill=tk.X, padx=6, pady=(2, 6))

        self.status_label = ttk.Label(bar, text="点击「打开图片」选择图片，或将图片拖到 exe 上启动")
        self.status_label.pack(side=tk.LEFT)

        self.coord_label = ttk.Label(bar, text="")
        self.coord_label.pack(side=tk.RIGHT)

    # ==================== 事件绑定 ====================

    def _bind_events(self):
        self.canvas.bind("<Configure>", self._on_resize)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Button-2>", self._on_middle_click)   # 中键删除标记
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<MouseWheel>", self._on_wheel)

        # Ctrl+右键拖拽平移 / 右键单击选中
        self.canvas.bind("<ButtonPress-3>", self._on_drag_start)
        self.canvas.bind("<Control-B3-Motion>", self._on_drag_move)
        self.canvas.bind("<ButtonRelease-3>", self._on_right_release)

        # Ctrl+Z 撤销 / Ctrl+Y 重做
        self.root.bind("<Control-z>", lambda e: self.undo_last_mark())
        self.root.bind("<Control-Z>", lambda e: self.undo_last_mark())
        self.root.bind("<Control-y>", lambda e: self.redo_last_mark())
        self.root.bind("<Control-Y>", lambda e: self.redo_last_mark())
        self.root.bind("<Control-s>", lambda e: self.save_marks())
        self.root.bind("<Control-S>", lambda e: self.save_marks())

    # ==================== 图片加载 ====================

    def open_file(self):
        path = filedialog.askopenfilename(
            title="选择图片",
            filetypes=[
                ("图片文件", "*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tiff *.ico"),
                ("所有文件", "*.*"),
            ],
        )
        if path:
            self.load_image(path)

    def load_image(self, path: str):
        try:
            img = Image.open(path)
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGBA")
            else:
                img = img.convert("RGB")
            self.pil_image = img
            self._loaded_filename = path
            self.scale = 1.0
            self.offset_x = 0
            self.offset_y = 0
            self.clear_marks()
            self._cancel_redraw()
            self.fit_to_window()
            self.status_label.config(
                text=f"已加载: {os.path.basename(path)}  |  {img.width} × {img.height} px"
            )
        except Exception as e:
            messagebox.showerror("打开失败", f"无法读取图片:\n{e}")

    # ==================== 视图控制 ====================

    def fit_to_window(self):
        if self.pil_image is None:
            return
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if cw < 10 or ch < 10:
            return
        iw, ih = self.pil_image.size
        self.scale = min(cw / iw, ch / ih, 1.0)
        self.offset_x = (cw - iw * self.scale) / 2
        self.offset_y = (ch - ih * self.scale) / 2
        self._schedule_redraw()

    def original_size(self):
        if self.pil_image is None:
            return
        self.scale = 1.0
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        iw, ih = self.pil_image.size
        self.offset_x = (cw - iw * self.scale) / 2
        self.offset_y = (ch - ih * self.scale) / 2
        self._schedule_redraw()

    def _on_resize(self, event=None):
        self._schedule_redraw()

    def _on_wheel(self, event):
        if self.pil_image is None:
            return
        factor = 1.12 if event.delta > 0 else 0.892857  # 0.892857 ≈ 1/1.12
        new_scale = self.scale * factor
        if new_scale < 0.05 or new_scale > 50:
            return

        mx = self.canvas.canvasx(event.x)
        my = self.canvas.canvasy(event.y)

        img_x = (mx - self.offset_x) / self.scale
        img_y = (my - self.offset_y) / self.scale

        self.scale = new_scale
        self.offset_x = mx - img_x * self.scale
        self.offset_y = my - img_y * self.scale

        # 防抖重绘: 取消上次定时器，延迟 ~16ms 后再重绘
        self._schedule_redraw(defer=True)

    def _on_drag_start(self, event):
        self._drag_x = event.x
        self._drag_y = event.y
        self._drag_start_x = event.x
        self._drag_start_y = event.y
        self._drag_active = False

    def _on_drag_move(self, event):
        self._drag_active = True
        dx = event.x - self._drag_x
        dy = event.y - self._drag_y
        self.offset_x += dx
        self.offset_y += dy
        self._drag_x = event.x
        self._drag_y = event.y
        self._schedule_redraw(defer=True)

    def _on_right_release(self, event):
        """右键释放：如果未拖拽 → 选中/取消选中标记"""
        if self._drag_active:
            return  # 拖拽平移，不触发选中
        dx = event.x - self._drag_start_x
        dy = event.y - self._drag_start_y
        if math.hypot(dx, dy) > 5:
            return  # 轻微移动，不触发选中

        coords = self._canvas_to_image(event.x, event.y)
        if coords is None:
            self._deselect_mark()
            return

        near = self._find_nearby_mark(coords[0], coords[1])
        if near is not None:
            self._select_mark(near)
        else:
            self._deselect_mark()

    # ==================== 选中 & 编辑 ====================

    def _select_mark(self, mark: dict):
        """选中标记，回填 meta 到工具栏并高亮"""
        if self._selected_mark is mark:
            return  # 已是选中状态

        # 先取消旧选中（重置 _selected_mark 避免旧标记重绘加高亮）
        old = self._selected_mark
        self._selected_mark = None
        if old is not None:
            self._redraw_single_mark(old)

        self._selected_mark = mark

        # 回填输入框（抑制同步回调）
        self._suppress_sync = True
        icon_id = mark.get("icon")
        if icon_id:
            info = self._icon_info(icon_id)
            self.icon_var.set(info["label"] if info else "无 (十字线)")
            self.icon_color_label.config(bg=info.get("accentColor", "#ff4444") if info else "#ff4444")
            self._current_icon_id = icon_id
        else:
            self.icon_var.set("无 (十字线)")
            self.icon_color_label.config(bg="#ff4444")
            self._current_icon_id = None
        self.title_var.set(mark.get("title", ""))
        self.desc_text.delete("1.0", tk.END)
        self.desc_text.insert("1.0", mark.get("description", ""))
        self.source_var.set(mark.get("source", ""))
        self.coord_display.config(text=f"坐标: ({mark['x']}, {mark['y']})")
        self._suppress_sync = False

        # 高亮重绘
        self._redraw_single_mark(mark)

        info = self._icon_info(mark.get("icon", "")) if mark.get("icon") else None
        icon_label = f" [{info['label']}]" if info else ""
        desc = mark.get("title") or mark.get("description") or ""
        label = f"「{desc}」" if desc else ""
        self.status_label.config(
            text=f"已选中{label}{icon_label} ({mark['x']}, {mark['y']})  |  修改标题/描述/来源自动更新"
        )

    def _deselect_mark(self):
        """取消选中"""
        if self._selected_mark is None:
            return
        mark = self._selected_mark
        self._selected_mark = None
        self._redraw_single_mark(mark)
        self.coord_display.config(text="坐标: —")
        if self.pil_image:
            iw, ih = self.pil_image.size
            self.status_label.config(text=f"{iw} × {ih} px")
        else:
            self.status_label.config(text="点击「打开图片」选择图片，或将图片拖到 exe 上启动")

    def _redraw_single_mark(self, mark: dict):
        """重绘单个标记的 canvas 元素"""
        for mid in mark["ids"]:
            self.canvas.delete(mid)
        cx, cy = self._image_to_canvas(mark["x"], mark["y"])
        mark["ids"] = self._create_mark_elements(cx, cy, mark)

    # ==================== 防抖重绘 ====================

    def _cancel_redraw(self):
        if self._redraw_after_id is not None:
            self.root.after_cancel(self._redraw_after_id)
            self._redraw_after_id = None

    def _schedule_redraw(self, defer: bool = False):
        """安排重绘。defer=True 时用 16ms 防抖；否则立即执行。"""
        if defer:
            self._cancel_redraw()
            self._redraw_after_id = self.root.after(16, self._do_redraw)
        else:
            self._cancel_redraw()
            self._do_redraw()

    def _do_redraw(self):
        self._redraw_after_id = None
        if self.pil_image is None:
            return
        iw, ih = self.pil_image.size
        new_w = max(1, int(iw * self.scale))
        new_h = max(1, int(ih * self.scale))

        # 缩放图片 — 用 BILINEAR 比 LANCZOS 更快，缩放交互更流畅
        resample = Image.BILINEAR if new_w < iw else Image.LANCZOS
        resized = self.pil_image.resize((new_w, new_h), resample)
        self.tk_image = ImageTk.PhotoImage(resized)

        if self.canvas_img_id is not None:
            self.canvas.coords(self.canvas_img_id, self.offset_x, self.offset_y)
            self.canvas.itemconfig(self.canvas_img_id, image=self.tk_image)
        else:
            self.canvas_img_id = self.canvas.create_image(
                self.offset_x, self.offset_y, anchor=tk.NW, image=self.tk_image,
            )

        pct = int(self.scale * 100)
        self.scale_var.set(f"{pct}%")

        # 重绘所有标记
        self._redraw_all_marks()

    # ==================== 坐标换算 ====================

    def _canvas_to_image(self, cx, cy) -> tuple[int, int] | None:
        if self.pil_image is None:
            return None
        iw, ih = self.pil_image.size
        ix = (cx - self.offset_x) / self.scale
        iy = (cy - self.offset_y) / self.scale
        if 0 <= ix < iw and 0 <= iy < ih:
            return int(round(ix)), int(round(iy))
        return None

    def _image_to_canvas(self, ix: float, iy: float) -> tuple[float, float]:
        return self.offset_x + ix * self.scale, self.offset_y + iy * self.scale

    # ==================== 鼠标交互 ====================

    def _on_motion(self, event):
        coords = self._canvas_to_image(event.x, event.y)
        if coords:
            self.coord_label.config(text=f"({coords[0]}, {coords[1]})")
        else:
            self.coord_label.config(text="")

    def _on_click(self, event):
        """左键: 标记坐标 / 放置图标。如果点击在已有标记附近则不做标记。"""
        coords = self._canvas_to_image(event.x, event.y)
        if coords is None:
            return

        # 检查是否在已有标记附近
        near = self._find_nearby_mark(coords[0], coords[1])
        if near is not None:
            icon_label = ""
            if near.get("icon"):
                info = self._icon_info(near["icon"])
                if info:
                    icon_label = f" [{info['label']}]"
            self.status_label.config(
                text=f"该位置已有标记{icon_label} ({near['x']}, {near['y']})，右键可选中编辑，中键可删除"
            )
            return

        self._add_mark(coords[0], coords[1])

    def _on_middle_click(self, event):
        """中键: 删除点击位置附近的标记。"""
        coords = self._canvas_to_image(event.x, event.y)
        if coords is None:
            return

        near = self._find_nearby_mark(coords[0], coords[1])
        if near is not None:
            self._remove_mark(near)
        else:
            self.status_label.config(text="此处没有标记可删除")

    # ==================== 标记操作 ====================

    def _find_nearby_mark(self, x: int, y: int) -> dict | None:
        """返回距离 (x, y) 最近的标记，超出命中半径则返回 None"""
        best = None
        best_dist = float("inf")
        for m in self.marks:
            d = math.hypot(m["x"] - x, m["y"] - y)
            # 图标标记使用更大的命中半径
            radius = MARK_HIT_RADIUS * 1.5 if m.get("icon") else MARK_HIT_RADIUS
            if d < radius and d < best_dist:
                best = m
                best_dist = d
        return best

    def _add_mark(self, x: int, y: int):
        """在原始图片坐标 (x, y) 处创建标记"""
        iw, ih = self.pil_image.size
        cx, cy = self._image_to_canvas(x, y)
        icon_id = self._current_icon_id
        title = self._current_title
        description = self._current_description
        source = self._current_source

        # 如果没填 title 且选了图标，自动使用图标名
        if not title and icon_id:
            info = self._icon_info(icon_id)
            if info:
                title = info["label"]

        mark = {
            "x": x, "y": y,
            "icon": icon_id,
            "title": title, "description": description, "source": source,
        }
        mark["ids"] = self._create_mark_elements(cx, cy, mark)
        # 分配持久 id，清空 redo 栈（新操作使 redo 无效）
        self._redo_stack.clear()
        self._mark_counter += 1
        icon_key = icon_id or "crosshair"
        mark["id"] = f"{icon_key}-{self._mark_counter:03d}"

        self.marks.append(mark)

        # 复制到剪贴板
        if icon_id:
            info = self._icon_info(icon_id)
            label = info["label"] if info else icon_id
            desc_part = f"{description} · " if description else ""
            pyperclip.copy(f"{desc_part}{label}: ({x}, {y})")
            status_extra = f"「{description}」" if description else ""
            self.status_label.config(
                text=f"已放置{status_extra} {label} ({x}, {y}) — 已复制到剪贴板  |  {iw} × {ih} px  "
                f"[{len(self.marks)} 个标记]"
            )
        else:
            pyperclip.copy(f"({x}, {y})")
            self.status_label.config(
                text=f"已标记 ({x}, {y}) — 已复制到剪贴板  |  {iw} × {ih} px  "
                f"[{len(self.marks)} 个标记]"
            )

    def _delete_mark_canvas(self, mark: dict):
        """仅从 canvas 移除标记元素，不动 marks 列表"""
        if mark is self._selected_mark:
            self._selected_mark = None
        for mid in mark["ids"]:
            self.canvas.delete(mid)

    def _remove_mark(self, mark: dict):
        """删除一个标记（从 canvas 和列表移除，并清空 redo 栈）"""
        self._redo_stack.clear()
        self._delete_mark_canvas(mark)
        self.marks.remove(mark)

        iw, ih = self.pil_image.size
        cnt = len(self.marks)
        icon_label = ""
        if mark.get("icon"):
            info = self._icon_info(mark["icon"])
            if info:
                icon_label = f" [{info['label']}]"
        desc = mark.get("title") or mark.get("description", "")
        if desc:
            icon_label = f"「{desc}」{icon_label}"
        self.status_label.config(
            text=f"已删除标记{icon_label} ({mark['x']}, {mark['y']})  |  {iw} × {ih} px  "
            f"[{cnt} 个标记]"
        )

    def undo_last_mark(self):
        """撤销最后一个标记 (Ctrl+Z)"""
        if not self.marks:
            if self.pil_image:
                iw, ih = self.pil_image.size
                self.status_label.config(text=f"没有标记可撤销  |  {iw} × {ih} px")
            return
        mark = self.marks.pop()
        self._delete_mark_canvas(mark)
        self._redo_stack.append(mark)

        iw, ih = self.pil_image.size
        cnt = len(self.marks)
        self.status_label.config(
            text=f"已撤销 ({mark['x']}, {mark['y']})  |  {iw} × {ih} px  [{cnt} 个标记]"
        )

    def redo_last_mark(self):
        """重做最后一个被撤销的标记 (Ctrl+Y)"""
        if not self._redo_stack:
            if self.pil_image:
                iw, ih = self.pil_image.size
                self.status_label.config(text=f"没有操作可重做  |  {iw} × {ih} px")
            return
        mark = self._redo_stack.pop()
        self.marks.append(mark)
        cx, cy = self._image_to_canvas(mark["x"], mark["y"])
        mark["ids"] = self._create_mark_elements(cx, cy, mark)

        iw, ih = self.pil_image.size
        cnt = len(self.marks)
        self.status_label.config(
            text=f"已重做 ({mark['x']}, {mark['y']})  |  {iw} × {ih} px  [{cnt} 个标记]"
        )

    def _create_mark_elements(self, cx: float, cy: float, mark: dict) -> list:
        """
        根据当前 scale / offset 创建标记的 canvas 元素。
        返回 canvas 元素 id 列表。
        """
        ids = []
        icon_id = mark.get("icon")
        x, y = mark["x"], mark["y"]
        title = mark.get("title", "")
        description = mark.get("description", "")
        source = mark.get("source", "")

        if icon_id is None:
            # ---------- 默认十字线标记 ----------
            r = 12
            ids.append(self.canvas.create_line(
                cx, cy - r, cx, cy + r,
                fill="#ff4444", width=2, tags="mark",
            ))
            ids.append(self.canvas.create_line(
                cx - r, cy, cx + r, cy,
                fill="#ff4444", width=2, tags="mark",
            ))
            display_name = title or description
            coord_text = f"({x}, {y})"
            if display_name:
                coord_text = f"{display_name} {coord_text}"
            ids.append(self.canvas.create_text(
                cx + 16, cy - 16,
                text=coord_text,
                fill="#ff4444", anchor=tk.SW,
                font=("Consolas", 10, "bold"),
                tags="mark",
            ))
        else:
            # ---------- 图标标记 ----------
            info = self._icon_info(icon_id)
            color = info.get("accentColor", "#ffffff") if info else "#ffffff"
            label = info["label"] if info else icon_id
            display_name = title or description or label
            tag_text = f"{display_name} ({x}, {y})"

            icon_size = int(ICON_BASE_SIZE * self.scale)

            if self._cairosvg_available:
                tk_img = self._render_icon_to_tk(icon_id, icon_size)
                if tk_img is not None:
                    self._icon_images.append(tk_img)
                    ids.append(self.canvas.create_image(
                        cx, cy, image=tk_img, anchor=tk.CENTER, tags="mark",
                    ))
                else:
                    ids.extend(self._draw_fallback_shape(cx, cy, icon_id, color, icon_size))
            else:
                ids.extend(self._draw_fallback_shape(cx, cy, icon_id, color, icon_size))

            # 标签（图标下方）
            ids.append(self.canvas.create_text(
                cx, cy + icon_size / 2 + 10,
                text=tag_text,
                fill=color, anchor=tk.N, font=("Microsoft YaHei", 9, "bold"),
                tags="mark",
            ))
            # 来源标签（标签下方，小字灰色）
            if source:
                ids.append(self.canvas.create_text(
                    cx, cy + icon_size / 2 + 26,
                    text=f"[{source}]",
                    fill="#aaaaaa", anchor=tk.N,
                    font=("Microsoft YaHei", 7),
                    tags="mark",
                ))

        # 选中高亮
        if mark is self._selected_mark:
            r = (icon_size / 2 + 6) if icon_id else 20
            ids.append(self.canvas.create_oval(
                cx - r, cy - r, cx + r, cy + r,
                outline="#ffcc00", width=2, dash=(4, 3),
                tags="mark",
            ))

        return ids

    def _redraw_all_marks(self):
        """缩放/平移后重新绘制所有标记（根据原始坐标 + 当前 scale/offset）"""
        # 先删除所有旧的 canvas 元素
        for m in self.marks:
            for mid in m["ids"]:
                self.canvas.delete(mid)

        # 清空 PhotoImage 引用（旧图像会在 GC 后释放）
        self._icon_images.clear()

        # 重新创建
        for m in self.marks:
            cx, cy = self._image_to_canvas(m["x"], m["y"])
            m["ids"] = self._create_mark_elements(cx, cy, m)

    def clear_marks(self):
        self._selected_mark = None
        self._redo_stack.clear()
        for m in self.marks:
            for mid in m["ids"]:
                self.canvas.delete(mid)
        self.marks.clear()
        self._icon_images.clear()
        if self.pil_image:
            iw, ih = self.pil_image.size
            self.status_label.config(text=f"{iw} × {ih} px")
        else:
            self.status_label.config(text="点击「打开图片」选择图片，或将图片拖到 exe 上启动")

    # ==================== 导入 / 导出 ====================

    def _build_pois_data(self) -> list[dict]:
        """构建导出用的 pois 列表"""
        pois = []
        for m in self.marks:
            icon_key = m.get("icon") or "crosshair"
            pid = m.get("id", f"{icon_key}-???")
            info = self._icon_info(icon_key) if m.get("icon") else None
            fallback_title = m.get("description", "") or (info["label"] if info else icon_key)
            title = m.get("title", "") or fallback_title
            pois.append({
                "id": pid,
                "title": title,
                "iconKey": icon_key,
                "iconSize": 120,
                "x": m["x"],
                "y": m["y"],
                "description": m.get("description", ""),
                "source": m.get("source", ""),
            })
        return pois

    def _export_to_path(self, path: str, pois: list[dict]):
        """将 pois 写入到指定路径"""
        data = {"pois": pois}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _default_export_name(self) -> str:
        """生成默认导出文件名"""
        if self.pil_image:
            img_name = getattr(self, "_loaded_filename", None)
            if img_name:
                base = os.path.splitext(os.path.basename(img_name))[0]
                return f"{base}-pois.json"
        return "pois.json"

    def save_marks(self, event=None):
        """Ctrl+S 保存：已导出过则覆盖，否则弹出对话框"""
        if not self.marks:
            return
        pois = self._build_pois_data()
        if self._save_path is not None:
            try:
                self._export_to_path(self._save_path, pois)
                self.status_label.config(
                    text=f"已保存 {len(pois)} 个点位 → {os.path.basename(self._save_path)}"
                )
            except Exception as e:
                messagebox.showerror("保存失败", f"写入文件失败:\n{e}")
        else:
            self.export_marks()

    def export_marks(self):
        """导出当前所有标记为 JSON 文件（弹出保存对话框）"""
        if not self.marks:
            messagebox.showinfo("导出点位", "当前没有标记可导出。")
            return

        pois = self._build_pois_data()
        path = filedialog.asksaveasfilename(
            title="导出点位",
            defaultextension=".json",
            initialfile=self._default_export_name(),
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
        )
        if not path:
            return

        try:
            self._export_to_path(path, pois)
            self._save_path = path  # 记住路径，后续 Ctrl+S 覆盖
            self.status_label.config(
                text=f"已导出 {len(pois)} 个点位 → {os.path.basename(path)}"
            )
        except Exception as e:
            messagebox.showerror("导出失败", f"写入文件失败:\n{e}")

    def import_marks(self):
        """从 JSON 文件导入点位（替换当前所有标记）"""
        path = filedialog.askopenfilename(
            title="导入点位",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
        )
        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            messagebox.showerror("导入失败", f"读取文件失败:\n{e}")
            return

        pois = data.get("pois", [])
        if not pois:
            messagebox.showinfo("导入点位", "文件中没有点位数据。")
            return

        # 验证 iconKey 是否在已知图标中
        valid_icon_ids = {icon["id"] for icon in self.icons_config}
        imported = 0
        skipped = 0

        self.clear_marks()
        self._selected_mark = None

        for poi in pois:
            x = poi.get("x")
            y = poi.get("y")
            if x is None or y is None:
                skipped += 1
                continue

            icon_key = poi.get("iconKey", "")
            # 只认已知图标，否则当十字线
            icon_id = icon_key if icon_key in valid_icon_ids else None

            mark = {
                "x": int(x),
                "y": int(y),
                "icon": icon_id,
                "title": poi.get("title", ""),
                "description": poi.get("description", ""),
                "source": poi.get("source", ""),
            }
            cx, cy = self._image_to_canvas(mark["x"], mark["y"])
            mark["ids"] = self._create_mark_elements(cx, cy, mark)
            # 优先使用 poIP 中原有的 id，没有则分配
            if poi.get("id"):
                mark["id"] = poi["id"]
            else:
                self._mark_counter += 1
                mark["id"] = f"{icon_key}-{self._mark_counter:03d}"

            self.marks.append(mark)
            imported += 1

        if self.pil_image:
            iw, ih = self.pil_image.size
        else:
            iw = ih = 0
        self.status_label.config(
            text=f"已导入 {imported} 个点位"
            + (f"，跳过 {skipped} 个" if skipped else "")
            + (f"  |  {iw} × {ih} px" if self.pil_image else "")
        )


def main():
    root = tk.Tk()

    try:
        ico = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
        if os.path.exists(ico):
            root.iconbitmap(ico)
    except Exception:
        pass

    app = ImageAxisApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
