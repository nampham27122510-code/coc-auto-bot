# -*- coding: utf-8 -*-
"""
core/adb_controller.py
----------------------
Điều khiển LDPlayer qua ADB: kết nối, chụp màn hình, bấm, vuốt.

Cổng ADB theo Index giả lập: Index n -> 5555 + n * 2 (Index 0 = 5555, Index 1 = 5557...).
"""

import os
import subprocess
import threading
import time

import cv2
import numpy as np

from config import ADB_PATH, ADB_TIMEOUT

try:
    from config import SCREENSHOT_MIN_INTERVAL
except ImportError:                      # config.py cũ chưa có khóa này
    SCREENSHOT_MIN_INTERVAL = 0.25

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ADBController:
    def __init__(self, adb_path=None, port=None, index=0):
        self.adb_path = adb_path or ADB_PATH
        self.index = int(index)
        # Không truyền port thì tự tính theo index
        self.port = 5555 + self.index * 2 if port is None else int(port)
        self.device_address = f"127.0.0.1:{self.port}"
        self.timeout = ADB_TIMEOUT
        self.last_zoom_window = ""   # mô tả cửa sổ đã nhận cho Ctrl + cuộn (để ghi log chẩn đoán)

        # Throttle chụp màn hình (chống sập máy / BSoD): xem capture_screen()
        self._last_screen = None
        self._last_capture_time = 0.0
        self._capture_lock = threading.Lock()
        self.last_error = ""   # lý do lần capture_screen() gần nhất thất bại (để main.py ghi log chẩn đoán)

    # ------------------------------------------------------------------
    # Hạ tầng
    # ------------------------------------------------------------------
    def _run(self, args, timeout=None, target_device=True):
        """Chạy lệnh adb, trả về CompletedProcess (stdout là bytes). Có thể ném lỗi subprocess."""
        cmd = [self.adb_path]
        if target_device:
            cmd += ["-s", self.device_address]
        cmd += list(args)
        return subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout or self.timeout,
            creationflags=_NO_WINDOW,
        )

    def is_connected(self):
        """True nếu địa chỉ giả lập đang ở trạng thái 'device' trong `adb devices`."""
        try:
            out = self._run(["devices"], target_device=False).stdout.decode(errors="ignore")
        except Exception:
            return False
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[0] == self.device_address and parts[1] == "device":
                return True
        return False

    def connect(self):
        """Kết nối tới giả lập theo index/port. Trả về True nếu thành công."""
        try:
            self._run(["connect", self.device_address], timeout=10, target_device=False)
        except Exception:
            return False
        if not self.is_connected():
            return False
        self.set_dns()
        return True

    def restart_server(self):
        """Tắt/bật lại adb server rồi kết nối lại (khi adb offline / lệch phiên bản). Trả về True nếu nối lại được."""
        try:
            self._run(["kill-server"], timeout=10, target_device=False)
            time.sleep(1)
            self._run(["start-server"], timeout=15, target_device=False)
        except Exception:
            pass
        return self.connect()

    def set_dns(self, dns="8.8.8.8"):
        """Đặt DNS (best-effort, lỗi thì bỏ qua)."""
        try:
            self._run(["shell", "setprop", "net.dns1", dns])
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Thao tác
    # ------------------------------------------------------------------
    def invalidate_screen_cache(self):
        """Bỏ ảnh đang cache để lần capture_screen() kế tiếp chụp ảnh MỚI (gọi sau mỗi thao tác tác động lên màn hình)."""
        self._last_capture_time = 0.0

    def clear_screenshot_cache(self):
        """Xóa hẳn ảnh cache: lần capture_screen() kế tiếp BẮT BUỘC chụp ảnh mới (gọi khi Dừng / Bắt đầu lại)."""
        with self._capture_lock:
            self._last_screen = None
            self._last_capture_time = 0.0

    def capture_screen(self, fresh=False):
        """
        fresh=True: bỏ qua cache, luôn chụp ảnh mới (dùng khi nhận diện trạng thái ban đầu).

        Chụp màn hình -> ảnh BGR (OpenCV). Trả về None nếu lỗi.

        Throttle Cache: nếu lần gọi này cách lần chụp thật gần nhất < SCREENSHOT_MIN_INTERVAL (0.25s)
        thì trả NGAY ảnh cũ (bản sao) mà KHÔNG chạy `adb exec-out screencap` -> giảm mạnh tải CPU/RAM.
        Cache tự bị hủy sau click / vuốt / zoom (invalidate_screen_cache) để bot không bao giờ
        nhìn ảnh cũ ngay sau khi vừa thao tác.
        """
        with self._capture_lock:
            if fresh:
                self._last_screen = None
                self._last_capture_time = 0.0
            now = time.monotonic()
            if (self._last_screen is not None
                    and 0 <= now - self._last_capture_time < SCREENSHOT_MIN_INTERVAL):
                return self._last_screen.copy()
            try:
                result = self._run(["exec-out", "screencap", "-p"], timeout=10)
                if result.returncode != 0 or not result.stdout:
                    err = (result.stderr or b"").decode(errors="ignore").strip()[:150]
                    self.last_error = f"screencap lỗi (mã {result.returncode}, {len(result.stdout or b'')} byte): {err or 'không có thông báo'}"
                    return None
                arr = np.frombuffer(result.stdout, dtype=np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if img is None:
                    self.last_error = f"ảnh nhận về không giải mã được ({len(result.stdout)} byte)"
                    return None
                self.last_error = ""
                self._last_screen = img
                self._last_capture_time = time.monotonic()
                return img.copy()
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {str(e)[:150]}"
                return None

    def click(self, x, y):
        """Bấm (x, y). :return: True nếu adb chạy lệnh thành công, False nếu adb báo lỗi (last_error ghi lý do).
        Lỗi subprocess (timeout...) vẫn ném ra như cũ."""
        self.invalidate_screen_cache()
        result = self._run(["shell", "input", "tap", str(int(x)), str(int(y))])
        if result.returncode != 0:
            err = (result.stderr or b"").decode(errors="ignore").strip()[:150]
            self.last_error = f"tap lỗi (mã {result.returncode}): {err or 'không có thông báo'}"
            return False
        return True

    def swipe(self, x1, y1, x2, y2, duration=300):
        self.invalidate_screen_cache()
        self._run(
            ["shell", "input", "swipe",
             str(int(x1)), str(int(y1)), str(int(x2)), str(int(y2)), str(int(duration))],
            timeout=max(self.timeout, duration / 1000 + 3),
        )

    def swipe_custom(self, start, end, y_scale=1.0, duration=250, screen_h=None, margin=8):
        """
        Vuốt (x, y) start -> end với quãng đường trục Y được NHÂN `y_scale` quanh điểm giữa của đoạn vuốt
        (dùng cuộn menu Búa: y_scale=2 -> quãng đường gấp đôi, cuộn danh sách nhanh gấp đôi).

        - Giữ nguyên hướng vuốt, tâm đoạn vuốt, và trục X; Y đầu/cuối bị kẹp trong [margin, screen_h - margin]
          (không có nó, vuốt gấp đôi có thể rơi ra ngoài màn hình và bị bỏ qua).
        - duration mặc định 250ms (khoảng 200-300ms cho cuộn nhanh); tự kẹp trong [100, 1500].
        - Nếu sau khi kẹp mà quãng đường ngắn hơn yêu cầu thì vẫn vuốt hết mức màn hình cho phép.
        :return: (x1, y1, x2, y2) thực tế đã vuốt (để ghi log / kiểm tra).
        """
        x1, y1 = int(start[0]), int(start[1])
        x2, y2 = int(end[0]), int(end[1])
        h = int(screen_h or (self._last_screen.shape[0] if self._last_screen is not None else 540))
        mid = (y1 + y2) / 2.0
        half = abs(y2 - y1) / 2.0 * max(0.1, float(y_scale))
        sign = 1 if y2 >= y1 else -1
        ny1, ny2 = mid - sign * half, mid + sign * half
        lo, hi = margin, max(margin + 1, h - margin)
        if min(ny1, ny2) < lo or max(ny1, ny2) > hi:     # lệch ra ngoài -> dịch cả đoạn vào trong, rồi mới kẹp
            shift = lo - min(ny1, ny2) if min(ny1, ny2) < lo else hi - max(ny1, ny2)
            ny1, ny2 = ny1 + shift, ny2 + shift
        ny1, ny2 = int(min(hi, max(lo, ny1))), int(min(hi, max(lo, ny2)))
        self.swipe(x1, ny1, x2, ny2, duration=int(min(1500, max(100, duration))))
        return (x1, ny1, x2, ny2)

    # ------------------------------------------------------------------
    # Zoom out (thu nhỏ làng) - không phụ thuộc `getevent -p`
    # ------------------------------------------------------------------
    def pinch_parallel(self, a, b, duration=600):
        """
        Chụm 2 ngón bằng 2 luồng Thread chạy 2 lệnh `adb shell input swipe` SONG SONG.
        Cả 2 luồng chờ nhau ở một Barrier rồi mới xuất phát để lệch thời gian nhỏ nhất.

        :param a: (x1, y1, x2, y2) ngón thứ nhất, ví dụ (200, 270, 430, 270) (hệ 960x540).
        :param b: (x1, y1, x2, y2) ngón thứ hai,  ví dụ (760, 270, 530, 270).
        :param duration: thời gian vuốt (ms).
        :return: True nếu cả 2 lệnh adb đều chạy xong với returncode == 0. KHÔNG ném lỗi.
        """
        self.invalidate_screen_cache()
        results = {}
        barrier = threading.Barrier(2)

        def worker(key, pts):
            try:
                try:
                    barrier.wait(timeout=5)
                except threading.BrokenBarrierError:
                    pass
                r = self._run(
                    ["shell", "input", "swipe",
                     str(int(pts[0])), str(int(pts[1])), str(int(pts[2])), str(int(pts[3])),
                     str(int(duration))],
                    timeout=max(self.timeout, duration / 1000 + 5),
                )
                results[key] = (r.returncode == 0)
            except Exception:
                results[key] = False

        try:
            threads = [threading.Thread(target=worker, args=("a", a), daemon=True),
                       threading.Thread(target=worker, args=("b", b), daemon=True)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=max(self.timeout, duration / 1000 + 5) + 2)
            return bool(results.get("a")) and bool(results.get("b"))
        except Exception:
            return False

    def ctrl_wheel_background(self, notches=10, process_names=("dnplayer.exe",), direction=-1):
        """
        Ctrl + cuộn chuột gửi NGẦM vào cửa sổ LDPlayer bằng PostMessage (không nhấc cửa sổ lên, không chiếm con trỏ
        chuột thật, không giữ phím Ctrl thật). Chỉ Windows. Gửi vào cửa sổ con lớn nhất (vùng hiển thị game) rồi
        cửa sổ chính. KHÔNG ném lỗi; kết quả mô tả ở self.last_zoom_window. :return: True nếu đã gửi.
        """
        if os.name != "nt":
            return False
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.QueryFullProcessImageNameW.argtypes = [
                wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
            wanted = [n.lower() for n in process_names]

            def exe_of(hwnd):
                pid = wintypes.DWORD(0)
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                h = kernel32.OpenProcess(0x1000, False, pid.value)
                if not h:
                    return ""
                try:
                    buf = ctypes.create_unicode_buffer(520)
                    size = wintypes.DWORD(520)
                    if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                        return os.path.basename(buf.value).lower()
                    return ""
                finally:
                    kernel32.CloseHandle(h)

            def area(hwnd):
                rc = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rc))
                return max(0, rc.right - rc.left) * max(0, rc.bottom - rc.top)

            mains, children = [], []
            enum_top = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

            def on_top(hwnd, _l):
                try:
                    if user32.IsWindowVisible(hwnd) and exe_of(hwnd) in wanted:
                        mains.append(hwnd)
                except Exception:
                    pass
                return True

            user32.EnumWindows(enum_top(on_top), 0)
            if not mains:
                self.last_zoom_window = "KHÔNG thấy cửa sổ " + "/".join(process_names)
                return False
            main = max(mains, key=area)

            def on_child(hwnd, _l):
                children.append(hwnd)
                return True

            user32.EnumChildWindows(main, enum_top(on_child), 0)
            targets = sorted(children, key=area, reverse=True)[:2] + [main]

            VK_CONTROL, WM_KEYDOWN, WM_KEYUP, WM_MOUSEWHEEL, MK_CONTROL = 0x11, 0x0100, 0x0101, 0x020A, 0x0008
            delta = (-120 if direction < 0 else 120) & 0xFFFF
            sent = 0
            for hwnd in targets:
                rc = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rc))
                cx, cy = (rc.left + rc.right) // 2, (rc.top + rc.bottom) // 2
                lparam = ((cy & 0xFFFF) << 16) | (cx & 0xFFFF)
                wparam = (delta << 16) | MK_CONTROL
                user32.PostMessageW(hwnd, WM_KEYDOWN, VK_CONTROL, 0x001D0001)
                for _ in range(max(1, int(notches))):
                    user32.PostMessageW(hwnd, WM_MOUSEWHEEL, wparam, lparam)
                    time.sleep(0.05)
                user32.PostMessageW(hwnd, WM_KEYUP, VK_CONTROL, 0xC01D0001)
                sent += 1
            self.last_zoom_window = f"đã gửi vào {sent} cửa sổ của dnplayer.exe (cửa sổ chính {main:#x})"
            return True
        except Exception as e:
            self.last_zoom_window = f"lỗi: {e}"
            return False
        finally:
            self.invalidate_screen_cache()

    def ctrl_scroll_zoom_out(self, notches=10, window_keywords=("LDPlayer",),
                             process_names=("dnplayer.exe",), direction=-1):
        """
        Mô phỏng giữ Ctrl + cuộn chuột trên cửa sổ LDPlayer (chỉ Windows, dùng ctypes).
        Cửa sổ giả lập được nhận diện theo TIẾN TRÌNH (mặc định dnplayer.exe), không chỉ theo
        tiêu đề, để không gửi nhầm vào cửa sổ bot / thư mục LDPlayer. Không thấy tiến trình thì
        mới dò theo từ khóa tiêu đề (loại trừ cửa sổ của chính tiến trình Python này).
        Phương án này đưa cửa sổ LDPlayer lên trên cùng và di chuyển con trỏ chuột thật trong
        vài giây (xong trả con trỏ về chỗ cũ).

        :param notches: số nấc cuộn.
        :param direction: -1 = cuộn xuống (mặc định), +1 = cuộn lên (nếu máy bạn zoom ngược).
        :return: True nếu đã gửi xong thao tác. KHÔNG ném lỗi. Cửa sổ đã chọn lưu ở self.last_zoom_window.
        """
        if os.name != "nt":
            return False
        # Mặc định TẮT: phương án này cướp con trỏ chuột thật. Chỉ chạy khi config.json bật
        # ZOOM_ENABLE_CTRL_WHEEL = true (zoom ngầm qua ADB là cách chính).
        try:
            from config import load_json_config
            if not load_json_config().get("ZOOM_ENABLE_CTRL_WHEEL", False):
                self.last_zoom_window = "Ctrl + cuộn chuột đang TẮT (ZOOM_ENABLE_CTRL_WHEEL = false)"
                return False
        except Exception:
            return False
        ctrl_down = False
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            try:
                user32.SetProcessDPIAware()   # tránh lệch tọa độ khi Windows scale màn hình
            except Exception:
                pass
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.QueryFullProcessImageNameW.argtypes = [
                wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]

            keywords = [k.lower() for k in window_keywords]
            wanted_exes = [n.lower() for n in process_names]
            my_pid = os.getpid()

            def _exe_and_pid(hwnd):
                pid = wintypes.DWORD(0)
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                handle = kernel32.OpenProcess(0x1000, False, pid.value)   # QUERY_LIMITED_INFORMATION
                if not handle:
                    return "", pid.value
                try:
                    buf = ctypes.create_unicode_buffer(520)
                    size = wintypes.DWORD(520)
                    if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                        return os.path.basename(buf.value).lower(), pid.value
                    return "", pid.value
                finally:
                    kernel32.CloseHandle(handle)

            def _area(hwnd):
                rc = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rc))
                return max(0, rc.right - rc.left) * max(0, rc.bottom - rc.top)

            by_exe, by_title = [], []
            enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

            def _collect(hwnd, _lparam):
                try:
                    if not user32.IsWindowVisible(hwnd):
                        return True
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length <= 0:
                        return True
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    title = buf.value
                    exe, pid = _exe_and_pid(hwnd)
                    if exe in wanted_exes:
                        by_exe.append((hwnd, title, exe))
                    elif pid != my_pid and any(k in title.lower() for k in keywords):
                        by_title.append((hwnd, title, exe))
                except Exception:
                    pass
                return True

            user32.EnumWindows(enum_proc(_collect), 0)
            candidates = by_exe or by_title
            if not candidates:
                self.last_zoom_window = "KHÔNG tìm thấy cửa sổ dnplayer.exe / tiêu đề chứa " + "/".join(window_keywords)
                return False

            hwnd, title, exe = max(candidates, key=lambda c: _area(c[0]))
            self.last_zoom_window = f"'{title}' ({exe or 'không rõ tiến trình'})"

            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)        # SW_RESTORE
            # Mẹo Alt: Windows chỉ cho SetForegroundWindow khi vừa có thao tác phím
            user32.keybd_event(0x12, 0x38, 0, 0)
            user32.keybd_event(0x12, 0x38, 0x0002, 0)
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.5)

            rc = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rc))
            # Trỏ chuột vào giữa vùng game (lệch trái một chút vì bên phải có thanh công cụ LDPlayer)
            cx = rc.left + int((rc.right - rc.left) * 0.45)
            cy = (rc.top + rc.bottom) // 2

            old = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(old))
            user32.SetCursorPos(cx, cy)
            time.sleep(0.15)
            user32.mouse_event(0x0001, 1, 0, 0, 0)    # nhích chuột 1px để cửa sổ nhận hover
            user32.mouse_event(0x0001, -1, 0, 0, 0)
            time.sleep(0.15)

            VK_CONTROL, SC_CTRL, KEYUP, WHEEL = 0x11, 0x1D, 0x0002, 0x0800
            delta = -120 if direction < 0 else 120
            user32.keybd_event(VK_CONTROL, SC_CTRL, 0, 0)    # giữ Ctrl (kèm scan code)
            ctrl_down = True
            time.sleep(0.25)
            for _ in range(max(1, int(notches))):
                user32.mouse_event(WHEEL, 0, 0, delta, 0)    # 1 nấc cuộn
                time.sleep(0.12)
            time.sleep(0.3)
            user32.keybd_event(VK_CONTROL, SC_CTRL, KEYUP, 0)
            ctrl_down = False
            user32.SetCursorPos(old.x, old.y)
            return True
        except Exception as e:
            self.last_zoom_window = f"lỗi: {e}"
            return False
        finally:
            self.invalidate_screen_cache()   # sau zoom phải chụp ảnh mới để so sánh trước/sau
            if ctrl_down:   # đảm bảo không kẹt phím Ctrl nếu lỗi giữa chừng
                try:
                    import ctypes
                    ctypes.windll.user32.keybd_event(0x11, 0x1D, 0x0002, 0)
                except Exception:
                    pass