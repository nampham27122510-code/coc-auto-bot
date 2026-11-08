import difflib
import glob
import hashlib
import os
import re
import shutil
import threading
import time
import unicodedata
from collections import OrderedDict
import cv2
import numpy as np
import pytesseract
from config import (
    TESSERACT_PATH, TESSERACT_ERROR_MSG, BASE_DIR, LOOT_REGIONS, BUILDER_REGION,
    HOME_RESOURCE_REGIONS, load_json_config,
)

# ---------------------------------------------------------------------------
# Cấu hình Tesseract + cảnh báo an toàn
# ---------------------------------------------------------------------------

# Hàm ghi log. Mặc định là print (console). Có thể thay bằng AppUI.log:
#     from core.image_processing import set_logger
#     set_logger(ui.log)
_log_callback = print
_warned = False          # chỉ cảnh báo thiếu Tesseract 1 lần
_logged_keys = set()     # các cảnh báo khác chỉ ghi 1 lần (tránh spam mỗi vòng lặp)

# Mọi phân số "X/Y" trong vùng thợ xây. Dấu '/' hay bị OCR nhầm thành | l I nên chấp nhận cả 3.
# Khi có Thợ Goblin vùng này hiện 2 phân số (vd "1/6 1/2") -> lấy phân số ĐẦU TIÊN (thợ chính).
_BUILDER_PATTERN = re.compile(r"(\d{1,2})\s*[/|lI]\s*(\d{1,2})")


# --- Nhận diện thẻ Tường trong menu Búa ---
_WALL_TEMPLATES = (("card_wall.png", "card"), ("icon_wall.png", "icon"),
                   ("text_wall.png", "text"))   # ưu tiên thẻ nguyên dòng; text_wall.png = ảnh cắt riêng chữ "Tường thành" (không có "x N"), tùy chọn
# Thẻ Tường nguyên dòng theo crop_tool (mục 12-21): wildcard -> nạp cả biến thể _2, _3... (mỗi pattern gắn gợi ý tài nguyên).
# Mỗi file được khớp BẰNG MASK (đục lỗ vùng giữa 35%-65% chứa số lượng xN), lấy điểm CAO NHẤT trong mọi biến thể.
_WALL_ITEM_PATTERNS = (
    ("wall_item_lvA_gold*.png", "GOLD"),
    ("wall_item_lvA_elixir*.png", "ELIXIR"),
    ("wall_item_lvB_gold*.png", "GOLD"),
    ("wall_item_lvB_elixir*.png", "ELIXIR"),
)
_WALL_MATCH_THRESHOLD_DEFAULT = 0.75   # ngưỡng khớp thẻ Tường (cũ 0.48 -> nhận nhầm công trình khác)
_WALL_AMBIGUOUS_MIN = 0.60        # điểm template trong [0.60, threshold) = nghi vấn -> hỏi OCR
_WALL_KEYWORDS = {"tuong", "wall", "wal", "uong", "thanh"}   # đã bỏ dấu + chữ thường
_WALL_XN_EXCLUDE_DEFAULT = (0.35, 0.65)   # vùng giữa thẻ (tỉ lệ chiều rộng) chứa chữ số xN -> bị mask (đục lỗ 35%-65%)
_WALL_FUZZY_RATIO = 0.80          # độ giống tối thiểu (difflib) với 'tuong' / 'wall'
_WALL_PRICE_PATTERN = re.compile(r"x\s?\d{1,3}\b.*\d[\d.,\s]{5,}")   # 'x12 ... 1.234.567'
_ocr_lang_cache = None

# Các tỉ lệ thử khi khớp nút UI (1.0 trước, rồi lệch nhẹ)
_UI_MATCH_SCALES = (1.0, 1.05, 0.95, 1.1, 0.9)

# --- Quét 2 vùng TRÁI / PHẢI trên từng dòng thẻ Tường (config.json có thể ghi đè các ROI) ---
_WALL_MENU_X_DEFAULT = (0.72, 1.0)       # PANEL menu Búa nằm bên phải màn hình (tỉ lệ chiều rộng ẢNH CHỤP). config.json: WALL_MENU_X_RANGE
_WALL_LEFT_SCALE = 4                     # phóng đại vùng trái trước khi OCR (chữ menu chỉ cao ~8px)
_WALL_TEXT_WHITE_HSV = ((0, 0, 185), (180, 70, 255))   # chữ trắng viền đen của game (V cao, S thấp)
_WALL_LEFT_ROI_DEFAULT = (0.10, 0.55)    # vùng TRÁI (icon + chữ "Tường thành") - tỉ lệ chiều rộng menu
_WALL_RIGHT_ROI_DEFAULT = (0.60, 0.98)   # vùng PHẢI (giá + icon Vàng/Dầu + nút Nâng)
_WALL_ROW_MERGE_PX = 24                  # hai kết quả lệch Y < ngần này px coi là cùng một dòng
_WALL_STRIP_H_DEFAULT = 40               # chiều cao dải ảnh PHẢI cắt tại center_y (px); config.json: WALL_RIGHT_STRIP_H
_WALL_ICON_FILES = (("GOLD", "icon_gold.png"), ("ELIXIR", "icon_elixir.png"))
_WALL_ICON_THRESHOLD = 0.65              # điểm template tối thiểu của icon Vàng/Dầu
_WALL_GOLD_HSV = ((15, 110, 150), (35, 255, 255))     # dự phòng theo màu khi chưa có icon mẫu
_WALL_ELIXIR_HSV = ((140, 70, 120), (170, 255, 255))   # Dầu hồng/tím: Hue 140-170 (thang OpenCV 0-180)
_WALL_ICON_MIN_PX = 25                   # số pixel màu tối thiểu để tin vào nhận diện theo màu
_WALL_RED_MIN_PX = 12                    # pixel chữ ĐỎ tối thiểu để kết luận thiếu tài nguyên
_WALL_RED_RATIO = 0.35                   # đỏ / (đỏ + trắng) >= ngưỡng này -> thiếu tài nguyên
_WALL_WHITE_MIN_PX = 15                  # pixel chữ TRẮNG tối thiểu để coi nút là "sáng"


def set_logger(func):
    """Đặt hàm ghi log (ví dụ ui.log) để cảnh báo hiện lên Log UI."""
    global _log_callback
    _log_callback = func if callable(func) else print


def _log_once(key, message):
    """Ghi log đúng một lần cho mỗi `key`."""
    if key in _logged_keys:
        return
    _logged_keys.add(key)
    try:
        _log_callback(message)
    except Exception:
        print(message)


def _warn_once():
    """Ghi cảnh báo thiếu Tesseract đúng một lần."""
    global _warned
    if _warned:
        return
    _warned = True
    try:
        _log_callback(TESSERACT_ERROR_MSG)
    except Exception:
        print(TESSERACT_ERROR_MSG)


def _setup_tesseract():
    """Kiểm tra tesseract.exe. Trả về True nếu dùng được."""
    # 1) Đường dẫn khai báo trong config.py
    if os.path.exists(TESSERACT_PATH):
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH
        return True
    # 2) Dự phòng: Tesseract đã nằm trong biến môi trường PATH
    found = shutil.which("tesseract")
    if found:
        pytesseract.pytesseract.tesseract_cmd = found
        return True
    # 3) Không có -> cảnh báo
    _warn_once()
    return False


TESSERACT_AVAILABLE = _setup_tesseract()


def asset(name):
    """Đường dẫn tuyệt đối tới file trong thư mục assets."""
    return os.path.join(BASE_DIR, "assets", name)


# ---------------------------------------------------------------------------
# MULTI-TEMPLATE: 1 nút UI có thể có nhiều ảnh mẫu (biến thể) trong assets/
#   button_return_home.png, button_return_home_2.png, button_return_home_3.png ...
# ---------------------------------------------------------------------------
_template_cache = {}        # đường dẫn tuyệt đối -> (mtime, ảnh BGR, ảnh xám)  (tránh đọc đĩa liên tục)
_pattern_cache = {}         # mẫu wildcard -> (thời điểm quét đĩa, [đường dẫn đã sắp xếp])
_last_hit_cache = {}        # bộ đường dẫn -> biến thể khớp gần nhất (được thử trước ở lần sau)
_template_lock = threading.Lock()
_PATTERN_TTL = 5.0          # giây: quét lại thư mục assets/ sau chừng này (ảnh biến thể mới thêm vào tự được nạp)
_SHORT_CIRCUIT_MARGIN = 0.10   # điểm >= threshold + margin -> trả kết quả NGAY, không quét tiếp các biến thể còn lại
_SHORT_CIRCUIT_CAP = 0.99      # trần của ngưỡng "khớp rất cao" (threshold lớn thì margin không vượt quá 0.99)
_WILDCARD_CHARS = "*?["


def _natural_key(path):
    """Khóa sắp xếp tự nhiên: ..._2.png đứng trước ..._10.png; file gốc (không hậu tố) đứng đầu."""
    parts = re.split(r"(\d+)", os.path.basename(path).lower())
    return [int(tok) if i % 2 else tok for i, tok in enumerate(parts)]


def _expand_template_paths(paths_or_pattern):
    """
    Chuẩn hóa đầu vào thành danh sách đường dẫn ảnh mẫu CÓ THẬT trên đĩa (không trùng, giữ thứ tự).

    `paths_or_pattern` có thể là:
      - 1 chuỗi: đường dẫn ảnh, hoặc mẫu wildcard (vd ".../assets/button_return_home*.png");
      - list/tuple các chuỗi như trên (mỗi phần tử là đường dẫn hoặc wildcard).
    Chuỗi chỉ có tên file (không kèm thư mục) được hiểu là nằm trong assets/.
    Kết quả quét wildcard được nhớ _PATTERN_TTL giây để không liệt kê thư mục ở mọi khung hình.
    """
    if paths_or_pattern is None:
        return []
    if isinstance(paths_or_pattern, (str, os.PathLike)):
        items = [paths_or_pattern]
    else:
        items = list(paths_or_pattern)
    now = time.time()
    result, seen = [], set()
    for item in items:
        try:
            p = os.fspath(item)
        except TypeError:
            continue
        if not p:
            continue
        if not os.path.isabs(p) and not os.path.dirname(p):
            p = asset(p)
        folder, base = os.path.split(p)
        if any(ch in base for ch in _WILDCARD_CHARS):
            cached = _pattern_cache.get(p)
            if cached is None or now - cached[0] > _PATTERN_TTL:
                found = sorted(glob.glob(os.path.join(glob.escape(folder), base)), key=_natural_key)
                with _template_lock:
                    _pattern_cache[p] = (now, found)
            else:
                found = cached[1]
        else:
            found = [p] if os.path.isfile(p) else []
        for f in found:
            if f not in seen:
                seen.add(f)
                result.append(f)
    return result


def _load_template_cached(path):
    """
    Nạp ảnh mẫu (Unicode-safe) và nhớ trong _template_cache. Trả (mtime, ảnh BGR, ảnh xám) hoặc None.
    File bị sửa / ghi đè (mtime đổi) sẽ tự được nạp lại; file đã xóa -> None.
    """
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        with _template_lock:
            _template_cache.pop(path, None)
        return None
    cached = _template_cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached
    try:
        color = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    except Exception:
        color = cv2.imread(path)
    if color is None:
        _log_once(f"tpl_unreadable_{path}",
                  f"[⚠️ CẢNH BÁO] Không đọc được ảnh mẫu {os.path.basename(path)} (file hỏng?)")
        return None
    gray = cv2.cvtColor(color, cv2.COLOR_BGR2GRAY) if color.ndim == 3 else color
    entry = (mtime, color, gray)
    with _template_lock:
        _template_cache[path] = entry
    return entry


def clear_template_cache():
    """Xóa toàn bộ cache ảnh mẫu / wildcard (dùng khi cần ép nạp lại ngay, vd sau khi crop_tool vừa lưu)."""
    with _template_lock:
        _template_cache.clear()
        _pattern_cache.clear()
        _last_hit_cache.clear()
    clear_match_cache()


# ---------------------------------------------------------------------------
# FINGERPRINT KHUNG HÌNH + CACHE KẾT QUẢ SO KHỚP ẢNH MẪU
#   Cùng một khung hình (fingerprint không đổi) -> dùng lại kết quả matchTemplate vừa tính,
#   không quét lại. Cache có TTL ngắn + giới hạn dung lượng nên không bao giờ giữ kết quả "cũ" lâu.
# ---------------------------------------------------------------------------
_FP_SIZE = (64, 36)             # ảnh thu nhỏ dùng để băm (rộng x cao)
_MATCH_CACHE_ENABLED = True
_MATCH_CACHE_MAX = 128          # số kết quả tối đa được nhớ (LRU)
_MATCH_CACHE_TTL = 5.0          # giây: quá hạn thì tính lại dù fingerprint không đổi (bằng _PATTERN_TTL; ảnh mẫu sửa tại chỗ tự có hiệu lực sau chừng này)
_match_result_cache = OrderedDict()     # (fingerprint, khóa so khớp) -> (thời điểm, kết quả)
_match_cache_stats = {"hit": 0, "miss": 0}
_match_cache_lock = threading.Lock()
_CACHE_MISS = object()          # phân biệt "chưa có trong cache" với kết quả None (đã quét, không thấy)


def frame_signature(image):
    """
    (fingerprint MD5, ảnh thu nhỏ 64x36) của một khung hình; (None, None) nếu ảnh rỗng / lỗi.
    Băm cả kích thước ảnh gốc để hai khung khác độ phân giải không bị coi là trùng nhau.
    Ảnh thu nhỏ trả kèm để nơi gọi (Watchdog...) so độ lệch mà không phải resize thêm lần nữa.
    """
    if image is None or getattr(image, "size", 0) == 0:
        return None, None
    try:
        thumb = cv2.resize(image, _FP_SIZE, interpolation=cv2.INTER_AREA)
    except cv2.error:
        return None, None
    h = hashlib.md5()
    h.update(f"{image.shape[1]}x{image.shape[0]}x{image.ndim}".encode("ascii"))
    h.update(np.ascontiguousarray(thumb).tobytes())
    return h.hexdigest(), thumb


def compute_frame_fingerprint(image):
    """Mã MD5 (chuỗi hex) của ảnh thu nhỏ 64x36 - dùng làm khóa cache kết quả so khớp. None nếu ảnh rỗng."""
    return frame_signature(image)[0]


def set_match_cache(enabled=True, ttl=None, max_entries=None):
    """Bật/tắt cache kết quả so khớp; tùy chọn đổi TTL (giây) / số mục tối đa."""
    global _MATCH_CACHE_ENABLED, _MATCH_CACHE_TTL, _MATCH_CACHE_MAX
    _MATCH_CACHE_ENABLED = bool(enabled)
    if ttl is not None:
        _MATCH_CACHE_TTL = max(0.0, float(ttl))
    if max_entries is not None:
        _MATCH_CACHE_MAX = max(1, int(max_entries))
    if not enabled:
        clear_match_cache()


def clear_match_cache():
    """Xóa cache kết quả so khớp (không đụng tới cache ảnh mẫu)."""
    with _match_cache_lock:
        _match_result_cache.clear()


def match_cache_stats():
    """{'hit': số lần dùng lại kết quả, 'miss': số lần phải quét thật, 'size': số mục đang nhớ}."""
    with _match_cache_lock:
        return {**_match_cache_stats, "size": len(_match_result_cache)}


def _cache_get(fp, key):
    if fp is None or not _MATCH_CACHE_ENABLED:
        return _CACHE_MISS
    now = time.time()
    k = (fp, key)
    with _match_cache_lock:
        item = _match_result_cache.get(k)
        if item is None or now - item[0] > _MATCH_CACHE_TTL:
            if item is not None:
                del _match_result_cache[k]
            _match_cache_stats["miss"] += 1
            return _CACHE_MISS
        _match_result_cache.move_to_end(k)
        _match_cache_stats["hit"] += 1
        return item[1]


def _cache_put(fp, key, value):
    if fp is None or not _MATCH_CACHE_ENABLED:
        return
    with _match_cache_lock:
        _match_result_cache[(fp, key)] = (time.time(), value)
        _match_result_cache.move_to_end((fp, key))
        while len(_match_result_cache) > _MATCH_CACHE_MAX:
            _match_result_cache.popitem(last=False)


# ---------------------------------------------------------------------------
# BỎ PHIẾU NHIỀU ẢNH (consensus): 1 ảnh khớp pixel là CHƯA ĐỦ để kết luận đó là nút / thẻ cần tìm.
#   Mỗi ảnh biến thể cho 1 "phiếu" tại vị trí nó khớp tốt nhất; các phiếu cách nhau <= TEMPLATE_VOTE_TOL px
#   gộp thành 1 cụm. Cụm thắng phải có >= TEMPLATE_MIN_VOTES ảnh KHÁC NHAU cùng đồng ý (ít hơn -> bị loại).
#   Ngoại lệ: chỉ 1 ảnh khớp nhưng điểm >= TEMPLATE_SOLO_SCORE (gần như trùng pixel) vẫn nhận.
#   Chỉ có 1 ảnh mẫu thì không thể bỏ phiếu -> dùng ngưỡng như cũ (hãy cắt thêm biến thể bằng crop_tool).
# ---------------------------------------------------------------------------
_VOTE_DEFAULTS = {"min_votes": 2, "tol": 14, "solo": 0.95}
_vote_cfg_cache = {"t": 0.0, "v": dict(_VOTE_DEFAULTS)}


def _vote_settings(min_votes=None):
    """(số phiếu tối thiểu, dung sai vị trí px, điểm 'solo') - đọc config.json, nhớ _PATTERN_TTL giây."""
    now = time.time()
    if now - _vote_cfg_cache["t"] > _PATTERN_TTL:
        v = dict(_VOTE_DEFAULTS)
        try:
            cfg = load_json_config()
            v["min_votes"] = max(1, int(cfg.get("TEMPLATE_MIN_VOTES", v["min_votes"])))
            v["tol"] = max(2, int(cfg.get("TEMPLATE_VOTE_TOL", v["tol"])))
            v["solo"] = float(cfg.get("TEMPLATE_SOLO_SCORE", v["solo"]))
        except Exception:
            pass
        _vote_cfg_cache["t"], _vote_cfg_cache["v"] = now, v
    v = _vote_cfg_cache["v"]
    return (max(1, int(min_votes)) if min_votes is not None else v["min_votes"]), v["tol"], v["solo"]


def _cluster_votes(hits, tol):
    """
    Gộp các phiếu (điểm, (x, y), đường_dẫn) cùng một vị trí (|dx|,|dy| <= tol) thành cụm.
    Trả list cụm dict(pos, votes, score, paths) - cụm nhiều phiếu nhất (rồi điểm cao nhất) đứng đầu.
    Mỗi ảnh chỉ được tính 1 phiếu trong 1 cụm.
    """
    clusters = []
    for score, pos, path in sorted(hits, key=lambda h: -h[0]):
        for cl in clusters:
            if abs(pos[0] - cl["pos"][0]) <= tol and abs(pos[1] - cl["pos"][1]) <= tol:
                if path not in cl["paths"]:
                    cl["paths"].append(path)
                    cl["members"].append(pos)
                break
        else:
            clusters.append({"pos": pos, "score": float(score), "paths": [path], "members": [pos]})
    for cl in clusters:
        xs = [m[0] for m in cl["members"]]
        ys = [m[1] for m in cl["members"]]
        cl["pos"] = (int(round(sum(xs) / len(xs))), int(round(sum(ys) / len(ys))))
        cl["votes"] = len(cl["paths"])
    clusters.sort(key=lambda c: (-c["votes"], -c["score"]))
    return clusters


def find_template_any(screen_img, patterns, threshold=0.75, return_details=False, use_cache=True,
                      min_votes=None):
    """
    Tìm 1 nút / thành phần UI bằng NHIỀU ảnh mẫu biến thể dạng wildcard.

    :param patterns: 1 chuỗi hoặc list chuỗi: đường dẫn ảnh, hoặc wildcard như "button_battle*.png",
                     "button_hero*.png", "button_spell*.png", "button_return_home*.png" (khớp cả
                     button_xxx.png, button_xxx_2.png, ...). Chuỗi chỉ có tên file được hiểu là nằm trong assets/.
    :param threshold: điểm khớp tối thiểu (TM_CCOEFF_NORMED, thử BGR rồi Gray, nhiều tỉ lệ).
    :param return_details: True -> trả dict(pos, score, path, variants) thay vì (x, y).
    :param use_cache: dùng cache theo fingerprint khung hình (khung không đổi -> khỏi quét lại).
    :param min_votes: số ảnh biến thể tối thiểu phải KHỚP CÙNG VỊ TRÍ (None -> config.json TEMPLATE_MIN_VOTES, mặc định 2;
                      1 = chế độ cũ "1 ảnh khớp là đủ"). Có N ảnh mẫu thì cần min(min_votes, N) phiếu.
    :return: (x, y) tâm cụm có nhiều ảnh đồng thuận nhất (điểm cao nhất làm hòa); None nếu không đủ phiếu.
             return_details=True -> dict(pos, score, path, variants, votes, required).

    Với 1 ảnh mẫu duy nhất không thể bỏ phiếu -> dùng ngưỡng như cũ. Khi đủ phiếu sớm và điểm rất cao thì dừng quét
    (tránh lag).
    """
    if screen_img is None or getattr(screen_img, "size", 0) == 0:
        return None
    paths = _expand_template_paths(patterns)
    if not paths:
        return None

    votes_cfg, vote_tol, solo_score = _vote_settings(min_votes)
    required = max(1, min(votes_cfg, len(paths)))
    key = (tuple(paths), round(float(threshold), 4), bool(return_details), required)
    fp = compute_frame_fingerprint(screen_img) if use_cache else None
    cached = _cache_get(fp, key)
    if cached is not _CACHE_MISS:
        return cached

    # Biến thể khớp lần trước được thử đầu tiên
    last = _last_hit_cache.get(tuple(paths))
    ordered = [last] + [p for p in paths if p != last] if (last in paths and paths[0] != last) else paths

    if screen_img.ndim == 3:
        screen_color = screen_img
        screen_gray = cv2.cvtColor(screen_img, cv2.COLOR_BGR2GRAY)
    else:
        screen_gray = screen_img
        screen_color = cv2.cvtColor(screen_img, cv2.COLOR_GRAY2BGR)

    strong = min(_SHORT_CIRCUIT_CAP, float(threshold) + _SHORT_CIRCUIT_MARGIN)
    hits = []                       # phiếu: (điểm, (x, y), đường dẫn)
    for path in ordered:
        entry = _load_template_cached(path)
        if entry is None:
            continue
        try:
            hit = ImageProcessor._match_variant(
                screen_color, screen_gray, entry[1], entry[2], threshold)
        except cv2.error as e:
            _log_once(f"tpl_match_err_{path}",
                      f"[⚠️ CẢNH BÁO] Lỗi khớp ảnh mẫu {os.path.basename(path)}: {e}")
            continue
        if hit is None:
            continue
        hits.append((hit[0], hit[1], path))
        # Dừng sớm: đã đủ phiếu đồng thuận ở 1 vị trí VÀ điểm rất cao
        if hit[0] >= strong and _cluster_votes(hits, vote_tol)[0]["votes"] >= required:
            break

    result = None
    if hits:
        top = _cluster_votes(hits, vote_tol)[0]
        if top["votes"] >= required or top["score"] >= solo_score:
            best_path = max((h[0], h[2]) for h in hits if h[2] in top["paths"])[1]
            if len(_last_hit_cache) > 256:
                _last_hit_cache.clear()
            _last_hit_cache[tuple(paths)] = best_path
            result = ({"pos": top["pos"], "score": top["score"], "path": best_path, "variants": len(paths),
                       "votes": top["votes"], "required": required} if return_details else top["pos"])
        else:
            _log_once(f"vote_reject_{os.path.basename(paths[0])}",
                      f"[VOTE] Từ chối {os.path.basename(paths[0])}...: chỉ {top['votes']}/{required} ảnh khớp cùng vị trí "
                      f"(điểm cao nhất {top['score']:.2f} < {solo_score:.2f}) -> coi là KHÔNG phải nút này")
    elif len(paths) < votes_cfg:
        _log_once(f"vote_single_{os.path.basename(paths[0])}",
                  f"[VOTE] {os.path.basename(paths[0])}: chỉ có {len(paths)} ảnh mẫu nên chưa thể bỏ phiếu "
                  f"(cần {votes_cfg}); hãy cắt thêm biến thể _2, _3 bằng crop_tool để nhận diện chắc hơn")
    _cache_put(fp, key, result)
    return result


def _near_wall_row(y, other_y, h):
    """Hai tọa độ Y có thuộc cùng một dòng thẻ không (dung sai = max(24px, nửa chiều cao))."""
    return abs(y - other_y) < max(_WALL_ROW_MERGE_PX, int(h) // 2)


class ImageProcessor:
    @staticmethod
    def find_image_location(screen_img, template_path, threshold=0.75):
        """
        Tìm vị trí ảnh mẫu trên màn hình (Hỗ trợ Unicode Path + Dual BGR/Gray Matching).
        Trả về (x, y) tâm nút hoặc None nếu không khớp.
        """
        if screen_img is None or not os.path.exists(template_path):
            return None

        # Nạp ảnh mẫu Unicode-safe
        try:
            template = cv2.imdecode(np.fromfile(template_path, dtype=np.uint8), cv2.IMREAD_COLOR)
        except Exception:
            template = cv2.imread(template_path)

        if template is None:
            return None

        sh, sw = screen_img.shape[:2]
        th, tw = template.shape[:2]
        if th > sh or tw > sw:
            return None

        gray_screen = cv2.cvtColor(screen_img, cv2.COLOR_BGR2GRAY) if screen_img.ndim == 3 else screen_img
        gray_tpl = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY) if template.ndim == 3 else template

        def _match(scr, tpl):
            res = cv2.matchTemplate(scr, tpl, cv2.TM_CCOEFF_NORMED)
            res = np.nan_to_num(res, nan=-1.0, posinf=-1.0, neginf=-1.0)
            _, val, _, loc = cv2.minMaxLoc(res)
            return val, loc

        # Tỉ lệ 1.0 (ảnh mẫu đúng cỡ) thử trước; sau đó thử lệch nhẹ +-5%/10% để chịu được
        # ảnh mẫu cắt lệch cỡ so với ảnh chụp thực tế (vd điểm khớp chỉ ~0.69 ở tỉ lệ 1.0).
        for scale in _UI_MATCH_SCALES:
            if scale == 1.0:
                tpl_c, tpl_g = template, gray_tpl
            else:
                tpl_c = cv2.resize(template, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA
                                   if scale < 1.0 else cv2.INTER_CUBIC)
                tpl_g = cv2.resize(gray_tpl, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA
                                   if scale < 1.0 else cv2.INTER_CUBIC)
            sth, stw = tpl_c.shape[:2]
            if sth > sh or stw > sw:
                continue
            # 1. So khớp BGR (ảnh màu)
            val, loc = _match(screen_img, tpl_c)
            if val >= threshold:
                return (loc[0] + stw // 2, loc[1] + sth // 2)
            # 2. Dự phòng: Grayscale (khử chấm đỏ thông báo / hiệu ứng đè lên nút)
            val, loc = _match(gray_screen, tpl_g)
            if val >= threshold:
                return (loc[0] + stw // 2, loc[1] + sth // 2)

        return None

    # ------------------------------------------------------------------
    # MULTI-TEMPLATE: tìm 1 nút bằng NHIỀU ảnh mẫu biến thể
    # ------------------------------------------------------------------
    @staticmethod
    def expand_template_paths(paths_or_pattern):
        """Danh sách ảnh mẫu có thật khớp đường dẫn / wildcard / list đường dẫn (xem _expand_template_paths)."""
        return _expand_template_paths(paths_or_pattern)

    @staticmethod
    def load_template(path):
        """Ảnh mẫu BGR lấy từ cache (không đọc đĩa lại nếu file không đổi). None nếu thiếu / hỏng.
        Ảnh trả về dùng chung giữa các lần gọi -> CHỈ ĐỌC, không sửa tại chỗ."""
        entry = _load_template_cached(path)
        return entry[1] if entry else None

    @staticmethod
    def _match_variant(screen_color, screen_gray, tpl_color, tpl_gray, threshold):
        """
        So khớp 1 ảnh mẫu (đúng logic của find_image_location: các tỉ lệ _UI_MATCH_SCALES, mỗi tỉ lệ
        thử BGR trước rồi tới Grayscale). Trả (điểm, (x, y) tâm) ở lần khớp ĐẦU TIÊN >= threshold, ngược lại None.
        """
        sh, sw = screen_color.shape[:2]
        for scale in _UI_MATCH_SCALES:
            if scale == 1.0:
                tpl_c, tpl_g = tpl_color, tpl_gray
            else:
                interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
                tpl_c = cv2.resize(tpl_color, None, fx=scale, fy=scale, interpolation=interp)
                tpl_g = cv2.resize(tpl_gray, None, fx=scale, fy=scale, interpolation=interp)
            sth, stw = tpl_c.shape[:2]
            if sth > sh or stw > sw or sth < 1 or stw < 1:
                continue
            for scr, tpl in ((screen_color, tpl_c), (screen_gray, tpl_g)):
                res = cv2.matchTemplate(scr, tpl, cv2.TM_CCOEFF_NORMED)
                res = np.nan_to_num(res, nan=-1.0, posinf=-1.0, neginf=-1.0)
                _, val, _, loc = cv2.minMaxLoc(res)
                if val >= threshold:
                    return float(val), (loc[0] + stw // 2, loc[1] + sth // 2)
        return None

    @staticmethod
    def find_any_template_location(screen_img, template_paths_or_pattern, threshold=0.75, min_votes=None):
        """
        Tìm 1 nút / thành phần UI bằng NHIỀU ảnh mẫu biến thể. Trả (x, y) tâm nút hoặc None.
        (Tên cũ - giữ để tương thích; nay chạy qua find_template_any: có cache theo fingerprint khung hình.)
        """
        return find_template_any(screen_img, template_paths_or_pattern, threshold, min_votes=min_votes)

    @staticmethod
    def find_template_any(screen_img, patterns, threshold=0.75, return_details=False, use_cache=True,
                          min_votes=None):
        """Xem hàm cấp module find_template_any."""
        return find_template_any(screen_img, patterns, threshold, return_details, use_cache, min_votes)

    @staticmethod
    def _binarize(gray):
        """
        Nhị phân hóa bằng Otsu (không dùng ngưỡng cố định nên ít phụ thuộc độ sáng nền).
        Đảm bảo kết quả là chữ đen trên nền trắng + thêm viền trắng để Tesseract đọc ổn định.
        """
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        # Nền phải chiếm đa số điểm ảnh (trắng); nếu ngược lại thì đảo màu
        if cv2.countNonZero(thresh) < thresh.size // 2:
            thresh = cv2.bitwise_not(thresh)
        return cv2.copyMakeBorder(thresh, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=255)

    @staticmethod
    def _ocr_digits(gray, psm=7, whitelist="0123456789"):
        """OCR ảnh xám (resize 3x -> Otsu). Có thể ném TesseractNotFoundError (hàm gọi bắt)."""
        gray = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        thresh = ImageProcessor._binarize(gray)
        return pytesseract.image_to_string(
            thresh, config=f"--psm {psm} -c tessedit_char_whitelist={whitelist}"
        ).strip()

    # ------------------------------------------------------------------
    # ĐỌC SỐ THỢ XÂY (CÓ CHỐNG MẤT GEM)
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_builder_text(text):
        """
        Bóc số thợ rảnh từ chuỗi OCR và áp dụng quy tắc chống mất Gem.

        Quét TẤT CẢ phân số X/Y trong chuỗi (kể cả khi có Thợ Goblin: "1/6 1/2") rồi chỉ dùng
        phân số ĐẦU TIÊN = thợ chính.
          - Y == 7 : Thợ Goblin đang là thợ chính (tốn Gem) -> trả 0.
          - 1 <= X <= Y : trả Y - X (số thợ rảnh).
          - còn lại : None (nghi đọc sai).

        Trả về int (0 = không có thợ rảnh an toàn) hoặc None nếu đọc lỗi / sai định dạng.
        """
        matches = _BUILDER_PATTERN.findall(text or "")
        if not matches:
            return None
        x_val, y_val = (int(v) for v in matches[0])

        if y_val == 7:
            return 0
        if 1 <= x_val <= y_val:
            return y_val - x_val
        return None

    @staticmethod
    def get_free_builders(screen_img):
        """
        Trả về số thợ rảnh an toàn (int, 0 = hết thợ / có Goblin) hoặc None nếu OCR lỗi.

        Quy trình: cắt vùng -> resize 3x -> xám -> Otsu -> OCR "X/Y" -> áp quy tắc chống mất Gem.
        """
        if screen_img is None:
            return None
        if not TESSERACT_AVAILABLE:
            _warn_once()
            return None
        try:
            y1, y2, x1, x2 = BUILDER_REGION
            crop = screen_img[y1:y2, x1:x2]
            if crop.size == 0:
                return None

            # 1) Tiền xử lý: resize 3x -> xám -> Otsu + viền đệm (xem _binarize)
            crop = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            thresh = ImageProcessor._binarize(gray)

            # 2) OCR: thử psm 7 (một dòng) trước, không hợp lệ thì thử psm 6
            # 3) + 4) Lấy phân số ĐẦU TIÊN rồi áp dụng quy tắc chống mất Gem
            #    (whitelist có dấu cách để giữ ranh giới giữa 2 phân số "1/6 1/2")
            for psm in (7, 6):
                text = pytesseract.image_to_string(
                    thresh,
                    config=f"--psm {psm} -c tessedit_char_whitelist=0123456789/lI| ",
                ).strip()
                result = ImageProcessor._parse_builder_text(text)
                if result is not None:
                    return result
            return None
        except pytesseract.TesseractNotFoundError:
            _warn_once()
            return None
        except Exception as e:
            # Lỗi OCR/cắt ảnh khác: coi như đọc lỗi, không làm sập luồng chính
            _log_once(f"builder_err_{type(e).__name__}",
                      f"[⚠️ CẢNH BÁO] Lỗi khi đọc số thợ xây: {e}")
            return None

    # ------------------------------------------------------------------
    # ĐỌC SỐ TÀI NGUYÊN (ROI lấy từ config.json)
    # ------------------------------------------------------------------
    @staticmethod
    def _digits_to_int(text):
        """Giữ lại các chữ số trong chuỗi OCR và đổi thành int. None nếu không có chữ số."""
        digits = re.sub(r"\D", "", text or "")
        # Rỗng hoặc dài bất thường (> 9 chữ số) -> coi là rác OCR
        if not digits or len(digits) > 9:
            return None
        return int(digits)

    @staticmethod
    def _crop_roi(screen_img, roi):
        """Cắt ảnh theo ROI [x1, y1, x2, y2] (có chặn biên). None nếu vùng rỗng."""
        height, width = screen_img.shape[:2]
        x1, y1, x2, y2 = roi
        x1, x2 = max(0, min(x1, width)), max(0, min(x2, width))
        y1, y2 = max(0, min(y1, height)), max(0, min(y2, height))
        crop = screen_img[y1:y2, x1:x2]
        return crop if crop.size else None

    @staticmethod
    def _read_stacked_numbers(screen_img, roi, expected, minimum):
        """
        Đọc các số xếp chồng theo hàng dọc trong MỘT ROI (ví dụ Vàng / Dầu / Dầu đen).

        Cách 1: OCR cả vùng (psm 6) rồi lấy các dòng có số, theo thứ tự trên -> dưới.
        Cách 2 (dự phòng, khi đọc được < `minimum` dòng): chia đều ROI thành `expected` dải
        ngang và OCR từng dải.

        Mẹo: ROI nên khoanh sát phần chữ số, tránh đè lên biểu tượng Vàng/Dầu vì
        biểu tượng có thể bị OCR thành số thừa.

        Trả về list độ dài `expected`, phần tử không đọc được là None.
        """
        crop = ImageProcessor._crop_roi(screen_img, roi)
        if crop is None:
            return [None] * expected

        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

        text = ImageProcessor._ocr_digits(gray, psm=6)
        numbers = [
            n for n in (ImageProcessor._digits_to_int(line) for line in text.splitlines())
            if n is not None
        ]
        if len(numbers) >= minimum:
            numbers = numbers[:expected]
            return numbers + [None] * (expected - len(numbers))

        # Dự phòng: chia đều theo chiều dọc
        height = gray.shape[0]
        values = []
        for i in range(expected):
            band = gray[i * height // expected:(i + 1) * height // expected]
            if band.size == 0:
                values.append(None)
                continue
            values.append(
                ImageProcessor._digits_to_int(ImageProcessor._ocr_digits(band, psm=7))
            )
        return values

    @staticmethod
    def _read_enemy_roi(screen_img, roi):
        """
        Đọc 3 dòng tài nguyên địch (Vàng / Dầu / Dầu đen) trong RESOURCE_ENEMY_ROI.

        Quy trình: cắt [x1, y1, x2, y2] -> resize 3x (INTER_CUBIC) -> xám
        -> threshold cố định 175 (giữ chữ trắng, loại nền đất/nét mờ)
        -> OCR psm 6 whitelist chữ số -> tách dòng -> int.

        Fallback: nếu đọc được < 2 số (Vàng, Dầu), lưu ảnh đã cắt ra
        assets/debug_enemy_roi.png và in chuỗi OCR thô ra console.

        Trả về [gold, elixir, dark]; mốc nào không đọc được là None.
        Chỉ để TesseractNotFoundError nổi lên cho hàm gọi xử lý (cảnh báo 1 lần).
        """
        result = [None, None, None]
        crop = None
        raw_text = ""
        try:
            crop = ImageProcessor._crop_roi(screen_img, roi)
            if crop is None:
                print("[DEBUG] RESOURCE_ENEMY_ROI nằm ngoài ảnh hoặc rỗng.")
                return result

            # 1) Tiền xử lý
            big = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
            gray = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY)
            _, thresh = cv2.threshold(gray, 175, 255, cv2.THRESH_BINARY)

            # 2) OCR + tách dòng
            raw_text = pytesseract.image_to_string(
                thresh, config="--psm 6 -c tessedit_char_whitelist=0123456789 "
            )
            numbers = []
            for line in raw_text.splitlines():
                value = ImageProcessor._digits_to_int(line.replace(" ", ""))
                if value is not None:
                    numbers.append(value)

            # 3) Gán lần lượt: Vàng -> Dầu -> Dầu đen
            for i, value in enumerate(numbers[:3]):
                result[i] = value

            # 4) Fallback log khi thiếu Vàng hoặc Dầu
            if result[0] is None or result[1] is None:
                ImageProcessor._dump_enemy_debug(crop, raw_text)
            return result
        except pytesseract.TesseractNotFoundError:
            raise
        except Exception as e:
            print(f"[⚠️ CẢNH BÁO] Lỗi khi đọc ROI tài nguyên địch: {e}")
            ImageProcessor._dump_enemy_debug(crop, raw_text)
            return [None, None, None]

    @staticmethod
    def _dump_enemy_debug(crop, raw_text):
        """Lưu ảnh ROI đã cắt + in OCR thô để kiểm tra lỗi. Không bao giờ ném lỗi."""
        try:
            print(f"[DEBUG] OCR thô (ROI địch): {raw_text!r}")
            if crop is not None and getattr(crop, "size", 0):
                path = asset("debug_enemy_roi.png")
                os.makedirs(os.path.dirname(path), exist_ok=True)
                cv2.imwrite(path, crop)
                print(f"[DEBUG] Đã lưu ảnh ROI: {path}")
        except Exception as e:
            print(f"[DEBUG] Không lưu được ảnh debug: {e}")

    @staticmethod
    def get_my_resources(screen_img):
        """
        Đọc (my_gold, my_elixir) ở làng nhà.
        Ưu tiên ROI `RESOURCE_MY_VILLAGE_ROI` trong config.json; chưa có thì dùng
        HOME_RESOURCE_REGIONS trong config.py. Số nào đọc lỗi sẽ là None.
        """
        if screen_img is None:
            return (None, None)
        if not TESSERACT_AVAILABLE:
            _warn_once()
            return (None, None)
        try:
            roi = load_json_config().get("RESOURCE_MY_VILLAGE_ROI")
            if roi:
                gold, elixir = ImageProcessor._read_stacked_numbers(
                    screen_img, roi, expected=2, minimum=2
                )
                return (gold, elixir)

            _log_once(
                "my_roi_missing",
                "ℹ️ Chưa có RESOURCE_MY_VILLAGE_ROI trong config.json -> dùng vùng mặc định "
                "trong config.py. Hãy chạy tools/crop_tool.py để khoanh vùng chính xác.",
            )
            return (
                ImageProcessor.read_number(screen_img, HOME_RESOURCE_REGIONS["gold"]),
                ImageProcessor.read_number(screen_img, HOME_RESOURCE_REGIONS["elixir"]),
            )
        except pytesseract.TesseractNotFoundError:
            _warn_once()
            return (None, None)
        except Exception as e:
            _log_once(f"my_res_err_{type(e).__name__}",
                      f"[⚠️ CẢNH BÁO] Lỗi khi đọc tài nguyên làng nhà: {e}")
            return (None, None)

    @staticmethod
    def get_enemy_resources(screen_img):
        """
        Đọc (enemy_gold, enemy_elixir, enemy_dark) khi đang soi nhà đối thủ.
        Ưu tiên ROI `RESOURCE_ENEMY_ROI` trong config.json; chưa có thì dùng LOOT_REGIONS
        trong config.py. Số nào đọc lỗi (hoặc không hiển thị, như Dầu đen) sẽ là None.
        """
        if screen_img is None:
            return (None, None, None)
        if not TESSERACT_AVAILABLE:
            _warn_once()
            return (None, None, None)
        try:
            roi = load_json_config().get("RESOURCE_ENEMY_ROI")
            if roi:
                gold, elixir, dark = ImageProcessor._read_enemy_roi(screen_img, roi)
                return (gold, elixir, dark)

            _log_once(
                "enemy_roi_missing",
                "ℹ️ Chưa có RESOURCE_ENEMY_ROI trong config.json -> dùng vùng mặc định "
                "trong config.py. Hãy chạy tools/crop_tool.py để khoanh vùng chính xác.",
            )
            return (
                ImageProcessor.read_number(screen_img, LOOT_REGIONS["gold"]),
                ImageProcessor.read_number(screen_img, LOOT_REGIONS["elixir"]),
                ImageProcessor.read_number(screen_img, LOOT_REGIONS["dark"]),
            )
        except pytesseract.TesseractNotFoundError:
            _warn_once()
            return (None, None, None)
        except Exception as e:
            _log_once(f"enemy_res_err_{type(e).__name__}",
                      f"[⚠️ CẢNH BÁO] Lỗi khi đọc tài nguyên làng địch: {e}")
            return (None, None, None)

    # ------------------------------------------------------------------
    # NHẬN DIỆN THẺ TƯỜNG TRONG MENU BÚA (HYBRID: TEMPLATE MATCHING + OCR)
    # ------------------------------------------------------------------
    @staticmethod
    def preprocess_for_coc_font(crop_img, border=10):
        """
        Tiền xử lý ảnh cho OCR chữ COC (chữ trắng, viền đen, bóng đổ).
        Phóng 3x (INTER_CUBIC) -> xám -> CLAHE -> Adaptive Gaussian Threshold -> medianBlur 3x3.

        Chữ COC SÁNG hơn nền cục bộ, viền đen TỐI hơn: dùng THRESH_BINARY_INV với C âm để
        pixel sáng hơn trung bình cục bộ + 10 thành CHỮ ĐEN, còn nền + viền đen thành TRẮNG
        (viền bị hòa vào nền nên "khử" được bóng đổ). Kết quả: chữ đen / nền trắng + viền đệm.
        """
        if crop_img is None or getattr(crop_img, "size", 0) == 0:
            return None
        big = cv2.resize(crop_img, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        gray = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY) if big.ndim == 3 else big
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)
        thresh = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 41, -10)
        thresh = cv2.medianBlur(thresh, 3)
        return cv2.copyMakeBorder(thresh, border, border, border, border,
                                  cv2.BORDER_CONSTANT, value=255)

    @staticmethod
    def _strip_accents(text):
        """'Tường' -> 'tuong' (bỏ dấu tiếng Việt, đ->d, chữ thường)."""
        text = unicodedata.normalize("NFD", (text or "").replace("đ", "d").replace("Đ", "D"))
        return "".join(c for c in text if unicodedata.category(c) != "Mn").lower()

    @staticmethod
    def _is_wall_word(token):
        """Token OCR có giống 'Tường' / 'Wall' không (khớp từ khóa hoặc mờ theo difflib)."""
        token = ImageProcessor._strip_accents(token)
        if len(token) < 3 or len(token) > 7:
            return False
        if token in _WALL_KEYWORDS:
            return True
        return any(difflib.SequenceMatcher(None, token, kw).ratio() >= _WALL_FUZZY_RATIO
                   for kw in ("tuong", "wall", "thanh"))

    @staticmethod
    def _ocr_lang():
        """'vie+eng' nếu Tesseract có gói tiếng Việt, ngược lại 'eng' (đã bỏ dấu nên vẫn khớp)."""
        global _ocr_lang_cache
        if _ocr_lang_cache is None:
            try:
                _ocr_lang_cache = "vie+eng" if "vie" in pytesseract.get_languages(config="") else "eng"
            except Exception:
                _ocr_lang_cache = "eng"
        return _ocr_lang_cache

    @staticmethod
    def _row_text_looks_like_wall(row_img):
        """OCR một dòng menu: có từ khóa Tường/Wall HOẶC cấu trúc 'xN ... giá lớn'."""
        thresh = ImageProcessor.preprocess_for_coc_font(row_img)
        if thresh is None:
            return False
        text = pytesseract.image_to_string(
            thresh, lang=ImageProcessor._ocr_lang(), config="--psm 6").strip()
        norm = ImageProcessor._strip_accents(text)
        if any(ImageProcessor._is_wall_word(t) for t in re.findall(r"[a-z0-9]+", norm)):
            return True
        return bool(_WALL_PRICE_PATTERN.search(norm.replace("\n", " ")))

    # ------------------------------------------------------------------
    # MASK LOẠI TRỪ CHỮ SỐ "xN" TRÊN THẺ TƯỜNG
    # ------------------------------------------------------------------
    @staticmethod
    def _wall_xn_exclude_ratio(override=None):
        """
        [bắt đầu, kết thúc] (tỉ lệ 0..1 theo chiều rộng thẻ) của vùng chữ số xN bị loại trừ.
        Ưu tiên `override`, sau đó config.json (WALL_CARD_XN_EXCLUDE_RATIO), cuối cùng mặc định [0.35, 0.65].
        """
        value = override
        if value is None:
            try:
                value = load_json_config().get("WALL_CARD_XN_EXCLUDE_RATIO")
            except Exception:
                value = None
        try:
            a, b = (float(v) for v in value)
        except (TypeError, ValueError):
            return _WALL_XN_EXCLUDE_DEFAULT
        a, b = max(0.0, min(1.0, a)), max(0.0, min(1.0, b))
        if a > b:
            a, b = b, a
        return (a, b) if b - a >= 0.01 else _WALL_XN_EXCLUDE_DEFAULT

    @staticmethod
    def _wall_card_mask(tpl_shape, ratio):
        """
        Mặt nạ uint8 cùng kích thước ảnh mẫu (3 kênh cho khớp ảnh BGR):
        TRẮNG (255) = vùng được so khớp (bên trái + bên phải), ĐEN (0) = vùng giữa chứa "xN" bị bỏ qua.
        Trả (mask, (x_bắt_đầu, x_kết_thúc) theo pixel).
        """
        h, w = tpl_shape[:2]
        x0 = int(round(w * ratio[0]))
        x1 = int(round(w * ratio[1]))
        x0, x1 = max(0, min(w, x0)), max(0, min(w, x1))
        mask = np.full((h, w), 255, dtype=np.uint8)
        mask[:, x0:x1] = 0
        return cv2.merge([mask, mask, mask]) if tpl_shape[2:] else mask, (x0, x1)

    @staticmethod
    def _masked_match(menu_img, tpl, ratio):
        """
        Khớp ảnh mẫu thẻ Tường BỎ QUA vùng xN (Masked Template Matching):
            cv2.matchTemplate(menu_img, tpl, cv2.TM_CCOEFF_NORMED, mask=mask)
        Bản OpenCV không hỗ trợ mask cho TM_CCOEFF_NORMED -> dự phòng: khớp riêng dải TRÁI và dải PHẢI
        rồi lấy trung bình có trọng số theo chiều rộng (kết quả tương đương, cùng thang điểm).
        Trả ma trận điểm (đã khử NaN/inf) cùng kích thước cv2.matchTemplate(menu_img, tpl, ...).
        """
        mask, (x0, x1) = ImageProcessor._wall_card_mask(tpl.shape, ratio)
        try:
            res = cv2.matchTemplate(menu_img, tpl, cv2.TM_CCOEFF_NORMED, mask=mask)
        except cv2.error:
            _log_once("wall_mask_fallback",
                      "[⚠️ CẢNH BÁO] OpenCV bản này không hỗ trợ mask cho TM_CCOEFF_NORMED "
                      "-> dùng khớp 2 dải trái/phải thay thế (nên nâng cấp opencv-python)")
            h, w = tpl.shape[:2]
            parts = []   # (điểm, trọng số)
            out_w = menu_img.shape[1] - w + 1
            if x0 >= 8:                                   # dải TRÁI [0, x0)
                r = cv2.matchTemplate(menu_img, tpl[:, :x0], cv2.TM_CCOEFF_NORMED)
                parts.append((r[:, :out_w], x0))
            if w - x1 >= 8:                               # dải PHẢI [x1, w)
                r = cv2.matchTemplate(menu_img, tpl[:, x1:], cv2.TM_CCOEFF_NORMED)
                parts.append((r[:, x1:x1 + out_w], w - x1))
            if not parts:
                res = cv2.matchTemplate(menu_img, tpl, cv2.TM_CCOEFF_NORMED)
            else:
                total = float(sum(wt for _, wt in parts))
                res = sum(np.nan_to_num(r, nan=-1.0, posinf=-1.0, neginf=-1.0) * (wt / total)
                          for r, wt in parts)
        res = np.nan_to_num(res, nan=-1.0, posinf=-1.0, neginf=-1.0)
        return np.clip(res, -1.0, 1.0)

    @staticmethod
    def _wall_template_candidates(menu_img, min_score, xn_ratio=None):
        """
        Quét card_wall.png / icon_wall.png (matchTemplate TM_CCOEFF_NORMED), mỗi ảnh lấy tối đa
        4 ứng viên có điểm >= min_score. Trả (list ứng viên, có_ảnh_mẫu_không).
        Ứng viên: dict(x, y, score, h, kind). Gộp ứng viên cùng một dòng (lệch Y < nửa chiều cao).

        Ảnh thẻ nguyên dòng (card_wall.png) được khớp BẰNG MASK: vùng giữa (WALL_CARD_XN_EXCLUDE_RATIO)
        chứa chữ số xN (x152, x150...) luôn biến động nên bị đục lỗ, chỉ so khớp phần trái + phải.
        icon_wall.png (chỉ có icon, không có chữ xN) vẫn khớp bình thường.
        """
        xn_ratio = ImageProcessor._wall_xn_exclude_ratio(xn_ratio)
        sh, sw = menu_img.shape[:2]
        found, have_template = [], False
        for filename, kind in _WALL_TEMPLATES:
            path = asset(filename)
            if not os.path.isfile(path):
                continue
            tpl = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
            if tpl is None:
                continue
            have_template = True
            h, w = tpl.shape[:2]
            if h > sh or w > sw:
                continue
            if kind == "card":
                res = ImageProcessor._masked_match(menu_img, tpl, xn_ratio)
            else:
                res = cv2.matchTemplate(menu_img, tpl, cv2.TM_CCOEFF_NORMED)
                res = np.nan_to_num(res, nan=-1.0, posinf=-1.0, neginf=-1.0)
            for _ in range(4):
                _, score, _, (lx, ly) = cv2.minMaxLoc(res)
                if score < min_score:
                    break
                res[max(0, ly - h // 2):ly + h // 2 + 1, max(0, lx - w // 2):lx + w // 2 + 1] = -1.0
                found.append({"x": lx + w // 2, "y": ly + h // 2, "score": float(score),
                              "h": h, "kind": kind})
        found.sort(key=lambda c: -c["score"])
        merged = []
        for c in found:
            if all(abs(c["y"] - m["y"]) >= max(c["h"], m["h"]) // 2 for m in merged):
                merged.append(c)
        return merged, have_template

    @staticmethod
    def _wall_item_variant_candidates(menu_img, min_score, xn_ratio=None, best_out=None, vote_score=None):
        """
        Quét MỌI biến thể thẻ Tường nguyên dòng theo wildcard _WALL_ITEM_PATTERNS
        (wall_item_lvA_gold*.png, wall_item_lvA_elixir*.png, wall_item_lvB_gold*.png, wall_item_lvB_elixir*.png,
        gồm cả _2.png, _3.png...). Mỗi file khớp bằng MASK 8-bit bỏ qua 35%-65% chiều rộng ở giữa (số lượng xN
        đổi sau mỗi lần nâng). Mỗi dòng chỉ giữ biến thể có ĐIỂM CAO NHẤT >= min_score.
        Trả list dict(x, y, score, h, kind='item', resource_hint, path) - tọa độ theo `menu_img`.
        """
        xn_ratio = ImageProcessor._wall_xn_exclude_ratio(xn_ratio)
        sh, sw = menu_img.shape[:2]
        found = []
        for pattern, hint in _WALL_ITEM_PATTERNS:
            for path in _expand_template_paths(asset(pattern)):
                entry = _load_template_cached(path)
                if entry is None:
                    continue
                tpl = entry[1]
                h, w = tpl.shape[:2]
                if h > sh or w > sw:
                    continue
                try:
                    res = ImageProcessor._masked_match(menu_img, tpl, xn_ratio)
                except cv2.error as e:
                    _log_once(f"wall_item_err_{path}", f"[⚠️ CẢNH BÁO] Lỗi khớp {os.path.basename(path)}: {e}")
                    continue
                for i in range(4):
                    _, score, _, (lx, ly) = cv2.minMaxLoc(res)
                    if i == 0 and best_out is not None and float(score) > best_out.get("max_val", -1.0):
                        best_out["max_val"] = float(score)
                        best_out["kind"] = os.path.basename(path)
                    if score < min_score:
                        break
                    res[max(0, ly - h // 2):ly + h // 2 + 1, max(0, lx - w // 2):lx + w // 2 + 1] = -1.0
                    found.append({"x": lx + w // 2, "y": ly + h // 2, "score": float(score), "h": h,
                                  "kind": "item", "resource_hint": hint, "path": path})
        found.sort(key=lambda c: -c["score"])
        n_files = len({path for pattern, _h in _WALL_ITEM_PATTERNS for path in _expand_template_paths(asset(pattern))})
        vote_score = min_score if vote_score is None else vote_score
        merged = []      # cùng một dòng (lệch Y < nửa chiều cao): giữ biến thể điểm cao nhất
        for c in found:
            if all(abs(c["y"] - m["y"]) >= max(c["h"], m["h"]) // 2 for m in merged):
                merged.append(c)
        for m in merged:   # BỎ PHIẾU: bao nhiêu ảnh biến thể KHÁC NHAU cùng khớp dòng này (cùng vị trí)
            m["votes"] = len({f["path"] for f in found
                              if f["score"] >= vote_score and abs(f["y"] - m["y"]) < max(f["h"], m["h"]) // 2
                              and abs(f["x"] - m["x"]) <= 14})
            m["n_files"] = n_files
        return merged

    @staticmethod
    def _wall_identity_scores(menu_img, y, h, xn_ratio=None):
        """
        Điểm NHẬN DẠNG "đây là Tường" tại dòng y, CHỈ dùng phần TRÁI (icon + chữ 'Tường thành', trước vùng xN):
        mỗi ảnh mẫu (icon_wall / text_wall / card_wall / wall_item_lvA|B_gold|elixir*, gồm cả _2, _3...) cho 1 điểm
        cao nhất trong dải quanh y. Phần PHẢI (giá, icon Vàng/Dầu) KHÔNG tham gia -> công trình khác cùng giá không
        mượn điểm được. Trả {đường dẫn: điểm}.
        """
        xn_ratio = ImageProcessor._wall_xn_exclude_ratio(xn_ratio)
        sh, sw = menu_img.shape[:2]
        files = []
        for filename, kind in _WALL_TEMPLATES:
            path = asset(filename)
            if os.path.isfile(path):
                files.append((path, kind))
        for pattern, _hint in _WALL_ITEM_PATTERNS:
            files.extend((path, "item") for path in _expand_template_paths(asset(pattern)))
        pad = max(_WALL_ROW_MERGE_PX, int(h) // 2) + 6
        scores = {}
        for path, kind in files:
            entry = _load_template_cached(path)
            if entry is None:
                continue
            tpl = entry[1]
            if kind in ("card", "item"):
                x0 = int(round(tpl.shape[1] * xn_ratio[0]))
                if x0 >= 8:
                    tpl = tpl[:, :x0]          # bỏ xN và toàn bộ phần phải (giá)
            th, tw = tpl.shape[:2]
            y1, y2 = max(0, int(y) - th // 2 - pad), min(sh, int(y) + th // 2 + pad)
            band = menu_img[y1:y2, :]
            if band.shape[0] < th or band.shape[1] < tw:
                continue
            res = np.nan_to_num(cv2.matchTemplate(band, tpl, cv2.TM_CCOEFF_NORMED), nan=-1.0, posinf=-1.0, neginf=-1.0)
            scores[path] = float(np.clip(res.max(), -1.0, 1.0))
        return scores

    @staticmethod
    def _ocr_scan_wall_words(menu_img):
        """OCR toàn menu (image_to_data), trả list (x, y) của các từ giống 'Tường'/'Wall' (tọa độ gốc)."""
        thresh = ImageProcessor.preprocess_for_coc_font(menu_img)
        if thresh is None:
            return []
        data = pytesseract.image_to_data(
            thresh, lang=ImageProcessor._ocr_lang(), config="--psm 11",
            output_type=pytesseract.Output.DICT)
        hits = []
        for i, word in enumerate(data.get("text", [])):
            if word and ImageProcessor._is_wall_word(word):
                cx = data["left"][i] + data["width"][i] / 2
                cy = data["top"][i] + data["height"][i] / 2
                hits.append((int((cx - 10) / 3), int((cy - 10) / 3)))
        return sorted(hits, key=lambda p: p[1])

    # ------------------------------------------------------------------
    # QUÉT 2 VÙNG ĐỘC LẬP (TRÁI / PHẢI) TRÊN TỪNG DÒNG THẺ TƯỜNG
    # ------------------------------------------------------------------
    @staticmethod
    def _wall_roi_ratio(key, default):
        """(bắt đầu, kết thúc) tỉ lệ chiều rộng của ROI; đọc config.json[key], sai/thiếu -> default."""
        try:
            a, b = (float(v) for v in load_json_config().get(key))
            a, b = max(0.0, min(1.0, a)), max(0.0, min(1.0, b))
            if a > b:
                a, b = b, a
            if b - a >= 0.05:
                return (a, b)
        except Exception:
            pass
        return default

    @staticmethod
    def _preprocess_wall_left(roi, border=10, mode="white", scale=None):
        """
        Tiền xử lý vùng TRÁI cho Tesseract -> chữ ĐEN / nền TRẮNG (+ viền đệm).
          mode="white": phóng `scale`x rồi tách CHỮ TRẮNG (HSV: V cao, S thấp) - chữ game trắng viền đen
                        trên nền nâu nên cách này đọc ổn định hơn Otsu toàn vùng.
          mode="otsu" : xám -> phóng -> Otsu (dự phòng).
        """
        if roi is None or getattr(roi, "size", 0) == 0:
            return None
        scale = scale or _WALL_LEFT_SCALE
        if roi.ndim == 2:
            roi = cv2.cvtColor(roi, cv2.COLOR_GRAY2BGR)
        big = cv2.resize(roi, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        if mode == "white":
            hsv = cv2.cvtColor(big, cv2.COLOR_BGR2HSV)
            mask = cv2.inRange(hsv, np.array(_WALL_TEXT_WHITE_HSV[0]), np.array(_WALL_TEXT_WHITE_HSV[1]))
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((2, 2), np.uint8))
            th = cv2.bitwise_not(mask)
        else:
            gray = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY)
            _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            if float(np.mean(th)) < 127:
                th = cv2.bitwise_not(th)
        return cv2.copyMakeBorder(th, border, border, border, border,
                                  cv2.BORDER_CONSTANT, value=255)

    @staticmethod
    def _ocr_wall_rows_left(left_roi):
        """OCR vùng TRÁI, trả list (cx, cy, h) (tọa độ trong ROI) của từ giống 'Tường'/'Thành'/'Wall'.
        Thử mặt nạ chữ trắng trước, không ra thì thử Otsu."""
        border, scale = 10, _WALL_LEFT_SCALE
        for mode in ("white", "otsu"):
            th = ImageProcessor._preprocess_wall_left(left_roi, border, mode, scale)
            if th is None:
                return []
            data = pytesseract.image_to_data(
                th, lang=ImageProcessor._ocr_lang(), config="--psm 11",
                output_type=pytesseract.Output.DICT)
            hits = []
            for i, word in enumerate(data.get("text", [])):
                if word and ImageProcessor._is_wall_word(word):
                    h = float(data["height"][i])
                    cx = (data["left"][i] + data["width"][i] / 2.0 - border) / scale
                    cy = (data["top"][i] + h / 2.0 - border) / scale
                    hits.append((int(cx), int(cy), int(h / scale)))
            if hits:
                return sorted(hits, key=lambda t: t[1])
        return []

    @staticmethod
    def _wall_left_template_candidates(left_roi, x_off, min_score, xn_ratio=None, best_out=None):
        """
        Template matching CHỈ trong vùng TRÁI: icon_wall.png, và phần TRÁI (icon + chữ Tường) của
        card_wall.png (phần giữa chứa xN bị cắt bỏ). Mỗi ảnh tối đa 4 ứng viên >= min_score.
        Trả list dict(x, y, score, h, kind) - x, y theo tọa độ ảnh menu gốc.
        """
        xn_ratio = ImageProcessor._wall_xn_exclude_ratio(xn_ratio)
        lh, lw = left_roi.shape[:2]
        found = []
        for filename, kind in _WALL_TEMPLATES:
            path = asset(filename)
            if not os.path.isfile(path):
                continue
            tpl = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
            if tpl is None:
                continue
            if kind == "card":
                x0 = int(round(tpl.shape[1] * xn_ratio[0]))
                if x0 >= 8:
                    tpl = tpl[:, :x0]
            h, w = tpl.shape[:2]
            if h > lh or w > lw:
                continue
            res = np.nan_to_num(cv2.matchTemplate(left_roi, tpl, cv2.TM_CCOEFF_NORMED),
                                nan=-1.0, posinf=-1.0, neginf=-1.0)
            for i in range(4):
                _, score, _, (lx, ly) = cv2.minMaxLoc(res)
                if i == 0 and best_out is not None and float(score) > best_out.get("max_val", -1.0):
                    best_out["max_val"] = float(score)      # max_val thực tế (kể cả khi < ngưỡng)
                    best_out["kind"] = kind
                if score < min_score:
                    break
                res[max(0, ly - h // 2):ly + h // 2 + 1, max(0, lx - w // 2):lx + w // 2 + 1] = -1.0
                found.append({"x": x_off + lx + w // 2, "y": ly + h // 2,
                              "score": float(score), "h": h, "kind": kind})
        return found

    @staticmethod
    def _dedupe_wall_rows(rows):
        """Gộp các ứng viên cùng một dòng (lệch Y < _WALL_ROW_MERGE_PX); giữ ứng viên điểm cao / template trước."""
        rows = sorted(rows, key=lambda c: (c["method"] != "template", -(c["score"] or 0.0)))
        merged = []
        for c in rows:
            # dung sai = max(24px, nửa chiều cao ứng viên): chữ OCR nằm lệch tâm thẻ template vẫn tính cùng dòng
            if all(abs(c["y"] - m["y"]) >= max(_WALL_ROW_MERGE_PX, max(c.get("h", 0), m.get("h", 0)) // 2)
                   for m in merged):
                merged.append(c)
        return merged

    @staticmethod
    def _detect_wall_icon(band):
        """Khớp icon_gold.png / icon_elixir.png (đa tỉ lệ) trong dải PHẢI. Trả (loại|None, điểm, (x, y, w, h)|None)."""
        try:
            thr = float(load_json_config().get("WALL_ICON_THRESHOLD", _WALL_ICON_THRESHOLD))
        except (TypeError, ValueError, Exception):
            thr = _WALL_ICON_THRESHOLD
        bh, bw = band.shape[:2]
        best = (None, 0.0, None)
        for rtype, filename in _WALL_ICON_FILES:
            path = asset(filename)
            if not os.path.isfile(path):
                continue
            tpl = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
            if tpl is None:
                continue
            for scale in _UI_MATCH_SCALES:
                t = tpl if scale == 1.0 else cv2.resize(tpl, None, fx=scale, fy=scale,
                                                        interpolation=cv2.INTER_AREA)
                th, tw = t.shape[:2]
                if th > bh or tw > bw:
                    continue
                res = np.nan_to_num(cv2.matchTemplate(band, t, cv2.TM_CCOEFF_NORMED),
                                    nan=-1.0, posinf=-1.0, neginf=-1.0)
                _, score, _, (lx, ly) = cv2.minMaxLoc(res)
                if score > best[1]:
                    best = (rtype, float(score), (lx, ly, tw, th))
        return best if best[1] >= thr else (None, best[1], None)

    @staticmethod
    def _resource_by_color(hsv):
        """Dự phòng khi chưa có icon mẫu: đếm pixel vàng / hồng-tím trong dải PHẢI. 'GOLD' | 'ELIXIR' | None."""
        gold = cv2.countNonZero(cv2.inRange(hsv, np.array(_WALL_GOLD_HSV[0]), np.array(_WALL_GOLD_HSV[1])))
        elix = cv2.countNonZero(cv2.inRange(hsv, np.array(_WALL_ELIXIR_HSV[0]), np.array(_WALL_ELIXIR_HSV[1])))
        if max(gold, elix) < _WALL_ICON_MIN_PX:
            return None
        if gold >= 1.5 * elix:
            return "GOLD"
        if elix >= 1.5 * gold:
            return "ELIXIR"
        return None

    @staticmethod
    def _analyze_wall_right(menu_img, y, row_h=0, hint=None):
        """
        BƯỚC 2 - Phân tích vùng PHẢI tại độ cao y của dòng Tường:
          * loại tài nguyên: icon_gold.png / icon_elixir.png (dự phòng: màu icon) -> GOLD | ELIXIR | UNKNOWN
          * nút sáng hay thiếu tài nguyên: chữ giá TRẮNG = đủ, chữ ĐỎ / không có chữ sáng = thiếu
        Trả dict: resource_type, can_upgrade, status, click_pos, debug.
        """
        sh, sw = menu_img.shape[:2]
        r0, r1 = ImageProcessor._wall_roi_ratio("WALL_RIGHT_ROI", _WALL_RIGHT_ROI_DEFAULT)
        rx0, rx1 = int(sw * r0), int(sw * r1)
        # Dải cắt cố định ~40px quanh center_y (không phụ thuộc chiều cao thẻ để không lẫn dòng kế bên).
        # `row_h` giữ để tương thích chữ ký cũ, không còn dùng.
        try:
            strip_h = int(load_json_config().get("WALL_RIGHT_STRIP_H", _WALL_STRIP_H_DEFAULT))
        except Exception:
            strip_h = _WALL_STRIP_H_DEFAULT
        half = max(strip_h, 16) // 2
        y1, y2 = max(0, int(y) - half), min(sh, int(y) + half)
        out = {"resource_type": "UNKNOWN", "can_upgrade": False,
               "status": "INSUFFICIENT_RESOURCES",
               "click_pos": (rx0 + (rx1 - rx0) // 2, int(y)), "debug": {},
               "icon_found": False, "price_px": 0}
        band = menu_img[y1:y2, rx0:rx1]
        if band.shape[0] < 8 or band.shape[1] < 8:
            return out

        rtype, icon_score, icon_box = ImageProcessor._detect_wall_icon(band)
        hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
        source = "icon"
        if rtype is None:
            rtype, source = ImageProcessor._resource_by_color(hsv), "color"

        # Phân tích màu chữ giá (trắng = đủ, đỏ = thiếu); icon bị loại khỏi phép đo
        price = analyze_price_color(band, exclude_box=icon_box)
        red_px, white_px, red_ratio = price["red_px"], price["white_px"], price["red_ratio"]
        bright, cluster = price["can_upgrade"], price["cluster"]

        cols = np.where(cluster.any(axis=0))[0]
        if len(cols) >= 5:   # tâm cụm (giá + icon) = tâm nút bấm
            cx = rx0 + int((np.percentile(cols, 5) + np.percentile(cols, 95)) // 2)
            out["click_pos"] = (cx, int(y))

        if rtype is None and hint in ("GOLD", "ELIXIR"):   # không thấy icon: dùng loại tài nguyên của ảnh mẫu khớp
            rtype, source = hint, "template_hint"
        out["icon_src"] = source
        out["icon_found"] = source in ("icon", "color") and rtype in ("GOLD", "ELIXIR")   # thấy icon THẬT (không tính gợi ý)
        out["price_px"] = int(red_px + white_px)                                           # có chữ giá (trắng/đỏ) hay không
        out["resource_type"] = rtype or "UNKNOWN"
        out["can_upgrade"] = bool(bright and rtype in ("GOLD", "ELIXIR"))
        out["status"] = "CAN_UPGRADE" if out["can_upgrade"] else "INSUFFICIENT_RESOURCES"
        out["debug"] = {"icon_score": round(icon_score, 2), "resource_source": source,
                        "red_px": red_px, "white_px": white_px, "red_ratio": round(red_ratio, 2)}
        return out

    @staticmethod
    def find_wall_cards_in_menu(menu_img, threshold=_WALL_MATCH_THRESHOLD_DEFAULT, ocr_full_scan=False, xn_exclude_ratio=None,
                                require_keyword=None):
        """
        Nhận ẢNH CHỤP (cả màn hình hoặc chỉ menu), tự cắt PANEL menu Búa ở bên phải
        (WALL_MENU_X_RANGE trong config.json, mặc định 72%-100% chiều rộng) rồi quét trong panel;
        các ROI trái/phải bên dưới tính theo chiều rộng PANEL. Tọa độ trả về là tọa độ ảnh chụp gốc.
        Đặt WALL_MENU_X_RANGE = [0, 1] nếu truyền vào ảnh đã cắt sẵn chỉ gồm menu.
        Chi tiết thuật toán: xem _find_wall_cards_in_panel.
        """
        if menu_img is None or getattr(menu_img, "size", 0) == 0:
            return []
        sw = menu_img.shape[1]
        m0, m1 = ImageProcessor._wall_roi_ratio("WALL_MENU_X_RANGE", _WALL_MENU_X_DEFAULT)
        ox = int(sw * m0)
        panel = menu_img[:, ox:int(sw * m1)]
        cards = ImageProcessor._find_wall_cards_in_panel(panel, threshold, ocr_full_scan, xn_exclude_ratio,
                                                         require_keyword)
        for c in cards:
            c["x"] = c["x"] + ox
            cx, cy = c["click_pos"]
            c["click_pos"] = (cx + ox, cy)
        return cards

    @staticmethod
    def _find_wall_cards_in_panel(menu_img, threshold=_WALL_MATCH_THRESHOLD_DEFAULT, ocr_full_scan=False, xn_exclude_ratio=None,
                                  require_keyword=None):
        """
        Quét 2 VÙNG độc lập trên từng dòng thẻ Tường của menu Búa. Trả list dict:
            {"y": center_y,
             "resource_type": "GOLD" | "ELIXIR" | "UNKNOWN",
             "can_upgrade": True | False,
             "click_pos": (click_x, center_y),          # tâm nút nâng bên PHẢI
             "status": "CAN_UPGRADE" | "INSUFFICIENT_RESOURCES",
             # giữ để tương thích code cũ:
             "x", "method": "template" | "ocr", "score", "kind", "h", "debug"}
        Sắp xếp: thẻ nâng được trước, rồi template trước OCR, rồi điểm cao.

        BƯỚC 1 - vùng TRÁI (WALL_LEFT_ROI, mặc định 10%-55% chiều rộng): template matching (icon_wall /
                 text_wall / phần trái card_wall; điểm >= threshold = WALL_MATCH_THRESHOLD) + OCR
                 (xám -> 2x -> Otsu) tìm 'Tường thành' / 'Tường' / 'Tuong'; gộp hai nguồn theo Y.
                 Điểm template trong [0.50, threshold) chỉ được nhận khi OCR xác nhận cùng dòng. -> Y trung tâm.
        BƯỚC 2 - vùng PHẢI (WALL_RIGHT_ROI, mặc định 60%-98%) tại cùng Y, dải cao ~40px: _analyze_wall_right().
                 Icon: template icon_gold/icon_elixir (>= 0.65), dự phòng HSV (Dầu Hue 140-170, Vàng 15-35).
                 Chữ giá TRẮNG = đủ (can_upgrade=True), ĐỎ = thiếu (False).
        `ocr_full_scan` được giữ để tương thích, không còn tác dụng (OCR chỉ chạy trên vùng TRÁI).
        """
        explicit_require = require_keyword
        if menu_img is None or getattr(menu_img, "size", 0) == 0:
            return []
        # threshold: None -> đọc WALL_MATCH_THRESHOLD trong config.json (mặc định 0.75)
        if threshold is None:
            try:
                threshold = float(load_json_config().get("WALL_MATCH_THRESHOLD", _WALL_MATCH_THRESHOLD_DEFAULT))
            except Exception:
                threshold = _WALL_MATCH_THRESHOLD_DEFAULT
        threshold = max(0.45, min(0.95, float(threshold)))
        ambiguous_min = min(_WALL_AMBIGUOUS_MIN, threshold)
        sh, sw = menu_img.shape[:2]
        l0, l1 = ImageProcessor._wall_roi_ratio("WALL_LEFT_ROI", _WALL_LEFT_ROI_DEFAULT)
        lx0, lx1 = int(sw * l0), int(sw * l1)
        left = menu_img[:, lx0:lx1]
        if left.size == 0:
            return []

        # ---- BƯỚC 1: vùng TRÁI -> các dòng Tường + Y ----
        best_info = {"max_val": -1.0, "kind": "-"}
        try:
            tcands = ImageProcessor._wall_left_template_candidates(
                left, lx0, ambiguous_min, xn_exclude_ratio, best_out=best_info)
        except Exception as e:
            _log_once(f"wall_tpl_err_{type(e).__name__}", f"[⚠️ CẢNH BÁO] Lỗi khớp ảnh mẫu thẻ Tường: {e}")
            tcands = []
        # Thẻ nguyên dòng đa biến thể (wall_item_lv*_*.png*) + mask xN: điểm cao nhất mỗi dòng, phải >= threshold
        try:
            icands = ImageProcessor._wall_item_variant_candidates(
                menu_img, ambiguous_min, xn_exclude_ratio, best_out=best_info,
                vote_score=max(ambiguous_min, threshold - 0.05))
        except Exception as e:
            _log_once(f"wall_item_scan_err_{type(e).__name__}", f"[⚠️ CẢNH BÁO] Lỗi quét biến thể thẻ Tường: {e}")
            icands = []
        tcands = tcands + icands
        # Log debug điểm khớp thực tế (kể cả điểm âm / dưới ngưỡng) để chẩn đoán độ nhạy
        _log_callback(f"[DEBUG WALL] Score khớp cao nhất: {best_info['max_val']:.3f} "
                      f"({best_info['kind']}) | Threshold cấu hình: {threshold}")
        # Khám phá ứng viên thoải mái (>= ambiguous_min); QUYẾT ĐỊNH cuối cùng bằng điểm tổng có trọng số ở BƯỚC 2
        rows = [{**c, "method": "template"} for c in tcands if c["score"] >= ambiguous_min]
        ambiguous = []

        # OCR "Tường thành"/"Tường"/"Tuong" ở vùng TRÁI. Mặc định LUÔN chạy (config.json: WALL_OCR_ALWAYS)
        # để bắt đủ từng thẻ kể cả khi template chỉ khớp được một vài dòng; kết quả gộp với template ở
        # _dedupe_wall_rows (template được ưu tiên). Tắt WALL_OCR_ALWAYS -> OCR chỉ chạy khi template bó tay.
        try:
            ocr_always = bool(load_json_config().get("WALL_OCR_ALWAYS", True))
        except Exception:
            ocr_always = True
        # SIẾT CHẶT (WALL_REQUIRE_KEYWORD, mặc định BẬT): mọi dòng ứng viên PHẢI có chữ
        # "Tường"/"Wall"/"Thành" ở dải TRÁI (OCR xác nhận cùng dòng). Template khớp mà không có chữ
        # -> bị loại, tránh nhận nhầm Mỏ vàng / Trạm chế tạo / Bơm khí độc... (nhất là ở các lần quét lặp).
        # Không có Tesseract hoặc OCR lỗi -> không lọc được, giữ nguyên kết quả template.
        if explicit_require is not None:
            require_keyword = bool(explicit_require)
        else:
            try:
                require_keyword = bool(load_json_config().get("WALL_REQUIRE_KEYWORD", False))   # chữ Tường nay là 1 BẰNG CHỨNG (xem WALL_MIN_EVIDENCE)
            except Exception:
                require_keyword = False
        ocr_ok = False
        hits = []
        if TESSERACT_AVAILABLE and (ocr_always or require_keyword or not rows or ambiguous):
            try:
                hits = ImageProcessor._ocr_wall_rows_left(left)
                ocr_ok = True
                for c in ambiguous:   # nghi vấn -> OCR xác nhận cùng dòng
                    if any(abs(c["y"] - hy) < max(_WALL_ROW_MERGE_PX, c["h"] // 2) for _, hy, _ in hits):
                        rows.append({**c, "method": "ocr", "kind": "ocr"})
                for hx, hy, hh in hits:
                    rows.append({"x": lx0 + hx, "y": hy, "score": None, "method": "ocr",
                                 "kind": "ocr", "h": hh * 2})
            except pytesseract.TesseractNotFoundError:
                _warn_once()
            except Exception as e:
                _log_once(f"wall_ocr_err_{type(e).__name__}", f"[⚠️ CẢNH BÁO] Lỗi OCR thẻ Tường: {e}")

        if require_keyword and ocr_ok:
            before = len(rows)
            rows = [r for r in rows
                    if r["method"] == "ocr"
                    or any(abs(r["y"] - hy) < max(_WALL_ROW_MERGE_PX, r.get("h", 0) // 2)
                           for _, hy, _ in hits)]
            if len(rows) < before:
                _log_callback(f"[WALL] Loại {before - len(rows)} dòng không có chữ Tường/Wall/Thành ở dải TRÁI")
        elif require_keyword and not ocr_ok:
            _log_once("wall_kw_no_ocr",
                      "[⚠️ CẢNH BÁO] Không OCR được dải TRÁI -> không thể bắt buộc từ khóa Tường, "
                      "chỉ dựa vào template (dễ nhận nhầm công trình khác)")

        # ---- BƯỚC 2: CHẤM ĐIỂM CÓ TRỌNG SỐ ----
        #   điểm tổng = W_ID x điểm NHẬN DẠNG TƯỜNG (ảnh + chữ, phần TRÁI)  +  W_PRICE x điểm GIÁ/TÀI NGUYÊN (phần PHẢI)
        #   mặc định W_PRICE = 0.6, W_ID = 0.4. Chữ giá ĐỎ (thiếu tài nguyên) vẫn tính là "có giá" -> thẻ vẫn được nhận
        #   (can_upgrade = False), không bị loại chỉ vì thiếu tài nguyên.
        #   Điểm sàn WALL_MIN_ID_SCORE: phần nhận dạng Tường phải >= sàn, nếu không công trình khác cùng giá/icon
        #   sẽ qua mặt bằng điểm giá cao.
        try:
            cfg_w = load_json_config()
            w_price = float(cfg_w.get("WALL_WEIGHT_PRICE", 0.6))
            w_id = float(cfg_w.get("WALL_WEIGHT_ID", 0.4))
            min_id = float(cfg_w.get("WALL_MIN_ID_SCORE", 0.6))
            k_votes = max(1, int(cfg_w.get("WALL_MIN_VARIANT_VOTES", 2)))
        except Exception:
            w_price, w_id, min_id, k_votes = 0.6, 0.4, 0.6, 2
        total_w = (w_price + w_id) or 1.0
        w_price, w_id = w_price / total_w, w_id / total_w     # chuẩn hóa tổng trọng số = 1

        out = []
        for c in ImageProcessor._dedupe_wall_rows(rows):
            y, hh = c["y"], c.get("h", 0)
            try:
                right = ImageProcessor._analyze_wall_right(menu_img, y, hh, c.get("resource_hint"))
                ident = ImageProcessor._wall_identity_scores(menu_img, y, hh, xn_exclude_ratio)
            except Exception as e:
                _log_once(f"wall_right_err_{type(e).__name__}", f"[⚠️ CẢNH BÁO] Lỗi phân tích thẻ Tường: {e}")
                continue

            # --- Điểm NHẬN DẠNG (0.4): nhiều ảnh phải đồng thuận (trung bình k ảnh cao nhất) + chữ Tường (OCR) ---
            ranked = sorted(ident.values(), reverse=True)
            tpl_id = None
            if ranked:
                k = min(k_votes, len(ranked))
                tpl_id = max(0.0, sum(ranked[:k]) / k)
            kw = any(_near_wall_row(y, hy, hh) for _, hy, _ in hits)
            if tpl_id is not None and ocr_ok:
                id_score = 0.7 * tpl_id + 0.3 * (1.0 if kw else 0.0)
            elif tpl_id is not None:
                id_score = tpl_id
            else:
                id_score = 0.8 if kw else 0.0           # không có ảnh mẫu nhận dạng: chỉ dựa chữ, không cho điểm tuyệt đối

            # --- Điểm GIÁ / TÀI NGUYÊN (0.6): icon Vàng/Dầu + có chữ giá (trắng HOẶC đỏ đều tính) ---
            dbg = right.get("debug", {})
            if right.get("icon_src") == "icon":
                icon_score = max(0.0, min(1.0, float(dbg.get("icon_score", 0.0))))
            elif right.get("icon_src") == "color":
                icon_score = 0.6
            else:
                icon_score = 0.0
            has_price = right.get("price_px", 0) >= _WALL_WHITE_MIN_PX
            price_score = 0.6 * icon_score + 0.4 * (1.0 if has_price else 0.0)

            total = w_id * id_score + w_price * price_score
            hint, rtype = c.get("resource_hint"), right.get("resource_type")
            conflict = bool(hint and right.get("icon_src") == "icon" and hint != rtype)
            breakdown = (f"tổng {total:.2f} = {w_id:.1f}×ID {id_score:.2f} + {w_price:.1f}×Giá {price_score:.2f} "
                         f"(ảnh+chữ Tường {'' if tpl_id is None else f'{tpl_id:.2f}'}{' +chữ' if kw else ''}, "
                         f"icon {icon_score:.2f}, giá {'có' if has_price else 'không'})")
            if conflict:
                _log_callback(f"[WALL] Loại y={y}: ảnh thẻ nói {hint} nhưng icon mẫu nói {rtype} | {breakdown}")
                continue
            if id_score < min_id:
                _log_callback(f"[WALL] Loại y={y}: phần nhận dạng Tường {id_score:.2f} < sàn {min_id:.2f} "
                              f"(có thể là công trình khác cùng giá) | {breakdown}")
                continue
            if total < threshold:
                _log_callback(f"[WALL] Loại y={y}: điểm tổng < ngưỡng {threshold:.2f} | {breakdown}")
                continue
            _log_callback(f"[WALL] Nhận thẻ Tường y={y} | {breakdown}")
            out.append({**c, **right, "id_score": round(id_score, 3), "price_score": round(price_score, 3),
                        "total_score": round(total, 3)})
        out.sort(key=lambda c: (not c["can_upgrade"], -c["total_score"]))
        return out

    # ------------------------------------------------------------------
    # XÁC THỰC POPUP SAU KHI BẤM THẺ TƯỜNG (chống bấm nhầm công trình khác trên map)
    # ------------------------------------------------------------------
    @staticmethod
    def read_popup_text(screen_img, exclude_right_ratio=0.28):
        """
        OCR toàn màn hình (bỏ dải PHẢI nơi menu Búa nằm, để chữ 'Tường' của danh sách không gây nhầm)
        và trả chuỗi đã bỏ dấu + chữ thường, các khoảng trắng gộp thành 1. '' nếu lỗi / thiếu Tesseract.
        """
        if screen_img is None or getattr(screen_img, "size", 0) == 0 or not TESSERACT_AVAILABLE:
            return ""
        try:
            w = screen_img.shape[1]
            crop = screen_img[:, :int(w * (1.0 - exclude_right_ratio))]
            thresh = ImageProcessor.preprocess_for_coc_font(crop)
            if thresh is None:
                return ""
            text = pytesseract.image_to_string(
                thresh, lang=ImageProcessor._ocr_lang(), config="--psm 11")
            return re.sub(r"\s+", " ", ImageProcessor._strip_accents(text)).strip()
        except pytesseract.TesseractNotFoundError:
            _warn_once()
            return ""
        except Exception as e:
            _log_once(f"popup_ocr_err_{type(e).__name__}", f"[⚠️ CẢNH BÁO] Lỗi OCR popup: {e}")
            return ""

    @staticmethod
    def text_has_wall_word(norm_text):
        """True nếu chuỗi (đã bỏ dấu) có token giống 'Tường'/'Wall'/'Thành'."""
        return any(ImageProcessor._is_wall_word(t) for t in re.findall(r"[a-z0-9]+", norm_text or ""))

    @staticmethod
    def find_wall_card_in_menu(menu_img, threshold=_WALL_MATCH_THRESHOLD_DEFAULT, return_details=False):
        """
        Vị trí Y của thẻ Tường tốt nhất trong menu (None nếu không thấy).
        return_details=True -> trả nguyên dict ứng viên (x, y, method, score, kind) hoặc None.
        """
        cands = ImageProcessor.find_wall_cards_in_menu(menu_img, threshold)
        if not cands:
            return None
        return cands[0] if return_details else cands[0]["y"]

    # ------------------------------------------------------------------
    # HÀM CŨ (giữ lại để tương thích với test_crop_ocr.py và các chỗ gọi khác)
    # ------------------------------------------------------------------
    @staticmethod
    def read_number(screen_img, region):
        """Đọc một số nguyên từ vùng (y1, y2, x1, x2). None nếu lỗi."""
        if screen_img is None:
            return None
        if not TESSERACT_AVAILABLE:
            _warn_once()
            return None
        try:
            y1, y2, x1, x2 = region
            crop = screen_img[y1:y2, x1:x2]
            if crop.size == 0:
                return None
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            return ImageProcessor._digits_to_int(ImageProcessor._ocr_digits(gray))
        except pytesseract.TesseractNotFoundError:
            _warn_once()
            return None
        except Exception:
            return None

    @staticmethod
    def read_loot(screen_img):
        """Đọc Vàng / Dầu / Dầu đen dưới dạng dict (bọc quanh get_enemy_resources)."""
        gold, elixir, dark = ImageProcessor.get_enemy_resources(screen_img)
        return {"gold": gold, "elixir": elixir, "dark": dark}


# ---------------------------------------------------------------------------
# PHÂN TÍCH MÀU CHỮ GIÁ TIỀN (HSV)  ->  đủ / thiếu tài nguyên
# ---------------------------------------------------------------------------
def analyze_price_color(price_img, exclude_box=None):
    """
    Phân tích màu chữ số giá tiền trên nút nâng (không gian HSV):
      * chữ TRẮNG sáng (S thấp, V cao)  -> đủ tài nguyên      -> can_upgrade = True
      * chữ ĐỎ (Hue ~0 / ~180)          -> thiếu tài nguyên   -> can_upgrade = False (KHÔNG được bấm)
      * không thấy chữ nào đủ rõ        -> can_upgrade = False

    :param price_img: ảnh BGR vùng giá (dải PHẢI của thẻ Tường).
    :param exclude_box: (x, y, w, h) icon Vàng/Dầu cần loại khỏi phép đo (icon hồng/đỏ dễ bị tính nhầm là chữ đỏ).
    :return: dict(can_upgrade, color 'white'|'red'|'none', white_px, red_px, red_ratio,
                  cluster = mặt nạ chữ(+icon) để tính tâm nút bấm)
    """
    empty = {"can_upgrade": False, "color": "none", "white_px": 0, "red_px": 0, "red_ratio": 0.0,
             "cluster": np.zeros((1, 1), np.uint8)}
    if price_img is None or getattr(price_img, "size", 0) == 0:
        return empty
    if price_img.ndim == 2:
        price_img = cv2.cvtColor(price_img, cv2.COLOR_GRAY2BGR)
    hsv = cv2.cvtColor(price_img, cv2.COLOR_BGR2HSV)
    red = cv2.bitwise_or(cv2.inRange(hsv, (0, 120, 110), (6, 255, 255)),
                         cv2.inRange(hsv, (176, 120, 110), (180, 255, 255)))
    white = cv2.inRange(hsv, (0, 0, 200), (180, 60, 255))
    cluster = cv2.bitwise_or(red, white)
    if exclude_box:
        ix, iy, iw, ih = exclude_box
        ys, xs = slice(max(0, iy - 2), iy + ih + 2), slice(max(0, ix - 2), ix + iw + 2)
        red[ys, xs] = 0
        white[ys, xs] = 0
        cluster = cv2.bitwise_or(red, white)
        cluster[ys, xs] = 255        # icon vẫn thuộc cụm (giá + icon) để tâm nút bấm đúng
    red_px, white_px = cv2.countNonZero(red), cv2.countNonZero(white)
    red_ratio = red_px / float(red_px + white_px + 1)
    insufficient = red_px >= _WALL_RED_MIN_PX and red_ratio >= _WALL_RED_RATIO
    bright = white_px >= _WALL_WHITE_MIN_PX and not insufficient
    return {"can_upgrade": bool(bright),
            "color": "red" if insufficient else ("white" if bright else "none"),
            "white_px": white_px, "red_px": red_px, "red_ratio": red_ratio, "cluster": cluster}


# ---------------------------------------------------------------------------
# NÚT XANH LÁ (dự phòng cho nút "Về nhà" khi chưa có ảnh mẫu / ảnh mẫu không khớp)
# ---------------------------------------------------------------------------
_GREEN_HSV = ((35, 110, 110), (90, 255, 255))       # Hue xanh lá (thang OpenCV 0-180)
_GREEN_BOTTOM_RATIO = 0.35                          # chỉ quét 35% phía DƯỚI màn hình
_GREEN_X_RANGE = (0.25, 0.75)                       # và dải giữa theo chiều ngang (tránh icon xanh ở hai rìa)


def find_green_button_hsv(screen_img, bottom_ratio=_GREEN_BOTTOM_RATIO, x_range=_GREEN_X_RANGE,
                          hsv_range=_GREEN_HSV, return_details=False):
    """
    Tìm khối nút XANH LÁ lớn ở vùng 35% dưới màn hình bằng HSV (nút 'Về nhà' / 'Về làng' trên màn hình
    kết quả) - không phụ thuộc ngôn ngữ hay ảnh mẫu. Dùng khi không khớp được button_return_home*.png.

    Chỉ nhận khối có kích cỡ + tỉ lệ + độ đặc đúng như một nút (rộng 8-30% / cao 5-16% khung, tỉ lệ 1.5-4.5,
    độ đặc >= 0.55) nên ô quân vuông, thanh máu, icon nhỏ không bị nhận nhầm.
    :return: (x, y) tâm nút theo tọa độ ảnh gốc, hoặc None. return_details=True -> dict(pos, box, score).
    """
    if screen_img is None or getattr(screen_img, "size", 0) == 0 or screen_img.ndim != 3:
        return None
    try:
        H, W = screen_img.shape[:2]
        y0 = int(H * (1.0 - max(0.05, min(1.0, bottom_ratio))))
        x0, x1 = int(W * x_range[0]), int(W * x_range[1])
        region = screen_img[y0:H, x0:x1]
        if region.size == 0:
            return None
        hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array(hsv_range[0]), np.array(hsv_range[1]))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))   # lấp chữ trắng trên nút
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        best = None
        for c in cnts:
            x, y, w, h = cv2.boundingRect(c)
            if w < W * 0.08 or h < H * 0.05 or w > W * 0.30 or h > H * 0.16:
                continue
            if not 1.5 <= w / float(h) <= 4.5:
                continue
            fill = cv2.contourArea(c) / float(w * h)
            if fill < 0.55:
                continue
            score = w * h * fill
            if best is None or score > best[0]:
                best = (score, (x0 + x + w // 2, y0 + y + h // 2), (x0 + x, y0 + y, w, h))
        if best is None:
            return None
        if return_details:
            return {"pos": best[1], "box": best[2], "score": float(best[0])}
        return best[1]
    except Exception:
        return None


# ---------------------------------------------------------------------------
# QUÉT THANH QUÂN TRẬN ĐÁNH  (hero / spell / troop)
# ---------------------------------------------------------------------------
BAR_REF_SIZE = (960, 540)           # toạ độ cấu hình & toạ độ trả về luôn theo khung 960x540
BAR_SCAN_X_RANGE = (74, 862)
BAR_CENTER_Y = 489
BAR_SLOT_SIZE = 68
BAR_SLOT_STEP = 75
BAR_MATCH_PAD = 8
BAR_INNER_RATIO = 0.5
BAR_EMPTY_STD = 14.0                # STD xám phần giữa ô < 14.0 -> ô nét đứt rỗng, bỏ qua
BAR_HERO_THRESHOLD = 0.70
BAR_SPELL_THRESHOLD = 0.70
BAR_HERO_PATTERN = "button_hero*.png"
BAR_SPELL_PATTERN = "button_spell*.png"


def _best_template_score(region, template_paths):
    """Điểm khớp cao nhất (0..1) giữa `region` và danh sách ảnh mẫu (mẫu lớn hơn vùng thì thu nhỏ vừa vùng)."""
    best = 0.0
    rh, rw = region.shape[:2]
    for path in template_paths:
        entry = _load_template_cached(path)
        if entry is None:
            continue
        tpl = entry[1]
        th, tw = tpl.shape[:2]
        if th > rh or tw > rw:
            scale = min(rh / float(th), rw / float(tw))
            tpl = cv2.resize(tpl, (max(1, int(tw * scale)), max(1, int(th * scale))), interpolation=cv2.INTER_AREA)
        if tpl.shape[0] < 2 or tpl.shape[1] < 2:
            continue
        try:
            res = cv2.matchTemplate(region, tpl, cv2.TM_CCOEFF_NORMED)
        except cv2.error:
            continue
        score = float(np.nan_to_num(res, nan=-1.0, posinf=-1.0, neginf=-1.0).max())
        if score > best:
            best = score
    return best


def scan_battle_bar(screen_img, x_range=BAR_SCAN_X_RANGE, center_y=BAR_CENTER_Y, slot_size=BAR_SLOT_SIZE,
                    slot_step=BAR_SLOT_STEP, match_pad=BAR_MATCH_PAD, inner_ratio=BAR_INNER_RATIO,
                    empty_std=BAR_EMPTY_STD, hero_threshold=BAR_HERO_THRESHOLD,
                    spell_threshold=BAR_SPELL_THRESHOLD, hero_pattern=BAR_HERO_PATTERN,
                    spell_pattern=BAR_SPELL_PATTERN, infer_spells=True):
    """
    Quét dải ô theo trục X trên thanh quân và phân loại từng ô (trái -> phải):
      1) Cắt phần GIỮA ô (inner_ratio, bỏ viền) -> độ lệch chuẩn mức xám (Gray STD).
         STD < empty_std (14.0) => ô nét đứt rỗng, loại hẳn.
      2) khớp ảnh mẫu `hero_pattern`  (button_hero*.png)  -> "hero"
      3) khớp ảnh mẫu `spell_pattern` (button_spell*.png) -> "spell"
      4) các ô còn lại                                      -> "troop"
    Chưa có ảnh mẫu Phép nhưng đã thấy Tướng: ô "troop" nằm SAU ô Tướng cuối cùng được suy đoán là Phép
    (thứ tự thanh quân: Lính -> Tướng -> Phép), tắt bằng infer_spells=False.

    Chỉ gọi khi ĐANG TRONG TRẬN: ở làng nhà, thanh UI phía dưới bị quét nhầm thành các ô quân.
    :return: list[dict(type, x, y, std, score)] - x, y theo khung 960x540; rỗng nếu không quét được.
    """
    if screen_img is None or getattr(screen_img, "size", 0) == 0 or screen_img.ndim != 3:
        return []
    img_h, img_w = screen_img.shape[:2]
    sx, sy = img_w / float(BAR_REF_SIZE[0]), img_h / float(BAR_REF_SIZE[1])
    half = slot_size // 2
    hero_paths = _expand_template_paths(hero_pattern)
    spell_paths = _expand_template_paths(spell_pattern)

    slots = []
    x_min, x_max = x_range
    for cx in range(x_min + half, x_max - half + 1, slot_step):
        x1, x2 = round((cx - half) * sx), round((cx + half) * sx)
        y1, y2 = round((center_y - half) * sy), round((center_y + half) * sy)
        if x1 < 0 or y1 < 0 or x2 > img_w or y2 > img_h:
            continue
        crop = screen_img[y1:y2, x1:x2]
        ih, iw = crop.shape[:2]
        my, mx = int(ih * (1 - inner_ratio) / 2), int(iw * (1 - inner_ratio) / 2)
        inner = crop[my:ih - my, mx:iw - mx]
        if inner.size == 0:
            continue
        std = float(cv2.cvtColor(inner, cv2.COLOR_BGR2GRAY).std())
        if std < empty_std:
            continue                      # ô nét đứt rỗng

        px, py = round(match_pad * sx), round(match_pad * sy)
        wide = screen_img[max(0, y1 - py):min(img_h, y2 + py), max(0, x1 - px):min(img_w, x2 + px)]
        hero_score = _best_template_score(wide, hero_paths) if hero_paths else 0.0
        spell_score = _best_template_score(wide, spell_paths) if spell_paths else 0.0
        if hero_paths and hero_score >= hero_threshold:
            kind, score = "hero", hero_score
        elif spell_paths and spell_score >= spell_threshold:
            kind, score = "spell", spell_score
        else:
            kind, score = "troop", 0.0
        slots.append({"type": kind, "x": cx, "y": center_y, "std": round(std, 1), "score": round(score, 3)})

    if infer_spells and not spell_paths:
        last_hero = max((i for i, sl in enumerate(slots) if sl["type"] == "hero"), default=None)
        if last_hero is not None:
            for sl in slots[last_hero + 1:]:
                sl["type"] = "spell"
    return slots


# ---------------------------------------------------------------------------
# XÁC THỰC POPUP NÂNG TƯỜNG
# ---------------------------------------------------------------------------
WALL_POPUP_OK_PATTERN = "wall_popup_ok*.png"              # ảnh mẫu popup nâng Tường hợp lệ (tùy chọn)
WALL_POPUP_WRONG_PATTERN = "wrong_building_popup*.png"    # ảnh mẫu popup công trình nhầm (tùy chọn)
WALL_POPUP_WRONG_KEYWORDS = ("thong tin", "tong quan", "phong thu")   # đã bỏ dấu + chữ thường


def verify_wall_popup(screen_img, strict=False, threshold=0.70):
    """
    Kiểm tra giao diện nâng Tường đã MỞ THÀNH CÔNG chưa - gọi TRƯỚC khi bấm nút Nâng / Xác nhận,
    để không bấm nhầm khi thực ra đã mở UI của công trình khác trên map.
    Thứ tự: (1) ảnh mẫu wall_popup_ok*.png      -> HỢP LỆ
            (2) ảnh mẫu wrong_building_popup*.png -> NHẦM
            (3) OCR: có chữ Tường/Wall/Thành -> HỢP LỆ; chỉ có THÔNG TIN/TỔNG QUAN/PHÒNG THỦ -> NHẦM
            (4) không kết luận được: strict=False -> cho đi tiếp; strict=True -> coi là NHẦM.
    :return: (ok, lý do)
    """
    if screen_img is None or getattr(screen_img, "size", 0) == 0:
        return (not strict), "không có ảnh để xác thực" + ("" if not strict else " (strict -> từ chối)")
    if find_template_any(screen_img, WALL_POPUP_OK_PATTERN, threshold, use_cache=False):
        return True, "khớp ảnh mẫu wall_popup_ok"
    if find_template_any(screen_img, WALL_POPUP_WRONG_PATTERN, threshold, use_cache=False):
        return False, "khớp ảnh mẫu wrong_building_popup"

    text = ImageProcessor.read_popup_text(screen_img)
    if text:
        if ImageProcessor.text_has_wall_word(text):
            return True, "OCR thấy chữ Tường/Wall"
        bad = next((k for k in WALL_POPUP_WRONG_KEYWORDS if k in text), None)
        if bad:
            return False, f"OCR thấy UI công trình khác ('{bad}') và không có chữ Tường"
    if strict:
        return False, "không xác nhận được popup Tường (chế độ strict)"
    return True, "không kết luận được -> cho đi tiếp"


# Gắn các hàm cấp module vào ImageProcessor để gọi được cả hai kiểu:
#     ImageProcessor.scan_battle_bar(...)  /  from core.image_processing import scan_battle_bar
ImageProcessor.analyze_price_color = staticmethod(analyze_price_color)
ImageProcessor.find_green_button_hsv = staticmethod(find_green_button_hsv)
ImageProcessor.scan_battle_bar = staticmethod(scan_battle_bar)
ImageProcessor.verify_wall_popup = staticmethod(verify_wall_popup)
ImageProcessor.compute_frame_fingerprint = staticmethod(compute_frame_fingerprint)


# Bí danh cấp module để có thể gọi: from core.image_processing import find_any_template_location
find_any_template_location = ImageProcessor.find_any_template_location