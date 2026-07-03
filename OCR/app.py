from __future__ import annotations

import re
import threading
from io import BytesIO
from pathlib import Path
from typing import Optional
import subprocess
import tempfile

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageGrab, ImageTk, UnidentifiedImageError
from winrt.windows.globalization import Language
from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapDecoder, BitmapPixelFormat
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream


SUPPORTED_EXTENSIONS = {
    ".bmp",
    ".dib",
    ".gif",
    ".jfif",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}

BG_COLOR = "#111827"
PANEL_COLOR = "#1f2937"
SURFACE_COLOR = "#0f172a"
TEXT_COLOR = "#e5e7eb"
MUTED_TEXT_COLOR = "#9ca3af"
ACCENT_COLOR = "#2563eb"
ACCENT_ACTIVE_COLOR = "#1d4ed8"
BORDER_COLOR = "#334155"
CJK_CHAR_CLASS = r"\u3400-\u4dbf\u4e00-\u9fff"
OCR_PUNCT_CLASS = r"""[\u3000-\u303f\uff00-\uffef\[\]（）()【】《》〈〉「」『』“”‘’"'`~!@#$%^&*_\-+=|\\:;,.?/]"""


class OCRApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("中文 OCR 文字识别工具")
        self.root.geometry("1100x700")
        self.root.minsize(900, 600)
        self.root.configure(background=BG_COLOR)

        self.tesseract_path = self._find_tesseract()
        self.engine = self._create_ocr_engine()
        self.current_image: Optional[Image.Image] = None
        self.current_image_path: Optional[Path] = None
        self.preview_photo: Optional[ImageTk.PhotoImage] = None
        self.is_recognizing = False

        language_tag = self.engine.recognizer_language.language_tag
        engine_name = "Tesseract 中文" if self.tesseract_path else "Windows OCR"
        self.status_var = tk.StringVar(
            value=f"就绪：请从剪切板粘贴图片，或打开本地图片文件。当前引擎：{engine_name}，语言：{language_tag}"
        )

        self._build_ui()
        self.root.bind_all("<Control-v>", self._handle_paste_shortcut)
        self.root.bind_all("<Return>", self._handle_recognize_shortcut)
        self.root.bind_all("<KP_Enter>", self._handle_recognize_shortcut)

    def _find_tesseract(self) -> Optional[Path]:
        candidates = [
            Path("C:/Program Files/Tesseract-OCR/tesseract.exe"),
            Path("C:/Program Files (x86)/Tesseract-OCR/tesseract.exe"),
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def _create_ocr_engine(self) -> OcrEngine:
        preferred_tags = ["zh-Hans-CN", "zh-CN", "zh-Hans", "en-US"]
        for tag in preferred_tags:
            language = Language(tag)
            if OcrEngine.is_language_supported(language):
                engine = OcrEngine.try_create_from_language(language)
                if engine is not None:
                    return engine

        engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            raise RuntimeError("当前系统未提供可用的 Windows OCR 语言包。请先安装中文 OCR 语言支持。")
        return engine

    def _build_ui(self) -> None:
        self._configure_styles()
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(self.root, padding=(12, 12, 12, 8), style="App.TFrame")
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.columnconfigure(6, weight=1)

        ttk.Button(toolbar, text="打开图片", command=self.open_image).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(toolbar, text="读取剪切板", command=self.load_from_clipboard).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(toolbar, text="开始识别", command=self.start_ocr).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(toolbar, text="复制结果", command=self.copy_result).grid(row=0, column=3, padx=(0, 8))
        ttk.Button(toolbar, text="清空结果", command=self.clear_result).grid(row=0, column=4, padx=(0, 8))

        self.source_label = ttk.Label(toolbar, text="当前图片：未加载", style="Muted.TLabel")
        self.source_label.grid(row=0, column=6, sticky="e")

        main = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 8))

        preview_frame = ttk.Frame(main, padding=10, style="Panel.TFrame")
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(1, weight=1)
        main.add(preview_frame, weight=1)

        ttk.Label(preview_frame, text="图片预览").grid(row=0, column=0, sticky="w", pady=(0, 8))

        self.preview_label = tk.Label(
            preview_frame,
            anchor="center",
            bg=SURFACE_COLOR,
            fg=MUTED_TEXT_COLOR,
            relief="solid",
            bd=1,
            text="暂无图片",
        )
        self.preview_label.grid(row=1, column=0, sticky="nsew")

        result_frame = ttk.Frame(main, padding=10, style="Panel.TFrame")
        result_frame.columnconfigure(0, weight=1)
        result_frame.rowconfigure(1, weight=1)
        main.add(result_frame, weight=1)

        ttk.Label(result_frame, text="识别结果").grid(row=0, column=0, sticky="w", pady=(0, 8))

        self.result_text = tk.Text(
            result_frame,
            wrap="word",
            font=("Microsoft YaHei UI", 11),
            bg=SURFACE_COLOR,
            fg=TEXT_COLOR,
            insertbackground=TEXT_COLOR,
            selectbackground=ACCENT_COLOR,
            selectforeground="#ffffff",
            relief="flat",
            borderwidth=0,
        )
        self.result_text.grid(row=1, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(result_frame, orient="vertical", command=self.result_text.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        self.result_text.configure(yscrollcommand=scrollbar.set)

        status_bar = ttk.Label(
            self.root,
            textvariable=self.status_var,
            padding=(12, 6),
            relief="sunken",
            anchor="w",
            style="Status.TLabel",
        )
        status_bar.grid(row=2, column=0, sticky="ew")

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        style.configure("App.TFrame", background=BG_COLOR)
        style.configure("Panel.TFrame", background=PANEL_COLOR)
        style.configure(
            "TLabel",
            background=PANEL_COLOR,
            foreground=TEXT_COLOR,
        )
        style.configure(
            "Muted.TLabel",
            background=BG_COLOR,
            foreground=MUTED_TEXT_COLOR,
        )
        style.configure(
            "TButton",
            background=ACCENT_COLOR,
            foreground="#ffffff",
            bordercolor=ACCENT_COLOR,
            focusthickness=0,
            focuscolor=ACCENT_COLOR,
            padding=(12, 8),
        )
        style.map(
            "TButton",
            background=[("active", ACCENT_ACTIVE_COLOR), ("pressed", ACCENT_ACTIVE_COLOR)],
            foreground=[("disabled", MUTED_TEXT_COLOR)],
        )
        style.configure(
            "TPanedwindow",
            background=BG_COLOR,
            sashwidth=8,
        )
        style.configure(
            "TScrollbar",
            background=PANEL_COLOR,
            troughcolor=SURFACE_COLOR,
            arrowcolor=TEXT_COLOR,
            bordercolor=BORDER_COLOR,
        )
        style.configure(
            "Status.TLabel",
            background="#0b1220",
            foreground=MUTED_TEXT_COLOR,
        )

    def open_image(self) -> None:
        filetypes = [
            ("图片文件", "*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp *.gif"),
            ("所有文件", "*.*"),
        ]
        file_path = filedialog.askopenfilename(title="选择图片", filetypes=filetypes)
        if not file_path:
            return

        try:
            with Image.open(file_path) as source_image:
                image = source_image.convert("RGB")
        except (FileNotFoundError, UnidentifiedImageError, OSError) as exc:
            messagebox.showerror("打开失败", f"无法读取图片文件：\n{exc}")
            self.status_var.set("打开图片失败，请确认文件是否有效。")
            return

        self._set_image(image, Path(file_path))

    def load_from_clipboard(self) -> None:
        try:
            clipboard_data = ImageGrab.grabclipboard()
        except OSError as exc:
            messagebox.showerror("读取失败", f"无法访问剪切板：\n{exc}")
            self.status_var.set("读取剪切板失败。")
            return

        image: Optional[Image.Image] = None
        source_path: Optional[Path] = None

        if isinstance(clipboard_data, Image.Image):
            image = clipboard_data.convert("RGB")
        elif isinstance(clipboard_data, list):
            for item in clipboard_data:
                candidate = Path(item)
                if candidate.suffix.lower() in SUPPORTED_EXTENSIONS and candidate.exists():
                    try:
                        with Image.open(candidate) as source_image:
                            image = source_image.convert("RGB")
                        source_path = candidate
                        break
                    except (UnidentifiedImageError, OSError):
                        continue

        if image is None:
            messagebox.showinfo("未发现图片", "剪切板中没有可识别的图片内容。")
            self.status_var.set("剪切板中未发现图片。")
            return

        self._set_image(image, source_path)

    def _set_image(self, image: Image.Image, path: Optional[Path]) -> None:
        self.current_image = image
        self.current_image_path = path
        self._update_preview(image)

        source_text = str(path) if path else "剪切板图片"
        self.source_label.config(text=f"当前图片：{source_text}")
        self.status_var.set("图片已加载，可以开始识别。")

    def _update_preview(self, image: Image.Image) -> None:
        preview = image.copy()
        preview.thumbnail((520, 520))
        self.preview_photo = ImageTk.PhotoImage(preview)
        self.preview_label.configure(image=self.preview_photo, text="")

    def start_ocr(self) -> None:
        if self.is_recognizing:
            self.status_var.set("正在识别中，请稍候。")
            return

        if self.current_image is None:
            messagebox.showwarning("缺少图片", "请先从剪切板读取图片，或打开一个图片文件。")
            self.status_var.set("请先加载图片。")
            return

        self.is_recognizing = True
        self.status_var.set("正在识别文字，请稍候...")

        worker = threading.Thread(target=self._run_ocr, daemon=True)
        worker.start()

    def _run_ocr(self) -> None:
        try:
            text = self._recognize_text(self.current_image)
            self.root.after(0, lambda: self._on_ocr_success(text))
        except Exception as exc:  # noqa: BLE001
            self.root.after(0, lambda: self._on_ocr_error(exc))

    def _recognize_text(self, image: Image.Image) -> str:
        if self.tesseract_path:
            text = self._recognize_with_tesseract(image)
            if text:
                return text

        software_bitmap = self._pil_to_software_bitmap(image)
        result = self.engine.recognize_async(software_bitmap).get()
        text = "\n".join(line.text.strip() for line in result.lines if line.text.strip()).strip()
        return text or "未识别到明显文字。"

    def _recognize_with_tesseract(self, image: Image.Image) -> str:
        if self.tesseract_path is None:
            return ""

        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "ocr_input.png"
            output_base = Path(temp_dir) / "ocr_output"

            image.save(image_path)

            command = [
                str(self.tesseract_path),
                str(image_path),
                str(output_base),
                "-l",
                "chi_sim+eng",
                "--psm",
                "6",
            ]
            startupinfo = None
            creationflags = 0
            if hasattr(subprocess, "STARTUPINFO"):
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                startupinfo=startupinfo,
                creationflags=creationflags,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "Tesseract 执行失败。")

            output_text_path = output_base.with_suffix(".txt")
            if not output_text_path.exists():
                return ""

            text = output_text_path.read_text(encoding="utf-8", errors="ignore").strip()
            return text

    def _pil_to_software_bitmap(self, image: Image.Image) -> object:
        image_bytes = BytesIO()
        image.save(image_bytes, format="PNG")

        stream = InMemoryRandomAccessStream()
        writer = DataWriter(stream)
        try:
            writer.write_bytes(image_bytes.getvalue())
            writer.store_async().get()
            writer.flush_async().get()
            stream.seek(0)

            decoder = BitmapDecoder.create_async(stream).get()
            return decoder.get_software_bitmap_converted_async(
                BitmapPixelFormat.BGRA8,
                BitmapAlphaMode.PREMULTIPLIED,
            ).get()
        finally:
            try:
                writer.close()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass

    def _on_ocr_success(self, text: str) -> None:
        self.is_recognizing = False
        normalized_text = self._normalize_ocr_text(text)
        self.result_text.delete("1.0", tk.END)
        self.result_text.insert("1.0", normalized_text)
        self.status_var.set("识别完成，可以直接复制结果。")

    def _on_ocr_error(self, exc: Exception) -> None:
        self.is_recognizing = False
        messagebox.showerror("识别失败", f"OCR 识别时出现问题：\n{exc}")
        self.status_var.set("识别失败，请检查依赖或更换图片后重试。")

    def copy_result(self) -> None:
        text = self.result_text.get("1.0", tk.END).strip()
        if not text:
            self.status_var.set("暂无可复制内容。")
            return

        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()
        self.status_var.set("识别结果已复制到剪切板。")

    def clear_result(self) -> None:
        self.result_text.delete("1.0", tk.END)
        self.status_var.set("识别结果已清空。")

    def _handle_paste_shortcut(self, _event: tk.Event) -> str:
        self.load_from_clipboard()
        return "break"

    def _handle_recognize_shortcut(self, _event: tk.Event) -> str:
        self.start_ocr()
        return "break"

    def _normalize_ocr_text(self, text: str) -> str:
        normalized_lines = [self._normalize_ocr_line(line) for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
        return "\n".join(normalized_lines).strip()

    def _normalize_ocr_line(self, line: str) -> str:
        line = re.sub(r"[ \t]+", " ", line.strip())
        if not line:
            return ""

        for _ in range(3):
            line = re.sub(fr"(?<=[{CJK_CHAR_CLASS}])\s+(?=[{CJK_CHAR_CLASS}])", "", line)
            line = re.sub(fr"(?<=[{CJK_CHAR_CLASS}])\s+(?={OCR_PUNCT_CLASS})", "", line)
            line = re.sub(fr"(?<={OCR_PUNCT_CLASS})\s+(?=[{CJK_CHAR_CLASS}])", "", line)
            line = re.sub(fr"(?<=[{CJK_CHAR_CLASS}])\s+(?=\d)", "", line)
            line = re.sub(fr"(?<=\d)\s+(?=[{CJK_CHAR_CLASS}])", "", line)
            line = re.sub(r"(?<=/)\s+(?=[A-Za-z0-9])", "", line)
            line = re.sub(r"(?<=[A-Za-z0-9])\s+(?=/)", "", line)

        line = re.sub(r" {2,}", " ", line)
        line = line.replace(":", "：")
        return line


def main() -> None:
    root = tk.Tk()
    style = ttk.Style(root)
    if "vista" in style.theme_names():
        style.theme_use("vista")
    app = OCRApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
