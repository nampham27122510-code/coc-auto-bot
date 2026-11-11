#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Công cụ thiết lập ảnh mẫu (Crop Tool) cho Auto Bot Clash of Clans - LDPlayer 9.

Cách dùng:
    python tools/crop_tool.py

Quy trình:
    0. Mục 22 (ANOMALY_BUTTONS): ảnh nút xử lý LUỒNG LẠ (popup sự kiện / bảo trì, nút X, Hủy, Xác nhận...).
       Mỗi lần lưu tạo file mới assets/anomaly/anomaly_btn_1.png, anomaly_btn_2.png ... (không bao giờ ghi đè)
       và tự cập nhật config.json -> ANOMALY_BUTTON_IMAGES; cắt bao nhiêu ảnh tùy ý, không cần sửa code.
    1. Bấm "Chụp màn hình Game" để lấy ảnh mới nhất từ LDPlayer 9 qua ADB.
    2. Kéo giữ chuột trái trên ảnh để khoanh vùng (khung xanh lá).
    3. Chọn tên Tiếng Việt tương ứng trong danh sách.
    4. Bấm "Xác nhận & Lưu vào Dự án":
       - Nhãn ảnh mẫu (nút bấm...)  -> lưu file PNG vào thư mục assets/.
         Mỗi nhãn có thể có NHIỀU ảnh biến thể: nếu file mặc định (vd button_return_home.png) đã
         tồn tại, công cụ đề xuất lưu thành button_return_home_2.png, _3.png... (bot tự nạp hết).
         (Tường: 4 nhóm ảnh dòng đề xuất = {Làng chính, Làng phụ} x {Vàng, Dầu}:
          wall_item_main_gold / main_elixir / sub_gold / sub_elixir. KHÔNG còn chia cấp A/B:
          bạn tự do chọn cấp Tường bất kỳ (ô "Cấp Tường", bắt buộc khi lưu Tường) và chụp BAO NHIÊU ẢNH tùy ý cho
          mỗi nhóm; MỖI CẤP phải có đủ cặp thẻ Vàng + thẻ Dầu (công cụ cảnh báo cấp nào thiếu cặp) - mỗi lần lưu luôn tạo file mới (vd wall_item_main_gold_lv12.png, _lv12_2.png ...),
          không bao giờ ghi đè; bot nạp hết và chấm điểm nhận diện trên toàn bộ ảnh của nhóm.
          Các nút Nâng/Xác nhận của Tường vẫn bấm bằng tọa độ thủ công chọn trên giao diện chính)
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
import re
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
    # Không phân cấp A/B: cấp Tường tự do, mỗi nhóm chụp nhiều ảnh (mọi cấp, mọi trạng thái) để bot chấm điểm.
    "11. Tường LÀNG CHÍNH - Dòng đề xuất, nút Vàng SÁNG (Đủ Vàng) - nhiều ảnh": "wall_item_main_gold.png",
    "12. Tường LÀNG CHÍNH - Dòng đề xuất, nút Dầu SÁNG (Đủ Dầu) - nhiều ảnh": "wall_item_main_elixir.png",
    "13. Tường LÀNG PHỤ - Dòng đề xuất, nút Vàng SÁNG (Đủ Vàng) - nhiều ảnh": "wall_item_sub_gold.png",
    "14. Tường LÀNG PHỤ - Dòng đề xuất, nút Dầu SÁNG (Đủ Dầu) - nhiều ảnh": "wall_item_sub_elixir.png",

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

    # --- Luồng lạ: popup sự kiện / bảo trì / màn hình chờ không xác định (cắt BAO NHIÊU ẢNH CŨNG ĐƯỢC) ---
    "22. Nút xử lý luồng lạ (X đóng / Hủy / Xác nhận popup lạ) - nhiều ảnh": "ANOMALY_BUTTONS",

    # --- LÀNG PHỤ (Làng thợ xây): tên file KHÔNG bắt đầu bằng button_attack / confirm_... của làng chính
    #     để wildcard stem*.png của 2 làng không nạp lẫn ảnh của nhau. Mỗi nút lưu được NHIỀU ảnh biến thể. ---
    "23. Nút Tấn công - LÀNG PHỤ (Làng thợ xây)": "builder_attack_btn.png",
    "24. Nút Xác nhận tấn công - LÀNG PHỤ (popup xác nhận)": "builder_confirm_attack_btn.png",
    "25. Nút Nhận thưởng - LÀNG PHỤ (Làng thợ xây)": "builder_claim_reward_btn.png",
    "26. Nút Xác nhận nhận thưởng - LÀNG PHỤ (popup xác nhận)": "builder_confirm_reward_btn.png",

    # --- THUYỀN ở Làng chính (bấm để sang Làng thợ xây). Cắt NHIỀU ảnh khi làng đã zoom out xa nhất (giống lúc bot tìm):
    #     nhiều góc / nhiều khung hoạt ảnh nước. Lưu thành boat_to_builder.png, _2, _3... (bot tự nạp hết). ---
    "27. Thuyền sang Làng thợ xây (Làng chính, zoom out xa nhất) - nhiều ảnh": "boat_to_builder.png",

    # --- THUYỀN ở Làng thợ xây (bấm để VỀ Làng chính khi đang farm Làng chính mà game đứng ở Làng phụ). Cũng cắt NHIỀU ảnh
    #     khi làng đã zoom out xa nhất. Lưu thành boat_to_main.png, _2, _3... (bot tự nạp hết). ---
    "28. Thuyền về Làng chính (Làng thợ xây, zoom out xa nhất) - nhiều ảnh": "boat_to_main.png",

    # --- LÀNG PHỤ - LƯỢT 2: màn đếm ngược trước mỗi lượt tấn công. CHỈ cắt phần CHỮ 'Trận chiến bắt đầu trong vòng:'
    #     (KHÔNG cắt số giây vì số đổi liên tục). Bot dùng ảnh này để biết lượt 2 đã bắt đầu và thả quân lại. ---
    "29. Chữ 'Trận chiến bắt đầu trong vòng' - LÀNG PHỤ lượt 2 (chỉ cắt CHỮ, không cắt số giây)": "builder_round_start.png",
}

# Ảnh mẫu nút bấm đồng thời cập nhật toạ độ bấm (tâm vùng khoanh) vào config.json
CLICK_POS_KEYS = {
    "button_return_home.png": "RETURN_HOME_POS",
    "button_reload.png": "RELOAD_GAME_POS",
}

# Khóa tọa độ nằm trong config.json -> "MANUAL_POSITIONS" (khớp ô nhập trên GUI, tab Tọa độ);
# các khóa còn lại (RETURN_HOME_POS) nằm ở cấp cao nhất của config.json.
MANUAL_POS_KEYS = {"RELOAD_GAME_POS"}


# Danh mục đặc biệt: nhiều ảnh nút xử lý luồng lạ, lưu vào assets/anomaly/ với tên tăng dần.
ANOMALY_KEY = "ANOMALY_BUTTONS"
ANOMALY_DIR_NAME = "anomaly"                      # khớp config.py -> ANOMALY_ASSET_DIR
ANOMALY_DIR = ASSETS_DIR / ANOMALY_DIR_NAME
ANOMALY_FILE_RE = re.compile(r"^anomaly_btn_(\d+)\.png$", re.IGNORECASE)
ANOMALY_CONFIG_DEFAULTS = {                       # khớp JSON_CONFIG_DEFAULTS của config.py (chỉ bổ sung khóa còn thiếu)
    "ANOMALY_MATCH_THRESHOLD": 0.85,
    "ANOMALY_SCAN_RETRIES": 2,
    "ANOMALY_SETTLE_SECONDS": 1.5,
}


def is_anomaly(target: str) -> bool:
    """Nhãn ANOMALY_BUTTONS (nhiều ảnh nút xử lý luồng lạ, không phải ROI cũng không phải 1 file cố định)."""
    return target == ANOMALY_KEY


def is_roi(target: str) -> bool:
    """Giá trị không kết thúc bằng .png (và không phải ANOMALY_BUTTONS) là khoá ROI (lưu toạ độ, không lưu ảnh)."""
    return (not target.lower().endswith(".png")) and not is_anomaly(target)


def _anomaly_number(name: str) -> int:
    m = ANOMALY_FILE_RE.match(name)
    return int(m.group(1)) if m else -1


def list_anomaly_files() -> List[str]:
    """Tên các file anomaly_btn_N.png trong assets/anomaly/, sắp theo N tăng dần; thư mục chưa có -> []."""
    try:
        names = [n for n in os.listdir(ANOMALY_DIR) if ANOMALY_FILE_RE.match(n)]
    except OSError:
        return []
    return sorted(names, key=_anomaly_number)


def next_anomaly_filename() -> str:
    """anomaly_btn_(N+1).png với N = số lớn nhất đang có (xóa bớt ảnh giữa chừng cũng không trùng / ghi đè ảnh cũ)."""
    nums = [_anomaly_number(n) for n in list_anomaly_files()]
    return f"anomaly_btn_{(max(nums) if nums else 0) + 1}.png"


def sync_anomaly_config() -> List[str]:
    """
    Cập nhật config.json -> ANOMALY_BUTTON_IMAGES theo các file đang có trong assets/anomaly/ (dạng 'anomaly/anomaly_btn_N.png').
    Giữ lại các đường dẫn khác người dùng tự thêm tay (không nằm trong anomaly/); bổ sung ngưỡng / số lần quét lại nếu thiếu.
    Trả về danh sách đã ghi.
    """
    cfg = load_config()
    prefix = ANOMALY_DIR_NAME + "/"
    mine = [prefix + n for n in list_anomaly_files()]
    old = cfg.get("ANOMALY_BUTTON_IMAGES")
    extras = []
    if isinstance(old, list):
        for item in old:
            if isinstance(item, str) and item.strip():
                norm = item.strip().replace("\\", "/")
                if not norm.lower().startswith(prefix) and norm not in extras:
                    extras.append(norm)
    cfg["ANOMALY_BUTTON_IMAGES"] = mine + extras
    for key, value in ANOMALY_CONFIG_DEFAULTS.items():
        cfg.setdefault(key, value)
    save_config(cfg)
    return cfg["ANOMALY_BUTTON_IMAGES"]


ROI_KEYS = [v for v in LABEL_TO_FILENAME.values() if is_roi(v)]

# Ảnh KHÔNG dùng để so khớp mẫu (bot không đọc dạng template) -> không có khái niệm biến thể,
# lưu lại vẫn hỏi "Ghi đè?" như trước.
NO_VARIANT_FILES = {"builder_region.png"}


def supports_variants(filename: str) -> bool:
    """Nhãn ảnh mẫu (.png) có hỗ trợ lưu nhiều ảnh biến thể không."""
    return (not is_roi(filename)) and not is_anomaly(filename) and filename not in NO_VARIANT_FILES


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


# --- Ảnh dòng đề xuất TƯỜNG: 2 loại tường (làng chính / làng phụ) x 2 tài nguyên (Vàng / Dầu) ---
# Không phân cấp A/B. Cấp Tường tùy ý (chỉ là nhãn trong tên file); mỗi nhóm lưu được nhiều ảnh.
WALL_ITEM_FILES = {
    "wall_item_main_gold.png", "wall_item_main_elixir.png",
    "wall_item_sub_gold.png", "wall_item_sub_elixir.png",
}
WALL_LEVEL_RE = re.compile(r"_lv(\d+)", re.IGNORECASE)


def is_wall_item(filename: str) -> bool:
    """Nhãn ảnh dòng đề xuất Tường (làng chính / làng phụ x Vàng / Dầu) - lưu nhiều ảnh, không ghi đè."""
    return filename in WALL_ITEM_FILES


def sanitize_wall_level(text: str) -> str:
    """Cấp Tường người dùng nhập: SỐ NGUYÊN BẤT KỲ (kể cả 0, không giới hạn độ dài) -> chỉ giữ chữ số,
    bỏ số 0 thừa ở đầu ('007' -> '7', '000' -> '0'); không có chữ số nào -> '' (chưa nhập)."""
    digits = re.sub(r"\D", "", text or "")
    if not digits:
        return ""
    return digits.lstrip("0") or "0"


def next_wall_filename(filename: str, level: str = "") -> str:
    """
    Tên file mới còn trống cho 1 ảnh Tường (KHÔNG BAO GIỜ trùng tên ảnh cũ):
      không cấp : wall_item_main_gold.png, wall_item_main_gold_2.png, _3 ...
      có cấp 12 : wall_item_main_gold_lv12.png, wall_item_main_gold_lv12_2.png ...
    Mọi tên đều khớp wildcard wall_item_main_gold*.png mà bot nạp.
    """
    stem, ext = os.path.splitext(filename)
    base = f"{stem}_lv{level}" if level else stem
    candidate = f"{base}{ext}"
    n = 2
    while (ASSETS_DIR / candidate).exists():
        candidate = f"{base}_{n}{ext}"
        n += 1
    return candidate


def wall_level_summary(filename: str) -> str:
    """Tóm tắt các cấp đã có ảnh của 1 nhóm Tường, vd 'lv12 x2, lv13, chưa gắn cấp x1'."""
    counts: dict = {}
    for name in list_template_files(filename):
        m = WALL_LEVEL_RE.search(name[len(os.path.splitext(filename)[0]):])
        key = int(m.group(1)) if m else None      # None = ảnh chưa gắn cấp
        counts[key] = counts.get(key, 0) + 1
    parts = []
    for key in sorted(counts, key=lambda k: (k is None, k if k is not None else 0)):
        label = f"lv{key}" if key is not None else "chưa gắn cấp"
        parts.append(label if counts[key] == 1 else f"{label} x{counts[key]}")
    return ", ".join(parts)


def wall_levels(filename: str) -> set:
    """Các cấp Tường (số nguyên bất kỳ, kể cả 0) đã có ảnh trong nhóm này (bỏ qua ảnh chưa gắn cấp)."""
    stem = os.path.splitext(filename)[0]
    levels = set()
    for name in list_template_files(filename):
        m = WALL_LEVEL_RE.search(name[len(stem):])
        if m:
            levels.add(int(m.group(1)))
    return levels


def wall_counterpart(filename: str) -> str:
    """Thẻ còn lại của cùng loại Tường: main_gold <-> main_elixir, sub_gold <-> sub_elixir."""
    if "_gold" in filename:
        return filename.replace("_gold", "_elixir")
    return filename.replace("_elixir", "_gold")


def wall_resource_vi(filename: str) -> str:
    return "Vàng" if "_gold" in filename else "Dầu"


def wall_missing_pair_levels(filename: str) -> List[int]:
    """Các cấp đã có ảnh ở nhóm này nhưng CHƯA có ảnh ở thẻ còn lại (Vàng <-> Dầu) -> thiếu cặp."""
    return sorted(wall_levels(filename) - wall_levels(wall_counterpart(filename)))


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

        # Khung bên phải: nút Lưu + thông báo GHIM cố định ở đáy (luôn thấy); phần còn lại cuộn được
        outer = ttk.Frame(self, style="Panel.TFrame", padding=(14, 14, 6, 14))
        outer.pack(side=tk.RIGHT, fill=tk.Y, padx=(7, 14), pady=14)

        self.msg_var = tk.StringVar(value="Sẵn sàng.")
        self.msg_label = tk.Label(outer, textvariable=self.msg_var, bg=PANEL, fg=MUTED,
                                  wraplength=400, justify="left", anchor="w")
        self.msg_label.pack(fill=tk.X, side=tk.BOTTOM, pady=(8, 0))

        self.save_btn = ttk.Button(outer, text="✔  Xác nhận & Lưu vào Dự án",
                                   style="Accent.TButton", command=self.save_crop)
        self.save_btn.pack(fill=tk.X, side=tk.BOTTOM, pady=(10, 0), padx=(0, 8))

        scroll_canvas = tk.Canvas(outer, bg=PANEL, highlightthickness=0, bd=0, width=440)
        vscroll = ttk.Scrollbar(outer, orient="vertical", command=scroll_canvas.yview)
        scroll_canvas.configure(yscrollcommand=vscroll.set)
        vscroll.pack(side=tk.RIGHT, fill=tk.Y)
        scroll_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        right = tk.Frame(scroll_canvas, bg=PANEL)
        win_id = scroll_canvas.create_window((0, 0), window=right, anchor="nw")
        right.bind("<Configure>", lambda _e: scroll_canvas.configure(scrollregion=scroll_canvas.bbox("all")))
        scroll_canvas.bind("<Configure>", lambda e: scroll_canvas.itemconfigure(win_id, width=e.width))

        def _wheel(event) -> None:
            scroll_canvas.yview_scroll(int(-event.delta / 120), "units")
        scroll_canvas.bind("<Enter>", lambda _e: scroll_canvas.bind_all("<MouseWheel>", _wheel))
        scroll_canvas.bind("<Leave>", lambda _e: scroll_canvas.unbind_all("<MouseWheel>"))

        ttk.Label(right, text="THIẾT LẬP ẢNH MẪU", style="Title.TLabel").pack(anchor="w")
        ttk.Label(right, text="Chỉ cần làm 1 lần duy nhất", style="Muted.TLabel").pack(
            anchor="w", pady=(0, 10))

        ttk.Button(right, text="📷  Chụp màn hình Game", style="Blue.TButton",
                   command=self.capture_screen).pack(fill=tk.X, padx=(0, 8))
        ttk.Button(right, text="📂  Mở ảnh từ máy (dự phòng)", style="Ghost.TButton",
                   command=self.open_image_file).pack(fill=tk.X, pady=(6, 0), padx=(0, 8))

        ttk.Separator(right).pack(fill=tk.X, pady=10, padx=(0, 8))

        ttk.Label(right, text="Chọn thành phần cần lưu:").pack(anchor="w")
        self.label_var = tk.StringVar(value=list(LABEL_TO_FILENAME.keys())[0])
        self.combo = ttk.Combobox(right, textvariable=self.label_var, state="readonly",
                                  values=list(LABEL_TO_FILENAME.keys()), width=50)
        self.combo.pack(fill=tk.X, pady=(4, 0), padx=(0, 8))
        self.combo.bind("<<ComboboxSelected>>", lambda _e: self._update_filename_hint())

        # Cấp Tường: SỐ NGUYÊN BẤT KỲ (không cố định), chỉ áp dụng cho các nhãn Tường (dòng đề xuất)
        self.level_var = tk.StringVar(value="")
        level_row = tk.Frame(right, bg=PANEL)
        level_row.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(level_row, text="Cấp Tường (số bất kỳ):").pack(side=tk.LEFT)
        self.level_entry = tk.Entry(level_row, textvariable=self.level_var, width=8,
                                    bg="#33364a", fg=FG, insertbackground=FG,
                                    relief="flat", justify="center")
        self.level_entry.pack(side=tk.LEFT, padx=(8, 0), ipady=3)
        self.level_var.trace_add("write", lambda *_a: self._update_filename_hint())

        self.filename_var = tk.StringVar()
        ttk.Label(right, textvariable=self.filename_var, style="Muted.TLabel",
                  wraplength=410, justify="left").pack(anchor="w", pady=(4, 8))

        # Thông tin khung chọn
        self.scale_var = tk.StringVar(value="")
        ttk.Label(right, textvariable=self.scale_var, style="Muted.TLabel",
                  wraplength=410, justify="left").pack(anchor="w")
        self.sel_var = tk.StringVar(value="Chưa khoanh vùng")
        ttk.Label(right, text="Vùng đang chọn:").pack(anchor="w")
        ttk.Label(right, textvariable=self.sel_var, foreground=ACCENT,
                  font=("Consolas", 10, "bold")).pack(anchor="w", pady=(2, 8))

        # Xem trước
        ttk.Label(right, text="Xem trước ảnh cắt (Preview Crop - cắt từ ẢNH GỐC):").pack(anchor="w")
        self.preview = tk.Label(right, bg="#0f1015", width=34, height=7,
                                text="(trống)", fg=MUTED)
        self.preview.pack(fill=tk.X, pady=(4, 4), padx=(0, 8))
        self.preview_photo: Optional[ImageTk.PhotoImage] = None
        self.preview_info = tk.Label(right, text="", bg=PANEL, fg=MUTED, anchor="w",
                                     justify="left", font=("Consolas", 9))
        self.preview_info.pack(fill=tk.X)
        # Ô xác nhận Preview CHỈ hiện khi lưu ROI tài nguyên (xem _toggle_roi_check)
        self.chk_preview = tk.Checkbutton(
            right, text="Đã thấy trọn 3 dòng (Vàng, Dầu, Dầu đen) - cho ROI tài nguyên",
            variable=self.var_preview_ok, bg=PANEL, fg=FG, selectcolor="#33364a",
            activebackground=PANEL, activeforeground=FG, anchor="w", justify="left",
            wraplength=400)
        self._chk_shown = False

        ttk.Separator(right).pack(fill=tk.X, pady=10, padx=(0, 8))

        ttk.Label(right, text="Trạng thái thiết lập (assets/ & config.json):").pack(anchor="w")
        self.status_frame = tk.Frame(right, bg=PANEL)
        self.status_frame.pack(fill=tk.X, pady=(6, 0))
        self.status_labels = {}
        for label in LABEL_TO_FILENAME:
            lbl = tk.Label(self.status_frame, text="", bg=PANEL, fg=MUTED,
                           anchor="w", justify="left", wraplength=400, font=("Segoe UI", 9))
            lbl.pack(fill=tk.X)
            self.status_labels[label] = lbl

        self._update_filename_hint()   # áp dụng hiện/ẩn ô xác nhận Preview theo nhãn đang chọn

    def _toggle_roi_check(self, fname: str) -> None:
        """Chỉ hiện ô tick xác nhận Preview khi nhãn đang chọn là ROI tài nguyên (đỡ chiếm chỗ)."""
        chk = getattr(self, "chk_preview", None)
        if chk is None:
            return
        want = is_roi(fname)
        if want and not self._chk_shown:
            chk.pack(fill=tk.X, pady=(4, 4), after=self.preview_info)
            self._chk_shown = True
        elif not want and self._chk_shown:
            chk.pack_forget()
            self._chk_shown = False

    # ---------------------------------------------------------- Tiện ích UI -- #
    def _update_filename_hint(self) -> None:
        fname = LABEL_TO_FILENAME[self.label_var.get()]
        self._toggle_roi_check(fname)
        if is_anomaly(fname):
            self.filename_var.set(
                f"→ Đã có {len(list_anomaly_files())} ảnh nút luồng lạ. Lần lưu này tạo ảnh mới:\n"
                f"   assets/{ANOMALY_DIR_NAME}/{next_anomaly_filename()}\n"
                "   (không ghi đè ảnh cũ; tự cập nhật ANOMALY_BUTTON_IMAGES trong config.json)")
            return
        if is_roi(fname):
            self.filename_var.set(
                f"→ Sẽ lưu toạ độ vào config.json  [{fname}]\n"
                "   Mẹo: khoanh sát phần chữ số, tránh đè lên biểu tượng Vàng/Dầu.")
            return
        if is_wall_item(fname):
            level = sanitize_wall_level(self.level_var.get())
            n = count_variants(fname)
            lines = [f"→ Đã có {n} ảnh cho nhóm này"
                     + (f" ({wall_level_summary(fname)})" if n else "") + "."]
            if level:
                lines.append(f"   Lần lưu này tạo ảnh MỚI: assets/{next_wall_filename(fname, level)}")
                if level and int(level) not in wall_levels(wall_counterpart(fname)):
                    lines.append(f"   ⚠ Cấp {level} chưa có thẻ {wall_resource_vi(wall_counterpart(fname))} - nhớ chụp cặp.")
            else:
                lines.append("   ⚠ Nhập Cấp Tường (số bất kỳ) vào ô phía trên trước khi lưu.")
            lines.append("   ℹ Chỉ cắt thẻ ĐỦ tài nguyên (chữ giá trắng); giá khác nhau không ảnh hưởng.")
            missing = wall_missing_pair_levels(fname)
            if missing:
                lines.append(f"   ⚠ Thiếu thẻ {wall_resource_vi(wall_counterpart(fname))} ở cấp: "
                             + ", ".join(str(v) for v in missing))
            self.filename_var.set("\n".join(lines))
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
            if is_anomaly(fname):
                n = len(list_anomaly_files())
                done = n > 0
                extra = f" [{n} ảnh]" if n else ""
            elif is_roi(fname):
                done = get_saved_roi(cfg, fname) is not None
            elif is_wall_item(fname):
                n = count_variants(fname)
                done = n > 0
                extra = f" [{n} ảnh: {wall_level_summary(fname)}]" if n else ""
                missing = wall_missing_pair_levels(fname)
                if missing:
                    extra += (f" ⚠ thiếu thẻ {wall_resource_vi(wall_counterpart(fname))} ở cấp "
                              + ", ".join(str(v) for v in missing))
                    done = False
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

        if is_anomaly(filename):
            self._save_anomaly(label)
            return

        if is_roi(filename):
            self._save_roi(label, filename)
            return

        if is_wall_item(filename):
            self._save_wall_item(label, filename)
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

    def _save_wall_item(self, label: str, filename: str) -> None:
        """
        Lưu 1 ảnh dòng đề xuất Tường (làng chính / làng phụ x Vàng / Dầu) thành file MỚI, không ghi đè.
        Cấp Tường (nếu nhập) chỉ gắn vào tên file (_lv12) cho dễ quản lý; bot không phụ thuộc cấp.
        """
        x1, y1, x2, y2 = self.selection
        crop = self.image_bgr[y1:y2, x1:x2]
        if crop.size == 0:
            messagebox.showerror("Lỗi", "Vùng cắt rỗng, hãy khoanh lại.")
            return

        raw_level = self.level_var.get().strip()
        level = sanitize_wall_level(raw_level)
        if not level:
            messagebox.showwarning("Thiếu Cấp Tường",
                                   "Hãy nhập Cấp Tường là một số bất kỳ (vd 12) vào ô 'Cấp Tường'.\n"
                                   "Mỗi cấp cần đủ cặp thẻ Vàng + thẻ Dầu nên cấp là bắt buộc.")
            return

        save_name = next_wall_filename(filename, level)
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

        name_vi = label.split(". ", 1)[-1]
        total = count_variants(filename)
        self._set_message(f"Đã lưu {save_name} (nhóm này có {total} ảnh).", ACCENT)
        messagebox.showinfo("Thành công",
                            f"Lưu thành công {name_vi}!\n\nassets/{save_name}\n"
                            f"Nhóm này hiện có {total} ảnh ({wall_level_summary(filename)}).")
        self._refresh_asset_status()
        self._clear_selection()
        # Cấp này chưa có thẻ còn lại (Vàng <-> Dầu) -> tự chuyển sang nhãn đó, giữ nguyên cấp để chụp cặp
        other = wall_counterpart(filename)
        if int(level) not in wall_levels(other):
            for lb, fn in LABEL_TO_FILENAME.items():
                if fn == other:
                    self.label_var.set(lb)
                    self._set_message(
                        f"Đã lưu {save_name}. Giờ chụp thẻ {wall_resource_vi(other)} cho cấp {level} "
                        f"({lb.split('. ', 1)[-1]}).", ACCENT)
                    break
        self._update_filename_hint()

    def _save_anomaly(self, label: str) -> None:
        """Lưu ảnh nút luồng lạ thành assets/anomaly/anomaly_btn_N.png (N tăng dần) rồi đồng bộ config.json."""
        x1, y1, x2, y2 = self.selection
        crop = self.image_bgr[y1:y2, x1:x2]
        if crop.size == 0:
            messagebox.showerror("Lỗi", "Vùng cắt rỗng, hãy khoanh lại.")
            return

        save_name = next_anomaly_filename()
        target = ANOMALY_DIR / save_name
        try:
            ANOMALY_DIR.mkdir(parents=True, exist_ok=True)
            ok, buf = cv2.imencode(".png", crop)
            if not ok:
                raise RuntimeError("Không mã hoá được ảnh PNG.")
            buf.tofile(str(target))  # an toàn với đường dẫn Unicode trên Windows
        except Exception as exc:  # noqa: BLE001
            self._set_message("Lưu thất bại.", DANGER)
            messagebox.showerror("Lỗi khi lưu", f"Không thể lưu file:\n{exc}")
            return

        cfg_note = ""
        try:
            listed = sync_anomaly_config()
            cfg_note = f"\n\nconfig.json → ANOMALY_BUTTON_IMAGES: {len(listed)} ảnh"
        except Exception as exc:  # noqa: BLE001
            cfg_note = (f"\n\n⚠️ Đã lưu ảnh nhưng KHÔNG cập nhật được ANOMALY_BUTTON_IMAGES "
                        f"trong config.json: {exc}\n(bot vẫn tự quét thư mục assets/anomaly/ khi danh sách trống)")

        name_vi = label.split(". ", 1)[-1]
        self._set_message(f"Đã lưu {save_name} (tổng {len(list_anomaly_files())} ảnh nút luồng lạ).", ACCENT)
        messagebox.showinfo("Thành công",
                            f"Lưu thành công {name_vi}!\n\nassets/{ANOMALY_DIR_NAME}/{save_name}{cfg_note}")
        self._refresh_asset_status()
        self._update_filename_hint()
        self._clear_selection()   # giữ nguyên nhãn để cắt tiếp ảnh kế

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