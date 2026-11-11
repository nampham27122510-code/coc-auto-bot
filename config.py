import copy
import json
import os
import shutil
import sys
import threading

# ============================================================
# CẤU HÌNH LDPLAYER & ADB
# ============================================================

def _detect_ldplayer_path():
    """Dò thư mục LDPlayer 9 ở các vị trí cài phổ biến (có adb.exe). Không thấy -> ổ D mặc định."""
    candidates = [
        r"D:\LDPlayer\LDPlayer9", r"C:\LDPlayer\LDPlayer9", r"E:\LDPlayer\LDPlayer9",
        r"C:\Program Files\LDPlayer\LDPlayer9", r"D:\Program Files\LDPlayer\LDPlayer9",
    ]
    for path in candidates:
        if os.path.isfile(os.path.join(path, "adb.exe")):
            return path
    return candidates[0]


# Đường dẫn cài đặt LDPlayer 9 mặc định (tự dò; người dùng đổi trên GUI -> lưu trong config.json)
LDPLAYER_PATH = _detect_ldplayer_path()

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

# Thư mục chứa file .exe khi đã đóng gói (PyInstaller); khi chạy từ mã nguồn thì là thư mục của config.py.
# config.json LUÔN nằm ở đây (cạnh COC_Auto_Bot.exe) để không mất khi app chạy / cập nhật.
# Không dùng __file__ cho việc này: bản PyInstaller __file__ trỏ vào thư mục tạm / _internal.
APP_DIR = (os.path.dirname(os.path.abspath(sys.executable))
           if getattr(sys, "frozen", False) else BASE_DIR)

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

CONFIG_JSON_PATH = os.path.join(APP_DIR, "config.json")

# Tăng số này khi ĐỔI CẤU TRÚC một khóa cũ (thêm khóa mới thì không cần: khóa thiếu tự được bổ sung),
# rồi thêm hàm chuyển đổi vào _MIGRATIONS bên dưới.
CONFIG_VERSION = 1

# Thư mục (trong assets/) chứa ảnh nút xử lý LUỒNG LẠ: anomaly_btn_1.png, anomaly_btn_2.png ...
# (tools/crop_tool.py mục 22 tự lưu + tự cập nhật ANOMALY_BUTTON_IMAGES bên dưới)
ANOMALY_ASSET_DIR = "anomaly"

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
    "CONFIG_VERSION": CONFIG_VERSION,  # phiên bản cấu trúc file (dùng cho _MIGRATIONS)
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
    "WALL_MATCH_THRESHOLD": 0.95,         # 0.45-0.95; CHỈ dùng cho việc quét thẻ Tường trong menu Búa (càng NHỎ càng nhạy)
    # --- BỎ PHIẾU NHIỀU ẢNH: 1 ảnh khớp là chưa đủ, phải có nhiều bằng chứng độc lập cùng đồng ý ---
    "TEMPLATE_MIN_VOTES": 2,              # nút UI có >= 2 ảnh biến thể: cần ít nhất ngần này ảnh KHỚP CÙNG VỊ TRÍ (1 = tắt bỏ phiếu)
    "TEMPLATE_VOTE_TOL": 14,              # px: các ảnh khớp cách nhau <= ngần này coi là CÙNG một vị trí
    "TEMPLATE_SOLO_SCORE": 0.95,          # chỉ 1 ảnh khớp nhưng điểm >= ngần này (gần như trùng pixel) vẫn chấp nhận
    # Điểm thẻ Tường = trung bình có trọng số của 3 thành phần (tự chuẩn hóa, 0 = bỏ thành phần đó). Chỉnh trên UI.
    "WALL_WEIGHT_IMAGE": 1.0,             # trọng số điểm ẢNH mẫu (làng + cấp + tài nguyên đã chọn)
    "WALL_WEIGHT_TEXT": 1.0,              # trọng số điểm CHỮ "Tường" (OCR)
    "WALL_WEIGHT_PRICE": 0.6,             # trọng số phần giá + icon Vàng/Dầu (chữ đỏ thiếu tài nguyên vẫn tính là có giá)
    "WALL_MIN_ID_SCORE": 0.6,             # điểm SÀN của phần ẢNH (0-1): thấp hơn thì loại dù giá/chữ cao
    # Bộ ảnh mẫu dùng làm "thang chấm điểm" = đúng LÀNG + đúng CẤP + đúng TÀI NGUYÊN (theo tên file wall_item_<main|sub>_<gold|elixir>_lv<N>.png)
    "WALL_LEVEL_MAIN": 0,                 # cấp Tường LÀNG CHÍNH đang nâng; 0 = không lọc theo cấp (nhận mọi cấp)
    "WALL_LEVEL_SUB": 0,                  # cấp Tường LÀNG PHỤ đang nâng; 0 = không lọc theo cấp
    "WALL_RESOURCE": "AUTO",              # "AUTO" (cả Vàng + Dầu) | "GOLD" | "ELIXIR": chỉ dùng ảnh mẫu / chỉ nhận thẻ của tài nguyên này
    "WALL_MIN_VARIANT_VOTES": 2,          # số ảnh biến thể wall_item_* phải cùng khớp 1 dòng để tính là 'nhiều ảnh đồng thuận'
    # Vùng GIỮA thẻ Tường bị "đục lỗ" (mask) khi khớp ảnh mẫu card_wall.png để bỏ chữ số xN (x152, x150...)
    # [bắt đầu, kết thúc] tính theo tỉ lệ chiều rộng thẻ (0.0-1.0): 0.40 = 40%, 0.70 = 70%
    "WALL_CARD_XN_EXCLUDE_RATIO": [0.40, 0.70],
    "WALL_MENU_MAX_SWIPES": 6,            # số lần vuốt menu tối đa để tìm thẻ Tường
    "WALL_MENU_SWIPE_DURATION": 250,      # mili giây mỗi lần vuốt (khớp WALL_MENU_SWIPE_DURATION của bot_logic.py)
    "WALL_MENU_SWIPE_MULT": 1.5,          # nhân quãng đường vuốt trục Y (0.5-3.0)
    # --- Nâng Tường liên tục ---
    "WALL_UPGRADE_CONTINUOUS": True,
    "WALL_STOP_MODE": "ANY",              # "ANY" | "ALL"
    "WALL_CONTINUOUS_SAFETY_CAP": 300,    # tối đa bấy nhiêu khúc Tường / 1 lần mở menu
    # --- Đứng yên quá lâu: bấm nút có điểm khớp cao nhất ---
    "IDLE_CLICK_BEST_MATCH": True,
    "IDLE_CLICK_MIN_SCORE": 0.2,
    # --- Vùng nhận diện nút (hệ 960x540, [x1, y1, x2, y2]) ---
    "NEXT_BTN_ROI": [640, 330, 960, 540],
    "NEXT_MATCH_SCALES": [1.0],
    "SURRENDER_BTN_ROI": [0, 355, 190, 455],
    "CONFIRM_SURRENDER_ROI": [240, 180, 740, 460],
    # --- OCR nhanh (đọc ở image_processor) ---
    "OCR_FAST_MODE": True,
    "OCR_FAST_SCALE": 2.0,
    "OCR_FAST_THRESH": 175,
    "OCR_USE_TESSEROCR": True,
    # --- Xử lý LUỒNG LẠ / popup không xác định (sự kiện, bảo trì, nút X / Hủy / Xác nhận...) ---
    # Thứ tự: (1) quét danh sách ảnh nút này -> (2) bấm FALLBACK_SAFE_CLICK_POS -> (3-4) mở lại game.
    "ANOMALY_BUTTON_IMAGES": [],          # danh sách ảnh nút (đường dẫn tương đối assets/ hoặc tuyệt đối); thêm / bớt N ảnh tùy ý
    "ANOMALY_MATCH_THRESHOLD": 0.85,      # ngưỡng khớp OpenCV RIÊNG cho ảnh nút luồng lạ (0.50-0.99)
    "ANOMALY_SCAN_RETRIES": 2,            # số lần quét lại toàn bộ danh sách ảnh trước khi chuyển sang bấm tọa độ an toàn
    "ANOMALY_SETTLE_SECONDS": 1.5,        # nghỉ sau mỗi cú bấm để màn hình ổn định rồi mới kiểm tra đã về Làng chính chưa
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
    # --- KIỂU THẢ / COMBO THẢ do người dùng đặt tên (UI: tab Tọa độ & Thẻ quân) ---
    # Vị trí + loại thẻ quân đặt 1 lần (DEPLOY_CARDS_CONFIG); mỗi kiểu thả chỉ lưu phần TỌA ĐỘ THẢ + số lần bấm + kiểu thả + kỹ năng Tướng.
    # Chọn kiểu nào thì UI ghi phần đó vào các khóa đang chạy (DEPLOY_MODE, DEPLOY_CARDS_CONFIG, MANUAL_POSITIONS, SPELL_CONFIG)
    # -> bot vẫn đọc các khóa cũ như trước, không phải sửa gì.
    "DEPLOY_PRESETS": {},                 # {tên: {"mode", "cards": [{slot, clicks_count, zones, skill_mode, skill_delay}], "singles", "spell"}}
    "DEPLOY_PRESET_ACTIVE": "",           # tên kiểu thả đang dùng
    "DEPLOY_CARDS_LOCKED": False,         # True = khóa vị trí/loại thẻ quân (đã setup xong), chỉ chỉnh tọa độ thả
    # Thẻ LƯỢT 2 (Làng phụ): [{"slot": N, "pos": [x, y] (tùy chọn: bỏ trống = theo thẻ cùng số), "type": "troop|hero" (tùy chọn),
    #   "clicks_count": N (tùy chọn), "drop": [[x, y], ...] (tùy chọn: điểm thả riêng, dùng cho kiểu thả nhiều điểm; thẻ tiếp viện 8-9 không có thẻ cùng số ở lượt 1)}]
    "ROUND2_CARDS": [],
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


_DEPLOY_ZONE_MODES = ("left_corner", "right_corner", "spread_left", "spread_right", "random_around")
_SKILL_MODES = ("none", "immediate", "delay")


def _normalize_card_zones(value):
    """zones = {kiểu thả: [[x, y], ...]}; bỏ kiểu lạ, điểm sai và danh sách rỗng."""
    out = {}
    if isinstance(value, dict):
        for mode, pts in value.items():
            if mode not in _DEPLOY_ZONE_MODES or not isinstance(pts, list):
                continue
            good = [p for p in (_valid_point(v) for v in pts) if p]
            if good:
                out[mode] = good
    return out


def _normalize_deploy_cards(value):
    """
    Ép DEPLOY_CARDS_CONFIG thành list thẻ hợp lệ; thẻ sai bị bỏ. Không có thẻ nào -> mặc định.
    Loại thẻ: troop / hero / spell. Giữ thêm các trường thả riêng của từng thẻ:
      zones (tọa độ thả theo kiểu), skill_mode / skill_delay / skill_pos (kỹ năng Tướng).
    """
    cards = []
    if isinstance(value, list):
        for i, item in enumerate(value, 1):
            if not isinstance(item, dict):
                continue
            pos = _valid_point(item.get("pos"))
            kind = str(item.get("type", "")).strip().lower()
            if pos is None or kind not in ("troop", "hero", "spell"):
                continue
            card = {
                "slot": _to_positive_int(item.get("slot"), i),
                "pos": pos,
                "type": kind,
                "clicks_count": _to_positive_int(item.get("clicks_count"), 1),
                "zones": _normalize_card_zones(item.get("zones")),
            }
            skill_mode = str(item.get("skill_mode", "none")).strip().lower()
            card["skill_mode"] = skill_mode if skill_mode in _SKILL_MODES else "none"
            try:
                delay = float(item.get("skill_delay", 0) or 0)
            except (TypeError, ValueError):
                delay = 0.0
            card["skill_delay"] = delay if delay >= 0 else 0.0
            card["skill_pos"] = _valid_point(item.get("skill_pos")) or []
            cards.append(card)
    if not cards and not isinstance(value, list):
        return [dict(c) for c in JSON_CONFIG_DEFAULTS["DEPLOY_CARDS_CONFIG"]]
    return cards


def _normalize_preset(value):
    """1 kiểu thả -> dict sạch; sai cấu trúc -> None."""
    if not isinstance(value, dict):
        return None
    mode = str(value.get("mode", "")).strip()
    if mode not in _DEPLOY_ZONE_MODES:
        mode = JSON_CONFIG_DEFAULTS["DEPLOY_MODE"]
    cards = []
    for i, item in enumerate(value.get("cards") or [], 1):
        if not isinstance(item, dict):
            continue
        sk = str(item.get("skill_mode", "none")).strip().lower()
        try:
            delay = max(0.0, float(item.get("skill_delay", 0) or 0))
        except (TypeError, ValueError):
            delay = 0.0
        cards.append({"slot": _to_positive_int(item.get("slot"), i),
                      "clicks_count": _to_positive_int(item.get("clicks_count"), 1),
                      "zones": _normalize_card_zones(item.get("zones")),
                      "skill_mode": sk if sk in _SKILL_MODES else "none",
                      "skill_delay": delay})
    singles = {}
    src = value.get("singles") if isinstance(value.get("singles"), dict) else {}
    for key in ("left_corner", "right_corner"):
        p = _valid_point(src.get(key))
        if p:
            singles[key] = p
    return {"mode": mode, "cards": cards, "singles": singles,
            "spell": _normalize_spell_config(value.get("spell"))}


def _normalize_presets(value):
    out = {}
    if isinstance(value, dict):
        for name, item in value.items():
            name = str(name).strip()
            preset = _normalize_preset(item)
            if name and preset is not None:
                out[name] = preset
    return out


def _normalize_round2_cards(value):
    """ROUND2_CARDS: slot >= 1; pos / type / clicks_count là tùy chọn. Slot trùng -> giữ mục đầu."""
    out, seen = [], set()
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            slot = _to_positive_int(item.get("slot"), 0)
            if slot < 1 or slot in seen:
                continue
            seen.add(slot)
            card = {"slot": slot}
            pos = _valid_point(item.get("pos"))
            if pos:
                card["pos"] = pos
            kind = str(item.get("type", "")).strip().lower()
            if kind in ("troop", "hero"):
                card["type"] = kind
            if item.get("clicks_count") not in (None, "", 0):
                card["clicks_count"] = _to_positive_int(item.get("clicks_count"), 1)
            drop = item.get("drop")
            if isinstance(drop, list):
                pts = [p for p in (_valid_point(v) for v in drop) if p]
                if pts:
                    card["drop"] = pts
            out.append(card)
    return out


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


def _normalize_anomaly_images(value):
    """Danh sách đường dẫn ảnh nút luồng lạ: chỉ giữ chuỗi không rỗng, bỏ trùng, giữ thứ tự, dùng dấu '/'."""
    if not isinstance(value, (list, tuple)):
        return []
    out, seen = [], set()
    for item in value:
        if not isinstance(item, str):
            continue
        p = item.strip().replace("\\", "/")
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


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
    data["WALL_WEIGHT_IMAGE"] = _to_float_range(data.get("WALL_WEIGHT_IMAGE"), d["WALL_WEIGHT_IMAGE"], 0.0, 1.0)
    data["WALL_WEIGHT_TEXT"] = _to_float_range(data.get("WALL_WEIGHT_TEXT"), d["WALL_WEIGHT_TEXT"], 0.0, 1.0)
    data["WALL_LEVEL_MAIN"] = _to_nonneg_int(data.get("WALL_LEVEL_MAIN"), d["WALL_LEVEL_MAIN"])
    data["WALL_LEVEL_SUB"] = _to_nonneg_int(data.get("WALL_LEVEL_SUB"), d["WALL_LEVEL_SUB"])
    _res = str(data.get("WALL_RESOURCE", d["WALL_RESOURCE"])).strip().upper()
    data["WALL_RESOURCE"] = _res if _res in ("AUTO", "GOLD", "ELIXIR") else d["WALL_RESOURCE"]
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
    data["DEPLOY_PRESETS"] = _normalize_presets(data.get("DEPLOY_PRESETS"))
    data["DEPLOY_PRESET_ACTIVE"] = str(data.get("DEPLOY_PRESET_ACTIVE") or "").strip()
    data["DEPLOY_CARDS_LOCKED"] = _to_bool(data.get("DEPLOY_CARDS_LOCKED"), d["DEPLOY_CARDS_LOCKED"])
    data["ROUND2_CARDS"] = _normalize_round2_cards(data.get("ROUND2_CARDS"))
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
    # Nâng Tường liên tục / chạm khi đứng yên / ROI nút / OCR nhanh
    data["WALL_MENU_SWIPE_MULT"] = _to_float_range(
        data.get("WALL_MENU_SWIPE_MULT"), d["WALL_MENU_SWIPE_MULT"], 0.5, 3.0)
    data["WALL_UPGRADE_CONTINUOUS"] = _to_bool(
        data.get("WALL_UPGRADE_CONTINUOUS"), d["WALL_UPGRADE_CONTINUOUS"])
    stop_mode = str(data.get("WALL_STOP_MODE", d["WALL_STOP_MODE"])).strip().upper()
    data["WALL_STOP_MODE"] = stop_mode if stop_mode in ("ANY", "ALL") else d["WALL_STOP_MODE"]
    data["WALL_CONTINUOUS_SAFETY_CAP"] = _to_positive_int(
        data.get("WALL_CONTINUOUS_SAFETY_CAP"), d["WALL_CONTINUOUS_SAFETY_CAP"])
    data["IDLE_CLICK_BEST_MATCH"] = _to_bool(
        data.get("IDLE_CLICK_BEST_MATCH"), d["IDLE_CLICK_BEST_MATCH"])
    data["IDLE_CLICK_MIN_SCORE"] = _to_float_range(
        data.get("IDLE_CLICK_MIN_SCORE"), d["IDLE_CLICK_MIN_SCORE"], 0.0, 1.0)
    for key in ("NEXT_BTN_ROI", "SURRENDER_BTN_ROI", "CONFIRM_SURRENDER_ROI"):
        data[key] = _valid_roi(data.get(key)) or list(d[key])
    scales = data.get("NEXT_MATCH_SCALES")
    good_scales = []
    if isinstance(scales, (list, tuple)):
        good_scales = [v for v in (_to_positive_float(x, None) for x in scales) if v]
    data["NEXT_MATCH_SCALES"] = good_scales or list(d["NEXT_MATCH_SCALES"])
    data["OCR_FAST_MODE"] = _to_bool(data.get("OCR_FAST_MODE"), d["OCR_FAST_MODE"])
    data["OCR_FAST_SCALE"] = _to_positive_float(data.get("OCR_FAST_SCALE"), d["OCR_FAST_SCALE"])
    data["OCR_FAST_THRESH"] = int(_to_float_range(data.get("OCR_FAST_THRESH"), d["OCR_FAST_THRESH"], 0, 255))
    data["OCR_USE_TESSEROCR"] = _to_bool(data.get("OCR_USE_TESSEROCR"), d["OCR_USE_TESSEROCR"])
    # Luồng lạ / popup không xác định
    data["ANOMALY_BUTTON_IMAGES"] = _normalize_anomaly_images(data.get("ANOMALY_BUTTON_IMAGES"))
    data["ANOMALY_MATCH_THRESHOLD"] = _to_float_range(
        data.get("ANOMALY_MATCH_THRESHOLD"), d["ANOMALY_MATCH_THRESHOLD"], 0.50, 0.99)
    data["ANOMALY_SCAN_RETRIES"] = _to_positive_int(
        data.get("ANOMALY_SCAN_RETRIES"), d["ANOMALY_SCAN_RETRIES"])
    data["ANOMALY_SETTLE_SECONDS"] = _to_float_range(
        data.get("ANOMALY_SETTLE_SECONDS"), d["ANOMALY_SETTLE_SECONDS"], 0.3, 10.0)
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


def _write_json_atomic(data):
    """Ghi `data` ra config.json qua file tạm + os.replace (tắt app giữa chừng cũng không hỏng file)."""
    tmp_path = CONFIG_JSON_PATH + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, CONFIG_JSON_PATH)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def _fresh_config():
    """Bộ cấu hình mặc định đầy đủ (đã chuẩn hóa) kèm CONFIG_VERSION, dùng khi tạo file mới."""
    data = _normalize_json_config({})
    data["CONFIG_VERSION"] = CONFIG_VERSION
    return data


def _backup_config(suffix=".bak"):
    try:
        shutil.copyfile(CONFIG_JSON_PATH, CONFIG_JSON_PATH + suffix)
    except OSError:
        pass


def save_json_config(updates):
    """
    Gộp `updates` vào config.json (giữ nguyên các khóa khác như ROI do crop_tool lưu).
    Ghi an toàn qua file tạm. Trả về True nếu thành công.
    """
    with _json_lock:
        try:
            if os.path.exists(CONFIG_JSON_PATH):
                current = {}
                try:
                    with open(CONFIG_JSON_PATH, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                    if isinstance(loaded, dict):
                        current = loaded
                except ValueError:
                    # File hỏng: sao lưu lại để không mất dữ liệu cũ
                    _backup_config()
            else:
                # Chưa có file (người dùng xóa tay): bắt đầu từ bộ mặc định đầy đủ, không phải file "trơ"
                current = copy.deepcopy(JSON_CONFIG_DEFAULTS)

            current.update(updates)
            _write_json_atomic(current)

            _json_cache["data"] = None   # buộc lần đọc sau nạp lại từ file
            return True
        except (OSError, TypeError, ValueError):
            return False


# Chuyển đổi cấu trúc giữa các phiên bản: {phiên bản đích: hàm(raw_dict) sửa tại chỗ}.
# Ví dụ khi CONFIG_VERSION = 2 đổi tên một khóa:  _MIGRATIONS[2] = lambda raw: raw.update(NEW=raw.pop("OLD", None))
_MIGRATIONS = {}


def ensure_json_config():
    """
    Gọi 1 lần lúc khởi động (cuối file này tự gọi). Đảm bảo config.json cạnh .exe luôn có và đủ khóa:
      - Chưa có file (lần chạy đầu)      -> tạo mới từ JSON_CONFIG_DEFAULTS            -> "created"
      - File hỏng / không phải JSON object -> sao lưu .bak rồi tạo lại từ mặc định     -> "reset"
      - Có file, bản mới thêm khóa mới    -> CHỈ bổ sung khóa thiếu, giữ nguyên mọi giá trị người dùng -> "updated"
      - Có file, đủ khóa                  -> không ghi gì                               -> "ok"
    Không bao giờ ghi đè giá trị người dùng đã chỉnh. Lỗi ghi file -> "error" (bot vẫn chạy bằng mặc định).
    """
    with _json_lock:
        try:
            if not os.path.exists(CONFIG_JSON_PATH):
                _write_json_atomic(_fresh_config())
                _json_cache["data"] = None
                return "created"

            try:
                with open(CONFIG_JSON_PATH, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if not isinstance(raw, dict):
                    raise ValueError("config.json không phải JSON object")
            except ValueError:
                _backup_config()
                _write_json_atomic(_fresh_config())
                _json_cache["data"] = None
                return "reset"

            changed = False
            try:
                version = int(raw.get("CONFIG_VERSION", 0))
            except (TypeError, ValueError):
                version = 0
            for target in sorted(_MIGRATIONS):
                if version < target:
                    _MIGRATIONS[target](raw)
                    changed = True
            norm = _normalize_json_config(raw)   # đã xử lý cả khóa cũ (rois, WALL_UPGRADE_BTN...)
            for key, default in JSON_CONFIG_DEFAULTS.items():
                if key == "CONFIG_VERSION":
                    continue
                if key not in raw:
                    raw[key] = norm.get(key, copy.deepcopy(default))
                    changed = True
            # MANUAL_POSITIONS lồng nhau: bổ sung từng tọa độ còn thiếu, không đụng tọa độ đã có
            manual = raw.get("MANUAL_POSITIONS")
            if isinstance(manual, dict):
                for key, value in norm["MANUAL_POSITIONS"].items():
                    if key not in manual:
                        manual[key] = value
                        changed = True
            if raw.get("CONFIG_VERSION") != CONFIG_VERSION:
                raw["CONFIG_VERSION"] = CONFIG_VERSION
                changed = True

            if not changed:
                return "ok"
            _backup_config(".prev")   # giữ bản trước khi nâng cấp phòng khi cần quay lại
            _write_json_atomic(raw)
            _json_cache["data"] = None
            return "updated"
        except (OSError, TypeError, ValueError):
            return "error"


# Khoảng nghỉ tối thiểu giữa 2 lần chụp ADB: config.json được ưu tiên hơn hằng số mặc định ở đầu file.
# (adb_controller.py import giá trị này lúc khởi động.)
CONFIG_INIT_STATUS = ensure_json_config()   # "created" | "reset" | "updated" | "ok" | "error"

try:
    SCREENSHOT_MIN_INTERVAL = load_json_config()["SCREENSHOT_MIN_INTERVAL"]
except Exception:
    pass