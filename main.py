import os
import sys
import shutil
import urllib.request
import subprocess
import threading
import time
import traceback

# ----------------------------------------------------------------------------------------------
# TỰ ĐỘNG CÀI TESSERACT OCR (chạy TRƯỚC khi import các module của bot)
# Lý do đặt ở đây: core/image_processing.py có thể dò đường dẫn Tesseract ngay lúc import,
# nên Tesseract phải có sẵn trước khi các module đó được nạp.
# ----------------------------------------------------------------------------------------------
TESSERACT_DEFAULT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
TESSERACT_INSTALLER_URL = (
    "https://github.com/UB-Mannheim/tesseract/releases/download/"
    "v5.3.3.20231005/tesseract-ocr-w64-setup-5.3.3.20231005.exe"
)


def auto_setup_dependencies():
    """Tự động kiểm tra và tải cài đặt Tesseract OCR nếu máy chưa có. Không bao giờ ném lỗi."""
    if os.name != "nt":
        return   # bản cài đặt .exe chỉ dành cho Windows

    # Kiểm tra xem Tesseract đã tồn tại hay chưa
    if os.path.exists(TESSERACT_DEFAULT_PATH) or shutil.which("tesseract"):
        _register_tesseract()
        return

    print("⚠️ Chưa tìm thấy Tesseract OCR. Đang tự động tải và cài đặt...")
    installer_path = os.path.join(os.environ.get("TEMP", "."), "tesseract_installer.exe")
    try:
        # Tải file cài đặt
        with urllib.request.urlopen(TESSERACT_INSTALLER_URL, timeout=60) as resp, \
                open(installer_path, "wb") as f:
            shutil.copyfileobj(resp, f)
        # Chạy cài đặt ngầm (Silent Mode)
        subprocess.run([installer_path, "/S"], check=True, timeout=600)
        if os.path.exists(TESSERACT_DEFAULT_PATH):
            print("✅ Đã tự động cài đặt Tesseract OCR thành công!")
            _register_tesseract()
        else:
            print("⚠️ Cài đặt xong nhưng chưa thấy tesseract.exe, hãy cài thủ công nếu OCR báo lỗi.")
    except Exception as e:
        print(f"❌ Lỗi tự động cài đặt Tesseract: {e}")
        print("   (Có thể cần chạy bằng quyền Administrator hoặc cài Tesseract thủ công.)")
    finally:
        try:
            if os.path.exists(installer_path):
                os.remove(installer_path)
        except Exception:
            pass


def _register_tesseract():
    """Để tiến trình hiện tại dùng được Tesseract ngay (không cần mở lại terminal)."""
    try:
        if os.path.exists(TESSERACT_DEFAULT_PATH) and not shutil.which("tesseract"):
            os.environ["PATH"] = os.path.dirname(TESSERACT_DEFAULT_PATH) + os.pathsep + os.environ.get("PATH", "")
        try:
            import pytesseract
            if os.path.exists(TESSERACT_DEFAULT_PATH):
                current = getattr(pytesseract.pytesseract, "tesseract_cmd", "tesseract")
                if not shutil.which(current) and not os.path.exists(current):
                    pytesseract.pytesseract.tesseract_cmd = TESSERACT_DEFAULT_PATH
        except ImportError:
            pass
    except Exception:
        pass


auto_setup_dependencies()

from gui.app_ui import (
    AppUI, DEPLOY_MODES, SPELL_MODES, DEFAULT_DEPLOY_MODE, DEFAULT_SPELL_MODE,
)
from core.emulator_controller import EmulatorController
from core.adb_controller import ADBController
from core.bot_logic import BotLogic
from core.image_processing import set_logger

GAME_PACKAGE = "com.supercell.clashofclans"

# Các trạng thái của BotManager (hiển thị qua log để dễ theo dõi)
STATE_STARTING = "STARTING"            # đang bật LDPlayer / kết nối ADB / mở game
STATE_WAITING_SETUP = "WAITING_SETUP"  # game đã mở, chờ người dùng xác nhận cấu hình tọa độ trên UI
STATE_RUNNING = "RUNNING"              # vòng lặp chính đang chạy
STATE_STOPPED = "STOPPED"


class BotManager:
    def __init__(self):
        self.ui = AppUI(start_bot_callback=self.start_bot,
                        stop_bot_callback=self.stop_bot)
        self.emulator = None
        self.adb = None
        self.bot = None
        self.stop_event = threading.Event()   # báo cho BotLogic thoát các lần chờ dài
        self._run_id = 0                      # tăng mỗi lần Bắt đầu: luồng cũ (đã Dừng) tự thoát, không chạy song song
        self.state = STATE_STOPPED
        set_logger(self.ui.log)               # cảnh báo OCR (thiếu Tesseract...) hiện lên Log UI

    def _alive(self, run_id):
        """Luồng chạy `run_id` còn hiệu lực (chưa bị Dừng và chưa bị lần Bắt đầu mới thay thế)."""
        return self.ui.is_running and run_id == self._run_id

    def _wait(self, seconds, run_id=None):
        """Chờ có kiểm tra nút Dừng. Trả về False nếu người dùng đã bấm dừng."""
        run_id = self._run_id if run_id is None else run_id
        for _ in range(seconds):
            if not self._alive(run_id):
                return False
            time.sleep(1)
        return True

    def _create_adb(self, ld_index):
        """
        Tạo ADBController. Nếu ADBController bản hiện tại chưa nhận tham số `index`
        thì tự dùng cách khởi tạo cũ (cổng mặc định 5555, đúng với Index 0).
        """
        try:
            return ADBController(adb_path=self.emulator.adb_path, index=ld_index)
        except TypeError:
            pass
        # Bản cũ chưa có `index`: tự tính cổng (Index n -> 5555 + n*2) và truyền qua `port`
        port = 5555 + ld_index * 2
        try:
            self.ui.log(f"ℹ️ ADBController chưa hỗ trợ `index` -> dùng cổng {port}.")
            return ADBController(adb_path=self.emulator.adb_path, port=port)
        except TypeError:
            if ld_index != 0:
                self.ui.log("⚠️ ADBController không nhận được cổng -> dùng 5555 mặc định, "
                            "chỉ đúng với Index 0. Hãy thay core/adb_controller.py bằng bản mới.")
            return ADBController(adb_path=self.emulator.adb_path)

    def _create_bot(self, config):
        """Tạo BotLogic + gán kiểu thả quân / phép từ config GUI."""
        bot = BotLogic(adb_controller=self.adb,
                       emulator_controller=self.emulator,
                       ui_queue=self.ui._queue,
                       stop_event=self.stop_event)
        # BotLogic.deploy_troops() đọc các giá trị này
        bot.deploy_mode = config.get("deploy_mode", DEFAULT_DEPLOY_MODE)
        bot.enable_spell = config.get("enable_spell", True)
        bot.spell_mode = config.get("spell_mode", DEFAULT_SPELL_MODE)
        spell_text = (SPELL_MODES.get(bot.spell_mode, bot.spell_mode) if bot.enable_spell else "TẮT")
        self.ui.log(f"🪖 Kiểu thả quân: {DEPLOY_MODES.get(bot.deploy_mode, bot.deploy_mode)}"
                    f" | Phép: {spell_text}")
        return bot

    def _try_fast_attach(self, config):
        """
        KHỞI ĐỘNG THÔNG MINH: nếu giả lập + game đã chạy sẵn thì kết nối ADB, chụp 1 ảnh và nhận diện
        trạng thái. Trả về trạng thái (khác 'UNKNOWN') nếu game đã mở sẵn, ngược lại None
        (-> chạy luồng khởi động đầy đủ như cũ). Không ném lỗi.
        """
        try:
            self.adb = self._create_adb(config["ld_index"])
            if not self.adb.connect():
                return None
            screen = self.adb.capture_screen()
            if screen is None:
                return None
            self.bot = self._create_bot(config)
            state, _screen = self.bot.detect_stable_state()   # xóa cache + 2 lần chụp mới phải trùng nhau
            if state == "UNKNOWN":
                self.bot = None
                return None
            return state
        except Exception:
            self.ui.log("[⚠️ CẢNH BÁO] Không nhận diện nhanh được trạng thái:\n" + traceback.format_exc())
            self.bot = None
            return None

    def start_bot(self, config):
        """Chạy trong thread riêng (do AppUI tạo), không chặn giao diện."""
        # Mỗi lần Bắt đầu: Event MỚI (luồng cũ giữ Event cũ đã set -> dừng hẳn) + mã lượt chạy mới
        self.stop_event = threading.Event()
        self._run_id += 1
        run_id = self._run_id
        self.state = STATE_STARTING
        self.ui.log("==================================================")
        self.ui.log("🚀 KHỞI ĐỘNG HỆ THỐNG AUTO BOT CLASH OF CLANS")

        # 1. LDPlayer
        ld_path, ld_index = config["ld_path"], config["ld_index"]
        self.ui.log(f"📁 Kiểm tra LDPlayer tại: {ld_path}")
        self.emulator = EmulatorController(index=ld_index, ld_path=ld_path)
        if not self.emulator.exists():
            self.ui.log("[❌ LỖI] Không tìm thấy ldconsole.exe, kiểm tra lại đường dẫn LDPlayer!")
            self.ui._on_click_stop()
            return

        # 1b. Game đã mở sẵn? -> bỏ qua khởi động giả lập / mở game / chờ xác nhận, nhảy thẳng vào luồng
        fast_state = self._try_fast_attach(config)
        if fast_state is not None:
            self.ui.log(f"⚡ Game đã mở sẵn (trạng thái: {fast_state}) -> bỏ qua khởi động giả lập & mở game.")
            self.ui.set_capture_provider(self.adb.capture_screen)
            self.state = STATE_RUNNING
            try:
                self.bot.process_initial_state(config["min_gold"], config["min_elixir"], config["min_dark"])
            except Exception:
                self.ui.log("[❌ LỖI] Xử lý trạng thái ban đầu gặp lỗi:\n" + traceback.format_exc())
            self._main_loop(config, run_id)
            return

        self.ui.set_status("📱 Đang bật LDPlayer...", "info")
        self.ui.log(f"📱 Cấu hình & khởi chạy LDPlayer (ID: {ld_index}) 960x540, 30FPS...")
        self.emulator.setup_and_launch(width=960, height=540, dpi=160, fps=30)

        # 2. ADB (chờ giả lập boot)
        self.ui.log("⏳ Chờ giả lập boot 20 giây...")
        if not self._wait(20, run_id):
            return

        self.ui.set_status("🔌 Đang kết nối ADB...", "info")
        self.adb = self._create_adb(ld_index)
        connected = False
        for i in range(15):
            if not self._alive(run_id):
                return
            if self.adb.connect():
                connected = True
                break
            self.ui.log(f"⏳ Chưa thấy ADB, đang chờ giả lập... ({i + 1}/15)")
            time.sleep(3)

        if not connected:
            self.ui.log("[❌ LỖI] Không kết nối được ADB! Hãy bật ADB trong Cài đặt LDPlayer.")
            self.ui._on_click_stop()
            return
        self.ui.log("🟢 Kết nối ADB thành công!")

        # 3. Mở game
        self.ui.set_status("🎮 Đang mở game...", "info")
        self.ui.log("🎮 Mở Clash of Clans...")
        self.emulator.launch_game(GAME_PACKAGE)
        self.ui.log("⏳ Chờ 12 giây để game load...")
        if not self._wait(12, run_id):
            return

        # 3b. WAITING_SETUP: game đã mở -> cho phép chụp màn hình để chọn tọa độ, rồi CHỜ người dùng
        # xác nhận cấu hình trên UI. Chỉ sau khi xác nhận mới kích hoạt vòng lặp chính.
        self.ui.set_capture_provider(self.adb.capture_screen)
        self.state = STATE_WAITING_SETUP
        self.ui.log(f"⏸ [{self.state}] Đã mở game, chờ người dùng xác nhận cấu hình tọa độ...")
        if not self.ui.wait_for_setup_confirm():
            self.state = STATE_STOPPED
            self.ui.log("⏹ Đã dừng trong lúc chờ xác nhận cấu hình.")
            return
        if self.stop_event.is_set():
            self.state = STATE_STOPPED
            return
        self.state = STATE_RUNNING

        # BotLogic gửi log/stats qua hàng đợi của AppUI
        self.bot = self._create_bot(config)
        self._main_loop(config, run_id)

    def _main_loop(self, config, run_id):
        """Vòng lặp chính (dùng chung cho cả khởi động đầy đủ lẫn khởi động nhanh)."""
        self.state = STATE_RUNNING
        self.ui.log("⚔️ Bot sẵn sàng! Bắt đầu vòng lặp giám sát...")

        # 4. Vòng lặp chính: chụp màn hình MỘT lần mỗi vòng rồi chuyền cho các hàm check_*
        # Thoát khi bấm Dừng (ui.is_running) HOẶC khi bot tự dừng (stop_event, ví dụ cào đủ mẫu)
        stop_event = self.stop_event
        bot, adb, emulator = self.bot, self.adb, self.emulator   # giữ tham chiếu của CHÍNH lượt chạy này
        cap_fail = 0   # số lần chụp màn hình thất bại liên tiếp
        while self._alive(run_id) and not stop_event.is_set():
            try:
                screen = adb.capture_screen()
                if screen is None:
                    cap_fail += 1
                    why = getattr(adb, "last_error", "") or "không rõ lý do"
                    self.ui.log(f"⚠️ Chưa chụp được màn hình ({cap_fail}): {why}")
                    if cap_fail == 3:
                        self.ui.log("🔌 Thử nối lại ADB...")
                        adb.connect()
                    elif cap_fail == 6:
                        self.ui.log("🔌 Vẫn lỗi -> khởi động lại ADB server...")
                        adb.restart_server()
                    elif cap_fail >= 12:
                        self.ui.log("🔁 ADB không phục hồi -> khởi động lại LDPlayer...")
                        bot._reboot_emulator()
                        cap_fail = 0
                    time.sleep(1)
                    continue
                cap_fail = 0

                # Ưu tiên 1: popup mất kết nối (Thử lại / Tải lại) -> xử lý xong thì bắt đầu lại vòng
                if bot.check_connection_error(screen):
                    continue

                # Ưu tiên 2: Stuck Watchdog - đứng yên quá STUCK_TIMEOUT thì phục hồi bậc thang
                # (bấm tọa độ an toàn -> tắt/mở lại game -> khởi động lại LDPlayer)
                if bot.update_stuck_watchdog(screen):
                    continue

                status = bot.process_home_village(screen, auto_stop_if_full=True)
                if status == "STOP":
                    self.ui.log("🛑 Hết thợ xây rảnh -> tắt bot và LDPlayer...")
                    emulator.close_emulator()
                    self.ui._on_click_stop()
                    break
                if status == "SUCCESS":
                    continue   # vừa thao tác menu -> ảnh `screen` đã cũ, chụp lại ở vòng sau

                bot.check_and_search_match(
                    screen,
                    min_gold=config["min_gold"],
                    min_elixir=config["min_elixir"],
                    min_dark=config["min_dark"],
                )
                s = bot.stats
                # Đồng bộ định dạng mới: (battles, walls, until_wall); tắt nâng Tường -> until_wall = None
                try:
                    until_wall = bot._battles_until_wall_check() if bot._wall_settings()["enable"] else None
                except Exception:
                    until_wall = None
                self.ui.update_stats(s["battles"], s["walls"], until_wall)
                if bot.collect_finished:
                    break   # đã cào đủ mẫu -> thoát vòng lặp, kết thúc bot
            except Exception:
                # Một vòng lỗi không được làm sập cả bot
                self.ui.log("[❌ LỖI] Vòng lặp chính gặp lỗi, sẽ thử lại:\n" + traceback.format_exc())
            # Nghỉ ngắn giữa các vòng (LOOP_SLEEP, tối đa ~0.3s) + dọn bộ nhớ định kỳ + ghi log phân tích vòng chậm.
            # (Trước đây nghỉ cứng 3 giây mỗi vòng -> mỗi bước đi tấn công bị trễ thêm ~3s.)
            bot.loop_tick()

        self.state = STATE_STOPPED
        self.ui.log("🔒 Tiến trình Bot đã kết thúc an toàn.")

    def stop_bot(self):
        self.stop_event.set()
        try:   # bỏ ảnh cache để lần Bắt đầu sau không nhìn ảnh cũ
            if self.adb is not None:
                self.adb.clear_screenshot_cache()
        except Exception:
            pass
        self.ui.log("⏹ Đã phát lệnh dừng bot.")

    def run(self):
        self.ui.mainloop()


if __name__ == "__main__":
    BotManager().run()