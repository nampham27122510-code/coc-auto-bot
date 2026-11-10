import os
import subprocess
from config import LDPLAYER_PATH


class EmulatorController:
    def __init__(self, index=0, ld_path=LDPLAYER_PATH):
        self.index = index
        self.ld_path = ld_path
        self.ldconsole = os.path.join(ld_path, "ldconsole.exe")
        self.adb_path = os.path.join(ld_path, "adb.exe")

    def _run(self, *args):
        """Chạy ldconsole.exe với danh sách tham số (không qua shell)."""
        return subprocess.run(
            [self.ldconsole, *[str(a) for a in args]],
            capture_output=True, text=True
        )

    def exists(self):
        return os.path.exists(self.ldconsole)

    def setup_and_launch(self, width=960, height=540, dpi=160, fps=30):
        """Cấu hình độ phân giải/FPS khi giả lập đang TẮT, sau đó mới khởi chạy."""
        print(f"[*] Cấu hình LDPlayer index {self.index}...")
        self._run("modify", "--index", self.index, "--resolution", f"{width},{height},{dpi}")
        self._run("modify", "--index", self.index, "--fps", fps)
        print(f"[+] Khởi chạy LDPlayer index {self.index}...")
        self._run("launch", "--index", self.index)

    def is_running(self):
        r = self._run("isrunning", "--index", self.index)
        return "running" in r.stdout.lower()

    def launch_game(self, package_name="com.supercell.clashofclans"):
        print(f"[+] Mở game {package_name}...")
        self._run("runapp", "--index", self.index, "--packagename", package_name)

    def close_game(self, package_name="com.supercell.clashofclans"):
        self._run("killapp", "--index", self.index, "--packagename", package_name)

    def close_emulator(self):
        print(f"[-] Tắt LDPlayer index {self.index}...")
        self._run("quit", "--index", self.index)
