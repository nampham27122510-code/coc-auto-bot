# -*- coding: utf-8 -*-
"""
core/updater.py - Cập nhật tự động CÓ BẢO VỆ.

  1. Chỉ kiểm tra/tải khi license hợp lệ.
  2. Mỗi bản phát hành gồm  <tên>.zip  +  <tên>.zip.sig  (do build_release.py ký bằng khóa riêng tư).
     File .sig chứa app + version + sha256 của zip + chữ ký. App kiểm tra bằng khóa công khai
     TRƯỚC khi giải nén bất cứ thứ gì.
  3. Chữ ký ràng buộc cả số phiên bản -> không thể đẩy bản cũ (có lỗi) lên làm "bản mới";
     chỉ nhận phiên bản LỚN HƠN bản đang chạy.
  4. Giải nén bằng Python vào thư mục tạm (chặn ../ và đường dẫn tuyệt đối), KHÔNG ghi đè
     config.json / license.key; sau khi app thoát, file .bat chép đè bằng robocopy rồi mở lại app.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import requests

from core.license import LicenseError, UPDATE_DOMAIN, app_dir, check_license, verify_signature

# Phiên bản hiện tại (TĂNG số này TRƯỚC khi build bản mới; build_release.py đọc dòng này để đặt tên zip)
CURRENT_VERSION = "v1.0.3"

APP_NAME = "COC_Auto_Bot"
REPO_API_URL = "https://api.github.com/repos/nampham27122510-code/coc-auto-bot/releases/latest"

UPDATE_DIR_NAME = "_update"
HTTP_TIMEOUT = (15, 30)                     # (kết nối, đọc) giây
MAX_ZIP_BYTES = 600 * 1024 * 1024           # từ chối file tải về quá lớn
MAX_UNPACKED_BYTES = 2 * 1024 * 1024 * 1024  # chống zip bomb


class UpdateError(Exception):
    pass


@dataclass
class UpdateInfo:
    version: str      # không có chữ "v", ví dụ "1.1.0"
    zip_name: str
    zip_url: str
    sig_url: str


# ----------------------------------------------------------------------------------------------
def _parse_version(text):
    nums = re.findall(r"\d+", str(text).strip().lstrip("vV").split("-")[0])
    return tuple(int(n) for n in nums[:4]) if nums else None


def is_newer(latest, current):
    a, b = _parse_version(latest), _parse_version(current)
    if a is None or b is None:
        return False
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def _is_protected(filename):
    """File của người dùng: bản cập nhật không bao giờ được ghi đè."""
    n = filename.lower()
    return n == "license.key" or n == "config.json" or n.startswith("config.json.")


# ----------------------------------------------------------------------------------------------
# 1. KIỂM TRA
# ----------------------------------------------------------------------------------------------
def check_for_updates():
    """Trả về UpdateInfo nếu có bản mới hơn, ngược lại None. Chưa có license hợp lệ -> không gọi mạng."""
    if not check_license().ok:
        return None
    try:
        resp = requests.get(REPO_API_URL, timeout=HTTP_TIMEOUT,
                            headers={"Accept": "application/vnd.github+json"})
        if resp.status_code != 200:
            print(f"Không thể kiểm tra cập nhật: HTTP {resp.status_code}")
            return None
        data = resp.json()
        tag = str(data.get("tag_name") or "")
        if data.get("draft") or data.get("prerelease") or not is_newer(tag, CURRENT_VERSION):
            return None

        assets = {a["name"]: a["browser_download_url"] for a in data.get("assets", [])
                  if a.get("name") and str(a.get("browser_download_url", "")).startswith("https://")}
        # Chỉ nhận zip có kèm file .sig đi cùng
        candidates = sorted(n for n in assets if n.lower().endswith(".zip") and (n + ".sig") in assets)
        if not candidates:
            print("Bản phát hành mới không có file .zip + .zip.sig -> bỏ qua.")
            return None
        name = candidates[0]
        return UpdateInfo(version=tag.lstrip("vV"), zip_name=name,
                          zip_url=assets[name], sig_url=assets[name + ".sig"])
    except Exception as e:
        print(f"Không thể kiểm tra cập nhật: {e}")
        return None


# ----------------------------------------------------------------------------------------------
# 2. TẢI + XÁC MINH + GIẢI NÉN
# ----------------------------------------------------------------------------------------------
def _download(url, dest, max_bytes, progress=None):
    try:
        with requests.get(url, stream=True, timeout=HTTP_TIMEOUT) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length") or 0)
            if total > max_bytes:
                raise UpdateError("File cập nhật quá lớn, từ chối tải.")
            done = 0
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    done += len(chunk)
                    if done > max_bytes:
                        raise UpdateError("File cập nhật quá lớn, từ chối tải.")
                    f.write(chunk)
                    if progress and total:
                        progress(min(done / total, 1.0))
    except requests.RequestException as e:
        raise UpdateError(f"Lỗi tải file cập nhật: {e}") from e


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def verify_package(zip_path, sig_path, expected_version):
    """Ném UpdateError nếu chữ ký/phiên bản/hash không khớp."""
    try:
        sig = json.loads(Path(sig_path).read_text(encoding="utf-8"))
        app, version = str(sig["app"]), str(sig["version"])
        sha, signature = str(sig["sha256"]).lower(), str(sig["signature"])
    except Exception as e:
        raise UpdateError("File chữ ký (.sig) bị hỏng.") from e

    message = UPDATE_DOMAIN + f"{app}|{version}|{sha}".encode("utf-8")
    try:
        valid = verify_signature(message, signature)
    except LicenseError as e:
        raise UpdateError(str(e)) from e
    if not valid:
        raise UpdateError("Chữ ký bản cập nhật KHÔNG hợp lệ -> từ chối cài đặt.")
    if app != APP_NAME:
        raise UpdateError("Bản cập nhật không dành cho phần mềm này.")
    if version.lstrip("vV") != str(expected_version).lstrip("vV"):
        raise UpdateError("Số phiên bản trong chữ ký không khớp bản phát hành.")
    if not is_newer(version, CURRENT_VERSION):
        raise UpdateError("Bản cập nhật không mới hơn bản đang chạy.")
    if _sha256_file(zip_path) != sha:
        raise UpdateError("File zip không khớp chữ ký (bị sửa hoặc tải lỗi).")


def _extract_safely(zip_path, stage):
    """Giải nén vào `stage`: chặn đường dẫn nguy hiểm, bỏ qua config.json / license.key."""
    stage.mkdir(parents=True, exist_ok=True)
    root = stage.resolve()
    try:
        with zipfile.ZipFile(zip_path) as zf:
            infos = zf.infolist()
            if sum(i.file_size for i in infos) > MAX_UNPACKED_BYTES:
                raise UpdateError("Nội dung giải nén quá lớn.")
            if zf.testzip():
                raise UpdateError("File zip bị lỗi.")
            for info in infos:
                if info.is_dir():
                    continue
                name = info.filename.replace("\\", "/")
                parts = PurePosixPath(name).parts
                if (not parts or name.startswith("/") or ".." in parts
                        or re.match(r"^[A-Za-z]:", parts[0])):
                    raise UpdateError(f"Đường dẫn nguy hiểm trong zip: {info.filename}")
                if _is_protected(parts[-1]):
                    continue
                target = (root.joinpath(*parts)).resolve()
                if root not in target.parents:
                    raise UpdateError(f"Đường dẫn nguy hiểm trong zip: {info.filename}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    except zipfile.BadZipFile as e:
        raise UpdateError("File zip bị lỗi.") from e

    # Gói có thể được bọc trong thư mục COC_Auto_Bot/ (build_release.py --wrap)
    exe_name = _exe_name()
    if not (root / exe_name).is_file():
        children = [c for c in root.iterdir()]
        if len(children) == 1 and children[0].is_dir() and (children[0] / exe_name).is_file():
            return children[0]
        raise UpdateError(f"Gói cập nhật không chứa {exe_name}.")
    return root


def _exe_name():
    return Path(sys.executable).name if getattr(sys, "frozen", False) else f"{APP_NAME}.exe"


# ----------------------------------------------------------------------------------------------
# 3. CÀI ĐẶT (script .bat chạy SAU KHI app thoát)
# ----------------------------------------------------------------------------------------------
_BAT = r"""@echo off
setlocal
set tries=0
:wait
tasklist /FI "PID eq %COC_PID%" 2>nul | find "%COC_PID%" >nul
if errorlevel 1 goto copy
set /a tries+=1
if %tries% GEQ 60 goto copy
timeout /t 1 /nobreak >nul
goto wait
:copy
robocopy "%COC_SRC%" "%COC_APP%" /E /XF config.json config.json.* license.key /R:10 /W:1 /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto fail
rmdir /s /q "%COC_UPD%"
goto launch
:fail
echo robocopy exit code %errorlevel% > "%COC_APP%\update_error.log"
:launch
start "" "%COC_APP%\%COC_EXE%"
(goto) 2>nul & del "%~f0"
"""


def launch_installer(source_dir, update_dir):
    """Tạo + chạy .bat tách rời. Sau khi gọi, CHƯƠNG TRÌNH GỌI PHẢI THOÁT NGAY."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        raise UpdateError("Chỉ tự cập nhật được khi chạy bản .exe trên Windows.")
    bat = Path(tempfile.gettempdir()) / f"coc_update_{os.getpid()}.bat"
    bat.write_text(_BAT, encoding="ascii")   # chỉ ASCII; mọi đường dẫn truyền qua biến môi trường (Unicode OK)

    env = dict(os.environ)
    env.update({
        "COC_PID": str(os.getpid()),
        "COC_SRC": str(source_dir),
        "COC_APP": str(app_dir()),
        "COC_UPD": str(update_dir),
        "COC_EXE": _exe_name(),
        "PYINSTALLER_RESET_ENVIRONMENT": "1",   # để exe mới (onefile) không dùng lại thư mục tạm của exe cũ
    })
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(["cmd.exe", "/c", str(bat)], env=env, cwd=str(app_dir()),
                     creationflags=flags, close_fds=True)


def apply_update(info, progress=None):
    """
    Tải -> xác minh chữ ký -> giải nén -> chạy bộ cài. KHÔNG tự thoát: gọi xong thì caller phải đóng app.
    Ném UpdateError nếu có vấn đề (app cũ vẫn nguyên vẹn).
    """
    if not check_license().ok:
        raise UpdateError("Cần license hợp lệ để cập nhật.")
    update_dir = app_dir() / UPDATE_DIR_NAME
    try:
        shutil.rmtree(update_dir, ignore_errors=True)
        update_dir.mkdir(parents=True)
        zip_path, sig_path = update_dir / "update.zip", update_dir / "update.zip.sig"
        _download(info.sig_url, sig_path, 64 * 1024)
        _download(info.zip_url, zip_path, MAX_ZIP_BYTES, progress)
        verify_package(zip_path, sig_path, info.version)       # <-- chặn mã độc: sai chữ ký là dừng
        source = _extract_safely(zip_path, update_dir / "stage")
        launch_installer(source, update_dir)
    except UpdateError:
        shutil.rmtree(update_dir, ignore_errors=True)
        raise
    except OSError as e:
        shutil.rmtree(update_dir, ignore_errors=True)
        raise UpdateError(f"Lỗi ghi file khi cập nhật: {e}") from e