# -*- coding: utf-8 -*-
"""
core/license.py - Bản quyền OFFLINE gắn với từng máy (Ed25519).

  * Khóa công khai (PUBLIC_KEY_B64) nhúng ngay trong phần mềm. Khóa riêng tư chỉ nằm trên máy bạn
    (tools/secret/private_key.pem) và KHÔNG BAO GIỜ được đóng vào bản phát hành.
  * Mã máy = hash(MachineGuid Windows + serial ổ đĩa hệ thống + tên CPU).
  * license.key (JSON) = {"payload": <base64 JSON>, "signature": <base64 chữ ký>}.
    payload chứa: app, machine_id, issued, expires (hoặc null = vĩnh viễn), name.

Chạy `python tools/keygen.py init` một lần để sinh cặp khóa và tự dán khóa công khai vào đây.
"""
import base64
import ctypes
import datetime
import hashlib
import json
import os
import platform
import re
import shutil
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# <<< `python tools/keygen.py init` sẽ tự điền dòng này. Rỗng = KHÔNG ai chạy được (fail-closed). >>>
PUBLIC_KEY_B64 = "aYMYxDcUsvcxbk5VrXS6TyyuLFlnixkpuCFhSgWE2do="

APP_ID = "COC_Auto_Bot"

# Thư mục trên máy chủ chứa các file license đã ký (KHÔNG có dấu "/" ở cuối). Ví dụ GitHub:
#   https://raw.githubusercontent.com/<user>/<repo>/main/licenses
# Rỗng = tắt tính năng tự tải (chỉ còn dán key / chọn file thủ công).
LICENSE_SERVER_URL = "https://raw.githubusercontent.com/nampham27122510-code/coc-auto-bot/main/licenses"
LICENSE_FILENAME = "license.key"
# Tiền tố "miền" cho chữ ký: chữ ký của license không thể bị đem dùng làm chữ ký của bản cập nhật và ngược lại.
# (PHẢI giống hệt trong tools/keygen.py)
LICENSE_DOMAIN = b"COC-LICENSE\n"
UPDATE_DOMAIN = b"COC-UPDATE\n"


class LicenseError(Exception):
    pass


@dataclass
class LicenseStatus:
    ok: bool
    reason: str = ""
    machine_id: str = ""
    expires: Optional[str] = None
    name: str = ""


def app_dir() -> Path:
    """Thư mục chứa .exe (khi đã đóng gói) hoặc thư mục gốc dự án (khi chạy từ mã nguồn)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


# ----------------------------------------------------------------------------------------------
# MÃ MÁY
# ----------------------------------------------------------------------------------------------
def _machine_guid() -> str:
    try:
        import winreg
        flags = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0, flags) as key:
            return str(winreg.QueryValueEx(key, "MachineGuid")[0]).strip().lower()
    except Exception:
        pass
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):   # Linux (chỉ để chạy thử)
        try:
            return Path(p).read_text().strip().lower()
        except Exception:
            continue
    return ""


def _volume_serial() -> str:
    """Serial của ổ đĩa hệ thống (không cần wmic/PowerShell nên không nháy cửa sổ console)."""
    if os.name != "nt":
        return ""
    try:
        serial = ctypes.c_uint32(0)
        root = os.environ.get("SystemDrive", "C:") + "\\"
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(root), None, 0, ctypes.byref(serial), None, None, None, 0)
        return f"{serial.value:08X}" if ok else ""
    except Exception:
        return ""


def _cpu_name() -> str:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
            return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip().lower()
    except Exception:
        pass
    try:
        return platform.processor().strip().lower()
    except Exception:
        return ""


_machine_id_cache = None


def get_machine_id() -> str:
    """Mã máy dạng XXXX-XXXX-XXXX-XXXX-XXXX (20 ký tự hex)."""
    global _machine_id_cache
    if _machine_id_cache:
        return _machine_id_cache
    parts = [_machine_guid(), _volume_serial(), _cpu_name()]
    if not any(parts):
        parts = [f"{uuid.getnode():012x}"]
    digest = hashlib.sha256(("COCBOT|" + "|".join(parts)).encode("utf-8")).hexdigest()[:20].upper()
    _machine_id_cache = "-".join(digest[i:i + 4] for i in range(0, 20, 4))
    return _machine_id_cache


def normalize_machine_id(text: str) -> str:
    return re.sub(r"[^0-9A-F]", "", str(text).upper())


# ----------------------------------------------------------------------------------------------
# CHỮ KÝ
# ----------------------------------------------------------------------------------------------
def _load_public_key():
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as e:
        raise LicenseError("Thiếu thư viện 'cryptography' (pip install cryptography).") from e
    if not PUBLIC_KEY_B64.strip():
        raise LicenseError("Bản build này chưa nhúng khóa công khai (chạy tools/keygen.py init rồi build lại).")
    try:
        return Ed25519PublicKey.from_public_bytes(base64.b64decode(PUBLIC_KEY_B64.strip(), validate=True))
    except Exception as e:
        raise LicenseError("Khóa công khai nhúng trong phần mềm không hợp lệ.") from e


def verify_signature(message: bytes, signature_b64: str) -> bool:
    """True nếu `signature_b64` là chữ ký hợp lệ của `message` bằng khóa riêng tư tương ứng."""
    from cryptography.exceptions import InvalidSignature
    key = _load_public_key()
    try:
        key.verify(base64.b64decode(signature_b64, validate=True), message)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


# ----------------------------------------------------------------------------------------------
# KIỂM TRA LICENSE
# ----------------------------------------------------------------------------------------------
def check_license(path=None) -> LicenseStatus:
    """Kiểm tra license.key (mặc định: cạnh phần mềm). Không bao giờ ném lỗi."""
    mid = get_machine_id()
    p = Path(path) if path else app_dir() / LICENSE_FILENAME
    if not p.is_file():
        return LicenseStatus(False, f"Chưa có file {LICENSE_FILENAME} cạnh phần mềm.", mid)

    try:
        raw = json.loads(p.read_text(encoding="utf-8-sig"))
        payload_bytes = base64.b64decode(raw["payload"], validate=True)
        signature = raw["signature"]
        payload = json.loads(payload_bytes.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("payload không phải object")
    except Exception:
        return LicenseStatus(False, f"File {LICENSE_FILENAME} bị hỏng hoặc sai định dạng.", mid)

    try:
        if not verify_signature(LICENSE_DOMAIN + payload_bytes, signature):
            return LicenseStatus(False, "Chữ ký license không hợp lệ (file bị sửa hoặc không phải của nhà phát hành).", mid)
    except LicenseError as e:
        return LicenseStatus(False, str(e), mid)
    except Exception as e:
        return LicenseStatus(False, f"Không kiểm tra được chữ ký: {e}", mid)

    if payload.get("app") != APP_ID:
        return LicenseStatus(False, "License này không dành cho phần mềm này.", mid)
    if normalize_machine_id(payload.get("machine_id", "")) != normalize_machine_id(mid):
        return LicenseStatus(False, "License này được cấp cho một máy khác.", mid)

    expires = payload.get("expires")
    if expires:
        try:
            exp_date = datetime.date.fromisoformat(str(expires))
        except ValueError:
            return LicenseStatus(False, "Ngày hết hạn trong license không hợp lệ.", mid)
        if datetime.date.today() > exp_date:
            return LicenseStatus(False, f"License đã hết hạn từ {expires}.", mid, expires)

    return LicenseStatus(True, "OK", mid, expires, str(payload.get("name") or ""))


def install_license(src_path) -> LicenseStatus:
    """Kiểm tra file người dùng chọn; hợp lệ thì chép thành license.key cạnh phần mềm."""
    status = check_license(src_path)
    if not status.ok:
        return status
    dst = app_dir() / LICENSE_FILENAME
    try:
        if Path(src_path).resolve() != dst.resolve():
            shutil.copyfile(src_path, dst)
    except Exception as e:
        return LicenseStatus(False, f"Không chép được license vào thư mục phần mềm: {e}", status.machine_id)
    return check_license()


# ----------------------------------------------------------------------------------------------
# TỰ TẢI LICENSE TỪ MÁY CHỦ
# ----------------------------------------------------------------------------------------------
def license_slug(machine_id=None) -> str:
    """Tên file trên server = hash của mã máy (người ngoài không dò/đoán được danh sách khách).
    PHẢI giống hệt hàm cùng tên trong tools/keygen.py."""
    mid = normalize_machine_id(machine_id or get_machine_id())
    return hashlib.sha256(("COCLIC|" + mid).encode("utf-8")).hexdigest()[:32]


def fetch_license_online(timeout=8) -> Optional[LicenseStatus]:
    """Thử tải license của máy này từ server. Trả về None nếu chưa có / lỗi mạng / tắt tính năng.
    File tải về vẫn phải qua install_license() (xác minh chữ ký + mã máy + hạn) mới được lưu."""
    base = LICENSE_SERVER_URL.strip().rstrip("/")
    if not base:
        return None
    import tempfile
    import urllib.request
    url = f"{base}/{license_slug()}.key"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "COCBot-License", "Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(65536)
    except Exception:
        return None
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=".key")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return install_license(tmp)
    except Exception:
        return None
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass