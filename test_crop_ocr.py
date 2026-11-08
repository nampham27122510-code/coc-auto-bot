"""
Script kiểm thử độc lập (đặt ở thư mục gốc dự án):
  1. Kết nối ADB tới LDPlayer 9 (Instance ID 0, cổng 5555)
  2. Chụp màn hình
  3. Cắt vùng BUILDER_REGION và đọc OCR số thợ xây rảnh
Chạy:  python test_crop_ocr.py
"""
import os
import sys

import cv2

# Đảm bảo import được package `core` và `config` khi chạy từ bất kỳ đâu
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import config
from core.adb_controller import ADBController
from core.image_processing import ImageProcessor


def test_screen_crop():
    print("=" * 60)
    print("🔍 TEST CẮT VÙNG & OCR - COC BOT")
    print("=" * 60)

    # ---------- Bước 1: Khởi tạo & kết nối ADB ----------
    print(f"[1/4] 🔌 Khởi tạo ADBController (adb: {config.ADB_PATH}, cổng: {config.ADB_PORT})...")
    try:
        adb = ADBController()
        if not adb.connect():
            print("❌ Kết nối ADB thất bại!")
            print("👉 Hướng dẫn khắc phục:")
            print("   1. Đảm bảo LDPlayer 9 (Instance ID 0) đang mở hẳn trên màn hình.")
            print("   2. LDPlayer -> Cài đặt -> Mục khác -> Debug ADB: chọn 'Bật kết nối local' -> Lưu.")
            print("   3. Kiểm tra ADB_PATH / LDPLAYER_PATH trong config.py.")
            return
        print(f"✅ Đã kết nối ADB tới {adb.device_str}")
    except Exception as e:
        print(f"❌ Lỗi khi kết nối ADB: {e}")
        return

    # ---------- Bước 2: Chụp màn hình ----------
    print("[2/4] 📸 Đang chụp màn hình giả lập...")
    try:
        screen = adb.capture_screen()
    except Exception as e:
        print(f"❌ Lỗi khi chụp màn hình: {e}")
        return

    if screen is None:
        print("❌ Chụp màn hình thất bại sau nhiều lần thử!")
        return

    h, w = screen.shape[:2]
    print(f"✅ Chụp thành công, kích thước ảnh: {w}x{h}")
    if (w, h) != (960, 540):
        print("⚠️ Cảnh báo: độ phân giải không phải 960x540, các vùng cắt trong config.py có thể bị lệch!")

    # Lưu ảnh toàn màn hình để đối chiếu
    try:
        cv2.imwrite("debug_fullscreen.png", screen)
        print("💾 Đã lưu ảnh toàn màn hình: debug_fullscreen.png")
    except Exception as e:
        print(f"⚠️ Không lưu được debug_fullscreen.png: {e}")

    # ---------- Bước 3: Cắt vùng thợ xây ----------
    print("[3/4] ✂️ Đang cắt vùng BUILDER_REGION...")
    try:
        y1, y2, x1, x2 = config.BUILDER_REGION
        builder_crop = screen[y1:y2, x1:x2]
        if builder_crop.size == 0:
            print("❌ Vùng cắt rỗng, hãy kiểm tra lại BUILDER_REGION trong config.py!")
        else:
            crop_big = cv2.resize(
                builder_crop,
                (builder_crop.shape[1] * 3, builder_crop.shape[0] * 3),
                interpolation=cv2.INTER_NEAREST,
            )
            cv2.imwrite("debug_builder_crop.png", crop_big)
            print("💾 Đã lưu vùng cắt (phóng to x3): debug_builder_crop.png")
    except Exception as e:
        print(f"⚠️ Lỗi khi cắt/lưu vùng thợ xây: {e}")

    # ---------- Bước 4: OCR số thợ xây ----------
    print("[4/4] 🔍 Đang đọc OCR số thợ xây rảnh...")
    try:
        builders_found = ImageProcessor.get_free_builders(screen)
        print(f"✅ Kết quả OCR - Thợ xây rảnh đọc được: {builders_found}")
    except Exception as e:
        print(f"❌ Lỗi khi OCR số thợ xây: {e}")
        print(config.TESSERACT_ERROR_MSG)
        return

    print("=" * 60)
    print("🎉 Hoàn tất kiểm thử. Mở debug_fullscreen.png và debug_builder_crop.png để đối chiếu.")
    print("=" * 60)


if __name__ == "__main__":
    test_screen_crop()