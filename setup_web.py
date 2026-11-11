#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
setup_web.py - Tạo thư mục web Firebase (coc-web) + dọn dẹp dự án phần mềm. Đặt cạnh main.py.

    python setup_web.py --project-id ten-du-an-firebase
    python setup_web.py --project-id ten-du-an --web-dir D:\\coc-web --untrack

An toàn: chạy lại nhiều lần được, KHÔNG ghi đè file đã có, KHÔNG xóa file nào, KHÔNG đổi chỗ file mã nguồn .py.
  --dry-run   chỉ in ra việc sẽ làm
  --untrack   chạy `git rm --cached` cho file không nên nằm trên Git (file thật vẫn còn trên đĩa)
"""
import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PROJECT = Path(__file__).resolve().parent

GITIGNORE_LINES = [
    "# --- bí mật / khóa: TUYỆT ĐỐI không commit ---",
    "tools/secret/", "tools/issued/", "license.key", "*.pem",
    "# --- dữ liệu người dùng ---",
    "config.json", "config.json.bak", "config.json.prev",
    "# --- kết quả build / tạm ---",
    "build/", "dist/", "dist.zip", "release/", "build_release_stage/", "_update/",
    "__pycache__/", "*.pyc", "venv/", ".venv/",
]
REQUIREMENTS = ["cryptography", "requests"]
UNTRACK = ["dist.zip", "config.json", "config.json.bak", "config.json.prev"]


class Ctx:
    dry = False


def say(msg=""):
    print(msg, flush=True)


def write_new(path: Path, content: str):
    """Tạo file nếu chưa có (không ghi đè)."""
    if path.exists():
        say(f"  = đã có, giữ nguyên: {path}")
        return
    say(f"  + tạo: {path}")
    if Ctx.dry:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


# ----------------------------------------------------------------------------------------------
# PHẦN 1: DỌN DỰ ÁN PHẦN MỀM
# ----------------------------------------------------------------------------------------------
def organize_project(untrack):
    say("\n=== 1. SẮP XẾP DỰ ÁN PHẦN MỀM ===")

    # .gitignore: chỉ thêm dòng còn thiếu
    gi = PROJECT / ".gitignore"
    text = gi.read_text(encoding="utf-8", errors="ignore") if gi.exists() else ""
    have = {ln.strip() for ln in text.splitlines()}
    add = [ln for ln in GITIGNORE_LINES if ln not in have]
    real_add = [ln for ln in add if not ln.startswith("#")]
    if real_add:
        say(f"  + .gitignore: thêm {len(real_add)} dòng ({', '.join(real_add[:4])}...)")
        if not Ctx.dry:
            block = ("\n" if text and not text.endswith("\n") else "") + "\n" + "\n".join(add) + "\n"
            gi.write_text(text + block, encoding="utf-8", newline="\n")
    else:
        say("  = .gitignore đã đủ")

    # requirements.txt
    req = PROJECT / "requirements.txt"
    rtext = req.read_text(encoding="utf-8", errors="ignore") if req.exists() else ""
    names = {re.split(r"[<>=~!\[; ]", ln.strip(), 1)[0].lower() for ln in rtext.splitlines() if ln.strip()}
    missing = [r for r in REQUIREMENTS if r.lower() not in names]
    if missing:
        say(f"  + requirements.txt: thêm {', '.join(missing)}")
        if not Ctx.dry:
            req.write_text(rtext + ("\n" if rtext and not rtext.endswith("\n") else "") + "\n".join(missing) + "\n",
                           encoding="utf-8", newline="\n")
    else:
        say("  = requirements.txt đã đủ")

    # Tài liệu: gom note.txt vào docs/ (file chữ, không ảnh hưởng import)
    note = PROJECT / "note.txt"
    if note.is_file():
        say("  > chuyển note.txt -> docs/note.txt")
        if not Ctx.dry:
            (PROJECT / "docs").mkdir(exist_ok=True)
            if not (PROJECT / "docs" / "note.txt").exists():
                shutil.move(str(note), str(PROJECT / "docs" / "note.txt"))

    # Cảnh báo file .spec
    spec = PROJECT / "COC_Auto_Bot.spec"
    if spec.is_file():
        s = spec.read_text(encoding="utf-8", errors="ignore")
        need = [m for m in ("core.license", "core.updater", "gui.license_window", "cryptography") if m not in s]
        if need:
            say("  ⚠ COC_Auto_Bot.spec chưa có hiddenimports: " + ", ".join(need))
            say('    -> thêm vào Analysis(...): hiddenimports=["core.license", "core.updater", '
                '"gui.license_window", "cryptography"]')

    # Git: bỏ theo dõi file không nên đưa lên Git
    if (PROJECT / ".git").exists() and shutil.which("git"):
        tracked = []
        for f in UNTRACK:
            r = subprocess.run(["git", "ls-files", "--error-unmatch", f], cwd=PROJECT,
                               capture_output=True, text=True)
            if r.returncode == 0:
                tracked.append(f)
        if tracked:
            cmd = ["git", "rm", "--cached", "--"] + tracked
            if untrack and not Ctx.dry:
                subprocess.call(cmd, cwd=PROJECT)
                say("  ✔ đã bỏ theo dõi: " + ", ".join(tracked) + " (file vẫn còn trên đĩa)")
            else:
                say("  ⚠ Đang bị Git theo dõi: " + ", ".join(tracked))
                say("    chạy lại với --untrack, hoặc tự chạy:  " + " ".join(cmd))

    # Kiểm tra vị trí file mới
    for rel in ["core/license.py", "core/updater.py", "gui/license_window.py", "tools/keygen.py",
                "run_all.py", "build_release.py"]:
        if not (PROJECT / rel).is_file():
            say(f"  ⚠ Chưa thấy {rel} (sai thư mục?)")


# ----------------------------------------------------------------------------------------------
# PHẦN 2: THƯ MỤC WEB FIREBASE
# ----------------------------------------------------------------------------------------------
FIREBASE_JSON = """{
  "hosting": {
    "public": "public",
    "ignore": ["firebase.json", "**/.*", "**/node_modules/**"],
    "cleanUrls": true,
    "headers": [
      {
        "source": "/latest.json",
        "headers": [
          { "key": "Cache-Control", "value": "no-cache, no-store, must-revalidate" }
        ]
      },
      {
        "source": "**/*.@(zip|sig)",
        "headers": [
          { "key": "Cache-Control", "value": "public, max-age=31536000, immutable" }
        ]
      }
    ]
  }
}
"""

INDEX_HTML = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>COC Auto Bot</title>
<style>
  :root { color-scheme: light dark; --bg:#f6f7f9; --fg:#1b1f24; --card:#ffffff; --accent:#2f6fed; --muted:#667085; }
  @media (prefers-color-scheme: dark) { :root { --bg:#14171c; --fg:#e8eaed; --card:#1d2229; --muted:#98a2b3; } }
  body { margin:0; font:16px/1.6 system-ui, sans-serif; background:var(--bg); color:var(--fg); }
  main { max-width:560px; margin:10vh auto; padding:0 20px; }
  .card { background:var(--card); border-radius:14px; padding:28px; box-shadow:0 2px 12px rgba(0,0,0,.08); }
  h1 { margin:0 0 6px; font-size:24px; }
  p { margin:8px 0; color:var(--muted); }
  a.btn { display:inline-block; margin-top:14px; padding:12px 22px; border-radius:10px; background:var(--accent);
          color:#fff; text-decoration:none; font-weight:600; }
  a.btn[aria-disabled="true"] { opacity:.5; pointer-events:none; }
  ol { color:var(--muted); padding-left:20px; }
</style>
</head>
<body>
<main>
  <div class="card">
    <h1>COC Auto Bot</h1>
    <p id="ver">Đang kiểm tra phiên bản mới nhất...</p>
    <a id="dl" class="btn" aria-disabled="true" href="#">Tải về (.zip)</a>
    <ol>
      <li>Giải nén file zip, mở <b>COC_Auto_Bot.exe</b>.</li>
      <li>Gửi <b>mã máy</b> hiện trên màn hình cho nhà phát hành để nhận <b>license.key</b>.</li>
      <li>Đặt license.key cạnh file .exe. Phần mềm sẽ tự cập nhật khi có bản mới.</li>
    </ol>
  </div>
</main>
<script>
fetch("latest.json", { cache: "no-store" })
  .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
  .then(function (d) {
    document.getElementById("ver").textContent = "Phiên bản mới nhất: v" + d.version;
    var a = document.getElementById("dl");
    a.href = d.zip;
    a.removeAttribute("aria-disabled");
  })
  .catch(function () {
    document.getElementById("ver").textContent = "Chưa có bản phát hành.";
  });
</script>
</body>
</html>
"""

LATEST_EXAMPLE = """{
  "version": "1.0.0",
  "zip": "COC_Auto_Bot_v1.0.0.zip",
  "sig": "COC_Auto_Bot_v1.0.0.zip.sig",
  "released": "2026-01-01"
}
"""

WEB_GITIGNORE = """node_modules/
.firebase/
firebase-debug.log*
# zip/sig/latest.json do publish.py sinh ra, không đưa vào Git
public/*.zip
public/*.sig
public/latest.json
"""

WEB_README = """# coc-web - trang tải + máy chủ cập nhật (Firebase Hosting)

## Cài đặt (một lần)
    npm install -g firebase-tools
    firebase login
    firebase use <project-id>        (hoặc sửa .firebaserc)

## Mỗi lần phát hành
1. Trong thư mục phần mềm:   python run_all.py --bump patch
2. Trong thư mục này:         python publish.py --deploy
   (copy zip + .sig từ release/ vào public/, sinh latest.json, rồi firebase deploy --only hosting)

## Lưu ý
- Chỉ những gì trong public/ mới lên mạng. KHÔNG bỏ private_key.pem, license.key, config.json vào đây.
- latest.json phải luôn được tạo bởi publish.py (đúng định dạng app đọc) và không bị cache.
- Đừng đổi tên miền *.web.app sau khi đã phát hành: app khách lưu cứng địa chỉ này.
"""

PUBLISH_PY = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
publish.py - Đưa bản phát hành mới nhất lên Firebase Hosting.

    python publish.py                    # copy zip + sig mới nhất từ release/ vào public/, sinh latest.json
    python publish.py --deploy           # ... rồi chạy `firebase deploy --only hosting`
    python publish.py --version 1.0.2    # chọn bản cụ thể
    python publish.py --keep 3           # giữ tối đa 3 bản zip trong public/ (mặc định 3)
"""
import argparse
import datetime
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

WEB = Path(__file__).resolve().parent
PUBLIC = WEB / "public"
DEFAULT_SOFTWARE = WEB.parent / "coc_auto_bot"
APP = "COC_Auto_Bot"
NAME_RE = re.compile(rf"^{APP}_v(\\d+(?:\\.\\d+)*)\\.zip$")


def vkey(v):
    return tuple(int(x) for x in v.split("."))


def die(msg):
    print(f"\\n[LỖI] {msg}")
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--software", default=str(DEFAULT_SOFTWARE), help="thư mục dự án phần mềm (chứa release/)")
    ap.add_argument("--version")
    ap.add_argument("--keep", type=int, default=3)
    ap.add_argument("--deploy", action="store_true")
    args = ap.parse_args()

    release = Path(args.software) / "release"
    if not release.is_dir():
        die(f"Không thấy {release}. Dùng --software để chỉ đúng thư mục dự án.")
    found = {}
    for p in release.glob(f"{APP}_v*.zip"):
        m = NAME_RE.match(p.name)
        if m and (release / (p.name + ".sig")).is_file():
            found[m.group(1)] = p
    if not found:
        die("Không có cặp .zip + .zip.sig nào trong release/ (đã chạy run_all.py chưa? có dùng --no-sign không?).")
    version = args.version.lstrip("vV") if args.version else max(found, key=vkey)
    if version not in found:
        die(f"Không có bản {version} trong release/. Có: {', '.join(sorted(found, key=vkey))}")

    zip_src = found[version]
    sig_src = release / (zip_src.name + ".sig")
    sig = json.loads(sig_src.read_text(encoding="utf-8"))
    if sig.get("version") != version or sig.get("app") != APP:
        die("File .sig không khớp phiên bản/tên app của zip.")

    PUBLIC.mkdir(exist_ok=True)
    shutil.copy2(zip_src, PUBLIC / zip_src.name)
    shutil.copy2(sig_src, PUBLIC / sig_src.name)
    latest = {"version": version, "zip": zip_src.name, "sig": sig_src.name,
              "released": datetime.date.today().isoformat()}
    (PUBLIC / "latest.json").write_text(json.dumps(latest, indent=2), encoding="utf-8")
    print(f"✔ public/: {zip_src.name}, {sig_src.name}, latest.json (v{version})")

    # Dọn bản cũ (luôn giữ bản vừa đăng)
    olds = sorted((v for v in (NAME_RE.match(p.name) for p in PUBLIC.glob("*.zip")) if v),
                  key=lambda m: vkey(m.group(1)), reverse=True)
    for m in olds[max(args.keep, 1):]:
        for suffix in ("", ".sig"):
            f = PUBLIC / (m.group(0) + suffix)
            if f.exists():
                f.unlink()
                print(f"  - xóa bản cũ: {f.name}")

    if args.deploy:
        fb = shutil.which("firebase")
        if not fb:
            die("Không thấy lệnh `firebase`. Cài: npm install -g firebase-tools, rồi firebase login.")
        if subprocess.call([fb, "deploy", "--only", "hosting"], cwd=WEB) != 0:
            die("firebase deploy thất bại.")
        print("✔ Đã deploy.")
    else:
        print("  (chưa deploy: chạy lại với --deploy hoặc `firebase deploy --only hosting`)")


if __name__ == "__main__":
    main()
'''


def build_web(web_dir: Path, project_id):
    say(f"\n=== 2. THƯ MỤC WEB FIREBASE: {web_dir} ===")
    if web_dir.resolve() == PROJECT or PROJECT in web_dir.resolve().parents:
        say("  ✖ --web-dir không được nằm TRONG thư mục phần mềm (tránh lọt vào gói phát hành).")
        sys.exit(1)
    write_new(web_dir / "firebase.json", FIREBASE_JSON)
    rc = '{\n  "projects": {\n    "default": "%s"\n  }\n}\n' % (project_id or "THAY-BANG-PROJECT-ID")
    write_new(web_dir / ".firebaserc", rc)
    write_new(web_dir / "public" / "index.html", INDEX_HTML)
    write_new(web_dir / "latest.example.json", LATEST_EXAMPLE)
    write_new(web_dir / "publish.py", PUBLISH_PY)
    write_new(web_dir / ".gitignore", WEB_GITIGNORE)
    write_new(web_dir / "README.md", WEB_README)
    if not project_id:
        say("  ⚠ Chưa có --project-id: hãy sửa .firebaserc (hoặc chạy `firebase use <project-id>`).")


def main():
    ap = argparse.ArgumentParser(description="Tạo thư mục web Firebase + dọn dự án")
    ap.add_argument("--web-dir", default=str(PROJECT.parent / "coc-web"),
                    help="nơi tạo thư mục web (mặc định: cạnh thư mục phần mềm, tên coc-web)")
    ap.add_argument("--project-id", help="Project ID trên Firebase (ten-du-an)")
    ap.add_argument("--untrack", action="store_true", help="git rm --cached cho dist.zip, config.json...")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    Ctx.dry = args.dry_run

    organize_project(args.untrack)
    build_web(Path(args.web_dir), args.project_id)

    say("\n=== XONG ===")
    say("Bước tiếp theo:")
    say("  1) cd vào thư mục web:  cd " + str(Path(args.web_dir)))
    say("  2) npm install -g firebase-tools  &&  firebase login")
    say("  3) Phát hành: (phần mềm) python run_all.py --bump patch  ->  (web) python publish.py --deploy")


if __name__ == "__main__":
    main()