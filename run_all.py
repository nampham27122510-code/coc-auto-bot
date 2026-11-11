#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_all.py - Chạy TOÀN BỘ quy trình phát hành COC Auto Bot bằng một lệnh. Đặt cạnh main.py.

    python run_all.py                       # hỏi số phiên bản, rồi: kiểm tra -> khóa -> build -> ký -> xác minh
    python run_all.py --bump patch          # tự tăng 1.0.0 -> 1.0.1 (minor: 1.1.0, major: 2.0.0)
    python run_all.py --version 1.2.0       # đặt số phiên bản cụ thể
    python run_all.py --skip-build          # dùng dist/ có sẵn (không chạy PyInstaller)
    python run_all.py --issue XXXX-XXXX-XXXX-XXXX-XXXX --days 365 --name "Khách A"   # cấp thêm license
    python run_all.py --publish             # sau cùng đăng lên GitHub Release bằng `gh` (cần cài GitHub CLI)

Các bước:
  1. Kiểm tra môi trường + file bắt buộc
  2. Khóa: chưa có thì tạo; có rồi thì kiểm tra khóa công khai trong core/license.py KHỚP khóa riêng tư
  3. Đặt số phiên bản trong core/updater.py
  4. Build PyInstaller + đóng zip + ký (gọi build_release.py)
  5. Xác minh lại zip/chữ ký đúng như app khách sẽ làm, và quét file không được phép lọt vào gói
  6. (tùy chọn) cấp license, đăng GitHub Release
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PROJECT = Path(__file__).resolve().parent
APP_NAME = "COC_Auto_Bot"
UPDATER_PY = PROJECT / "core" / "updater.py"
LICENSE_PY = PROJECT / "core" / "license.py"
KEYGEN_PY = PROJECT / "tools" / "keygen.py"
DEFAULT_KEY = PROJECT / "tools" / "secret" / "private_key.pem"
RELEASE_DIR = PROJECT / "release"

REQUIRED_FILES = ["main.py", "build_release.py", "core/__init__.py", "core/license.py", "core/updater.py",
                  "gui/__init__.py", "gui/license_window.py", "tools/keygen.py"]
FORBIDDEN_IN_ZIP = ("config.json", "license.key", "keygen.py", ".pem", "private_key")


def say(msg=""):
    print(msg, flush=True)


def step(n, title):
    say(f"\n{'=' * 64}\n  BƯỚC {n}: {title}\n{'=' * 64}")


def die(msg):
    say(f"\n[LỖI] {msg}")
    sys.exit(1)


def warn(msg):
    say(f"  ⚠ {msg}")


def ok(msg):
    say(f"  ✔ {msg}")


def run(cmd, **kw):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.call(cmd, cwd=PROJECT, env=env, **kw)


# ----------------------------------------------------------------------------------------------
def preflight(skip_build):
    step(1, "Kiểm tra môi trường")
    if sys.version_info < (3, 9):
        die("Cần Python 3.9 trở lên.")
    ok(f"Python {sys.version.split()[0]}")
    if os.name != "nt":
        warn("Không phải Windows: PyInstaller sẽ không ra file .exe cho khách.")

    missing = [f for f in REQUIRED_FILES if not (PROJECT / f).is_file()]
    if missing:
        die("Thiếu file: " + ", ".join(missing) + "\n  Hãy chép đủ các file mới vào đúng thư mục (xem sơ đồ).")
    ok("Đủ file mã nguồn")

    need = [("cryptography", "cryptography"), ("requests", "requests")]
    if not skip_build:
        need.append(("PyInstaller", "pyinstaller"))
        if not (PROJECT / f"{APP_NAME}.spec").is_file():
            die(f"Không thấy {APP_NAME}.spec (hoặc dùng --skip-build).")
    for mod, pip_name in need:
        try:
            __import__(mod)
        except ImportError:
            die(f"Thiếu thư viện '{mod}'. Chạy: pip install {pip_name}")
    ok("Đủ thư viện (cryptography, requests" + ("" if skip_build else ", PyInstaller") + ")")
    try:
        __import__("customtkinter")
    except ImportError:
        warn("Không import được customtkinter ở môi trường này (app vẫn cần nó để chạy).")

    gi = PROJECT / ".gitignore"
    text = gi.read_text(encoding="utf-8", errors="ignore") if gi.exists() else ""
    if "tools/secret" not in text:
        warn("`.gitignore` chưa có dòng `tools/secret/` -> NGUY CƠ commit nhầm khóa riêng tư lên GitHub!")


def _key_path():
    return Path(os.environ.get("COC_PRIVATE_KEY") or DEFAULT_KEY)


def _embedded_public_key():
    m = re.search(r'(?m)^PUBLIC_KEY_B64\s*=\s*"(.*)"', LICENSE_PY.read_text(encoding="utf-8"))
    if not m:
        die("Không thấy dòng PUBLIC_KEY_B64 trong core/license.py.")
    return m.group(1).strip()


def ensure_keys():
    step(2, "Khóa bảo mật")
    embedded = _embedded_public_key()
    kp = _key_path()
    if not kp.is_file():
        if embedded:
            die(f"core/license.py đã có khóa công khai nhưng KHÔNG thấy khóa riêng tư ({kp}).\n"
                f"  Khôi phục file đã sao lưu về đúng chỗ. Tạo khóa mới sẽ làm mọi license/bản cập nhật đã phát hành vô hiệu.")
        say("  Chưa có khóa -> tạo mới (chỉ làm một lần) ...")
        if run([sys.executable, str(KEYGEN_PY), "init"]) != 0:
            die("Tạo khóa thất bại.")
        warn(f"HÃY SAO LƯU {kp} ra nơi an toàn NGAY BÂY GIỜ.")
        embedded = _embedded_public_key()

    sys.path.insert(0, str(PROJECT / "tools"))
    import keygen
    try:
        pub = keygen.public_key_b64(keygen.load_private_key(None))
    except keygen.KeygenError as e:
        die(str(e))
    if not embedded:
        text = LICENSE_PY.read_text(encoding="utf-8")
        LICENSE_PY.write_text(re.sub(r'(?m)^PUBLIC_KEY_B64\s*=\s*".*"', f'PUBLIC_KEY_B64 = "{pub}"', text, count=1),
                              encoding="utf-8")
        ok("Đã dán khóa công khai vào core/license.py")
    elif embedded != pub:
        die("Khóa công khai trong core/license.py KHÔNG khớp khóa riêng tư.\n"
            "  Build xong khách sẽ không kích hoạt/cập nhật được. Kiểm tra bạn đang dùng đúng private_key.pem.")
    ok("Khóa riêng tư khớp khóa công khai nhúng trong app")


def _current_version():
    m = re.search(r'(?m)^CURRENT_VERSION\s*=\s*"v?([^"]+)"', UPDATER_PY.read_text(encoding="utf-8"))
    if not m:
        die('Không thấy dòng CURRENT_VERSION = "vX.Y.Z" trong core/updater.py.')
    return m.group(1)


def _bump(ver, kind):
    nums = [int(x) for x in re.findall(r"\d+", ver)[:3]]
    nums += [0] * (3 - len(nums))
    if kind == "major":
        nums = [nums[0] + 1, 0, 0]
    elif kind == "minor":
        nums = [nums[0], nums[1] + 1, 0]
    else:
        nums[2] += 1
    return ".".join(map(str, nums))


def set_version(args):
    step(3, "Số phiên bản")
    cur = _current_version()
    if args.version:
        new = args.version.lstrip("vV")
    elif args.bump:
        new = _bump(cur, args.bump)
    elif sys.stdin.isatty():
        ans = input(f"  Phiên bản hiện tại: {cur}. Nhập số mới (Enter = giữ nguyên, 'p' = tăng patch): ").strip()
        new = _bump(cur, "patch") if ans.lower() == "p" else (ans.lstrip("vV") or cur)
    else:
        new = cur
    if not re.fullmatch(r"\d+(\.\d+){1,3}", new):
        die(f"Số phiên bản không hợp lệ: {new}")
    if new != cur:
        text = UPDATER_PY.read_text(encoding="utf-8")
        UPDATER_PY.write_text(re.sub(r'(?m)^CURRENT_VERSION\s*=\s*".*"', f'CURRENT_VERSION = "v{new}"', text, count=1),
                              encoding="utf-8")
        ok(f"core/updater.py: v{cur} -> v{new}")
    else:
        ok(f"Giữ nguyên v{new}")
    return new


def build(version, skip_build):
    step(4, "Build + đóng zip + ký")
    cmd = [sys.executable, str(PROJECT / "build_release.py"), "--version", version]
    if skip_build:
        cmd.append("--skip-build")
    if run(cmd) != 0:
        die("build_release.py thất bại (xem thông báo phía trên).")


def verify(version):
    step(5, "Xác minh gói phát hành (như app khách sẽ làm)")
    import hashlib
    import json
    zip_path = RELEASE_DIR / f"{APP_NAME}_v{version}.zip"
    sig_path = zip_path.with_name(zip_path.name + ".sig")
    for p in (zip_path, sig_path):
        if not p.is_file():
            die(f"Thiếu {p.name} trong release/.")

    sys.path.insert(0, str(PROJECT))
    for mod in [m for m in sys.modules if m == "core" or m.startswith("core.")]:
        del sys.modules[mod]   # nạp lại license.py (khóa công khai vừa được dán)
    from core import license as lic

    sig = json.loads(sig_path.read_text(encoding="utf-8"))
    sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    message = lic.UPDATE_DOMAIN + f"{sig['app']}|{sig['version']}|{sig['sha256']}".encode("utf-8")
    if not lic.verify_signature(message, sig["signature"]):
        die("Chữ ký KHÔNG hợp lệ với khóa công khai của app.")
    if sig["sha256"] != sha:
        die("sha256 trong .sig không khớp file zip.")
    if sig["app"] != APP_NAME or sig["version"] != version:
        die("Tên app / phiên bản trong .sig không khớp.")
    ok("Chữ ký hợp lệ, sha256 khớp, đúng phiên bản")

    with zipfile.ZipFile(zip_path) as zf:
        if zf.testzip():
            die("Zip bị lỗi.")
        names = zf.namelist()
    bad = [n for n in names if any(tag in n.lower() for tag in FORBIDDEN_IN_ZIP)
           or n.lower().startswith(("tools/", f"{APP_NAME.lower()}/tools/"))]
    if bad:
        die("Zip chứa file KHÔNG được phép: " + ", ".join(bad))
    if not any(n.endswith(f"{APP_NAME}.exe") for n in names):
        die(f"Zip không có {APP_NAME}.exe.")
    ok(f"Zip sạch: {len(names)} file, không có config.json / license.key / khóa / tools")
    ok(f"{zip_path.stat().st_size / 1_048_576:.1f} MB")
    return zip_path, sig_path


def issue_license(args):
    step(6, "Cấp license")
    cmd = [sys.executable, str(KEYGEN_PY), "issue", "--machine", args.issue]
    if args.days:
        cmd += ["--days", str(args.days)]
    if args.name:
        cmd += ["--name", args.name]
    if run(cmd) != 0:
        die("Cấp license thất bại.")


def publish(version, zip_path, sig_path):
    step(7, "Đăng GitHub Release")
    if not shutil.which("gh"):
        warn("Không thấy GitHub CLI (`gh`). Tự đăng tay: tạo Release, tag "
             f"v{version}, đính kèm {zip_path.name} VÀ {sig_path.name}.")
        return
    cmd = ["gh", "release", "create", f"v{version}", str(zip_path), str(sig_path),
           "--title", f"v{version}", "--notes", f"Phiên bản {version}"]
    if run(cmd) != 0:
        warn("`gh release create` thất bại (đã đăng nhập `gh auth login` chưa? tag đã tồn tại?). Hãy đăng tay.")
    else:
        ok(f"Đã đăng release v{version}")


def main():
    ap = argparse.ArgumentParser(description="Chạy toàn bộ quy trình phát hành COC Auto Bot")
    ap.add_argument("--version", help="đặt số phiên bản, ví dụ 1.2.0")
    ap.add_argument("--bump", choices=["patch", "minor", "major"], help="tự tăng số phiên bản")
    ap.add_argument("--skip-build", action="store_true", help="không chạy PyInstaller, dùng dist/ có sẵn")
    ap.add_argument("--issue", metavar="MA_MAY", help="cấp license cho mã máy này sau khi build")
    ap.add_argument("--days", type=int, help="license hết hạn sau N ngày (bỏ trống = vĩnh viễn)")
    ap.add_argument("--name", help="ghi chú tên khách trong license")
    ap.add_argument("--publish", action="store_true", help="đăng lên GitHub Release bằng `gh`")
    args = ap.parse_args()

    preflight(args.skip_build)
    ensure_keys()
    version = set_version(args)
    build(version, args.skip_build)
    zip_path, sig_path = verify(version)
    if args.issue:
        issue_license(args)
    if args.publish:
        publish(version, zip_path, sig_path)

    say(f"\n{'=' * 64}\n  XONG - v{version}\n{'=' * 64}")
    say(f"  File phát hành (trong release/):\n    {zip_path.name}\n    {sig_path.name}")
    if not args.publish:
        say(f"  Đăng lên GitHub: tag v{version}, đính kèm CẢ HAI file trên.")
    say("  Nhớ: khách cần license.key (python tools/keygen.py issue ...) mới chạy và cập nhật được.")


if __name__ == "__main__":
    main()