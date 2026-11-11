import queue
import re
import threading
import time
import traceback
import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk
import cv2
from core.updater import check_for_updates, apply_update
from PIL import Image, ImageTk
from config import (
    LDPLAYER_PATH, MIN_GOLD, MIN_ELIXIR, MIN_DARK_ELIXIR,
    load_json_config, save_json_config, get_json_mtime,
)

def check_update_on_startup(root):
    """Hàm chạy ngầm kiểm tra update để không làm đơ giao diện chính"""
    def _worker():
        latest_version, download_url = check_for_updates()
        if latest_version and download_url:
            # Sau khi có kết quả mới hiện popup lên giao diện
            root.after(0, lambda: show_update_dialog(latest_version, download_url))

    def show_update_dialog(version, url):
        confirm = messagebox.askyesno(
            "Phát hiện bản cập nhật mới",
            f"Đã có phiên bản mới ({version}). Bạn có muốn cập nhật ngay không?"
        )
        if confirm:
            apply_update(url)

    # Chạy việc kiểm tra trên 1 luồng riêng ngầm bên dưới
    threading.Thread(target=_worker, daemon=True).start()

DEFAULT_STRATEGY = "Thả quân cơ bản"   # không còn chọn trên UI; vẫn truyền cho bot để tương thích
# Kiểu thả quân: key (lưu vào config.json / truyền cho bot) -> nhãn hiển thị
DEPLOY_MODES = {
    "left_corner": "Thả 1 góc bên trái (1 tọa độ)",
    "right_corner": "Thả 1 góc bên phải (1 tọa độ)",
    "spread_left": "Thả dàn trải bên trái (nhiều tọa độ, chia đều)",
    "spread_right": "Thả dàn trải bên phải (nhiều tọa độ, chia đều)",
    "random_around": "Thả ngẫu nhiên quanh map (nhiều tọa độ)",
}
DEFAULT_DEPLOY_MODE = "left_corner"
SINGLE_POINT_MODES = ("left_corner", "right_corner")   # các kiểu chỉ cần 1 tọa độ
# Tên kiểu thả cũ -> kiểu mới (để config.json cũ vẫn mở được)
DEPLOY_MODE_LEGACY = {"left_random": "random_around", "right_random": "random_around", "full_map": "random_around",
                      "spread": "spread_left"}
SKILL_MODES = {"none": "Không dùng", "immediate": "Dùng ngay khi thả", "delay": "Dùng sau N giây"}
# Thẻ quân LƯỢT 2 (Làng phụ): lượt 2 chỉ thả lại đúng các thẻ này. Tọa độ gợi ý đo từ ảnh chụp màn hình lượt 2 (hệ 960x540)
ROUND2_SLOTS = (1, 8, 9)
ROUND2_FALLBACK_POS = {1: (98, 486), 8: (645, 486), 9: (721, 486)}


def normalize_deploy_mode(key):
    key = DEPLOY_MODE_LEGACY.get(key, key)
    return key if key in DEPLOY_MODES else DEFAULT_DEPLOY_MODE

# Kiểu thả phép (Spell): hiện chỉ có 1 kiểu mặc định, dễ mở rộng sau này
# Chỉ giữ lại để main.py import được; UI không còn mục chọn kiểu thả phép (phép luôn thả một kiểu)
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

# Làng farm: key (lưu config.json -> FARM_VILLAGE, bot đọc trực tiếp) -> nhãn hiển thị
FARM_VILLAGES = {
    "main": "Làng chính",
    "builder": "Làng phụ (Thợ xây)",
}
DEFAULT_FARM_VILLAGE = "main"

CARD_TYPES = {"troop": "Lính", "hero": "Tướng", "spell": "Phép"}
SPELL_DROP_MODES = {"evenly": "Thả đều (xoay vòng)", "random": "Thả ngẫu nhiên"}
BOT_SCREEN = (960, 540)   # hệ toạ độ chuẩn của bot
SETUP_WAIT_TEXT = "Đã mở game, chờ người dùng xác nhận cấu hình tọa độ"

MAX_LOG_LINES = 300   # giới hạn dòng log để ô nhật ký không phình to gây đơ UI

# Chế độ gọn (mặc định): Dashboard chỉ hiện lỗi / cảnh báo và vài mốc quan trọng.
# Trạng thái đang làm gì đã có Banner phía trên nên không lặp lại trong nhật ký.
# Bật công tắc "Log chi tiết" để xem toàn bộ.
IMPORTANT_LOG_PREFIXES = ("🚀", "🛑", "⏹", "🔒", "📸", "↩️", "🏁", "🛠️", "🏘️", "✅ Đã nâng", "✅ Đã cào")

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
IDLE_STATUS_TEXT = "Bot đang dừng  |  Nhấn BẮT ĐẦU để chạy"

# Nhật ký dạng cột: [giờ]  MỨC ĐỘ │ nội dung (bỏ emoji / thẻ [LỖI] thừa trong thông báo gốc của bot)
LOG_LEVEL_LABELS = {"err": "LỖI", "warn": "CẢNH BÁO", "ok": "HOÀN TẤT", None: "THÔNG TIN"}
_LEADING_TAG_RE = re.compile(r"^\s*\[[^\]]{0,24}\]\s*")
_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2190-\u21FF\u2300-\u23FF\u2460-\u27BF\u2B00-\u2BFF\uFE0F\u200d]")
_LEADING_JUNK_RE = re.compile(r"^[^\w(\"']+")


def clean_message(text):
    """Làm sạch thông báo cho Dashboard: bỏ thẻ [..] đầu dòng, emoji, ký tự trang trí thừa."""
    text = _LEADING_TAG_RE.sub("", str(text))
    text = _EMOJI_RE.sub("", text)
    text = _LEADING_JUNK_RE.sub("", text.strip())
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = text.replace(" -> ", " → ")
    return text.strip() or str(text)

# Hiệu ứng "NGUY HIỂM" khi bot đang thả quân (level "attack"): 2 sắc đỏ nhấp nháy luân phiên
DANGER_INTERVAL_MS = 450
DANGER_PALETTE = [
    {"banner": "#7b0f0f", "window": "#1c0505", "header": "#3a0a0a", "border": "#ff1a1a", "led": "#ff0000"},
    {"banner": "#e60000", "window": "#2e0808", "header": "#5c0d0d", "border": "#8b0000", "led": "#5a0000"},
]

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
        check_update_on_startup(self)

        self.start_bot_callback = start_bot_callback
        self.stop_bot_callback = stop_bot_callback
        self.is_running = False
        self._queue = queue.Queue()   # luồng bot -> UI (thread-safe)
        self._save_job = None         # id của after() đang chờ lưu config.json (debounce)
        self._last_mtime = get_json_mtime()   # để phát hiện config.json bị sửa từ bên ngoài
        self._manual_job = None       # after() chờ lưu tọa độ thủ công (debounce)
        self._basic_job = None        # after() chờ lưu mốc tài nguyên / LDPlayer / chiến thuật (debounce)
        self.capture_provider = None  # hàm () -> ảnh BGR; main.py gán sau khi kết nối ADB
        self.setup_confirmed = threading.Event()   # người dùng bấm "Xác nhận cấu hình"
        self.card_rows = []           # các dòng cấu hình thẻ quân
        self._danger_job = None       # after() của hiệu ứng nguy hiểm khi đang thả quân
        self._danger_phase = 0
        self._danger_saved = None     # màu gốc để khôi phục sau hiệu ứng
        self._zone_mode = DEFAULT_DEPLOY_MODE   # kiểu thả đang hiển thị ở ô "Tọa độ thả" của từng thẻ
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
        self._header = header

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
            self.status_banner, text="", text_color=fg, anchor="e", width=150,
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
        ctk.CTkLabel(self.log_header, text="Nhật ký hoạt động", anchor="w",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")
        self.var_verbose = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(self.log_header, text="Hiển thị chi tiết", variable=self.var_verbose,
                      width=40).pack(side="right")
        self.log_box = ctk.CTkTextbox(tab, state="disabled", wrap="word",
                                      font=ctk.CTkFont(family=LOG_FONT[0], size=LOG_FONT[1]))
        self.log_box.pack(fill="both", expand=True, padx=4, pady=(2, 4))
        for tag, color in LOG_TAG_COLORS.items():
            self.log_box.tag_config(tag, foreground=color)

    def _field(self, parent, label, default, on_change=None):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=3, padx=10)
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
        # Tab này dài -> đặt trong khung CUỘN được (lăn chuột) để không bị che nút ở cuối
        tab = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        tab.pack(fill="both", expand=True)

        # --- Chọn làng để farm (FARM_VILLAGE): làng chính hoặc làng phụ (Thợ xây) ---
        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.pack(fill="x", pady=(2, 0), padx=10)
        ctk.CTkLabel(row, text="Làng farm:", width=190, anchor="w",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        farm_key = base.get("FARM_VILLAGE", DEFAULT_FARM_VILLAGE)
        farm_key = farm_key if farm_key in FARM_VILLAGES else DEFAULT_FARM_VILLAGE
        self.opt_farm = ctk.CTkOptionMenu(row, values=list(FARM_VILLAGES.values()), width=220,
                                          command=lambda _v: self._save_farm_village())
        self.opt_farm.set(FARM_VILLAGES[farm_key])
        self.opt_farm.pack(side="left")
        self.lbl_farm_hint = ctk.CTkLabel(tab, text="", text_color="#95a5a6", anchor="w", justify="left",
                                          wraplength=760)
        self.lbl_farm_hint.pack(fill="x", padx=12, pady=(0, 2))
        self.lbl_json_status = ctk.CTkLabel(tab, text="", anchor="w", text_color="#95a5a6")
        self.lbl_json_status.pack(fill="x", padx=12, pady=(0, 2))

        # Các nhóm cài đặt riêng của LÀNG CHÍNH nằm trong khung riêng -> chọn Làng phụ thì ẩn hẳn (đỡ chật, đỡ cuộn)
        self.frm_loot = ctk.CTkFrame(tab, fg_color="transparent")
        self.frm_loot.pack(fill="x")

        self.ent_gold = self._field(self.frm_loot, "Vàng tối thiểu:", base["MIN_GOLD"],
                                    on_change=self._schedule_basic_save)
        self.ent_elixir = self._field(self.frm_loot, "Dầu tối thiểu:", base["MIN_ELIXIR"],
                                      on_change=self._schedule_basic_save)
        self.ent_dark = self._field(self.frm_loot, "Dầu đen tối thiểu:", base["MIN_DARK_ELIXIR"],
                                    on_change=self._schedule_basic_save)

        # --- Chế độ kiểm tra điều kiện tài nguyên (LOOT_MATCH_MODE) ---
        row = ctk.CTkFrame(self.frm_loot, fg_color="transparent")
        row.pack(fill="x", pady=3, padx=10)
        ctk.CTkLabel(row, text="Điều kiện tấn công:", width=190, anchor="w").pack(side="left")
        self.opt_loot_mode = ctk.CTkOptionMenu(
            row, values=list(LOOT_MODES.values()), width=260,
            command=lambda _v: self._schedule_basic_save())
        self.opt_loot_mode.set(LOOT_MODES.get(base.get("LOOT_MATCH_MODE"), LOOT_MODES[DEFAULT_LOOT_MODE]))
        self.opt_loot_mode.pack(side="left")
        ctk.CTkLabel(self.frm_loot, text="   TỔNG: (Vàng + Dầu) >= (Vàng tối thiểu + Dầu tối thiểu). "
                               "Dầu đen chỉ xét khi mốc > 0.",
                     text_color="#95a5a6", anchor="w").pack(fill="x", padx=12)

        # --- ROI đọc tài nguyên nhà địch: [x1, y1, x2, y2] (hệ 960x540) ---
        row = ctk.CTkFrame(self.frm_loot, fg_color="transparent")
        row.pack(fill="x", pady=3, padx=10)
        ctk.CTkLabel(row, text="ROI Tài nguyên Địch:", width=190, anchor="w").pack(side="left")
        self.ent_enemy_roi = ctk.CTkEntry(row, width=200, placeholder_text="x1, y1, x2, y2")
        roi = base.get("RESOURCE_ENEMY_ROI")
        if roi:
            self.ent_enemy_roi.insert(0, self._fmt_roi(roi))
        self.ent_enemy_roi.pack(side="left")
        self.ent_enemy_roi.bind("<KeyRelease>", lambda _e: self._schedule_basic_save())
        ctk.CTkButton(row, text="📌 Chọn 2 góc", width=110,
                      command=self._pick_enemy_roi).pack(side="left", padx=(8, 0))
        self.lbl_roi_status = ctk.CTkLabel(self.frm_loot, text="", anchor="w", text_color="#95a5a6")
        self.lbl_roi_status.pack(fill="x", padx=12, pady=(0, 2))

        # --- Thả phép: chỉ có công tắc bật/tắt (phép luôn thả theo tọa độ phép ở tab "Tọa độ & Thẻ quân") ---
        saved = load_json_config()
        self.var_spell = ctk.BooleanVar(value=bool(saved.get("SPELL_ENABLED", True)))
        self.sw_spell = ctk.CTkSwitch(tab, text="Thả phép (Spell)", variable=self.var_spell,
                                      command=self._save_deploy_settings)
        self.sw_spell.pack(anchor="w", padx=12, pady=(10, 6))

        # --- Xả tài nguyên vào Tường (đọc/ghi trực tiếp config.json) ---
        json_cfg = load_json_config()
        self.frm_wall = ctk.CTkFrame(tab, fg_color="transparent")
        self.frm_wall.pack(fill="x")
        ctk.CTkLabel(self.frm_wall, text="Nâng Tường (tự lưu vào config.json)",
                     font=ctk.CTkFont(size=14, weight="bold"), anchor="w"
                     ).pack(fill="x", padx=12, pady=(10, 2))

        self.var_wall = ctk.BooleanVar(value=json_cfg["ENABLE_WALL_UPGRADE"])
        self.sw_wall = ctk.CTkSwitch(self.frm_wall, text="Bật tự động nâng Tường",
                                     variable=self.var_wall, command=self._save_wall_settings)
        self.sw_wall.pack(anchor="w", padx=12, pady=6)

        # Nhóm chỉnh thông số quét thẻ Tường: dùng chung cho Làng chính VÀ Làng phụ (xem _apply_farm_village_ui)
        self.frm_wall_tune = ctk.CTkFrame(tab, fg_color="transparent")
        self.frm_wall_tune.pack(fill="x")

        # --- Độ nhạy quét thẻ Tường trong menu Búa (Hybrid Template + OCR) ---
        row = ctk.CTkFrame(self.frm_wall_tune, fg_color="transparent")
        row.pack(fill="x", pady=3, padx=10)
        ctk.CTkLabel(row, text="Độ nhạy quét Tường:", width=190, anchor="w").pack(side="left")
        self.sld_wall_match = ctk.CTkSlider(
            row, from_=0.45, to=0.95, number_of_steps=50, width=200,
            command=self._on_wall_match_slide)
        self.sld_wall_match.set(json_cfg["WALL_MATCH_THRESHOLD"])
        self.sld_wall_match.pack(side="left")
        self.lbl_wall_match = ctk.CTkLabel(row, text="", width=150, anchor="w")
        self.lbl_wall_match.pack(side="left", padx=(8, 0))
        self._update_wall_match_label(json_cfg["WALL_MATCH_THRESHOLD"])

        # --- Quãng đường vuốt menu Búa khi tìm thẻ Tường (WALL_MENU_SWIPE_MULT) ---
        row = ctk.CTkFrame(self.frm_wall_tune, fg_color="transparent")
        row.pack(fill="x", pady=3, padx=10)
        ctk.CTkLabel(row, text="Quãng đường vuốt menu:", width=190, anchor="w").pack(side="left")
        self.sld_wall_swipe = ctk.CTkSlider(
            row, from_=self._SWIPE_MULT_MIN, to=self._SWIPE_MULT_MAX,
            number_of_steps=int(round((self._SWIPE_MULT_MAX - self._SWIPE_MULT_MIN) / 0.05)),
            width=200, command=self._on_wall_swipe_slide)
        _swipe_mult = self._clamp_swipe_mult(json_cfg.get("WALL_MENU_SWIPE_MULT", 1.5))
        self.sld_wall_swipe.set(_swipe_mult)
        self.sld_wall_swipe.pack(side="left")
        self.lbl_wall_swipe = ctk.CTkLabel(row, text="", width=230, anchor="w")
        self.lbl_wall_swipe.pack(side="left", padx=(8, 0))
        self._update_wall_swipe_label(_swipe_mult)

        # --- Vùng loại trừ chữ số xN ở giữa thẻ Tường (Masking khi khớp card_wall.png) ---
        xn_start, xn_end = json_cfg["WALL_CARD_XN_EXCLUDE_RATIO"]
        row = ctk.CTkFrame(self.frm_wall_tune, fg_color="transparent")
        row.pack(fill="x", pady=3, padx=10)
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

        # --- BỘ ẢNH MẪU LÀM THANG CHẤM ĐIỂM = LÀNG + CẤP + TÀI NGUYÊN ---
        row = ctk.CTkFrame(self.frm_wall_tune, fg_color="transparent")
        row.pack(fill="x", pady=3, padx=10)
        ctk.CTkLabel(row, text="Làng đang nâng Tường:", width=190, anchor="w").pack(side="left")
        self.lbl_wall_village = ctk.CTkLabel(row, text="", anchor="w")
        self.lbl_wall_village.pack(side="left")

        row = ctk.CTkFrame(self.frm_wall_tune, fg_color="transparent")
        row.pack(fill="x", pady=3, padx=10)
        ctk.CTkLabel(row, text="Cấp Tường (0 = mọi cấp):", width=190, anchor="w").pack(side="left")
        self.ent_wall_level = ctk.CTkEntry(row, width=70, justify="center")
        self.ent_wall_level.insert(0, str(self._wall_level_from_cfg(json_cfg)))
        self.ent_wall_level.pack(side="left")
        ctk.CTkLabel(row, text="  chỉ dùng ảnh wall_item_*_lv<N>.png đúng cấp này",
                     text_color="#95a5a6", anchor="w").pack(side="left", padx=(4, 0))
        self.ent_wall_level.bind("<KeyRelease>", lambda _ev: self._schedule_save())
        self.ent_wall_level.bind("<FocusOut>", lambda _ev: self._save_wall_settings())

        row = ctk.CTkFrame(self.frm_wall_tune, fg_color="transparent")
        row.pack(fill="x", pady=3, padx=10)
        ctk.CTkLabel(row, text="Tài nguyên:", width=190, anchor="w").pack(side="left")
        self.opt_wall_res = ctk.CTkOptionMenu(
            row, values=list(self._WALL_RES_LABELS.values()), width=200,
            command=lambda _v: self._save_wall_settings())
        self.opt_wall_res.set(self._WALL_RES_LABELS.get(json_cfg.get("WALL_RESOURCE", "AUTO"), self._WALL_RES_LABELS["AUTO"]))
        self.opt_wall_res.pack(side="left")

        # --- Trọng số 3 thành phần của điểm tổng (Ảnh / Chữ / Giá) ---
        self.sld_w_img, self.lbl_w_img = self._wall_weight_row("Trọng số điểm ẢNH:", json_cfg.get("WALL_WEIGHT_IMAGE", 1.0))
        self.sld_w_txt, self.lbl_w_txt = self._wall_weight_row("Trọng số điểm CHỮ:", json_cfg.get("WALL_WEIGHT_TEXT", 1.0))
        self.sld_w_price, self.lbl_w_price = self._wall_weight_row("Trọng số điểm GIÁ:", json_cfg.get("WALL_WEIGHT_PRICE", 0.6))

        # --- Làng phụ (Thợ xây): CHỈ có 1 công tắc nâng Tường (không có Thả phép / tài nguyên / thông số quét thẻ) ---
        self.frm_builder_wall = ctk.CTkFrame(tab, fg_color="transparent")
        self.var_builder_wall = ctk.BooleanVar(value=bool(json_cfg.get("ENABLE_BUILDER_WALL_UPGRADE", False)))
        self.sw_builder_wall = ctk.CTkSwitch(
            self.frm_builder_wall, text="Bật tự động nâng Tường (Làng thợ xây)",
            variable=self.var_builder_wall, command=self._save_builder_wall_settings)
        self.sw_builder_wall.pack(anchor="w", padx=12, pady=(10, 2))
        ctk.CTkLabel(self.frm_builder_wall,
                     text="Cứ đủ 5 trận: nhận thưởng trước, rồi mới nâng Tường (tự lưu vào config.json)",
                     text_color="#95a5a6", anchor="w", justify="left").pack(fill="x", padx=12, pady=(0, 6))

        self._update_roi_status(json_cfg)
        self._apply_farm_village_ui(farm_key)   # làng phụ: ẩn các nhóm của làng chính

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

        # --- Kiểu thả quân + tọa độ thả (đổi theo kiểu) ---
        ctk.CTkLabel(scroll, text="Thả quân", font=ctk.CTkFont(size=14, weight="bold"),
                     anchor="w").pack(fill="x", padx=12, pady=(14, 2))
        self._deploy_anchor = scroll
        deploy_key = normalize_deploy_mode(cfg.get("DEPLOY_MODE"))
        self._zone_mode = deploy_key
        self._load_single_points(pos)
        # --- KIỂU THẢ / COMBO THẢ do bạn đặt tên: mỗi kiểu nhớ riêng tọa độ thả, số lần bấm, kiểu thả, kỹ năng Tướng ---
        row = ctk.CTkFrame(scroll, fg_color="transparent")
        row.pack(fill="x", pady=(4, 0), padx=10)
        ctk.CTkLabel(row, text="Kiểu thả đã lưu:", width=190, anchor="w").pack(side="left")
        self.opt_preset = ctk.CTkOptionMenu(row, values=["(chưa có)"], width=220, command=self._on_preset_change)
        self.opt_preset.pack(side="left")
        ctk.CTkButton(row, text="➕ Mới", width=70, command=self._on_preset_new).pack(side="left", padx=(8, 4))
        ctk.CTkButton(row, text="✏ Đổi tên", width=90, command=self._on_preset_rename).pack(side="left", padx=4)
        ctk.CTkButton(row, text="🗑 Xóa", width=70, fg_color="#c0392b", hover_color="#a93226",
                      command=self._on_preset_delete).pack(side="left", padx=4)
        ctk.CTkLabel(scroll, anchor="w", justify="left", text_color="#95a5a6",
                     text=("   Vị trí & loại thẻ quân setup 1 lần (dùng chung). Mỗi kiểu thả chỉ lưu phần tọa độ thả; chỉnh tọa độ nào "
                           "thì tự lưu vào kiểu đang chọn.")).pack(fill="x", padx=12, pady=(0, 4))
        row = ctk.CTkFrame(scroll, fg_color="transparent")
        row.pack(fill="x", pady=4, padx=10)
        ctk.CTkLabel(row, text="Kiểu thả quân:", width=190, anchor="w").pack(side="left")
        self.opt_deploy = ctk.CTkOptionMenu(row, values=list(DEPLOY_MODES.values()), width=330,
                                            command=self._on_deploy_mode_change)
        self.opt_deploy.set(DEPLOY_MODES[deploy_key])
        self.opt_deploy.pack(side="left")
        # 1 tọa độ DUY NHẤT cho mọi thẻ (chỉ hiện khi chọn Thả 1 góc trái / phải)
        self.single_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        self.lbl_single = ctk.CTkLabel(self.single_frame, text="", width=190, anchor="w")
        self.lbl_single.pack(side="left")
        self.ent_single = ctk.CTkEntry(self.single_frame, width=150)
        self.ent_single.pack(side="left")
        self.ent_single.bind("<KeyRelease>", lambda _e: self._schedule_manual_save())
        ctk.CTkButton(self.single_frame, text="📌 Chọn Tọa độ", width=130,
                      command=lambda: self._pick_coordinate(self._on_single_picked, "Tọa độ thả quân")
                      ).pack(side="left", padx=8)

        # --- Tọa độ DỰ PHÒNG (Tải lại trò chơi / thoát luồng lạ) ---
        # (Các nút xử lý bằng ẢNH MẪU - Chiến đấu, Tìm trận, Tấn công, Về nhà, Búa, Tăng tốc...
        #  được bot tự định vị nên KHÔNG có ô nhập tọa độ ở đây.)
        ctk.CTkLabel(scroll, text="Tọa độ dự phòng (khi thiếu ảnh mẫu)",
                     font=ctk.CTkFont(size=14, weight="bold"), anchor="w").pack(fill="x", padx=12, pady=(14, 2))
        self.ent_reload_pos = self._coord_row(scroll, "Nút Tải Lại Trò Chơi:", pos["RELOAD_GAME_POS"])
        self.ent_safe_click = self._coord_row(scroll, "Tọa Độ Thoát Luồng Lạ (An toàn):", pos["FALLBACK_SAFE_CLICK_POS"])

        # --- Thả phép (chỉ dành cho Làng chính; Làng phụ không có phép -> ẩn) ---
        spell = cfg["SPELL_CONFIG"]
        self.frm_spell_coords = ctk.CTkFrame(scroll, fg_color="transparent")
        self.frm_spell_coords.pack(fill="x")
        ctk.CTkLabel(self.frm_spell_coords, text="Thả phép (Spell)", font=ctk.CTkFont(size=14, weight="bold"),
                     anchor="w").pack(fill="x", padx=12, pady=(14, 2))
        row = ctk.CTkFrame(self.frm_spell_coords, fg_color="transparent")
        row.pack(fill="x", pady=4, padx=10)
        ctk.CTkLabel(row, text="Chế độ thả phép:", width=190, anchor="w").pack(side="left")
        self.opt_spell_drop = ctk.CTkOptionMenu(
            row, values=list(SPELL_DROP_MODES.values()), width=200,
            command=lambda _v: self._schedule_manual_save())
        self.opt_spell_drop.set(SPELL_DROP_MODES[spell["DROP_MODE"]])
        self.opt_spell_drop.pack(side="left")

        row = ctk.CTkFrame(self.frm_spell_coords, fg_color="transparent")
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
        ctk.CTkLabel(self.frm_spell_coords, text="   Các điểm cách nhau bằng dấu ';' (ví dụ: 405, 170; 610, 195)",
                     text_color="#95a5a6", anchor="w").pack(fill="x", padx=12)

        # --- Danh sách thẻ quân ---
        self.lbl_cards_title = ctk.CTkLabel(scroll, text="Danh sách Thẻ quân (thứ tự thả từ trên xuống)",
                                            font=ctk.CTkFont(size=14, weight="bold"), anchor="w")
        self.lbl_cards_title.pack(fill="x", padx=12, pady=(14, 2))
        self.lbl_cards_hint = ctk.CTkLabel(scroll, justify="left", anchor="w", text_color="#95a5a6", text="")
        self.lbl_cards_hint.pack(fill="x", padx=12)
        self.var_cards_lock = ctk.BooleanVar(value=bool(cfg.get("DEPLOY_CARDS_LOCKED", False)))
        self.sw_cards_lock = ctk.CTkSwitch(
            scroll, text="🔒 Đã setup xong vị trí thẻ - khóa vị trí & loại thẻ (chỉ còn chỉnh tọa độ thả)",
            variable=self.var_cards_lock, command=self._on_cards_lock)
        self.sw_cards_lock.pack(anchor="w", padx=12, pady=(6, 2))
        self.cards_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        self.cards_frame.pack(fill="x", padx=6)
        for card in cfg["DEPLOY_CARDS_CONFIG"]:
            self._add_card_row(card["pos"], card["type"], card["clicks_count"], save=False, extra=card)
        self._refresh_mode_layout()
        self.btn_add_card = ctk.CTkButton(scroll, text="➕ Thêm thẻ", width=120, command=self._add_card_row)
        self.btn_add_card.pack(anchor="w", padx=12, pady=8)
        self._apply_cards_lock()

        # --- Thẻ quân LƯỢT 2 (Làng phụ): danh sách tự chỉnh (thêm / xóa / đổi số thẻ) ---
        self._round2_locked = False
        self._round2_dirty = False
        self.frm_round2 = ctk.CTkFrame(scroll, fg_color="transparent")   # chỉ hiện khi farm Làng phụ
        self.frm_round2.pack(fill="x")
        ctk.CTkLabel(self.frm_round2, text="Thẻ quân LƯỢT 2 (Làng phụ)",
                     font=ctk.CTkFont(size=14, weight="bold"), anchor="w").pack(fill="x", padx=12, pady=(14, 2))
        ctk.CTkLabel(self.frm_round2, justify="left", anchor="w", text_color="#95a5a6", wraplength=720,
                     text=("Lượt 2 chỉ thả đúng các thẻ trong danh sách này. Ô tọa độ để TRỐNG = dùng luôn vị trí thẻ CÙNG SỐ ở danh sách "
                           "thẻ quân phía trên (khỏi nhập lại). Loại thẻ / số lần bấm chọn 'Theo thẻ trên' = lấy theo thẻ cùng số lượt 1; "
                           "Thẻ tiếp viện (8, 9) chỉ xuất hiện sau đợt 1 nên không có thẻ cùng số ở trên: hãy nhập 'Tọa độ thả' riêng cho chúng "
                           "(dùng cho kiểu thả nhiều điểm; kiểu 1 góc vẫn dùng điểm chung). Sửa xong bấm Lưu 1 lần, dùng chung cho cả 2 làng.")
                     ).pack(fill="x", padx=12)
        self.round2_frame = ctk.CTkFrame(self.frm_round2, fg_color="transparent")
        self.round2_frame.pack(fill="x", padx=6)
        self.round2_rows = []
        row = ctk.CTkFrame(self.frm_round2, fg_color="transparent")
        row.pack(fill="x", pady=(4, 10), padx=10)
        self.btn_round2_add = ctk.CTkButton(row, text="➕ Thêm thẻ lượt 2", width=150,
                                            command=lambda: (self._add_round2_row(), self._round2_mark_dirty()))
        self.btn_round2_add.pack(side="left")
        self.btn_round2_save = ctk.CTkButton(row, text="💾 Lưu thẻ lượt 2", width=170, command=self._on_round2_button)
        self.btn_round2_save.pack(side="left", padx=8)
        self.lbl_round2 = ctk.CTkLabel(row, text="", anchor="w", wraplength=420, justify="left")
        self.lbl_round2.pack(side="left", padx=10)
        self._apply_round2_from_cfg(cfg, force=True)
        self._ensure_presets(cfg)
        _fk = cfg.get("FARM_VILLAGE", DEFAULT_FARM_VILLAGE)
        self._apply_coords_village_ui(_fk if _fk in FARM_VILLAGES else DEFAULT_FARM_VILLAGE)

    def _apply_coords_village_ui(self, key):
        """Tab 'Tọa độ & Thẻ quân': chỉ hiện cài đặt của làng đang farm.
        Làng chính -> có Thả phép, ẩn Thẻ lượt 2. Làng phụ -> ẩn Thả phép, hiện Thẻ lượt 2."""
        if not hasattr(self, "frm_round2") or key == getattr(self, "_coords_ui_key", None):
            return
        self._coords_ui_key = key
        if key == "builder":
            self.frm_spell_coords.pack_forget()
            self.frm_round2.pack(fill="x")
        else:
            self.frm_round2.pack_forget()
            self.frm_spell_coords.pack(fill="x", before=self.lbl_cards_title)

    def _append_spell_target(self, x, y):
        cur = self.ent_spell_targets.get().strip().rstrip(";").strip()
        self._put(self.ent_spell_targets, f"{cur}; {x}, {y}" if cur else f"{x}, {y}")
        self._schedule_manual_save()

    # ------------------------------------------- Tọa độ thả 1 điểm (Thả 1 góc trái / phải)
    def _load_single_points(self, pos):
        """MANUAL_POSITIONS -> {left_corner, right_corner}. Trái = TROOP_DROP_ZONE, phải = DEPLOY_POINT_RIGHT (thiếu -> đối xứng)."""
        def center(v):
            v = [int(x) for x in v]
            return [(v[0] + v[2]) // 2, (v[1] + v[3]) // 2] if len(v) >= 4 else v[:2]
        left = center(pos["TROOP_DROP_ZONE"])
        right = pos.get("DEPLOY_POINT_RIGHT")
        right = center(right) if right and len(right) >= 2 else [960 - left[0], left[1]]
        self._single_points = {"left_corner": left, "right_corner": right}

    def _refresh_single_widgets(self):
        mode = self._zone_mode
        if mode not in SINGLE_POINT_MODES:
            return
        side = "TRÁI" if mode == "left_corner" else "PHẢI"
        self.lbl_single.configure(text=f"Tọa độ thả góc {side} (mọi thẻ):")
        self._put(self.ent_single, self._fmt_point(self._single_points[mode]))

    def _commit_single_text(self, mode):
        if mode not in SINGLE_POINT_MODES:
            return
        try:
            self._single_points[mode] = self._parse_point(self.ent_single.get())
        except ValueError:
            pass

    def _on_single_picked(self, x, y):
        self._put(self.ent_single, f"{x}, {y}")
        self._schedule_manual_save()

    def _refresh_mode_layout(self):
        """Hiện đúng ô theo kiểu thả: 1 góc -> 1 ô tọa độ chung; dàn trải / ngẫu nhiên -> tọa độ riêng từng thẻ."""
        single = self._zone_mode in SINGLE_POINT_MODES
        self.single_frame.pack_forget()
        if single:
            self._refresh_single_widgets()
            self.single_frame.pack(fill="x", pady=4, padx=10, before=self.lbl_cards_title)
            self.lbl_cards_hint.configure(
                text=("   Thả 1 góc: dùng 1 tọa độ chung ở trên cho mọi thẻ Lính và Tướng -> chỉ nhập 1 lần.\n"
                      "   Tướng thả 1 lần, có thể dùng kỹ năng ngay hoặc sau N giây (bấm lại thẻ Tướng)."))
        else:
            self.lbl_cards_hint.configure(
                text=("   Kiểu này: mỗi thẻ Lính có NHIỀU tọa độ riêng (thả chia đều / ngẫu nhiên giữa các điểm).\n"
                      "   Thẻ Tướng chỉ chọn 1 tọa độ, thả 1 lần; có thể dùng kỹ năng ngay hoặc sau N giây."))
        for r in self.card_rows:
            self._refresh_zone_widgets(r)
            self._refresh_card_row(r)

    @staticmethod
    def _parse_points(text):
        """'100, 260; 120, 300' -> [[100, 260], [120, 300]] (rỗng -> [])."""
        return [AppUI._parse_point(t) for t in str(text).split(";") if t.strip()]

    def _add_card_row(self, pos=(220, 480), kind="troop", clicks=10, save=True, extra=None):
        extra = extra or {}
        frame = ctk.CTkFrame(self.cards_frame, corner_radius=8)
        frame.pack(fill="x", pady=4)
        row = {"frame": frame,
               "zones": {m: [list(p) for p in pts] for m, pts in (extra.get("zones") or {}).items() if pts}}

        def changed(_v=None):
            self._refresh_zone_widgets(row)
            self._refresh_card_row(row)
            self._schedule_manual_save()

        # --- Dòng 1: vị trí thẻ + loại + số lần bấm
        top = ctk.CTkFrame(frame, fg_color="transparent")
        top.pack(fill="x", pady=(4, 0))
        lbl = ctk.CTkLabel(top, text="", width=60, anchor="w")
        lbl.pack(side="left", padx=(6, 0))
        ent_pos = ctk.CTkEntry(top, width=100)
        ent_pos.insert(0, self._fmt_point(pos))
        ent_pos.pack(side="left")
        ent_pos.bind("<KeyRelease>", lambda _e: self._schedule_manual_save())
        btn_pick = ctk.CTkButton(top, text="📌", width=36,
                                 command=lambda: self._pick_coordinate(
                                     lambda x, y: self._on_picked(ent_pos, x, y), "Vị trí thẻ quân"))
        btn_pick.pack(side="left", padx=4)
        opt = ctk.CTkOptionMenu(top, values=list(CARD_TYPES.values()), width=80, command=changed)
        opt.set(CARD_TYPES.get(kind, "Lính"))
        opt.pack(side="left", padx=4)
        lbl_clicks = ctk.CTkLabel(top, text="Số lần bấm:")
        lbl_clicks.pack(side="left", padx=(8, 2))
        ent_clicks = ctk.CTkEntry(top, width=60)
        ent_clicks.insert(0, str(clicks))
        ent_clicks.pack(side="left")
        ent_clicks.bind("<KeyRelease>", lambda _e: self._schedule_manual_save())
        btn_del = ctk.CTkButton(top, text="🗑", width=36, fg_color="#c0392b", hover_color="#a93226",
                                command=lambda: self._remove_card_row(row))
        btn_del.pack(side="left", padx=8)

        # --- Dòng 2: tọa độ thả riêng của thẻ (theo kiểu thả đang chọn)
        zone_frame = ctk.CTkFrame(frame, fg_color="transparent")
        lbl_zone = ctk.CTkLabel(zone_frame, text="Tọa độ thả:", width=86, anchor="w")
        lbl_zone.pack(side="left", padx=(6, 0))
        ent_zone = ctk.CTkEntry(zone_frame, width=320, placeholder_text="trống = dùng tọa độ thả mặc định")
        ent_zone.pack(side="left")
        ent_zone.bind("<KeyRelease>", lambda _e: self._schedule_manual_save())
        btn_zone = ctk.CTkButton(zone_frame, text="📌 Chọn", width=100,
                                 command=lambda: self._pick_coordinate(
                                     lambda x, y: self._on_zone_picked(row, x, y), "Tọa độ thả của thẻ"))
        btn_zone.pack(side="left", padx=(8, 4))
        ctk.CTkButton(zone_frame, text="Xóa", width=50, fg_color="#7f8c8d", hover_color="#626d6e",
                      command=lambda: (self._put(ent_zone, ""), self._schedule_manual_save())
                      ).pack(side="left")

        # --- Dòng 3 (chỉ Tướng): kỹ năng
        skill_frame = ctk.CTkFrame(frame, fg_color="transparent")
        ctk.CTkLabel(skill_frame, text="Kỹ năng:", width=86, anchor="w").pack(side="left", padx=(6, 0))
        opt_skill = ctk.CTkOptionMenu(skill_frame, values=list(SKILL_MODES.values()), width=170, command=changed)
        opt_skill.set(SKILL_MODES.get(extra.get("skill_mode"), SKILL_MODES["none"]))
        opt_skill.pack(side="left")
        lbl_delay = ctk.CTkLabel(skill_frame, text="sau (giây):")
        ent_delay = ctk.CTkEntry(skill_frame, width=60)
        ent_delay.insert(0, f"{float(extra.get('skill_delay') or 0):g}")
        ent_delay.bind("<KeyRelease>", lambda _e: self._schedule_manual_save())
        lbl_skhint = ctk.CTkLabel(skill_frame, text="(bấm lại đúng vị trí thẻ Tướng ở trên)", text_color="#95a5a6")

        row.update({"label": lbl, "pos": ent_pos, "type": opt, "clicks": ent_clicks, "lbl_clicks": lbl_clicks,
                    "btn_pick": btn_pick, "btn_del": btn_del,
                    "zone_frame": zone_frame, "zone_entry": ent_zone, "zone_btn": btn_zone,
                    "skill_frame": skill_frame, "skill_mode": opt_skill,
                    "lbl_delay": lbl_delay, "skill_delay": ent_delay, "lbl_skhint": lbl_skhint})
        self.card_rows.append(row)
        self._refresh_zone_widgets(row)
        self._refresh_card_row(row)
        self._lock_card_row(row, self._cards_locked())
        self._renumber_cards()
        if save:
            self._schedule_manual_save()

    def _refresh_card_row(self, row):
        """Hiện/ẩn ô theo loại thẻ: Lính/Tướng có tọa độ thả riêng; Tướng thả 1 lần + kỹ năng; Phép dùng tọa độ phép chung."""
        kind = self._key_from_label(CARD_TYPES, row["type"].get(), "troop")
        row["zone_frame"].pack_forget()
        row["skill_frame"].pack_forget()
        if kind in ("troop", "hero") and self._zone_mode not in SINGLE_POINT_MODES:
            row["zone_frame"].pack(fill="x", pady=(2, 0))
        if kind == "hero":
            row["lbl_clicks"].configure(text="Thả 1 lần")
            row["clicks"].configure(state="disabled")
            row["skill_frame"].pack(fill="x", pady=(2, 0))
            mode = self._key_from_label(SKILL_MODES, row["skill_mode"].get(), "none")
            for w in (row["lbl_delay"], row["skill_delay"], row["lbl_skhint"]):
                w.pack_forget()
            if mode == "delay":
                row["lbl_delay"].pack(side="left", padx=(10, 2))
                row["skill_delay"].pack(side="left")
            if mode != "none":
                row["lbl_skhint"].pack(side="left", padx=(10, 0))
        else:
            row["lbl_clicks"].configure(text="Số lần bấm:" if kind == "troop" else "Số bình phép:")
            row["clicks"].configure(state="normal")

    def _refresh_zone_widgets(self, row):
        """Đổ tọa độ của kiểu thả hiện tại vào ô 'Tọa độ thả'. Tướng chỉ có 1 tọa độ."""
        kind = self._key_from_label(CARD_TYPES, row["type"].get(), "troop")
        pts = row["zones"].get(self._zone_mode) or []
        if kind == "hero":
            pts = pts[:1]
        self._put(row["zone_entry"], "; ".join(self._fmt_point(p) for p in pts))
        row["zone_btn"].configure(text="📌 Chọn" if kind == "hero" else "📌 Thêm điểm")

    def _on_zone_picked(self, row, x, y):
        kind = self._key_from_label(CARD_TYPES, row["type"].get(), "troop")
        if kind == "hero":
            self._put(row["zone_entry"], f"{x}, {y}")
        else:
            cur = row["zone_entry"].get().strip().rstrip(";").strip()
            self._put(row["zone_entry"], f"{cur}; {x}, {y}" if cur else f"{x}, {y}")
        self._schedule_manual_save()

    def _commit_zone_text(self, row, mode):
        """Ghi nội dung ô 'Tọa độ thả' vào row['zones'][mode] (chỉ kiểu nhiều điểm; sai định dạng -> giữ giá trị cũ)."""
        if mode in SINGLE_POINT_MODES:
            return
        try:
            pts = self._parse_points(row["zone_entry"].get())
        except ValueError:
            return
        if self._key_from_label(CARD_TYPES, row["type"].get(), "troop") == "hero":
            pts = pts[:1]
        if pts:
            row["zones"][mode] = pts
        else:
            row["zones"].pop(mode, None)

    def _switch_zone_mode(self, new_mode, commit=True):
        """Đổi kiểu thả: cất tọa độ của kiểu cũ rồi hiện ô nhập phù hợp với kiểu mới."""
        if new_mode == self._zone_mode:
            return
        if commit:
            self._commit_single_text(self._zone_mode)
            for r in self.card_rows:
                self._commit_zone_text(r, self._zone_mode)
        self._zone_mode = new_mode
        self._refresh_mode_layout()

    def _on_deploy_mode_change(self, _v=None):
        new_mode = self._key_from_label(DEPLOY_MODES, self.opt_deploy.get(), DEFAULT_DEPLOY_MODE)
        self._switch_zone_mode(new_mode, commit=True)
        self._save_deploy_settings()
        self._schedule_manual_save()

    def _card_from_row(self, row):
        """Đọc 1 thẻ từ UI -> dict để lưu config.json. Sai định dạng -> ValueError."""
        pos = self._parse_point(row["pos"].get())
        kind = self._key_from_label(CARD_TYPES, row["type"].get(), "troop")
        clicks = 1 if kind == "hero" else self._parse_int(row["clicks"].get())
        if clicks <= 0:
            raise ValueError("số lần bấm phải > 0")
        zones = {m: [list(p) for p in pts] for m, pts in row["zones"].items() if pts and m not in SINGLE_POINT_MODES}
        if self._zone_mode not in SINGLE_POINT_MODES:
            cur = self._parse_points(row["zone_entry"].get())
            if cur:
                zones[self._zone_mode] = cur
            else:
                zones.pop(self._zone_mode, None)
        if kind == "hero":
            zones = {m: pts[:1] for m, pts in zones.items()}
        skill_mode, skill_delay = "none", 0.0
        if kind == "hero":
            skill_mode = self._key_from_label(SKILL_MODES, row["skill_mode"].get(), "none")
            if skill_mode == "delay":
                try:
                    skill_delay = float(row["skill_delay"].get().strip().replace(",", "."))
                except ValueError:
                    raise ValueError("thời gian dùng kỹ năng phải là số giây") from None
                if skill_delay < 0:
                    raise ValueError("thời gian dùng kỹ năng không được âm")
        # Kỹ năng Tướng = bấm lại đúng vị trí thẻ Tướng -> không lưu tọa độ kỹ năng riêng (skill_pos luôn rỗng)
        return {"pos": pos, "type": kind, "clicks_count": clicks, "zones": zones,
                "skill_mode": skill_mode, "skill_delay": skill_delay, "skill_pos": []}

    @staticmethod
    def _card_sig(c):
        """Chữ ký chuẩn hóa của 1 thẻ để so sánh UI với config.json."""
        hero = c.get("type") == "hero"
        zsig = tuple(sorted((m, tuple(tuple(int(v) for v in p[:2]) for p in (pts[:1] if hero else pts)))
                            for m, pts in (c.get("zones") or {}).items() if pts and m not in SINGLE_POINT_MODES))
        return (tuple(int(v) for v in c["pos"][:2]), c["type"], 1 if hero else int(c["clicks_count"]), zsig,
                (c.get("skill_mode") or "none") if hero else "none",
                round(float(c.get("skill_delay") or 0), 3) if hero else 0.0)

    def _remove_card_row(self, row):
        if row in self.card_rows:
            self.card_rows.remove(row)
        row["frame"].destroy()
        self._renumber_cards()
        self._schedule_manual_save()

    def _renumber_cards(self):
        for i, row in enumerate(self.card_rows, 1):
            row["label"].configure(text=f"Slot {i}:")

    # ------------------------------------------------- Khóa vị trí thẻ quân (setup 1 lần)
    def _cards_locked(self):
        try:
            return bool(self.var_cards_lock.get())
        except Exception:
            return False

    def _lock_card_row(self, row, locked):
        state = "disabled" if locked else "normal"
        for k in ("pos", "btn_pick", "type", "btn_del"):
            try:
                row[k].configure(state=state)
            except Exception:
                pass

    def _apply_cards_lock(self):
        locked = self._cards_locked()
        for r in self.card_rows:
            self._lock_card_row(r, locked)
        try:
            self.btn_add_card.configure(state="disabled" if locked else "normal")
        except Exception:
            pass

    def _on_cards_lock(self):
        self._apply_cards_lock()
        if not save_json_config({"DEPLOY_CARDS_LOCKED": self._cards_locked()}):
            self._set_json_status("❌ Không ghi được config.json (kiểm tra quyền ghi file)", "#e74c3c")
            return
        self._last_mtime = get_json_mtime()
        self._set_json_status("🔒 Đã khóa vị trí thẻ quân - chỉ chỉnh tọa độ thả" if self._cards_locked()
                              else "🔓 Đã mở khóa vị trí thẻ quân", "#2ecc71")

    # ------------------------------------------------- KIỂU THẢ / COMBO THẢ (đặt tên, lưu nhiều bộ)
    @staticmethod
    def _center_point(v):
        v = [int(x) for x in v]
        return [(v[0] + v[2]) // 2, (v[1] + v[3]) // 2] if len(v) >= 4 else v[:2]

    def _snapshot_from_cfg(self, cfg):
        """Các khóa đang chạy -> 1 kiểu thả (chỉ phần tọa độ thả + số lần bấm + kiểu thả + kỹ năng; KHÔNG gồm vị trí/loại thẻ)."""
        pos = cfg["MANUAL_POSITIONS"]
        left = self._center_point(pos["TROOP_DROP_ZONE"])
        right = pos.get("DEPLOY_POINT_RIGHT")
        right = self._center_point(right) if right and len(right) >= 2 else [960 - left[0], left[1]]
        cards = []
        for i, c in enumerate(cfg["DEPLOY_CARDS_CONFIG"], 1):
            cards.append({"slot": i, "clicks_count": int(c["clicks_count"]),
                          "zones": {m: [list(p) for p in pts] for m, pts in (c.get("zones") or {}).items() if pts},
                          "skill_mode": c.get("skill_mode") or "none",
                          "skill_delay": float(c.get("skill_delay") or 0)})
        sp = cfg["SPELL_CONFIG"]
        return {"mode": normalize_deploy_mode(cfg.get("DEPLOY_MODE")), "cards": cards,
                "singles": {"left_corner": left, "right_corner": right},
                "spell": {"DROP_MODE": sp["DROP_MODE"], "TARGET_POSITIONS": [list(p) for p in sp["TARGET_POSITIONS"]]}}

    @staticmethod
    def _preset_save_dict(preset, cfg):
        """Kiểu thả -> các khóa đang chạy (giữ nguyên vị trí/loại thẻ). Thẻ nào không có trong kiểu thả thì giữ như cũ."""
        cards = [dict(c) for c in cfg["DEPLOY_CARDS_CONFIG"]]
        by_slot = {int(p["slot"]): p for p in preset["cards"]}
        for i, c in enumerate(cards, 1):
            p = by_slot.get(i)
            if not p:
                continue
            c["clicks_count"] = 1 if c["type"] == "hero" else int(p["clicks_count"])
            c["zones"] = {m: [list(q) for q in pts] for m, pts in p["zones"].items()}
            if c["type"] == "hero":
                c["skill_mode"], c["skill_delay"] = p["skill_mode"], p["skill_delay"]
            c["skill_pos"] = []
        pos = dict(cfg["MANUAL_POSITIONS"])
        if preset["singles"].get("left_corner"):
            pos["TROOP_DROP_ZONE"] = list(preset["singles"]["left_corner"])
        if preset["singles"].get("right_corner"):
            pos["DEPLOY_POINT_RIGHT"] = list(preset["singles"]["right_corner"])
        return {"DEPLOY_MODE": preset["mode"], "DEPLOY_CARDS_CONFIG": cards, "MANUAL_POSITIONS": pos,
                "SPELL_CONFIG": {"DROP_MODE": preset["spell"]["DROP_MODE"],
                                 "TARGET_POSITIONS": [list(p) for p in preset["spell"]["TARGET_POSITIONS"]]}}

    def _refresh_preset_menu(self, presets, active):
        names = list(presets) or ["(chưa có)"]
        self.opt_preset.configure(values=names)
        self.opt_preset.set(active if active in presets else names[0])

    def _ensure_presets(self, cfg):
        """Lần đầu (chưa có kiểu thả nào): tạo 'Kiểu thả 1' từ cấu hình đang chạy để không mất tọa độ cũ."""
        presets = dict(cfg.get("DEPLOY_PRESETS") or {})
        active = cfg.get("DEPLOY_PRESET_ACTIVE") or ""
        if active not in presets:
            name, n = "Kiểu thả 1", 1
            while name in presets:
                n += 1
                name = f"Kiểu thả {n}"
            presets[name] = self._snapshot_from_cfg(cfg)
            active = name
            if save_json_config({"DEPLOY_PRESETS": presets, "DEPLOY_PRESET_ACTIVE": active}):
                self._last_mtime = get_json_mtime()
        self._refresh_preset_menu(presets, active)

    def _sync_active_preset(self):
        """Sau mỗi lần lưu tọa độ: ghi cấu hình đang chạy vào kiểu thả đang chọn."""
        try:
            cfg = load_json_config(force=True)
            presets = dict(cfg.get("DEPLOY_PRESETS") or {})
            active = cfg.get("DEPLOY_PRESET_ACTIVE") or ""
            if active not in presets:
                return
            snap = self._snapshot_from_cfg(cfg)
            if presets[active] == snap:
                return
            presets[active] = snap
            if save_json_config({"DEPLOY_PRESETS": presets}):
                self._last_mtime = get_json_mtime()
        except Exception:
            pass

    def _flush_pending_save(self):
        """Lưu nốt chỉnh sửa đang chờ (debounce) trước khi đổi kiểu thả. False nếu dữ liệu đang nhập sai."""
        if self._manual_job is not None:
            try:
                self.after_cancel(self._manual_job)
            except Exception:
                pass
            self._manual_job = None
        return self._save_manual_settings()

    def _reload_deploy_ui(self, cfg):
        """Nạp lại kiểu thả / tọa độ thả / danh sách thẻ lên UI từ cfg (sau khi đổi kiểu thả)."""
        mode = normalize_deploy_mode(cfg.get("DEPLOY_MODE"))
        self.opt_deploy.set(DEPLOY_MODES[mode])
        self._zone_mode = mode
        self._load_single_points(cfg["MANUAL_POSITIONS"])
        spell = cfg["SPELL_CONFIG"]
        self.opt_spell_drop.set(SPELL_DROP_MODES[spell["DROP_MODE"]])
        self._put(self.ent_spell_targets, "; ".join(self._fmt_point(p) for p in spell["TARGET_POSITIONS"]))
        for r in list(self.card_rows):
            r["frame"].destroy()
        self.card_rows.clear()
        for c in cfg["DEPLOY_CARDS_CONFIG"]:
            self._add_card_row(c["pos"], c["type"], c["clicks_count"], save=False, extra=c)
        self._refresh_mode_layout()

    def _activate_preset(self, name):
        cfg = load_json_config(force=True)
        target = (cfg.get("DEPLOY_PRESETS") or {}).get(name)
        if target is None:
            return False
        data = self._preset_save_dict(target, cfg)
        data["DEPLOY_PRESET_ACTIVE"] = name
        if not save_json_config(data):
            self._set_json_status("❌ Không ghi được config.json (kiểm tra quyền ghi file)", "#e74c3c")
            return False
        self._last_mtime = get_json_mtime()
        self._reload_deploy_ui(load_json_config(force=True))
        self._set_json_status(f"🎯 Đang dùng kiểu thả: {name}", "#2ecc71")
        return True

    def _on_preset_change(self, name):
        cfg = load_json_config(force=True)
        presets = cfg.get("DEPLOY_PRESETS") or {}
        active = cfg.get("DEPLOY_PRESET_ACTIVE") or ""
        if name == active or name not in presets:
            self._refresh_preset_menu(presets, active)
            return
        if not self._flush_pending_save():      # đang nhập sai -> không đổi kiểu, tránh mất dữ liệu
            self._refresh_preset_menu(presets, active)
            return
        if not self._activate_preset(name):
            self._refresh_preset_menu(presets, active)

    def _ask_preset_name(self, title, prompt):
        try:
            value = ctk.CTkInputDialog(text=prompt, title=title).get_input()
        except Exception:
            value = None
        return value.strip() if value and value.strip() else None

    def _on_preset_new(self):
        if not self._flush_pending_save():
            return
        name = self._ask_preset_name("Kiểu thả mới", "Đặt tên cho kiểu thả / combo thả mới\n(sao chép từ kiểu đang dùng):")
        if not name:
            return
        cfg = load_json_config(force=True)
        presets = dict(cfg.get("DEPLOY_PRESETS") or {})
        if name in presets:
            messagebox.showwarning("Trùng tên", f"Đã có kiểu thả tên '{name}'.")
            return
        presets[name] = self._snapshot_from_cfg(cfg)
        if save_json_config({"DEPLOY_PRESETS": presets, "DEPLOY_PRESET_ACTIVE": name}):
            self._last_mtime = get_json_mtime()
            self._refresh_preset_menu(presets, name)
            self._set_json_status(f"➕ Đã tạo kiểu thả '{name}' - chỉnh tọa độ rồi tự lưu vào kiểu này", "#2ecc71")

    def _on_preset_rename(self):
        cfg = load_json_config(force=True)
        presets = dict(cfg.get("DEPLOY_PRESETS") or {})
        old = cfg.get("DEPLOY_PRESET_ACTIVE") or ""
        if old not in presets:
            return
        name = self._ask_preset_name("Đổi tên kiểu thả", f"Tên mới cho '{old}':")
        if not name or name == old:
            return
        if name in presets:
            messagebox.showwarning("Trùng tên", f"Đã có kiểu thả tên '{name}'.")
            return
        renamed = {(name if k == old else k): v for k, v in presets.items()}   # giữ nguyên thứ tự
        if save_json_config({"DEPLOY_PRESETS": renamed, "DEPLOY_PRESET_ACTIVE": name}):
            self._last_mtime = get_json_mtime()
            self._refresh_preset_menu(renamed, name)

    def _on_preset_delete(self):
        cfg = load_json_config(force=True)
        presets = dict(cfg.get("DEPLOY_PRESETS") or {})
        active = cfg.get("DEPLOY_PRESET_ACTIVE") or ""
        if active not in presets:
            return
        if len(presets) <= 1:
            messagebox.showinfo("Không xóa được", "Phải giữ lại ít nhất 1 kiểu thả.")
            return
        if not messagebox.askyesno("Xóa kiểu thả", f"Xóa kiểu thả '{active}'?"):
            return
        del presets[active]
        nxt = next(iter(presets))
        if save_json_config({"DEPLOY_PRESETS": presets, "DEPLOY_PRESET_ACTIVE": nxt}):
            self._last_mtime = get_json_mtime()
            self._refresh_preset_menu(presets, nxt)
            self._activate_preset(nxt)

    # ------------------------------------------------- Thẻ quân LƯỢT 2 (danh sách tự chỉnh, Lưu 1 lần)
    _R2_TYPES = {"": "Theo thẻ trên", "troop": "Lính", "hero": "Tướng"}

    def _round2_defaults(self, cfg):
        """Danh sách gợi ý khi chưa lưu: Thẻ 1, 8, 9 - tọa độ lấy theo thẻ cùng số ở danh sách trên, thiếu thì ước lượng."""
        cards = cfg.get("DEPLOY_CARDS_CONFIG") or []
        out = []
        for slot in ROUND2_SLOTS:
            try:
                pos = [int(v) for v in cards[slot - 1]["pos"][:2]]
            except Exception:
                pos = list(ROUND2_FALLBACK_POS[slot])
            out.append({"slot": slot, "pos": pos})
        return out

    def _add_round2_row(self, slot=None, pos=None, kind="", clicks=None, drop=None):
        frame = ctk.CTkFrame(self.round2_frame, corner_radius=8)
        frame.pack(fill="x", pady=3, padx=4)
        line1 = ctk.CTkFrame(frame, fg_color="transparent")
        line1.pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(line1, text="Thẻ số:", width=60, anchor="w").pack(side="left", padx=(6, 0))
        ent_slot = ctk.CTkEntry(line1, width=50, justify="center")
        ent_slot.insert(0, str(slot if slot else len(self.round2_rows) + 1))
        ent_slot.pack(side="left")
        ent_pos = ctk.CTkEntry(line1, width=130, placeholder_text="= theo thẻ cùng số")
        if pos:
            ent_pos.insert(0, self._fmt_point(pos))
        ent_pos.pack(side="left", padx=(10, 0))
        btn = ctk.CTkButton(line1, text="📌", width=36,
                            command=lambda: self._pick_coordinate(
                                lambda x, y: self._on_round2_picked(ent_pos, x, y), "Vị trí thẻ lượt 2"))
        btn.pack(side="left", padx=4)
        opt = ctk.CTkOptionMenu(line1, values=list(self._R2_TYPES.values()), width=120,
                                command=lambda _v: self._round2_mark_dirty())
        opt.set(self._R2_TYPES.get(kind or "", self._R2_TYPES[""]))
        opt.pack(side="left", padx=4)
        ctk.CTkLabel(line1, text="Số lần bấm:").pack(side="left", padx=(8, 2))
        ent_clicks = ctk.CTkEntry(line1, width=60, justify="center", placeholder_text="theo thẻ")
        if clicks:
            ent_clicks.insert(0, str(int(clicks)))
        ent_clicks.pack(side="left")
        row = {"frame": frame, "slot": ent_slot, "pos": ent_pos, "btn": btn, "type": opt, "clicks": ent_clicks}
        btn_del = ctk.CTkButton(line1, text="🗑", width=36, fg_color="#c0392b", hover_color="#a93226",
                                command=lambda: self._remove_round2_row(row))
        btn_del.pack(side="left", padx=8)
        row["del"] = btn_del

        # Dòng 2: điểm thả RIÊNG của thẻ lượt 2 (thẻ tiếp viện 8-9 không có thẻ cùng số ở lượt 1 nên cần nhập ở đây)
        line2 = ctk.CTkFrame(frame, fg_color="transparent")
        line2.pack(fill="x", pady=(2, 4))
        ctk.CTkLabel(line2, text="Tọa độ thả:", width=86, anchor="w").pack(side="left", padx=(6, 0))
        ent_drop = ctk.CTkEntry(line2, width=300, placeholder_text="trống = theo thẻ cùng số lượt 1 / điểm mặc định")
        if drop:
            ent_drop.insert(0, "; ".join(self._fmt_point(p) for p in drop))
        ent_drop.pack(side="left")
        btn_drop = ctk.CTkButton(line2, text="📌 Thêm điểm", width=110,
                                 command=lambda: self._pick_coordinate(
                                     lambda x, y: self._on_round2_drop_picked(ent_drop, x, y), "Điểm thả thẻ lượt 2"))
        btn_drop.pack(side="left", padx=(8, 4))
        ctk.CTkButton(line2, text="Xóa", width=50, fg_color="#7f8c8d", hover_color="#626d6e",
                      command=lambda: (self._put(ent_drop, ""), self._round2_mark_dirty())
                      ).pack(side="left")
        row.update({"drop": ent_drop, "btn_drop": btn_drop})
        for e in (ent_slot, ent_pos, ent_clicks, ent_drop):
            e.bind("<KeyRelease>", lambda _e: self._round2_mark_dirty())
        self.round2_rows.append(row)
        return row

    def _on_round2_drop_picked(self, entry, x, y):
        cur = entry.get().strip().rstrip(";").strip()
        self._put(entry, f"{cur}; {x}, {y}" if cur else f"{x}, {y}")
        self._round2_mark_dirty()

    def _remove_round2_row(self, row):
        if row in self.round2_rows:
            self.round2_rows.remove(row)
        row["frame"].destroy()
        self._round2_mark_dirty()

    def _set_round2_locked(self, locked):
        """Đã lưu -> khóa các ô (tránh sửa nhầm); bấm 🔓 Sửa lại để mở khóa."""
        self._round2_locked = bool(locked)
        state = "disabled" if locked else "normal"
        for r in self.round2_rows:
            for k in ("slot", "pos", "btn", "type", "clicks", "del", "drop", "btn_drop"):
                r[k].configure(state=state)
        self.btn_round2_add.configure(state=state)
        self.btn_round2_save.configure(text="🔓 Sửa lại" if locked else "💾 Lưu thẻ lượt 2")

    def _apply_round2_from_cfg(self, cfg, force=False):
        """Nạp ROUND2_CARDS từ config.json lên UI. Đang sửa dở (chưa Lưu) thì không ghi đè."""
        if not hasattr(self, "round2_frame"):
            return
        if not force and not self._round2_locked and self._round2_dirty:
            return
        self._set_round2_locked(False)
        for r in list(self.round2_rows):
            r["frame"].destroy()
        self.round2_rows.clear()
        saved = cfg.get("ROUND2_CARDS") or []
        if saved:
            for c in saved:
                self._add_round2_row(c["slot"], c.get("pos"), c.get("type", ""), c.get("clicks_count"), c.get("drop"))
        else:
            for c in self._round2_defaults(cfg):
                self._add_round2_row(c["slot"], c["pos"])
        self._round2_dirty = False
        if saved:
            self._set_round2_locked(True)
            self.lbl_round2.configure(text="✅ Đã lưu - lượt 2 dùng đúng các thẻ này. Cần đổi thì bấm 🔓 Sửa lại.",
                                      text_color="#2ecc71")
        else:
            self.lbl_round2.configure(text="Chưa lưu - lượt 2 đang tự nhận ô còn quân. Kiểm tra rồi bấm Lưu (1 lần).",
                                      text_color="#e67e22")

    def _round2_mark_dirty(self):
        self._round2_dirty = True
        self.lbl_round2.configure(text="● Chưa lưu - bấm 💾 Lưu thẻ lượt 2 để áp dụng", text_color="#e67e22")

    def _on_round2_picked(self, entry, x, y):
        self._put(entry, f"{x}, {y}")
        self._round2_mark_dirty()

    def _on_round2_button(self):
        if self._round2_locked:                # 🔓 Sửa lại
            self._set_round2_locked(False)
            self.lbl_round2.configure(text="Đang sửa - đổi xong bấm 💾 Lưu thẻ lượt 2 (1 lần)", text_color="#e67e22")
            return
        self._save_round2_cards()

    def _collect_round2_cards(self):
        main = load_json_config().get("DEPLOY_CARDS_CONFIG") or []
        cards, seen = [], set()
        for i, r in enumerate(self.round2_rows, 1):
            try:
                slot = self._parse_int(r["slot"].get())
            except ValueError:
                raise ValueError(f"dòng {i}: số thẻ phải là số nguyên") from None
            if slot < 1 or slot in seen:
                raise ValueError(f"dòng {i}: số thẻ phải >= 1 và không trùng")
            seen.add(slot)
            card = {"slot": slot}
            txt = r["pos"].get().strip()
            if txt:
                card["pos"] = self._parse_point(txt)
            elif slot > len(main):
                raise ValueError(f"Thẻ {slot}: chưa có thẻ cùng số ở danh sách phía trên -> hãy nhập tọa độ")
            kind = self._key_from_label(self._R2_TYPES, r["type"].get(), "")
            if kind:
                card["type"] = kind
            ctext = r["clicks"].get().strip()
            if ctext:
                try:
                    n = self._parse_int(ctext)
                except ValueError:
                    raise ValueError(f"Thẻ {slot}: số lần bấm phải là số") from None
                if n <= 0:
                    raise ValueError(f"Thẻ {slot}: số lần bấm phải > 0")
                card["clicks_count"] = n
            dtxt = r["drop"].get().strip()
            if dtxt:
                try:
                    card["drop"] = self._parse_points(dtxt)
                except ValueError as exc:
                    raise ValueError(f"Thẻ {slot}: tọa độ thả - {exc}") from None
            cards.append(card)
        return sorted(cards, key=lambda c: c["slot"])

    def _save_round2_cards(self):
        """Lưu ROUND2_CARDS vào config.json ĐÚNG 1 LẦN (không tự lưu khi gõ), đọc lại để chắc chắn đã ghi được. True nếu xong."""
        try:
            cards = self._collect_round2_cards()
        except ValueError as exc:
            self.lbl_round2.configure(text=f"⚠️ Thẻ lượt 2: {exc} - CHƯA lưu", text_color="#e67e22")
            self._set_json_status(f"⚠️ Thẻ lượt 2: {exc} - CHƯA lưu", "#e67e22")
            return False
        if not save_json_config({"ROUND2_CARDS": cards}):
            self._set_json_status("❌ Không ghi được config.json (kiểm tra quyền ghi file)", "#e74c3c")
            self.lbl_round2.configure(text="❌ Không ghi được config.json", text_color="#e74c3c")
            return False
        self._last_mtime = get_json_mtime()
        try:
            back = load_json_config(force=True).get("ROUND2_CARDS")
        except Exception:
            back = None
        if back != cards:
            msg = "❌ Đã ghi nhưng đọc lại không khớp khóa ROUND2_CARDS"
            self._set_json_status(msg, "#e74c3c")
            self.lbl_round2.configure(text=msg, text_color="#e74c3c")
            return False
        self._round2_dirty = False
        stamp = time.strftime("%H:%M:%S")
        if cards:
            self._set_round2_locked(True)
            self.lbl_round2.configure(text=f"✅ Đã lưu lúc {stamp} - các lần sau bot cứ dựa vào đây, không cần lưu lại.",
                                      text_color="#2ecc71")
        else:
            self.lbl_round2.configure(text=f"Đã lưu lúc {stamp}: danh sách trống -> lượt 2 tự nhận ô còn quân.",
                                      text_color="#e67e22")
        self._set_json_status(f"💾 Đã lưu thẻ quân lượt 2 lúc {stamp}", "#2ecc71")
        return True

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
            singles = dict(self._single_points)
            if self._zone_mode in SINGLE_POINT_MODES:
                singles[self._zone_mode] = self._parse_point(self.ent_single.get())
            positions["TROOP_DROP_ZONE"] = singles["left_corner"]
            positions["DEPLOY_POINT_RIGHT"] = singles["right_corner"]
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
                card = self._card_from_row(row)
            except ValueError as exc:
                raise ValueError(f"Thẻ Slot {i}: {exc}") from exc
            card["slot"] = i
            cards.append(card)

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
        self._sync_active_preset()      # tọa độ thả vừa chỉnh -> ghi luôn vào kiểu thả đang chọn
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
            "SPELL_MODE": DEFAULT_SPELL_MODE,
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

    def _apply_farm_village_ui(self, key):
        """Đổi ghi chú theo làng đang chọn; Làng phụ không dùng tài nguyên / Thả phép / nâng Tường làng chính -> ẨN hẳn, chỉ còn công tắc nâng Tường làng phụ."""
        builder = (key == "builder")
        self._apply_coords_village_ui(key)
        self.lbl_farm_hint.configure(text=(
            "Bấm Tấn công → thả quân → Về làng, không xét tài nguyên. Đang ở Làng chính thì tự tìm thuyền (ảnh mục 27)."
            if builder else "Tìm trận 1700, soi nhà địch, thả quân, nâng Tường theo lịch."))
        self._refresh_wall_village_widgets(key)
        if key == getattr(self, "_farm_ui_key", None):
            return                       # không đổi -> không xếp lại giao diện (tránh nháy mỗi lần đồng bộ config)
        self._farm_ui_key = key
        if builder:                      # làng phụ: ẩn tài nguyên / Thả phép / Tường làng chính, chỉ hiện công tắc nâng Tường làng phụ
            for widget in (self.frm_wall, self.sw_spell, self.frm_loot):
                widget.pack_forget()
            self.frm_builder_wall.pack(fill="x", after=self.lbl_json_status)
            self.frm_wall_tune.pack(fill="x", after=self.frm_builder_wall)
        else:                            # xếp lại đúng thứ tự: tài nguyên -> công tắc phép -> Tường
            self.frm_builder_wall.pack_forget()
            self.frm_loot.pack(fill="x", after=self.lbl_json_status)
            self.sw_spell.pack(anchor="w", padx=12, pady=(10, 6), after=self.frm_loot)
            self.frm_wall.pack(fill="x", after=self.sw_spell)
            self.frm_wall_tune.pack(fill="x", after=self.frm_wall)

    def _save_builder_wall_settings(self):
        """Lưu công tắc nâng Tường của Làng phụ (ENABLE_BUILDER_WALL_UPGRADE) vào config.json."""
        enabled = bool(self.var_builder_wall.get())
        if not save_json_config({"ENABLE_BUILDER_WALL_UPGRADE": enabled}):
            self._set_json_status("❌ Không ghi được config.json (kiểm tra quyền ghi file)", "#e74c3c")
            return False
        self._last_mtime = get_json_mtime()
        self._set_json_status(
            f"💾 Nâng Tường làng phụ: {'BẬT' if enabled else 'TẮT'} - đã lưu lúc {time.strftime('%H:%M:%S')}",
            "#2ecc71")
        return True

    def _save_farm_village(self):
        """Lưu FARM_VILLAGE vào config.json (bot đọc trực tiếp, đổi là có hiệu lực ở vòng lặp kế tiếp)."""
        key = self._key_from_label(FARM_VILLAGES, self.opt_farm.get(), DEFAULT_FARM_VILLAGE)
        self._apply_farm_village_ui(key)
        if not save_json_config({"FARM_VILLAGE": key}):
            self._set_json_status("❌ Không ghi được config.json (kiểm tra quyền ghi file)", "#e74c3c")
            return False
        self._last_mtime = get_json_mtime()
        self._set_json_status(f"💾 Đã chọn farm {'LÀNG PHỤ (Thợ xây)' if key == 'builder' else 'LÀNG CHÍNH'} "
                              f"lúc {time.strftime('%H:%M:%S')}", "#2ecc71")
        return True

    def _save_basic_settings(self):
        """
        Lưu LDPLAYER_PATH, LDPLAYER_INDEX, MIN_GOLD, MIN_ELIXIR, MIN_DARK_ELIXIR
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
        })
        if not ok:
            self._set_json_status("❌ Không ghi được config.json (kiểm tra quyền ghi file)", "#e74c3c")
            return False

        self._last_mtime = get_json_mtime()
        self._set_json_status(f"💾 Đã lưu thiết lập chung lúc {time.strftime('%H:%M:%S')}", "#2ecc71")
        return True

    def _schedule_save(self):
        """Gõ xong ~0.6s mới lưu (debounce) để không ghi file mỗi phím bấm."""
        if self._save_job is not None:
            try:
                self.after_cancel(self._save_job)
            except Exception:
                pass
        self._save_job = self.after(600, self._save_wall_settings)

    _WALL_RES_LABELS = {"AUTO": "Tự động (Vàng + Dầu)", "GOLD": "Chỉ Vàng", "ELIXIR": "Chỉ Dầu"}

    def _wall_village_key(self, farm_key=None):
        """'sub' (Làng phụ) | 'main' - cùng quy tắc bot_logic._wall_village (WALL_VILLAGE ghi đè)."""
        try:
            v = str(load_json_config().get("WALL_VILLAGE", "")).strip().lower()
        except Exception:
            v = ""
        if v in ("main", "sub"):
            return v
        farm_key = farm_key or getattr(self, "_farm_ui_key", None) or DEFAULT_FARM_VILLAGE
        return "sub" if farm_key == "builder" else "main"

    def _wall_level_from_cfg(self, cfg, farm_key=None):
        k = "WALL_LEVEL_SUB" if self._wall_village_key(farm_key) == "sub" else "WALL_LEVEL_MAIN"
        try:
            return int(cfg.get(k, 0) or 0)
        except (TypeError, ValueError):
            return 0

    def _refresh_wall_village_widgets(self, farm_key):
        """Cập nhật nhãn 'Làng' và ô 'Cấp' theo làng đang chọn (mỗi làng có cấp Tường riêng)."""
        if not hasattr(self, "lbl_wall_village"):
            return
        v = self._wall_village_key(farm_key)
        self.lbl_wall_village.configure(
            text="Làng phụ (Thợ xây)" if v == "sub" else "Làng chính", text_color="#f1c40f")
        try:
            self._set_entry(self.ent_wall_level, self._wall_level_from_cfg(load_json_config(), farm_key))
        except Exception:
            pass

    def _wall_weight_row(self, text, value):
        row = ctk.CTkFrame(self.frm_wall_tune, fg_color="transparent")
        row.pack(fill="x", pady=3, padx=10)
        ctk.CTkLabel(row, text=text, width=190, anchor="w").pack(side="left")
        sld = ctk.CTkSlider(row, from_=0.0, to=1.0, number_of_steps=20, width=200,
                            command=lambda _v: self._on_wall_weight_slide())
        sld.set(float(value))
        sld.pack(side="left")
        lbl = ctk.CTkLabel(row, text="", width=150, anchor="w")
        lbl.pack(side="left", padx=(8, 0))
        lbl.configure(text=f"{float(value):.2f}")
        return sld, lbl

    def _on_wall_weight_slide_nosave(self):
        for sld, lbl in ((self.sld_w_img, self.lbl_w_img), (self.sld_w_txt, self.lbl_w_txt),
                         (self.sld_w_price, self.lbl_w_price)):
            lbl.configure(text=f"{float(sld.get()):.2f}")

    def _on_wall_weight_slide(self):
        for sld, lbl in ((self.sld_w_img, self.lbl_w_img), (self.sld_w_txt, self.lbl_w_txt),
                         (self.sld_w_price, self.lbl_w_price)):
            lbl.configure(text=f"{float(sld.get()):.2f}")
        self._schedule_save()

    def _save_wall_settings(self):
        """
        Lưu công tắc nâng Tường + thông số quét thẻ Tường vào config.json (giữ nguyên ROI do crop_tool lưu).
        Trả về True nếu hợp lệ và đã lưu.
        """
        self._save_job = None
        try:
            xn_ratio = self._xn_ratio_from_entries()
        except ValueError:
            self._set_json_status(
                "⚠️ Vùng xN phải là 2 số 0-100, kết thúc lớn hơn bắt đầu ít nhất 5 - CHƯA lưu", "#e67e22")
            return False

        try:
            level = int(float(self.ent_wall_level.get().strip() or "0"))
            if level < 0:
                raise ValueError
        except ValueError:
            self._set_json_status("⚠️ Cấp Tường phải là số nguyên >= 0 (0 = mọi cấp) - CHƯA lưu", "#e67e22")
            return False
        res_key = next((k for k, v in self._WALL_RES_LABELS.items() if v == self.opt_wall_res.get()), "AUTO")
        level_key = "WALL_LEVEL_SUB" if self._wall_village_key() == "sub" else "WALL_LEVEL_MAIN"

        ok = save_json_config({
            "ENABLE_WALL_UPGRADE": bool(self.var_wall.get()),
            "WALL_MATCH_THRESHOLD": round(float(self.sld_wall_match.get()), 2),
            "WALL_MENU_SWIPE_MULT": round(float(self.sld_wall_swipe.get()), 2),
            "WALL_CARD_XN_EXCLUDE_RATIO": xn_ratio,
            level_key: level,
            "WALL_RESOURCE": res_key,
            "WALL_WEIGHT_IMAGE": round(float(self.sld_w_img.get()), 2),
            "WALL_WEIGHT_TEXT": round(float(self.sld_w_txt.get()), 2),
            "WALL_WEIGHT_PRICE": round(float(self.sld_w_price.get()), 2),
        })
        if not ok:
            self._set_json_status("❌ Không ghi được config.json (kiểm tra quyền ghi file)", "#e74c3c")
            self.log("[❌ LỖI] Không ghi được config.json!")
            return False

        self._last_mtime = get_json_mtime()
        self._set_json_status(f"💾 Đã lưu config.json lúc {time.strftime('%H:%M:%S')}", "#2ecc71")
        return True

    def _update_wall_match_label(self, value):
        self.lbl_wall_match.configure(
            text=f"{float(value):.2f}  (nhỏ = nhạy hơn)")

    def _on_wall_match_slide(self, value):
        """Kéo thanh trượt: cập nhật nhãn ngay, ~0.6s sau khi ngừng kéo mới lưu config.json."""
        self._update_wall_match_label(value)
        self._schedule_save()

    # ---- Quãng đường vuốt menu Búa (WALL_MENU_SWIPE_MULT) ----
    _SWIPE_MULT_MIN = 0.5    # trùng khoảng kẹp trong bot_logic._swipe_wall_menu (0.5 - 3.0)
    _SWIPE_MULT_MAX = 3.0
    _SWIPE_BASE_DISTANCE = 200   # = WALL_MENU_SWIPE_DISTANCE trong bot_logic.py

    @classmethod
    def _clamp_swipe_mult(cls, value):
        try:
            v = float(value)
        except (TypeError, ValueError):
            v = 1.5
        return max(cls._SWIPE_MULT_MIN, min(cls._SWIPE_MULT_MAX, v))

    def _update_wall_swipe_label(self, value):
        """Hiện hệ số x và quãng đường vuốt thực tế (px) để dễ hình dung khi kéo thanh trượt."""
        mult = self._clamp_swipe_mult(value)
        try:
            pos = load_json_config().get("MANUAL_POSITIONS", {})
            sy = int(pos["BUILDER_MENU_SCROLL_START"][1])
            dy = int(pos["BUILDER_MENU_SCROLL_END"][1]) - sy
        except Exception:
            sy, dy = 360, -180
        step = min(abs(dy), self._SWIPE_BASE_DISTANCE) * (1 if dy >= 0 else -1)
        ey = int(max(8, min(532, sy + step * mult)))     # giống công thức kẹp màn hình ở bot_logic
        self.lbl_wall_swipe.configure(text=f"x{mult:.2f}  (~{abs(ey - sy)}px, lớn = vuốt xa hơn)")

    def _on_wall_swipe_slide(self, value):
        """Kéo thanh trượt: cập nhật nhãn ngay, ~0.6s sau khi ngừng kéo mới lưu config.json."""
        self._update_wall_swipe_label(value)
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
        _farm = cfg.get("FARM_VILLAGE", DEFAULT_FARM_VILLAGE)
        _farm = _farm if _farm in FARM_VILLAGES else DEFAULT_FARM_VILLAGE
        self.opt_farm.set(FARM_VILLAGES[_farm])
        self._apply_farm_village_ui(_farm)
        if not self._is_focused(self.ent_enemy_roi):
            self._set_entry(self.ent_enemy_roi,
                            self._fmt_roi(cfg["RESOURCE_ENEMY_ROI"]) if cfg.get("RESOURCE_ENEMY_ROI") else "")
        self._set_entry(self.ent_path, cfg["LDPLAYER_PATH"])
        self._set_entry(self.ent_index, cfg["LDPLAYER_INDEX"])
        _mode = normalize_deploy_mode(cfg.get("DEPLOY_MODE"))
        self.opt_deploy.set(DEPLOY_MODES[_mode])
        if _mode != self._zone_mode:
            self._switch_zone_mode(_mode, commit=False)
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
        if not self._is_focused(self.ent_single):
            self._load_single_points(pos)
            self._refresh_single_widgets()
        self._set_entry(self.ent_reload_pos, self._fmt_point(pos["RELOAD_GAME_POS"]))
        self._set_entry(self.ent_safe_click, self._fmt_point(pos["FALLBACK_SAFE_CLICK_POS"]))
        spell = cfg["SPELL_CONFIG"]
        self.opt_spell_drop.set(SPELL_DROP_MODES.get(spell["DROP_MODE"], SPELL_DROP_MODES["evenly"]))
        self._set_entry(self.ent_spell_targets,
                        "; ".join(self._fmt_point(p) for p in spell["TARGET_POSITIONS"]))
        self._sync_card_rows(cfg["DEPLOY_CARDS_CONFIG"])
        self._apply_round2_from_cfg(cfg)

    def _sync_card_rows(self, cards):
        """Dựng lại danh sách thẻ quân nếu khác config.json (bỏ qua khi người dùng đang gõ trong thẻ)."""
        desired = [self._card_sig(c) for c in cards]
        try:
            current = [self._card_sig(self._card_from_row(r)) for r in self.card_rows]
        except ValueError:
            current = None
        if current == desired:
            return
        if any(self._is_focused(r[k]) for r in self.card_rows
               for k in ("pos", "clicks", "zone_entry", "skill_delay")):
            return
        for r in list(self.card_rows):
            r["frame"].destroy()
        self.card_rows.clear()
        for c in cards:
            self._add_card_row(c["pos"], c["type"], c["clicks_count"], save=False, extra=c)

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
                self.sld_wall_match.set(cfg["WALL_MATCH_THRESHOLD"])
                self._update_wall_match_label(cfg["WALL_MATCH_THRESHOLD"])
                _sm = self._clamp_swipe_mult(cfg.get("WALL_MENU_SWIPE_MULT", 1.5))
                self.sld_wall_swipe.set(_sm)
                self._update_wall_swipe_label(_sm)
                xn_a, xn_b = cfg["WALL_CARD_XN_EXCLUDE_RATIO"]
                self._set_entry(self.ent_xn_start, self._fmt_pct(xn_a))
                self._set_entry(self.ent_xn_end, self._fmt_pct(xn_b))
                self.var_builder_wall.set(bool(cfg.get("ENABLE_BUILDER_WALL_UPGRADE", False)))
                self._set_entry(self.ent_wall_level, self._wall_level_from_cfg(cfg))
                self.opt_wall_res.set(self._WALL_RES_LABELS.get(cfg.get("WALL_RESOURCE", "AUTO"), self._WALL_RES_LABELS["AUTO"]))
                for _s, _k, _d in ((self.sld_w_img, "WALL_WEIGHT_IMAGE", 1.0), (self.sld_w_txt, "WALL_WEIGHT_TEXT", 1.0),
                                   (self.sld_w_price, "WALL_WEIGHT_PRICE", 0.6)):
                    _s.set(float(cfg.get(_k, _d)))
                self._on_wall_weight_slide_nosave()
                self._update_roi_status(cfg)
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
        for stamp, raw in batch:
            tag = self._log_tag(raw)
            label = LOG_LEVEL_LABELS.get(tag, LOG_LEVEL_LABELS[None])
            self.log_box.insert("end", f"{stamp}  {label:<9}│ {clean_message(raw)}\n", tag)
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
        text = clean_message(text)
        self._status_level = level
        self._status_since = time.time()
        self._timer_text = None
        self.status_banner.configure(fg_color=bg)
        self.lbl_status_main.configure(text=text, text_color=fg)
        self.lbl_status_timer.configure(text="", text_color=fg)
        self._update_danger(level)

    # ------------------------------------------- Hiệu ứng nguy hiểm khi thả lính
    def _update_danger(self, level):
        """level == 'attack' -> bật hiệu ứng đỏ nhấp nháy toàn giao diện; level khác -> tắt và khôi phục màu."""
        if level == "attack":
            if self._danger_job is None:
                try:
                    self._danger_saved = {
                        "window": self.cget("fg_color"),
                        "header": self._header.cget("fg_color"),
                        "border_w": self.tabs.cget("border_width"),
                        "border_c": self.tabs.cget("border_color"),
                    }
                except Exception:
                    self._danger_saved = None
                self._danger_phase = 0
                self._danger_tick()
        else:
            self._stop_danger()

    def _danger_tick(self):
        try:
            pal = DANGER_PALETTE[self._danger_phase % len(DANGER_PALETTE)]
            self._danger_phase += 1
            self.status_banner.configure(fg_color=pal["banner"])
            self.configure(fg_color=pal["window"])
            self._header.configure(fg_color=pal["header"])
            self.tabs.configure(border_width=3, border_color=pal["border"])
            self.led.configure(text_color=pal["led"])
        except Exception:
            pass
        self._danger_job = self.after(DANGER_INTERVAL_MS, self._danger_tick)

    def _stop_danger(self):
        if self._danger_job is None:
            return
        try:
            self.after_cancel(self._danger_job)
        except Exception:
            pass
        self._danger_job = None
        saved, self._danger_saved = self._danger_saved, None
        try:
            if saved:
                self.configure(fg_color=saved["window"])
                self._header.configure(fg_color=saved["header"])
                self.tabs.configure(border_width=saved["border_w"], border_color=saved["border_c"])
            self.led.configure(text_color="#2ecc71" if self.is_running else "#e74c3c")
        except Exception:
            pass

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
                text = f"Đã chạy {sec}s" if sec < 60 else f"Đã chạy {sec // 60}p{sec % 60:02d}s"
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
                    text, color = "Nâng Tường: đến lượt ở trận kế tiếp", "#2ecc71"
                else:
                    text, color = f"Nâng Tường: còn {until_wall} trận nữa", "#f39c12"
                self.lbl_next_wall_status.configure(text=text, text_color=color)
                if not self.lbl_next_wall_status.winfo_manager():
                    self.lbl_next_wall_status.pack(fill="x", padx=12, pady=(0, 4), before=self.log_header)
        elif kind == "state":
            if not data:
                self._hide_setup_wait()
                if self._status_level != "done":   # đã cào xong dữ liệu thì giữ banner hoàn tất
                    self._set_banner("Bot đã dừng", "idle")
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
            "farm_village": self._key_from_label(FARM_VILLAGES, self.opt_farm.get(), DEFAULT_FARM_VILLAGE),
            "strategy": load_json_config().get("STRATEGY") or DEFAULT_STRATEGY,
            # Kiểu thả quân / phép (key, xem DEPLOY_MODES)
            "deploy_mode": self._deploy_settings()["DEPLOY_MODE"],
            "enable_spell": bool(self.var_spell.get()),
            "spell_mode": self._deploy_settings()["SPELL_MODE"],
            # Thông số xả tường (cũng được bot đọc trực tiếp từ config.json)
            "enable_wall": bool(self.var_wall.get()),
            "wall_gold": load_json_config()["WALL_UPGRADE_GOLD_THRESHOLD"],   # không còn ô nhập trên UI
            "wall_elixir": load_json_config()["WALL_UPGRADE_ELIXIR_THRESHOLD"],
            "ocr_only_for_wall": bool(load_json_config().get("OCR_ONLY_FOR_WALL", True)),
            "collect_data_mode": bool(load_json_config().get("COLLECT_DATA_MODE", False)),
            "collect_max_samples": int(load_json_config().get("COLLECT_MAX_SAMPLES", 100)),
        }

    def _on_click_start(self):
        if self.is_running:
            return
        try:
            config = self._read_config()
        except ValueError:
            self.log("[❌ LỖI] Mốc tài nguyên và Index phải là số nguyên!")
            return
        if not self._save_basic_settings():
            self.log("[❌ LỖI] Chưa lưu được thiết lập chung (LDPlayer, Index, mốc tài nguyên, chiến thuật), bot chưa chạy.")
            return
        if not self._save_wall_settings():
            self.log("[❌ LỖI] Chưa lưu được cấu hình nâng Tường vào config.json, bot chưa chạy.")
            return
        if not self._save_builder_wall_settings():
            self.log("[❌ LỖI] Chưa lưu được cấu hình nâng Tường làng phụ vào config.json, bot chưa chạy.")
            return
        self._save_deploy_settings()
        if not self._save_manual_settings():
            self.log("[❌ LỖI] Tọa độ / thẻ quân chưa hợp lệ (tab 'Tọa độ & Thẻ quân'), bot chưa chạy.")
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
        for job_attr in ("_save_job", "_manual_job", "_basic_job", "_danger_job"):
            job = getattr(self, job_attr, None)
            if job is not None:
                try:
                    self.after_cancel(job)
                except Exception:
                    pass
                setattr(self, job_attr, None)
        for save in (self._save_basic_settings, self._save_deploy_settings, self._save_wall_settings,
                     self._save_builder_wall_settings,
                     self._save_manual_settings):
            try:
                save()
            except Exception:
                pass
        self.destroy()