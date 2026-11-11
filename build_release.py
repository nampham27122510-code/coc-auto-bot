#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_release.py - Đóng gói COC Auto Bot thành file .zip phát hành / cập nhật.

Đặt cạnh main.py, chạy:
    python build_release.py                 # build PyInstaller rồi đóng zip
    python build_release.py --skip-build    # dùng sẵn thư mục dist/ (đã build trước đó)
    python build_release.py --version 1.4.0 # tự đặt số phiên bản
    python build_release.py --no-sign       # KHÔNG ký (chỉ để thử; app sẽ từ chối bản này khi cập nhật)

Kết quả: release/COC_Auto_Bot_v<phiên bản>.zip + .sha256 + .zip.sig (chữ ký Ed25519 bằng tools/secret/private_key.pem).
Khi phát hành trên GitHub: tag = v<phiên bản>, đính kèm CẢ .zip VÀ .zip.sig.

NGUYÊN TẮC
  * Gói được lấy từ kết quả build (dist/), KHÔNG lấy từ thư mục mã nguồn -> không lọt .py, venv, .git...
  * config.json (và .bak/.prev/.tmp) KHÔNG BAO GIỜ nằm trong zip: file này do app tự tạo cạnh .exe
    và là của người dùng; zip cập nhật không được đụng tới nó.
  * Thư viện cần để chạy nằm nguyên trong kết quả PyInstaller; script chỉ bỏ file rác chắc chắn không dùng.
"""
import argparse
import datetime
import fnmatch
import hashlib
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

APP_NAME = "COC_Auto_Bot"
PROJECT = Path(__file__).resolve().parent
SPEC_FILE = PROJECT / f"{APP_NAME}.spec"
DIST_DIR = PROJECT / "dist"
STAGE_DIR = PROJECT / "build_release_stage"
OUT_DIR = PROJECT / "release"

# ----------------------------------------------------------------------------
# DANH SÁCH LOẠI TRỪ (sửa tại đây nếu cần)
# ----------------------------------------------------------------------------
# 1. File của người dùng: tuyệt đối không đóng gói (so khớp tên file, ở mọi cấp thư mục)
# (cũng gồm license.key và mọi thứ liên quan khóa riêng tư: không bao giờ được lọt vào gói)
USER_DATA_GLOBS = ["config.json", "config.json.*", "license.key", "*.pem", "private_key*", "keygen.py"]

# 2. Rác sinh ra khi phát triển / chạy thử (tên file hoặc thư mục, ở mọi cấp)
JUNK_DIR_NAMES = {"__pycache__", ".git", ".idea", ".vscode", ".pytest_cache", ".mypy_cache"}
JUNK_FILE_GLOBS = ["*.pdb", "*.log", "*.tmp", "*.bak", "*.prev", "*.orig", "*.rej",
                   ".DS_Store", "Thumbs.db", ".gitignore", "*.zip", "*.sha256"]

# 3. Chỉ loại ở THƯ MỤC GỐC của gói (cạnh .exe): dữ liệu sinh ra khi chạy trên máy dev
ROOT_EXCLUDE_NAMES = {"logs", "log", "debug", "debug_screens", "collected_data", "collect_data",
                      "dataset", "samples", "screenshots", "venv", ".venv", "env", "build", "dist",
                      "tools", "secret", "issued", "release", "build_release_stage", "_update"}

# 4. Chỉ thêm từ thư mục dự án nếu kết quả build chưa có (ảnh mẫu OpenCV, tài liệu)
EXTRA_FROM_PROJECT = ["assets", "README.md", "config.default.json"]

# Ảnh mẫu tối thiểu phải có (theo note.txt) - thiếu thì cảnh báo to
REQUIRED_ASSETS = ["button_battle.png", "button_next.png", "button_return_home.png", "button_build.png"]


def log(msg):
    print(msg, flush=True)


def die(msg):
    print(f"\n[LỖI] {msg}", file=sys.stderr)
    sys.exit(1)


def _match_any(name, patterns):
    return any(fnmatch.fnmatch(name, p) for p in patterns)


def is_excluded(rel, is_dir):
    """rel: đường dẫn tương đối trong gói. True nếu phải bỏ."""
    parts = rel.parts
    name = parts[-1]
    if any(p in JUNK_DIR_NAMES for p in parts):
        return True
    if len(parts) == 1 and name.lower() in ROOT_EXCLUDE_NAMES and is_dir:
        return True
    if not is_dir and (_match_any(name, USER_DATA_GLOBS) or _match_any(name, JUNK_FILE_GLOBS)):
        return True
    return False


def detect_version(cli_version):
    if cli_version:
        return cli_version.lstrip("vV")
    updater = PROJECT / "core" / "updater.py"
    if updater.exists():
        text = updater.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"""(?:APP_VERSION|CURRENT_VERSION|__version__|VERSION)\s*=\s*['"]([^'"]+)['"]""", text)
        if m:
            return m.group(1).lstrip("vV")
    return datetime.date.today().strftime("%Y.%m.%d")


def run_pyinstaller():
    if not SPEC_FILE.exists():
        die(f"Không thấy {SPEC_FILE.name}. Đặt script cạnh file .spec hoặc dùng --skip-build.")
    log("▶ Build PyInstaller ...")
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(SPEC_FILE)]
    if subprocess.call(cmd, cwd=PROJECT) != 0:
        die("PyInstaller build thất bại.")


def locate_build():
    onedir = DIST_DIR / APP_NAME
    onefile = DIST_DIR / f"{APP_NAME}.exe"
    if onedir.is_dir():
        return "onedir", onedir
    if onefile.is_file():
        return "onefile", onefile
    die(f"Không thấy {onedir} hoặc {onefile}. Hãy build trước (bỏ --skip-build).")


def copy_filtered(src_root, dst_root):
    copied = skipped = 0
    for path in sorted(src_root.rglob("*")):
        rel = path.relative_to(src_root)
        if is_excluded(rel, path.is_dir()):
            skipped += 1
            continue
        target = dst_root / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            copied += 1
    return copied, skipped


def stage(kind, build_path):
    if STAGE_DIR.exists():
        shutil.rmtree(STAGE_DIR)
    STAGE_DIR.mkdir(parents=True)
    if kind == "onedir":
        copied, skipped = copy_filtered(build_path, STAGE_DIR)
    else:
        shutil.copy2(build_path, STAGE_DIR / build_path.name)
        copied, skipped = 1, 0
    log(f"  Đã chép {copied} file, bỏ {skipped} mục rác / dữ liệu người dùng")

    # Bổ sung assets / README nếu build chưa kèm theo
    for item in EXTRA_FROM_PROJECT:
        src = PROJECT / item
        if not src.exists():
            continue
        already = (STAGE_DIR / item).exists() or (STAGE_DIR / "_internal" / item).exists()
        if already:
            continue
        if src.is_dir():
            copy_filtered(src, STAGE_DIR / item)
        else:
            shutil.copy2(src, STAGE_DIR / item)
        log(f"  + Bổ sung '{item}' từ thư mục dự án (build chưa kèm)")


def verify_stage():
    exe = STAGE_DIR / f"{APP_NAME}.exe"
    if not exe.is_file():
        die(f"Thiếu {exe.name} trong gói.")
    leaked = [p for p in STAGE_DIR.rglob("*") if p.is_file() and _match_any(p.name, USER_DATA_GLOBS)]
    if leaked:
        die("File của người dùng / khóa bí mật lọt vào gói: " + ", ".join(str(p) for p in leaked))

    assets = next((d for d in (STAGE_DIR / "assets", STAGE_DIR / "_internal" / "assets") if d.is_dir()), None)
    if assets is None:
        log("  ⚠ CẢNH BÁO: không thấy thư mục assets/ trong gói - bot sẽ không nhận diện được nút!")
    else:
        missing = [a for a in REQUIRED_ASSETS if not (assets / a).exists()]
        n = len(list(assets.glob("*.png")))
        log(f"  assets: {assets.relative_to(STAGE_DIR)} ({n} ảnh png)")
        if missing:
            log("  ⚠ CẢNH BÁO: thiếu ảnh mẫu bắt buộc: " + ", ".join(missing))


def make_zip(version, wrap):
    OUT_DIR.mkdir(exist_ok=True)
    zip_path = OUT_DIR / f"{APP_NAME}_v{version}.zip"
    if zip_path.exists():
        zip_path.unlink()
    prefix = f"{APP_NAME}/" if wrap else ""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(STAGE_DIR.rglob("*")):
            if path.is_file():
                zf.write(path, prefix + path.relative_to(STAGE_DIR).as_posix())

    # Kiểm tra lại chính file zip vừa tạo
    with zipfile.ZipFile(zip_path) as zf:
        bad = zf.testzip()
        names = zf.namelist()
    if bad:
        die(f"Zip lỗi tại: {bad}")
    forbidden = [n for n in names if _match_any(n.rsplit("/", 1)[-1], USER_DATA_GLOBS)]
    if forbidden:
        die("Zip chứa file của người dùng / khóa bí mật: " + ", ".join(forbidden))

    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    (OUT_DIR / (zip_path.name + ".sha256")).write_text(f"{digest}  {zip_path.name}\n", encoding="utf-8")
    log(f"✔ {zip_path}  ({zip_path.stat().st_size / 1_048_576:.1f} MB, {len(names)} file)")
    log(f"  sha256: {digest}")
    return zip_path


def sign_release(zip_path, version):
    """Ký zip bằng khóa riêng tư -> release/<zip>.sig (app kiểm tra bằng khóa công khai trước khi cập nhật)."""
    sys.path.insert(0, str(PROJECT / "tools"))
    try:
        import keygen
        sig = keygen.sign_update(zip_path, version)
    except ImportError as e:
        die(f"Không nạp được tools/keygen.py ({e}). Cần `pip install cryptography`.")
    except keygen.KeygenError as e:
        die(f"Không ký được bản phát hành:\n  {e}\n  (dùng --no-sign nếu chỉ build thử)")
    log(f"✔ Chữ ký: {sig}")
    return sig


def main():
    ap = argparse.ArgumentParser(description="Đóng gói COC Auto Bot thành zip phát hành")
    ap.add_argument("--skip-build", action="store_true", help="không chạy PyInstaller, dùng dist/ có sẵn")
    ap.add_argument("--version", help="số phiên bản (mặc định: đọc từ core/updater.py, không có thì lấy ngày)")
    ap.add_argument("--wrap", action="store_true",
                    help=f"bọc mọi file trong thư mục '{APP_NAME}/' (mặc định: file nằm thẳng ở gốc zip)")
    ap.add_argument("--no-sign", action="store_true",
                    help="không ký zip (bản này KHÔNG dùng để phát hành: app sẽ từ chối cập nhật)")
    args = ap.parse_args()

    if not args.skip_build:
        run_pyinstaller()
    kind, build_path = locate_build()
    version = detect_version(args.version)
    log(f"▶ Đóng gói {APP_NAME} v{version} ({kind}) từ {build_path}")
    stage(kind, build_path)
    verify_stage()
    zip_path = make_zip(version, args.wrap)
    if args.no_sign:
        log("  ⚠ Bỏ qua ký (--no-sign): zip này không cập nhật được cho khách.")
    else:
        sign_release(zip_path, version)
    shutil.rmtree(STAGE_DIR, ignore_errors=True)


if __name__ == "__main__":
    main()