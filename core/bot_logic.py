# -*- coding: utf-8 -*-
"""
core/bot_logic.py
-----------------
"Bộ não" điều phối của Auto Bot Clash of Clans (LDPlayer 9).

Lớp BotLogic đảm nhiệm các nhiệm vụ chính trong vòng lặp chính (main.py):
  1) check_connection_error()   : Self-Healing khi game báo mất kết nối ("THỬ LẠI"); phần bấm popup
                                  nằm ở handle_connection_error(); kẹt quá STUCK_TIMEOUT -> check_stuck_watchdog().
  2) process_home_village()     : Ở làng nhà: đọc thợ rảnh AN TOÀN; vừa vào game nâng Tường ngay,
                                  thiếu tài nguyên thì đi cướp, cứ 5 trận lại thử nâng Tường.
  3) run_wall_upgrade_cycle()   : Búa -> vuốt menu -> dòng Tường (can_upgrade) -> verify_wall_popup ->
                                  Nâng bằng Vàng/Dầu -> Xác nhận -> đóng menu.
  4) check_and_search_match()   : Chiến đấu -> Tìm trận 1700 -> Tấn công! -> soi nhà địch -> thả quân / Next.
  5) execute_deployment()       : Thả HẾT lính & phép (DEPLOY_SEQUENCE -> scan_battle_bar -> thẻ thủ công),
                                  chờ trận, quét button_return_home*.png / nút xanh HSV -> Về nhà.

Cấu hình động (ROI, bật/tắt xả tường, ngưỡng Vàng/Dầu) được đọc từ config.json
thông qua config.load_json_config(); các hằng số tọa độ nằm trong config.py.

Quy ước: mọi hàm check_* / process_* đều nhận ảnh chụp màn hình `screen`
(OpenCV BGR) do luồng chính chụp một lần mỗi vòng lặp, tránh chụp lặp nhiều lần.
"""

import gc
import glob
import os
import queue
import random
import re
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import cv2
import numpy as np

from core.image_processing import (
    ImageProcessor, asset, compute_frame_fingerprint, frame_signature,
)
from config import (
    BUILD_BUTTON_POS,
    BUILDER_MENU_SCROLL_START,
    BUILDER_MENU_SCROLL_END,
    BUILDER_MENU_SCROLL_DURATION,
    WALL_ITEM_POS,
    FIRST_UPGRADE_POS,
    CONFIRM_UPGRADE_POS,
    CLOSE_MENU_POS,
    WALL_UPGRADE_MAX_PER_CYCLE,
    DEPLOY_SWIPE_1,
    DEPLOY_SWIPE_2,
    HERO_DEPLOY_POS,
    ADB_PATH,
    ADB_PORT,
    EMULATOR_INDEX,
    ZOOM_OUT_SWIPE_A,
    ZOOM_OUT_SWIPE_B,
    ZOOM_OUT_DURATION_MS,
    ZOOM_OUT_REPEAT,
    LOOP_SLEEP,
    GC_INTERVAL_SECONDS,
    load_json_config,
)

# --- Popup nâng Tường theo từng cấp ---
# Ảnh mẫu cắt bằng tools/crop_tool.py (mục 12-21). Nút SÁNG = đủ tài nguyên; nút TỐI
# (thiếu tài nguyên, chữ đỏ) không khớp ảnh mẫu -> chính điều đó cho biết không đủ.
# Thứ tự trong danh sách = thứ tự ưu tiên quét dòng Tường trong menu đề xuất.
# (tên, ảnh mẫu, loại tài nguyên) - loại tài nguyên quyết định bấm nút Nâng bằng Vàng hay bằng Dầu
WALL_ITEM_TEMPLATES = [
    ("Tường Cấp A - Vàng", "wall_item_lvA_gold.png",   "gold"),
    ("Tường Cấp A - Dầu",  "wall_item_lvA_elixir.png", "elixir"),
    ("Tường Cấp B - Vàng", "wall_item_lvB_gold.png",   "gold"),
    ("Tường Cấp B - Dầu",  "wall_item_lvB_elixir.png", "elixir"),
]
WALL_ITEM_THRESHOLD = 0.75   # ngưỡng khớp dòng Tường trong menu đề xuất
WALL_ITEM_LEFT_RATIO = 0.42    # phần TRÁI ảnh mẫu dùng để nhận ra dòng Tường (icon + tên)
WALL_ITEM_RIGHT_RATIO = 0.30   # phần PHẢI dùng để nhận ra loại tài nguyên + đủ/thiếu (giá + icon)
                               # phần GIỮA (số lượng "xN", thay đổi sau mỗi lần nâng) bị bỏ qua
WALL_RIGHT_TOL = 8             # cho phép phần phải lệch tối đa ngần này px so với vị trí kỳ vọng
WALL_ROW_CANDIDATES = 4        # số vị trí dòng tối đa thử cho mỗi ảnh mẫu
WALL_POPUP_DELAY = 0.8       # chờ popup nâng cấp / xác nhận hiện ra (giây)

# Lịch nâng Tường: vừa vào game là thử nâng ngay; không thấy dòng Tường (thiếu tài nguyên) thì
# đóng menu, đi đánh WALL_RETRY_BATTLES_LIMIT trận (config.json) rồi mới mở menu thử lại.
WALL_RETRY_BATTLES_LIMIT = 5   # giá trị mặc định nếu config.json không ghi
IDLE_CLICK_AFTER = 3           # số lần ghi log 'không khớp nút nào' liên tiếp (~5s/lần) trước khi bấm nút điểm cao nhất



# --- Chế độ Cào dữ liệu (Collect Data Mode) ---
# Thư mục gốc dự án = cha của thư mục core/ (không phụ thuộc thư mục chạy lệnh)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATASET_DIR = os.path.join(PROJECT_ROOT, "dataset")
RAW_LOOT_DIR = os.path.join(DATASET_DIR, "raw_loot")        # ảnh vùng tài nguyên địch
RAW_BUILDER_DIR = os.path.join(DATASET_DIR, "raw_builder")  # dành cho cào ảnh số thợ xây (sau này)
BOT_REF_SIZE = (960, 540)       # độ phân giải chuẩn nếu config.json không ghi "screen_size"
COLLECT_DEFAULT_MAX = 100
# Chờ nhà mới sau khi bấm Next: thăm dò ảnh thay vì ngủ cố định
COLLECT_POLL_INTERVAL = 0.3        # nghỉ giữa 2 lần thăm dò (giây) - đủ dài để không spam ADB (chống BSoD)
COLLECT_CHANGE_THRESHOLD = 5.0     # độ lệch xám trung bình ROI so với nhà cũ để coi là "đã đổi nhà"
COLLECT_SETTLE_THRESHOLD = 2.5     # độ lệch tối đa giữa 2 khung liên tiếp để coi là "đã ổn định"
COLLECT_NEXT_TIMEOUT = 8.0         # quá thời gian này mà chưa thấy nhà mới -> trả về vòng chính


# --- Thả HẾT quân & phép (màn hình 960x540) ---
# (tên hiển thị, tọa độ slot trên thanh điều khiển, kiểu thả: "swipe" = vuốt rìa map, "tap" = bấm dồn dập)
DEPLOY_SLOTS = [
    ("Quân 1",      (220, 480), "swipe"),
    ("Quân 2",      (290, 480), "swipe"),
    ("Quân 3",      (360, 480), "swipe"),
    ("Quân 4",      (430, 480), "swipe"),
    ("Tướng (Hero)", (500, 480), "hero"),
    ("Phép (Spell)", (570, 480), "tap"),
]

# 3 đường vuốt dài dọc rìa map: 2 đường lấy từ config.py, đường thứ 3 lấy DEPLOY_SWIPE_3
# trong config.py (nếu có), không thì vuốt NGƯỢC lại đường 1 để phủ thêm cùng rìa đó.
try:
    from config import DEPLOY_SWIPE_3
except ImportError:
    DEPLOY_SWIPE_3 = None
DEPLOY_EDGE_SWIPES = [
    tuple(DEPLOY_SWIPE_1),
    tuple(DEPLOY_SWIPE_2),
    tuple(DEPLOY_SWIPE_3) if DEPLOY_SWIPE_3 else (
        DEPLOY_SWIPE_1[2], DEPLOY_SWIPE_1[3], DEPLOY_SWIPE_1[0], DEPLOY_SWIPE_1[1]),
]
DEPLOY_SWIPE_DURATION = 500     # ms mỗi đường vuốt
DEPLOY_TAP_COUNT = 8            # số lần bấm dồn dập cho slot kiểu "tap" (5-10)
DEPLOY_TAP_INTERVAL = 0.05       # giây giữa 2 lần bấm
DEPLOY_SLOT_DELAY = 0.02        # nghỉ giữa 2 slot
BATTLE_WAIT_MIN = 20            # chờ lính đánh sau khi thả hết (giây)
BATTLE_WAIT_MAX = 60
# --- Độ trễ giữa các bước đi tấn công (giảm so với bản cũ: 1s / 1.5s / 0.3s) ---
STEP_CLICK_DELAY = 0.4            # nghỉ sau khi bấm Chiến đấu / Tìm trận / Tấn công (vòng chính sẽ quét lại ngay)
NEXT_FALLBACK_DELAY = 0.8         # nghỉ sau khi bấm Next nếu không có ROI để dò nhà mới
NEXT_WAIT_TIMEOUT = 4.0           # dò nhà mới sau Next tối đa bấy nhiêu giây; hết giờ thì trả về vòng chính
DEPLOY_SELECT_DELAY = 0.08        # nghỉ sau khi chọn thẻ rồi mới thả (cũ 0.3s)
DEPLOY_GUARD_GAP = 15.0           # kiểm tra mất kết nối khi thả quân tối đa mỗi 15s (cũ: chụp ảnh MỖI thẻ; _wait_battle_end vẫn kiểm tra sau đó)
LOOP_SLEEP_CAP = 0.3              # trần nghỉ mỗi vòng lặp chính (LOOP_SLEEP trong config.py lớn hơn thì bị chặn ở đây)
CONN_CHECK_STATIC_SECONDS = 5.0   # chỉ dò popup mất kết nối khi màn hình ĐỨNG YÊN >= bấy nhiêu giây (kẹt)
STEP_POLL_TIMEOUT = 2.0           # sau khi bấm 1 bước: dò màn hình kế tiếp tối đa bấy nhiêu giây rồi đi tiếp NGAY
STEP_POLL_INTERVAL = 0.35         # nhịp dò màn hình kế tiếp (đủ thưa để không spam ADB chụp ảnh)
IDLE_REBOOT_SECONDS = 20          # kẹt ở màn hình không nhận ra (không nút nào khớp, không thấy popup) quá bấy nhiêu giây -> tắt + mở lại LDPlayer (config.json ghi đè)
STALL_DUMP_SECONDS = 10         # luồng bot không 'nhịp' quá bấy nhiêu giây -> ghi log đang kẹt ở dòng code nào
CONN_CHECK_GAP = 3.0              # ...hoặc khi ở màn hình LẠ; mỗi lần dò cách nhau tối thiểu bấy nhiêu giây
RETURN_HOME_POLL_INTERVAL = 0.7    # quét tìm button_return_home.png mỗi 5 giây (không bấm theo toạ độ)
HOME_VERIFY_INTERVAL = 0.5           # sau khi bấm 'Về nhà': quét xem đã về làng nhà (button_battle.png) chưa
HOME_VERIFY_TIMEOUT = 60           # quá thời gian này chưa thấy làng nhà -> trả quyền cho vòng lặp chính
BOOST_POLL_INTERVAL = 0.7          # khi chờ trận: quét nút Tăng tốc + Về nhà mỗi 5 giây
BOOST_THRESHOLD = 0.75             # ngưỡng khớp ảnh mẫu nút Tăng tốc (0.60 cũ khớp NHẦM vào ô quân dưới thanh quân; config.json -> BOOST_THRESHOLD ghi đè)
RETURN_HOME_THRESHOLD = 0.60       # ngưỡng khớp ảnh mẫu nút Về nhà / Về làng (hạ thấp cho dễ nhận)
BATTLE_MONITOR_TIMEOUT = 180       # _wait_battle_end: giám sát trận tối đa bấy nhiêu giây (không còn chờ cố định 20-60s)
BATTLE_POLL_MIN = 0.7              # mỗi vòng giám sát trận nghỉ trong [MIN, MAX] giây -> quét Tăng tốc + Về nhà 5s / lần
BATTLE_POLL_MAX = 0.7
BATTLE_CONFIRM_POLL = 0.5          # CHỈ khi vừa thấy ứng viên nút 'Về nhà': quét lại sau bấy nhiêu giây để xác nhận
BATTLE_WATCHDOG_GRACE = 300        # trong bấy nhiêu giây kể từ lúc bắt đầu thả quân: TẠM TẮT Stuck Watchdog (trận đánh tối đa ~3 phút,
                                   # màn hình đổi chậm nên Watchdog hay hiểu nhầm là kẹt -> bấm bậy / mở lại game giữa trận)
RETURN_HOME_CONFIRM_TEMPLATE = 1   # nút 'Về nhà' (ảnh mẫu) phải thấy liên tiếp ngần này khung mới tính là hết trận
RETURN_HOME_CONFIRM_HSV = 3        # nút xanh lá (HSV, dễ nhầm hơn) phải thấy liên tiếp ngần này khung
RETURN_HOME_CONFIRM_TOL = 30       # px: các khung liên tiếp phải thấy nút ở cùng chỗ
# Vùng KHÔNG tìm nút Tăng tốc (toạ độ 960x540, tô đen trước khi so khớp): thanh quân dưới đáy + nút Đầu hàng.
# config.json -> BOOST_EXCLUDE_ROIS = [[x1,y1,x2,y2], ...] ghi đè; BOOST_ROI = [x1,y1,x2,y2] = CHỈ tìm trong vùng này.
BOOST_EXCLUDE_ROIS = ((55, 398, 905, 540), (0, 365, 140, 432))
HOME_GREEN_HSV = ((35, 110, 110), (90, 255, 255))   # xanh lá của nút 'Về nhà / Về làng'
HOME_MIN_GREEN_RATIO = 0.25        # ảnh mẫu 'Về nhà' khớp tại đâu thì quanh đó phải có >= bấy nhiêu phần XANH LÁ (nút thật luôn xanh)
HOME_TRUST_SCORE = 0.92            # hoặc điểm khớp >= mức này thì tin luôn (không cần kiểm tra màu)
CODE_VERSION = "v5-2026-11-09"   # hiện trong log đầu mỗi trận: thấy khác phiên bản này nghĩa là chưa copy đúng file
BOOST_MIN_VOTES = 1                # nút Tăng tốc: 1 ảnh mẫu khớp là đủ (TEMPLATE_MIN_VOTES=2 làm bot bỏ qua nút vì chỉ 1/2 ảnh khớp)
HOME_GREEN_MATCH_TOL = 70          # px: vị trí ảnh mẫu 'Về nhà' lệch nút XANH LÁ quá mức này -> ảnh mẫu khớp NHẦM chỗ, dùng vị trí nút xanh
HOME_CLICK_ESCALATE_AFTER = 2      # bấm 'Về nhà' bấy nhiêu lần mà màn hình kết quả vẫn còn -> luân phiên thử nút xanh HSV (chỉ theo ảnh)
HOME_CLICK_DELAY = 0.0              # thấy nút 'Về nhà' -> bấm NGAY (0). Đặt > 0 để đợi bấy nhiêu giây rồi quét LẠI xác nhận nút còn đó mới bấm
BOOST_CLICK_DELAY = 0.0             # thấy nút 'Tăng tốc' -> bấm NGAY (0). Đặt > 0 để nút phải còn thấy liên tục bấy nhiêu giây mới bấm
HOME_VERIFY_WAIT = 10.0             # sau khi bấm 'Về nhà': chờ tối đa bấy nhiêu giây để thấy nút Búa/Chiến đấu ở làng (game cần thời gian tải)
HOME_RECLICK_AFTER = 3.0            # sau khi bấm bấy nhiêu giây mà vẫn thấy nút 'Về nhà' (chưa thấy nút Búa) -> quét lại, đợi 2s, bấm lại
DIAG_INTERVAL = 15.0               # giây: khi giám sát trận, log điểm khớp cao nhất của nút Tăng tốc / Về nhà (chẩn đoán)
# Nút ĐỎ góc dưới trái ("Kết thúc trận chiến" / "Đầu hàng") chỉ có khi trận ĐANG đánh (toạ độ 960x540).
# Thấy nút này thì chắc chắn chưa hết trận -> không được kết luận 'Về nhà' (ảnh mẫu dễ khớp nhầm nút đỏ này).
BATTLE_END_BTN_ROI = (10, 380, 132, 428)   # x1, y1, x2, y2
BATTLE_END_BTN_RED_RATIO = 0.20            # tỉ lệ điểm ảnh đỏ trong ROI để coi là có nút
ZOOM_OUT_STEPS = 8                 # số bước nội suy khi chụm 2 ngón (càng nhiều càng mượt)
# Zoom out: thử lần lượt các phương án cho tới khi màn hình thực sự đổi (không phụ thuộc getevent -p)
# Mặc định chỉ dùng Ctrl + cuộn chuột (LDPlayer cần giữ Ctrl rồi cuộn). "parallel" (swipe song song)
# thực chất chỉ kéo lệch camera nên KHÔNG để mặc định; muốn thử thì thêm vào cuối tuple.
ZOOM_METHOD_ORDER = ("sendevent",)      # multi-touch THẬT qua ADB. "parallel" (2 swipe rời) chỉ chạy khi bật ZOOM_ALLOW_PARALLEL_SWIPE
ZOOM_ALLOW_PARALLEL_SWIPE = False        # 2 lệnh `input swipe` KHÔNG phải multi-touch -> kéo camera giật; mặc định TẮT
ZOOM_PARALLEL_A = (200, 270, 430, 270)    # ngón 1: mép trái -> tâm
ZOOM_PARALLEL_B = (760, 270, 530, 270)    # ngón 2: mép phải -> tâm
ZOOM_PARALLEL_DURATION_MS = 600
ZOOM_CTRL_NOTCHES = 10                    # số nấc cuộn chuột khi dùng Ctrl + cuộn
ZOOM_ENABLE_CTRL_WHEEL = True             # mặc định TẮT (config.json -> ZOOM_ENABLE_CTRL_WHEEL ghi đè); tắt = không đụng con trỏ chuột thật
ZOOM_WINDOW_KEYWORDS = ("LDPlayer",)      # từ khóa tiêu đề cửa sổ giả lập
ZOOM_BEFORE_ATTACK = True                 # zoom out NGAY TRƯỚC khi đi tấn công (làng chính, trước khi bấm 'Chiến đấu'); config.json -> ZOOM_BEFORE_ATTACK ghi đè
ZOOM_REQUIRED = True                      # BẮT BUỘC zoom thành công mới được đi tấn công (config.json -> ZOOM_REQUIRED ghi đè); False = zoom hỏng vẫn đi đánh
ZOOM_RETRY_PAUSE = 3.0                    # nghỉ giữa 2 lần thử zoom lại khi bắt buộc (giây)
ZOOM_RESTART_AFTER = 3                    # thất bại liên tiếp bấy nhiêu lần (mỗi lần = đủ vòng/phương án) -> mở lại game rồi thử lại
ZOOM_MAX_ROUNDS = 2                       # số vòng thử lại toàn bộ các phương án nếu vòng đầu không phương án nào zoom được
ZOOM_PRE_TAP = True                       # bấm vào TRUNG TÂM màn hình để thoát list nâng cấp rồi mới zoom (config.json -> ZOOM_PRE_TAP)
ZOOM_PRE_TAP_POS = (480, 270)             # (960x540) điểm bấm trước khi zoom (config.json -> ZOOM_PRE_TAP_POS = [x, y] ghi đè)
ZOOM_PRE_TAP_WAIT = 0.5                   # chờ list đóng xong rồi mới zoom (giây)
ZOOM_STATIC_CONCLUDE = 2                  # ảnh KHÔNG đổi ở bấy nhiêu phương án KHÁC NHAU mới kết luận 'làng đã ở mức xa nhất'
ZOOM_VERIFY_DIFF = 4.0                    # độ lệch xám TB giữa 2 ảnh trước/sau để coi là "đã zoom"
WALL_MENU_MAX_SWIPES = 6                  # số lần vuốt menu tối đa để tìm thẻ Tường
# Vuốt menu CHẬM + NGẮN để không bị quán tính (fling) làm menu trôi quá xa và nhảy qua thẻ Tường.
WALL_MENU_SWIPE_DISTANCE = 200            # px mỗi lần vuốt (nhỏ hơn chiều cao danh sách -> các màn quét chồng nhau)
WALL_MENU_SWIPE_DURATION = 250            # ms mỗi lần vuốt (200-300ms: cuộn nhanh; config.json -> WALL_MENU_SWIPE_DURATION ghi đè)
WALL_MENU_SWIPE_MULT_DEFAULT = 1.5        # quãng đường vuốt trục Y gấp 1.5 lần bản cũ (config.json -> WALL_MENU_SWIPE_MULT)
WALL_SAME_SPOT_TOL = 40                   # px: thẻ Tường cách vị trí vừa nâng <= bấy nhiêu coi là "cùng chỗ" -> nâng tiếp tại chỗ
WALL_CONTINUOUS_SAFETY_CAP = 300          # chốt chặn an toàn: tối đa bấy nhiêu khúc Tường / 1 lần mở menu (config.json -> WALL_CONTINUOUS_SAFETY_CAP)
WALL_OUT_OF_RESOURCE = "OUT_OF_RESOURCE"  # trạng thái thẻ Tường: chữ giá ĐỎ / XÁM (không đủ tài nguyên)
WALL_CAN_UPGRADE = "CAN_UPGRADE"
WALL_MENU_SETTLE_TIMEOUT = 3.0            # chờ menu đứng yên tối đa (giây) trước khi quét
WALL_MENU_SETTLE_DIFF = 2.0               # độ lệch xám TB giữa 2 khung liên tiếp để coi là "đã đứng yên"
# --- Phục hồi: Tải lại trò chơi / popup lạ ---
# (RELOAD_WAIT_SECONDS cũ đã bỏ: sau "Tải lại trò chơi" chờ động bằng wait_for_game_start, xem RELOAD_START_TIMEOUT)
# KHÔNG quét nút X bằng ảnh nữa (dễ bấm nhầm X của menu Búa / Sự kiện / Shop).
# Ở màn hình LẠ liên tục UNKNOWN_SCREEN_SECONDS giây -> bấm 1 cú vào FALLBACK_SAFE_CLICK_POS
# (config.json -> MANUAL_POSITIONS, người dùng tự chỉnh trên GUI).
UNKNOWN_SCREEN_FALLBACK = True     # False: tắt hẳn việc tự bấm tọa độ thoát an toàn
UNKNOWN_SCREEN_SECONDS = 5         # số giây liên tục ở trạng thái UNKNOWN trước khi bấm tọa độ thoát

# --- Stuck Watchdog: chống kẹt / treo màn hình ở BẤT KỲ bước nào ---
STUCK_WATCHDOG_ENABLE = True       # False: tắt hẳn Watchdog
GAME_PACKAGE = "com.supercell.clashofclans"
STUCK_TIMEOUT = 30                 # giây đứng yên (fingerprint màn hình không đổi) trước khi bắt đầu phục hồi (config.json -> STUCK_TIMEOUT ghi đè)
STUCK_ESCALATE_SECONDS = 15        # sau khi bấm tọa độ an toàn, chờ thêm bấy nhiêu giây rồi mới tắt/mở lại game
STUCK_MAX_GAME_RESTARTS = 2        # mở lại game quá số lần này mà vẫn kẹt -> khởi động lại LDPlayer
STUCK_CHECK_INTERVAL = 2.0         # Watchdog chỉ phân tích màn hình mỗi ~2s (tiết kiệm CPU)
STUCK_FRAME_DIFF = 6.0             # fingerprint khác nhưng độ lệch TB (ảnh thu nhỏ 64x36) < ngưỡng này = chỉ là hoạt ảnh nhẹ của làng, vẫn tính là ĐỨNG YÊN
STUCK_POLL_GAP = 20                # nếu Watchdog không được gọi quá bấy nhiêu giây (luồng bận việc dài hợp lệ) -> đếm lại từ đầu
STUCK_HEALTHY_RESET = 180          # sau lần phục hồi cuối, khỏe lại quá bấy nhiêu giây thì xóa bộ đếm số lần mở lại game
# Khởi động game ĐỘNG: không còn sleep cố định (12s / 20s), thăm dò màn hình tới khi thấy nút Búa / Chiến đấu.
GAME_START_TIMEOUT = 90            # chờ tối đa bấy nhiêu giây để vào được Làng chính (máy chậm vẫn kịp, máy nhanh vào sớm)
GAME_START_POLL = 1.0              # nhịp thăm dò màn hình khi chờ game mở (giây)
RELOAD_START_TIMEOUT = 60          # chờ Làng chính sau khi bấm 'Tải lại trò chơi'
EMULATOR_BOOT_TIMEOUT = 120        # chờ LDPlayer boot (ADB chụp được ảnh) tối đa bấy nhiêu giây
EMULATOR_BOOT_POLL = 3.0           # nhịp thử nối lại ADB / chụp ảnh khi chờ giả lập boot
# --- Ảnh mẫu NHIỀU BIẾN THỂ (wildcard): button_xxx.png, button_xxx_2.png, button_xxx_3.png ... ---
# Thêm biến thể bằng tools/crop_tool.py (tự đặt tên _2, _3...) - KHÔNG cần sửa code, bot tự nạp (trễ tối đa vài giây).
TPL_BATTLE = "button_battle*.png"
TPL_BUILD = "button_build*.png"
TPL_FIND_MATCH = "button_find_match_1700*.png"
TPL_ATTACK = "button_attack*.png"
TPL_NEXT = "button_next*.png"
TPL_RETURN_HOME = "button_return_home*.png"
TPL_BOOST = "button_boost*.png"
TPL_RETRY = "button_retry*.png"
TPL_RELOAD = "button_reload*.png"
TPL_BATTLE_SUMMARY = "battle_summary*.png"   # (tùy chọn) bảng tổng kết thiệt hại % - thấy nghĩa là trận đã kết thúc
# --- Đầu hàng (Surrender Fallback): cắt bằng tools/crop_tool.py, mục 17 / 18 ---
TPL_SURRENDER = "surrender_btn*.png"                     # nút Đầu hàng (góc dưới trái khi đang ở trận / soi nhà)
TPL_CONFIRM_SURRENDER = "confirm_surrender_btn*.png"     # nút Xác nhận đầu hàng (popup giữa màn hình)
TPL_HAMMER = TPL_BUILD                                   # nút Búa (hammer_icon) = đã ở Làng chính
SURRENDER_THRESHOLD = 0.75
SURRENDER_ROI_DEFAULT = (0, 355, 190, 455)               # (960x540) config.json -> SURRENDER_BTN_ROI
CONFIRM_SURRENDER_ROI_DEFAULT = (240, 180, 740, 460)     # (960x540) config.json -> CONFIRM_SURRENDER_ROI
SURRENDER_STEP_WAIT = 5.0         # sau khi bấm Đầu hàng: chờ tối đa bấy nhiêu giây để thấy nút Búa HOẶC nút Xác nhận
SURRENDER_POLL = 0.25             # nhịp chụp khi chờ
SURRENDER_IDLE_SECONDS = 4.0      # đứng ở màn hình có nút Đầu hàng (không có Next, không đang thả quân) quá bấy nhiêu giây -> Đầu hàng
SURRENDER_NO_NEXT_CONFIRM = 2     # số lần chụp liên tiếp KHÔNG thấy Next mới kết luận "mất nút Next" (tránh nhầm lúc đang chuyển cảnh)

# Ảnh mẫu "mốc": thấy 1 trong số này nghĩa là đang ở màn hình bot hiểu (không phải popup lạ)
ANCHOR_TEMPLATES = (
    TPL_BATTLE, TPL_BUILD, TPL_FIND_MATCH, TPL_ATTACK,
    TPL_NEXT, TPL_RETURN_HOME, TPL_BOOST,
)

# --- 7 trạng thái màn hình mà detect_initial_state() phân loại ---
GAME_STATES = (
    "HOME_VILLAGE", "SEARCH_MENU", "ATTACK_PREPARE", "SCOUTING",
    "IN_BATTLE", "CONNECTION_ERROR", "UNKNOWN",
)
# Bảng luật nhận diện, QUÉT THEO THỨ TỰ (trạng thái, các mẫu wildcard, ngưỡng khớp, câu log)
STATE_RULES = (
    ("CONNECTION_ERROR", (TPL_RETRY, TPL_RELOAD),  0.70, "✅ Nhận diện tức thì: Popup mất kết nối (CONNECTION_ERROR)"),
    ("HOME_VILLAGE",     (TPL_BATTLE, TPL_BUILD),  0.75, "✅ Nhận diện tức thì: Đang ở Làng chính (HOME_VILLAGE)"),
    ("SEARCH_MENU",      (TPL_FIND_MATCH,),        0.75, "✅ Nhận diện tức thì: Đang ở Menu chọn chế độ"),
    ("ATTACK_PREPARE",   (TPL_ATTACK,),            0.75, "✅ Nhận diện tức thì: Đang ở Bảng chuẩn bị quân"),
    ("SCOUTING",         (TPL_NEXT,),              0.75, "✅ Nhận diện tức thì: Đang ở Màn hình soi nhà"),
    ("IN_BATTLE",        (TPL_RETURN_HOME,),       0.60, "✅ Nhận diện tức thì: Đang trong trận đấu (IN_BATTLE)"),
)
DEPLOY_AUTO_SCAN = True            # False (hoặc config.json -> DEPLOY_AUTO_SCAN=false): bỏ tầng 2, không quét thanh quân
ZOOM_PROCESS_NAMES = ("dnplayer.exe",)    # tiến trình cửa sổ LDPlayer (nhận diện theo tiến trình, không chỉ tiêu đề)
ZOOM_CTRL_DIRECTION = -1                  # -1 = Ctrl + cuộn XUỐNG; đổi thành 1 nếu máy bạn zoom ngược
DEPLOY_FAST_SWIPE_DURATION = 250   # ms mỗi đường vuốt ở kiểu "fast_swipe"

# --- Quét thanh điều khiển trận đánh (_scan_battle_bar) ---
BAR_SCAN_X_RANGE = (74, 862)       # quét các ô trong khoảng X này (toạ độ 960x540)
BAR_CENTER_Y = 489                 # tâm dọc của thanh quân (ảnh thật: icon nằm trong Y 448-535)
BAR_SLOT_SIZE = 68                 # cạnh ô cắt (px)
BAR_SLOT_STEP = 75                 # bước giữa 2 ô liên tiếp (px): tâm ô ≈ 108, 183, 258, ... (đo trên ảnh thật)
BAR_MATCH_PAD = 8                  # đệm quanh ô khi khớp ảnh mẫu (cho phép lệch vài px)
BAR_INNER_RATIO = 0.5              # chỉ xét phần giữa ô (bỏ viền nét đứt của ô trống)
BAR_EMPTY_STD = 14.0               # độ lệch chuẩn xám (STD) phần giữa ô < 14.0 -> ô nét đứt rỗng, loại bỏ
BAR_HERO_THRESHOLD = 0.70          # ngưỡng khớp ảnh mẫu Tướng
BAR_SPELL_THRESHOLD = 0.70         # ngưỡng khớp ảnh mẫu Phép
HERO_TEMPLATE_PATTERN = "button_hero*.png"    # button_hero.png, button_hero_king.png, ...
SPELL_TEMPLATE_PATTERN = "button_spell*.png"  # button_spell.png, button_spell_rage.png, ...

# --- Điểm thả quân & phép: đo từ ảnh chụp thật (960x540) ---
# Điểm thả lính DUY NHẤT: ngoài cùng bên trái (mọi thẻ lính + Tướng đều thả vào đây)
LEFT_EDGE_POINTS = [(100, 260)]

# 4 khu vực thả Phép trong lõi (4 vòng trắng quanh nhà chính)
SPELL_WHITE_ZONES = [(405, 170), (610, 195), (405, 290), (610, 305)]

DEPLOY_RANDOM_SPREAD = (40, 120)     # độ lệch ngẫu nhiên (x, y) px quanh điểm thả khi chọn kiểu "ngẫu nhiên"/"toàn bản đồ"
DEPLOY_CLICKS_PER_SLOT = 30            # số click cho thẻ lính đầu tiên (config.json["DEPLOY_CLICKS_PER_SLOT"] ghi đè)
DEPLOY_CLICK_INTERVAL = 0.01           # giây giữa 2 click (siêu nhanh; 0 = liên tục không nghỉ)
DEPLOY_CLICK_JITTER = 4                # lệch ngẫu nhiên ±px để không trùng 1 pixel
HERO_CLICK_COUNT = (2, 3)              # Tướng: bấm ngẫu nhiên 2-3 lần vào điểm đầu tiên của dải đã chọn
HERO_CLICK_INTERVAL = 0.15

# Từ thẻ thứ này trở đi (và mọi thẻ Phép) -> xả vào trung tâm (SPELL_WHITE_ZONES) thay vì dải lính
CENTER_DROP_FROM_CARD = 7

# Phép: xả SẠCH toàn bộ lọ trong slot, xoay vòng 4 khu vực trắng
SPELL_DROP_COUNT = 11                  # số lọ phép mang theo (cho phép 8-12; config.json["SPELL_DROP_COUNT"] ghi đè)
SPELL_DROP_COUNT_RANGE = (8, 12)
SPELL_DROP_INTERVAL = 0.1              # nghỉ giữa 2 lần thả để game kịp nhận lệnh

# Kiểu thả hợp lệ: chỉ còn CLICK (không giữ, không vuốt). Tên cũ vẫn được chấp nhận để tương thích.
DEPLOY_MODE_ALIASES = {
    "fast_click": "fast_click", "fast_swipe": "fast_click", "swipe": "fast_click",
    "point_click": "fast_click", "tap": "fast_click",
    "hero": "hero",
    "spell_click": "spell_click", "spell": "spell_click",
}
DEFAULT_MODE_BY_TYPE = {"troop": "fast_click", "hero": "hero", "spell": "spell_click"}

# --- Kiểu thả quân (config.json -> DEPLOY_MODE). MỖI THẺ có tọa độ thả riêng: card["zones"][kiểu] = [[x, y], ...] ---
#   left_corner / right_corner : 1 góc -> chỉ dùng 1 tọa độ
#   spread_left / spread_right : thả dàn trải bên trái / bên phải -> nhiều tọa độ, chia ĐỀU số lần bấm cho từng điểm (xoay vòng)
#   random_around              : thả ngẫu nhiên quanh map -> nhiều tọa độ do người dùng đặt, mỗi lần bấm chọn ngẫu nhiên 1 điểm
DEPLOY_SINGLE_MODES = ("left_corner", "right_corner")
DEPLOY_MULTI_MODES = ("spread_left", "spread_right", "random_around")
DEPLOY_MODE_LEGACY = {"left_random": "random_around", "right_random": "random_around", "full_map": "random_around",
                      "spread": "spread_left"}


def normalize_deploy_mode(mode):
    """Chuẩn hóa tên kiểu thả (chấp nhận tên cũ left_random/right_random/full_map)."""
    mode = str(mode or "left_corner").strip().lower()
    mode = DEPLOY_MODE_LEGACY.get(mode, mode)
    return mode if mode in DEPLOY_SINGLE_MODES + DEPLOY_MULTI_MODES else "left_corner"


# Kỹ năng Tướng: bấm lại vào thẻ Tướng (hoặc tọa độ riêng người dùng đặt)
HERO_SKILL_IMMEDIATE_GAP = 0.1         # nghỉ sau khi thả Tướng rồi mới bấm kỹ năng (kiểu "dùng ngay")


# --- Bộ nhớ đệm so khớp ảnh + đo thời gian (tìm nguyên nhân bot chậm) ---
# Mỗi vòng lặp bot từng so khớp CÙNG 1 ảnh màn hình với CÙNG 1 ảnh mẫu nhiều lần (check_connection_error,
# Watchdog, _is_known_screen, check_and_search_match...). Kết quả được nhớ theo (ảnh, mẫu, ngưỡng).
SLOW_TICK_LOG = 3.0     # vòng lặp lâu hơn bấy nhiêu giây thì ghi log phân tích thời gian
_MATCH_CACHE = {"screen": None, "fp": None, "res": {}}
_PROF = {"match_s": 0.0, "match_n": 0, "hit_n": 0, "cap_s": 0.0, "cap_n": 0, "t0": time.time()}


def _screen_fingerprint(screen):
    """Fingerprint MD5 của ảnh thu nhỏ 64x36 (xem image_processing.compute_frame_fingerprint)."""
    try:
        return compute_frame_fingerprint(screen)
    except Exception:
        return None


def _find_image(screen, template_path, threshold=0.75, **kwargs):
    """ImageProcessor.find_image_location có nhớ kết quả cho đúng ảnh màn hình đó (không đổi kết quả)."""
    if screen is None:
        return ImageProcessor.find_image_location(screen, template_path, threshold=threshold, **kwargs)
    fp = _screen_fingerprint(screen)
    c = _MATCH_CACHE
    if c["screen"] is not screen or c["fp"] != fp:
        c["screen"], c["fp"], c["res"] = screen, fp, {}
    key = (template_path, float(threshold), tuple(sorted(kwargs.items())))
    if key in c["res"]:
        _PROF["hit_n"] += 1
        return c["res"][key]
    t = time.time()
    res = ImageProcessor.find_image_location(screen, template_path, threshold=threshold, **kwargs)
    _PROF["match_s"] += time.time() - t
    _PROF["match_n"] += 1
    c["res"][key] = res
    return res


def _asset_specs(names):
    """Tên file / wildcard trong assets/ (str hoặc list) -> đường dẫn tuyệt đối tương ứng."""
    if isinstance(names, (str, os.PathLike)):
        return asset(os.fspath(names))
    return [asset(os.fspath(n)) for n in names]


def _template_names_exist(names):
    """True nếu có ÍT NHẤT 1 ảnh mẫu (kể cả biến thể _2, _3...) khớp tên / wildcard trong assets/."""
    return bool(ImageProcessor.expand_template_paths(_asset_specs(names)))


def _find_any(screen, names, threshold=0.75):
    """
    Giống _find_image nhưng cho NHIỀU ảnh mẫu biến thể: `names` là tên wildcard trong assets/
    (vd "button_return_home*.png") hoặc list tên. Dùng ImageProcessor.find_any_template_location
    (cache ảnh mẫu + short-circuit) và cũng nhớ kết quả theo ảnh màn hình như _find_image.
    """
    specs = _asset_specs(names)
    if screen is None:
        return ImageProcessor.find_any_template_location(screen, specs, threshold=threshold)
    fp = _screen_fingerprint(screen)
    c = _MATCH_CACHE
    if c["screen"] is not screen or c["fp"] != fp:
        c["screen"], c["fp"], c["res"] = screen, fp, {}
    key = ("ANY", tuple(specs) if isinstance(specs, list) else specs, float(threshold))
    if key in c["res"]:
        _PROF["hit_n"] += 1
        return c["res"][key]
    t = time.time()
    res = ImageProcessor.find_any_template_location(screen, specs, threshold=threshold)
    _PROF["match_s"] += time.time() - t
    _PROF["match_n"] += 1
    c["res"][key] = res
    return res


def _find_next(screen):
    """
    Nút Next (soi nhà) bằng đường NHANH: chỉ quét NEXT_BTN_ROI, 1 tỉ lệ, ảnh xám (ImageProcessor.find_next_button).
    Nhớ kết quả theo ảnh màn hình như _find_any. Trả (x, y) hoặc None.
    """
    if screen is None:
        return ImageProcessor.find_next_button(screen)
    fp = _screen_fingerprint(screen)
    c = _MATCH_CACHE
    if c["screen"] is not screen or c["fp"] != fp:
        c["screen"], c["fp"], c["res"] = screen, fp, {}
    key = ("NEXT_FAST",)
    if key in c["res"]:
        _PROF["hit_n"] += 1
        return c["res"][key]
    t = time.time()
    res = ImageProcessor.find_next_button(screen, 0.75)
    _PROF["match_s"] += time.time() - t
    _PROF["match_n"] += 1
    c["res"][key] = res
    return res


class BotLogic:
    """Bộ não điều khiển luồng chính của Bot."""

    # ------------------------------------------------------------------
    # 1. KHỞI TẠO
    # ------------------------------------------------------------------
    def __init__(self, adb_controller, emulator_controller, ui_queue=None, stop_event=None):
        """
        :param adb_controller:      đối tượng ADBController (chụp, bấm, vuốt).
        :param emulator_controller: đối tượng EmulatorController (điều khiển LDPlayer).
        :param ui_queue:            hàng đợi (queue.Queue) gửi log/stats về AppUI.
        :param stop_event:          (tùy chọn) threading.Event; khi được set, bot thoát
                                    khỏi các lần chờ dài (ví dụ chờ trận đánh) để dừng nhanh.
        """
        self.adb = adb_controller
        self.emulator = emulator_controller
        self.ui_queue = ui_queue
        self.stop_event = stop_event

        # Quản lý Self-Healing
        self.retry_count = 0
        self.max_retries = 3

        # Thống kê gửi lên Dashboard
        self.stats = {"battles": 0, "walls": 0}

        # Cooldown giữa các lần bấm nâng cấp (giây)
        self.upgrade_cooldown = 30
        self.last_upgrade_time = 0  # mốc thời gian nâng cấp gần nhất

        # Lịch kiểm tra nâng Tường: None = chưa kiểm tra lần nào kể từ khi vào game
        # -> lần đầu ở làng nhà sẽ nâng Tường ngay; sau đó cứ đủ WALL_RETRY_BATTLES_LIMIT trận.
        self._wall_check_battle_mark = None
        self._last_builder_warn = 0   # giới hạn tần suất log khi không đọc được số thợ

        # Chế độ Cào dữ liệu
        self.collected_count = 0
        self._roi_debug_done = False    # đã lưu ảnh chẩn đoán ROI của lần chạy này chưa
        self.collect_finished = False   # True khi đã cào đủ -> main.py có thể dùng để thoát vòng lặp
        self._template_cache = {}       # pattern -> list ảnh mẫu đã nạp (Tướng / Phép)
        self._bar_debug_done = False    # đã lưu ảnh chẩn đoán thanh quân chưa
        self._save_queue = queue.Queue(maxsize=256)
        self._saver_thread = None
        self._has_boosted = False       # đã bấm nút Tăng tốc trong trận hiện tại chưa (chỉ bấm 1 lần)
        self._touch_info = None         # cache thiết bị cảm ứng đa điểm (dùng cho zoom out)
        self._touch_checked = False     # đã dò getevent -p rồi (không dò lặp nếu máy không có)
        self._zoom_method = None        # phương án zoom out đã từng hoạt động (thử trước)
        self._last_gc = time.time()     # lần gc.collect() gần nhất (dọn bộ nhớ định kỳ)
        self._unknown_since = None      # mốc bắt đầu chuỗi "màn hình lạ" liên tục (None = đang ở màn hình quen)
        self._unknown_taps = 0          # số lần đã bấm tọa độ thoát mà màn hình vẫn lạ
        self._reload_hint_logged = False
        self._last_status = None          # (text, level) trạng thái Banner gửi gần nhất

        # Stuck Watchdog
        self.current_state = "UNKNOWN"              # trạng thái màn hình gần nhất Watchdog nhận ra
        self.last_state_change_time = time.time()   # lần cuối đổi trạng thái / chuyển cảnh
        self.STUCK_TIMEOUT = STUCK_TIMEOUT          # số giây tối đa được đứng yên ở 1 trạng thái / UNKNOWN
        self._stuck_stage = 0                       # 0: chưa xử lý | 1: đã bấm tọa độ an toàn
        self._stuck_stage_time = 0.0
        self._game_restarts = 0                     # số lần đã tắt/mở lại game mà vẫn kẹt
        self._last_recovery_time = 0.0
        self._wd_last_check = 0.0
        self._wd_last_frame = None
        self._wd_last_fp = None                     # fingerprint khung hình lần kiểm tra trước
        self._wall_stop_reason = "NONE"             # lý do lượt nâng Tường gần nhất dừng (OUT_OF_RESOURCE / NOT_FOUND / ...)
        self._surr_idle_since = None                # mốc bắt đầu đứng ở màn hình có nút Đầu hàng mà không làm gì
        self._ensure_dataset_dirs()

    # ------------------------------------------------------------------
    # 2. HỖ TRỢ GIAO DIỆN & THAO TÁC
    # ------------------------------------------------------------------
    def log(self, message, ui=True):
        """
        In log ra terminal và (mặc định) đẩy vào ui_queue cho Nhật ký trên Dashboard.
        Chỉ dùng cho MỐC SỰ KIỆN quan trọng, ngắn gọn, người dùng thường đọc được.
        ui=False hoặc self.debug() -> chỉ in terminal.
        """
        print(message)
        if ui and self.ui_queue is not None:
            try:
                self.ui_queue.put({"type": "log", "content": str(message)})
            except Exception as e:
                print(f"[LỖI] Không gửi được log lên UI: {e}")

    def debug(self, message):
        """Log kỹ thuật chi tiết (điểm khớp ảnh, tọa độ, zoom...): CHỈ in ra terminal, không lên UI."""
        print(message)

    def set_status(self, text, level="info"):
        """
        Cập nhật Banner trạng thái thời gian thực trên Dashboard (qua ui_queue, không chặn bot).
        level: home | search | attack | wall | wait | warn | done | info | idle
        Bỏ qua nếu trạng thái giống hệt lần gửi trước để không spam hàng đợi.
        """
        key = (text, level)
        if key == self._last_status:
            return
        self._last_status = key
        print(f"[TRẠNG THÁI] {text}")
        if self.ui_queue is not None:
            try:
                self.ui_queue.put({"type": "status", "content": {"text": text, "level": level}})
            except Exception as e:
                print(f"[LỖI] Không gửi được trạng thái lên UI: {e}")

    def update_ui_stats(self):
        """Gửi bản sao thống kê hiện tại lên Dashboard của AppUI."""
        if self.ui_queue is None:
            return
        try:
            # Số trận còn lại tới lượt nâng Tường (0 = trận kế tiếp sẽ nâng).
            # Tắt nâng Tường -> None để UI hiện "chưa kích hoạt" thay vì đếm ngược sai.
            until_check = self._battles_until_wall_check() if self._wall_settings()["enable"] else None
            self.ui_queue.put({
                "type": "stats",
                "content": {
                    "battles": self.stats.get("battles", 0),
                    "walls": self.stats.get("walls", 0),
                    "until_wall": until_check,
                },
            })
        except Exception as e:
            print(f"[LỖI] Không gửi được stats lên UI: {e}")

    def _swipe(self, start, end, duration=400):
        """
        Vuốt từ start -> end. Thử truyền duration (mili giây); nếu ADBController.swipe
        chưa hỗ trợ tham số này thì gọi lại dạng 4 tham số.
        """
        try:
            self.adb.swipe(start[0], start[1], end[0], end[1], duration=duration)
        except TypeError:
            self.adb.swipe(start[0], start[1], end[0], end[1])

    def _capture(self, fresh=False):
        """Chụp lại màn hình (dùng khi cần xem menu vừa mở). fresh=True: bỏ cache, chụp ảnh MỚI. None nếu lỗi."""
        try:
            if fresh:
                clear = getattr(self.adb, "clear_screenshot_cache", None)
                if callable(clear):
                    clear()
            t = time.time()
            img = self.adb.capture_screen()
            _PROF["cap_s"] += time.time() - t
            _PROF["cap_n"] += 1
            return img
        except Exception as e:
            self.debug(f"[⚠️ CẢNH BÁO] Không chụp được màn hình: {e}")
            return None

    def detect_stable_state(self, attempts=3, gap=0.6):
        """
        Nhận diện trạng thái ban đầu CHẮC CHẮN: mỗi lần đều xóa cache + chụp ảnh MỚI, chỉ tin kết quả khi
        2 lần chụp liên tiếp (cách nhau `gap` giây) cho CÙNG trạng thái. Không ổn định -> 'UNKNOWN'.
        :return: (state, screen_mới_nhất)
        """
        last_state, last_screen = "UNKNOWN", None
        for _ in range(max(2, attempts)):
            screen = self._capture(fresh=True)
            state = self.detect_initial_state(screen)
            if last_screen is not None and state == last_state and state != "UNKNOWN":
                return state, screen
            last_state, last_screen = state, screen
            time.sleep(gap)
        return "UNKNOWN", last_screen

    def _locate(self, screen, template_name, fallback_pos, threshold=0.75):
        """
        Tìm tâm nút theo ảnh mẫu trong assets/.

        - Có file ảnh mẫu và khớp  -> trả về tọa độ tìm được.
        - Có file ảnh mẫu nhưng KHÔNG khớp -> None (không bấm mù để tránh bấm nhầm).
        - Chưa có file ảnh mẫu     -> dùng tọa độ mặc định trong config.py.
        """
        template_path = asset(template_name)
        if not os.path.exists(template_path):
            self.debug(f"ℹ️ Chưa có {template_name} trong assets/ -> dùng tọa độ mặc định {fallback_pos}")
            return fallback_pos
        return _find_image(screen, template_path, threshold=threshold)

    # ------------------------------------------------------------------
    # 2b. CHẾ ĐỘ CÀO DỮ LIỆU (COLLECT DATA MODE)
    # ------------------------------------------------------------------
    def _ensure_dataset_dirs(self):
        """Tự tạo dataset/raw_loot/ và dataset/raw_builder/ nếu chưa có."""
        for folder in (RAW_LOOT_DIR, RAW_BUILDER_DIR):
            try:
                os.makedirs(folder, exist_ok=True)
            except Exception as e:
                print(f"[LỖI] Không tạo được thư mục {folder}: {e}")

    def _collect_settings(self):
        """
        Đọc COLLECT_DATA_MODE / COLLECT_MAX_SAMPLES từ config.json (cache theo mtime nên nhẹ).
        Thiếu khóa hoặc sai kiểu -> dùng mặc định (tắt / 100). Trả về (enable, max_samples, roi).
        """
        try:
            cfg = load_json_config()
        except Exception as e:
            self.log(f"[❌ LỖI] Không đọc được cấu hình cào dữ liệu: {e}")
            return False, COLLECT_DEFAULT_MAX, None
        enable = bool(cfg.get("COLLECT_DATA_MODE", False))
        try:
            max_samples = int(cfg.get("COLLECT_MAX_SAMPLES", COLLECT_DEFAULT_MAX))
            if max_samples <= 0:
                max_samples = COLLECT_DEFAULT_MAX
        except (TypeError, ValueError):
            max_samples = COLLECT_DEFAULT_MAX
        return enable, max_samples, cfg.get("RESOURCE_ENEMY_ROI")

    @staticmethod
    def _roi_to_screen_coords(screen, roi):
        """
        Đổi ROI sang toạ độ (x1, y1, x2, y2) TRÊN ẢNH `screen` đang cắt, đã kẹp trong biên.
        - Hỗ trợ: mảng [x1, y1, x2, y2] (do crop_tool.py lưu), dict {x1,y1,x2,y2}, dict {x,y,w,h}.
        - Tự chuẩn hóa độ phân giải: ROI được khoanh trên ảnh cỡ `screen_size` trong config.json
          (ví dụ 1000x574 của bản crop_tool cũ; không có thì coi là 960x540). Nếu khác cỡ ảnh
          `screen` thì scale: x * rộng_screen / rộng_gốc, y * cao_screen / cao_gốc.
        Trả về None nếu ROI không hợp lệ.
        """
        if screen is None or roi is None or len(roi) == 0:
            return None
        try:
            if isinstance(roi, dict):
                if "w" in roi:
                    x1, y1 = int(roi["x"]), int(roi["y"])
                    x2, y2 = x1 + int(roi["w"]), y1 + int(roi["h"])
                else:
                    x1, y1 = int(roi["x1"]), int(roi["y1"])
                    x2, y2 = int(roi["x2"]), int(roi["y2"])
            elif isinstance(roi, (list, tuple)) and len(roi) >= 4:
                x1, y1, x2, y2 = int(roi[0]), int(roi[1]), int(roi[2]), int(roi[3])
            else:
                return None
        except Exception:
            return None

        img_h, img_w = screen.shape[:2]

        # --- Chuẩn hóa độ phân giải: cỡ ảnh lúc khoanh ROI -> cỡ ảnh ADB hiện tại ---
        ref_w, ref_h = BOT_REF_SIZE
        try:
            size = load_json_config().get("screen_size")
            if size and int(size[0]) > 0 and int(size[1]) > 0:
                ref_w, ref_h = int(size[0]), int(size[1])
        except Exception:
            pass
        if (ref_w, ref_h) != (img_w, img_h):
            sx, sy = img_w / ref_w, img_h / ref_h
            x1, x2 = round(x1 * sx), round(x2 * sx)
            y1, y2 = round(y1 * sy), round(y2 * sy)

        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(img_w, x2), min(img_h, y2)
        if x2 <= x1 or y2 <= y1:
            return None
        return x1, y1, x2, y2

    @staticmethod
    def _crop_roi(screen, roi):
        """
        Cắt ảnh theo ROI (đã tự chuẩn hóa độ phân giải, xem _roi_to_screen_coords).
        Trả về bản COPY của vùng cắt (để thread ghi file không bị ảnh hưởng khi `screen` đổi),
        hoặc None nếu ROI không hợp lệ.
        """
        box = BotLogic._roi_to_screen_coords(screen, roi)
        if box is None:
            return None
        x1, y1, x2, y2 = box
        return screen[y1:y2, x1:x2].copy()

    def _debug_roi_once(self, screen, roi):
        """
        Chẩn đoán ROI (chỉ 1 lần mỗi lần chạy): log cỡ ảnh ADB, ROI gốc và ROI sau chuẩn hóa,
        và lưu dataset/debug_roi_overlay_*.png = TOÀN màn hình với khung ROI (đúng khung đang
        được dùng để cắt) vẽ màu xanh lá.
        """
        if self._roi_debug_done or screen is None:
            return
        self._roi_debug_done = True
        try:
            h, w = screen.shape[:2]
            box = self._roi_to_screen_coords(screen, roi)
            try:
                cfg_size = load_json_config().get("screen_size")
            except Exception:
                cfg_size = None
            self.debug(f"🔎 [ROI] Ảnh ADB: {w}x{h} | ROI trong config = {roi} "
                     f"(khoanh trên {cfg_size or list(BOT_REF_SIZE)}) | ROI dùng để cắt = "
                     f"{list(box) if box else None}")
            if cfg_size and [int(cfg_size[0]), int(cfg_size[1])] != [w, h]:
                self.debug(f"ℹ️ [ROI] ROI khoanh trên {cfg_size[0]}x{cfg_size[1]}, ảnh ADB là {w}x{h} "
                         f"-> đã tự scale ROI. Nên khoanh lại bằng tools/crop_tool.py (đã chuẩn hóa "
                         f"về 960x540) để chính xác tuyệt đối.")
            overlay = screen.copy()
            if box:
                x1, y1, x2, y2 = box
                cv2.rectangle(overlay, (x1, y1), (x2, y2), (0, 255, 0), 2)
            self._save_image_async(DATASET_DIR, "debug_roi_overlay", overlay)
        except Exception as e:
            self.debug(f"[⚠️ CẢNH BÁO] Không tạo được ảnh chẩn đoán ROI: {e}")

    def _start_saver(self):
        """Bật luồng nền ghi file (chỉ tạo 1 lần)."""
        if self._saver_thread is not None and self._saver_thread.is_alive():
            return
        self._saver_thread = threading.Thread(
            target=self._saver_loop, name="DatasetSaver", daemon=True)
        self._saver_thread.start()

    def _saver_loop(self):
        """Luồng nền: lấy (đường dẫn, ảnh) từ hàng đợi và ghi ra đĩa, không chặn vòng lặp bot."""
        while True:
            path, img = self._save_queue.get()
            try:
                # imencode + tofile thay cho cv2.imwrite: imwrite im lặng thất bại (trả False)
                # khi đường dẫn có tiếng Việt / ký tự Unicode trên Windows.
                ok, buf = cv2.imencode(".png", img)
                if not ok:
                    raise RuntimeError("cv2.imencode trả về False")
                os.makedirs(os.path.dirname(path), exist_ok=True)
                buf.tofile(path)
            except Exception as e:
                self.debug(f"[⚠️ CẢNH BÁO] Ghi ảnh thất bại ({os.path.basename(path)}): {e}")
            finally:
                self._save_queue.task_done()

    def _save_image_async(self, folder, prefix, img):
        """
        Đưa ảnh vào hàng đợi để luồng nền ghi file; trả về ngay lập tức.
        Hàng đợi đầy -> bỏ qua mẫu này (không bao giờ block vòng lặp). Trả về True nếu đã xếp hàng.
        """
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = os.path.join(folder, f"{prefix}_{stamp}.png")
        self._start_saver()
        try:
            self._save_queue.put_nowait((path, img))
            return True
        except queue.Full:
            self.debug("[⚠️ CẢNH BÁO] Hàng đợi ghi ảnh đầy, bỏ qua 1 mẫu")
            return False

    def _flush_saver(self, timeout=5.0):
        """Chờ luồng nền ghi nốt các ảnh còn trong hàng đợi (tối đa `timeout` giây)."""
        deadline = time.time() + timeout
        while self._save_queue.unfinished_tasks and time.time() < deadline:
            time.sleep(0.05)

    def reset_collect(self):
        """Đặt lại bộ đếm cào dữ liệu (gọi nếu main.py dùng lại cùng 1 BotLogic cho lần chạy mới)."""
        self.collected_count = 0
        self.collect_finished = False

    def _finish_collect(self, max_samples):
        """Đã cào đủ: ghi log, đợi ghi nốt file, dừng bot và tắt trạng thái chạy trên UI."""
        self.log(f"✅ Đã cào đủ {max_samples} mẫu dữ liệu! Tự động dừng Bot.")
        self.set_status(f"✅ Đã cào đủ {max_samples} mẫu dữ liệu", "done")
        self.collect_finished = True
        self._flush_saver()
        if self.stop_event is not None:
            self.stop_event.set()
        if self.ui_queue is not None:
            try:
                self.ui_queue.put({"type": "state", "content": False})
            except Exception as e:
                print(f"[LỖI] Không gửi được trạng thái dừng lên UI: {e}")

    def is_collect_mode(self):
        """True nếu COLLECT_DATA_MODE đang bật trong config.json (main.py dùng để rút ngắn vòng lặp)."""
        return self._collect_settings()[0]

    @staticmethod
    def _roi_gray(screen, roi):
        """Ảnh xám của vùng ROI (dùng so sánh khung hình), None nếu ROI không hợp lệ."""
        crop = BotLogic._crop_roi(screen, roi)
        if crop is None:
            return None
        return cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

    def _wait_new_base(self, prev_gray, roi, timeout=None):
        """
        Chờ THÔNG MINH sau khi bấm Next, thay cho time.sleep cố định:
        thăm dò ảnh liên tục và chỉ coi là "nhà mới đã sẵn sàng" khi
          (1) vùng ROI đã khác hẳn nhà trước,
          (2) vùng ROI đã ổn định (không còn hiệu ứng chuyển cảnh),
          (3) nút Next đã hiện lại.
        Nhờ vậy sang nhà mới ngay khi game tải xong, không chụp trùng nhà cũ.
        Trả về (screen, next_btn) hoặc (None, None) nếu quá thời gian / có lệnh dừng.
        """
        deadline = time.time() + (COLLECT_NEXT_TIMEOUT if timeout is None else timeout)
        last = prev_gray
        changed = False
        while time.time() < deadline:
            if self._stop_requested():
                return None, None
            screen = self._capture()
            if screen is None:
                time.sleep(COLLECT_POLL_INTERVAL)
                continue
            if self._guard_connection_throttled(2.0, screen):
                self.log("🔌 [Cào dữ liệu] Mất kết nối khi chờ nhà mới -> đã phục hồi, thoát vòng chờ")
                return None, None
            cur = self._roi_gray(screen, roi)
            if cur is None or cur.shape != prev_gray.shape:
                time.sleep(COLLECT_POLL_INTERVAL)
                continue
            if not changed and float(cv2.absdiff(cur, prev_gray).mean()) >= COLLECT_CHANGE_THRESHOLD:
                changed = True
            if changed and float(cv2.absdiff(cur, last).mean()) <= COLLECT_SETTLE_THRESHOLD:
                next_btn = _find_next(screen)
                if next_btn:
                    return screen, next_btn
            last = cur
            time.sleep(COLLECT_POLL_INTERVAL)
        return None, None

    def _collect_enemy_sample(self, screen, next_btn, max_samples, roi):
        """
        Vòng cào nhanh (chạy liên tục trong 1 lần gọi, không chờ vòng lặp chính):
        cắt ROI -> xếp hàng ghi file -> đếm/log -> bấm Next -> chờ nhà mới tải xong -> lặp lại.
        Không OCR, không thả quân. Thoát khi đủ mẫu, có lệnh dừng, hoặc mất màn hình soi nhà
        (khi đó trả quyền về vòng lặp chính để xử lý popup / mất kết nối).
        """
        while True:
            if self.collect_finished or self._stop_requested():
                return
            collect_on, max_samples, roi = self._collect_settings()   # đọc lại: bật/tắt giữa chừng
            if not collect_on:
                return

            self._debug_roi_once(screen, roi)
            crop = self._crop_roi(screen, roi)
            if crop is None:
                self.log("❌ Chưa có / sai RESOURCE_ENEMY_ROI trong config.json "
                         "(đặt bằng tools/crop_tool.py) -> không cào được. Dừng Bot.")
                self.collect_finished = True
                if self.stop_event is not None:
                    self.stop_event.set()
                if self.ui_queue is not None:
                    self.ui_queue.put({"type": "state", "content": False})
                return

            if self._save_image_async(RAW_LOOT_DIR, "loot", crop):
                self.collected_count += 1
                self.log(f"📸 Đã cào dữ liệu nhà thứ {self.collected_count}/{max_samples}...")
                self.set_status(f"📸 Đang cào dữ liệu ({self.collected_count}/{max_samples})", "search")

            if self.collected_count >= max_samples:
                self._finish_collect(max_samples)
                return

            # Bấm Next ngay lập tức, rồi chờ đúng đến khi nhà mới sẵn sàng
            prev_gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            self.adb.click(*next_btn)
            screen, next_btn = self._wait_new_base(prev_gray, roi)
            if screen is None:
                if not self._stop_requested():
                    self.debug("⚠️ Chưa thấy nhà mới sau khi bấm Next (hoặc vừa phục hồi mất kết nối) -> kiểm tra lại ở vòng chính")
                return

    def _ocr_only_for_wall(self):
        """
        True (mặc định) -> OCR tài nguyên CHỈ để nâng Tường: không OCR nhà địch, và chỉ OCR
        tài nguyên làng nhà khi ENABLE_WALL_UPGRADE bật. Đọc từ config.json (khóa OCR_ONLY_FOR_WALL).
        """
        try:
            return bool(load_json_config().get("OCR_ONLY_FOR_WALL", True))
        except Exception:
            return True

    def _wall_settings(self):
        """
        Đọc thông số xả tường từ config.json (có cache theo mtime nên gọi mỗi vòng vẫn nhẹ).
        Lỗi đọc -> tắt xả tường cho vòng này (an toàn).
        """
        try:
            cfg = load_json_config()
            return {
                "enable": bool(cfg["ENABLE_WALL_UPGRADE"]),
                "gold": int(cfg["WALL_UPGRADE_GOLD_THRESHOLD"]),
                "elixir": int(cfg["WALL_UPGRADE_ELIXIR_THRESHOLD"]),
            }
        except Exception as e:
            self.log(f"[❌ LỖI] Không đọc được cấu hình xả tường từ config.json: {e}")
            return {"enable": False, "gold": 0, "elixir": 0}

    # ------------------------------------------------------------------
    # 3. TỰ PHỤC HỒI KHI MẤT KẾT NỐI
    # ------------------------------------------------------------------
    def _screen_static_seconds(self, screen):
        """Số giây màn hình gần như KHÔNG đổi (so khung xám thu nhỏ, rất nhẹ). Vừa đổi -> 0."""
        now = time.time()
        try:
            small = cv2.resize(cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY), (96, 54))
            last = getattr(self, "_cf_frame", None)
            if (last is None or last.shape != small.shape
                    or float(cv2.absdiff(small, last).mean()) >= STUCK_FRAME_DIFF):
                self._cf_since = now
            self._cf_frame = small
        except Exception:
            self._cf_since = now
        return now - getattr(self, "_cf_since", now)

    def handle_connection_error(self, screen, allow_manual_fallback=False):
        """
        Tự phục hồi khi game báo lỗi kết nối. Dò theo thứ tự:
          1) popup "THỬ LẠI"          (button_retry*.png)  -> bấm; thử quá max_retries thì khởi động lại LDPlayer;
          2) popup "TẢI LẠI TRÒ CHƠI" (button_reload*.png) -> bấm, chờ game tải lại, reset lịch nâng Tường;
          3) (chỉ khi allow_manual_fallback=True VÀ chưa có ảnh mẫu retry/reload nào trong assets/)
             -> bấm tọa độ thủ công RELOAD_GAME_POS (config.json -> MANUAL_POSITIONS). Không bao giờ bấm mù
             khi đã có ảnh mẫu, vì khi đó không thấy popup nghĩa là KHÔNG có lỗi.
        :return: True nếu vừa bấm để phục hồi (luồng gọi nên thoát vòng chờ và bắt đầu lại).
        """
        if screen is None:
            return False
        # --- 1) Popup "THỬ LẠI" ---
        retry_btn = _find_any(screen, TPL_RETRY, threshold=0.7)
        if retry_btn:
            x, y = retry_btn
            self.retry_count += 1
            self.set_status("🔌 Mất kết nối - đang thử lại...", "warn")
            self._reset_unknown()
            self.log(
                f"⚠️ Mất kết nối! Bấm 'THỬ LẠI' "
                f"(Lần {self.retry_count}/{self.max_retries})..."
            )
            self.adb.click(x, y)
            time.sleep(6)  # chờ game load lại
            self._has_boosted = False
            self._watchdog_reset()

            # --- Thử lại quá số lần cho phép: khởi động lại LDPlayer ---
            if self.retry_count >= self.max_retries:
                self.log("❌ Thử lại thất bại nhiều lần. KHỞI ĐỘNG LẠI LDPlayer...")
                self._reboot_emulator()
                self.retry_count = 0
            return True

        # --- 2) Popup "TẢI LẠI TRÒ CHƠI" ---
        if self._handle_reload_popup(screen):
            return True

        # --- 3) Tọa độ thủ công (chỉ khi không có ảnh mẫu nào để nhận ra popup) ---
        if allow_manual_fallback and not _template_names_exist(TPL_RELOAD) and not _template_names_exist(TPL_RETRY):
            try:
                pos = self._manual_pos("RELOAD_GAME_POS")
            except Exception as e:
                self.debug(f"[⚠️ CẢNH BÁO] Không đọc được RELOAD_GAME_POS: {e}")
                return False
            self.log(f"🔄 Chưa có ảnh mẫu popup lỗi -> thử bấm tọa độ thủ công RELOAD_GAME_POS {pos}")
            self._press_reload(pos)
            return True
        return False

    def _check_conn_popups(self, screen):
        """Tên cũ, giữ lại cho tương thích -> handle_connection_error()."""
        return self.handle_connection_error(screen)

    def check_connection_error(self, screen, full=True):
        """
        Quét màn hình tìm popup lỗi và tự phục hồi (gọi mỗi vòng lặp chính):
          1) "THỬ LẠI"            (button_retry.png)  -> bấm; thử quá max_retries thì khởi động lại LDPlayer.
          2) "TẢI LẠI TRÒ CHƠI"   (button_reload.png) -> bấm, chờ 8-10s, reset lịch nâng Tường.
          3) luồng/giao diện lạ    -> _handle_unknown_flow(): lạ quá 5s thì bấm FALLBACK_SAFE_CLICK_POS.

        Ngoài vòng chính, hàm này còn được gọi trong mọi vòng chờ con (_wait_battle_end, _wait_new_base,
        check_and_search_match, _open_build_menu, _leave_battle, thả quân...) với full=False:
        chỉ xử lý popup Thử lại / Tải lại + Stuck Watchdog, KHÔNG chạy _handle_unknown_flow
        (để không bấm tọa độ thoát nhầm giữa trận). Qua _guard_connection().

        :param screen: ảnh chụp màn hình (OpenCV BGR).
        :param full: True (vòng chính) -> có cả luồng lạ; False (vòng con) -> chỉ popup + Watchdog.
        :return: True nếu vừa xử lý một lỗi / popup / kẹt (luồng gọi nên THOÁT vòng chờ và bắt đầu lại),
                 False nếu mọi thứ bình thường.
        """
        try:
            self._maybe_gc()
            if screen is None:
                return False
            # Cùng ảnh này vừa được kiểm tra sạch (vòng chính -> check_and_search_match) -> khỏi làm lại
            clean = getattr(self, "_clean_guard", None)
            if clean is not None and clean[0] is screen and (clean[1] or not full):
                return False

            # Chỉ dò popup khi: (a) màn hình đứng yên (kẹt) hoặc (b) màn hình LẠ (chỉ ở vòng chính).
            # Màn hình quen + đang chuyển động -> bỏ qua, không spam so khớp ảnh.
            static = self._screen_static_seconds(screen)
            need_conn = static >= CONN_CHECK_STATIC_SECONDS
            if not need_conn and full:
                need_conn = (self._is_known_screen(screen) is False)
            now = time.time()
            if need_conn and now - getattr(self, "_conn_checked_at", 0.0) >= CONN_CHECK_GAP:
                self._conn_checked_at = now
                if self._check_conn_popups(screen):
                    return True
                self.retry_count = 0

            # --- 3) Stuck Watchdog: đứng yên quá STUCK_TIMEOUT -> phục hồi bậc thang ---
            if self.check_stuck_watchdog(screen):
                return True

            # --- 4) Popup lạ bị mở nhầm (Cài đặt...) --- (chỉ ở vòng chính)
            if not full:
                self._clean_guard = (screen, False)
                return False
            handled = self._handle_unknown_flow(screen)
            if not handled:
                self._clean_guard = (screen, True)
            return handled

        except Exception as e:
            self.log(f"[❌ LỖI] check_connection_error: {e}")
            return False

    def _press_reload(self, pos):
        """Bấm 'Tải lại trò chơi' tại pos, chờ game tải lại làng nhà, reset lịch nâng Tường."""
        self.adb.click(*pos)
        self.log("🔄 Đã bấm Tải lại trò chơi!")
        self.set_status("🔄 Đang tải lại trò chơi...", "warn")
        self.wait_for_game_start(timeout=RELOAD_START_TIMEOUT)   # chờ ĐỘNG tới khi vào lại Làng chính
        self._wall_check_battle_mark = None   # game tải lại -> vào làng nhà là nâng Tường ngay
        self.retry_count = 0
        self._reset_unknown()
        self._has_boosted = False
        self._watchdog_reset()

    def _handle_reload_popup(self, screen):
        """Thấy button_reload.png -> bấm. Chưa có ảnh mẫu thì không đoán mò (xem _handle_unhandled_popups)."""
        if not _template_names_exist(TPL_RELOAD):
            if not self._reload_hint_logged:
                self._reload_hint_logged = True
                self.debug("ℹ️ Chưa có assets/button_reload.png (crop_tool mục 16) -> chưa nhận diện được "
                         "popup 'Tải lại trò chơi' bằng ảnh; chỉ còn tọa độ dự phòng RELOAD_GAME_POS.")
            return False
        pos = _find_any(screen, TPL_RELOAD, threshold=0.7)
        if pos is None:
            return False
        self._press_reload(pos)
        return True

    def _is_known_screen(self, screen):
        """
        True nếu thấy ít nhất 1 ảnh mẫu 'mốc' (Chiến đấu, Xây dựng, Tìm trận, Tấn công, Next, Về nhà, Tăng tốc).
        Trả None nếu không có ảnh mẫu mốc nào trong assets/ (không đủ thông tin để kết luận).
        """
        existing = [t for t in ANCHOR_TEMPLATES if _template_names_exist(t)]
        if not existing:
            return None
        for name in existing:
            if _find_any(screen, name, threshold=0.75):
                return True
        return False

    def _reset_unknown(self):
        """Màn hình đã quen / vừa phục hồi -> xóa bộ đếm 'luồng lạ'."""
        self._unknown_since = None
        self._unknown_taps = 0

    def _handle_unknown_flow(self, screen):
        """
        Xử lý giao diện / luồng lạ (menu Cài đặt, popup hệ thống, màn hình sự kiện...) mà KHÔNG quét nút X:
          - Thấy 1 ảnh mẫu 'mốc' (Chiến đấu, Xây dựng, Tìm trận, Tấn công, Next, Về nhà, Tăng tốc) -> bình thường.
          - Không thấy mốc nào liên tục UNKNOWN_SCREEN_SECONDS (5s) -> bấm 1 cú vào FALLBACK_SAFE_CLICK_POS
            (config.json -> MANUAL_POSITIONS, người dùng tự chỉnh trên GUI), rồi đếm lại 5s.
          - Đã bấm tọa độ thoát mà vẫn lạ VÀ chưa có assets/button_reload.png -> lần kế tiếp thử
            RELOAD_GAME_POS (có thể là popup 'Tải lại trò chơi' mà bot chưa có ảnh mẫu để nhận ra).
        Tắt bằng UNKNOWN_SCREEN_FALLBACK = False. KHÔNG ném lỗi.
        :return: True nếu vừa bấm để thoát / phục hồi.
        """
        try:
            if screen is None or not UNKNOWN_SCREEN_FALLBACK:
                return False

            known = self._is_known_screen(screen)
            if known is None or known:      # không có ảnh mốc để kết luận / đang ở màn hình quen
                self._reset_unknown()
                return False

            now = time.time()
            if self._unknown_since is None:
                self._unknown_since = now
                return False
            if now - self._unknown_since < UNKNOWN_SCREEN_SECONDS:
                return False

            self._unknown_since = now        # bấm xong phải lạ thêm 5s nữa mới bấm tiếp
            self._unknown_taps += 1
            no_reload_img = not _template_names_exist(TPL_RELOAD)
            if self._unknown_taps >= 2 and no_reload_img and self._unknown_taps % 2 == 0:
                pos = self._manual_pos("RELOAD_GAME_POS")
                self.debug(f"🔄 Vẫn ở luồng lạ sau khi bấm tọa độ thoát -> thử RELOAD_GAME_POS {pos}")
                self._press_reload(pos)
                return True

            pos = self._manual_pos("FALLBACK_SAFE_CLICK_POS")
            self.log(f"⚠️ Đang ở giao diện/luồng lạ -> Bấm tọa độ thoát an toàn tại ({pos[0]}, {pos[1]}) "
                     f"để khôi phục Làng nhà")
            self.adb.click(*pos)
            time.sleep(1)
            return True
        except Exception as e:
            self.debug(f"[⚠️ CẢNH BÁO] _handle_unknown_flow: {e}")
            return False

    def _handle_unhandled_popups(self, screen):
        """Tên cũ, giữ lại cho tương thích -> chuyển sang _handle_unknown_flow()."""
        return self._handle_unknown_flow(screen)

    # ------------------------------------------------------------------
    # 3a. KHỞI ĐỘNG THÔNG MINH: NHẬN DIỆN TRẠNG THÁI NGAY KHI BẤM "BẮT ĐẦU"
    # ------------------------------------------------------------------
    def detect_initial_state(self, screen, quiet=False):
        """
        Phân loại màn hình hiện tại thành ĐÚNG 1 trong 7 trạng thái (GAME_STATES), không bấm gì.
        Chỉ dùng ảnh mẫu (KHÔNG dùng scan_battle_bar: thanh UI làng chính bị quét nhầm thành ô quân
        -> kẹt IN_BATTLE). Quét tuần tự theo bảng STATE_RULES, mỗi luật tìm mọi biến thể wildcard:
          1. button_retry* / button_reload*  (0.70) -> 'CONNECTION_ERROR'
          2. button_battle* / button_build*  (0.75) -> 'HOME_VILLAGE'
          3. button_find_match_1700*         (0.75) -> 'SEARCH_MENU'
          4. button_attack*                  (0.75) -> 'ATTACK_PREPARE'
          5. button_next*                    (0.75) -> 'SCOUTING'
          6. button_return_home*             (0.75) -> 'IN_BATTLE'
             (chưa có ảnh mẫu 'Về nhà' nào thì dò nút xanh lá HSV ở 35% đáy màn hình)
          7. không khớp nút nào                     -> 'UNKNOWN'
        Kết quả so khớp được cache theo fingerprint khung hình nên gọi lại trên cùng khung gần như miễn phí.
        """
        say = (lambda *_a, **_k: None) if quiet else self.debug   # quiet=True: Watchdog gọi ngầm, không in log
        if screen is None:
            return "UNKNOWN"
        try:
            for state, patterns, threshold, message in STATE_RULES:
                if any(_find_any(screen, pattern, threshold=threshold) for pattern in patterns):
                    say(message)
                    return state

            # Dự phòng HSV: chưa có ảnh mẫu 'Về nhà' nào -> nút xanh lá lớn ở đáy màn hình = màn hình kết quả
            if not _template_names_exist(TPL_RETURN_HOME) and ImageProcessor.find_green_button_hsv(screen):
                say("✅ Nhận diện bằng màu: nút xanh lá ở đáy màn hình (IN_BATTLE)")
                return "IN_BATTLE"
        except Exception as e:
            say(f"[⚠️ CẢNH BÁO] detect_initial_state: {e}")

        say("⚠️ Chưa xác định được màn hình hiện tại (UNKNOWN)")
        return "UNKNOWN"

    def process_initial_state(self, min_gold, min_elixir, min_dark=0):
        """
        Gọi ĐÚNG 1 LẦN ngay khi bấm 'Bắt đầu' (trước vòng lặp chính, trước khi mở lại game / giả lập):
        chụp 1 ảnh -> detect_initial_state -> nhảy thẳng vào luồng tương ứng.

        :return: trạng thái đã nhận diện. Khác 'UNKNOWN' nghĩa là game ĐÃ MỞ SẴN -> main.py KHÔNG
                 cần chạy lại luồng khởi động giả lập / mở game; 'UNKNOWN' -> chạy khởi động như cũ.
        """
        state, screen = self.detect_stable_state()
        labels = {
            "HOME_VILLAGE": "Làng chính (Process Home Village)",
            "SEARCH_MENU": "Menu tìm trận (Search Match)",
            "ATTACK_PREPARE": "Bảng chuẩn bị tấn công (Search Match)",
            "SCOUTING": "Soi nhà địch (Scout / Attack)",
            "IN_BATTLE": "Đang trong trận (In Battle)",
            "CONNECTION_ERROR": "Popup mất kết nối / Tải lại trò chơi",
            "UNKNOWN": "Không nhận ra",
        }
        self.log(f"🧭 Trạng thái ban đầu: {labels[state]}")
        if state == "UNKNOWN":
            return state

        if state == "CONNECTION_ERROR":
            # Bấm 'Thử lại' / 'Tải lại' ngay
            if not self.handle_connection_error(screen):
                self.check_connection_error(screen)
            return state

        if state == "HOME_VILLAGE":
            result = self.process_home_village(screen, auto_stop_if_full=True)
            self.debug(f"🏠 Xử lý làng chính -> {result}")
            if result == "STOP":
                return state
            screen = self._capture()
            if screen is not None:
                self.check_and_search_match(screen, min_gold, min_elixir, min_dark)
        elif state in ("SEARCH_MENU", "ATTACK_PREPARE", "SCOUTING"):
            self.check_and_search_match(screen, min_gold, min_elixir, min_dark)
        elif state == "IN_BATTLE":
            self._has_boosted = False
            if self._wait_battle_end():
                self._leave_battle()
        return state

    # ------------------------------------------------------------------
    # 3c. STUCK WATCHDOG + TỰ MỞ LẠI GAME / KHỞI ĐỘNG LẠI LDPLAYER
    # ------------------------------------------------------------------
    def _guard_connection(self, screen=None):
        """
        Dùng trong MỌI vòng chờ con: kiểm tra popup mất kết nối (Thử lại / Tải lại) + Stuck Watchdog.
        screen=None -> tự chụp ảnh mới. Trả True nếu vừa phục hồi (luồng gọi phải THOÁT vòng chờ).
        KHÔNG bao giờ ném lỗi.
        """
        try:
            if screen is None:
                screen = self._capture()
            if screen is None:
                return False
            return bool(self.check_connection_error(screen, full=False))
        except Exception as e:
            self.debug(f"[⚠️ CẢNH BÁO] _guard_connection: {e}")
            return False

    def _watchdog_reset(self):
        """Vừa phục hồi / vừa đổi cảnh -> đếm lại thời gian kẹt từ đầu."""
        self.last_state_change_time = time.time()
        self._stuck_stage = 0
        self._stuck_stage_time = 0.0
        self._wd_last_frame = None
        self._wd_last_fp = None
        self._wd_last_check = time.time()

    def wait_for_game_start(self, timeout=GAME_START_TIMEOUT, interval=GAME_START_POLL):
        """
        Xác nhận game ĐÃ VÀO Làng chính (HOME_VILLAGE) bằng thăm dò màn hình - KHÔNG chờ cố định.
        Chỉ trả True khi thấy nút Cái Búa Xây dựng (button_build*.png) HOẶC nút Chiến đấu (button_battle*.png).
        Trong lúc chờ: thấy popup 'Thử lại' / 'Tải lại' thì bấm luôn (không gọi Watchdog để tránh đệ quy mở lại game).
        :return: True nếu đã vào làng chính; False nếu hết `timeout` hoặc có lệnh dừng.
        """
        started = time.time()
        last_log = 0.0
        self.set_status("⏳ Đang chờ game vào Làng chính...", "wait")
        while time.time() - started < timeout:
            if self._stop_requested():
                return False
            screen = self._capture(fresh=True)
            if screen is not None:
                try:
                    if _find_any(screen, TPL_BUILD, threshold=0.75) or _find_any(screen, TPL_BATTLE, threshold=0.75):
                        self.log(f"✅ Game đã vào Làng chính (thấy nút Búa / Chiến đấu sau {time.time() - started:.0f}s)")
                        self.set_status("🏠 Đang ở Làng chính", "home")
                        return True
                    for tpl in (TPL_RETRY, TPL_RELOAD):          # popup lỗi lúc game đang tải
                        pos = _find_any(screen, tpl, threshold=0.70)
                        if pos:
                            self.log("🔌 Popup lỗi khi game đang mở -> bấm xử lý rồi chờ tiếp")
                            self.adb.click(*pos)
                            time.sleep(1.0)
                            break
                except Exception as e:
                    self.debug(f"[⚠️ CẢNH BÁO] wait_for_game_start: {e}")
            now = time.time()
            if now - last_log >= 10:
                last_log = now
                self.debug(f"⏳ Chờ game vào Làng chính... ({int(now - started)}s/{int(timeout)}s)")
            self._sleep_interruptible(interval)
        self.log(f"[⚠️ CẢNH BÁO] Quá {int(timeout)}s chưa thấy nút Búa / Chiến đấu -> chưa xác nhận được Làng chính")
        return False

    def launch_game_dynamic(self, force_stop=False, timeout=GAME_START_TIMEOUT):
        """Mở game (tùy chọn force-stop trước) rồi chờ ĐỘNG tới khi vào Làng chính. True nếu đã xác nhận."""
        if force_stop:
            self._adb_shell(f"am force-stop {GAME_PACKAGE}")
            time.sleep(2)
        self._launch_game_safe()
        self.debug("⏳ Đã gửi lệnh mở Game, thăm dò màn hình thay vì chờ cố định...")
        return self.wait_for_game_start(timeout=timeout)

    def _wait_emulator_ready(self, timeout=EMULATOR_BOOT_TIMEOUT):
        """Chờ LDPlayer boot xong: lặp nối lại ADB tới khi chụp được ảnh (thay cho sleep cố định)."""
        started = time.time()
        while time.time() - started < timeout:
            if self._stop_requested():
                return False
            try:
                self.adb.connect()
            except Exception:
                pass
            if self._capture(fresh=True) is not None:
                self.log(f"✅ LDPlayer đã sẵn sàng sau {time.time() - started:.0f}s")
                return True
            self._sleep_interruptible(EMULATOR_BOOT_POLL)
        self.log(f"[⚠️ CẢNH BÁO] LDPlayer chưa chụp được ảnh sau {int(timeout)}s")
        return False

    def _launch_game_safe(self):
        """Mở game: ưu tiên emulator.launch_game(package); không nhận tham số -> gọi không tham số; lỗi -> monkey qua ADB."""
        try:
            try:
                self.emulator.launch_game(GAME_PACKAGE)
            except TypeError:
                self.emulator.launch_game()
        except Exception as e:
            self.debug(f"[⚠️ CẢNH BÁO] emulator.launch_game lỗi ({e}) -> mở game bằng ADB monkey...")
            self._adb_shell(f"monkey -p {GAME_PACKAGE} -c android.intent.category.LAUNCHER 1")

    def _reboot_emulator(self):
        """Cấp nặng: đóng + mở lại LDPlayer, nối lại ADB, mở lại game."""
        self.log("🔁 Khởi động lại LDPlayer...")
        self.set_status("🔁 Đang khởi động lại LDPlayer...", "warn")
        try:
            self.emulator.close_emulator()
            time.sleep(5)
            try:   # cùng cấu hình như main.py: 960x540, dpi 160, 30FPS (tọa độ bot dựa trên độ phân giải này)
                self.emulator.setup_and_launch(width=960, height=540, dpi=160, fps=30)
            except TypeError:
                self.emulator.setup_and_launch()
            self._wait_emulator_ready()                        # chờ giả lập boot ĐỘNG (ADB chụp được ảnh)
            self.launch_game_dynamic()                         # mở game + chờ thấy nút Búa / Chiến đấu
        except Exception as e:
            self.log(f"[❌ LỖI] Không khởi động lại được LDPlayer: {e}")
        self._wall_check_battle_mark = None   # vào lại game -> nâng Tường ngay
        self._has_boosted = False
        self.retry_count = 0
        self._game_restarts = 0
        self._last_recovery_time = time.time()
        self._reset_unknown()
        self._watchdog_reset()

    def restart_game_app(self):
        """Buộc dừng App Clash of Clans qua ADB và mở lại từ đầu."""
        self.log("⚠️ Phát hiện Bot bị kẹt/treo quá lâu -> Tiến hành Force Stop và mở lại Game...")
        self.set_status("🔄 Bot bị kẹt - đang mở lại Game...", "warn")
        try:
            # 1. Reset các cờ trạng thái bot
            self._wall_check_battle_mark = None
            self._has_boosted = False
            self._reset_unknown()
            # 2. Tắt app + mở lại game, rồi chờ ĐỘNG tới khi thấy nút Búa / Chiến đấu (không sleep cố định)
            self.launch_game_dynamic(force_stop=True)
        except Exception as e:
            self.log(f"[❌ LỖI] Không thể tự mở lại Game: {e}")
        finally:
            self._last_recovery_time = time.time()
            self._watchdog_reset()

    def _stuck_timeout(self):
        """Số giây đứng yên tối đa: config.json -> STUCK_TIMEOUT, không có/sai thì self.STUCK_TIMEOUT (mặc định 30)."""
        try:
            value = float(load_json_config().get("STUCK_TIMEOUT", self.STUCK_TIMEOUT))
            return value if value > 0 else float(self.STUCK_TIMEOUT)
        except Exception:
            return float(self.STUCK_TIMEOUT)

    def _safe_escape_pos(self):
        """Tọa độ thoát an toàn: MANUAL_POSITIONS.SAFE_ESCAPE_POS, thiếu thì FALLBACK_SAFE_CLICK_POS; không có -> None."""
        for key in ("SAFE_ESCAPE_POS", "FALLBACK_SAFE_CLICK_POS"):
            try:
                pos = self._manual_pos(key)
                if len(pos) >= 2:
                    return (int(pos[0]), int(pos[1]))
            except Exception:
                continue
        return None

    def check_stuck_watchdog(self, screen, current_state=None):
        """
        Giám sát chống kẹt: theo dõi thời gian ĐỨNG YÊN của fingerprint màn hình (MD5 ảnh thu nhỏ 64x36).
        Khung hình giống hệt, hoặc chỉ lệch nhẹ do hoạt ảnh làng (< STUCK_FRAME_DIFF), thì vẫn tính là đứng yên.
        Đứng yên quá STUCK_TIMEOUT (mặc định 30s, config.json ghi đè) ở BẤT KỲ bước nào -> phục hồi bậc thang:
          Cấp nhẹ : bấm SAFE_ESCAPE_POS (thoát menu / popup phụ).
          Cấp trung: sau STUCK_ESCALATE_SECONDS vẫn kẹt -> restart_game_app() (force-stop + mở lại).
          Cấp nặng : đã mở lại game STUCK_MAX_GAME_RESTARTS lần mà vẫn kẹt -> _reboot_emulator().
        Chỉ so khung thu nhỏ, KHÔNG so khớp ảnh mẫu (rất nhẹ); throttle STUCK_CHECK_INTERVAL.
        :return: True nếu vừa chạy một bước phục hồi (luồng gọi nên thoát vòng chờ).
        """
        try:
            if not STUCK_WATCHDOG_ENABLE or screen is None or self._stop_requested():
                return False
            now = time.time()
            started = getattr(self, "_battle_started_at", None)
            if started is not None and now - started < BATTLE_WATCHDOG_GRACE:
                self._watchdog_reset()   # đang trong trận hợp lệ -> không coi là kẹt, đếm lại từ đầu
                return False
            since_poll = now - self._wd_last_check
            if since_poll < STUCK_CHECK_INTERVAL and self._wd_last_check:
                return False                      # throttle
            self._wd_last_check = now

            # Luồng vừa bận việc dài hợp lệ (nâng Tường, thả quân...) và không gọi Watchdog -> đếm lại
            if since_poll > STUCK_POLL_GAP:
                self.last_state_change_time = now
                self._stuck_stage = 0
                self._wd_last_frame = None
                self._wd_last_fp = None

            # --- Có chuyển cảnh không? So fingerprint; khác nhau thì kiểm tra thêm độ lệch để bỏ qua hoạt ảnh nhẹ ---
            fp, thumb = frame_signature(screen)
            scene_changed = False
            if fp is not None:
                if self._wd_last_fp is not None and fp != self._wd_last_fp:
                    last = self._wd_last_frame
                    if last is not None and last.shape == thumb.shape:
                        scene_changed = float(cv2.absdiff(thumb, last).mean()) >= STUCK_FRAME_DIFF
                    else:
                        scene_changed = True
                self._wd_last_fp, self._wd_last_frame = fp, thumb

            if scene_changed:
                self.last_state_change_time = now
                self._stuck_stage = 0
                if self._game_restarts and now - self._last_recovery_time > STUCK_HEALTHY_RESET:
                    self._game_restarts = 0       # chạy khỏe đủ lâu -> xóa bộ đếm
                return False

            idle = now - self.last_state_change_time
            if idle < self._stuck_timeout():
                return False

            # Chỉ khi thật sự kẹt mới tốn công nhận diện trạng thái (phục vụ log)
            if current_state is None:
                current_state = self.detect_initial_state(screen, quiet=True)
            self.current_state = current_state
            fp_text = (fp or "?")[:8]

            # ----- Cấp nhẹ -----
            if self._stuck_stage == 0:
                pos = self._safe_escape_pos()
                if pos is None:
                    self.log(f"🐢 [Watchdog] Kẹt {int(idle)}s ở {self.current_state} (fp {fp_text}) nhưng chưa cấu hình "
                             f"SAFE_ESCAPE_POS / FALLBACK_SAFE_CLICK_POS -> bỏ qua cấp 1")
                else:
                    self.log(f"🐢 [Watchdog] Kẹt {int(idle)}s ở trạng thái {self.current_state} (fp {fp_text}) -> "
                             f"Cấp 1: bấm tọa độ an toàn {pos}")
                    self.adb.click(*pos)
                self._stuck_stage = 1
                self.set_status("🐢 Bot bị kẹt - đang thử thoát màn hình lạ...", "warn")
                self._stuck_stage_time = now
                return True

            # Chờ STUCK_ESCALATE_SECONDS xem cú bấm có tác dụng không
            if now - self._stuck_stage_time < STUCK_ESCALATE_SECONDS:
                return False

            # ----- Cấp trung -----
            if self._game_restarts < STUCK_MAX_GAME_RESTARTS:
                self._game_restarts += 1
                self.log(f"🐢 [Watchdog] Vẫn kẹt sau cú bấm -> Cấp 2: tắt & mở lại Game "
                         f"(lần {self._game_restarts}/{STUCK_MAX_GAME_RESTARTS})")
                self.restart_game_app()
                return True

            # ----- Cấp nặng -----
            self.log(f"🐢 [Watchdog] Mở lại Game {STUCK_MAX_GAME_RESTARTS} lần vẫn kẹt -> "
                     f"Cấp 3: KHỞI ĐỘNG LẠI LDPlayer")
            self._reboot_emulator()
            return True
        except Exception as e:
            self.debug(f"[⚠️ CẢNH BÁO] check_stuck_watchdog: {e}")
            return False

    def update_stuck_watchdog(self, screen, current_state=None):
        """Tên cũ, giữ lại cho tương thích -> check_stuck_watchdog()."""
        return self.check_stuck_watchdog(screen, current_state)

    # ------------------------------------------------------------------
    # 3b. TIỆN ÍCH HIỆU NĂNG (chống BSoD / sập máy)
    # ------------------------------------------------------------------
    def _guard_connection_throttled(self, min_gap=None, screen=None):
        """Như _guard_connection nhưng chỉ chụp+kiểm tra nếu đã qua `min_gap` giây kể từ lần trước."""
        gap = DEPLOY_GUARD_GAP if min_gap is None else min_gap
        now = time.time()
        if now - getattr(self, "_last_guard_at", 0.0) < gap:
            return False
        self._last_guard_at = now
        return self._guard_connection(screen)

    def _maybe_gc(self, force=False):
        """Dọn bộ nhớ gc.collect() định kỳ (mỗi GC_INTERVAL_SECONDS giây). Không ném lỗi."""
        now = time.time()
        if force or now - self._last_gc >= GC_INTERVAL_SECONDS:
            self._last_gc = now
            try:
                gc.collect()
            except Exception:
                pass

    def _ensure_stall_monitor(self):
        """Khởi chạy luồng giám sát (1 lần): bot đứng im quá STALL_DUMP_SECONDS thì ghi log vị trí code đang chạy."""
        self._main_ident = threading.get_ident()
        self._last_tick_at = time.time()
        if getattr(self, "_stall_thread", None) is not None:
            return

        def run():
            reported_at = 0.0
            while True:
                time.sleep(2.0)
                try:
                    if self._stop_requested():
                        reported_at = 0.0
                        continue
                    now = time.time()
                    idle = now - self._last_tick_at
                    if idle < STALL_DUMP_SECONDS or now - reported_at < STALL_DUMP_SECONDS:
                        continue
                    frame = sys._current_frames().get(self._main_ident)
                    if frame is None:
                        continue
                    where = []
                    for fs in traceback.extract_stack(frame)[-6:]:
                        where.append(f"{os.path.basename(fs.filename)}:{fs.lineno} {fs.name}()")
                    reported_at = now
                    self.debug(f"🧊 Bot đứng {int(idle)}s chưa xong vòng lặp -> đang ở: " + " > ".join(where))
                except Exception:
                    pass

        self._stall_thread = threading.Thread(target=run, name="stall-monitor", daemon=True)
        self._stall_thread.start()

    def loop_tick(self):
        """
        Gọi ở CUỐI MỖI VÒNG `while` của luồng chính (main.py): nghỉ LOOP_SLEEP (0.4s) để không chụp/xử lý
        ảnh liên tục làm treo CPU/RAM, đồng thời dọn bộ nhớ định kỳ.
        """
        self._ensure_stall_monitor()
        now = time.time()
        total = now - _PROF["t0"]
        if total >= SLOW_TICK_LOG:
            self.debug(f"⏱️ Vòng lặp {total:.1f}s | chụp ảnh {_PROF['cap_s']:.1f}s ({_PROF['cap_n']} lần) | "
                     f"so khớp ảnh {_PROF['match_s']:.1f}s ({_PROF['match_n']} lần, tái dùng {_PROF['hit_n']}) | "
                     f"khác {total - _PROF['cap_s'] - _PROF['match_s']:.1f}s")
        time.sleep(min(LOOP_SLEEP, LOOP_SLEEP_CAP))
        self._maybe_gc()
        _PROF.update(match_s=0.0, match_n=0, hit_n=0, cap_s=0.0, cap_n=0, t0=time.time())

    def _sleep_interruptible(self, seconds):
        """Ngủ `seconds` giây nhưng thoát sớm nếu có lệnh dừng."""
        end = time.time() + seconds
        while time.time() < end:
            if self._stop_requested():
                return False
            time.sleep(min(0.2, max(0.0, end - time.time())))
        return not self._stop_requested()

    # ------------------------------------------------------------------
    # 4. NÂNG CẤP TƯỜNG TỪ MENU "CÔNG TRÌNH VÀ TƯỜNG"
    # ------------------------------------------------------------------
    def _close_menu(self, times=None):
        """
        Đóng TRIỆT ĐỂ menu Búa + mọi popup công trình bị chọn nhầm trên map: bấm FALLBACK_SAFE_CLICK_POS
        nhiều lần (mặc định 3, config.json -> WALL_CLOSE_CLICKS; mỗi lần cách nhau 0.3s).
        """
        try:
            if times is None:
                try:
                    times = int(load_json_config().get("WALL_CLOSE_CLICKS", 3))
                except Exception:
                    times = 3
            times = max(1, min(5, int(times)))
            pos = self._manual_pos("FALLBACK_SAFE_CLICK_POS")
            for i in range(times):
                self.adb.click(*pos)
                time.sleep(0.3)
            time.sleep(0.2)
        except Exception:
            pass

    def _manual_pos(self, key):
        """Tọa độ thủ công (config.json -> MANUAL_POSITIONS[key]) dạng (x, y); luôn có giá trị hợp lệ."""
        return tuple(load_json_config()["MANUAL_POSITIONS"][key])

    def _wall_retry_limit(self):
        """Số trận phải đánh trước khi thử nâng Tường lại (config.json -> WALL_RETRY_BATTLES_LIMIT)."""
        try:
            return int(load_json_config().get("WALL_RETRY_BATTLES_LIMIT", WALL_RETRY_BATTLES_LIMIT))
        except Exception:
            return WALL_RETRY_BATTLES_LIMIT

    @staticmethod
    def _wall_item_variants(filename):
        """
        Ảnh gốc + mọi biến thể của 1 ảnh dòng Tường, vd "wall_item_lvA_gold.png" ->
        wall_item_lvA_gold.png, wall_item_lvA_gold_2.png, ... (mẫu wall_item_lvA_gold*.png).
        Mỗi dòng (Cấp A/B x Vàng/Dầu) chỉ gom biến thể của CHÍNH nó nên loại tài nguyên không bị lẫn.
        """
        stem, ext = os.path.splitext(filename)
        return ImageProcessor.expand_template_paths(asset(f"{stem}*{ext}"))

    def _find_wall_row(self, screen_menu):
        """
        Quét 4 ảnh mẫu dòng đề xuất Tường (WALL_ITEM_TEMPLATES) trên screen_menu, trả về
        (tên, tâm_dòng, loại tài nguyên "gold"/"elixir") của ảnh KHỚP TỐT NHẤT;
        không có -> (None, None, None).

        Mỗi ảnh mẫu được so khớp theo 2 phần, BỎ QUA phần giữa chứa số lượng "xN":
          - phần TRÁI (icon + chữ "Tường thành")      -> đúng là dòng Tường;
          - phần PHẢI (giá + icon Vàng/Dầu, chữ trắng nếu đủ / đỏ nếu thiếu)
                                                       -> loại tài nguyên + đang đủ hay không.
        Điểm của ảnh mẫu = min(điểm trái, điểm phải); phải >= WALL_ITEM_THRESHOLD.
        """
        best_name, best_pos, best_res, best_score = None, None, None, 0.0
        for name, filename, resource in WALL_ITEM_TEMPLATES:
            for template_path in self._wall_item_variants(filename):   # ảnh gốc + biến thể _2, _3...
                pos, score = self._match_wall_template(screen_menu, template_path)
                if pos is not None and score >= WALL_ITEM_THRESHOLD and score > best_score:
                    best_name, best_pos, best_res, best_score = name, pos, resource, score
        if best_pos is not None:
            self.debug(f"🔍 Khớp dòng {best_name} (điểm {best_score:.2f})")
        return best_name, best_pos, best_res

    def _match_wall_template(self, screen, template_path):
        """
        So khớp phần trái + phần phải của ảnh mẫu (bỏ phần giữa chứa "xN").
        Phần trái tìm trên toàn màn hình (tối đa WALL_ROW_CANDIDATES ứng viên); phần phải chỉ
        kiểm tra tại đúng vị trí tương ứng trên CÙNG dòng đó (lệch tối đa WALL_RIGHT_TOL px).
        :return: ((x, y) tâm của CẢ dòng, điểm 0..1); (None, 0.0) nếu không khớp / lỗi.
        """
        try:
            tpl = ImageProcessor.load_template(template_path)   # có cache, không đọc đĩa lặp lại (chỉ đọc, không sửa)
            if tpl is None or screen is None:
                return None, 0.0
            h, w = tpl.shape[:2]
            sh, sw = screen.shape[:2]
            lw = max(8, int(w * WALL_ITEM_LEFT_RATIO))
            rw = max(8, int(w * WALL_ITEM_RIGHT_RATIO))
            if h > sh or w > sw:
                return None, 0.0
            left, right = tpl[:, :lw], tpl[:, w - rw:]

            res = cv2.matchTemplate(screen, left, cv2.TM_CCOEFF_NORMED)
            res = np.nan_to_num(res, nan=-1.0, posinf=-1.0, neginf=-1.0)
            best_pos, best_score = None, 0.0
            for _ in range(WALL_ROW_CANDIDATES):
                _, lscore, _, (lx, ly) = cv2.minMaxLoc(res)
                if lscore < WALL_ITEM_THRESHOLD:
                    break
                # triệt ứng viên này khỏi bản đồ điểm để lần sau lấy ứng viên khác
                res[max(0, ly - h // 2):ly + h // 2 + 1, max(0, lx - lw // 2):lx + lw // 2 + 1] = -1.0

                ex = lx + w - rw   # vị trí phần phải kỳ vọng trên cùng dòng
                x1, x2 = max(0, ex - WALL_RIGHT_TOL), min(sw, ex + rw + WALL_RIGHT_TOL)
                y1, y2 = max(0, ly - WALL_RIGHT_TOL), min(sh, ly + h + WALL_RIGHT_TOL)
                win = screen[y1:y2, x1:x2]
                if win.shape[0] < h or win.shape[1] < rw:
                    continue
                rscore = float(cv2.minMaxLoc(cv2.matchTemplate(win, right, cv2.TM_CCOEFF_NORMED))[1])
                score = min(float(lscore), rscore if np.isfinite(rscore) else 0.0)
                if score > best_score:
                    best_score, best_pos = score, (min(sw - 1, lx + w // 2), ly + h // 2)
            return best_pos, best_score
        except Exception as e:
            self.debug(f"[⚠️ CẢNH BÁO] _match_wall_template: {e}")
            return None, 0.0

    def _wall_menu_settings(self):
        """
        Đọc từ config.json: (độ nhạy thẻ Tường, số lần vuốt tối đa, thời gian mỗi lần vuốt ms).
        Thiếu / sai kiểu -> dùng hằng số mặc định ở đầu file.

        LƯU Ý: WALL_MATCH_THRESHOLD CHỈ được truyền vào ImageProcessor.find_wall_cards_in_menu
        (quét thẻ Tường trong menu Búa). Mọi nút UI khác dùng ngưỡng riêng của chúng
        (find_image_location(..., threshold=...), WALL_ITEM_THRESHOLD...).
        """
        cfg = load_json_config()
        try:
            threshold = float(cfg.get("WALL_MATCH_THRESHOLD", 0.95))
        except (TypeError, ValueError):
            threshold = 0.95
        try:
            max_swipes = int(cfg.get("WALL_MENU_MAX_SWIPES", WALL_MENU_MAX_SWIPES))
        except (TypeError, ValueError):
            max_swipes = WALL_MENU_MAX_SWIPES
        try:
            duration = int(cfg.get("WALL_MENU_SWIPE_DURATION", WALL_MENU_SWIPE_DURATION))
        except (TypeError, ValueError):
            duration = WALL_MENU_SWIPE_DURATION
        return max(0.45, min(0.95, threshold)), max(1, max_swipes), max(100, duration)

    def _guess_wall_resource(self, screen, y):
        """
        Đoán dòng Tường tại độ cao y dùng Vàng hay Dầu + đang ĐỦ tài nguyên không, bằng phần PHẢI
        (giá + icon, chữ trắng nếu đủ / đỏ nếu thiếu) của 4 ảnh mẫu WALL_ITEM_TEMPLATES, tìm trong
        dải ngang quanh y. Trả ("gold"/"elixir", điểm) hoặc (None, 0.0) nếu không khớp
        (không có ảnh mẫu, hoặc thiếu tài nguyên -> chữ đỏ không khớp).
        """
        best_res, best_score = None, 0.0
        sh, sw = screen.shape[:2]
        for _name, filename, resource in WALL_ITEM_TEMPLATES:
            for path in self._wall_item_variants(filename):   # ảnh gốc + biến thể _2, _3...
                try:
                    tpl = ImageProcessor.load_template(path)
                    if tpl is None:
                        continue
                    h, w = tpl.shape[:2]
                    rw = max(8, int(w * WALL_ITEM_RIGHT_RATIO))
                    right = tpl[:, w - rw:]
                    y1, y2 = max(0, y - h // 2 - WALL_RIGHT_TOL), min(sh, y + h // 2 + WALL_RIGHT_TOL)
                    band = screen[y1:y2, :]
                    if band.shape[0] < h or band.shape[1] < rw:
                        continue
                    score = float(cv2.minMaxLoc(cv2.matchTemplate(band, right, cv2.TM_CCOEFF_NORMED))[1])
                    if np.isfinite(score) and score > best_score:
                        best_res, best_score = resource, score
                except Exception as e:
                    self.debug(f"[⚠️ CẢNH BÁO] _guess_wall_resource: {e}")
        return (best_res, best_score) if best_score >= WALL_ITEM_THRESHOLD - 0.10 else (None, 0.0)

    def _find_wall_row_hybrid(self, screen_menu, threshold):
        """
        Phương án Hybrid (khi 4 ảnh mẫu cả dòng không khớp): ImageProcessor.find_wall_cards_in_menu
        (Masked Template card_wall - bỏ qua vùng chữ số xN - / icon_wall -> OCR bổ trợ), lấy độ cao Y
        của thẻ rồi xác định Vàng/Dầu + đủ tài nguyên. Tọa độ nút Nâng (Vàng/Dầu) lấy từ
        MANUAL_POSITIONS trong _click_upgrade_wall_row.
        `threshold` (WALL_MATCH_THRESHOLD) chỉ dùng cho lần quét thẻ Tường này.
        :return: (tên, (x, y), resource) hoặc (None, None, None).
        """
        cands = ImageProcessor.find_wall_cards_in_menu(screen_menu, threshold=threshold)
        for c in cands:
            resource, rscore = self._guess_wall_resource(screen_menu, c["y"])
            if resource is None:
                self.debug(f"ℹ️ Thấy thẻ Tường ở y={c['y']} nhưng không xác định được Vàng/Dầu hoặc đang thiếu tài nguyên -> bỏ qua")
                continue
            if c["method"] == "template":
                self.debug(f"🎯 Phát hiện thẻ Tường qua khớp ảnh mẫu có Mask xN "
                         f"(Độ khớp: {c['score'] * 100:.0f}%, y={c['y']})")
            else:
                self.debug("🔤 Phát hiện thẻ Tường qua OCR văn bản")
            if c["kind"] == "card":
                x = c["x"]
            else:   # icon/OCR: bấm giữa dòng, dùng x người dùng đã chỉnh (MANUAL_POSITIONS) nếu có
                try:
                    x = int(load_json_config()["MANUAL_POSITIONS"].get(
                        "BUILDER_MENU_SCROLL_START", BUILDER_MENU_SCROLL_START)[0])
                except Exception:
                    x = BUILDER_MENU_SCROLL_START[0]
            label = "Vàng" if resource == "gold" else "Dầu"
            return f"Tường - {label}", (x, c["y"]), resource
        return None, None, None

    def _swipe_wall_menu(self, duration):
        """
        Vuốt cuộn danh sách đề xuất trong menu Búa. GIỮ NGUYÊN điểm bắt đầu và trục X như bản cũ
        (BUILDER_MENU_SCROLL_START / BUILDER_MENU_SCROLL_END trong config.py); CHỈ kéo dài quãng đường trục Y
        gấp WALL_MENU_SWIPE_MULT (mặc định 1.5) lần so với bản cũ, cùng hướng, thời gian `duration` ms (200-300).
        """
        try:
            mult = float(load_json_config().get("WALL_MENU_SWIPE_MULT", WALL_MENU_SWIPE_MULT_DEFAULT))
        except (TypeError, ValueError):
            mult = WALL_MENU_SWIPE_MULT_DEFAULT
        mult = max(0.5, min(3.0, mult))
        sx, sy = BUILDER_MENU_SCROLL_START
        dy = BUILDER_MENU_SCROLL_END[1] - sy
        step = min(abs(dy), WALL_MENU_SWIPE_DISTANCE) * (1 if dy >= 0 else -1)   # đúng công thức bản cũ
        ey = int(max(8, min(532, sy + step * mult)))                              # chỉ Y đổi, kẹp trong màn hình
        self._swipe((sx, sy), (sx, ey), duration=duration)
        self.debug(f"↕️ Vuốt menu Búa x{mult:g}: ({sx}, {sy}) -> ({sx}, {ey}) ({duration}ms)")
        return (sx, sy, sx, ey)

    def _wall_resource_priority(self):
        """Ưu tiên tài nguyên khi nhiều thẻ Tường cùng nâng được (config.json -> WALL_RESOURCE_PRIORITY): AUTO | GOLD | ELIXIR."""
        try:
            value = str(load_json_config().get("WALL_RESOURCE_PRIORITY", "AUTO")).strip().upper()
        except Exception:
            value = "AUTO"
        return value if value in ("GOLD", "ELIXIR") else "AUTO"

    def _pick_wall_card(self, ready_cards):
        """Chọn 1 thẻ Tường trong danh sách đã nâng được: ưu tiên loại tài nguyên đã cấu hình, rồi thẻ nằm trên cùng."""
        prefer = self._wall_resource_priority()
        return min(ready_cards, key=lambda c: (0 if c["resource_type"] == prefer else 1, c["y"]))

    def _open_build_menu(self):
        """Chụp màn hình, tìm nút Búa (button_build.png) và bấm mở menu. True nếu đã bấm."""
        screen = self._capture()
        if screen is None:
            self.debug("⚠️ Không chụp được màn hình -> bỏ qua nâng Tường")
            return False
        if self._guard_connection(screen):
            self.log("🔌 Có popup mất kết nối đè lên khi mở menu Búa -> đã phục hồi, bỏ qua lần nâng Tường này")
            return False
        if not _template_names_exist(TPL_BUILD):
            self.debug("⚠️ Không có ảnh mẫu button_build.png -> không thể mở menu")
            return False
        build_pos = _find_any(screen, TPL_BUILD, threshold=0.75)
        if build_pos is None:
            self.log("⚠️ Không thấy nút Cái Búa -> bỏ qua lần nâng Tường này")
            return False
        self.adb.click(*build_pos)
        time.sleep(0.8)
        return True

    # Từ khóa (đã bỏ dấu) cho thấy đã bấm nhầm sang UI công trình khác trên map
    WALL_POPUP_WRONG_KEYWORDS = ("thong tin", "tong quan", "phong thu")

    def _verify_wall_popup(self, screen):
        """
        Xác thực màn hình sau khi bấm thẻ Tường có phải popup nâng Tường hợp lệ không
        (ảnh mẫu wall_popup_ok / wrong_building_popup, rồi OCR chữ Tường). Logic nằm trong
        ImageProcessor.verify_wall_popup; config.json WALL_POPUP_STRICT = true -> không kết luận được = NHẦM.
        :return: (ok, lý do)
        """
        try:
            strict = bool(load_json_config().get("WALL_POPUP_STRICT", False))
        except Exception:
            strict = False
        return ImageProcessor.verify_wall_popup(screen, strict=strict, threshold=WALL_ITEM_THRESHOLD)

    def _click_upgrade_wall_row(self, name, row_pos, resource):
        """
        Bấm dòng Tường -> XÁC THỰC popup -> nút Nâng (Vàng/Dầu) -> Xác nhận.
        True nếu đã bấm Xác nhận; False nếu bấm nhầm công trình khác (đã đóng UI nhầm, KHÔNG bấm mù).
        """
        self.debug(f"🧱 Tìm thấy dòng {name} trong danh sách!")
        self.adb.click(*row_pos)
        time.sleep(WALL_POPUP_DELAY)          # chờ popup nâng cấp hiện ra

        # --- Xác thực popup trước khi bấm bất kỳ nút nào ---
        ok, reason = self._verify_wall_popup(self._capture(fresh=True))
        if not ok:
            time.sleep(0.5)       # popup có thể còn đang trượt ra -> xác thực lại 1 lần với ảnh mới
            ok, reason = self._verify_wall_popup(self._capture(fresh=True))
        if not ok:
            self.log("[⚠️ BẤM NHẦM] Bấm lệch thẻ Tường làm mở UI công trình khác -> "
                     f"Hủy lần nâng này và đóng UI nhầm. ({reason})")
            self._close_menu()
            self._close_menu()
            return False
        self.debug(f"✅ Xác thực popup nâng Tường: {reason}")

        btn_key = "WALL_UPGRADE_GOLD_BTN" if resource == "gold" else "WALL_UPGRADE_ELIXIR_BTN"
        upgrade_pos = self._manual_pos(btn_key)
        self.adb.click(*upgrade_pos)
        self.debug(f"👆 Bấm nút Nâng cấp bằng {'Vàng' if resource == 'gold' else 'Dầu'} tại {upgrade_pos}")
        time.sleep(WALL_POPUP_DELAY)          # chờ popup xác nhận

        confirm_pos = self._manual_pos("WALL_CONFIRM_BTN")
        self.adb.click(*confirm_pos)
        self.debug(f"✔️ Đã bấm Xác nhận nâng Tường tại {confirm_pos}!")
        self.log("✅ Đã nâng 1 khúc Tường (đã bấm Xác nhận)")
        time.sleep(0.8)
        return True

    def _wait_menu_settled(self):
        """
        Chờ menu ngừng trôi (animation mở menu / quán tính cuộn) rồi trả về khung hình đứng yên
        để quét. Hết WALL_MENU_SETTLE_TIMEOUT thì trả về khung cuối. None nếu không chụp được.
        """
        def small(img):
            return cv2.cvtColor(cv2.resize(img, (160, 90)), cv2.COLOR_BGR2GRAY)

        prev = self._capture()
        if prev is None:
            return None
        deadline = time.time() + WALL_MENU_SETTLE_TIMEOUT
        while time.time() < deadline:
            time.sleep(0.25)
            cur = self._capture()
            if cur is None:
                return prev
            try:
                diff = float(np.mean(cv2.absdiff(small(prev), small(cur))))
            except Exception:
                return cur
            prev = cur
            if diff < WALL_MENU_SETTLE_DIFF:
                break
        return prev

    def _reopen_build_menu_if_closed(self):
        """
        Sau khi nâng 1 khúc Tường, game có thể tự đóng menu. Thấy lại nút Búa (button_build.png)
        nghĩa là menu đã đóng -> bấm mở lại để quét tiếp. Không thấy -> menu còn mở, quét tại chỗ
        (giữ nguyên vị trí cuộn, vì thẻ Tường vẫn ở đó nếu vừa nâng thành công).
        :return: True nếu đã mở lại menu (menu về đầu danh sách).
        """
        screen = self._capture()
        if screen is None or not _template_names_exist(TPL_BUILD):
            return False
        build_pos = _find_any(screen, TPL_BUILD, threshold=0.75)
        if build_pos is None:
            return False
        self.debug("🔁 Menu đã đóng sau khi nâng -> mở lại để quét thẻ Tường tiếp")
        self.adb.click(*build_pos)
        time.sleep(0.8)
        return True

    @staticmethod
    def _wall_card_state(card):
        """CAN_UPGRADE (chữ giá TRẮNG) | OUT_OF_RESOURCE (chữ giá ĐỎ hoặc XÁM = không đủ tài nguyên)."""
        return WALL_CAN_UPGRADE if card.get("can_upgrade") else WALL_OUT_OF_RESOURCE

    def _wall_stop_mode(self):
        """
        Khi nào dừng hẳn nâng Tường (config.json -> WALL_STOP_MODE):
          "ANY" (mặc định, đúng yêu cầu) = BẤT KỲ thẻ Tường nào hiện OUT_OF_RESOURCE là dừng;
          "ALL" = chỉ dừng khi KHÔNG còn thẻ Tường nào nâng được (thẻ đỏ chỉ bị bỏ qua).
        """
        try:
            return "ALL" if str(load_json_config().get("WALL_STOP_MODE", "ANY")).strip().upper() == "ALL" else "ANY"
        except Exception:
            return "ANY"

    def _reopen_wall_menu(self):
        """Mở lại menu Búa từ đầu danh sách: menu đã đóng (thấy nút Búa) thì bấm Búa, còn mở thì đóng sạch rồi bấm Búa."""
        screen = self._capture(fresh=True)
        if not (screen is not None and self._see_hammer(screen)):
            self._close_menu(times=2)
            time.sleep(0.3)
        return self._open_build_menu()

    def run_wall_upgrade_cycle(self, max_walls=None):
        """
        Nâng Tường LIÊN TỤC cho tới khi hết tài nguyên:
          1) Mở menu Búa, quét thẻ Tường (ImageProcessor.find_wall_cards_in_menu; chữ giá TRẮNG = CAN_UPGRADE,
             ĐỎ/XÁM = OUT_OF_RESOURCE).
          2) Bấm thẻ -> verify_wall_popup -> Nâng (Vàng/Dầu) -> Xác nhận.
          3) QUÉT LẠI NGAY tại vị trí vừa nâng: thẻ còn nâng được (CAN_UPGRADE) -> nâng tiếp tại chỗ.
          4) Không còn thẻ tại chỗ (menu đóng / danh sách dịch) -> MỞ LẠI menu Búa -> vuốt danh sách
             (quãng đường gấp WALL_MENU_SWIPE_MULT = 2 lần, 200-300ms/lần) -> tìm thẻ Tường kế tiếp.
          5) DỪNG HẲN chỉ khi thẻ Tường hiện OUT_OF_RESOURCE (xem _wall_stop_mode: ANY = một thẻ đỏ/xám là dừng;
             ALL = hết thẻ nâng được mới dừng), hoặc vuốt hết số lần mà không thấy thẻ Tường nào (NOT_FOUND),
             hoặc bấm nhầm công trình khác (WRONG_CARD). Lý do dừng lưu ở self._wall_stop_reason.
        Khối finally luôn đóng sạch menu. Zoom out KHÔNG làm ở đây mà ngay trước khi đi tấn công.

        :param max_walls: None / 0 = liên tục (chỉ bị chặn bởi WALL_CONTINUOUS_SAFETY_CAP); số dương = giới hạn cứng.
        :return: số khúc Tường đã nâng (0 nếu không nâng được) - vẫn dùng được như True/False.
        """
        menu_opened = False
        walls_done = 0
        try:
            safety = int(load_json_config().get("WALL_CONTINUOUS_SAFETY_CAP", WALL_CONTINUOUS_SAFETY_CAP))
        except Exception:
            safety = WALL_CONTINUOUS_SAFETY_CAP
        cap = int(max_walls) if max_walls and int(max_walls) > 0 else max(1, safety)
        wall_threshold, max_swipes, swipe_duration = self._wall_menu_settings()
        stop_mode = self._wall_stop_mode()
        self._wall_stop_reason = "NONE"
        try:
            self.log("🔨 Mở menu 'Xây dựng' để nâng Tường (liên tục tới khi hết tài nguyên)...")
            self.set_status("🧱 Đang nâng Tường thành...", "wall")
            if not self._open_build_menu():
                self._wall_stop_reason = "MENU_NOT_OPENED"
                return 0
            menu_opened = True

            swipes_used = 0
            last_swipe_fp = None      # fingerprint panel menu trước lần vuốt gần nhất (phát hiện menu đã cuộn tới cuối)
            last_row_y = None         # Y của thẻ vừa nâng (None = chưa nâng / vị trí đã mất)
            menu_x0 = ImageProcessor._wall_roi_ratio("WALL_MENU_X_RANGE", (0.72, 1.0))[0]
            while walls_done < cap:
                if self._stop_requested():
                    self._wall_stop_reason = "STOPPED"
                    break
                screen_menu = self._wait_menu_settled()   # chỉ quét khi menu đã đứng yên
                if screen_menu is None:
                    self.debug("⚠️ Không chụp được màn hình menu -> đóng menu")
                    self._wall_stop_reason = "NO_SCREEN"
                    break

                # Quét 2 vùng: TRÁI định vị dòng Tường (Y) -> PHẢI nhận Vàng/Dầu + chữ giá trắng / đỏ / xám
                cards = ImageProcessor.find_wall_cards_in_menu(screen_menu, threshold=wall_threshold)
                if cards:
                    self.debug("🧱 Quét được " + " | ".join(
                        f"y={c['y']} {c['resource_type']} {self._wall_card_state(c)}" for c in cards))
                    ready = [c for c in cards if self._wall_card_state(c) == WALL_CAN_UPGRADE]
                    out_of = [c for c in cards if self._wall_card_state(c) == WALL_OUT_OF_RESOURCE]
                    if out_of and (stop_mode == "ANY" or not ready):
                        self._wall_stop_reason = WALL_OUT_OF_RESOURCE
                        self.log(f"⛔ Thẻ Tường hiển thị {WALL_OUT_OF_RESOURCE} (giá đỏ/xám) -> dừng nâng Tường "
                                 f"(đã nâng {walls_done} khúc)")
                        break
                    # Ưu tiên nâng TIẾP TẠI CHỖ (thẻ cùng vị trí vừa nâng), không có mới chọn theo ưu tiên tài nguyên
                    card = None
                    if last_row_y is not None:
                        near = [c for c in ready if abs(c["y"] - last_row_y) <= WALL_SAME_SPOT_TOL]
                        if near:
                            card = min(near, key=lambda c: abs(c["y"] - last_row_y))
                    if card is None:
                        card = self._pick_wall_card(ready)
                    resource = card["resource_type"].lower()          # "gold" | "elixir"
                    label = "Vàng" if resource == "gold" else "Dầu"
                    name, row_pos = f"Tường - {label}", card["click_pos"]
                    self.debug(f"🎯 Thẻ Tường y={card['y']} dùng {label} (CAN_UPGRADE) -> bấm {row_pos} | {card.get('debug')}")

                    if not self._click_upgrade_wall_row(name, row_pos, resource):
                        # Bấm nhầm công trình khác: UI nhầm đã được đóng -> dừng lượt này, không đếm là đã nâng
                        self._wall_stop_reason = "WRONG_CARD"
                        self.log("⛔ Dừng lượt nâng Tường này do bấm nhầm thẻ (sẽ thử lại ở lượt sau)")
                        break
                    walls_done += 1
                    last_row_y = card["y"]
                    swipes_used = 0
                    last_swipe_fp = None
                    self.set_status(f"🧱 Đã nâng {walls_done} khúc Tường", "wall")
                    self.debug(f"🔍 Đã nâng {walls_done} khúc -> quét lại NGAY tại vị trí y={last_row_y} xem còn nâng tiếp được không")
                    if self._reopen_build_menu_if_closed():
                        last_row_y = None      # game đóng menu -> mở lại thì danh sách về đầu, vị trí cũ không còn giá trị
                    continue

                # --- Không thấy thẻ Tường nào trên màn hình menu ---
                if last_row_y is not None:
                    # Vừa nâng xong mà tại chỗ không còn thẻ -> MỞ LẠI menu Búa rồi vuốt tìm thẻ Tường kế tiếp
                    self.debug("↩️ Tại vị trí vừa nâng không còn thẻ Tường nâng được -> mở lại menu Búa, vuốt tìm thẻ kế tiếp")
                    last_row_y = None
                    if not self._reopen_wall_menu():
                        self._wall_stop_reason = "MENU_NOT_OPENED"
                        break
                    swipes_used = 0
                    last_swipe_fp = None
                    continue

                panel = screen_menu[:, int(screen_menu.shape[1] * menu_x0):]
                panel_fp = compute_frame_fingerprint(panel)
                if last_swipe_fp is not None and panel_fp == last_swipe_fp:
                    self._wall_stop_reason = "NOT_FOUND"
                    self.debug("⚠️ Vuốt xong menu không đổi (đã cuộn tới cuối danh sách) -> không còn thẻ Tường nào")
                    break
                last_swipe_fp = panel_fp
                if swipes_used >= max_swipes:
                    self._wall_stop_reason = "NOT_FOUND"
                    self.debug(f"⚠️ Đã vuốt {max_swipes} lần vẫn không thấy thẻ Tường -> đóng menu, "
                               f"đi đánh {self._wall_retry_limit()} trận rồi thử lại")
                    break
                swipes_used += 1
                self.debug(f"↕️ Chưa thấy thẻ Tường -> vuốt menu lần {swipes_used}/{max_swipes} (quãng đường gấp đôi)")
                self._swipe_wall_menu(swipe_duration)

            else:
                self._wall_stop_reason = "SAFETY_CAP"
                self.log(f"⚠️ Đã nâng {walls_done} khúc Tường (chạm chốt chặn an toàn {cap}) -> dừng lượt này")

            return walls_done

        except Exception as e:
            self.log(f"[❌ LỖI] run_wall_upgrade_cycle: {e}")
            self._wall_stop_reason = "ERROR"
            return walls_done
        finally:
            # Luôn đóng SẠCH menu + popup (nếu đã mở); chỉ Zoom Out khi màn hình đã là Làng chính sạch.
            if menu_opened:
                try:
                    time.sleep(0.4)
                    self._close_menu()          # đóng sạch mọi popup/menu
                    time.sleep(0.5)
                except Exception as e:
                    self.debug(f"[⚠️ CẢNH BÁO] Không đóng được menu sau nâng Tường: {e}")

                try:
                    state = "UNKNOWN"
                    for attempt in range(3):
                        screen_check = self._capture(fresh=True)
                        state = self.detect_initial_state(screen_check, quiet=True)
                        if state == "HOME_VILLAGE":
                            break
                        self.debug(f"⚠️ Màn hình chưa sạch hoàn toàn (trạng thái: {state}) -> "
                                 f"bấm thoát thêm lần nữa trước khi Zoom ({attempt + 1}/3)")
                        self._close_menu(times=2)
                        time.sleep(0.4)
                    if state == "HOME_VILLAGE":
                        self.debug("🏠 Đã về Làng chính sạch sau nâng Tường (zoom out sẽ làm ngay trước khi đi tấn công)")
                    else:
                        self.debug("⚠️ Vẫn chưa về Làng chính sạch sau nâng Tường -> trả quyền cho vòng lặp chính")
                except Exception as e:
                    self.debug(f"[⚠️ CẢNH BÁO] Zoom out làng nhà thất bại: {e}")

    def upgrade_wall_from_menu(self, max_walls=1):
        """Tên cũ, giữ lại cho tương thích -> run_wall_upgrade_cycle()."""
        return self.run_wall_upgrade_cycle(max_walls)

    # ------------------------------------------------------------------
    # 4b. ZOOM OUT LÀNG NHÀ (2 luồng swipe song song / Ctrl + cuộn chuột / sendevent)
    # ------------------------------------------------------------------
    # `adb shell input swipe` chỉ có 1 ngón nên không pinch được; ở đây gửi trực tiếp
    # chuỗi sự kiện cảm ứng đa điểm (multi-touch protocol B) tới thiết bị cảm ứng của LDPlayer.
    def _adb_serial(self):
        """Serial thiết bị: ưu tiên thuộc tính của ADBController, không có thì suy từ cổng ADB."""
        for attr in ("device_address", "serial", "device_serial", "device_id", "device", "address"):
            value = getattr(self.adb, attr, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return f"127.0.0.1:{ADB_PORT + EMULATOR_INDEX * 2}"

    def _adb_shell(self, command, timeout=20, with_err=False):
        """Chạy 1 lệnh `adb shell` bằng adb.exe của LDPlayer; trả về stdout (chuỗi). with_err=True: nối thêm stderr."""
        exe = getattr(self.adb, "adb_path", None) or ADB_PATH
        if not os.path.exists(exe):
            exe = "adb"
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(
            [exe, "-s", self._adb_serial(), "shell", command],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, **kwargs,
        )
        if with_err:
            return (result.stdout or "") + (result.stderr or "")
        return result.stdout or ""

    def _get_touch_info(self):
        """
        Tìm thiết bị cảm ứng đa điểm (có ABS_MT_POSITION_X/Y 0035/0036 và ABS_MT_SLOT 002f)
        qua `getevent -p`. Trả về dict {path, max_x, max_y, btn_touch} hoặc None. Có cache.
        """
        if self._touch_info:
            return self._touch_info
        if self._touch_checked:      # đã dò mà máy không có thiết bị đa điểm -> không dò lại
            return None
        self._touch_checked = True
        out = self._adb_shell("getevent -p")
        for block in re.split(r"(?m)^add device \d+:\s*", out)[1:]:
            path = block.splitlines()[0].strip()
            mx = re.search(r"0035\s*:[^\n]*?max\s+(\d+)", block)
            my = re.search(r"0036\s*:[^\n]*?max\s+(\d+)", block)
            if mx and my and re.search(r"002f\s*:", block):
                def axis_max(code):
                    m = re.search(code + r"\s*:[^\n]*?max\s+(\d+)", block)
                    return int(m.group(1)) if m else None
                self._touch_info = {
                    "path": path,
                    "max_x": int(mx.group(1)),
                    "max_y": int(my.group(1)),
                    "btn_touch": "014a" in block,
                    "slots": axis_max("002f"),        # ABS_MT_SLOT max (số điểm chạm - 1)
                    "pressure": axis_max("003a"),     # ABS_MT_PRESSURE max (None = máy không có)
                    "major": axis_max("0030"),        # ABS_MT_TOUCH_MAJOR max
                }
                ti = self._touch_info
                self.debug(f"🖐️ Thiết bị cảm ứng: {path} (X≤{mx.group(1)}, Y≤{my.group(1)}, "
                         f"slot≤{ti['slots']}, pressure≤{ti['pressure']}, major≤{ti['major']})")
                return self._touch_info
        return None

    def _build_pinch_script(self, info, a, b, duration_ms, steps=ZOOM_OUT_STEPS):
        """
        Chuỗi lệnh sendevent (multi-touch giao thức B): đặt 2 ngón xuống, GIỮ ngắn cho game nhận đủ 2 ngón,
        rồi di chuyển MƯỢT (>= 20 bước, easing) từ A->A', B->B' (chụm lại = thu nhỏ), cuối cùng nhả cả 2 ngón.
        Tọa độ 960x540 được đổi sang thang đo của thiết bị cảm ứng.
        Mỗi `sendevent` là 1 tiến trình riêng trên máy ảo nên chỉ gửi các trục THỰC SỰ đổi để chạy nhanh/đều.
        """
        ref_w, ref_h = BOT_REF_SIZE
        dev = info["path"]
        steps = max(20, int(steps))

        def ev(t, c, v):
            return f"sendevent {dev} {t} {c} {v}"

        def scale(x, y):
            return (int(round(x * info["max_x"] / ref_w)),
                    int(round(y * info["max_y"] / ref_h)))

        def lerp(p, t):
            return (p[0] + (p[2] - p[0]) * t, p[1] + (p[3] - p[1]) * t)

        def ease(t):          # smoothstep: bắt đầu/kết thúc chậm, giữa nhanh -> không bị giật
            return t * t * (3 - 2 * t)

        cmds = []
        pause = max(0.02, duration_ms / 1000.0 / steps / 2)
        last = {0: (None, None), 1: (None, None)}

        def move(slot, pos, down_id=None):
            cmds.append(ev(3, 47, slot))            # ABS_MT_SLOT
            if down_id is not None:
                cmds.append(ev(3, 57, down_id))     # ABS_MT_TRACKING_ID
                # Máy có khai báo lực nhấn / kích thước điểm chạm mà không gửi -> =0 -> Android coi là chưa chạm
                # (đúng triệu chứng: chỉ 1 ngón ăn, game chỉ bị kéo ngang thay vì zoom)
                if info.get("pressure"):
                    cmds.append(ev(3, 58, max(1, int(info["pressure"] * 0.4))))   # ABS_MT_PRESSURE
                if info.get("major"):
                    cmds.append(ev(3, 48, max(1, min(int(info["major"] * 0.3), 20))))   # ABS_MT_TOUCH_MAJOR
            lx, ly = last[slot]
            if pos[0] != lx:
                cmds.append(ev(3, 53, pos[0]))      # ABS_MT_POSITION_X
            if pos[1] != ly:
                cmds.append(ev(3, 54, pos[1]))      # ABS_MT_POSITION_Y
            last[slot] = pos

        # chạm xuống 2 ngón
        pa, pb = scale(*lerp(a, 0)), scale(*lerp(b, 0))
        move(0, pa, 100)
        move(1, pb, 101)
        if info["btn_touch"]:
            cmds.append(ev(1, 330, 1))              # BTN_TOUCH down
        cmds += [ev(0, 0, 0), "sleep 0.15"]         # giữ 2 ngón một chút

        # chụm lại mượt
        for i in range(1, steps + 1):
            t = ease(i / steps)
            move(0, scale(*lerp(a, t)))
            move(1, scale(*lerp(b, t)))
            cmds += [ev(0, 0, 0), f"sleep {pause:.3f}"]

        # nhả 2 ngón: tracking id = -1 (dùng -1 chứ KHÔNG dùng 4294967295 vì sendevent đọc kiểu số nguyên có dấu,
        # giá trị quá lớn làm ngón không nhả -> game chỉ bị kéo/giật chứ không zoom)
        cmds += [ev(3, 47, 0), ev(3, 57, -1), ev(3, 47, 1), ev(3, 57, -1)]
        if info["btn_touch"]:
            cmds.append(ev(1, 330, 0))
        cmds.append(ev(0, 0, 0))
        return "; ".join(cmds)

    def _pinch_points(self):
        """
        Hai đường vuốt của pinch. Dùng ZOOM_OUT_SWIPE_A/B (config.py) nếu thật sự là CHỤM VÀO (khoảng cách 2 ngón
        giảm rõ rệt); ngược lại (2 ngón cùng hướng / tách ra -> chỉ kéo camera hoặc zoom vào) dùng
        ZOOM_PARALLEL_A/B (mép -> tâm) để chắc chắn là thu nhỏ.
        """
        def dist(p, q, t):
            ax, ay = p[0] + (p[2] - p[0]) * t, p[1] + (p[3] - p[1]) * t
            bx, by = q[0] + (q[2] - q[0]) * t, q[1] + (q[3] - q[1]) * t
            return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5
        try:
            a, b = tuple(ZOOM_OUT_SWIPE_A), tuple(ZOOM_OUT_SWIPE_B)
            d0, d1 = dist(a, b, 0.0), dist(a, b, 1.0)
            if len(a) >= 4 and len(b) >= 4 and d0 > 50 and d1 < d0 * 0.7:
                return a, b
            self.debug(f"⚠️ ZOOM_OUT_SWIPE_A/B trong config.py KHÔNG phải cử chỉ chụm vào "
                     f"(khoảng cách 2 ngón {d0:.0f}px -> {d1:.0f}px) -> dùng ZOOM_PARALLEL_A/B (mép -> tâm)")
        except Exception as e:
            self.debug(f"⚠️ Không đọc được ZOOM_OUT_SWIPE_A/B ({e}) -> dùng ZOOM_PARALLEL_A/B")
        return tuple(ZOOM_PARALLEL_A), tuple(ZOOM_PARALLEL_B)

    @staticmethod
    def _frames_differ(before, after, threshold=ZOOM_VERIFY_DIFF):
        """True nếu 2 ảnh khác nhau đủ nhiều (độ lệch xám trung bình, đã thu nhỏ) -> camera đã đổi."""
        try:
            a = cv2.cvtColor(cv2.resize(before, (160, 90)), cv2.COLOR_BGR2GRAY)
            b = cv2.cvtColor(cv2.resize(after, (160, 90)), cv2.COLOR_BGR2GRAY)
            return float(np.mean(cv2.absdiff(a, b))) >= threshold
        except Exception:
            return False

    def _zoom_via_parallel_swipe(self):
        """Phương án 1: 2 luồng adb swipe song song, chụm từ 2 mép vào tâm."""
        fn = getattr(self.adb, "pinch_parallel", None)
        if fn is None:
            return False
        sent = False
        for _ in range(max(1, int(ZOOM_OUT_REPEAT))):
            if self._stop_requested():
                break
            sent = bool(fn(ZOOM_PARALLEL_A, ZOOM_PARALLEL_B, ZOOM_PARALLEL_DURATION_MS)) or sent
            time.sleep(0.4)
        return sent

    @staticmethod
    def _parallel_swipe_enabled():
        try:
            return bool(load_json_config().get("ZOOM_ALLOW_PARALLEL_SWIPE", ZOOM_ALLOW_PARALLEL_SWIPE))
        except Exception:
            return bool(ZOOM_ALLOW_PARALLEL_SWIPE)

    @staticmethod
    def _ctrl_wheel_enabled():
        """Ctrl + cuộn chuột có được bật không (config.json ưu tiên hơn hằng số). Mặc định False."""
        try:
            return bool(load_json_config().get("ZOOM_ENABLE_CTRL_WHEEL", ZOOM_ENABLE_CTRL_WHEEL))
        except Exception:
            return bool(ZOOM_ENABLE_CTRL_WHEEL)

    def _zoom_via_ctrl_wheel(self):
        """Phương án 2: giữ Ctrl + cuộn chuột xuống trên cửa sổ LDPlayer (Windows)."""
        if not self._ctrl_wheel_enabled():
            return False
        fn = getattr(self.adb, "ctrl_scroll_zoom_out", None)
        if fn is None:
            return False
        ok = bool(fn(ZOOM_CTRL_NOTCHES, ZOOM_WINDOW_KEYWORDS, ZOOM_PROCESS_NAMES, ZOOM_CTRL_DIRECTION))
        info = getattr(self.adb, "last_zoom_window", "")
        if info:
            self.debug(f"🖱️ Ctrl + cuộn chuột -> cửa sổ: {info}")
        return ok

    def _zoom_via_sendevent(self, strong=False):
        """Phương án 3 (dự phòng): pinch thật bằng sendevent nếu máy có thiết bị cảm ứng đa điểm."""
        info = self._get_touch_info()
        if info is None:
            if not getattr(self, "_touch_warned", False):
                self._touch_warned = True
                self.debug("⚠️ Không tìm thấy thiết bị cảm ứng đa điểm qua `getevent -p` -> không zoom được bằng sendevent.")
            return False
        if info.get("slots") is not None and info["slots"] < 1:
            self.debug(f"⚠️ Thiết bị cảm ứng chỉ có {info['slots'] + 1} điểm chạm (ABS_MT_SLOT max={info['slots']}) "
                     f"-> sendevent không thể chụm 2 ngón, bỏ qua")
            return False
        pa, pb = self._pinch_points()
        # strong: chụm CHẬM hơn (1.6x thời gian) + nhiều lần hơn -> dùng khi các cách khác thất bại
        script = self._build_pinch_script(info, pa, pb, int(ZOOM_OUT_DURATION_MS * (1.6 if strong else 1.0)))
        repeats = max(3, int(ZOOM_OUT_REPEAT) * 2) if strong else max(1, int(ZOOM_OUT_REPEAT))
        for n in range(repeats):
            if self._stop_requested():
                break
            out = self._adb_shell(script, timeout=40, with_err=True).strip()
            if out:   # sendevent chỉ in ra khi có lỗi (Permission denied, Invalid argument...)
                self.debug(f"⚠️ sendevent báo lỗi (lần {n + 1}): {out[:200]}")
            time.sleep(0.4)
        return True

    def _zoom_via_sendevent_strong(self):
        """Phương án 4: pinch sendevent bản MẠNH (chậm hơn, lặp nhiều hơn) - chạy sau khi 3 cách đầu đều thất bại."""
        return self._zoom_via_sendevent(strong=True)

    @staticmethod
    def _zoom_measure(before, after):
        """
        ĐO zoom thật: ghép điểm đặc trưng (ORB) giữa 2 ảnh rồi ước lượng phép đồng dạng.
        Trả về (scale, dx, dy, số điểm khớp) hoặc None nếu không đo được.
        scale < 1 = nhìn xa hơn (thu nhỏ); scale ≈ 1 mà dx/dy lớn = chỉ kéo camera (pan), KHÔNG phải zoom.
        """
        try:
            a = cv2.cvtColor(before, cv2.COLOR_BGR2GRAY)
            b = cv2.cvtColor(after, cv2.COLOR_BGR2GRAY)
            orb = cv2.ORB_create(1500)
            ka, da = orb.detectAndCompute(a, None)
            kb, db = orb.detectAndCompute(b, None)
            if da is None or db is None or len(ka) < 20 or len(kb) < 20:
                return None
            matches = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(da, db)
            if len(matches) < 15:
                return None
            src = np.float32([ka[m.queryIdx].pt for m in matches])
            dst = np.float32([kb[m.trainIdx].pt for m in matches])
            M, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=3.0)
            if M is None or inl is None or int(inl.sum()) < 12:
                return None
            return (float(np.hypot(M[0, 0], M[1, 0])), float(M[0, 2]), float(M[1, 2]), int(inl.sum()))
        except Exception:
            return None

    def _zoom_via_wheel_bg(self):
        """Phương án 4: gửi Ctrl + cuộn chuột THẲNG vào cửa sổ LDPlayer bằng thông điệp Windows (không chiếm chuột thật)."""
        fn = getattr(self.adb, "ctrl_wheel_background", None)
        if fn is None:
            return False
        ok = bool(fn(ZOOM_CTRL_NOTCHES, ZOOM_PROCESS_NAMES, ZOOM_CTRL_DIRECTION))
        info = getattr(self.adb, "last_zoom_window", "")
        if info:
            self.debug(f"🖱️ Ctrl + cuộn (gửi ngầm) -> {info}")
        return ok

    @staticmethod
    def _zoom_before_attack_enabled():
        try:
            return bool(load_json_config().get("ZOOM_BEFORE_ATTACK", ZOOM_BEFORE_ATTACK))
        except Exception:
            return bool(ZOOM_BEFORE_ATTACK)

    @staticmethod
    def _zoom_required():
        try:
            return bool(load_json_config().get("ZOOM_REQUIRED", ZOOM_REQUIRED))
        except Exception:
            return bool(ZOOM_REQUIRED)

    def _ensure_zoomed_out(self):
        """
        Cổng chặn trước khi đi tấn công. Trả True = ĐƯỢC PHÉP đánh (zoom thành công, hoặc ZOOM_REQUIRED tắt);
        False = KHÔNG được đánh lượt này (có lệnh dừng, hoặc vừa mở lại game -> vòng chính nhận diện lại rồi zoom lại từ đầu).
        Bắt buộc: zoom hỏng thì thử lại liên tục (nghỉ ZOOM_RETRY_PAUSE giây); thất bại liên tiếp ZOOM_RESTART_AFTER lần
        thì mở lại game (restart_game_app) vì lỗi zoom hay do giả lập/game kẹt. Không bao giờ đi đánh khi chưa zoom được.
        """
        if self._zoom_out_village():
            self._zoom_fail_streak = 0
            return True
        if not self._zoom_required():
            self.log("⚠️ Zoom out không thành công bằng mọi phương án -> vẫn đi tấn công (ZOOM_REQUIRED tắt)")
            return True
        self._zoom_fail_streak = getattr(self, "_zoom_fail_streak", 0) + 1
        self.log(f"⛔ Zoom out THẤT BẠI (lần {self._zoom_fail_streak}) -> KHÔNG đi tấn công, sẽ thử lại")
        self.set_status("⛔ Zoom lỗi - chưa tấn công, đang thử lại...", "warn")
        if self._zoom_fail_streak >= max(1, int(ZOOM_RESTART_AFTER)):
            self._zoom_fail_streak = 0
            self.log("🔄 Zoom lỗi nhiều lần liên tiếp -> mở lại game rồi thử zoom lại")
            self.restart_game_app()
            return False
        self._sleep_interruptible(ZOOM_RETRY_PAUSE)
        return False

    def _zoom_pre_tap(self):
        """Bấm vào TRUNG TÂM màn hình để thoát list nâng cấp (nếu đang mở), chờ list đóng rồi mới zoom."""
        try:
            cfg = load_json_config()
            if not bool(cfg.get("ZOOM_PRE_TAP", ZOOM_PRE_TAP)):
                return
            pos = cfg.get("ZOOM_PRE_TAP_POS") or ZOOM_PRE_TAP_POS
            scr = self._capture()
            h, w = (scr.shape[:2] if scr is not None else (540, 960))
            x, y = int(pos[0] * w / 960.0), int(pos[1] * h / 540.0)
            self.debug(f"👆 Bấm trung tâm ({x}, {y}) để thoát list nâng cấp trước khi zoom")
            self.adb.click(x, y)
            self._sleep_interruptible(ZOOM_PRE_TAP_WAIT)
        except Exception as e:
            self.debug(f"[⚠️ CẢNH BÁO] Bấm trung tâm trước khi zoom lỗi: {e}")

    def _zoom_out_village(self):
        """
        Thu nhỏ làng nhà về góc nhìn xa nhất, CHỈ bằng Ctrl + cuộn chuột, sau khi bấm trung tâm để thoát list nâng cấp:
          1) wheel_bg   - Ctrl + cuộn gửi ngầm vào cửa sổ LDPlayer (không chiếm chuột)
          2) ctrl_wheel - Ctrl + cuộn bằng chuột thật (chỉ chạy khi cách 1 thất bại; tắt bằng ZOOM_ENABLE_CTRL_WHEEL=false)
        Phương án từng chạy được (self._zoom_method) được thử TRƯỚC nhưng KHÔNG còn là phương án duy nhất:
        nếu nó thất bại lần này thì vẫn rơi sang các cách còn lại. Sau MỖI phương án ĐO zoom thật (ORB):
        scale <= 0.97 mới tính thành công; chỉ dịch camera thì thất bại. Hết vòng mà chưa được -> thử lại
        tối đa ZOOM_MAX_ROUNDS vòng. KHÔNG ném lỗi. :return: True nếu zoom thành công / đã ở mức xa nhất.
        """
        try:
            methods = {
                "wheel_bg": self._zoom_via_wheel_bg,
                "ctrl_wheel": self._zoom_via_ctrl_wheel,
            }
            order = ["wheel_bg"]
            if self._ctrl_wheel_enabled():
                order.append("ctrl_wheel")
            known = self._zoom_method if self._zoom_method in order else None
            if known:
                order = [known] + [m for m in order if m != known]

            self._zoom_pre_tap()   # thoát list nâng cấp rồi mới zoom
            self.debug(f"🔭 Zoom out làng nhà (thứ tự thử: {' -> '.join(order)})...")
            any_sent, unverified = False, False
            for rnd in range(1, max(1, int(ZOOM_MAX_ROUNDS)) + 1):
                static_methods = []
                for name in order:
                    if self._stop_requested():
                        return False
                    before = self._capture(fresh=True)
                    try:
                        sent = methods[name]()
                    except Exception as e:
                        self.debug(f"[⚠️ CẢNH BÁO] Zoom out bằng '{name}' lỗi: {e}")
                        sent = False
                    if not sent:
                        self.debug(f"ℹ️ Zoom out '{name}' không gửi được -> thử phương án tiếp theo")
                        continue
                    any_sent = True
                    time.sleep(0.8)
                    after = self._capture(fresh=True)
                    if before is None or after is None:
                        unverified = True
                        self.debug(f"ℹ️ Zoom '{name}': không chụp được ảnh để kiểm chứng")
                        continue
                    m = self._zoom_measure(before, after)
                    if m is None:
                        unverified = True
                        changed = self._frames_differ(before, after)
                        self.debug(f"ℹ️ Zoom '{name}': không đo được tỉ lệ zoom (ảnh {'có' if changed else 'không'} đổi)")
                        if not changed:
                            static_methods.append(name)
                        continue
                    scale, dx, dy, n = m
                    self.debug(f"📐 Zoom '{name}' (vòng {rnd}): tỉ lệ {scale:.3f} | dịch ({dx:+.0f}, {dy:+.0f})px | {n} điểm khớp")
                    if scale <= 0.97:
                        self._zoom_method = name
                        self.debug(f"✅ Zoom out thành công bằng '{name}' (thu nhỏ {100 * (1 - scale):.0f}%)")
                        return True
                    if abs(dx) > 12 or abs(dy) > 12:
                        self.debug(f"⚠️ '{name}' chỉ KÉO camera chứ không zoom -> thử phương án khác")
                        continue
                    # Ảnh gần như không đổi: hoặc làng đã ở mức xa nhất, hoặc phương án này không tác dụng
                    static_methods.append(name)
                    if name == known:
                        self.debug(f"ℹ️ '{name}' (từng zoom được) không làm ảnh đổi nữa -> làng đã ở mức xa nhất")
                        return True
                    self.debug(f"ℹ️ '{name}': ảnh gần như không đổi -> thử phương án khác để chắc chắn")
                    if len(static_methods) >= max(1, min(len(order), int(ZOOM_STATIC_CONCLUDE))):
                        self.debug(f"ℹ️ {len(static_methods)} phương án khác nhau đều không làm ảnh đổi -> làng đã ở mức xa nhất")
                        return True
                if rnd < int(ZOOM_MAX_ROUNDS):
                    self.debug(f"🔁 Vòng {rnd} chưa zoom được -> thử lại toàn bộ phương án (vòng {rnd + 1})")

            if any_sent and unverified:
                self.debug("⚠️ Không xác nhận được zoom out (không đo được tỉ lệ)")
                return True
            if any_sent:
                self.debug("⚠️ KHÔNG zoom được: các phương án chỉ kéo camera hoặc không tác dụng. "
                         "Gửi mình các dòng log 📐 / 🖐️ ở trên")
                return False
            self.debug("[⚠️ CẢNH BÁO] Mọi phương án zoom out đều không gửi được")
            return False
        except Exception as e:
            self.debug(f"[⚠️ CẢNH BÁO] _zoom_out_village: {e}")
            return False

    def _upgrade_main_building(self):
        """Nâng công trình chính: dòng đề xuất đầu tiên trong menu Xây dựng."""
        menu_opened = False
        try:
            self.debug("🏗️ Nâng công trình chính (dòng đề xuất đầu tiên)...")
            menu_opened = True
            self.adb.click(*BUILD_BUTTON_POS)
            time.sleep(1.5)

            self.adb.click(*CONFIRM_UPGRADE_POS)
            time.sleep(1)
            return True
        except Exception as e:
            self.log(f"[❌ LỖI] _upgrade_main_building: {e}")
            return False
        finally:
            if menu_opened:
                self._close_menu()

    # ------------------------------------------------------------------
    # 5. XỬ LÝ LÀNG NHÀ: THỢ XÂY + XẢ TƯỜNG
    # ------------------------------------------------------------------
    def _battles_until_wall_check(self):
        """
        Số trận còn lại tới lần kiểm tra nâng Tường kế tiếp (0 = đã tới lượt).
        Chưa kiểm tra lần nào kể từ khi vào game (mark = None) -> 0, tức nâng ngay.
        """
        mark = self._wall_check_battle_mark
        battles = self.stats["battles"]
        if mark is None:
            return 0
        if battles < mark:                 # bộ đếm trận bị reset -> tính lại từ hiện tại
            self._wall_check_battle_mark = mark = battles
        return max(0, self._wall_retry_limit() - (battles - mark))

    def _run_wall_schedule(self, free_builders):
        """
        Chạy lịch nâng Tường: nếu đã tới lượt (vừa vào game hoặc đủ WALL_RETRY_BATTLES_LIMIT
        trận) thì gọi upgrade_wall_from_menu() tối đa min(WALL_UPGRADE_MAX_PER_CYCLE, thợ rảnh) lần.
        Không nâng được (thiếu tài nguyên...) -> dừng, để bot quay lại đi cướp.

        :return: số lần đã nâng Tường thành công (0 nếu chưa tới lượt / tắt / không nâng được).
        """
        if not self._wall_settings()["enable"]:
            self.debug("ℹ️ ENABLE_WALL_UPGRADE đang tắt -> không nâng Tường")
            return 0

        until_check = self._battles_until_wall_check()
        if until_check > 0:
            self.debug(f"ℹ️ Chưa tới lượt nâng Tường (còn {until_check} trận nữa)")
            return 0

        # Mặc định NÂNG LIÊN TỤC tới khi hết tài nguyên (WALL_UPGRADE_CONTINUOUS = false -> quay lại giới hạn theo thợ rảnh)
        try:
            continuous = bool(load_json_config().get("WALL_UPGRADE_CONTINUOUS", True))
        except Exception:
            continuous = True
        wall_times = None if continuous else min(WALL_UPGRADE_MAX_PER_CYCLE, free_builders)
        retry_limit = self._wall_retry_limit()
        limit_text = "liên tục tới khi hết tài nguyên" if continuous else f"tối đa {wall_times} lần"
        self.set_status("🧱 Đang kiểm tra nâng Tường...", "wall")
        if self._wall_check_battle_mark is None:
            self.log(f"🧱 Vừa vào game -> nâng Tường ngay ({limit_text})")
        else:
            self.log(f"🧱 Đã đủ {retry_limit} trận -> thử nâng Tường lại ({limit_text})")
        walls_done = self.run_wall_upgrade_cycle(max_walls=wall_times)
        if continuous or walls_done < wall_times:
            # Lượt liên tục chỉ kết thúc khi hết tài nguyên / không thấy thẻ / lỗi; lượt có giới hạn mà chưa đủ số khúc
            # cũng vậy -> đặt mốc đếm ngược: đánh đủ retry_limit trận mới thử lại
            self.log(
                f"↩️ Đã nâng {walls_done} khúc Tường (dừng: {self._wall_stop_reason}) -> "
                f"đi đánh, thử lại sau {retry_limit} trận"
            )
            self._wall_check_battle_mark = self.stats["battles"]
        # Cộng dồn số khúc Tường đã nâng, rồi đẩy lên Dashboard (sau khi đã đặt lại mốc đếm ngược)
        if walls_done > 0:
            self.stats["walls"] += walls_done
        self.update_ui_stats()
        self.set_status("🏠 Đang ở Làng chính", "home")
        return walls_done

    def _wall_check_before_battle(self, screen):
        """
        Chốt chặn ở làng chính (đã thấy nút 'Chiến đấu'): nếu tới lượt nâng Tường thì nâng
        TRƯỚC khi đi tìm trận, kể cả khi vòng lặp chính không gọi process_home_village()
        hoặc OCR số thợ xây đọc lỗi.

        :return: True nếu đã chạy lượt nâng Tường (màn hình đã đổi -> caller nên chụp lại);
                 False nếu chưa tới lượt / tắt / hết thợ.
        """
        if not self._wall_settings()["enable"]:
            return False
        if self._battles_until_wall_check() > 0:
            return False

        # Bỏ OCR số thợ xây: nâng Tường ngay khi tới lượt
        self._run_wall_schedule(WALL_UPGRADE_MAX_PER_CYCLE)
        return True

    def process_home_village(self, screen, auto_stop_if_full=True):
        """
        Ở làng nhà: đọc thợ rảnh an toàn + tài nguyên nhà, xả vào Tường khi dư.

        Lịch nâng Tường (cần ENABLE_WALL_UPGRADE = True trong config.json và thợ rảnh > 0):
          - Vừa vào game: gọi upgrade_wall_from_menu() ngay, KHÔNG đọc OCR tài nguyên nhà.
            Đủ Vàng/Dầu thì nút nâng sáng -> nâng; không đủ thì hàm trả False.
          - Không nâng được -> quay lại đi cướp tài nguyên; đủ WALL_RETRY_BATTLES_LIMIT (5)
            trận đánh xong, lần ghé làng nhà kế tiếp sẽ thử nâng Tường lại.

        :param screen: ảnh chụp màn hình (OpenCV BGR).
        :param auto_stop_if_full: True -> hết thợ thì trả "STOP".
        :return: "STOP" | "NO_BUILDER" | "SUCCESS" | "CONTINUE"
        """
        try:
            # Chỉ coi là Làng chính khi thấy nút 'Chiến đấu'. Nếu đang ở menu tìm trận / làng địch / trong trận
            # thì KHÔNG đổi Banner và KHÔNG chạy lịch nâng Tường (các bước đó do check_and_search_match lo).
            if not _find_any(screen, TPL_BATTLE, threshold=0.75):
                return "CONTINUE"
            self.set_status("🏠 Đang ở Làng chính", "home")
            # Bỏ hẳn OCR số thợ xây (chậm, hay đọc lỗi): vào làng là nâng Tường ngay theo lịch rồi đi farm
            if not self._wall_settings()["enable"]:
                return "CONTINUE"
            if self._battles_until_wall_check() > 0:
                return "CONTINUE"      # chưa tới lượt -> để vòng chính đi farm
            walls_done = self._run_wall_schedule(WALL_UPGRADE_MAX_PER_CYCLE)
            self.last_upgrade_time = time.time()
            return "SUCCESS" if walls_done else "CONTINUE"

        except Exception as e:
            self.log(f"[❌ LỖI] process_home_village: {e}")
            return "CONTINUE"

    def check_builder_and_upgrade(self, screen, auto_stop_if_full=True):
        """Tên cũ, giữ lại để main.py cũ vẫn chạy được. Chuyển sang process_home_village()."""
        return self.process_home_village(screen, auto_stop_if_full)

    # ------------------------------------------------------------------
    # 6. TÌM TRẬN & SOI TÀI NGUYÊN
    # ------------------------------------------------------------------
    def _poll_screen(self, names, timeout=None, interval=None):
        """
        Sau khi bấm 1 bước: chụp ảnh MỚI liên tục (mỗi STEP_POLL_INTERVAL) tới khi thấy 1 trong các nút `names`
        hoặc hết STEP_POLL_TIMEOUT. Trả về ảnh (thấy nút) hoặc None. Giúp sang bước kế ngay thay vì chờ vòng chính.
        """
        timeout = STEP_POLL_TIMEOUT if timeout is None else timeout
        interval = STEP_POLL_INTERVAL if interval is None else interval
        end = time.time() + timeout
        while time.time() < end:
            if self._stop_requested():
                return None
            scr = self._capture(fresh=True)
            if scr is not None:
                for n in names:   # n: tên wildcard trong assets/ (gồm cả biến thể _2, _3...)
                    found = _find_next(scr) if n == TPL_NEXT else _find_any(scr, n, threshold=0.75)
                    if found:
                        return scr
            time.sleep(interval)
        return None

    def _idle_watch(self, screen):
        """
        Gọi khi check_and_search_match KHÔNG khớp nút nào. Kẹt liên tục quá IDLE_REBOOT_SECONDS:
        dò lần cuối popup Thử lại / Tải lại; không có -> tắt LDPlayer hiện tại rồi mở lại.
        """
        now = time.time()
        last = getattr(self, "_idle_last", 0.0)
        self._idle_last = now
        if getattr(self, "_idle_since", None) is None or now - last > 8.0:   # không còn liên tục -> đếm lại
            self._idle_since = now
            return False
        try:
            limit = float(load_json_config().get("IDLE_REBOOT_SECONDS", IDLE_REBOOT_SECONDS))
        except Exception:
            limit = IDLE_REBOOT_SECONDS
        if now - self._idle_since < limit:
            return False
        idle = int(now - self._idle_since)
        self._idle_since = None
        if self._check_conn_popups(screen):
            self.log(f"🔌 Kẹt {idle}s -> đã xử lý popup mất kết nối")
            return True
        self.log(f"🐢 Kẹt {idle}s ở màn hình không nhận ra, không có nút Tải lại -> TẮT & MỞ LẠI LDPlayer")
        self._reboot_emulator()
        return True

    def check_and_search_match(self, screen, min_gold, min_elixir, min_dark=0):
        """
        Luồng chuyển cảnh tìm trận đấu (4 bước), mỗi lần gọi xử lý đúng 1 màn hình:

          1/4 Làng chính           : thấy button_battle.png           -> bấm, chờ 2s
          2/4 Menu chọn chế độ     : thấy button_find_match_1700.png  -> bấm, chờ 2s
          3/4 Bảng chuẩn bị quân   : thấy button_attack.png ("Tấn công!") -> bấm, chờ 4s
          4/4 Màn hình soi nhà địch: thấy button_next.png -> OCR RESOURCE_ENEMY_ROI
                                      + đạt mốc  -> deploy_troops()
                                      + chưa đạt -> bấm Next

        Mỗi nút được kiểm tra bằng ImageProcessor.find_any_template_location (qua _find_any): ảnh gốc +
        mọi biến thể button_xxx_2.png, _3.png... Nếu màn hình hiện tại không khớp nút nào thì không làm gì
        (vòng lặp chính sẽ chụp và gọi lại).

        :param screen: ảnh chụp màn hình (OpenCV BGR).
        :param min_gold / min_elixir / min_dark: mốc tài nguyên tối thiểu.
        """
        try:
            self._ensure_stall_monitor()   # đánh dấu 'nhịp' + bật luồng giám sát kẹt
            # --- Trước mọi thao tác: có popup mất kết nối / kẹt không? ---
            if self._guard_connection(screen):
                return

            # --- Bước 1/4: Làng chính -> bấm "Chiến đấu" ---
            battle_btn = _find_any(screen, TPL_BATTLE, threshold=0.75)
            if battle_btn:
                # Tới lượt nâng Tường (vừa vào game / đủ 5 trận) -> nâng trước, vòng sau mới đi cướp
                if self._wall_check_before_battle(screen):
                    return
                # Zoom out NGAY TRƯỚC khi đi tấn công (đã qua bước nâng Tường, làng chính sạch)
                if self._zoom_before_attack_enabled():
                    self.set_status("🔭 Đang zoom out làng...", "search")
                    if not self._ensure_zoomed_out():
                        return   # chưa zoom được -> KHÔNG bấm Chiến đấu; vòng chính chạy lại sẽ zoom lại
                    scr_z = self._capture(fresh=True)
                    if scr_z is None:
                        return
                    battle_btn = _find_any(scr_z, TPL_BATTLE, threshold=0.75)
                    if not battle_btn:
                        return   # màn hình đổi bất thường sau zoom -> để vòng chính nhận diện lại
                self.set_status("🔍 Đang tìm đối thủ...", "search")
                self.log("🔍 Bắt đầu tìm đối thủ...")
                self._idle_since = None
                self.adb.click(*battle_btn)
                nxt = self._poll_screen((TPL_FIND_MATCH,))   # sang bước 2 ngay khi menu hiện
                if nxt is not None:
                    return self.check_and_search_match(nxt, min_gold, min_elixir, min_dark)
                return

            # --- Bước 2/4: Menu chế độ -> bấm "Tìm trận đấu 1700" ---
            find_btn = _find_any(screen, TPL_FIND_MATCH, threshold=0.75)
            if find_btn:
                self.debug("🔍 Ở menu chế độ -> bấm 'Tìm trận đấu 1700'...")
                self.set_status("🔍 Đang tìm đối thủ...", "search")
                self._idle_since = None
                self.adb.click(*find_btn)
                nxt = self._poll_screen((TPL_ATTACK,))            # sang bước 3 ngay khi bảng hiện
                if nxt is not None:
                    return self.check_and_search_match(nxt, min_gold, min_elixir, min_dark)
                return

            # --- Bước 3/4: Bảng chuẩn bị quân -> bấm "Tấn công!" (nút xanh lá) ---
            attack_btn = _find_any(screen, TPL_ATTACK, threshold=0.75)
            if attack_btn:
                self.debug("🟢 Ở bảng chuẩn bị quân -> bấm 'Tấn công!' để vào làng đối thủ...")
                self.set_status("🟢 Đang vào làng đối thủ...", "search")
                self._idle_since = None
                self.adb.click(*attack_btn)
                nxt = self._poll_screen((TPL_NEXT,))              # sang bước 4 ngay khi vào làng địch
                if nxt is not None:
                    return self.check_and_search_match(nxt, min_gold, min_elixir, min_dark)
                return

            # --- Bước 4/4: Màn hình soi nhà đối thủ (có nút Next) ---
            next_btn = _find_next(screen)
            while next_btn:   # lặp: Next -> chờ nhà mới -> OCR ngay bằng chính khung vừa chụp (không quay về vòng chính)
                self._idle_since = None
                # --- Chế độ Cào dữ liệu: chụp ROI, lưu ảnh, bấm Next, KHÔNG thả quân ---
                collect_on, max_samples, enemy_roi = self._collect_settings()
                if collect_on:
                    if self.collect_finished or self._stop_requested():
                        return
                    self._collect_enemy_sample(screen, next_btn, max_samples, enemy_roi)
                    return

                # OCR chỉ dành cho nâng Tường -> không đọc tài nguyên nhà địch, tấn công ngay
                if self._ocr_only_for_wall():
                    self.debug("👀 OCR nhà địch đang TẮT (chỉ OCR để nâng Tường) -> tấn công ngay...")
                    if self.deploy_troops():
                        self.stats["battles"] += 1
                        self.update_ui_stats()
                    else:
                        self.log("⚠️ Trận đánh gặp sự cố -> không tính vào thống kê")
                    return

                self.debug("👀 Đang soi nhà đối thủ -> đọc tài nguyên (OCR)...")
                self.set_status("📊 Đang kiểm tra tài nguyên nhà địch...", "search")
                gold, elixir, dark = ImageProcessor.get_enemy_resources(screen)

                # Không đọc được cả Vàng lẫn Dầu thì bỏ qua, đọc lại ở vòng sau
                if gold is None and elixir is None:
                    self.debug("⚠️ OCR chưa đọc được Vàng/Dầu của địch, thử lại vòng sau")
                    return

                try:
                    settings = self._loot_settings()
                except Exception as e:
                    self.debug(f"[⚠️ CẢNH BÁO] Không đọc được mốc từ config.json ({e}) -> dùng mốc truyền vào")
                    settings = (min_gold, min_elixir, min_dark, "OR")
                s_gold, s_elixir, s_dark, s_mode = settings
                gold_text = f"{gold:,}" if gold is not None else "?"
                elixir_text = f"{elixir:,}" if elixir is not None else "?"
                dark_text = f"{dark:,}" if dark is not None else "?"
                loot_ok = self._check_loot_conditions(gold, elixir, dark, settings)
                self.debug(f"💰 Mốc {s_mode}: {s_gold:,} / {s_elixir:,} / {s_dark:,}")
                self.log(
                    f"💰 Địch: Vàng {gold_text} | Dầu {elixir_text} | Dầu đen {dark_text} -> "
                    + ("✅ đạt mốc, tấn công!" if loot_ok else "❌ chưa đạt, bỏ qua")
                )

                if loot_ok:
                    attacked = self.deploy_troops()

                    # Cộng dồn thống kê rồi cập nhật Dashboard (chỉ khi trận hoàn tất)
                    if attacked:
                        self.stats["battles"] += 1
                        self.update_ui_stats()
                    else:
                        self.log("⚠️ Trận đánh gặp sự cố -> không tính vào thống kê")
                else:
                    self.debug("➡️ Chưa đạt mốc -> bấm 'Next' sang nhà tiếp theo")
                    self.set_status("➡️ Chưa đạt mốc - đang tìm nhà khác...", "search")
                    # Dò thông minh: bấm Next xong chờ ĐÚNG đến khi nhà mới hiện (thay sleep cứng 1.5s)
                    prev_gray = None
                    try:
                        _, _, enemy_roi = self._collect_settings()
                        prev_gray = self._roi_gray(screen, enemy_roi)
                    except Exception:
                        enemy_roi = None
                    self.adb.click(*next_btn)
                    if prev_gray is not None:
                        new_screen, new_btn = self._wait_new_base(prev_gray, enemy_roi, timeout=NEXT_WAIT_TIMEOUT)
                        if new_screen is not None and new_btn:
                            screen, next_btn = new_screen, new_btn   # nhà mới sẵn sàng -> OCR NGAY, bỏ nhịp nghỉ + chụp lại của vòng chính
                            continue
                        # Không đủ tài nguyên (vừa bỏ qua nhà này) + KHÔNG thấy lại nút Next -> Đầu hàng để khỏi treo
                        if not self._stop_requested() and self._surrender_if_next_missing(
                                "địch chưa đạt mốc tài nguyên và không thấy lại nút Next"):
                            return
                    else:
                        time.sleep(NEXT_FALLBACK_DELAY)
                        self._guard_connection()   # sau khi bấm Next: kiểm tra ngay popup mất kết nối
                return

            # Không khớp nút nào ở 4 bước trên: đang ở trong trận / màn hình có nút Đầu hàng mà không thấy Next
            # (vào trận không đủ tài nguyên để đánh) -> Đầu hàng sau SURRENDER_IDLE_SECONDS giây
            if self._surrender_idle_check(screen):
                return
            # Còn lại -> chỉ GHI LOG chẩn đoán (không đổi luồng), 5s/lần
            self._log_idle_screen(screen)
            self._idle_watch(screen)

        except Exception as e:
            self.log(f"[❌ LỖI] check_and_search_match: {e}")

    # ------------------------------------------------------------------
    # 4c. ĐẦU HÀNG (Surrender Fallback) - chống treo khi vào trận mà không đánh được
    # ------------------------------------------------------------------
    def _roi_cfg(self, key, default):
        """ROI [x1, y1, x2, y2] (hệ 960x540) từ config.json[key]; thiếu -> default."""
        try:
            roi = load_json_config().get(key)
            if roi and len(roi) >= 4:
                return tuple(roi[:4])
        except Exception:
            pass
        return default

    def _find_surrender_btn(self, screen):
        """Tâm nút Đầu hàng (ảnh mẫu surrender_btn*.png, chỉ quét SURRENDER_BTN_ROI) hoặc None."""
        if screen is None or not _template_names_exist(TPL_SURRENDER):
            return None
        try:
            return ImageProcessor.find_template_any(
                screen, _asset_specs(TPL_SURRENDER), SURRENDER_THRESHOLD,
                roi=self._roi_cfg("SURRENDER_BTN_ROI", SURRENDER_ROI_DEFAULT), fast=True)
        except Exception as e:
            self.debug(f"[⚠️ CẢNH BÁO] _find_surrender_btn: {e}")
            return None

    def _surrender_target(self, screen):
        """
        Vị trí bấm nút Đầu hàng: ảnh mẫu surrender_btn*.png; chưa có ảnh mẫu thì dùng nút ĐỎ góc dưới trái
        (BATTLE_END_BTN_ROI) nếu nhận ra bằng màu. None nếu không thấy nút nào.
        """
        pos = self._find_surrender_btn(screen)
        if pos:
            return tuple(pos)
        if screen is not None and not _template_names_exist(TPL_SURRENDER) and self._battle_in_progress(screen):
            h, w = screen.shape[:2]
            x1, y1, x2, y2 = BATTLE_END_BTN_ROI
            self.log("ℹ️ Chưa có assets/surrender_btn.png -> bấm nút đỏ góc dưới trái (nên cắt ảnh bằng crop_tool, mục 17)")
            return (int((x1 + x2) / 2 * w / 960.0), int((y1 + y2) / 2 * h / 540.0))
        return None

    def _find_confirm_surrender(self, screen):
        """Tâm nút Xác nhận đầu hàng (confirm_surrender_btn*.png, chỉ quét CONFIRM_SURRENDER_ROI) hoặc None."""
        if screen is None or not _template_names_exist(TPL_CONFIRM_SURRENDER):
            return None
        try:
            return ImageProcessor.find_template_any(
                screen, _asset_specs(TPL_CONFIRM_SURRENDER), SURRENDER_THRESHOLD,
                roi=self._roi_cfg("CONFIRM_SURRENDER_ROI", CONFIRM_SURRENDER_ROI_DEFAULT), fast=True)
        except Exception as e:
            self.debug(f"[⚠️ CẢNH BÁO] _find_confirm_surrender: {e}")
            return None

    def _see_hammer(self, screen):
        """True nếu thấy nút Búa (nhà chính / Làng chính) - ảnh mẫu button_build*.png."""
        try:
            return bool(screen is not None and _find_any(screen, TPL_HAMMER, threshold=0.75))
        except Exception:
            return False

    def execute_surrender_flow(self):
        """
        Luồng ĐẦU HÀNG khi đã vào màn hình trận đấu mà KHÔNG đủ tài nguyên để đánh VÀ KHÔNG thấy nút Next:
          Step 1: bấm nút Đầu hàng (surrender_btn).
          Step 2: kiểm tra NGAY xem có nút Búa (đã ở Làng chính) chưa - chụp lại liên tục tối đa SURRENDER_STEP_WAIT giây:
                    - CÓ nút Búa     -> bỏ qua Xác nhận + Về làng, trả về True ngay (vòng chính đi farm tiếp);
                    - KHÔNG thấy Búa mà thấy nút Xác nhận đầu hàng -> bấm Xác nhận -> _leave_battle() (bấm Về làng, chờ thấy nút Búa).
        Không bao giờ ném lỗi.
        :return: True nếu đã rời trận / về làng; False nếu không thấy nút Đầu hàng hoặc hết giờ chờ (vòng chính sẽ xử lý tiếp).
        """
        try:
            self.set_status("🏳️ Không đủ tài nguyên - đang đầu hàng...", "warn")
            screen = self._capture(fresh=True)
            pos = self._surrender_target(screen)
            if pos is None:
                self.log("⚠️ Cần đầu hàng nhưng không thấy nút Đầu hàng -> bỏ qua")
                return False
            self.log(f"🏳️ Bấm nút Đầu hàng tại {tuple(pos)}")
            self._tap(pos, "Đầu hàng")

            deadline = time.time() + SURRENDER_STEP_WAIT
            confirm = None
            while time.time() < deadline:
                if self._stop_requested():
                    return False
                time.sleep(SURRENDER_POLL)
                scr = self._capture(fresh=True)
                if scr is None:
                    continue
                if self._see_hammer(scr):              # Step 2: CÓ nút Búa -> khỏi xác nhận, khỏi bấm Về làng
                    self.log("🏠 Thấy nút Búa ngay sau khi Đầu hàng -> bỏ qua Xác nhận + Về làng, đi farm tiếp")
                    self.set_status("🏠 Đang ở Làng chính", "home")
                    self._watchdog_reset()
                    return True
                confirm = self._find_confirm_surrender(scr)
                if confirm:
                    break
                if self._guard_connection(scr):
                    self.log("🔌 Mất kết nối khi đầu hàng -> đã phục hồi, thoát luồng đầu hàng")
                    return False
            if not confirm:
                self.log("⚠️ Sau khi bấm Đầu hàng không thấy nút Búa lẫn nút Xác nhận -> trả quyền cho vòng chính")
                return False

            self.log(f"✅ Bấm Xác nhận đầu hàng tại {tuple(confirm)}")
            self._tap(confirm, "Xác nhận đầu hàng")
            self._leave_battle()                      # bấm Về làng (return_home_btn) tới khi thấy nút Búa / Chiến đấu
            self._watchdog_reset()
            return True
        except Exception as e:
            self.log(f"[❌ LỖI] execute_surrender_flow: {e}")
            return False
        finally:
            self._surr_idle_since = None

    def _surrender_if_next_missing(self, reason):
        """
        Gọi khi vừa bấm Next mà nhà mới không hiện nút Next: chụp lại SURRENDER_NO_NEXT_CONFIRM lần liên tiếp;
        vẫn không thấy Next mà có nút Đầu hàng -> execute_surrender_flow(). True nếu đã chạy luồng đầu hàng.
        """
        for i in range(SURRENDER_NO_NEXT_CONFIRM):
            scr = self._capture(fresh=True)
            if scr is None or _find_next(scr):
                return False                          # còn Next -> bình thường, vòng chính xử lý tiếp
            if i + 1 < SURRENDER_NO_NEXT_CONFIRM:
                time.sleep(0.4)
        if self._surrender_target(scr) is None:
            return False
        self.log(f"🏳️ {reason} -> đầu hàng")
        self.execute_surrender_flow()
        return True

    def _surrender_idle_check(self, screen):
        """
        Ở nhánh 'không khớp nút nào': đang đứng ở màn hình có nút Đầu hàng (không có Next, không đang thả quân)
        liên tục >= SURRENDER_IDLE_SECONDS -> execute_surrender_flow(). True nếu đã chạy luồng đầu hàng.
        """
        if getattr(self, "_battle_started_at", None) is not None or self._surrender_target(screen) is None:
            self._surr_idle_since = None
            return False
        now = time.time()
        if self._surr_idle_since is None:
            self._surr_idle_since = now
            return False
        if now - self._surr_idle_since < SURRENDER_IDLE_SECONDS:
            return False
        self.log(f"🏳️ Kẹt {now - self._surr_idle_since:.0f}s ở màn hình trận đấu, không thấy nút Next -> đầu hàng")
        self.execute_surrender_flow()
        return True

    def _log_idle_screen(self, screen, every=5.0):
        """
        Khi check_and_search_match không khớp nút nào: ghi log điểm khớp (5s/lần) + lưu khung hình.
        Nếu tình trạng này kéo dài >= IDLE_CLICK_AFTER lần ghi liên tiếp (~15s) và IDLE_CLICK_BEST_MATCH
        bật (config.json, mặc định bật) -> bấm vào nút có điểm khớp CAO NHẤT (miễn điểm >= IDLE_CLICK_MIN_SCORE).
        """
        now = time.time()
        last = getattr(self, "_idle_log_at", 0.0)
        if now - last < every:
            return
        if now - last > every * 3:           # không còn liên tục -> đếm lại từ đầu
            self._idle_count = 0
        self._idle_log_at = now
        self._idle_count = getattr(self, "_idle_count", 0) + 1
        try:
            parts, best = [], None           # best = (điểm, tên, (x, y))
            for fname in ("button_battle.png", "button_find_match_1700.png",
                          "button_attack.png", "button_next.png"):
                path = asset(fname)
                if not os.path.exists(path):
                    parts.append(f"{fname}: THIẾU FILE")
                    continue
                tpl = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
                if tpl is None or tpl.shape[0] > screen.shape[0] or tpl.shape[1] > screen.shape[1]:
                    parts.append(f"{fname}: không đọc được")
                    continue
                _, score, _, (lx, ly) = cv2.minMaxLoc(cv2.matchTemplate(screen, tpl, cv2.TM_CCOEFF_NORMED))
                score = float(score)
                short = fname.replace("button_", "").replace(".png", "")
                parts.append(f"{short}: {score:.2f}")
                if best is None or score > best[0]:
                    best = (score, short, (lx + tpl.shape[1] // 2, ly + tpl.shape[0] // 2))
            saved = ""
            try:   # lưu đúng khung hình bot đang thấy để đối chiếu với cửa sổ LDPlayer
                dbg = asset("debug_idle_screen.png")
                os.makedirs(os.path.dirname(dbg), exist_ok=True)
                if cv2.imwrite(dbg, screen):
                    saved = " | đã lưu assets/debug_idle_screen.png"
            except Exception:
                pass
            self.debug(f"🔎 Không khớp nút nào (ngưỡng 0.75) | ảnh {screen.shape[1]}x{screen.shape[0]} | "
                     + " | ".join(parts) + saved)

            cfg = load_json_config()
            if (best is not None and self._idle_count >= IDLE_CLICK_AFTER
                    and bool(cfg.get("IDLE_CLICK_BEST_MATCH", True))
                    and best[0] >= float(cfg.get("IDLE_CLICK_MIN_SCORE", 0.2))):
                score, name, (bx, by) = best
                self.debug(f"👆 Đứng yên quá lâu -> bấm nút điểm cao nhất '{name}' ({score:.2f}) tại ({bx}, {by})")
                self.adb.click(bx, by)
                time.sleep(1)
        except Exception as e:
            self.debug(f"[⚠️ CẢNH BÁO] _log_idle_screen: {e}")

    # ------------------------------------------------------------------
    # 6b. ĐIỀU KIỆN TÀI NGUYÊN ĐỂ TẤN CÔNG (đọc từ config.json, chỉnh trên GUI)
    # ------------------------------------------------------------------
    def _loot_settings(self):
        """(min_gold, min_elixir, min_dark, mode) từ config.json (đã chuẩn hóa)."""
        cfg = load_json_config()
        return (int(cfg["MIN_GOLD"]), int(cfg["MIN_ELIXIR"]), int(cfg["MIN_DARK_ELIXIR"]),
                str(cfg.get("LOOT_MATCH_MODE", "OR")).upper())

    def _check_loot_conditions(self, gold, elixir, dark, settings=None):
        """
        True nếu nhà địch đạt điều kiện tấn công.
          OR    : gold >= MIN_GOLD  hoặc  elixir >= MIN_ELIXIR
          AND   : gold >= MIN_GOLD  và    elixir >= MIN_ELIXIR
          TOTAL : gold + elixir >= MIN_GOLD + MIN_ELIXIR
        Thêm điều kiện Dầu đen khi MIN_DARK_ELIXIR > 0: (dark is None or dark >= MIN_DARK_ELIXIR)
        (Dầu đen không đọc được thì không chặn). Vàng/Dầu = None coi như 0.
        """
        min_gold, min_elixir, min_dark, mode = settings or self._loot_settings()
        g = gold or 0
        e = elixir or 0
        if mode == "AND":
            base_ok = g >= min_gold and e >= min_elixir
        elif mode == "TOTAL":
            base_ok = (g + e) >= (min_gold + min_elixir)
        else:   # "OR" (mặc định cho mọi giá trị lạ)
            base_ok = g >= min_gold or e >= min_elixir
        dark_ok = True if min_dark <= 0 else (dark is None or dark >= min_dark)
        return bool(base_ok and dark_ok)

    # ------------------------------------------------------------------
    # 7. THẢ QUÂN
    # ------------------------------------------------------------------
    def _stop_requested(self):
        """True nếu luồng chính đã yêu cầu dừng bot (khi có truyền stop_event)."""
        return self.stop_event is not None and self.stop_event.is_set()

    # ------------------------------------------------------------------
    # 7a. NHẬN BIẾT Ô QUÂN TRÊN THANH ĐIỀU KHIỂN
    # ------------------------------------------------------------------
    def _load_template_set(self, pattern):
        """
        Nạp tất cả ảnh mẫu assets/<pattern> (ví dụ button_hero*.png), có cache.
        Đọc bằng imdecode để an toàn với đường dẫn Unicode. Chưa có file -> danh sách rỗng.
        (Thêm file ảnh mẫu mới thì cần khởi động lại bot để nạp.)
        """
        if pattern in self._template_cache:
            return self._template_cache[pattern]
        images = []
        folder = os.path.dirname(asset("_"))
        for path in sorted(glob.glob(os.path.join(folder, pattern))):
            try:
                img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
                if img is not None:
                    images.append(img)
                else:
                    self.debug(f"[⚠️ CẢNH BÁO] Không đọc được ảnh mẫu {os.path.basename(path)}")
            except Exception as e:
                self.debug(f"[⚠️ CẢNH BÁO] Lỗi nạp ảnh mẫu {os.path.basename(path)}: {e}")
        self._template_cache[pattern] = images
        return images

    @staticmethod
    def _best_match_score(crop, templates):
        """Điểm khớp cao nhất (0..1) giữa ô `crop` và danh sách ảnh mẫu; 0.0 nếu không có."""
        best = 0.0
        ch, cw = crop.shape[:2]
        for tpl in templates:
            th, tw = tpl.shape[:2]
            if th > ch or tw > cw:      # mẫu lớn hơn ô -> thu nhỏ vừa ô
                scale = min(ch / th, cw / tw)
                tpl = cv2.resize(tpl, (max(1, int(tw * scale)), max(1, int(th * scale))))
            res = cv2.matchTemplate(crop, tpl, cv2.TM_CCOEFF_NORMED)
            score = float(res.max())
            if np.isfinite(score) and score > best:
                best = score
        return best

    def scan_battle_bar(self, screen):
        """
        Tự nhận biết các ô đang có trên thanh điều khiển trận đánh (ImageProcessor.scan_battle_bar):
          - ô nét đứt rỗng (STD xám phần giữa < BAR_EMPTY_STD = 14.0)  -> bỏ qua
          - khớp assets/button_hero*.png                                -> "hero"
          - khớp assets/button_spell*.png                               -> "spell"
          - còn lại                                                     -> "troop"
        Chưa có ảnh mẫu Phép nhưng đã thấy Tướng: các ô sau Tướng cuối cùng được suy đoán là Phép
        (thứ tự thanh: Lính -> Tướng -> Phép).

        :return: list[dict(type, x, y, deploy_mode, std, score)] (toạ độ 960x540, cùng định dạng DEPLOY_SEQUENCE);
                 rỗng nếu không quét được.
        """
        if screen is None:
            return []
        try:
            slots = ImageProcessor.scan_battle_bar(
                screen, x_range=BAR_SCAN_X_RANGE, center_y=BAR_CENTER_Y, slot_size=BAR_SLOT_SIZE,
                slot_step=BAR_SLOT_STEP, match_pad=BAR_MATCH_PAD, inner_ratio=BAR_INNER_RATIO,
                empty_std=BAR_EMPTY_STD, hero_threshold=BAR_HERO_THRESHOLD,
                spell_threshold=BAR_SPELL_THRESHOLD, hero_pattern=HERO_TEMPLATE_PATTERN,
                spell_pattern=SPELL_TEMPLATE_PATTERN)
            for sl in slots:
                sl["deploy_mode"] = DEFAULT_MODE_BY_TYPE[sl["type"]]
            self._debug_bar_once(screen, slots)
            return slots
        except Exception as e:
            self.debug(f"[⚠️ CẢNH BÁO] scan_battle_bar lỗi: {e}")
            return []

    _scan_battle_bar = scan_battle_bar     # tên cũ, giữ lại cho tương thích

    def _debug_bar_once(self, screen, slots):
        """Chỉ 1 lần mỗi lần chạy: lưu dataset/debug_battle_bar_*.png (khung xanh=lính, vàng=tướng, tím=phép)."""
        if self._bar_debug_done:
            return
        self._bar_debug_done = True
        try:
            colors = {"troop": (0, 255, 0), "hero": (0, 255, 255), "spell": (255, 0, 255)}
            img_h, img_w = screen.shape[:2]
            sx, sy = img_w / 960.0, img_h / 540.0
            half = BAR_SLOT_SIZE // 2
            overlay = screen.copy()
            for sl in slots:
                p1 = (round((sl["x"] - half) * sx), round((sl["y"] - half) * sy))
                p2 = (round((sl["x"] + half) * sx), round((sl["y"] + half) * sy))
                cv2.rectangle(overlay, p1, p2, colors[sl["type"]], 2)
            self._save_image_async(DATASET_DIR, "debug_battle_bar", overlay)
            summary = ", ".join(f"{sl['type']}@{sl['x']}" for sl in slots) or "không có ô nào"
            self.debug(f"🔎 [Thanh quân] Phát hiện {len(slots)} ô: {summary}")
        except Exception as e:
            self.debug(f"[⚠️ CẢNH BÁO] Không tạo được ảnh chẩn đoán thanh quân: {e}")

    def _normalize_slot(self, item, index=0):
        """Chuẩn hóa 1 mục của DEPLOY_SEQUENCE -> dict(type,x,y,deploy_mode) hoặc None nếu sai."""
        try:
            kind = str(item["type"]).strip().lower()
            if kind not in DEFAULT_MODE_BY_TYPE:
                raise ValueError(f"type '{kind}' không hợp lệ (troop/hero/spell)")
            x, y = int(item["x"]), int(item["y"])
            mode = DEPLOY_MODE_ALIASES.get(
                str(item.get("deploy_mode", "")).strip().lower(), DEFAULT_MODE_BY_TYPE[kind])
            count = item.get("count")
            return {"type": kind, "x": x, "y": y, "deploy_mode": mode,
                    "count": int(count) if count else None}
        except Exception as e:
            self.debug(f"[⚠️ CẢNH BÁO] DEPLOY_SEQUENCE[{index}] bị bỏ qua: {e}")
            return None

    def _load_deploy_sequence(self):
        """
        Đọc DEPLOY_SEQUENCE từ config.json (cách NHANH, không quét ảnh). Không khai báo / rỗng /
        không có mục hợp lệ -> None để chuyển sang quét thanh quân.
        """
        try:
            raw = load_json_config().get("DEPLOY_SEQUENCE")
        except Exception as e:
            self.debug(f"[⚠️ CẢNH BÁO] Không đọc được DEPLOY_SEQUENCE: {e}")
            return None
        if not raw:
            return None
        if not isinstance(raw, list):
            self.debug("[⚠️ CẢNH BÁO] DEPLOY_SEQUENCE phải là một danh sách -> bỏ qua")
            return None
        plan = [sl for sl in (self._normalize_slot(it, i) for i, it in enumerate(raw)) if sl]
        return plan or None

    @staticmethod
    def _read_points(raw):
        """Chuyển list [[x, y], ...] thành [(x, y), ...]; sai định dạng -> []."""
        try:
            return [(int(p[0]), int(p[1])) for p in (raw or []) if p and len(p) >= 2]
        except Exception:
            return []

    def _deploy_side(self):
        """Trả về (nhãn log, danh sách điểm thả lính): luôn là điểm ngoài cùng bên trái."""
        return "DẢI BÊN TRÁI (LEFT_EDGE_POINTS)", list(LEFT_EDGE_POINTS)

    def _clicks_per_slot(self):
        try:
            n = int(load_json_config().get("DEPLOY_CLICKS_PER_SLOT", DEPLOY_CLICKS_PER_SLOT))
            return n if n > 0 else DEPLOY_CLICKS_PER_SLOT
        except Exception:
            return DEPLOY_CLICKS_PER_SLOT

    def _spell_count(self):
        """Số lọ phép cần xả: config.json['SPELL_DROP_COUNT'] -> SPELL_DROP_COUNT; kẹp trong khoảng 8-12."""
        lo, hi = SPELL_DROP_COUNT_RANGE
        try:
            n = int(load_json_config().get("SPELL_DROP_COUNT", SPELL_DROP_COUNT))
        except Exception:
            n = SPELL_DROP_COUNT
        return max(lo, min(hi, n))

    def _manual_cards_plan(self, cfg):
        """Tầng 3: DEPLOY_CARDS_CONFIG (thẻ cấu hình thủ công trên GUI) -> danh sách ô cùng định dạng DEPLOY_SEQUENCE."""
        plan = []
        for i, card in enumerate(cfg.get("DEPLOY_CARDS_CONFIG") or [], 1):
            try:
                kind = str(card.get("type", "troop")).strip().lower()
                if kind not in DEFAULT_MODE_BY_TYPE:
                    kind = "troop"
                x, y = (int(v) for v in card["pos"][:2])
                count = int(card.get("clicks_count") or 0)
                skill_mode = str(card.get("skill_mode") or "none").strip().lower()
                if skill_mode not in ("none", "immediate", "delay"):
                    skill_mode = "none"
                try:
                    skill_delay = max(0.0, float(card.get("skill_delay") or 0))
                except Exception:
                    skill_delay = 0.0
                skill_pos = self._read_points([card.get("skill_pos")]) if card.get("skill_pos") else []
                plan.append({"type": kind, "x": x, "y": y, "deploy_mode": DEFAULT_MODE_BY_TYPE[kind],
                             "count": count or None, "name": f"Thẻ {card.get('slot', i)} ({kind})",
                             "zones": card.get("zones") or {},
                             "skill_mode": skill_mode, "skill_delay": skill_delay,
                             "skill_pos": skill_pos[0] if skill_pos else None})
            except Exception as e:
                self.debug(f"[⚠️ CẢNH BÁO] DEPLOY_CARDS_CONFIG[{i - 1}] bị bỏ qua: {e}")
        return plan

    def _build_deploy_plan(self, cfg=None):
        """
        Quyết định danh sách ô cần thả, ƯU TIÊN 3 TẦNG:
          1) DEPLOY_SEQUENCE trong config.json           (nhanh nhất, không phân tích ảnh)
          2) scan_battle_bar() trên ảnh chụp mới          (tự nhận biết thanh quân; tắt bằng DEPLOY_AUTO_SCAN=false)
          3) DEPLOY_CARDS_CONFIG (danh sách thẻ thủ công)  (phương án cuối)
        :return: (tên nguồn, plan) - plan là list[dict(type, x, y, deploy_mode[, count, name])].
        """
        if cfg is None:
            cfg = load_json_config()
        spell_on = bool(cfg.get("SPELL_ENABLED", True))

        def finish(source, plan):
            if not spell_on:   # công tắc "Thả phép" trên UI tắt -> bỏ mọi ô phép
                plan = [sl for sl in plan if sl.get("type") != "spell"]
            return source, plan

        # Ưu tiên 1: thẻ quân người dùng cấu hình trên UI (vị trí ô, loại, số lần bấm)
        plan = self._manual_cards_plan(cfg)
        if plan:
            self.debug(f"🗂️ Dùng thẻ quân cấu hình trên UI ({len(plan)} thẻ)")
            return finish("UI", plan)
        # Phương án dự phòng khi UI chưa khai báo thẻ nào
        plan = self._load_deploy_sequence()
        if plan:
            self.debug(f"📋 UI chưa có thẻ -> dùng DEPLOY_SEQUENCE ({len(plan)} ô)")
            return finish("DEPLOY_SEQUENCE", plan)
        if bool(cfg.get("DEPLOY_AUTO_SCAN", DEPLOY_AUTO_SCAN)):
            plan = self.scan_battle_bar(self._capture(fresh=True))
            if plan:
                self.debug(f"🔍 UI chưa có thẻ -> tự nhận biết thanh quân ({len(plan)} ô)")
                return finish("SCAN_BATTLE_BAR", plan)
        return finish("UI", [])

    @staticmethod
    def _troop_drop_zone(cfg):
        """Điểm thả lính: MANUAL_POSITIONS.TROOP_DROP_ZONE, thiếu thì điểm đầu của LEFT_EDGE_POINTS."""
        try:
            return tuple(cfg["MANUAL_POSITIONS"]["TROOP_DROP_ZONE"])
        except Exception:
            return tuple(LEFT_EDGE_POINTS[0])

    @staticmethod
    def _spell_config(cfg):
        """SPELL_CONFIG chuẩn hóa (đủ TARGET_POSITIONS + DROP_MODE); thiếu thì dùng SPELL_WHITE_ZONES, xoay vòng."""
        sc = cfg.get("SPELL_CONFIG") or {}
        return {"TARGET_POSITIONS": sc.get("TARGET_POSITIONS") or list(SPELL_WHITE_ZONES),
                "DROP_MODE": sc.get("DROP_MODE", "evenly")}

    def execute_deployment(self):
        """
        Thả HẾT lính, Tướng & phép rồi thoát về làng nhà. Danh sách ô được chọn theo 3 tầng
        (xem _build_deploy_plan): DEPLOY_SEQUENCE -> scan_battle_bar -> DEPLOY_CARDS_CONFIG.
          Với mỗi ô: bấm chọn ô (x, y) -> troop: bấm N lần vào điểm thả lính;
          hero: bấm 2-3 lần vào điểm thả; spell: thả N bình vào SPELL_CONFIG.TARGET_POSITIONS.
          Thả xong -> chờ trận (thoát sớm nếu thấy nút Về nhà) -> Về nhà.

        Lỗi ở 1 ô chỉ được log rồi chuyển ô kế tiếp. Phần thoát trận nằm trong finally
        nên dù có lỗi ở đâu bot vẫn luôn Về nhà, không bị kẹt trong trận đấu.

        :return: True nếu thả hết và chờ trận hoàn tất bình thường (dùng để tính thống kê).
        """
        completed = False
        self._has_boosted = False   # trận mới -> được phép bấm Tăng tốc 1 lần
        self._battle_started_at = time.time()   # bật chế độ "đang đánh": tạm tắt Stuck Watchdog
        try:
            cfg = load_json_config()
            drop_zone = self._troop_drop_zone(cfg)
            spell_cfg = self._spell_config(cfg)
            self._cur_deploy_mode = normalize_deploy_mode(cfg.get("DEPLOY_MODE"))
            self._single_point = self._single_point_for(cfg, self._cur_deploy_mode)
            self._pending_skills = []   # kỹ năng Tướng hẹn giờ (thời điểm, vị trí, tên)
            self.set_status("⚔️ Đang thả quân tấn công...", "attack")
            source, plan = self._build_deploy_plan(cfg)
            if not plan:
                self.log("⚠️ Không có ô nào để thả -> hãy thêm thẻ quân ở tab 'Tọa độ & Thẻ quân'")
            self.log(f"⚔️ Bắt đầu thả quân ({len(plan)} ô, nguồn: {source}, kiểu thả: {self._cur_deploy_mode})...")
            for _i, _sl in enumerate(plan, 1):
                self.debug(f"   • Ô {_i}: {_sl['type']} tại ({_sl['x']}, {_sl['y']}), "
                           f"zones={ {m: len(v) for m, v in (_sl.get('zones') or {}).items()} }, count={_sl.get('count')}")
            self._last_guard_at = time.time()   # vừa vào trận: bỏ qua lần chụp kiểm tra ở ô đầu
            self._spell_cursor = 0   # con trỏ xoay vòng vị trí thả phép (chế độ evenly)
            for idx, slot in enumerate(plan, 1):
                name = slot.get("name") or f"Ô {idx} ({slot['type']})"
                if self._stop_requested():
                    self.log("⏹️ Nhận lệnh dừng -> ngừng thả quân, thoát trận")
                    break
                if self._guard_connection_throttled():
                    self.log("🔌 Mất kết nối giữa lúc thả quân -> đã phục hồi, hủy trận này")
                    break
                self._run_due_skills()   # kỹ năng Tướng hẹn giờ đã tới lúc -> bấm trước khi chọn thẻ kế tiếp
                try:
                    self._deploy_slot(name, slot, drop_zone, spell_cfg)
                except Exception as e:
                    self.log(f"[⚠️ CẢNH BÁO] Lỗi khi thả {name}: {e} -> chuyển sang ô kế tiếp")
                time.sleep(DEPLOY_SLOT_DELAY)
            else:
                # for-else: chỉ chạy khi duyệt hết mọi ô mà không bị break
                self._run_due_skills()
                self.log("✅ Đã thả hết lính và phép! Đang chờ kết thúc trận đấu...")
                completed = self._wait_battle_end()
        except Exception as e:
            self.log(f"[❌ LỖI] execute_deployment: {e}")
            completed = False
        finally:
            # Luôn thoát về làng nhà, bất kể lỗi gì ở phía trên
            self._pending_skills = []   # hết trận: bỏ mọi kỹ năng chưa tới giờ
            try:
                self._leave_battle()
            finally:
                self._battle_started_at = None   # hết trận -> bật lại Stuck Watchdog
                self._watchdog_reset()
        return completed

    def deploy_troops(self):
        """Tên cũ, giữ lại cho tương thích -> execute_deployment()."""
        return self.execute_deployment()

    def _deploy_slot(self, name, slot, drop_zone, spell_cfg):
        """
        Chọn 1 ô tại (x, y) rồi thả theo loại (chỉ CLICK):
          troop : `count` lần vào các điểm riêng của THẺ này (theo kiểu thả: 1 góc / dàn trải / ngẫu nhiên);
          hero  : thả ĐÚNG 1 LẦN, rồi (tùy chọn) bấm kỹ năng ngay hoặc sau N giây;
          spell : `count` bình vào SPELL_CONFIG.TARGET_POSITIONS.
        """
        pos = (int(slot["x"]), int(slot["y"]))
        kind = slot["type"]
        count = slot.get("count")
        self.debug(f"🪖 Chọn {name} tại {pos}...")
        try:
            self.adb.click(*pos)
            time.sleep(DEPLOY_SELECT_DELAY)
        except Exception as e:
            self.debug(f"[⚠️ CẢNH BÁO] Không chọn được {name}: {e} -> bỏ qua ô này")
            return
        if kind == "troop":
            self._deploy_troop_clicks(self._card_points(slot, drop_zone), count or self._clicks_per_slot())
        elif kind == "hero":
            self._deploy_hero_once(name, slot, drop_zone)
        elif kind == "spell":
            self._deploy_spell_clicks(spell_cfg, count or self._spell_count())

    # ---------------------------------------------------------------- Điểm thả theo THẺ
    @staticmethod
    def _single_point_for(cfg, mode):
        """
        Tọa độ DUY NHẤT dùng cho mọi thẻ khi chọn 'Thả 1 góc': trái = MANUAL_POSITIONS.TROOP_DROP_ZONE,
        phải = MANUAL_POSITIONS.DEPLOY_POINT_RIGHT (thiếu -> lấy đối xứng qua trục dọc giữa màn hình).
        """
        pos = (cfg.get("MANUAL_POSITIONS") or {})

        def center(v):
            v = [int(x) for x in v]
            return ((v[0] + v[2]) // 2, (v[1] + v[3]) // 2) if len(v) >= 4 else (v[0], v[1])
        try:
            left = center(pos["TROOP_DROP_ZONE"])
        except Exception:
            left = tuple(LEFT_EDGE_POINTS[0])
        if mode == "right_corner":
            try:
                return center(pos["DEPLOY_POINT_RIGHT"])
            except Exception:
                return (960 - left[0], left[1])
        return left

    def _card_points(self, slot, drop_zone):
        """
        Điểm thả của MỘT thẻ theo kiểu thả hiện tại:
          - Thả 1 góc (left/right_corner): 1 tọa độ CHUNG cho mọi thẻ Lính + Tướng.
          - spread_left / spread_right / random_around: slot["zones"][kiểu] = các điểm riêng của thẻ
            (thiếu -> dự phòng theo TROOP_DROP_ZONE: trái / phải / cả hai).
          - Tướng luôn chỉ dùng 1 điểm (điểm đầu tiên).
        """
        mode = getattr(self, "_cur_deploy_mode", "left_corner")
        if mode in DEPLOY_SINGLE_MODES:
            pt = getattr(self, "_single_point", None)
            if pt is None:
                dz = [int(v) for v in drop_zone]
                px, py = ((dz[0] + dz[2]) // 2, (dz[1] + dz[3]) // 2) if len(dz) >= 4 else (dz[0], dz[1])
                pt = (960 - px, py) if mode == "right_corner" else (px, py)
            return [tuple(pt)]
        pts = self._read_points((slot.get("zones") or {}).get(mode))
        if not pts:
            self.log(f"[⚠️ CẢNH BÁO] {slot.get('name') or 'Thẻ'} KHÔNG có tọa độ riêng cho kiểu '{mode}' "
                     f"(zones đọc được: {list((slot.get('zones') or {}).keys())}) -> dùng tọa độ mặc định trái/phải")
            dz = [int(v) for v in drop_zone]
            px, py = ((dz[0] + dz[2]) // 2, (dz[1] + dz[3]) // 2) if len(dz) >= 4 else (dz[0], dz[1])
            if mode == "spread_right":
                pts = [(960 - px, py)]
            elif mode == "random_around":
                pts = [(px, py), (960 - px, py)]
            else:
                pts = [(px, py)]
        if slot.get("type") == "hero":
            pts = pts[:1]
        return pts

    def _pick_drop_point(self, points, index):
        """Điểm cho lần bấm thứ `index`: spread = xoay vòng đều | random_around = ngẫu nhiên | 1 góc = điểm duy nhất."""
        mode = getattr(self, "_cur_deploy_mode", "left_corner")
        if len(points) == 1 or mode in DEPLOY_SINGLE_MODES:
            return points[0]
        if mode == "random_around":
            return random.choice(points)
        return points[index % len(points)]   # spread: chia đều số lần bấm cho từng điểm

    def _deploy_troop_clicks(self, points, count):
        """Bấm `count` lần vào các điểm của thẻ (jitter ±DEPLOY_CLICK_JITTER px)."""
        jitter = abs(int(DEPLOY_CLICK_JITTER))
        mode = getattr(self, "_cur_deploy_mode", "left_corner")
        self.debug(f"🪖 Thả lính {count} lần, kiểu '{mode}', {len(points)} điểm...")
        for i in range(count):
            if self._stop_requested():
                return
            px, py = self._pick_drop_point(points, i)
            self._safe_click(max(0, min(959, px + self._safe_randint(-jitter, jitter))),
                             max(0, min(539, py + self._safe_randint(-jitter, jitter))))
            if DEPLOY_CLICK_INTERVAL > 0:
                time.sleep(DEPLOY_CLICK_INTERVAL)

    # ---------------------------------------------------------------- Tướng + kỹ năng
    def _deploy_hero_once(self, name, slot, drop_zone):
        """Tướng: thả đúng 1 lần, rồi xử lý kỹ năng theo slot['skill_mode'] (none / immediate / delay)."""
        points = self._card_points(slot, drop_zone)
        px, py = self._pick_drop_point(points, 0)
        px, py = max(0, min(959, px)), max(0, min(539, py))
        self.debug(f"🦸 Thả {name} 1 lần tại ({px}, {py})...")
        if not self._safe_click(px, py):
            return
        skill_mode = slot.get("skill_mode") or "none"
        if skill_mode == "none":
            return
        skill_pos = tuple(slot.get("skill_pos") or (slot["x"], slot["y"]))   # mặc định: bấm lại đúng thẻ Tướng
        if skill_mode == "immediate":
            time.sleep(HERO_SKILL_IMMEDIATE_GAP)
            self._safe_click(*skill_pos)
            self.log(f"✨ Đã dùng kỹ năng {name} ngay khi thả (bấm {skill_pos})")
        elif skill_mode == "delay":
            delay = float(slot.get("skill_delay") or 0)
            self._pending_skills.append((time.time() + delay, skill_pos, name))
            self.debug(f"⏱️ Hẹn dùng kỹ năng {name} sau {delay:g}s (bấm {skill_pos})")

    def _run_due_skills(self):
        """Bấm kỹ năng Tướng đã tới giờ hẹn. Gọi giữa các thẻ và trong vòng chờ hết trận (cùng luồng, không đè lệnh thả)."""
        pending = getattr(self, "_pending_skills", None)
        if not pending:
            return
        now = time.time()
        due = [it for it in pending if it[0] <= now]
        if not due:
            return
        self._pending_skills = [it for it in pending if it[0] > now]
        for _t, skill_pos, name in sorted(due, key=lambda it: it[0]):
            self._safe_click(*skill_pos)
            self.log(f"✨ Đã dùng kỹ năng {name} (bấm {skill_pos})")

    @staticmethod
    def _safe_randint(a, b):
        """Tạo số nguyên ngẫu nhiên an toàn giữa a và b, tự động đảo ngược nếu a > b."""
        low = min(int(a), int(b))
        high = max(int(a), int(b))
        if low == high:
            return low
        return random.randint(low, high)

    def _safe_click(self, x, y):
        """Click 1 điểm, KHÔNG ném lỗi. :return: True nếu click thành công."""
        try:
            self.adb.click(int(x), int(y))
            return True
        except Exception as e:
            self.debug(f"[⚠️ CẢNH BÁO] Click ({x}, {y}) thất bại: {e}")
            return False

    def _deploy_spell_clicks(self, spell_cfg, count):
        """Thả `count` bình phép theo SPELL_CONFIG (evenly = xoay vòng, random = ngẫu nhiên)."""
        targets = [(max(0, int(p[0])), max(0, int(p[1]))) for p in spell_cfg["TARGET_POSITIONS"]]
        if not targets:
            self.log("[⚠️ CẢNH BÁO] SPELL_CONFIG.TARGET_POSITIONS trống -> bỏ qua thả phép")
            return
        mode = spell_cfg["DROP_MODE"]
        self.debug(f"🧪 Thả {count} bình phép, chế độ '{mode}', {len(targets)} vị trí mục tiêu...")
        for _ in range(count):
            if self._stop_requested():
                return
            if mode == "random":
                px, py = random.choice(targets)
            else:
                px, py = targets[self._spell_cursor % len(targets)]
                self._spell_cursor += 1
            self._safe_click(px, py)
            time.sleep(SPELL_DROP_INTERVAL)

    def _boost_available(self):
        """True nếu có ảnh mẫu assets/button_boost.png."""
        return _template_names_exist(TPL_BOOST)

    @staticmethod
    def _scale_roi(roi, screen):
        """ROI toạ độ 960x540 -> toạ độ thật của `screen` (đã kẹp trong khung)."""
        h, w = screen.shape[:2]
        sx, sy = w / 960.0, h / 540.0
        x1, y1, x2, y2 = roi
        return max(0, int(x1 * sx)), max(0, int(y1 * sy)), min(w, int(x2 * sx)), min(h, int(y2 * sy))

    def _boost_threshold(self):
        try:
            return float(load_json_config().get("BOOST_THRESHOLD", BOOST_THRESHOLD))
        except Exception:
            return BOOST_THRESHOLD

    def _boost_view(self, screen):
        """Bản sao màn hình đã TÔ ĐEN các vùng không thể có nút Tăng tốc (thanh quân, nút Đầu hàng) để ảnh mẫu
        không khớp nhầm vào ô quân. Có BOOST_ROI trong config -> chỉ giữ lại đúng vùng đó."""
        try:
            cfg = load_json_config()
        except Exception:
            cfg = {}
        view = screen.copy()
        only = cfg.get("BOOST_ROI")
        if only and len(only) == 4:
            x1, y1, x2, y2 = self._scale_roi(only, screen)
            keep = screen[y1:y2, x1:x2].copy()
            view[:] = 0
            view[y1:y2, x1:x2] = keep
        for z in cfg.get("BOOST_EXCLUDE_ROIS", BOOST_EXCLUDE_ROIS):
            if len(z) == 4:
                x1, y1, x2, y2 = self._scale_roi(z, screen)
                view[y1:y2, x1:x2] = 0
        return view

    def _find_boost(self, screen):
        """Tìm nút Tăng tốc (1 ảnh mẫu khớp là đủ) trên màn hình đã tô đen thanh quân / nút Đầu hàng. (x, y) hoặc None."""
        if screen is None or not _template_names_exist(TPL_BOOST):
            return None
        return ImageProcessor.find_template_any(self._boost_view(screen), _asset_specs(TPL_BOOST),
                                                self._boost_threshold(), min_votes=BOOST_MIN_VOTES)

    @staticmethod
    def _green_ratio(screen, pos, half_w=45, half_h=16):
        """Tỉ lệ điểm ảnh XANH LÁ trong hộp quanh `pos` (0..1). Nút 'Về nhà' thật có nhiều xanh lá."""
        try:
            x, y = int(pos[0]), int(pos[1])
            roi = screen[max(0, y - half_h):y + half_h, max(0, x - half_w):x + half_w]
            if roi.size == 0:
                return 0.0
            hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
            mask = cv2.inRange(hsv, np.array(HOME_GREEN_HSV[0]), np.array(HOME_GREEN_HSV[1]))
            return float(mask.mean()) / 255.0
        except Exception:
            return 0.0

    def _find_home_template(self, screen):
        """
        Nút 'Về nhà' bằng ảnh mẫu, CÓ KIỂM TRA: ngưỡng khớp thấp (0.60) nên ảnh mẫu hay khớp nhầm vào ô quân; chỉ nhận khi
        điểm >= HOME_TRUST_SCORE hoặc quanh vị trí khớp có màu xanh lá của nút thật. (x, y) hoặc None.
        """
        try:
            d = ImageProcessor.find_template_any(screen, _asset_specs(TPL_RETURN_HOME), RETURN_HOME_THRESHOLD,
                                                 return_details=True)
            if not d:
                return None
            pos, score = d["pos"], d["score"]
            if score >= HOME_TRUST_SCORE or self._green_ratio(screen, pos) >= HOME_MIN_GREEN_RATIO:
                return pos
            self.debug(f"ℹ️ Ảnh mẫu 'Về nhà' khớp {score:.2f} tại {tuple(pos)} nhưng không có màu xanh lá -> khớp nhầm, bỏ qua")
            return None
        except Exception:
            return None

    def _tap(self, pos, what="nút"):
        """Bấm (x, y) qua ADB. adb báo lỗi (trả False) -> thử lại 1 lần, vẫn lỗi thì log rõ lý do. :return: True nếu bấm được."""
        x, y = int(pos[0]), int(pos[1])
        for _ in range(2):
            if self.adb.click(x, y) is not False:      # bản adb cũ trả None = coi như thành công
                return True
            self.log(f"[⚠️ CẢNH BÁO] ADB bấm {what} tại ({x}, {y}) lỗi: {getattr(self.adb, 'last_error', '')} -> thử lại")
            time.sleep(0.2)
        return False

    def _see_battle_summary(self, screen):
        """True nếu thấy bảng tổng kết thiệt hại % (ảnh mẫu tùy chọn assets/battle_summary*.png)."""
        try:
            return (_template_names_exist(TPL_BATTLE_SUMMARY)
                    and bool(_find_any(screen, TPL_BATTLE_SUMMARY, threshold=0.75)))
        except Exception:
            return False

    def in_battle_loop(self):
        """Tên theo đặc tả -> _wait_battle_end() (vòng giám sát trận sau khi xả lính)."""
        return self._wait_battle_end()

    def _battle_in_progress(self, screen):
        """True nếu thấy nút đỏ 'Kết thúc trận chiến / Đầu hàng' (góc dưới trái) = trận CHƯA kết thúc."""
        try:
            h, w = screen.shape[:2]
            sx, sy = w / 960.0, h / 540.0
            x1, y1, x2, y2 = BATTLE_END_BTN_ROI
            roi = screen[int(y1 * sy):int(y2 * sy), int(x1 * sx):int(x2 * sx)]
            if roi.size == 0:
                return False
            hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
            red = ((hsv[..., 0] <= 8) | (hsv[..., 0] >= 172)) & (hsv[..., 1] >= 130) & (hsv[..., 2] >= 130)
            return float(red.mean()) >= BATTLE_END_BTN_RED_RATIO
        except Exception:
            return False

    def _scan_battle_buttons(self, screen):
        """
        Quét SONG SONG (2 luồng) trên CÙNG 1 khung hình:
          - nút Tăng tốc  (button_boost*.png);
          - nút Về nhà    (button_return_home*.png, dự phòng nút xanh lá HSV).
        Chỉ dùng hàm của ImageProcessor (có cache + khóa luồng), không đụng cache của BotLogic.
        :return: (boost_pos, home_pos, home_src) với home_src = 'template' | 'hsv' | None.
        """
        def scan_boost():
            return self._find_boost(screen)

        def scan_home():
            pos = self._find_home_template(screen)
            if pos:
                return pos, "template"
            pos = ImageProcessor.find_green_button_hsv(screen)
            return (pos, "hsv") if pos else (None, None)

        pool = getattr(self, "_scan_pool", None)
        if pool is None:                       # 1 pool dùng lại mọi vòng (không tạo/hủy luồng mỗi giây)
            pool = self._scan_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="scan")
        f_boost, f_home = pool.submit(scan_boost), pool.submit(scan_home)
        boost_pos = f_boost.result()
        home_pos, home_src = f_home.result()
        return boost_pos, home_pos, home_src

    def _wait_battle_end(self):
        """
        Giám sát trận SAU KHI thả quân: KHÔNG chờ cố định rồi tự kết luận. Lặp (mỗi 0.5-1.0s, tối đa
        BATTLE_MONITOR_TIMEOUT = 180s), mỗi vòng chụp khung MỚI rồi quét SONG SONG nút Tăng tốc + nút Về nhà:
          - thấy nút 'Về nhà' (đủ BƯỚC xác nhận)  -> trận đã kết thúc: KHÔNG bấm Tăng tốc nữa, thoát ngay để
                                                      _leave_battle() bấm Về nhà, rồi vòng chính đi đánh tiếp / nâng Tường;
          - chưa thấy 'Về nhà' + còn nút Tăng tốc chưa bấm -> bấm Tăng tốc (1 lần/trận);
          - đã Tăng tốc rồi                          -> vẫn tiếp tục tìm nút 'Về nhà'.
        Chống kết luận sớm: nút 'Về nhà' phải thấy liên tiếp ở cùng vị trí (ảnh mẫu: 2 khung, HSV: 3 khung);
        khối xanh HSV bị bỏ qua khi nút Tăng tốc còn hiện (trận vẫn đang đánh, khối xanh chỉ là thẻ quân).
        :return: True khi trận kết thúc (hoặc hết 180s: vẫn trả True để _leave_battle() tự quét tiếp, log nói rõ);
                 False nếu có lệnh dừng hoặc vừa phục hồi mất kết nối.
        """
        self._has_boosted = False   # trận mới -> được phép bấm Tăng tốc 1 lần
        self.log(f"👀 Giám sát trận đấu [{CODE_VERSION}]: quét Tăng tốc + Về nhà song song (tối đa {BATTLE_MONITOR_TIMEOUT}s)...")
        self.set_status("⏳ Đang chờ kết thúc trận...", "wait")
        started = time.time()
        streak, last_pos, last_src = 0, None, None
        last_diag = 0.0
        boost_seen = None            # (thời điểm thấy, vị trí) nút Tăng tốc đang chờ đủ BOOST_CLICK_DELAY
        while time.time() - started < BATTLE_MONITOR_TIMEOUT:
            if self._stop_requested():
                self.log("⏹️ Nhận lệnh dừng -> thoát trận sớm")
                return False
            self._run_due_skills()                  # kỹ năng Tướng hẹn giờ (nếu tới lúc)
            screen = self._capture(fresh=True)      # khung MỚI mỗi vòng, không dùng ảnh cache cũ
            if screen is not None:
                if time.time() - last_diag >= DIAG_INTERVAL:
                    last_diag = time.time()
                    self._log_battle_diag(screen)
                try:
                    boost_pos, home_pos, home_src = self._scan_battle_buttons(screen)
                except Exception as e:
                    self.debug(f"[⚠️ CẢNH BÁO] Quét nút Tăng tốc / Về nhà lỗi: {e}")
                    boost_pos, home_pos, home_src = None, None, None
                if home_pos and self._battle_in_progress(screen):
                    home_pos = None        # còn nút đỏ Kết thúc/Đầu hàng = trận đang đánh, KHÔNG phải nút Về nhà
                if home_pos and home_src == "hsv" and boost_pos is not None:
                    home_pos = None        # còn nút Tăng tốc = đang đánh, khối xanh không phải nút Về nhà
                if self._see_battle_summary(screen):   # bảng tổng kết thiệt hại % hiện ra = chắc chắn hết trận
                    self.log("🏁 Trận đấu đã kết thúc (thấy bảng tổng kết thiệt hại %) -> rời trận")
                    return True

                if home_pos:
                    same = (last_pos is not None and home_src == last_src
                            and abs(home_pos[0] - last_pos[0]) <= RETURN_HOME_CONFIRM_TOL
                            and abs(home_pos[1] - last_pos[1]) <= RETURN_HOME_CONFIRM_TOL)
                    streak = streak + 1 if same else 1
                    last_pos, last_src = home_pos, home_src
                    need = RETURN_HOME_CONFIRM_HSV if home_src == "hsv" else RETURN_HOME_CONFIRM_TEMPLATE
                    if streak >= need:
                        how = "nút xanh lá (HSV)" if home_src == "hsv" else "ảnh mẫu button_return_home"
                        self.log(f"🏁 Trận đấu đã kết thúc (thấy nút 'Về nhà' bằng {how}) -> không Tăng tốc nữa, rời trận")
                        return True
                    # Đã thấy 'Về nhà' (đang xác nhận): TUYỆT ĐỐI không bấm Tăng tốc ở vòng này
                else:
                    streak, last_pos, last_src = 0, None, None
                    if boost_pos is not None and not self._has_boosted:
                        # Quét SONG SONG: đếm 2s mà vẫn tiếp tục quét nút 'Về nhà' ở mỗi vòng (không chặn).
                        # Nút phải còn thấy liên tục ở cùng chỗ trong suốt 2s mới bấm; 'Về nhà' hiện ra thì thôi, không Tăng tốc nữa.
                        now = time.time()
                        moved = (boost_seen is None
                                 or abs(boost_pos[0] - boost_seen[1][0]) > RETURN_HOME_CONFIRM_TOL
                                 or abs(boost_pos[1] - boost_seen[1][1]) > RETURN_HOME_CONFIRM_TOL)
                        if BOOST_CLICK_DELAY <= 0 or (not moved and now - boost_seen[0] >= BOOST_CLICK_DELAY):
                            self._tap(boost_pos, "Tăng tốc")      # thấy là bấm luôn (delay = 0) / hoặc đã thấy đủ lâu
                            self._has_boosted = True
                            boost_seen = None
                            self.log(f"⏩ Đã bấm nút Tăng tốc trận đấu tại {tuple(boost_pos)}! (vẫn tiếp tục tìm nút 'Về nhà')")
                        elif moved:
                            boost_seen = (now, tuple(boost_pos))
                            self.log(f"🔎 Thấy nút Tăng tốc tại {tuple(boost_pos)} -> đợi {BOOST_CLICK_DELAY:g}s (vẫn quét 'Về nhà') rồi bấm")
                    else:
                        boost_seen = None
                    if self._guard_connection(screen):
                        self.log("🔌 Mất kết nối khi chờ trận -> đã bấm phục hồi, thoát luồng chờ trận")
                        return False
            self._maybe_gc()
            self._sleep_interruptible(BATTLE_CONFIRM_POLL if streak else random.uniform(BATTLE_POLL_MIN, BATTLE_POLL_MAX))
        self.log(f"[⚠️ CẢNH BÁO] Quá {BATTLE_MONITOR_TIMEOUT}s chưa xác nhận được nút 'Về nhà' "
                 f"(KHÔNG kết luận trận đã xong) -> chuyển sang _leave_battle() tiếp tục quét")
        return True

    def _log_battle_diag(self, screen):
        """Log điểm khớp CAO NHẤT từng ảnh mẫu Tăng tốc / Về nhà (bất kể ngưỡng) để biết vì sao không nhận ra nút."""
        try:
            parts = []
            if _template_names_exist(TPL_BOOST):
                sc = ImageProcessor.best_match_scores(self._boost_view(screen), _asset_specs(TPL_BOOST))
                parts.append(f"Tăng tốc (ngưỡng {self._boost_threshold():.2f}): "
                             + ", ".join(f"{n}={s:.2f}@{p}" for n, s, p in sc))
            else:
                parts.append("Tăng tốc: THIẾU ảnh mẫu")
            if _template_names_exist(TPL_RETURN_HOME):
                sc = ImageProcessor.best_match_scores(screen, _asset_specs(TPL_RETURN_HOME))
                parts.append(f"Về nhà (ngưỡng {RETURN_HOME_THRESHOLD:.2f}, cần xanh>={HOME_MIN_GREEN_RATIO}): " + ", ".join(
                    f"{n}={s:.2f}@{p} xanh={self._green_ratio(screen, p) if p else 0:.2f}" for n, s, p in sc))
            else:
                parts.append("Về nhà: THIẾU ảnh mẫu")
            parts.append(f"nút xanh HSV: {self._find_green_button(screen)}")
            self.log("📊 " + " | ".join(parts))
        except Exception as e:
            self.debug(f"[⚠️ CẢNH BÁO] _log_battle_diag: {e}")

    def _find_green_button(self, screen):
        """
        Dự phòng nút 'Về nhà' khi KHÔNG tìm thấy ảnh mẫu button_return_home*: quét khối nút xanh lá ở 35% đáy
        màn hình bằng HSV (ImageProcessor.find_green_button_hsv). Trả (x, y) tâm nút hoặc None.
        """
        try:
            return ImageProcessor.find_green_button_hsv(screen)
        except Exception:
            return None

    def _find_return_home(self, screen):
        """
        Tìm nút thoát trận: (1) mọi ảnh mẫu assets/button_return_home*.png (thêm ảnh 'Về làng' cũng được)
        bằng find_any_template_location (cache ảnh mẫu, thấy khớp rất cao là trả ngay);
        (2) không thấy -> dò nút xanh lá theo màu (chống lệch ngôn ngữ / ảnh mẫu cắt chưa chuẩn).
        """
        try:
            if self._battle_in_progress(screen):
                return None   # còn nút đỏ Đầu hàng -> trận chưa xong, tuyệt đối không bấm (tránh bấm nhầm đầu hàng)
            pos = self._find_home_template(screen)
            if pos:
                # Ngưỡng 0.60 thấp -> ảnh mẫu có thể khớp NHẦM chỗ khác. Nút 'Về nhà/Về làng' luôn là khối XANH LÁ:
                # nếu thấy nút xanh mà ảnh mẫu lệch xa nó -> tin nút xanh (bấm đúng chỗ).
                green = self._find_green_button(screen)
                if green and (abs(green[0] - pos[0]) > HOME_GREEN_MATCH_TOL
                              or abs(green[1] - pos[1]) > HOME_GREEN_MATCH_TOL):
                    self.log(f"🟢 Ảnh mẫu 'Về nhà' khớp tại {tuple(pos)} nhưng nút xanh thật nằm ở {tuple(green)} "
                             f"-> bấm vào nút xanh")
                    return green
                return pos
            pos = self._find_green_button(screen)
            if pos and not getattr(self, "_green_logged", False):
                self._green_logged = True
                self.debug(f"🟢 Ảnh mẫu 'Về nhà' không khớp -> nhận ra nút xanh lá theo màu tại {pos} "
                         f"(nên cắt thêm ảnh nút này thành assets/button_return_home_2.png)")
            return pos
        except Exception:
            return None

    def _see_return_home(self, screen):
        """True nếu thấy nút thoát trận trên `screen` (ảnh mẫu hoặc nút xanh lá)."""
        try:
            return bool(self._find_return_home(screen))
        except Exception:
            return False

    def _see_home_village(self, screen):
        """True nếu đã ở làng nhà: thấy nút Búa (button_build*) hoặc nút Chiến đấu (button_battle*)."""
        try:
            return bool(_find_any(screen, TPL_BUILD, threshold=0.75)
                        or _find_any(screen, TPL_BATTLE, threshold=0.75))
        except Exception:
            return False

    def _home_click_pos(self, screen, pos, attempts):
        """Vị trí bấm 'Về nhà' ở lần thứ `attempts` - CHỈ theo xử lý ảnh, không bao giờ bấm toạ độ cố định:
        2 lần đầu theo ảnh mẫu; kẹt hơn nữa thì luân phiên giữa vị trí ảnh mẫu và khối nút XANH LÁ (HSV) vừa dò được."""
        click_pos = tuple(pos)
        if attempts > HOME_CLICK_ESCALATE_AFTER and attempts % 2 == 1 and screen is not None:
            alt = self._find_green_button(screen)
            if alt and (abs(alt[0] - pos[0]) > 10 or abs(alt[1] - pos[1]) > 10):
                click_pos = tuple(alt)
                self.log(f"↩️ Đã bấm {attempts - 1} lần chưa về được làng -> thử bấm nút xanh lá dò theo màu {click_pos}")
        return click_pos

    def _leave_battle(self):
        """
        Thoát trận về làng nhà, KHÔNG ném lỗi:
          1) Quét liên tục (0.7s/lần) tìm nút 'Về nhà' (ảnh mẫu button_return_home*, đối chiếu nút xanh lá).
             Chưa thấy -> không bấm gì, chỉ quét tiếp (đã vào luồng rời trận nên KHÔNG Tăng tốc nữa).
          2) THẤY NÚT -> bấm NGAY (HOME_CLICK_DELAY = 0). Đặt HOME_CLICK_DELAY > 0 thì đợi rồi chụp khung MỚI quét lại, còn nút mới bấm.
          3) Sau khi bấm: quét 0.5s/lần tới khi thấy nút Búa / Chiến đấu ở làng nhà = xong.
             Quá HOME_RECLICK_AFTER (3s) mà vẫn thấy nút 'Về nhà' (chưa thấy Búa) -> quay lại bước 1: quét và bấm lại.
             Bấm quá HOME_CLICK_ESCALATE_AFTER lần vẫn kẹt -> luân phiên vị trí ảnh mẫu / nút xanh HSV (chỉ theo ảnh, KHÔNG bấm toạ độ cố định).
        Quá HOME_VERIFY_TIMEOUT (60s) kể từ lần bấm đầu mà chưa về làng -> trả quyền cho vòng lặp chính.
        """
        if not _template_names_exist(TPL_RETURN_HOME):
            self.debug("ℹ️ Thiếu assets/button_return_home.png -> chỉ nhận nút 'Về nhà' bằng màu xanh lá")
        self.set_status("⏳ Đang chờ kết thúc trận...", "wait")
        attempts, first_click_at = 0, None

        def at_home(scr):
            if scr is not None and self._see_home_village(scr):
                self.log("🏠 Đã về Làng chính" if attempts else "✅ Đã ở làng nhà -> thoát luồng rời trận")
                self.set_status("🏠 Đang ở Làng chính", "home")
                return True
            return False

        try:
            while True:
                if self._stop_requested():
                    self.log("⏹️ Nhận lệnh dừng -> ngừng quét nút 'Về nhà'")
                    return
                if first_click_at is not None and time.time() - first_click_at > HOME_VERIFY_TIMEOUT:
                    self.log("[⚠️ CẢNH BÁO] Chưa thấy làng nhà sau nhiều lần bấm 'Về nhà' -> trả quyền cho vòng lặp chính")
                    return
                self._maybe_gc()
                screen = self._capture()
                if screen is None:
                    time.sleep(0.5)
                    continue
                if at_home(screen):
                    return
                pos = self._find_return_home(screen)
                if pos is None:
                    if self._guard_connection(screen):
                        self.log("🔌 Mất kết nối khi rời trận -> đã phục hồi, thoát luồng rời trận")
                        return
                    self._sleep_interruptible(RETURN_HOME_POLL_INTERVAL)   # đã vào luồng rời trận: KHÔNG Tăng tốc nữa
                    continue

                self.set_status("🏠 Đang quay về Làng chính...", "home")
                if HOME_CLICK_DELAY > 0:
                    # --- THẤY NÚT: đợi rồi quét lại xác nhận ---
                    self.log(f"🔎 Thấy nút 'Về nhà' tại {tuple(pos)} -> đợi {HOME_CLICK_DELAY:g}s rồi bấm")
                    if not self._sleep_interruptible(HOME_CLICK_DELAY):
                        return
                    screen = self._capture(fresh=True)
                    if screen is None:
                        continue
                    if at_home(screen):
                        return
                    pos = self._find_return_home(screen)
                    if pos is None:
                        self.log("↩️ Sau khi chờ không còn thấy nút 'Về nhà' -> quét lại")
                        continue
                # HOME_CLICK_DELAY = 0: thấy nút là bấm ngay tại vị trí vừa quét được

                attempts += 1
                click_pos = self._home_click_pos(screen, pos, attempts)
                self.log(f"🏠 Bấm 'Về nhà' lần {attempts} tại {tuple(click_pos)}")
                self._tap(click_pos, "Về nhà")
                click_at = time.time()
                if first_click_at is None:
                    first_click_at = click_at

                # --- Sau khi bấm: quét tới khi thấy nút Búa / Chiến đấu ---
                while time.time() - click_at < HOME_VERIFY_WAIT:
                    if not self._sleep_interruptible(HOME_VERIFY_INTERVAL):
                        return
                    screen = self._capture()
                    if screen is None:
                        continue
                    if at_home(screen):
                        return
                    if self._guard_connection(screen):
                        self.log("🔌 Mất kết nối khi chờ về làng -> đã phục hồi, thoát luồng rời trận")
                        return
                    if time.time() - click_at >= HOME_RECLICK_AFTER and self._find_return_home(screen):
                        self.log("↩️ Vẫn thấy nút 'Về nhà', chưa thấy nút Búa ở làng -> quét lại và bấm lại")
                        break
                # hết vòng chờ / còn nút -> quay lại đầu vòng ngoài: quét lại, đợi 2s, bấm lại
        except Exception as e:
            self.log(f"[❌ LỖI] Không thoát được trận: {e}")