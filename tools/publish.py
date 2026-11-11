#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/publish.py - CÔNG CỤ RIÊNG CỦA BẠN (không đưa vào bản phát hành).

  python tools/publish.py release [--version 1.1.0] [--skip-build]
      build -> nén zip -> ký -> tạo GitHub Release và tải zip + .sig lên (dùng GitHub CLI `gh`)

  python tools/publish.py grant XXXX-XXXX-XXXX-XXXX-XXXX [--days 365 | --expires 2027-12-31] [--name "Khách A"] [--no-push]
      cấp license cho 1 mã máy -> chép vào licenses/ -> git commit + push (khách tự kích hoạt)

Yêu cầu một lần: cài Git, cài GitHub CLI (https://cli.github.com) rồi chạy `gh auth login`.
Thư mục dự án phải là git repo của https://github.com/nampham27122510-code/coc-auto-bot
(hoặc đặt biến môi trường COC_LICENSE_REPO trỏ tới bản clone của repo đó).
Lệnh `grant` CHỈ commit thư mục licenses/ nên không bao giờ đẩy nhầm khóa riêng tư.
"""
import argparse
import datetime
import os
import shutil
import subprocess
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
PROJECT = TOOLS_DIR.parent
sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(PROJECT))

import keygen  # noqa: E402


def die(msg):
    print(f"\n[LỖI] {msg}", file=sys.stderr)
    sys.exit(1)


def run(cmd, cwd=None, check=True):
    print("  $ " + " ".join(str(c) for c in cmd), flush=True)
    r = subprocess.run(cmd, cwd=cwd)
    if check and r.returncode != 0:
        die(f"Lệnh thất bại: {' '.join(str(c) for c in cmd)}")
    return r.returncode


def need(tool, hint):
    if shutil.which(tool) is None:
        die(f"Chưa cài '{tool}'. {hint}")


# ---------------------------------------------------------------------------------------------
def cmd_release(args):
    need("gh", "Cài GitHub CLI tại https://cli.github.com rồi chạy: gh auth login")
    import build_release as br

    version = br.detect_version(args.version)
    build_cmd = [sys.executable, str(PROJECT / "build_release.py"), "--version", version]
    if args.skip_build:
        build_cmd.append("--skip-build")
    print(f"▶ Build + đóng gói + ký v{version}")
    run(build_cmd, cwd=PROJECT)

    zip_path = PROJECT / "release" / f"{br.APP_NAME}_v{version}.zip"
    sig_path = zip_path.with_name(zip_path.name + ".sig")
    for f in (zip_path, sig_path):
        if not f.is_file():
            die(f"Thiếu {f} (build chưa ra file).")

    print(f"▶ Tạo GitHub Release v{version}")
    notes = args.notes or f"Phiên bản {version}"
    run(["gh", "release", "create", f"v{version}", str(zip_path), str(sig_path),
         "--title", f"v{version}", "--notes", notes], cwd=PROJECT)
    print(f"\n✔ Đã phát hành v{version}")


def _repo_dir():
    d = Path(os.environ.get("COC_LICENSE_REPO") or PROJECT)
    if not (d / ".git").exists():
        die(f"{d} không phải git repo.\n  -> chạy `git init`/`git clone` hoặc đặt COC_LICENSE_REPO.")
    return d


def cmd_grant(args):
    need("git", "Cài Git rồi thử lại.")
    repo = _repo_dir()

    # 1. Cấp license (ghi tools/issued/... và tools/issued/issued.csv như bình thường)
    ns = argparse.Namespace(machine=args.machine, days=args.days, expires=args.expires,
                            name=args.name, out=None, key=None)
    try:
        keygen.cmd_issue(ns)
        machine = keygen._norm_machine(args.machine)
    except keygen.KeygenError as e:
        die(str(e))

    # 2. Chép file công khai vào licenses/ của repo
    slug = keygen.license_slug(machine)
    src = keygen.ISSUED_DIR / "public" / f"{slug}.key"
    dst_dir = repo / "licenses"
    dst_dir.mkdir(exist_ok=True)
    shutil.copyfile(src, dst_dir / src.name)

    # 3. Commit CHỈ thư mục licenses/ rồi push
    rel = f"licenses/{src.name}"
    run(["git", "add", rel], cwd=repo)
    if run(["git", "diff", "--cached", "--quiet", "--", rel], cwd=repo, check=False) == 0:
        print("ℹ License không đổi so với bản đã có trên repo, không cần commit.")
    else:
        run(["git", "commit", "-m", f"cap license {machine}", "--", rel], cwd=repo)
    if args.no_push:
        print("⏸ --no-push: chưa đẩy lên GitHub. Tự chạy `git push` khi sẵn sàng.")
        return
    run(["git", "push"], cwd=repo)
    print(f"\n✔ Xong. Khách {machine} sẽ tự kích hoạt sau tối đa ~5 phút (GitHub cache).")


def main():
    ap = argparse.ArgumentParser(description="Phát hành bản mới & cấp license nhanh")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("release", help="build + zip + ký + tạo GitHub Release")
    p.add_argument("--version")
    p.add_argument("--skip-build", action="store_true")
    p.add_argument("--notes", help="ghi chú phát hành")
    p.set_defaults(func=cmd_release)

    p = sub.add_parser("grant", help="cấp license cho 1 mã máy và đẩy lên GitHub")
    p.add_argument("machine")
    p.add_argument("--days", type=int)
    p.add_argument("--expires")
    p.add_argument("--name", default="")
    p.add_argument("--no-push", action="store_true")
    p.set_defaults(func=cmd_grant)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()