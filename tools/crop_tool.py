#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Công cụ thiết lập ảnh mẫu (Crop Tool) cho Auto Bot Clash of Clans - LDPlayer 9.

Cách dùng:
    python tools/crop_tool.py

Quy trình:
    1. Bấm "Chụp màn hình Game" để lấy ảnh mới nhất từ LDPlayer 9 qua ADB.
    2. Kéo giữ chuột trái trên ảnh để khoanh vùng (khung xanh lá).
    3. Chọn tên Tiếng Việt tương ứng trong danh sách.
    4. Bấm "Xác nhận & Lưu vào Dự án":
       - Nhãn ảnh mẫu (nút bấm...)  -> lưu file PNG vào thư mục assets/.
         Mỗi nhãn có thể có NHIỀU ảnh biến thể: nếu file mặc định (vd button_return_home.png) đã
         tồn tại, công cụ đề xuất lưu thành button_return_home_2.png, _3.png... (bot tự nạp hết).
         (Tường: 4 ảnh dòng đề xuất wall_item_lvA/lvB_gold/elixir.png; các nút Nâng/Xác nhận
          của Tường nay bấm bằng tọa độ thủ công chọn trên giao diện chính)
       - Nhãn ROI đọc OCR (tài nguyên) -> lưu toạ độ [x1, y1, x2, y2] vào
         config.json ở thư mục gốc dự án (khóa cấp cao nhất, cùng chỗ config.py đọc):
             {"screen_size": [960, 540],
              "RESOURCE_MY_VILLAGE_ROI": [x1, y1, x2, y2],
              "RESOURCE_ENEMY_ROI": [x1, y1, x2, y2]}
       (các khóa khác như ngưỡng xả tường do giao diện chính ghi được giữ nguyên;
        khóa "rois" của bản cũ sẽ tự được chuyển lên cấp cao nhất.)
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image, ImageTk

# --------------------------------------------------------------------------- #
# Cấu hình đường dẫn dự án
# --------------------------------------------------------------------------- #
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = PROJECT_ROOT / "assets"
CONFIG_PATH = PROJECT_ROOT / "config.json"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Dùng đúng adb.exe của LDPlayer (khai báo trong config.py) để khỏi xung đột ADB Server
try:
    from config import ADB_PATH as _CFG_ADB_PATH
except Exception:  # noqa: BLE001
    _CFG_ADB_PATH = "adb"
ADB_EXE = _CFG_ADB_PATH if os.path.exists(_CFG_ADB_PATH) else "adb"

BOT_SCREEN_SIZE = (960, 540)   # độ phân giải bot được thiết kế cho

# --------------------------------------------------------------------------- #
# Ánh xạ: Nhãn Tiếng Việt -> Tên file Tiếng Anh
# --------------------------------------------------------------------------- #
LABEL_TO_FILENAME = {
    "1. Nút Chiến đấu (Làng chính - Góc dưới)": "button_battle.png",
    "2. Nút Tìm trận đấu 1700 (Menu chọn chế độ)": "button_find_match_1700.png",
    "3. Nút Tấn công (Xác nhận sau khi tìm trận)": "button_attack.png",
    "4. Nút Tiếp theo 1700 (Soi nhà đối thủ)": "button_next.png",
    "5. Nút Thử lại (Mất kết nối popup)": "button_retry.png",
    "6. Vùng đọc Số Thợ xây (Đỉnh màn hình X/Y)": "builder_region.png",
    "7. Nút Cái Búa Xây dựng (Mở menu công trình)": "button_build.png",
    "8. Vùng ROI Tài nguyên Nhà Ta (Làng chính - Góc trên phải)": "RESOURCE_MY_VILLAGE_ROI",
    "9. Vùng ROI Tài nguyên Địch (Soi trận - Góc trên trái)": "RESOURCE_ENEMY_ROI",
    "10. Nút Về nhà (Màn hình kết thúc trận đấu)": "button_return_home.png",

    # --- DÒNG ĐỀ XUẤT TƯỜNG trong menu Búa (nút nâng/xác nhận nay bấm bằng tọa độ thủ công) ---
    "11. Tường Cấp A - Dòng đề xuất, nút Vàng SÁNG (Đủ Vàng)": "wall_item_lvA_gold.png",
    "12. Tường Cấp A - Dòng đề xuất, nút Dầu SÁNG (Đủ Dầu)": "wall_item_lvA_elixir.png",
    "13. Tường Cấp B - Dòng đề xuất, nút Vàng SÁNG (Đủ Vàng)": "wall_item_lvB_gold.png",
    "14. Tường Cấp B - Dòng đề xuất, nút Dầu SÁNG (Đủ Dầu)": "wall_item_lvB_elixir.png",

    # --- Trong trận đấu ---
    "15. Nút Tăng tốc trận đấu (Góc dưới trận đấu)": "button_boost.png",

    # --- Phục hồi lỗi (luồng lạ nay xử lý bằng tọa độ thoát an toàn trên GUI, KHÔNG dùng nút X) ---
    "16. Nút Tải lại trò chơi (Popup ngắt kết nối)": "button_reload.png",

    # --- Đầu hàng (Surrender Fallback) + ROI quét nhanh ---
    "17. Nút Đầu hàng (Trong trận - Góc dưới trái)": "surrender_btn.png",
    "18. Nút Xác nhận đầu hàng (Popup giữa màn hình)": "confirm_surrender_btn.png",
    "19. Vùng ROI nút Next (Soi nhà - Góc dưới phải, khoanh SÁT nút + chừa lề nhỏ)": "NEXT_BTN_ROI",
    "20. Vùng ROI nút Đầu hàng (Trong trận - Góc dưới trái)": "SURRENDER_BTN_ROI",
    "21. Vùng ROI nút Xác nhận đầu hàng (Popup giữa màn hình)": "CONFIRM_SURRENDER_ROI",
}

# Ảnh mẫu nút bấm đồng thời cập nhật toạ độ bấm (tâm vùng khoanh) vào config.json
CLICK_POS_KEYS = {
    "button_return_home.png": "RETURN_HOME_POS",
    "button_reload.png": "RELOAD_GAME_POS",
}

# Khóa tọa độ nằm trong config.json -> "MANUAL_POSITIONS" (khớp ô nhập trên GUI, tab Tọa độ);
# các khóa còn lại (RETURN_HOME_POS) nằm ở cấp cao nhất của config.json.
MANUAL_POS_KEYS = {"RELOAD_GAME_POS"}


def is_roi(target: str) -> bool:
    """Giá trị không kết thúc bằng .png là khoá ROI (lưu toạ độ, không lưu ảnh)."""
    return not target.lower().endswith(".png")


ROI_KEYS = [v for v in LABEL_TO_FILENAME.values() if is_roi(v)]

# Ảnh KHÔNG dùng để so khớp mẫu (bot không đọc dạng template) -> không có khái niệm biến thể,
# lưu lại vẫn hỏi "Ghi đè?" như trước.
NO_VARIANT_FILES = {"builder_region.png"}


def supports_variants(filename: str) -> bool:
    """Nhãn ảnh mẫu (.png) có hỗ trợ lưu nhiều ảnh biến thể không."""
    return (not is_roi(filename)) and filename not in NO_VARIANT_FILES


def list_template_files(filename: str) -> List[str]:
    """
    Tên các file ảnh mẫu của 1 nhãn trong assets/: ảnh gốc + mọi biến thể (đúng mẫu wildcard
    stem*.png mà bot nạp, vd button_return_home*.png). Đã sắp xếp; thư mục chưa có -> [].
    """
    stem, ext = os.path.splitext(filename)
    try:
        names = os.listdir(ASSETS_DIR)
    except OSError:
        return []
    prefix, suffix = stem.lower(), ext.lower()
    found = [n for n in names
             if len(n) >= len(prefix) + len(suffix)
             and n.lower().startswith(prefix) and n.lower().endswith(suffix)]
    return sorted(found, key=str.lower)


def count_variants(filename: str) -> int:
    """Số ảnh mẫu (gốc + biến thể) đang có cho nhãn này."""
    return len(list_template_files(filename))


def next_variant_filename(filename: str) -> str:
    """Tên biến thể kế tiếp còn trống: button_x.png -> button_x_2.png, button_x_3.png ..."""
    stem, ext = os.path.splitext(filename)
    n = 2
    while (ASSETS_DIR / f"{stem}_{n}{ext}").exists():
        n += 1
    return f"{stem}_{n}{ext}"


def normalize_to_bot_size(img: np.ndarray) -> Tuple[np.ndarray, str]:
    """
    Chuẩn hóa ảnh về đúng độ phân giải chuẩn của bot (BOT_SCREEN_SIZE = 960x540).
    Trả về (ảnh_đã_chuẩn_hóa, ghi_chú); ghi_chú rỗng nếu ảnh vốn đã đúng 960x540.
    """
    h, w = img.shape[:2]
    bw, bh = BOT_SCREEN_SIZE
    if (w, h) == (bw, bh):
        return img, ""
    interp = cv2.INTER_AREA if (w > bw or h > bh) else cv2.INTER_CUBIC
    out = cv2.resize(img, (bw, bh), interpolation=interp)
    sx, sy = bw / w, bh / h
    note = (f"Ảnh gốc {w}x{h} đã được chuẩn hóa về {bw}x{bh} "
            f"(scale x={sx:.3f}, y={sy:.3f}).")
    if abs(sx - sy) / max(sx, sy) > 0.005:
        note += (" ⚠ Tỉ lệ khung khác 16:9 (ảnh có thể chứa viền cửa sổ) nên bị méo nhẹ; "
                 "nên chụp bằng ADB (không lấy viền) để toạ độ chính xác tuyệt đối.")
    return out, note


def get_saved_roi(cfg: dict, key: str):
    """Lấy ROI đã lưu: ưu tiên khóa cấp cao nhất, dự phòng khóa "rois" của bản cũ."""
    value = cfg.get(key)
    if not value:
        legacy = cfg.get("rois")
        value = legacy.get(key) if isinstance(legacy, dict) else None
    return value or None


def load_config() -> dict:
    """Đọc config.json; chưa có file thì trả về {}. File hỏng -> ném ValueError
    (không ghi đè để tránh mất dữ liệu cấu hình cũ)."""
    if not CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"config.json bị lỗi cú pháp: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("config.json phải là một đối tượng JSON ({...}).")
    return data


def save_config(data: dict) -> None:
    """Ghi config.json an toàn (ghi file tạm rồi thay thế)."""
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_PATH)


# Địa chỉ ADB thường gặp của LDPlayer 9
LDPLAYER_ADB_TARGETS = ["127.0.0.1:5555", "emulator-5554", "127.0.0.1:5554"]

# Bảng màu giao diện (dark theme)
BG = "#1e1f26"
PANEL = "#272935"
FG = "#e8e9ee"
MUTED = "#9aa0b4"
ACCENT = "#3ddc84"
ACCENT_DARK = "#2fb36a"
BLUE = "#4c8dff"
DANGER = "#ff5c5c"


# --------------------------------------------------------------------------- #
# Lấy ảnh màn hình từ ADB
# --------------------------------------------------------------------------- #
class ScreenshotProvider:
    """Lấy ảnh màn hình qua ADBController của dự án; nếu không có thì dùng adb thuần."""

    # Các module/tên lớp có thể chứa ADBController trong dự án
    _MODULE_CANDIDATES = (
        "core.adb_controller",
        "core.adb",
        "adb_controller",
        "controllers.adb_controller",
        "utils.adb_controller",
        "src.adb_controller",
        "bot.adb_controller",
    )
    _METHOD_CANDIDATES = (
        "capture_screen",      # đúng phương thức bot_logic.py dùng -> cùng nguồn ảnh với bot
        "screenshot",
        "take_screenshot",
        "get_screenshot",
        "capture",
        "screencap",
        "get_screen",
    )

    def __init__(self) -> None:
        self._controller = None
        self._init_error: Optional[str] = None
        self._try_load_controller()

    # -- ADBController của dự án ------------------------------------------- #
    def _try_load_controller(self) -> None:
        for mod_name in self._MODULE_CANDIDATES:
            try:
                module = importlib.import_module(mod_name)
            except Exception:
                continue
            cls = getattr(module, "ADBController", None)
            if cls is None:
                continue
            try:
                self._controller = cls()
                return
            except Exception as exc:  # noqa: BLE001
                self._init_error = f"Không khởi tạo được ADBController: {exc}"
        if self._controller is None and self._init_error is None:
            self._init_error = "Không tìm thấy ADBController, dùng adb trực tiếp."

    @staticmethod
    def _to_bgr(data) -> Optional[np.ndarray]:
        """Chuẩn hoá nhiều kiểu dữ liệu ảnh về mảng BGR của OpenCV."""
        if data is None:
            return None
        if isinstance(data, np.ndarray):
            if data.ndim == 2:
                return cv2.cvtColor(data, cv2.COLOR_GRAY2BGR)
            if data.ndim == 3 and data.shape[2] == 4:
                return cv2.cvtColor(data, cv2.COLOR_BGRA2BGR)
            return data
        if isinstance(data, Image.Image):
            return cv2.cvtColor(np.array(data.convert("RGB")), cv2.COLOR_RGB2BGR)
        if isinstance(data, (bytes, bytearray)):
            arr = np.frombuffer(data, dtype=np.uint8)
            return cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if isinstance(data, (str, os.PathLike)) and Path(data).exists():
            arr = np.fromfile(str(data), dtype=np.uint8)
            return cv2.imdecode(arr, cv2.IMREAD_COLOR)
        return None

    def _capture_with_controller(self) -> Optional[np.ndarray]:
        if self._controller is None:
            return None
        for name in self._METHOD_CANDIDATES:
            method = getattr(self._controller, name, None)
            if not callable(method):
                continue
            try:
                img = self._to_bgr(method())
            except Exception:  # noqa: BLE001
                continue
            if img is not None and img.size > 0:
                return img
        return None

    # -- adb thuần (dự phòng) ---------------------------------------------- #
    @staticmethod
    def _run_adb(args: list, timeout: int = 15) -> subprocess.CompletedProcess:
        return subprocess.run(
            [ADB_EXE, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def _capture_with_raw_adb(self) -> np.ndarray:
        try:
            for target in LDPLAYER_ADB_TARGETS:
                if ":" in target:
                    try:
                        self._run_adb(["connect", target], timeout=8)
                    except subprocess.TimeoutExpired:
                        continue

            devices = self._run_adb(["devices"]).stdout.decode(errors="ignore")
            lines = [ln for ln in devices.splitlines()[1:] if "\tdevice" in ln]
            if not lines:
                raise RuntimeError(
                    "Không tìm thấy thiết bị ADB nào.\n"
                    "Hãy mở LDPlayer 9 và bật 'Gỡ lỗi ADB' (Cài đặt > Khác)."
                )
            serial = lines[0].split("\t")[0]
            result = self._run_adb(["-s", serial, "exec-out", "screencap", "-p"])
            if result.returncode != 0 or not result.stdout:
                raise RuntimeError(result.stderr.decode(errors="ignore") or "screencap thất bại")
            img = self._to_bgr(result.stdout)
            if img is None:
                raise RuntimeError("Không giải mã được ảnh màn hình từ ADB.")
            return img
        except FileNotFoundError as exc:
            raise RuntimeError(
                "Không tìm thấy lệnh 'adb'. Hãy cài Android Platform Tools "
                "hoặc thêm adb vào biến môi trường PATH."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("ADB phản hồi quá chậm (timeout).") from exc

    def capture(self) -> np.ndarray:
        img = self._capture_with_controller()
        if img is not None:
            return img
        return self._capture_with_raw_adb()


# --------------------------------------------------------------------------- #
# Giao diện chính
# --------------------------------------------------------------------------- #
class CropToolApp(tk.Tk):
    MIN_BOX = 4  # kích thước tối thiểu của khung (pixel ảnh gốc)

    def __init__(self) -> None:
        super().__init__()
        self.title("Công cụ Thiết lập Ảnh mẫu - Auto Bot Clash of Clans")
        self.geometry("1180x760")
        self.minsize(900, 560)
        self.configure(bg=BG)

        self.provider = ScreenshotProvider()

        # Trạng thái ảnh / khung chọn
        self.image_bgr: Optional[np.ndarray] = None
        self.photo: Optional[ImageTk.PhotoImage] = None
        self.scale = 1.0
        self.offset = (0, 0)
        self.drag_start: Optional[Tuple[int, int]] = None
        self.selection: Optional[Tuple[int, int, int, int]] = None  # toạ độ ảnh gốc
        self.rect_id: Optional[int] = None
        self.var_preview_ok = tk.BooleanVar(value=False)   # người dùng xác nhận đã xem preview

        self._setup_style()
        self._build_ui()
        self._refresh_asset_status()

    # ------------------------------------------------------------------ UI -- #
    def _setup_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=BG, foreground=FG, font=("Segoe UI", 10))
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=PANEL, foreground=FG)
        style.configure("Title.TLabel", background=PANEL, foreground=FG,
                        font=("Segoe UI", 13, "bold"))
        style.configure("Muted.TLabel", background=PANEL, foreground=MUTED,
                        font=("Segoe UI", 9))
        style.configure("Accent.TButton", background=ACCENT, foreground="#0b1a11",
                        font=("Segoe UI", 10, "bold"), padding=(12, 9), borderwidth=0)
        style.map("Accent.TButton", background=[("active", ACCENT_DARK), ("disabled", "#44485a")])
        style.configure("Blue.TButton", background=BLUE, foreground="white",
                        font=("Segoe UI", 10, "bold"), padding=(12, 9), borderwidth=0)
        style.map("Blue.TButton", background=[("active", "#3a76d8"), ("disabled", "#44485a")])
        style.configure("Ghost.TButton", background="#3a3d4f", foreground=FG,
                        padding=(10, 7), borderwidth=0)
        style.map("Ghost.TButton", background=[("active", "#4a4e63")])
        style.configure("TCombobox", fieldbackground="#33364a", background="#33364a",
                        foreground=FG, arrowcolor=FG, padding=6)
        style.map("TCombobox", fieldbackground=[("readonly", "#33364a")],
                  foreground=[("readonly", FG)])
        self.option_add("*TCombobox*Listbox.background", "#33364a")
        self.option_add("*TCombobox*Listbox.foreground", FG)
        self.option_add("*TCombobox*Listbox.selectBackground", BLUE)

    def _build_ui(self) -> None:
        # Khung bên trái: Canvas
        left = tk.Frame(self, bg=BG)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(14, 7), pady=14)

        # highlightthickness=0: viền highlight làm toạ độ chuột lệch so với toạ độ vẽ trên Canvas
        self.canvas = tk.Canvas(left, bg="#0f1015", highlightthickness=0, bd=0,
                                cursor="crosshair")
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Configure>", lambda _e: self._render_image())

        self.canvas_hint = self.canvas.create_text(
            0, 0, text="Bấm \"Chụp màn hình Game\" để bắt đầu",
            fill=MUTED, font=("Segoe UI", 14), tags="hint")

        # Khung bên phải: điều khiển
        right = ttk.Frame(self, style="Panel.TFrame", padding=16)
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=(7, 14), pady=14)

        ttk.Label(right, text="THIẾT LẬP ẢNH MẪU", style="Title.TLabel").pack(anchor="w")
        ttk.Label(right, text="Chỉ cần làm 1 lần duy nhất", style="Muted.TLabel").pack(
            anchor="w", pady=(0, 14))

        ttk.Button(right, text="📷  Chụp màn hình Game", style="Blue.TButton",
                   command=self.capture_screen).pack(fill=tk.X)
        ttk.Button(right, text="📂  Mở ảnh từ máy (dự phòng)", style="Ghost.TButton",
                   command=self.open_image_file).pack(fill=tk.X, pady=(6, 0))

        ttk.Separator(right).pack(fill=tk.X, pady=14)

        ttk.Label(right, text="Chọn thành phần cần lưu:").pack(anchor="w")
        self.label_var = tk.StringVar(value=list(LABEL_TO_FILENAME.keys())[0])
        self.combo = ttk.Combobox(right, textvariable=self.label_var, state="readonly",
                                  values=list(LABEL_TO_FILENAME.keys()), width=56)
        self.combo.pack(fill=tk.X, pady=(4, 0))
        self.combo.bind("<<ComboboxSelected>>", lambda _e: self._update_filename_hint())

        self.filename_var = tk.StringVar()
        ttk.Label(right, textvariable=self.filename_var, style="Muted.TLabel").pack(
            anchor="w", pady=(4, 12))
        self._update_filename_hint()

        # Thông tin khung chọn
        self.scale_var = tk.StringVar(value="")
        ttk.Label(right, textvariable=self.scale_var, style="Muted.TLabel").pack(anchor="w")
        self.sel_var = tk.StringVar(value="Chưa khoanh vùng")
        ttk.Label(right, text="Vùng đang chọn:").pack(anchor="w")
        ttk.Label(right, textvariable=self.sel_var, foreground=ACCENT,
                  font=("Consolas", 10, "bold")).pack(anchor="w", pady=(2, 10))

        # Xem trước
        ttk.Label(right, text="Xem trước ảnh cắt (Preview Crop - cắt từ ẢNH GỐC):").pack(anchor="w")
        self.preview = tk.Label(right, bg="#0f1015", width=34, height=7,
                                text="(trống)", fg=MUTED)
        self.preview.pack(fill=tk.X, pady=(4, 4))
        self.preview_photo: Optional[ImageTk.PhotoImage] = None
        self.preview_info = tk.Label(right, text="", bg=PANEL, fg=MUTED, anchor="w",
                                     justify="left", font=("Consolas", 9))
        self.preview_info.pack(fill=tk.X)
        self.chk_preview = tk.Checkbutton(
            right, text="Đã thấy trọn 3 dòng (Vàng, Dầu, Dầu đen) - cho ROI tài nguyên",
            variable=self.var_preview_ok, bg=PANEL, fg=FG, selectcolor="#33364a",
            activebackground=PANEL, activeforeground=FG, anchor="w", justify="left",
            wraplength=330)
        self.chk_preview.pack(fill=tk.X, pady=(4, 10))

        self.save_btn = ttk.Button(right, text="✔  Xác nhận & Lưu vào Dự án",
                                   style="Accent.TButton", command=self.save_crop)
        self.save_btn.pack(fill=tk.X)

        ttk.Separator(right).pack(fill=tk.X, pady=14)

        ttk.Label(right, text="Trạng thái thiết lập (assets/ & config.json):").pack(anchor="w")
        self.status_frame = tk.Frame(right, bg=PANEL)
        self.status_frame.pack(fill=tk.X, pady=(6, 0))
        self.status_labels = {}
        for label in LABEL_TO_FILENAME:
            lbl = tk.Label(self.status_frame, text="", bg=PANEL, fg=MUTED,
                           anchor="w", justify="left", wraplength=400, font=("Segoe UI", 9))
            lbl.pack(fill=tk.X)
            self.status_labels[label] = lbl

        self.msg_var = tk.StringVar(value="Sẵn sàng.")
        self.msg_label = tk.Label(right, textvariable=self.msg_var, bg=PANEL, fg=MUTED,
                                  wraplength=330, justify="left", anchor="w")
        self.msg_label.pack(fill=tk.X, side=tk.BOTTOM, pady=(12, 0))

    # ---------------------------------------------------------- Tiện ích UI -- #
    def _update_filename_hint(self) -> None:
        fname = LABEL_TO_FILENAME[self.label_var.get()]
        if is_roi(fname):
            self.filename_var.set(
                f"→ Sẽ lưu toạ độ vào config.json  [{fname}]\n"
                "   Mẹo: khoanh sát phần chữ số, tránh đè lên biểu tượng Vàng/Dầu.")
            return
        target = fname
        lines = []
        if supports_variants(fname) and (ASSETS_DIR / fname).exists():
            target = next_variant_filename(fname)
            lines.append(f"→ Đã có {count_variants(fname)} ảnh mẫu. Lần lưu này sẽ thêm biến thể:")
            lines.append(f"   assets/{target}")
        else:
            lines.append(f"→ Sẽ lưu thành: assets/{target}")
        if fname in CLICK_POS_KEYS:
            lines.append(f"   và ghi toạ độ tâm vùng khoanh vào config.json [{CLICK_POS_KEYS[fname]}]")
        self.filename_var.set("\n".join(lines))

    def _set_message(self, text: str, color: str = MUTED) -> None:
        self.msg_var.set(text)
        self.msg_label.configure(fg=color)

    def _refresh_asset_status(self) -> None:
        try:
            cfg = load_config()
        except ValueError:
            cfg = {}
        for label, fname in LABEL_TO_FILENAME.items():
            extra = ""
            if is_roi(fname):
                done = get_saved_roi(cfg, fname) is not None
            elif supports_variants(fname):
                n = count_variants(fname)
                done = n > 0
                extra = f" [{n} biến thể]" if n else ""
            else:
                done = (ASSETS_DIR / fname).exists()
            mark = "✅" if done else "⬜"
            self.status_labels[label].configure(
                text=f"{mark} {label}{extra}", fg=ACCENT if done else MUTED)

    # ------------------------------------------------------- Lấy / mở ảnh -- #
    def capture_screen(self) -> None:
        self._set_message("Đang chụp màn hình từ LDPlayer 9...", MUTED)
        self.update_idletasks()
        try:
            img = self.provider.capture()
        except Exception as exc:  # noqa: BLE001
            self._set_message("Không chụp được màn hình. Kiểm tra kết nối ADB.", DANGER)
            messagebox.showerror(
                "Lỗi kết nối ADB",
                f"Không thể chụp màn hình game.\n\n{exc}\n\n"
                "Gợi ý:\n"
                "• Mở LDPlayer 9 và bật 'Gỡ lỗi ADB'.\n"
                "• Kiểm tra adb có trong PATH.\n"
                "• Hoặc dùng nút 'Mở ảnh từ máy' để chọn ảnh có sẵn.")
            return
        note = self._set_image(img)
        if note:
            self._set_message(f"Đã chụp. {note} Hãy kéo chuột để khoanh vùng.", "#ffb84d")
            return
        self._set_message(
            f"Đã chụp màn hình ({BOT_SCREEN_SIZE[0]}x{BOT_SCREEN_SIZE[1]}). "
            "Hãy kéo chuột để khoanh vùng.", ACCENT)

    def open_image_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Chọn ảnh chụp màn hình",
            filetypes=[("Ảnh", "*.png *.jpg *.jpeg *.bmp"), ("Tất cả", "*.*")])
        if not path:
            return
        try:
            arr = np.fromfile(path, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                raise ValueError("Định dạng ảnh không hợp lệ.")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Lỗi", f"Không mở được ảnh:\n{exc}")
            return
        note = self._set_image(img)
        if note:
            self._set_message(f"Đã mở ảnh. {note} Hãy kéo chuột để khoanh vùng.", "#ffb84d")
        else:
            self._set_message("Đã mở ảnh. Hãy kéo chuột để khoanh vùng.", ACCENT)

    def _set_image(self, img: np.ndarray) -> str:
        """Chuẩn hóa ảnh về 960x540 rồi đưa lên Canvas. Trả về ghi chú chuẩn hóa ('' nếu không cần)."""
        note = ""
        try:
            img, note = normalize_to_bot_size(img)
        except Exception as exc:  # noqa: BLE001
            note = f"Không chuẩn hóa được ảnh: {exc}"
        self.image_bgr = img
        self._clear_selection()
        self._render_image()
        return note

    # ------------------------------------------------------------ Vẽ ảnh -- #
    def _render_image(self) -> None:
        cw = max(self.canvas.winfo_width(), 1)
        ch = max(self.canvas.winfo_height(), 1)
        self.canvas.delete("img")
        self.canvas.delete("hint")

        if self.image_bgr is None:
            self.canvas.create_text(cw // 2, ch // 2,
                                    text="Bấm \"Chụp màn hình Game\" để bắt đầu",
                                    fill=MUTED, font=("Segoe UI", 14), tags="hint")
            return

        ih, iw = self.image_bgr.shape[:2]
        self.scale = min(cw / iw, ch / ih)
        nw, nh = max(int(iw * self.scale), 1), max(int(ih * self.scale), 1)
        self.offset = ((cw - nw) // 2, (ch - nh) // 2)
        self.scale_var.set(
            f"Ảnh gốc {iw}x{ih} → hiển thị {nw}x{nh} (scale {self.scale:.3f}, "
            f"lề {self.offset[0]},{self.offset[1]})")

        rgb = cv2.cvtColor(self.image_bgr, cv2.COLOR_BGR2RGB)
        pil = Image.fromarray(rgb).resize((nw, nh), Image.LANCZOS)
        self.photo = ImageTk.PhotoImage(pil)
        self.canvas.create_image(self.offset[0], self.offset[1], anchor="nw",
                                 image=self.photo, tags="img")
        self.canvas.tag_lower("img")
        self._draw_selection()

    # --------------------------------------------------- Chuyển toạ độ ----- #
    def _canvas_to_image(self, cx: float, cy: float) -> Tuple[int, int]:
        """
        Canvas -> toạ độ ẢNH GỐC.
        Ảnh được thu/phóng ĐỒNG ĐỀU (self.scale) và căn giữa Canvas (self.offset = lề đen),
        nên phải TRỪ lề rồi mới chia scale:  x_goc = (x_canvas - lề_x) / scale.
        (Nhân thẳng gốc_rộng/canvas_rộng sẽ sai vì bỏ qua lề và tỉ lệ khung Canvas.)
        """
        ih, iw = self.image_bgr.shape[:2]
        x = round((cx - self.offset[0]) / self.scale)
        y = round((cy - self.offset[1]) / self.scale)
        return max(0, min(iw, x)), max(0, min(ih, y))

    def _image_to_canvas(self, x: int, y: int) -> Tuple[float, float]:
        return x * self.scale + self.offset[0], y * self.scale + self.offset[1]

    # ------------------------------------------------------- Sự kiện chuột -- #
    def _on_press(self, event) -> None:
        if self.image_bgr is None:
            return
        self.drag_start = self._canvas_to_image(event.x, event.y)
        self.selection = None
        self.var_preview_ok.set(False)

    def _on_drag(self, event) -> None:
        if self.image_bgr is None or self.drag_start is None:
            return
        x2, y2 = self._canvas_to_image(event.x, event.y)
        x1, y1 = self.drag_start
        self.selection = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        self._draw_selection()

    def _on_release(self, event) -> None:
        if self.image_bgr is None or self.drag_start is None:
            return
        self._on_drag(event)
        self.drag_start = None
        if self.selection:
            x1, y1, x2, y2 = self.selection
            if (x2 - x1) < self.MIN_BOX or (y2 - y1) < self.MIN_BOX:
                self._clear_selection()
                self._set_message("Vùng chọn quá nhỏ, hãy khoanh lại.", DANGER)
                return
            self._update_preview()
            self._set_message("Đã khoanh vùng. Chọn tên rồi bấm Lưu.", ACCENT)

    def _draw_selection(self) -> None:
        self.canvas.delete("sel")
        if not self.selection or self.image_bgr is None:
            return
        x1, y1, x2, y2 = self.selection
        cx1, cy1 = self._image_to_canvas(x1, y1)
        cx2, cy2 = self._image_to_canvas(x2, y2)
        self.canvas.create_rectangle(cx1, cy1, cx2, cy2, outline=ACCENT, width=2, tags="sel")
        self.sel_var.set(f"x:{x1}  y:{y1}  ({x2 - x1}×{y2 - y1} px)")

    def _clear_selection(self) -> None:
        self.selection = None
        self.drag_start = None
        self.canvas.delete("sel")
        self.sel_var.set("Chưa khoanh vùng")
        self.preview.configure(image="", text="(trống)")
        self.preview_photo = None
        self.preview_info.configure(text="")
        self.var_preview_ok.set(False)

    def _show_crop_preview(self, crop: np.ndarray, info: str) -> None:
        """Hiện ảnh cắt (phóng to tối đa 4 lần để nhìn rõ chữ số) kèm dòng thông tin toạ độ."""
        h, w = crop.shape[:2]
        zoom = min(300 / w, 150 / h, 4.0)
        zoom = max(zoom, 0.1)
        interp = cv2.INTER_CUBIC if zoom > 1 else cv2.INTER_AREA
        big = cv2.resize(crop, (max(int(w * zoom), 1), max(int(h * zoom), 1)),
                         interpolation=interp)
        pil = Image.fromarray(cv2.cvtColor(big, cv2.COLOR_BGR2RGB))
        self.preview_photo = ImageTk.PhotoImage(pil)
        self.preview.configure(image=self.preview_photo, text="", width=0, height=0)
        self.preview_info.configure(text=info)

    def _update_preview(self) -> None:
        """Preview Crop: cắt trực tiếp trên ảnh GỐC theo [X1, Y1, X2, Y2] vừa tính."""
        try:
            if not self.selection or self.image_bgr is None:
                return
            x1, y1, x2, y2 = (int(v) for v in self.selection)
            crop = self.image_bgr[y1:y2, x1:x2]
            if crop.size == 0:
                return
            self._show_crop_preview(
                crop, f"ROI = [{x1}, {y1}, {x2}, {y2}]  ({x2 - x1}x{y2 - y1} px)")
        except Exception as exc:  # noqa: BLE001
            self._set_message(f"Không tạo được ảnh xem trước: {exc}", DANGER)

    # ------------------------------------------------------------- Lưu ảnh -- #
    def save_crop(self) -> None:
        if self.image_bgr is None:
            messagebox.showwarning("Chưa có ảnh", "Hãy chụp màn hình game trước.")
            return
        if not self.selection:
            messagebox.showwarning("Chưa khoanh vùng",
                                   "Hãy kéo chuột trái trên ảnh để khoanh vùng cần lưu.")
            return

        label = self.label_var.get()
        filename = LABEL_TO_FILENAME.get(label)
        if not filename:
            messagebox.showerror("Lỗi", "Nhãn không hợp lệ.")
            return

        if is_roi(filename):
            self._save_roi(label, filename)
            return

        x1, y1, x2, y2 = self.selection
        crop = self.image_bgr[y1:y2, x1:x2]
        if crop.size == 0:
            messagebox.showerror("Lỗi", "Vùng cắt rỗng, hãy khoanh lại.")
            return

        # Tên file thực sự sẽ lưu. Nhãn hỗ trợ biến thể mà file mặc định đã có -> đề xuất _2, _3...
        save_name = filename
        variant_no = 0   # >0: đang lưu thành ảnh biến thể thứ mấy (1 = ảnh gốc)
        if (ASSETS_DIR / filename).exists():
            if supports_variants(filename):
                existing_n = count_variants(filename)
                variant_name = next_variant_filename(filename)
                answer = messagebox.askyesnocancel(
                    "Thêm ảnh biến thể?",
                    f"Nhãn này đã có {existing_n} ảnh mẫu trong assets/.\n\n"
                    f"• YES  : lưu thành ảnh BIẾN THỂ mới  →  assets/{variant_name}\n"
                    f"• NO   : ghi đè ảnh gốc  →  assets/{filename}\n"
                    f"• Cancel: không lưu\n\n"
                    "Bot sẽ thử lần lượt tất cả ảnh (gốc + biến thể) để tăng tỉ lệ nhận diện.")
                if answer is None:
                    return
                if answer:
                    save_name = variant_name
                    variant_no = existing_n + 1
            elif not messagebox.askyesno(
                    "Ghi đè?", f"File assets/{filename} đã tồn tại.\nBạn có muốn lưu đè không?"):
                return

        target = ASSETS_DIR / save_name
        try:
            ASSETS_DIR.mkdir(parents=True, exist_ok=True)
            ok, buf = cv2.imencode(".png", crop)
            if not ok:
                raise RuntimeError("Không mã hoá được ảnh PNG.")
            buf.tofile(str(target))  # an toàn với đường dẫn Unicode trên Windows
        except Exception as exc:  # noqa: BLE001
            self._set_message("Lưu thất bại.", DANGER)
            messagebox.showerror("Lỗi khi lưu", f"Không thể lưu file:\n{exc}")
            return

        # Nút có toạ độ bấm riêng (vd. Về nhà): ghi tâm vùng khoanh vào config.json ngay lập tức
        pos_note = ""
        pos_key = CLICK_POS_KEYS.get(filename)
        if pos_key:
            try:
                cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                cfg = load_config()
                if pos_key in MANUAL_POS_KEYS:
                    manual = cfg.get("MANUAL_POSITIONS")
                    if not isinstance(manual, dict):
                        manual = {}
                    manual[pos_key] = [cx, cy]
                    cfg["MANUAL_POSITIONS"] = manual
                    where = f"MANUAL_POSITIONS.{pos_key}"
                else:
                    cfg[pos_key] = [cx, cy]
                    where = pos_key
                save_config(cfg)
                pos_note = f"\n\nconfig.json → {where} = [{cx}, {cy}]"
            except Exception as exc:  # noqa: BLE001
                pos_note = (f"\n\n⚠️ Đã lưu ảnh nhưng KHÔNG ghi được {pos_key} "
                            f"vào config.json: {exc}")

        name_vi = label.split(". ", 1)[-1]
        variant_note = f" (biến thể thứ {variant_no})" if variant_no else ""
        self._set_message(f"Lưu thành công {name_vi}{variant_note}!", ACCENT)
        messagebox.showinfo("Thành công",
                            f"Lưu thành công {name_vi}{variant_note}!\n\nassets/{save_name}{pos_note}")
        self._refresh_asset_status()
        self._update_filename_hint()
        if variant_no:
            # Vừa thêm biến thể: giữ nguyên nhãn để người dùng thêm tiếp biến thể kế (không nhảy sang nhãn khác)
            self._clear_selection()
        else:
            self._advance_label()

    def _save_roi(self, label: str, key: str) -> None:
        """Lưu toạ độ ROI [x1, y1, x2, y2] (pixel ảnh gốc) vào config.json."""
        x1, y1, x2, y2 = (int(v) for v in self.selection)
        ih, iw = self.image_bgr.shape[:2]
        name_vi = label.split(". ", 1)[-1]

        # Chỉ lưu khi người dùng đã nhìn Preview Crop và tick xác nhận
        if not self.var_preview_ok.get():
            self._set_message("Hãy xem Preview Crop; thấy đủ nội dung thì tick ô xác nhận rồi Lưu.",
                              DANGER)
            messagebox.showwarning(
                "Chưa xác nhận Preview",
                "Hãy nhìn ảnh 'Xem trước ảnh cắt' bên phải.\n"
                "Với ROI tài nguyên, phải thấy trọn 3 dòng (Vàng, Dầu, Dầu đen) "
                "rồi tick ô xác nhận trước khi Lưu.")
            return

        try:
            cfg = load_config()
        except ValueError as exc:
            self._set_message("Không đọc được config.json.", DANGER)
            messagebox.showerror("Lỗi config.json", str(exc))
            return

        existing = get_saved_roi(cfg, key)
        if existing and not messagebox.askyesno(
                "Ghi đè?", f"{key} đã có trong config.json:\n{existing}\n\n"
                           "Bạn có muốn lưu đè không?"):
            return

        # Cảnh báo nếu độ phân giải khác với các ROI đã lưu trước đó
        old_size = cfg.get("screen_size")
        other_rois = [k for k in ROI_KEYS if k != key and get_saved_roi(cfg, k)]
        rescaled = {}
        try:
            if other_rois and old_size and [int(old_size[0]), int(old_size[1])] != [iw, ih]:
                ow, oh = int(old_size[0]), int(old_size[1])
                if not messagebox.askyesno(
                        "Khác độ phân giải",
                        f"Các ROI đã lưu trước đó được khoanh trên màn hình {ow}x{oh}, "
                        f"còn chuẩn hiện tại là {iw}x{ih}.\n\n"
                        "Tự động quy đổi các ROI cũ sang chuẩn mới theo tỉ lệ rồi lưu?\n"
                        "(Nên khoanh lại các ROI đó để chính xác tuyệt đối.)"):
                    return
                for k in other_rois:
                    v = get_saved_roi(cfg, k)
                    rescaled[k] = [round(v[0] * iw / ow), round(v[1] * ih / oh),
                                   round(v[2] * iw / ow), round(v[3] * ih / oh)]
        except Exception as exc:  # noqa: BLE001
            self._set_message("Không quy đổi được ROI cũ.", DANGER)
            messagebox.showerror("Lỗi", f"Không quy đổi được các ROI cũ:\n{exc}")
            return

        try:
            # Chuyển ROI của bản cũ ("rois") lên khóa cấp cao nhất rồi bỏ khóa lồng nhau
            legacy = cfg.pop("rois", None)
            if isinstance(legacy, dict):
                for old_key, old_val in legacy.items():
                    cfg.setdefault(old_key, old_val)
            cfg.update(rescaled)           # ROI cũ đã quy đổi về 960x540 (nếu có)
            cfg[key] = [x1, y1, x2, y2]
            cfg["screen_size"] = [iw, ih]  # luôn là 960x540 vì ảnh đã được chuẩn hóa
            save_config(cfg)
        except Exception as exc:  # noqa: BLE001
            self._set_message("Lưu thất bại.", DANGER)
            messagebox.showerror("Lỗi khi lưu", f"Không thể ghi config.json:\n{exc}")
            return

        # Đồng bộ & kiểm chứng: đọc LẠI config.json từ đĩa, cắt lại ảnh gốc theo giá trị đã lưu
        try:
            saved = get_saved_roi(load_config(), key)
            if saved is None or [int(v) for v in saved[:4]] != [x1, y1, x2, y2]:
                raise ValueError(f"giá trị đọc lại {saved} khác giá trị vừa lưu")
            sx1, sy1, sx2, sy2 = (int(v) for v in saved[:4])
            self._show_crop_preview(
                self.image_bgr[sy1:sy2, sx1:sx2],
                f"✔ Đã đọc lại config.json: {key} = [{sx1}, {sy1}, {sx2}, {sy2}]")
        except Exception as exc:  # noqa: BLE001
            self._set_message(f"Đã ghi nhưng kiểm chứng lại thất bại: {exc}", DANGER)
            messagebox.showwarning("Kiểm chứng thất bại", f"Hãy kiểm tra config.json:\n{exc}")
            return

        self._set_message(f"Lưu thành công {name_vi}!", ACCENT)
        messagebox.showinfo(
            "Thành công",
            f"Lưu thành công {name_vi}!\n\nconfig.json → {key}\n"
            f"[x1, y1, x2, y2] = [{x1}, {y1}, {x2}, {y2}]")
        self._refresh_asset_status()
        self._advance_label()

    def _advance_label(self) -> None:
        """Tự chuyển sang nhãn kế tiếp để thao tác nhanh hơn."""
        labels = list(LABEL_TO_FILENAME.keys())
        idx = labels.index(self.label_var.get())
        if idx + 1 < len(labels):
            self.label_var.set(labels[idx + 1])
            self._update_filename_hint()
        self._clear_selection()


def main() -> None:
    app = CropToolApp()
    app.mainloop()


if __name__ == "__main__":
    main()