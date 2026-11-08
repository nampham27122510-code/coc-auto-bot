import json
import os
import shutil
import threading

# ============================================================
# CẤU HÌNH LDPLAYER & ADB
# ============================================================

# Đường dẫn cài đặt LDPlayer 9 mặc định (Ổ D)
LDPLAYER_PATH = r"D:\LDPlayer\LDPlayer9"

# Đường dẫn adb.exe đi kèm LDPlayer (dùng đúng bản adb của LDPlayer
# để tránh xung đột phiên bản ADB Server với các bản adb khác trên máy)
ADB_PATH = os.path.join(LDPLAYER_PATH, "adb.exe")

# Instance ID của LDPlayer (0 = máy ảo đầu tiên)
EMULATOR_INDEX = 0

# Cổng ADB mặc định của Index 0 (Index n -> 5555 + n * 2)
ADB_PORT = 5555

# Thời gian chờ tối đa (giây) cho mỗi lệnh ADB thông thường
ADB_TIMEOUT = 5

# Throttle chụp màn hình (chống sập máy / BSoD): 2 lần gọi capture_screen() cách nhau ít hơn
# số giây này thì trả ngay ảnh cũ, không gọi `adb exec-out screencap` nữa.
SCREENSHOT_MIN_INTERVAL = 0.25

# Nhịp nghỉ của các vòng lặp while (giây) và chu kỳ dọn bộ nhớ gc.collect() (giây)
LOOP_SLEEP = 0.4
GC_INTERVAL_SECONDS = 60

# ============================================================
# CẤU HÌNH TESSERACT OCR
# ============================================================

# Đường dẫn Tesseract OCR trên Windows.
# Nếu cài ở nơi khác, hãy sửa cho đúng (phải trỏ thẳng tới file tesseract.exe).
# Bản cài mặc định: C:\Program Files\Tesseract-OCR\tesseract.exe
# Tải về tại: https://github.com/UB-Mannheim/tesseract/wiki
TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# Thông báo lỗi hiển thị khi không tìm thấy Tesseract
TESSERACT_ERROR_MSG = (
    "[❌ LỖI] Chưa cài đặt Tesseract OCR hoặc sai đường dẫn "
    "TESSERACT_PATH trong config.py!"
)

# ============================================================
# CẤU HÌNH DỰ ÁN & TÀI NGUYÊN
# ============================================================

# Thư mục gốc của dự án (dùng để tìm assets)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Cấu hình tài nguyên mặc định khi soi nhà đối phương
MIN_GOLD = 1000000
MIN_ELIXIR = 1000000
MIN_DARK_ELIXIR = 0

# Chế độ kiểm tra điều kiện tài nguyên địch:
#   "OR"    : Vàng >= MIN_GOLD HOẶC Dầu >= MIN_ELIXIR
#   "AND"   : Vàng >= MIN_GOLD VÀ   Dầu >= MIN_ELIXIR
#   "TOTAL" : (Vàng + Dầu) >= (MIN_GOLD + MIN_ELIXIR)
LOOT_MATCH_MODES = ("OR", "AND", "TOTAL")
DEFAULT_LOOT_MATCH_MODE = "OR"

# Vùng cắt OCR (y1, y2, x1, x2) trên màn hình 960x540 khi đang soi nhà đối phương.
# ĐÂY LÀ GIÁ TRỊ ƯỚC LƯỢNG - hãy chụp màn hình thật và chỉnh lại cho khớp.
LOOT_REGIONS = {
    "gold":   (85, 110, 50, 180),
    "elixir": (115, 140, 50, 180),
    "dark":   (145, 170, 50, 180),
}

# Vùng số thợ xây (y1, y2, x1, x2) trên 960x540. Định dạng chữ: "X/Y"
BUILDER_REGION = (60, 88, 825, 865)

# Vùng đọc tài nguyên CỦA MÌNH ở làng nhà (y1, y2, x1, x2) - GIÁ TRỊ ƯỚC LƯỢNG,
# hãy chỉnh lại theo ảnh chụp thật. Dùng để quyết định có xả tài nguyên vào Tường không.
HOME_RESOURCE_REGIONS = {
    "gold":   (20, 45, 700, 880),
    "elixir": (60, 85, 700, 880),
}

# Ngưỡng tài nguyên "dư" ở làng nhà: vượt ngưỡng này thì ưu tiên nâng Tường
# (mặc định 5,000,000 - có thể chỉnh trên giao diện, giá trị lưu trong config.json)
SURPLUS_GOLD = 5000000
SURPLUS_ELIXIR = 5000000

# Số lần nâng Tường tối đa trong một lượt nâng cấp (1-2)
WALL_UPGRADE_MAX_PER_CYCLE = 2

# ============================================================
# TỌA ĐỘ THAO TÁC (960x540) - TẤT CẢ LÀ GIÁ TRỊ ƯỚC LƯỢNG, CẦN CHỈNH THEO MÁY THẬT
# ============================================================

# Tâm nút cái búa màu vàng có chữ "Xây dựng" -> mở bảng "CÔNG TRÌNH VÀ TƯỜNG"
BUILD_BUTTON_POS = (480, 500)

# Vuốt menu gợi ý từ dưới lên để cuộn tới phần Tường thành
BUILDER_MENU_SCROLL_START = (750, 450)
BUILDER_MENU_SCROLL_END = (750, 150)
BUILDER_MENU_SCROLL_DURATION = 400  # mili giây

# Dòng Tường thành xuất hiện ở phía dưới menu sau khi cuộn
WALL_ITEM_POS = (750, 430)

# Dòng đề xuất đầu tiên trong menu (dùng để nâng công trình chính)
FIRST_UPGRADE_POS = (750, 150)

# Nút xác nhận nâng cấp
CONFIRM_UPGRADE_POS = (480, 420)

# Điểm bấm vùng trống để đóng menu
CLOSE_MENU_POS = (100, 100)

# Giá trị mặc định của 2 tọa độ dự phòng (giá trị thật nằm ở config.json -> MANUAL_POSITIONS,
# sửa trên GUI tab 'Tọa độ & Thẻ quân'):
RELOAD_GAME_POS_DEFAULT = (310, 370)         # nút "TẢI LẠI TRÒ CHƠI" (popup ngắt kết nối)
FALLBACK_SAFE_CLICK_POS_DEFAULT = (840, 130)  # bấm thoát luồng/giao diện lạ (vùng trống an toàn)

# ============================================================
# ZOOM OUT LÀNG NHÀ (pinch 2 ngón qua ADB) - gọi sau khi nâng Tường xong
# ============================================================
# Mỗi đường là (x1, y1, x2, y2): ngón A và ngón B cùng chạm lúc đầu rồi CHỤM VÀO nhau
# (zoom out). Giá trị ước lượng trên 960x540 - chỉnh theo máy thật nếu góc nhìn chưa xa hết cỡ.
ZOOM_OUT_SWIPE_A = (300, 270, 450, 270)   # ngón trái: từ ngoài -> vào giữa
ZOOM_OUT_SWIPE_B = (660, 270, 510, 270)   # ngón phải: từ ngoài -> vào giữa
ZOOM_OUT_DURATION_MS = 600                # thời gian một lần chụm (mili giây)
ZOOM_OUT_REPEAT = 2                       # số lần chụm liên tiếp (đảm bảo xa hết cỡ)

# ============================================================
# TỌA ĐỘ THẢ QUÂN (960x540)
# ============================================================

# Thanh quân ở dưới cùng
TROOP_SLOT_1_POS = (120, 500)   # Ô 1: Barch / Goblin
TROOP_SLOT_2_POS = (200, 500)   # Ô 2: loại quân thứ hai
HERO_SLOT_POS = (280, 500)      # Ô Tướng (nếu có)

# Đường vuốt thả quân dọc rìa làng đối phương: (x1, y1, x2, y2)
DEPLOY_SWIPE_1 = (200, 250, 400, 120)
DEPLOY_SWIPE_2 = (250, 300, 450, 160)
HERO_DEPLOY_POS = (220, 270)

# Thời gian chờ trận đánh (giây): chọn ngẫu nhiên trong khoảng này
BATTLE_WAIT_MIN = 40
BATTLE_WAIT_MAX = 50

# Chuỗi nút kết thúc trận
END_BATTLE_POS = (80, 470)          # nút "Kết thúc trận" (góc trái dưới)
END_BATTLE_CONFIRM_POS = (580, 330)  # nút "Xác nhận" trên popup
RETURN_HOME_POS = (480, 450)         # nút "Về nhà" (chính giữa dưới màn hình 960x540); bot ưu tiên
                                     # ảnh mẫu assets/button_return_home.png, rồi RETURN_HOME_POS
                                     # trong config.json (crop_tool ghi), cuối cùng mới tới giá trị này

# ============================================================
# ĐỌC / GHI config.json (do tools/crop_tool.py và gui/app_ui.py cập nhật)
# ============================================================
# Quy ước: giá trị trong config.json được ƯU TIÊN hơn hằng số ở trên.
# ROI trong config.json có dạng [x1, y1, x2, y2] (khác với các vùng
# (y1, y2, x1, x2) ở phía trên của file này).

CONFIG_JSON_PATH = os.path.join(BASE_DIR, "config.json")

JSON_CONFIG_DEFAULTS = {
    # --- Thiết lập giao diện (gui/app_ui.py tự lưu; thiếu khóa -> lấy hằng số ở đầu file này) ---
    "LDPLAYER_PATH": LDPLAYER_PATH,
    "LDPLAYER_INDEX": EMULATOR_INDEX,
    "MIN_GOLD": MIN_GOLD,
    "MIN_ELIXIR": MIN_ELIXIR,
    "MIN_DARK_ELIXIR": MIN_DARK_ELIXIR,
    "LOOT_MATCH_MODE": DEFAULT_LOOT_MATCH_MODE,   # "OR" | "AND" | "TOTAL"
    "STRATEGY": "Thả quân cơ bản",
    "DEPLOY_MODE": "left_corner",
    "SPELL_ENABLED": True,
    "SPELL_MODE": "enemy_center_random",
    "OCR_ONLY_FOR_WALL": False,
    "RESOURCE_MY_VILLAGE_ROI": None,   # [x1, y1, x2, y2] - Vàng/Dầu làng nhà
    "RESOURCE_ENEMY_ROI": None,        # [x1, y1, x2, y2] - Vàng/Dầu/Dầu đen làng địch
    "ENABLE_WALL_UPGRADE": True,
    "WALL_UPGRADE_GOLD_THRESHOLD": SURPLUS_GOLD,
    "WALL_UPGRADE_ELIXIR_THRESHOLD": SURPLUS_ELIXIR,
    # --- Chế độ Cào dữ liệu (bật/tắt trên giao diện, tab "Cấu hình Game") ---
    "COLLECT_DATA_MODE": False,        # True: chỉ chụp ROI địch + bấm Next, không thả quân
    "COLLECT_MAX_SAMPLES": 100,        # số nhà cần cào trước khi bot tự dừng
    # --- Do tools/crop_tool.py ghi ---
    "RETURN_HOME_POS": None,           # [x, y] tâm nút "Về nhà"; None -> dùng hằng số RETURN_HOME_POS ở trên
    "screen_size": None,               # [rộng, cao] của ảnh lúc khoanh ROI (crop_tool luôn ghi [960, 540])
    # --- Tọa độ thủ công (chọn trên giao diện bằng nút [📌 Chọn Tọa độ]) ---
    "MANUAL_POSITIONS": {
        "WALL_UPGRADE_GOLD_BTN": [480, 490],    # nút Nâng cấp bằng VÀNG trong popup Tường
        "WALL_UPGRADE_ELIXIR_BTN": [580, 490],  # nút Nâng cấp bằng DẦU trong popup Tường
        "WALL_CONFIRM_BTN": [700, 460],   # nút Xác nhận nâng
        "TROOP_DROP_ZONE": [120, 260],    # điểm thả lính
        "RELOAD_GAME_POS": [310, 370],    # nút Tải lại trò chơi (dự phòng khi thiếu ảnh mẫu)
        "FALLBACK_SAFE_CLICK_POS": [840, 130],  # tọa độ thoát an toàn khi ở luồng lạ > 5s (không dùng nút X)
    },
    "SCREENSHOT_MIN_INTERVAL": SCREENSHOT_MIN_INTERVAL,   # giây tối thiểu giữa 2 lần chụp ADB thật
    "ZOOM_ENABLE_CTRL_WHEEL": False,      # False = KHÔNG dùng Ctrl + cuộn chuột (không cướp con trỏ thật)
    "WALL_RETRY_BATTLES_LIMIT": 5,        # thiếu tài nguyên -> đi đánh đủ số trận này mới thử nâng Tường lại
    # --- Nhận diện thẻ Tường trong menu Búa (Hybrid Template + OCR) ---
    "WALL_MATCH_THRESHOLD": 0.75,         # 0.45-0.95; CHỈ dùng cho việc quét thẻ Tường trong menu Búa (càng NHỎ càng nhạy)
    # --- BỎ PHIẾU NHIỀU ẢNH: 1 ảnh khớp là chưa đủ, phải có nhiều bằng chứng độc lập cùng đồng ý ---
    "TEMPLATE_MIN_VOTES": 2,              # nút UI có >= 2 ảnh biến thể: cần ít nhất ngần này ảnh KHỚP CÙNG VỊ TRÍ (1 = tắt bỏ phiếu)
    "TEMPLATE_VOTE_TOL": 14,              # px: các ảnh khớp cách nhau <= ngần này coi là CÙNG một vị trí
    "TEMPLATE_SOLO_SCORE": 0.95,          # chỉ 1 ảnh khớp nhưng điểm >= ngần này (gần như trùng pixel) vẫn chấp nhận
    # Điểm thẻ Tường = WALL_WEIGHT_ID x điểm nhận dạng (ảnh + chữ Tường, phần TRÁI) + WALL_WEIGHT_PRICE x điểm giá/icon (phần PHẢI)
    "WALL_WEIGHT_PRICE": 0.6,             # trọng số phần giá + icon Vàng/Dầu (chữ đỏ thiếu tài nguyên vẫn tính là có giá)
    "WALL_WEIGHT_ID": 0.4,                # trọng số phần ảnh + chữ "Tường" (tự chuẩn hóa nếu tổng != 1)
    "WALL_MIN_ID_SCORE": 0.6,             # điểm SÀN của phần nhận dạng Tường (0-1): thấp hơn thì loại dù giá khớp cao
    "WALL_MIN_VARIANT_VOTES": 2,          # số ảnh biến thể wall_item_* phải cùng khớp 1 dòng để tính là 'nhiều ảnh đồng thuận'
    # Vùng GIỮA thẻ Tường bị "đục lỗ" (mask) khi khớp ảnh mẫu card_wall.png để bỏ chữ số xN (x152, x150...)
    # [bắt đầu, kết thúc] tính theo tỉ lệ chiều rộng thẻ (0.0-1.0): 0.40 = 40%, 0.70 = 70%
    "WALL_CARD_XN_EXCLUDE_RATIO": [0.40, 0.70],
    "WALL_MENU_MAX_SWIPES": 6,            # số lần vuốt menu tối đa để tìm thẻ Tường
    "WALL_MENU_SWIPE_DURATION": 600,      # mili giây mỗi lần vuốt (nhỏ = nhanh nhưng dễ bị trôi quán tính)
    "DEPLOY_CARDS_CONFIG": [
        {"slot": 1, "pos": [220, 480], "type": "troop", "clicks_count": 30},
        {"slot": 2, "pos": [290, 480], "type": "troop", "clicks_count": 10},
        {"slot": 3, "pos": [360, 480], "type": "troop", "clicks_count": 10},
        {"slot": 4, "pos": [430, 480], "type": "troop", "clicks_count": 10},
        {"slot": 5, "pos": [500, 480], "type": "spell", "clicks_count": 11},
    ],
    "SPELL_CONFIG": {
        "DROP_MODE": "evenly",            # "evenly" (xoay vòng) | "random"
        "TARGET_POSITIONS": [[405, 170], [610, 195], [405, 290], [610, 305]],
    },
}

_json_lock = threading.Lock()
_json_cache = {"mtime": None, "data": None}


def _valid_roi(value):
    """Trả về [x1, y1, x2, y2] hợp lệ hoặc None."""
    try:
        x1, y1, x2, y2 = [int(round(float(v))) for v in value]
    except (TypeError, ValueError):
        return None
    if min(x1, y1) < 0 or x2 <= x1 or y2 <= y1:
        return None
    return [x1, y1, x2, y2]


def _valid_point(value):
    """Trả về [x, y] (số nguyên không âm) hoặc None."""
    try:
        x, y = [int(round(float(v))) for v in value]
    except (TypeError, ValueError):
        return None
    if x < 0 or y < 0:
        return None
    return [x, y]


def _valid_size(value):
    """Trả về [rộng, cao] (số nguyên dương) hoặc None."""
    size = _valid_point(value)
    return size if size and size[0] > 0 and size[1] > 0 else None


def _to_positive_int(value, default):
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _to_positive_float(value, default):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _to_bool(value, default):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _to_threshold(value, default):
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return number if number >= 0 else default


def _to_float_range(value, default, low, high):
    """Số thực kẹp trong [low, high]; sai kiểu -> mặc định."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:   # NaN
        return default
    return max(low, min(high, number))


def _to_nonneg_int(value, default):
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return number if number >= 0 else default


def _to_text(value, default):
    """Chuỗi không rỗng (đã strip) hoặc mặc định."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _normalize_ratio_range(value, default, min_gap=0.05):
    """
    Ép [bắt đầu, kết thúc] về cặp số thực trong [0, 1] với bắt đầu + min_gap <= kết thúc.
    Sai kiểu / thiếu / không hợp lệ -> trả bản sao của `default`.
    """
    try:
        a, b = [float(v) for v in value]
    except (TypeError, ValueError):
        return list(default)
    if a != a or b != b:   # NaN
        return list(default)
    a = max(0.0, min(1.0, a))
    b = max(0.0, min(1.0, b))
    if a > b:
        a, b = b, a
    if b - a < min_gap:
        return list(default)
    return [round(a, 3), round(b, 3)]


def _normalize_manual_positions(value):
    """Ép MANUAL_POSITIONS về dict đủ các khóa [x, y]; khóa sai/thiếu -> mặc định."""
    default = JSON_CONFIG_DEFAULTS["MANUAL_POSITIONS"]
    src = value if isinstance(value, dict) else {}
    out = dict(src)   # giữ khóa lạ
    # Tương thích ngược: CLOSE_POPUP_POS (nút X cũ) -> FALLBACK_SAFE_CLICK_POS; không còn dùng nút X
    legacy_close = _valid_point(out.pop("CLOSE_POPUP_POS", None))
    # Tương thích ngược: bản cũ chỉ có 1 nút WALL_UPGRADE_BTN -> dùng cho cả Vàng và Dầu
    legacy_btn = _valid_point(src.get("WALL_UPGRADE_BTN"))
    for key, dflt in default.items():
        fallback = legacy_btn if key in ("WALL_UPGRADE_GOLD_BTN", "WALL_UPGRADE_ELIXIR_BTN") else None
        if key == "FALLBACK_SAFE_CLICK_POS":
            fallback = legacy_close
        out[key] = _valid_point(src.get(key)) or fallback or list(dflt)
    return out


def _normalize_deploy_cards(value):
    """Ép DEPLOY_CARDS_CONFIG thành list thẻ hợp lệ; thẻ sai bị bỏ. Không có thẻ nào -> mặc định."""
    cards = []
    if isinstance(value, list):
        for i, item in enumerate(value, 1):
            if not isinstance(item, dict):
                continue
            pos = _valid_point(item.get("pos"))
            kind = str(item.get("type", "")).strip().lower()
            if pos is None or kind not in ("troop", "spell"):
                continue
            cards.append({
                "slot": _to_positive_int(item.get("slot"), i),
                "pos": pos,
                "type": kind,
                "clicks_count": _to_positive_int(item.get("clicks_count"), 1),
            })
    if not cards and not isinstance(value, list):
        return [dict(c) for c in JSON_CONFIG_DEFAULTS["DEPLOY_CARDS_CONFIG"]]
    return cards


def _normalize_spell_config(value):
    default = JSON_CONFIG_DEFAULTS["SPELL_CONFIG"]
    src = value if isinstance(value, dict) else {}
    mode = str(src.get("DROP_MODE", default["DROP_MODE"])).strip().lower()
    if mode not in ("evenly", "random"):
        mode = default["DROP_MODE"]
    points = []
    raw_points = src.get("TARGET_POSITIONS")
    if isinstance(raw_points, list):
        points = [p for p in (_valid_point(v) for v in raw_points) if p]
    if not points:
        points = [list(p) for p in default["TARGET_POSITIONS"]]
    return {"DROP_MODE": mode, "TARGET_POSITIONS": points}


def _normalize_json_config(raw):
    """Giữ nguyên khóa lạ, ép các khóa đã biết về đúng kiểu / giá trị mặc định."""
    data = dict(raw) if isinstance(raw, dict) else {}
    d = JSON_CONFIG_DEFAULTS
    # Tương thích ngược: bản crop_tool cũ từng lưu ROI trong khóa lồng nhau "rois"
    legacy = data.get("rois") if isinstance(data.get("rois"), dict) else {}
    for key in ("RESOURCE_MY_VILLAGE_ROI", "RESOURCE_ENEMY_ROI"):
        data[key] = _valid_roi(data.get(key)) or _valid_roi(legacy.get(key))
    data["ENABLE_WALL_UPGRADE"] = _to_bool(
        data.get("ENABLE_WALL_UPGRADE"), d["ENABLE_WALL_UPGRADE"]
    )
    for key in ("WALL_UPGRADE_GOLD_THRESHOLD", "WALL_UPGRADE_ELIXIR_THRESHOLD"):
        data[key] = _to_threshold(data.get(key), d[key])
    # Chế độ Cào dữ liệu: sai kiểu -> về mặc định (tắt / 100 mẫu)
    data["COLLECT_DATA_MODE"] = _to_bool(data.get("COLLECT_DATA_MODE"), d["COLLECT_DATA_MODE"])
    data["COLLECT_MAX_SAMPLES"] = _to_positive_int(
        data.get("COLLECT_MAX_SAMPLES"), d["COLLECT_MAX_SAMPLES"]
    )
    # Do crop_tool ghi: sai định dạng -> None (bot sẽ dùng giá trị mặc định tương ứng)
    data["RETURN_HOME_POS"] = _valid_point(data.get("RETURN_HOME_POS"))
    data["screen_size"] = _valid_size(data.get("screen_size"))
    # Tọa độ thủ công / thẻ quân / phép / số trận chờ nâng Tường lại
    data["MANUAL_POSITIONS"] = _normalize_manual_positions(data.get("MANUAL_POSITIONS"))
    data["SCREENSHOT_MIN_INTERVAL"] = _to_positive_float(
        data.get("SCREENSHOT_MIN_INTERVAL"), d["SCREENSHOT_MIN_INTERVAL"])
    data["WALL_RETRY_BATTLES_LIMIT"] = _to_positive_int(
        data.get("WALL_RETRY_BATTLES_LIMIT"), d["WALL_RETRY_BATTLES_LIMIT"]
    )
    data["WALL_MATCH_THRESHOLD"] = _to_float_range(
        data.get("WALL_MATCH_THRESHOLD"), d["WALL_MATCH_THRESHOLD"], 0.45, 0.95)
    # Bỏ phiếu nhiều ảnh
    data["TEMPLATE_MIN_VOTES"] = _to_positive_int(data.get("TEMPLATE_MIN_VOTES"), d["TEMPLATE_MIN_VOTES"])
    data["TEMPLATE_VOTE_TOL"] = _to_positive_int(data.get("TEMPLATE_VOTE_TOL"), d["TEMPLATE_VOTE_TOL"])
    data["TEMPLATE_SOLO_SCORE"] = _to_float_range(
        data.get("TEMPLATE_SOLO_SCORE"), d["TEMPLATE_SOLO_SCORE"], 0.80, 1.0)
    data["WALL_WEIGHT_PRICE"] = _to_float_range(data.get("WALL_WEIGHT_PRICE"), d["WALL_WEIGHT_PRICE"], 0.0, 1.0)
    data["WALL_WEIGHT_ID"] = _to_float_range(data.get("WALL_WEIGHT_ID"), d["WALL_WEIGHT_ID"], 0.0, 1.0)
    data["WALL_MIN_ID_SCORE"] = _to_float_range(data.get("WALL_MIN_ID_SCORE"), d["WALL_MIN_ID_SCORE"], 0.0, 1.0)
    data["WALL_MIN_VARIANT_VOTES"] = _to_positive_int(
        data.get("WALL_MIN_VARIANT_VOTES"), d["WALL_MIN_VARIANT_VOTES"])
    data["WALL_CARD_XN_EXCLUDE_RATIO"] = _normalize_ratio_range(
        data.get("WALL_CARD_XN_EXCLUDE_RATIO"), d["WALL_CARD_XN_EXCLUDE_RATIO"])
    data["WALL_MENU_MAX_SWIPES"] = _to_positive_int(
        data.get("WALL_MENU_MAX_SWIPES"), d["WALL_MENU_MAX_SWIPES"])
    data["WALL_MENU_SWIPE_DURATION"] = _to_positive_int(
        data.get("WALL_MENU_SWIPE_DURATION"), d["WALL_MENU_SWIPE_DURATION"])
    data["DEPLOY_CARDS_CONFIG"] = _normalize_deploy_cards(data.get("DEPLOY_CARDS_CONFIG"))
    data["SPELL_CONFIG"] = _normalize_spell_config(data.get("SPELL_CONFIG"))
    # Thiết lập giao diện: thiếu / sai kiểu -> giá trị mặc định ở đầu config.py
    data["LDPLAYER_PATH"] = _to_text(data.get("LDPLAYER_PATH"), d["LDPLAYER_PATH"])
    data["LDPLAYER_INDEX"] = _to_nonneg_int(data.get("LDPLAYER_INDEX"), d["LDPLAYER_INDEX"])
    for key in ("MIN_GOLD", "MIN_ELIXIR", "MIN_DARK_ELIXIR"):
        data[key] = _to_threshold(data.get(key), d[key])
    mode = str(data.get("LOOT_MATCH_MODE", d["LOOT_MATCH_MODE"])).strip().upper()
    data["LOOT_MATCH_MODE"] = mode if mode in LOOT_MATCH_MODES else d["LOOT_MATCH_MODE"]
    data["ZOOM_ENABLE_CTRL_WHEEL"] = _to_bool(
        data.get("ZOOM_ENABLE_CTRL_WHEEL"), d["ZOOM_ENABLE_CTRL_WHEEL"])
    data["STRATEGY"] = _to_text(data.get("STRATEGY"), d["STRATEGY"])
    data["DEPLOY_MODE"] = _to_text(data.get("DEPLOY_MODE"), d["DEPLOY_MODE"])
    data["SPELL_MODE"] = _to_text(data.get("SPELL_MODE"), d["SPELL_MODE"])
    data["SPELL_ENABLED"] = _to_bool(data.get("SPELL_ENABLED"), d["SPELL_ENABLED"])
    data["OCR_ONLY_FOR_WALL"] = _to_bool(data.get("OCR_ONLY_FOR_WALL"), d["OCR_ONLY_FOR_WALL"])
    return data


def get_json_mtime():
    """Thời điểm sửa file config.json gần nhất (None nếu chưa có file)."""
    try:
        return os.path.getmtime(CONFIG_JSON_PATH)
    except OSError:
        return None


def load_json_config(force=False):
    """
    Đọc config.json (có cache theo thời gian sửa file nên gọi mỗi vòng lặp vẫn nhẹ).
    File thiếu / hỏng -> trả về giá trị mặc định, không bao giờ ném lỗi.
    """
    with _json_lock:
        mtime = get_json_mtime()
        if (not force and _json_cache["data"] is not None
                and _json_cache["mtime"] == mtime):
            return dict(_json_cache["data"])

        raw = {}
        if mtime is not None:
            try:
                with open(CONFIG_JSON_PATH, "r", encoding="utf-8") as f:
                    raw = json.load(f)
            except (OSError, ValueError):
                raw = {}

        data = _normalize_json_config(raw)
        _json_cache["mtime"] = mtime
        _json_cache["data"] = data
        return dict(data)


def save_json_config(updates):
    """
    Gộp `updates` vào config.json (giữ nguyên các khóa khác như ROI do crop_tool lưu).
    Ghi an toàn qua file tạm. Trả về True nếu thành công.
    """
    with _json_lock:
        try:
            current = {}
            if os.path.exists(CONFIG_JSON_PATH):
                try:
                    with open(CONFIG_JSON_PATH, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                    if isinstance(loaded, dict):
                        current = loaded
                except ValueError:
                    # File hỏng: sao lưu lại để không mất dữ liệu cũ
                    shutil.copyfile(CONFIG_JSON_PATH, CONFIG_JSON_PATH + ".bak")

            current.update(updates)
            tmp_path = CONFIG_JSON_PATH + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(current, f, indent=4, ensure_ascii=False)
            os.replace(tmp_path, CONFIG_JSON_PATH)

            _json_cache["data"] = None   # buộc lần đọc sau nạp lại từ file
            return True
        except OSError:
            return False


# Khoảng nghỉ tối thiểu giữa 2 lần chụp ADB: config.json được ưu tiên hơn hằng số mặc định ở đầu file.
# (adb_controller.py import giá trị này lúc khởi động.)
try:
    SCREENSHOT_MIN_INTERVAL = load_json_config()["SCREENSHOT_MIN_INTERVAL"]
except Exception:
    pass