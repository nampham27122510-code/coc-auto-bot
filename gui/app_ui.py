import queue
import threading
import time
import traceback
import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk
import cv2
from PIL import Image, ImageTk
from config import (
    LDPLAYER_PATH, MIN_GOLD, MIN_ELIXIR, MIN_DARK_ELIXIR,
    load_json_config, save_json_config, get_json_mtime,
)

STRATEGIES = ["Thả quân cơ bản", "Rồng (Dragon)", "Golem + Phù thủy"]
# Kiểu thả quân: key (lưu vào config.json / truyền cho bot) -> nhãn hiển thị
DEPLOY_MODES = {
    "left_corner": "Thả 1 góc bên trái",
    "right_corner": "Thả 1 góc bên phải",
    "left_random": "Thả ngẫu nhiên bên trái",
    "right_random": "Thả ngẫu nhiên bên phải",
    "full_map": "Thả toàn bản đồ",
}
DEFAULT_DEPLOY_MODE = "left_corner"

# Kiểu thả phép (Spell): hiện chỉ có 1 kiểu mặc định, dễ mở rộng sau này
SPELL_MODES = {
    "enemy_center_random": "Ngẫu nhiên giữa nhà địch",
}
DEFAULT_SPELL_MODE = "enemy_center_random"

# Chế độ kiểm tra điều kiện tài nguyên địch: key (lưu config.json) -> nhãn hiển thị
LOOT_MODES = {
    "OR": "HOẶC (Chỉ cần Vàng hoặc Dầu)",
    "AND": "VÀ (Cần cả Vàng và Dầu)",
    "TOTAL": "TỔNG (Tổng Vàng + Dầu)",
}
DEFAULT_LOOT_MODE = "OR"

CARD_TYPES = {"troop": "Lính", "hero": "Tướng", "spell": "Phép"}
SPELL_DROP_MODES = {"evenly": "Thả đều (xoay vòng)", "random": "Thả ngẫu nhiên"}
BOT_SCREEN = (960, 540)   # hệ toạ độ chuẩn của bot
SETUP_WAIT_TEXT = "Đã mở game, chờ người dùng xác nhận cấu hình tọa độ"

MAX_LOG_LINES = 300   # giới hạn dòng log để ô nhật ký không phình to gây đơ UI

# Chế độ gọn (mặc định): Dashboard chỉ hiện lỗi / cảnh báo và vài mốc quan trọng.
# Trạng thái đang làm gì đã có Banner phía trên nên không lặp lại trong nhật ký.
# Bật công tắc "Log chi tiết" để xem toàn bộ.
IMPORTANT_LOG_PREFIXES = ("🚀", "🛑", "⏹", "🔒", "📸", "↩️", "🏁", "✅ Đã nâng", "✅ Đã cào")

# Banner trạng thái tiến trình: level -> (màu nền, màu chữ)
STATUS_STYLES = {
    "idle":   ("#34495e", "#ecf0f1"),   # bot chưa chạy / đã dừng
    "info":   ("#2c3e50", "#ffffff"),   # đang khởi động / thông tin chung
    "home":   ("#1f6aa5", "#ffffff"),   # ở làng chính
    "search": ("#7d3c98", "#ffffff"),   # tìm đối thủ / soi nhà / cào dữ liệu
    "attack": ("#c0392b", "#ffffff"),   # thả quân tấn công
    "wait":   ("#148f77", "#ffffff"),   # chờ kết thúc trận
    "wall":   ("#d35400", "#ffffff"),   # nâng Tường
    "warn":   ("#b7950b", "#ffffff"),   # mất kết nối / bị kẹt / đang phục hồi
    "done":   ("#1e8449", "#ffffff"),   # hoàn tất
}
DEFAULT_STATUS_LEVEL = "info"
IDLE_STATUS_TEXT = "⏸ Bot chưa chạy - bấm ▶ BẮT ĐẦU"

# Màu dòng nhật ký theo mức độ (tag của Text widget)
LOG_TAG_COLORS = {"err": "#ff6b6b", "warn": "#f5b041", "ok": "#58d68d"}
LOG_FONT = ("Consolas", 13)


class CoordPicker(ctk.CTkToplevel):
    """Cửa sổ phụ: hiện ảnh chụp màn hình game, bấm chuột trái để lấy toạ độ (hệ 960x540)."""

    MAX_W, MAX_H = 1000, 640

    def __init__(self, master, image_bgr, on_pick, title="Chọn tọa độ"):
        super().__init__(master)
        self.title(title)
        self._on_pick = on_pick

        h, w = image_bgr.shape[:2]
        self._sx, self._sy = BOT_SCREEN[0] / w, BOT_SCREEN[1] / h   # đổi về hệ 960x540
        self._scale = min(self.MAX_W / w, self.MAX_H / h, 1.0)
        dw, dh = max(int(w * self._scale), 1), max(int(h * self._scale), 1)
        rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        self._photo = ImageTk.PhotoImage(Image.fromarray(rgb).resize((dw, dh)))

        self.lbl = ctk.CTkLabel(self, text="Nhấp chuột trái vào vị trí mong muốn", anchor="w")
        self.lbl.pack(fill="x", padx=10, pady=(8, 2))
        self.canvas = tk.Canvas(self, width=dw, height=dh, highlightthickness=0, bd=0, cursor="crosshair")
        self.canvas.pack(padx=10, pady=(0, 10))
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Button-1>", self._on_click)
        self.resizable(False, False)
        self.after(150, self._grab)

    def _grab(self):
        try:
            self.lift()
            self.focus_force()
            self.grab_set()
        except Exception:
            pass

    def _to_bot(self, ev):
        x = round(ev.x / self._scale * self._sx)
        y = round(ev.y / self._scale * self._sy)
        return max(0, min(BOT_SCREEN[0], x)), max(0, min(BOT_SCREEN[1], y))

    def _on_motion(self, ev):
        x, y = self._to_bot(ev)
        self.lbl.configure(text=f"Tọa độ: ({x}, {y}) - nhấp chuột trái để chọn")

    def _on_click(self, ev):
        x, y = self._to_bot(ev)
        self.canvas.delete("mark")
        r = 8
        self.canvas.create_oval(ev.x - r, ev.y - r, ev.x + r, ev.y + r, outline="#2ecc71", width=2, tags="mark")
        try:
            self._on_pick(x, y)
        finally:
            self.after(150, self.destroy)


class AppUI(ctk.CTk):
    def __init__(self, start_bot_callback, stop_bot_callback):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.start_bot_callback = start_bot_callback
        self.stop_bot_callback = stop_bot_callback
        self.is_running = False
        self._queue = queue.Queue()   # luồng bot -> UI (thread-safe)
        self._save_job = None         # id của after() đang chờ lưu config.json (debounce)
        self._collect_job = None      # id của after() chờ lưu cấu hình Cào dữ liệu (debounce)
        self._last_mtime = get_json_mtime()   # để phát hiện config.json bị sửa từ bên ngoài
        self._manual_job = None       # after() chờ lưu tọa độ thủ công (debounce)
        self._basic_job = None        # after() chờ lưu mốc tài nguyên / LDPlayer / chiến thuật (debounce)
        self.capture_provider = None  # hàm () -> ảnh BGR; main.py gán sau khi kết nối ADB
        self.setup_confirmed = threading.Event()   # người dùng bấm "Xác nhận cấu hình"
        self.card_rows = []           # các dòng cấu hình thẻ quân
        self._log_batch = []          # log chờ ghi: gom theo lô mỗi nhịp poll để cuộn mượt, không giật
        self._status_level = "idle"   # mức độ của Banner hiện tại
        self._status_since = time.time()   # mốc bắt đầu trạng thái hiện tại (đếm giờ trên Banner)
        self._timer_text = None       # chuỗi đếm giờ đang hiển thị (tránh configure lặp)

        self.title("CoC Auto Bot - LDPlayer 9")
        self.geometry("920x640")
        self.minsize(820, 560)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_header()
        self._build_tabs()
        self.after(100, self._poll_queue)
        self.after(1000, self._tick_status_timer)
        self.after(2000, self._sync_from_json)

    # ------------------------------------------------------------ Header
    def _build_header(self):
        header = ctk.CTkFrame(self, corner_radius=10)
        header.pack(fill="x", padx=12, pady=(12, 6))

        self.led = ctk.CTkLabel(header, text="●", font=ctk.CTkFont(size=26), text_color="#e74c3c")
        self.led.pack(side="left", padx=(14, 6), pady=8)
        self.status_label = ctk.CTkLabel(header, text="ĐÃ DỪNG", font=ctk.CTkFont(size=15, weight="bold"))
        self.status_label.pack(side="left")

        self.btn_confirm = ctk.CTkButton(header, text="✅ XÁC NHẬN CẤU HÌNH", width=170, fg_color="#e67e22",
                                         hover_color="#ca6f1e", command=self._on_click_confirm_setup)
        self.lbl_banner = ctk.CTkLabel(self, text=SETUP_WAIT_TEXT, text_color="#f1c40f",
                                       font=ctk.CTkFont(size=13, weight="bold"), anchor="w")

        self.btn_stop = ctk.CTkButton(header, text="⏸ TẠM DỪNG", width=130, fg_color="#c0392b",
                                      hover_color="#a93226", command=self._on_click_stop, state="disabled")
        self.btn_stop.pack(side="right", padx=(6, 14), pady=8)
        self.btn_start = ctk.CTkButton(header, text="▶ BẮT ĐẦU", width=130, fg_color="#27ae60",
                                       hover_color="#1e8449", command=self._on_click_start)
        self.btn_start.pack(side="right", padx=6, pady=8)

    # -------------------------------------------------------------- Tabs
    def _build_tabs(self):
        self.tabs = ctk.CTkTabview(self)
        self.tabs.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self._build_dashboard(self.tabs.add("Dashboard"))
        self._build_game_config(self.tabs.add("Cấu hình Game"))
        self._build_coords(self.tabs.add("Tọa độ & Thẻ quân"))
        self._build_setup(self.tabs.add("Setup LDPlayer"))

    def _stat_card(self, parent, title):
        card = ctk.CTkFrame(parent, corner_radius=10)
        card.pack(side="left", expand=True, fill="x", padx=5)
        ctk.CTkLabel(card, text=title, text_color="#95a5a6").pack(pady=(6, 0))
        value = ctk.CTkLabel(card, text="0", font=ctk.CTkFont(size=22, weight="bold"))
        value.pack(pady=(0, 6))
        return value

    def _build_dashboard(self, tab):
        # --- Banner trạng thái tiến trình (thời gian thực) - nổi bật phía trên Nhật ký ---
        bg, fg = STATUS_STYLES["idle"]
        self.status_banner = ctk.CTkFrame(tab, corner_radius=14, fg_color=bg)
        self.status_banner.pack(fill="x", padx=4, pady=(4, 8))
        self.lbl_status_main = ctk.CTkLabel(
            self.status_banner, text=IDLE_STATUS_TEXT, text_color=fg, anchor="w",
            font=ctk.CTkFont(size=24, weight="bold"))
        self.lbl_status_main.pack(side="left", fill="x", expand=True, padx=(20, 8), pady=16)
        self.lbl_status_timer = ctk.CTkLabel(
            self.status_banner, text="", text_color=fg, anchor="e", width=90,
            font=ctk.CTkFont(size=14))
        self.lbl_status_timer.pack(side="right", padx=(8, 20))

        # --- Số liệu chính (gọn) ---
        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.pack(fill="x", pady=(0, 6))
        self.lbl_battles = self._stat_card(row, "Số trận đã đánh")
        self.lbl_walls = self._stat_card(row, "Tường đã nâng")

        # Dòng đếm ngược nâng Tường: chỉ hiện khi đang bật nâng Tường
        self.lbl_next_wall_status = ctk.CTkLabel(
            tab, text="", font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#95a5a6", anchor="w")

        # --- Nhật ký rút gọn ---
        self.log_header = ctk.CTkFrame(tab, fg_color="transparent")
        self.log_header.pack(fill="x", padx=4)
        ctk.CTkLabel(self.log_header, text="Sự kiện quan trọng", anchor="w").pack(side="left")
        self.var_verbose = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(self.log_header, text="Log chi tiết", variable=self.var_verbose,
                      width=40).pack(side="right")
        self.log_box = ctk.CTkTextbox(tab, state="disabled", wrap="word",
                                      font=ctk.CTkFont(family=LOG_FONT[0], size=LOG_FONT[1]))
        self.log_box.pack(fill="both", expand=True, padx=4, pady=(2, 4))
        for tag, color in LOG_TAG_COLORS.items():
            self.log_box.tag_config(tag, foreground=color)

    def _field(self, parent, label, default, on_change=None):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=6, padx=10)
        ctk.CTkLabel(row, text=label, width=190, anchor="w").pack(side="left")
        entry = ctk.CTkEntry(row, width=260)
        entry.insert(0, str(default))
        entry.pack(side="left")
        if on_change is not None:
            entry.bind("<KeyRelease>", lambda _e: on_change())
        return entry

    def _build_game_config(self, tab):
        # Ưu tiên giá trị đã lưu trong config.json; khóa chưa có thì load_json_config trả về
        # mặc định lấy từ config.py (MIN_GOLD, MIN_ELIXIR, MIN_DARK_ELIXIR...).
        base = load_json_config()
        self.ent_gold = self._field(tab, "Vàng tối thiểu:", base["MIN_GOLD"],
                                    on_change=self._schedule_basic_save)
        self.ent_elixir = self._field(tab, "Dầu tối thiểu:", base["MIN_ELIXIR"],
                                      on_change=self._schedule_basic_save)
        self.ent_dark = self._field(tab, "Dầu đen tối thiểu:", base["MIN_DARK_ELIXIR"],
                                    on_change=self._schedule_basic_save)

        # --- Chế độ kiểm tra điều kiện tài nguyên (LOOT_MATCH_MODE) ---
        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.pack(fill="x", pady=6, padx=10)
        ctk.CTkLabel(row, text="Điều kiện tấn công:", width=190, anchor="w").pack(side="left")
        self.opt_loot_mode = ctk.CTkOptionMenu(
            row, values=list(LOOT_MODES.values()), width=260,
            command=lambda _v: self._schedule_basic_save())
        self.opt_loot_mode.set(LOOT_MODES.get(base.get("LOOT_MATCH_MODE"), LOOT_MODES[DEFAULT_LOOT_MODE]))
        self.opt_loot_mode.pack(side="left")
        ctk.CTkLabel(tab, text="   TỔNG: (Vàng + Dầu) >= (Vàng tối thiểu + Dầu tối thiểu). "
                               "Dầu đen chỉ xét khi mốc > 0.",
                     text_color="#95a5a6", anchor="w").pack(fill="x", padx=12)

        # --- ROI đọc tài nguyên nhà địch: [x1, y1, x2, y2] (hệ 960x540) ---
        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.pack(fill="x", pady=6, padx=10)
        ctk.CTkLabel(row, text="ROI Tài nguyên Địch:", width=190, anchor="w").pack(side="left")
        self.ent_enemy_roi = ctk.CTkEntry(row, width=200, placeholder_text="x1, y1, x2, y2")
        roi = base.get("RESOURCE_ENEMY_ROI")
        if roi:
            self.ent_enemy_roi.insert(0, self._fmt_roi(roi))
        self.ent_enemy_roi.pack(side="left")
        self.ent_enemy_roi.bind("<KeyRelease>", lambda _e: self._schedule_basic_save())
        ctk.CTkButton(row, text="📌 Chọn 2 góc", width=110,
                      command=self._pick_enemy_roi).pack(side="left", padx=(8, 0))

        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.pack(fill="x", pady=6, padx=10)
        ctk.CTkLabel(row, text="Chiến thuật thả quân:", width=190, anchor="w").pack(side="left")
        self.opt_strategy = ctk.CTkOptionMenu(
            row, values=STRATEGIES, width=260,
            command=lambda _v: self._schedule_basic_save())
        strategy = base.get("STRATEGY")
        self.opt_strategy.set(strategy if strategy in STRATEGIES else STRATEGIES[0])
        self.opt_strategy.pack(side="left")

        # --- Kiểu thả quân & thả phép (lưu vào config.json; logic thả sẽ bổ sung sau) ---
        saved = load_json_config()
        deploy_key = saved.get("DEPLOY_MODE")
        if deploy_key not in DEPLOY_MODES:
            deploy_key = DEFAULT_DEPLOY_MODE
        spell_key = saved.get("SPELL_MODE")
        if spell_key not in SPELL_MODES:
            spell_key = DEFAULT_SPELL_MODE

        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.pack(fill="x", pady=6, padx=10)
        ctk.CTkLabel(row, text="Kiểu thả quân:", width=190, anchor="w").pack(side="left")
        self.opt_deploy = ctk.CTkOptionMenu(
            row, values=list(DEPLOY_MODES.values()), width=260,
            command=lambda _v: self._save_deploy_settings())
        self.opt_deploy.set(DEPLOY_MODES[deploy_key])
        self.opt_deploy.pack(side="left")

        self.var_spell = ctk.BooleanVar(value=bool(saved.get("SPELL_ENABLED", True)))
        self.sw_spell = ctk.CTkSwitch(tab, text="Thả phép (Spell)", variable=self.var_spell,
                                      command=self._save_deploy_settings)
        self.sw_spell.pack(anchor="w", padx=12, pady=(6, 0))

        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.pack(fill="x", pady=6, padx=10)
        ctk.CTkLabel(row, text="Kiểu thả phép:", width=190, anchor="w").pack(side="left")
        self.opt_spell = ctk.CTkOptionMenu(
            row, values=list(SPELL_MODES.values()), width=260,
            command=lambda _v: self._save_deploy_settings())
        self.opt_spell.set(SPELL_MODES[spell_key])
        self.opt_spell.pack(side="left")

        # --- Xả tài nguyên vào Tường (đọc/ghi trực tiếp config.json) ---
        json_cfg = load_json_config()
        ctk.CTkLabel(tab, text="Xả tài nguyên vào Tường (tự lưu vào config.json)",
                     font=ctk.CTkFont(size=14, weight="bold"), anchor="w"
                     ).pack(fill="x", padx=12, pady=(16, 2))

        self.var_wall = ctk.BooleanVar(value=json_cfg["ENABLE_WALL_UPGRADE"])
        self.sw_wall = ctk.CTkSwitch(tab, text="Bật tự động nâng Tường",
                                     variable=self.var_wall, command=self._save_wall_settings)
        self.sw_wall.pack(anchor="w", padx=12, pady=6)

        # OCR tài nguyên CHỈ phục vụ nâng Tường: bật -> bot không OCR tài nguyên nhà địch,
        # và chỉ OCR tài nguyên làng nhà khi công tắc nâng Tường đang bật.
        self.var_ocr_wall_only = ctk.BooleanVar(value=bool(json_cfg.get("OCR_ONLY_FOR_WALL", True)))
        self.sw_ocr_wall_only = ctk.CTkSwitch(
            tab, text="Chỉ đọc OCR tài nguyên để nâng Tường (không OCR nhà địch)",
            variable=self.var_ocr_wall_only, command=self._save_wall_settings)
        self.sw_ocr_wall_only.pack(anchor="w", padx=12, pady=(0, 6))

        self.ent_wall_gold = self._field(
            tab, "Ngưỡng Vàng xả tường:", json_cfg["WALL_UPGRADE_GOLD_THRESHOLD"],
            on_change=self._schedule_save)
        self.ent_wall_elixir = self._field(
            tab, "Ngưỡng Dầu xả tường:", json_cfg["WALL_UPGRADE_ELIXIR_THRESHOLD"],
            on_change=self._schedule_save)

        # --- Độ nhạy quét thẻ Tường trong menu Búa (Hybrid Template + OCR) ---
        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.pack(fill="x", pady=6, padx=10)
        ctk.CTkLabel(row, text="Độ nhạy quét Tường:", width=190, anchor="w").pack(side="left")
        self.sld_wall_match = ctk.CTkSlider(
            row, from_=0.45, to=0.85, number_of_steps=40, width=200,
            command=self._on_wall_match_slide)
        self.sld_wall_match.set(json_cfg["WALL_MATCH_THRESHOLD"])
        self.sld_wall_match.pack(side="left")
        self.lbl_wall_match = ctk.CTkLabel(row, text="", width=150, anchor="w")
        self.lbl_wall_match.pack(side="left", padx=(8, 0))
        self._update_wall_match_label(json_cfg["WALL_MATCH_THRESHOLD"])

        # --- Vùng loại trừ chữ số xN ở giữa thẻ Tường (Masking khi khớp card_wall.png) ---
        xn_start, xn_end = json_cfg["WALL_CARD_XN_EXCLUDE_RATIO"]
        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.pack(fill="x", pady=6, padx=10)
        ctk.CTkLabel(row, text="Vùng loại trừ xN Tường (% từ - đến):",
                     width=190, anchor="w", wraplength=185, justify="left").pack(side="left")
        self.ent_xn_start = ctk.CTkEntry(row, width=70, justify="center")
        self.ent_xn_start.insert(0, self._fmt_pct(xn_start))
        self.ent_xn_start.pack(side="left")
        ctk.CTkLabel(row, text="  đến  ").pack(side="left")
        self.ent_xn_end = ctk.CTkEntry(row, width=70, justify="center")
        self.ent_xn_end.insert(0, self._fmt_pct(xn_end))
        self.ent_xn_end.pack(side="left")
        ctk.CTkLabel(row, text="  % chiều rộng thẻ (mặc định 40 - 70)",
                     anchor="w").pack(side="left", padx=(4, 0))
        for _e in (self.ent_xn_start, self.ent_xn_end):
            _e.bind("<KeyRelease>", lambda _ev: self._schedule_save())
            _e.bind("<FocusOut>", lambda _ev: self._save_wall_settings())

        # --- Chế độ Cào dữ liệu (thu ảnh vùng tài nguyên địch để huấn luyện AI OCR) ---
        ctk.CTkLabel(tab, text="Chế độ Cào dữ liệu (tự lưu vào config.json)",
                     font=ctk.CTkFont(size=14, weight="bold"), anchor="w"
                     ).pack(fill="x", padx=12, pady=(16, 2))

        self.var_collect = ctk.BooleanVar(value=bool(json_cfg.get("COLLECT_DATA_MODE", False)))
        self.sw_collect = ctk.CTkSwitch(
            tab, text="Bật Collect Data Mode (chỉ chụp ảnh + Next, không thả quân)",
            variable=self.var_collect, command=self._save_collect_settings)
        self.sw_collect.pack(anchor="w", padx=12, pady=6)

        self.ent_collect_max = self._field(
            tab, "Số nhà cần cào:", json_cfg.get("COLLECT_MAX_SAMPLES", 100),
            on_change=self._schedule_collect_save)

        self.lbl_json_status = ctk.CTkLabel(tab, text="", anchor="w", text_color="#95a5a6")
        self.lbl_json_status.pack(fill="x", padx=12, pady=(6, 0))
        self.lbl_roi_status = ctk.CTkLabel(tab, text="", anchor="w", text_color="#95a5a6")
        self.lbl_roi_status.pack(fill="x", padx=12, pady=(0, 4))
        self.lbl_wall_info = ctk.CTkLabel(tab, text="", anchor="w", text_color="#95a5a6")
        self.lbl_wall_info.pack(fill="x", padx=12, pady=(0, 4))
        self._update_roi_status(json_cfg)
        self._apply_ocr_ui_state()
        self._update_wall_info(json_cfg["ENABLE_WALL_UPGRADE"],
                               json_cfg["WALL_UPGRADE_GOLD_THRESHOLD"],
                               json_cfg["WALL_UPGRADE_ELIXIR_THRESHOLD"])

    # ------------------------------------------- Tab "Tọa độ & Thẻ quân"
    @staticmethod
    def _fmt_point(p):
        return f"{int(p[0])}, {int(p[1])}"

    @staticmethod
    def _parse_point(text):
        """'480, 490' -> [480, 490]; sai định dạng / số âm -> ValueError."""
        parts = [t for t in str(text).replace(";", ",").replace(" ", ",").split(",") if t.strip()]
        if len(parts) != 2:
            raise ValueError("cần đúng 2 số X, Y")
        x, y = int(parts[0]), int(parts[1])
        if x < 0 or y < 0:
            raise ValueError("toạ độ không được âm")
        return [x, y]

    @staticmethod
    def _put(entry, text):
        entry.delete(0, "end")
        entry.insert(0, str(text))

    def _coord_row(self, parent, label, point):
        """Một dòng: nhãn + ô nhập 'X, Y' + nút [📌 Chọn Tọa độ]. Trả về ô nhập."""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=4, padx=10)
        ctk.CTkLabel(row, text=label, width=190, anchor="w").pack(side="left")
        entry = ctk.CTkEntry(row, width=150)
        entry.insert(0, self._fmt_point(point))
        entry.pack(side="left")
        entry.bind("<KeyRelease>", lambda _e: self._schedule_manual_save())
        ctk.CTkButton(row, text="📌 Chọn Tọa độ", width=130,
                      command=lambda: self._pick_coordinate(
                          lambda x, y: self._on_picked(entry, x, y), label)
                      ).pack(side="left", padx=8)
        return entry

    def _on_picked(self, entry, x, y):
        self._put(entry, f"{x}, {y}")
        self._schedule_manual_save()

    def _build_coords(self, tab):
        cfg = load_json_config()
        pos = cfg["MANUAL_POSITIONS"]
        scroll = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        ctk.CTkLabel(scroll, text="Tọa độ thao tác thủ công (hệ 960x540)",
                     font=ctk.CTkFont(size=14, weight="bold"), anchor="w").pack(fill="x", padx=12, pady=(4, 2))
        self.ent_wall_btn_gold = self._coord_row(scroll, "Nút Nâng Tường (Vàng):", pos["WALL_UPGRADE_GOLD_BTN"])
        self.ent_wall_btn_elixir = self._coord_row(scroll, "Nút Nâng Tường (Dầu):", pos["WALL_UPGRADE_ELIXIR_BTN"])
        self.ent_wall_confirm = self._coord_row(scroll, "Nút Xác Nhận Nâng:", pos["WALL_CONFIRM_BTN"])
        self.ent_troop_drop = self._coord_row(scroll, "Tọa độ Thả Lính:", pos["TROOP_DROP_ZONE"])

        # --- Tọa độ DỰ PHÒNG (Tải lại trò chơi / thoát luồng lạ) ---
        # (Các nút xử lý bằng ẢNH MẪU - Chiến đấu, Tìm trận, Tấn công, Về nhà, Búa, Tăng tốc...
        #  được bot tự định vị nên KHÔNG có ô nhập tọa độ ở đây.)
        ctk.CTkLabel(scroll, text="Tọa độ dự phòng (khi thiếu ảnh mẫu)",
                     font=ctk.CTkFont(size=14, weight="bold"), anchor="w").pack(fill="x", padx=12, pady=(14, 2))
        self.ent_reload_pos = self._coord_row(scroll, "Nút Tải Lại Trò Chơi:", pos["RELOAD_GAME_POS"])
        self.ent_safe_click = self._coord_row(scroll, "Tọa Độ Thoát Luồng Lạ (An toàn):", pos["FALLBACK_SAFE_CLICK_POS"])

        # --- Thả phép ---
        spell = cfg["SPELL_CONFIG"]
        ctk.CTkLabel(scroll, text="Thả phép (Spell)", font=ctk.CTkFont(size=14, weight="bold"),
                     anchor="w").pack(fill="x", padx=12, pady=(14, 2))
        row = ctk.CTkFrame(scroll, fg_color="transparent")
        row.pack(fill="x", pady=4, padx=10)
        ctk.CTkLabel(row, text="Chế độ thả phép:", width=190, anchor="w").pack(side="left")
        self.opt_spell_drop = ctk.CTkOptionMenu(
            row, values=list(SPELL_DROP_MODES.values()), width=200,
            command=lambda _v: self._schedule_manual_save())
        self.opt_spell_drop.set(SPELL_DROP_MODES[spell["DROP_MODE"]])
        self.opt_spell_drop.pack(side="left")

        row = ctk.CTkFrame(scroll, fg_color="transparent")
        row.pack(fill="x", pady=4, padx=10)
        ctk.CTkLabel(row, text="Tọa độ Thả Phép:", width=190, anchor="w").pack(side="left")
        self.ent_spell_targets = ctk.CTkEntry(row, width=330)
        self.ent_spell_targets.insert(0, "; ".join(self._fmt_point(p) for p in spell["TARGET_POSITIONS"]))
        self.ent_spell_targets.pack(side="left")
        self.ent_spell_targets.bind("<KeyRelease>", lambda _e: self._schedule_manual_save())
        ctk.CTkButton(row, text="📌 Thêm điểm", width=110,
                      command=lambda: self._pick_coordinate(self._append_spell_target, "Tọa độ Thả Phép")
                      ).pack(side="left", padx=(8, 4))
        ctk.CTkButton(row, text="Xóa hết", width=70, fg_color="#7f8c8d", hover_color="#626d6e",
                      command=lambda: (self._put(self.ent_spell_targets, ""), self._schedule_manual_save())
                      ).pack(side="left")
        ctk.CTkLabel(scroll, text="   Các điểm cách nhau bằng dấu ';' (ví dụ: 405, 170; 610, 195)",
                     text_color="#95a5a6", anchor="w").pack(fill="x", padx=12)

        # --- Danh sách thẻ quân ---
        ctk.CTkLabel(scroll, text="Danh sách Thẻ quân (thứ tự thả từ trên xuống)",
                     font=ctk.CTkFont(size=14, weight="bold"), anchor="w").pack(fill="x", padx=12, pady=(14, 2))
        self.cards_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        self.cards_frame.pack(fill="x", padx=6)
        for card in cfg["DEPLOY_CARDS_CONFIG"]:
            self._add_card_row(card["pos"], card["type"], card["clicks_count"], save=False)
        ctk.CTkButton(scroll, text="➕ Thêm thẻ", width=120, command=self._add_card_row).pack(
            anchor="w", padx=12, pady=8)

    def _append_spell_target(self, x, y):
        cur = self.ent_spell_targets.get().strip().rstrip(";").strip()
        self._put(self.ent_spell_targets, f"{cur}; {x}, {y}" if cur else f"{x}, {y}")
        self._schedule_manual_save()

    def _add_card_row(self, pos=(220, 480), kind="troop", clicks=10, save=True):
        frame = ctk.CTkFrame(self.cards_frame, fg_color="transparent")
        frame.pack(fill="x", pady=2)
        lbl = ctk.CTkLabel(frame, text="", width=60, anchor="w")
        lbl.pack(side="left", padx=(6, 0))
        ent_pos = ctk.CTkEntry(frame, width=100)
        ent_pos.insert(0, self._fmt_point(pos))
        ent_pos.pack(side="left")
        ent_pos.bind("<KeyRelease>", lambda _e: self._schedule_manual_save())
        row = {"frame": frame, "label": lbl, "pos": ent_pos}
        ctk.CTkButton(frame, text="📌", width=36,
                      command=lambda: self._pick_coordinate(
                          lambda x, y: self._on_picked(ent_pos, x, y), "Vị trí thẻ quân")
                      ).pack(side="left", padx=4)
        opt = ctk.CTkOptionMenu(frame, values=list(CARD_TYPES.values()), width=80,
                                command=lambda _v: self._schedule_manual_save())
        opt.set(CARD_TYPES.get(kind, "Lính"))
        opt.pack(side="left", padx=4)
        ctk.CTkLabel(frame, text="Số lần bấm:").pack(side="left", padx=(8, 2))
        ent_clicks = ctk.CTkEntry(frame, width=60)
        ent_clicks.insert(0, str(clicks))
        ent_clicks.pack(side="left")
        ent_clicks.bind("<KeyRelease>", lambda _e: self._schedule_manual_save())
        ctk.CTkButton(frame, text="🗑", width=36, fg_color="#c0392b", hover_color="#a93226",
                      command=lambda: self._remove_card_row(row)).pack(side="left", padx=8)
        row.update({"type": opt, "clicks": ent_clicks})
        self.card_rows.append(row)
        self._renumber_cards()
        if save:
            self._schedule_manual_save()

    def _remove_card_row(self, row):
        if row in self.card_rows:
            self.card_rows.remove(row)
        row["frame"].destroy()
        self._renumber_cards()
        self._schedule_manual_save()

    def _renumber_cards(self):
        for i, row in enumerate(self.card_rows, 1):
            row["label"].configure(text=f"Slot {i}:")

    # ------------------------------------------------- Chọn tọa độ bằng ảnh
    def set_capture_provider(self, provider):
        """main.py gọi sau khi ADB kết nối: provider() -> ảnh BGR (hoặc None)."""
        self.capture_provider = provider

    def _pick_coordinate(self, on_pick, title="Chọn tọa độ"):
        """Chụp màn hình LDPlayer qua ADB, mở cửa sổ phụ để nhấp chọn vị trí."""
        if self.capture_provider is None:
            messagebox.showinfo(
                "Chưa kết nối game",
                "Chưa có kết nối ADB. Hãy bấm ▶ BẮT ĐẦU để mở LDPlayer + game trước, "
                "rồi dùng 📌 Chọn Tọa độ trong lúc chờ xác nhận cấu hình.")
            return
        try:
            img = self.capture_provider()
        except Exception as exc:
            messagebox.showerror("Lỗi chụp màn hình", f"Không chụp được màn hình LDPlayer:\n{exc}")
            return
        if img is None:
            messagebox.showerror("Lỗi chụp màn hình", "ADB không trả về ảnh. Kiểm tra kết nối LDPlayer.")
            return
        CoordPicker(self, img, on_pick, title=title)

    # ---------------------------------------------- Đọc / lưu tọa độ thủ công
    def _collect_manual_settings(self):
        """Đọc toàn bộ ô nhập tọa độ/thẻ/phép -> dict để lưu. Sai định dạng -> ValueError (có mô tả)."""
        try:
            positions = dict(load_json_config()["MANUAL_POSITIONS"])   # giữ khóa lạ
            positions.pop("WALL_UPGRADE_BTN", None)   # khóa cũ (1 nút chung) không còn dùng
            positions["WALL_UPGRADE_GOLD_BTN"] = self._parse_point(self.ent_wall_btn_gold.get())
            positions["WALL_UPGRADE_ELIXIR_BTN"] = self._parse_point(self.ent_wall_btn_elixir.get())
            positions["WALL_CONFIRM_BTN"] = self._parse_point(self.ent_wall_confirm.get())
            positions["TROOP_DROP_ZONE"] = self._parse_point(self.ent_troop_drop.get())
            positions["RELOAD_GAME_POS"] = self._parse_point(self.ent_reload_pos.get())
            positions.pop("CLOSE_POPUP_POS", None)   # khóa cũ (nút X) không còn dùng
            positions["FALLBACK_SAFE_CLICK_POS"] = self._parse_point(self.ent_safe_click.get())
        except ValueError as exc:
            raise ValueError(f"Tọa độ nút/thả lính: {exc}") from exc

        try:
            targets = [self._parse_point(t) for t in self.ent_spell_targets.get().split(";") if t.strip()]
        except ValueError as exc:
            raise ValueError(f"Tọa độ thả phép: {exc}") from exc
        if not targets:
            raise ValueError("Tọa độ thả phép: cần ít nhất 1 điểm")
        drop_mode = self._key_from_label(SPELL_DROP_MODES, self.opt_spell_drop.get(), "evenly")

        cards = []
        for i, row in enumerate(self.card_rows, 1):
            try:
                pos = self._parse_point(row["pos"].get())
                clicks = self._parse_int(row["clicks"].get())
                if clicks <= 0:
                    raise ValueError("số lần bấm phải > 0")
            except ValueError as exc:
                raise ValueError(f"Thẻ Slot {i}: {exc}") from exc
            kind = self._key_from_label(CARD_TYPES, row["type"].get(), "troop")
            cards.append({"slot": i, "pos": pos, "type": kind, "clicks_count": clicks})

        return {
            "MANUAL_POSITIONS": positions,
            "SPELL_CONFIG": {"DROP_MODE": drop_mode, "TARGET_POSITIONS": targets},
            "DEPLOY_CARDS_CONFIG": cards,
        }

    def _schedule_manual_save(self):
        if self._manual_job is not None:
            try:
                self.after_cancel(self._manual_job)
            except Exception:
                pass
        self._manual_job = self.after(600, self._save_manual_settings)

    def _save_manual_settings(self):
        """Lưu tọa độ/thẻ/phép vào config.json. True nếu hợp lệ và đã lưu."""
        self._manual_job = None
        try:
            data = self._collect_manual_settings()
        except ValueError as exc:
            self._set_json_status(f"⚠️ {exc} - CHƯA lưu", "#e67e22")
            return False
        if not save_json_config(data):
            self._set_json_status("❌ Không ghi được config.json (kiểm tra quyền ghi file)", "#e74c3c")
            return False
        self._last_mtime = get_json_mtime()
        self._set_json_status(f"💾 Đã lưu tọa độ & thẻ quân lúc {time.strftime('%H:%M:%S')}", "#2ecc71")
        return True

    # ------------------------------------------- Chờ xác nhận cấu hình (WAITING_SETUP)
    def wait_for_setup_confirm(self):
        """
        Gọi từ luồng bot sau khi game đã mở: hiện thông báo + nút Xác nhận, rồi chặn tới khi
        người dùng xác nhận. Trả về False nếu người dùng bấm Dừng trong lúc chờ.
        """
        self.setup_confirmed.clear()
        self._queue.put(("setup_wait", True))
        while not self.setup_confirmed.is_set():
            if not self.is_running:
                return False
            time.sleep(0.3)
        return True

    def _show_setup_wait(self):
        self.led.configure(text_color="#f39c12")
        self.status_label.configure(text="CHỜ XÁC NHẬN TỌA ĐỘ")
        self.lbl_banner.configure(text=f"⏳ {SETUP_WAIT_TEXT}")
        self._set_banner("⏳ Chờ bạn xác nhận cấu hình tọa độ", "warn")
        self.lbl_banner.pack(fill="x", padx=18, pady=(0, 4), before=self.tabs)
        self.btn_confirm.pack(side="right", padx=6, pady=8, before=self.btn_start)
        try:
            self.tabs.set("Tọa độ & Thẻ quân")
        except Exception:
            pass
        self.log(f"⏸ {SETUP_WAIT_TEXT}. Dùng 📌 Chọn Tọa độ nếu cần, rồi bấm 'XÁC NHẬN CẤU HÌNH'.")

    def _hide_setup_wait(self):
        self.btn_confirm.pack_forget()
        self.lbl_banner.pack_forget()

    def _on_click_confirm_setup(self):
        if not self._save_manual_settings():
            messagebox.showwarning("Cấu hình chưa hợp lệ",
                                   "Hãy sửa các ô tọa độ bị lỗi (xem dòng trạng thái màu cam) rồi xác nhận lại.")
            return
        self._hide_setup_wait()
        self._apply_state(True)
        self._set_banner("🚀 Bot bắt đầu chạy...", "info")
        self.log("✅ Đã xác nhận cấu hình tọa độ -> bot bắt đầu chạy.")
        self.setup_confirmed.set()

    def _build_setup(self, tab):
        base = load_json_config()
        self.ent_path = self._field(tab, "Thư mục LDPlayer:", base["LDPLAYER_PATH"],
                                    on_change=self._schedule_basic_save)
        self.ent_index = self._field(tab, "Index giả lập:", base["LDPLAYER_INDEX"],
                                     on_change=self._schedule_basic_save)

    # ------------------------------------------------ Đồng bộ config.json
    def _set_json_status(self, text, color="#95a5a6"):
        self.lbl_json_status.configure(text=text, text_color=color)

    @staticmethod
    def _key_from_label(mapping, label, default):
        """Đổi nhãn hiển thị về key; không khớp thì dùng mặc định."""
        for key, text in mapping.items():
            if text == label:
                return key
        return default

    def _deploy_settings(self):
        return {
            "DEPLOY_MODE": self._key_from_label(DEPLOY_MODES, self.opt_deploy.get(), DEFAULT_DEPLOY_MODE),
            "SPELL_ENABLED": bool(self.var_spell.get()),
            "SPELL_MODE": self._key_from_label(SPELL_MODES, self.opt_spell.get(), DEFAULT_SPELL_MODE),
        }

    def _save_deploy_settings(self):
        """Lưu kiểu thả quân / phép vào config.json (giữ nguyên các khóa khác)."""
        if not save_json_config(self._deploy_settings()):
            self._set_json_status("❌ Không ghi được config.json (kiểm tra quyền ghi file)", "#e74c3c")
            return False
        self._last_mtime = get_json_mtime()
        self._set_json_status(f"💾 Đã lưu kiểu thả quân lúc {time.strftime('%H:%M:%S')}", "#2ecc71")
        return True

    @staticmethod
    def _parse_int(text):
        """Đổi chuỗi người dùng nhập thành int; chấp nhận 5,000,000 / 5.000.000 / 5 000 000."""
        cleaned = str(text).strip()
        for ch in (",", ".", " ", "_"):
            cleaned = cleaned.replace(ch, "")
        return int(cleaned)

    def _apply_ocr_ui_state(self):
        """
        Khóa/mở các ô nhập theo logic OCR:
          - Nâng Tường TẮT           -> khóa ngưỡng Vàng/Dầu xả tường (không OCR tài nguyên làng nhà).
          - "Chỉ OCR để nâng Tường" BẬT -> khóa mốc Vàng/Dầu/Dầu đen của nhà địch (bot không OCR nhà địch).
        """
        wall_on = bool(self.var_wall.get())
        ocr_wall_only = bool(self.var_ocr_wall_only.get())
        for entry in (self.ent_wall_gold, self.ent_wall_elixir):
            entry.configure(state="normal" if wall_on else "disabled")
        for entry in (self.ent_gold, self.ent_elixir, self.ent_dark):
            entry.configure(state="disabled" if ocr_wall_only else "normal")

    def _ocr_status_text(self, wall_on):
        if not bool(self.var_ocr_wall_only.get()):
            return "OCR tài nguyên: làng nhà + nhà địch"
        if wall_on:
            return "OCR tài nguyên: chỉ làng nhà để nâng Tường"
        return "OCR tài nguyên: TẮT (nâng Tường đang tắt)"

    def _update_wall_info(self, enable, gold, elixir):
        """Hiện thông số xả tường + trạng thái OCR đang áp dụng ngay trên Dashboard."""
        state = "BẬT" if enable else "TẮT"
        self.lbl_wall_info.configure(
            text=f"🧱 Xả tường: {state}  |  Ngưỡng Vàng ≥ {gold:,}  |  Ngưỡng Dầu ≥ {elixir:,}"
                 f"  |  🔎 {self._ocr_status_text(enable)}")

    def _update_roi_status(self, cfg):
        def mark(v):
            return "✅ đã khoanh" if v else "❌ chưa có"
        self.lbl_roi_status.configure(
            text=f"ROI làng nhà: {mark(cfg.get('RESOURCE_MY_VILLAGE_ROI'))}  |  "
                 f"ROI làng địch: {mark(cfg.get('RESOURCE_ENEMY_ROI'))}  "
                 f"(đặt bằng tools/crop_tool.py)")

    @staticmethod
    def _fmt_roi(roi):
        return ", ".join(str(int(v)) for v in roi)

    @staticmethod
    def _parse_roi(text):
        """'18, 48, 170, 150' -> [18, 48, 170, 150]; rỗng -> None; sai -> ValueError."""
        parts = [t for t in str(text).replace(";", ",").replace(" ", ",").split(",") if t.strip()]
        if not parts:
            return None
        if len(parts) != 4:
            raise ValueError("ROI cần đúng 4 số: x1, y1, x2, y2")
        x1, y1, x2, y2 = (int(float(p)) for p in parts)
        if min(x1, y1) < 0 or x2 <= x1 or y2 <= y1:
            raise ValueError("ROI không hợp lệ (cần x2 > x1, y2 > y1, không âm)")
        return [x1, y1, x2, y2]

    def _pick_enemy_roi(self):
        """Chọn ROI bằng 2 lần nhấp trên ảnh chụp: góc TRÊN-TRÁI rồi góc DƯỚI-PHẢI."""
        first = {}

        def on_second(x, y):
            x1, y1 = first["p"]
            lo_x, hi_x, lo_y, hi_y = min(x1, x), max(x1, x), min(y1, y), max(y1, y)
            if hi_x - lo_x < 2 or hi_y - lo_y < 2:
                self._set_json_status("⚠️ ROI quá nhỏ - chưa cập nhật", "#e67e22")
                return
            self._put(self.ent_enemy_roi, self._fmt_roi([lo_x, lo_y, hi_x, hi_y]))
            self._schedule_basic_save()

        def on_first(x, y):
            first["p"] = (x, y)
            self.after(300, lambda: self._pick_coordinate(on_second, "ROI địch: nhấp góc DƯỚI-PHẢI"))

        self._pick_coordinate(on_first, "ROI địch: nhấp góc TRÊN-TRÁI")

    def _schedule_basic_save(self):
        """Gõ xong / chọn menu ~0.6s mới lưu mốc tài nguyên, LDPlayer, Index, Chiến thuật (debounce)."""
        if self._basic_job is not None:
            try:
                self.after_cancel(self._basic_job)
            except Exception:
                pass
        self._basic_job = self.after(600, self._save_basic_settings)

    def _save_basic_settings(self):
        """
        Lưu LDPLAYER_PATH, LDPLAYER_INDEX, MIN_GOLD, MIN_ELIXIR, MIN_DARK_ELIXIR, STRATEGY
        vào config.json (giữ nguyên các khóa khác). True nếu hợp lệ và đã lưu.
        """
        self._basic_job = None
        try:
            gold = self._parse_int(self.ent_gold.get())
            elixir = self._parse_int(self.ent_elixir.get())
            dark = self._parse_int(self.ent_dark.get())
            index = self._parse_int(self.ent_index.get())
            if min(gold, elixir, dark, index) < 0:
                raise ValueError
        except ValueError:
            self._set_json_status("⚠️ Mốc tài nguyên và Index phải là số nguyên không âm - CHƯA lưu", "#e67e22")
            return False
        try:
            enemy_roi = self._parse_roi(self.ent_enemy_roi.get())
        except ValueError as exc:
            self._set_json_status(f"⚠️ {exc} - CHƯA lưu", "#e67e22")
            return False

        ok = save_json_config({
            "LOOT_MATCH_MODE": self._key_from_label(LOOT_MODES, self.opt_loot_mode.get(), DEFAULT_LOOT_MODE),
            "RESOURCE_ENEMY_ROI": enemy_roi,
            "LDPLAYER_PATH": self.ent_path.get().strip() or LDPLAYER_PATH,
            "LDPLAYER_INDEX": index,
            "MIN_GOLD": gold,
            "MIN_ELIXIR": elixir,
            "MIN_DARK_ELIXIR": dark,
            "STRATEGY": self.opt_strategy.get(),
        })
        if not ok:
            self._set_json_status("❌ Không ghi được config.json (kiểm tra quyền ghi file)", "#e74c3c")
            return False

        self._last_mtime = get_json_mtime()
        self._set_json_status(f"💾 Đã lưu thiết lập chung lúc {time.strftime('%H:%M:%S')}", "#2ecc71")
        return True

    def _schedule_collect_save(self):
        """Gõ xong ~0.6s mới lưu số nhà cần cào (debounce)."""
        if self._collect_job is not None:
            try:
                self.after_cancel(self._collect_job)
            except Exception:
                pass
        self._collect_job = self.after(600, self._save_collect_settings)

    def _save_collect_settings(self):
        """Lưu COLLECT_DATA_MODE + COLLECT_MAX_SAMPLES vào config.json. True nếu hợp lệ và đã lưu."""
        self._collect_job = None
        try:
            max_samples = self._parse_int(self.ent_collect_max.get())
            if max_samples <= 0:
                raise ValueError
        except ValueError:
            self._set_json_status("⚠️ Số nhà cần cào phải là số nguyên dương - CHƯA lưu", "#e67e22")
            return False

        ok = save_json_config({
            "COLLECT_DATA_MODE": bool(self.var_collect.get()),
            "COLLECT_MAX_SAMPLES": max_samples,
        })
        if not ok:
            self._set_json_status("❌ Không ghi được config.json (kiểm tra quyền ghi file)", "#e74c3c")
            return False

        self._last_mtime = get_json_mtime()
        state = "BẬT" if self.var_collect.get() else "TẮT"
        self._set_json_status(
            f"💾 Cào dữ liệu: {state}, {max_samples} nhà - đã lưu lúc {time.strftime('%H:%M:%S')}",
            "#2ecc71")
        return True

    def _schedule_save(self):
        """Gõ xong ~0.6s mới lưu (debounce) để không ghi file mỗi phím bấm."""
        if self._save_job is not None:
            try:
                self.after_cancel(self._save_job)
            except Exception:
                pass
        self._save_job = self.after(600, self._save_wall_settings)

    def _save_wall_settings(self):
        """
        Lưu công tắc + ngưỡng xả tường vào config.json (giữ nguyên ROI do crop_tool lưu).
        Trả về True nếu hợp lệ và đã lưu.
        """
        self._save_job = None
        self._apply_ocr_ui_state()
        try:
            gold = self._parse_int(self.ent_wall_gold.get())
            elixir = self._parse_int(self.ent_wall_elixir.get())
            if gold < 0 or elixir < 0:
                raise ValueError
        except ValueError:
            self._set_json_status("⚠️ Ngưỡng xả tường phải là số nguyên không âm - CHƯA lưu", "#e67e22")
            return False

        try:
            xn_ratio = self._xn_ratio_from_entries()
        except ValueError:
            self._set_json_status(
                "⚠️ Vùng xN phải là 2 số 0-100, kết thúc lớn hơn bắt đầu ít nhất 5 - CHƯA lưu", "#e67e22")
            return False

        ok = save_json_config({
            "ENABLE_WALL_UPGRADE": bool(self.var_wall.get()),
            "WALL_UPGRADE_GOLD_THRESHOLD": gold,
            "WALL_UPGRADE_ELIXIR_THRESHOLD": elixir,
            "OCR_ONLY_FOR_WALL": bool(self.var_ocr_wall_only.get()),
            "WALL_MATCH_THRESHOLD": round(float(self.sld_wall_match.get()), 2),
            "WALL_CARD_XN_EXCLUDE_RATIO": xn_ratio,
        })
        if not ok:
            self._set_json_status("❌ Không ghi được config.json (kiểm tra quyền ghi file)", "#e74c3c")
            self.log("[❌ LỖI] Không ghi được config.json!")
            return False

        self._last_mtime = get_json_mtime()
        self._update_wall_info(bool(self.var_wall.get()), gold, elixir)
        self._set_json_status(f"💾 Đã lưu config.json lúc {time.strftime('%H:%M:%S')}", "#2ecc71")
        return True

    def _update_wall_match_label(self, value):
        self.lbl_wall_match.configure(
            text=f"{float(value):.2f}  (nhỏ = nhạy hơn)")

    def _on_wall_match_slide(self, value):
        """Kéo thanh trượt: cập nhật nhãn ngay, ~0.6s sau khi ngừng kéo mới lưu config.json."""
        self._update_wall_match_label(value)
        self._schedule_save()

    # ---- Vùng loại trừ chữ số xN (WALL_CARD_XN_EXCLUDE_RATIO) ----
    _XN_MIN_GAP = 0.05   # bề rộng tối thiểu của vùng bị che (5% chiều rộng thẻ)

    @staticmethod
    def _fmt_pct(ratio):
        """0.40 -> '40' ; 0.425 -> '42.5'."""
        v = round(float(ratio) * 100, 1)
        return str(int(v)) if v == int(v) else str(v)

    def _xn_ratio_from_entries(self):
        """Đọc 2 ô % -> [bắt đầu, kết thúc] dạng float 0..1 (vd [0.4, 0.7]). Sai định dạng -> ValueError."""
        a = float(self.ent_xn_start.get().strip().replace(",", ".").rstrip("%")) / 100.0
        b = float(self.ent_xn_end.get().strip().replace(",", ".").rstrip("%")) / 100.0
        if not (0.0 <= a <= 1.0 and 0.0 <= b <= 1.0) or b - a < self._XN_MIN_GAP - 1e-9:
            raise ValueError("xn range")
        return [round(a, 3), round(b, 3)]

    def _is_focused(self, entry):
        try:
            return self.focus_get() is getattr(entry, "_entry", entry)
        except Exception:
            return False

    def _set_entry(self, entry, value):
        """Cập nhật ô nhập từ file, trừ khi người dùng đang gõ trong ô đó."""
        if self._is_focused(entry):
            return
        state = entry.cget("state")
        entry.configure(state="normal")   # ô đang khóa (disabled) thì không sửa được nội dung
        try:
            entry.delete(0, "end")
            entry.insert(0, str(value))
        finally:
            entry.configure(state=state)

    def _apply_basic_from_cfg(self, cfg):
        """Nạp mốc tài nguyên, LDPlayer, Index, Chiến thuật, kiểu thả quân/phép từ config.json lên UI."""
        if self._basic_job is not None:   # người dùng đang gõ dở -> không ghi đè
            return
        self._set_entry(self.ent_gold, cfg["MIN_GOLD"])
        self._set_entry(self.ent_elixir, cfg["MIN_ELIXIR"])
        self._set_entry(self.ent_dark, cfg["MIN_DARK_ELIXIR"])
        self.opt_loot_mode.set(LOOT_MODES.get(cfg.get("LOOT_MATCH_MODE"), LOOT_MODES[DEFAULT_LOOT_MODE]))
        if not self._is_focused(self.ent_enemy_roi):
            self._set_entry(self.ent_enemy_roi,
                            self._fmt_roi(cfg["RESOURCE_ENEMY_ROI"]) if cfg.get("RESOURCE_ENEMY_ROI") else "")
        self._set_entry(self.ent_path, cfg["LDPLAYER_PATH"])
        self._set_entry(self.ent_index, cfg["LDPLAYER_INDEX"])
        if cfg["STRATEGY"] in STRATEGIES:
            self.opt_strategy.set(cfg["STRATEGY"])
        if cfg["DEPLOY_MODE"] in DEPLOY_MODES:
            self.opt_deploy.set(DEPLOY_MODES[cfg["DEPLOY_MODE"]])
        if cfg["SPELL_MODE"] in SPELL_MODES:
            self.opt_spell.set(SPELL_MODES[cfg["SPELL_MODE"]])
        self.var_spell.set(bool(cfg["SPELL_ENABLED"]))
        xn = cfg.get("WALL_CARD_XN_EXCLUDE_RATIO")
        if xn and self._save_job is None:   # không ghi đè khi đang chờ lưu
            self._set_entry(self.ent_xn_start, self._fmt_pct(xn[0]))
            self._set_entry(self.ent_xn_end, self._fmt_pct(xn[1]))

    def _apply_manual_from_cfg(self, cfg):
        """Nạp tọa độ thủ công, thả phép và danh sách thẻ quân từ config.json lên UI."""
        if self._manual_job is not None:  # có thay đổi chưa kịp lưu -> không ghi đè
            return
        pos = cfg["MANUAL_POSITIONS"]
        self._set_entry(self.ent_wall_btn_gold, self._fmt_point(pos["WALL_UPGRADE_GOLD_BTN"]))
        self._set_entry(self.ent_wall_btn_elixir, self._fmt_point(pos["WALL_UPGRADE_ELIXIR_BTN"]))
        self._set_entry(self.ent_wall_confirm, self._fmt_point(pos["WALL_CONFIRM_BTN"]))
        self._set_entry(self.ent_troop_drop, self._fmt_point(pos["TROOP_DROP_ZONE"]))
        self._set_entry(self.ent_reload_pos, self._fmt_point(pos["RELOAD_GAME_POS"]))
        self._set_entry(self.ent_safe_click, self._fmt_point(pos["FALLBACK_SAFE_CLICK_POS"]))
        spell = cfg["SPELL_CONFIG"]
        self.opt_spell_drop.set(SPELL_DROP_MODES.get(spell["DROP_MODE"], SPELL_DROP_MODES["evenly"]))
        self._set_entry(self.ent_spell_targets,
                        "; ".join(self._fmt_point(p) for p in spell["TARGET_POSITIONS"]))
        self._sync_card_rows(cfg["DEPLOY_CARDS_CONFIG"])

    def _sync_card_rows(self, cards):
        """Dựng lại danh sách thẻ quân nếu khác config.json (bỏ qua khi người dùng đang gõ trong thẻ)."""
        desired = [(tuple(c["pos"]), c["type"], int(c["clicks_count"])) for c in cards]
        try:
            current = [(tuple(self._parse_point(r["pos"].get())),
                        self._key_from_label(CARD_TYPES, r["type"].get(), "troop"),
                        self._parse_int(r["clicks"].get())) for r in self.card_rows]
        except ValueError:
            current = None
        if current == desired:
            return
        if any(self._is_focused(r["pos"]) or self._is_focused(r["clicks"]) for r in self.card_rows):
            return
        for r in list(self.card_rows):
            r["frame"].destroy()
        self.card_rows.clear()
        for c in cards:
            self._add_card_row(c["pos"], c["type"], c["clicks_count"], save=False)

    def _sync_from_json(self):
        """Mỗi 2s kiểm tra config.json; nếu bị sửa từ ngoài (crop_tool...) thì nạp lại lên UI."""
        try:
            mtime = get_json_mtime()
            if mtime != self._last_mtime:
                self._last_mtime = mtime
                cfg = load_json_config(force=True)
                self._apply_basic_from_cfg(cfg)
                self._apply_manual_from_cfg(cfg)
                self.var_wall.set(cfg["ENABLE_WALL_UPGRADE"])
                self.var_ocr_wall_only.set(bool(cfg.get("OCR_ONLY_FOR_WALL", True)))
                self._set_entry(self.ent_wall_gold, cfg["WALL_UPGRADE_GOLD_THRESHOLD"])
                self._set_entry(self.ent_wall_elixir, cfg["WALL_UPGRADE_ELIXIR_THRESHOLD"])
                self.sld_wall_match.set(cfg["WALL_MATCH_THRESHOLD"])
                self._update_wall_match_label(cfg["WALL_MATCH_THRESHOLD"])
                xn_a, xn_b = cfg["WALL_CARD_XN_EXCLUDE_RATIO"]
                self._set_entry(self.ent_xn_start, self._fmt_pct(xn_a))
                self._set_entry(self.ent_xn_end, self._fmt_pct(xn_b))
                self.var_collect.set(bool(cfg.get("COLLECT_DATA_MODE", False)))
                self._set_entry(self.ent_collect_max, cfg.get("COLLECT_MAX_SAMPLES", 100))
                self._update_roi_status(cfg)
                self._apply_ocr_ui_state()
                self._update_wall_info(cfg["ENABLE_WALL_UPGRADE"],
                                       cfg["WALL_UPGRADE_GOLD_THRESHOLD"],
                                       cfg["WALL_UPGRADE_ELIXIR_THRESHOLD"])
                self._set_json_status("🔄 Đã nạp lại config.json (file vừa được cập nhật)")
        except Exception:
            pass
        self.after(2000, self._sync_from_json)

    # ----------------------------------------------------- Public (thread-safe)
    def log(self, message):
        self._queue.put(("log", str(message)))

    def update_stats(self, battles, walls, until_wall=None):
        self._queue.put(("stats", (battles, walls, until_wall)))

    def set_status(self, text, level=DEFAULT_STATUS_LEVEL):
        """Cập nhật Banner trạng thái (an toàn khi gọi từ luồng bot - đi qua Queue)."""
        self._queue.put(("status", {"text": str(text), "level": level}))

    # ---------------------------------------------------------- Queue poller
    @staticmethod
    def _normalize_msg(item):
        """
        Chuẩn hóa tin nhắn từ hàng đợi. Chấp nhận:
          - tuple (kind, data)                    : do AppUI.log / update_stats gửi
          - dict {"type": ..., "content": ...}    : do BotLogic gửi trực tiếp qua ui_queue
        Với "stats", dict {battles, walls, until_wall} được đổi thành tuple (b, w, u).
        """
        if isinstance(item, dict):
            kind, data = item.get("type"), item.get("content")
            if kind == "stats" and isinstance(data, dict):
                until = data.get("until_wall")
                data = (int(data.get("battles", 0)), int(data.get("walls", 0)),
                        None if until is None else int(until))
            return kind, data
        kind, data = item
        return kind, data

    @staticmethod
    def _log_tag(message):
        """Chọn màu cho dòng nhật ký theo nội dung (lỗi / cảnh báo / thành công)."""
        if "❌" in message or "LỖI" in message:
            return "err"
        if "⚠️" in message or "CẢNH BÁO" in message or "🔌" in message or "🐢" in message:
            return "warn"
        if message.startswith(("✅", "🏁", "🏠")) or "✅" in message[:4]:
            return "ok"
        return None

    @classmethod
    def _is_important_log(cls, message):
        """Chế độ gọn: chỉ lỗi, cảnh báo và vài mốc quan trọng."""
        if cls._log_tag(message) in ("err", "warn"):
            return True
        return message.startswith(IMPORTANT_LOG_PREFIXES)

    def _flush_logs(self):
        """
        Ghi cả lô log đã gom trong nhịp poll vào ô nhật ký bằng 1 lần cập nhật.
        Chỉ tự cuộn xuống cuối khi người dùng đang ở cuối (đang kéo lên đọc log cũ thì không giật lại).
        """
        if not self._log_batch:
            return
        batch, self._log_batch = self._log_batch[-MAX_LOG_LINES:], []
        try:
            at_bottom = self.log_box.yview()[1] >= 0.97
        except Exception:
            at_bottom = True
        self.log_box.configure(state="normal")
        for stamp, msg in batch:
            self.log_box.insert("end", f"[{stamp}] {msg}\n", self._log_tag(msg))
        # Cắt bớt log cũ để ô nhật ký không quá nặng
        total = int(self.log_box.index("end-1c").split(".")[0])
        if total > MAX_LOG_LINES:
            self.log_box.delete("1.0", f"{total - MAX_LOG_LINES // 2}.0")
        if at_bottom:
            self.log_box.see("end")
        self.log_box.configure(state="disabled")

    # ---- Banner trạng thái tiến trình ----
    def _set_banner(self, text, level=DEFAULT_STATUS_LEVEL):
        """Đổi chữ + màu Banner và reset đồng hồ đếm thời gian của trạng thái. Chỉ gọi ở luồng GUI."""
        bg, fg = STATUS_STYLES.get(level, STATUS_STYLES[DEFAULT_STATUS_LEVEL])
        self._status_level = level
        self._status_since = time.time()
        self._timer_text = None
        self.status_banner.configure(fg_color=bg)
        self.lbl_status_main.configure(text=text, text_color=fg)
        self.lbl_status_timer.configure(text="", text_color=fg)

    def _apply_status(self, data):
        """Nhận trạng thái từ bot (dict {text, level} hoặc chuỗi). Bỏ qua trạng thái muộn sau khi đã dừng."""
        if not self.is_running:
            return
        if isinstance(data, dict):
            text, level = str(data.get("text", "")), data.get("level", DEFAULT_STATUS_LEVEL)
        else:
            text, level = str(data), DEFAULT_STATUS_LEVEL
        if text:
            self._set_banner(text, level)

    def _tick_status_timer(self):
        """Mỗi giây cập nhật thời gian đã ở trạng thái hiện tại (chỉ khi bot đang chạy)."""
        try:
            if self.is_running:
                sec = int(time.time() - self._status_since)
                text = f"⏱ {sec}s" if sec < 60 else f"⏱ {sec // 60}p{sec % 60:02d}s"
            else:
                text = ""
            if text != self._timer_text:
                self._timer_text = text
                self.lbl_status_timer.configure(text=text)
        except Exception:
            pass
        self.after(1000, self._tick_status_timer)

    def _handle_message(self, kind, data):
        if kind == "log":
            msg = str(data)
            if self.var_verbose.get() or self._is_important_log(msg):
                self._log_batch.append((time.strftime('%H:%M:%S'), msg))
        elif kind == "status":
            self._apply_status(data)
        elif kind == "stats":
            battles, walls, until_wall = data
            self.lbl_battles.configure(text=f"{battles:,}")
            self.lbl_walls.configure(text=f"{walls:,}")
            if until_wall is None:
                self.lbl_next_wall_status.pack_forget()   # tắt nâng Tường -> ẩn hẳn
            else:
                if until_wall == 0:
                    text, color = "🧱 Nâng Tường: sắp tới lượt", "#2ecc71"
                else:
                    text, color = f"🧱 Nâng Tường: còn {until_wall} trận", "#f39c12"
                self.lbl_next_wall_status.configure(text=text, text_color=color)
                if not self.lbl_next_wall_status.winfo_manager():
                    self.lbl_next_wall_status.pack(fill="x", padx=12, pady=(0, 4), before=self.log_header)
        elif kind == "state":
            if not data:
                self._hide_setup_wait()
                if self._status_level != "done":   # đã cào xong dữ liệu thì giữ banner hoàn tất
                    self._set_banner("⏸ Bot đã dừng", "idle")
            self._apply_state(data)
        elif kind == "setup_wait":
            self._show_setup_wait()

    def _poll_queue(self):
        # Xử lý tối đa 200 tin / lượt (100ms) để giao diện luôn mượt; một tin lỗi không làm sập vòng lặp
        for _ in range(200):
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                kind, data = self._normalize_msg(item)
                self._handle_message(kind, data)
            except Exception:
                pass
        try:
            self._flush_logs()   # ghi cả lô log 1 lần / nhịp -> mượt, không đơ GUI
        except Exception:
            pass
        self.after(100, self._poll_queue)

    def _apply_state(self, running):
        self.led.configure(text_color="#2ecc71" if running else "#e74c3c")
        self.status_label.configure(text="ĐANG CHẠY" if running else "ĐÃ DỪNG")
        self.btn_start.configure(state="disabled" if running else "normal")
        self.btn_stop.configure(state="normal" if running else "disabled")

    # ------------------------------------------------------------- Controls
    def _read_config(self):
        return {
            "ld_path": self.ent_path.get().strip(),
            "ld_index": self._parse_int(self.ent_index.get()),
            "min_gold": self._parse_int(self.ent_gold.get()),
            "min_elixir": self._parse_int(self.ent_elixir.get()),
            "min_dark": self._parse_int(self.ent_dark.get()),
            "loot_match_mode": self._key_from_label(LOOT_MODES, self.opt_loot_mode.get(), DEFAULT_LOOT_MODE),
            "strategy": self.opt_strategy.get(),
            # Kiểu thả quân / phép (key, xem DEPLOY_MODES và SPELL_MODES)
            "deploy_mode": self._deploy_settings()["DEPLOY_MODE"],
            "enable_spell": bool(self.var_spell.get()),
            "spell_mode": self._deploy_settings()["SPELL_MODE"],
            # Thông số xả tường (cũng được bot đọc trực tiếp từ config.json)
            "enable_wall": bool(self.var_wall.get()),
            "wall_gold": self._parse_int(self.ent_wall_gold.get()),
            "wall_elixir": self._parse_int(self.ent_wall_elixir.get()),
            "ocr_only_for_wall": bool(self.var_ocr_wall_only.get()),
            # Chế độ Cào dữ liệu (bot cũng đọc trực tiếp từ config.json)
            "collect_data_mode": bool(self.var_collect.get()),
            "collect_max_samples": self._parse_int(self.ent_collect_max.get()),
        }

    def _on_click_start(self):
        if self.is_running:
            return
        try:
            config = self._read_config()
        except ValueError:
            self.log("[❌ LỖI] Mốc tài nguyên, ngưỡng xả tường và Index phải là số nguyên!")
            return
        if not self._save_basic_settings():
            self.log("[❌ LỖI] Chưa lưu được thiết lập chung (LDPlayer, Index, mốc tài nguyên, chiến thuật), bot chưa chạy.")
            return
        if not self._save_wall_settings():
            self.log("[❌ LỖI] Chưa lưu được cấu hình xả tường vào config.json, bot chưa chạy.")
            return
        self._save_deploy_settings()
        if not self._save_manual_settings():
            self.log("[❌ LỖI] Tọa độ / thẻ quân chưa hợp lệ (tab 'Tọa độ & Thẻ quân'), bot chưa chạy.")
            return
        if not self._save_collect_settings():
            self.log("[❌ LỖI] Chưa lưu được cấu hình Cào dữ liệu vào config.json, bot chưa chạy.")
            return
        self.is_running = True
        self._apply_state(True)
        self._set_banner("🚀 Đang khởi động bot (mở LDPlayer & game)...", "info")
        threading.Thread(target=self._worker, args=(config,), daemon=True).start()

    def _worker(self, config):
        try:
            self.start_bot_callback(config)
        except Exception:
            self.log("[❌ LỖI] Bot gặp lỗi:\n" + traceback.format_exc())
        finally:
            self.is_running = False
            self._queue.put(("state", False))

    def _on_click_stop(self):
        """An toàn khi gọi từ bất kỳ luồng nào."""
        self.is_running = False
        self._queue.put(("state", False))
        try:
            self.stop_bot_callback()
        except Exception:
            pass

    def _on_close(self):
        self.is_running = False
        # Hủy các lệnh lưu debounce còn chờ rồi lưu nốt TOÀN BỘ thiết lập; mỗi bước độc lập
        # để một ô nhập sai không làm mất phần còn lại.
        for job_attr in ("_save_job", "_collect_job", "_manual_job", "_basic_job"):
            job = getattr(self, job_attr, None)
            if job is not None:
                try:
                    self.after_cancel(job)
                except Exception:
                    pass
                setattr(self, job_attr, None)
        for save in (self._save_basic_settings, self._save_deploy_settings, self._save_wall_settings,
                     self._save_collect_settings, self._save_manual_settings):
            try:
                save()
            except Exception:
                pass
        self.destroy()