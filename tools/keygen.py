#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/keygen.py - CÔNG CỤ RIÊNG CỦA BẠN. TUYỆT ĐỐI KHÔNG đưa thư mục tools/ vào bản phát hành.

  python tools/keygen.py init                                   # 1 lần duy nhất: sinh cặp khóa Ed25519
  python tools/keygen.py issue --machine XXXX-XXXX-XXXX-XXXX-XXXX [--days 365 | --expires 2027-12-31] [--name "Khách A"]
  python tools/keygen.py sign-update release/COC_Auto_Bot_v1.1.0.zip --version 1.1.0   (build_release.py tự gọi)

Khóa riêng tư: tools/secret/private_key.pem (hoặc đường dẫn trong biến môi trường COC_PRIVATE_KEY, hoặc --key).
MẤT khóa riêng tư = không cấp được license / ký được bản cập nhật mới. HÃY SAO LƯU Ở NƠI AN TOÀN.
ĐỔI khóa = mọi bản đã phát hành sẽ từ chối license và bản cập nhật mới -> phải gửi lại bản cài thủ công.
"""
import argparse
import base64
import csv
import datetime
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

TOOLS_DIR = Path(__file__).resolve().parent
PROJECT = TOOLS_DIR.parent
DEFAULT_KEY_PATH = TOOLS_DIR / "secret" / "private_key.pem"
ISSUED_DIR = TOOLS_DIR / "issued"
LICENSE_PY = PROJECT / "core" / "license.py"

APP_ID = "COC_Auto_Bot"
LICENSE_DOMAIN = b"COC-LICENSE\n"   # PHẢI giống core/license.py
UPDATE_DOMAIN = b"COC-UPDATE\n"     # PHẢI giống core/license.py


class KeygenError(Exception):
    pass


def _key_path(arg=None):
    return Path(arg or os.environ.get("COC_PRIVATE_KEY") or DEFAULT_KEY_PATH)


def load_private_key(path=None):
    p = _key_path(path)
    if not p.is_file():
        raise KeygenError(f"Không thấy khóa riêng tư: {p}\n  -> chạy `python tools/keygen.py init` (lần đầu) "
                          f"hoặc đặt COC_PRIVATE_KEY / --key.")
    try:
        key = serialization.load_pem_private_key(p.read_bytes(), password=None)
    except Exception as e:
        raise KeygenError(f"Không đọc được khóa riêng tư {p}: {e}") from e
    if not isinstance(key, Ed25519PrivateKey):
        raise KeygenError("Khóa riêng tư không phải Ed25519.")
    return key


def public_key_b64(private_key):
    raw = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


# ----------------------------------------------------------------------------------------------
def cmd_init(args):
    path = _key_path(args.key)
    if path.exists():
        raise KeygenError(f"Đã có khóa tại {path}. Không ghi đè (sẽ làm hỏng mọi license đã cấp).\n"
                          f"  Nếu thật sự muốn tạo lại, tự tay xóa/di chuyển file đó trước.")
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                       serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    (path.parent / ".gitignore").write_text("*\n", encoding="utf-8")   # không cho git commit nhầm khóa

    pub = public_key_b64(key)
    print(f"✔ Đã tạo khóa riêng tư: {path}")
    print(f"  Khóa công khai: {pub}")
    try:
        text = LICENSE_PY.read_text(encoding="utf-8")
        new, n = re.subn(r'(?m)^PUBLIC_KEY_B64\s*=\s*".*"', f'PUBLIC_KEY_B64 = "{pub}"', text, count=1)
        if n != 1:
            raise ValueError("không thấy dòng PUBLIC_KEY_B64")
        LICENSE_PY.write_text(new, encoding="utf-8")
        print(f"✔ Đã dán khóa công khai vào {LICENSE_PY.relative_to(PROJECT)}")
    except Exception as e:
        print(f"⚠ Không tự dán được ({e}). Hãy tự sửa dòng PUBLIC_KEY_B64 trong core/license.py thành:\n"
              f'  PUBLIC_KEY_B64 = "{pub}"')
    print("\n⚠ SAO LƯU file khóa riêng tư ở nơi an toàn và KHÔNG gửi cho ai. Sau đó BUILD LẠI app.")


def _norm_machine(text):
    h = re.sub(r"[^0-9A-F]", "", text.upper())
    if len(h) != 20:
        raise KeygenError("Mã máy không hợp lệ: cần 20 ký tự hex (dạng XXXX-XXXX-XXXX-XXXX-XXXX).")
    return "-".join(h[i:i + 4] for i in range(0, 20, 4))


def license_slug(machine_id):   # PHẢI giống core/license.py
    mid = re.sub(r"[^0-9A-F]", "", machine_id.upper())
    return hashlib.sha256(("COCLIC|" + mid).encode("utf-8")).hexdigest()[:32]


def issue_license(machine_id, expires=None, name="", key_path=None):
    key = load_private_key(key_path)
    payload = {
        "app": APP_ID,
        "machine_id": _norm_machine(machine_id),
        "issued": datetime.date.today().isoformat(),
        "expires": expires,
        "name": name,
    }
    payload_bytes = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")).encode("utf-8")
    signature = key.sign(LICENSE_DOMAIN + payload_bytes)
    return payload, json.dumps({
        "payload": base64.b64encode(payload_bytes).decode("ascii"),
        "signature": base64.b64encode(signature).decode("ascii"),
    }, indent=2)


def cmd_issue(args):
    expires = None
    if args.expires and args.days:
        raise KeygenError("Chỉ dùng một trong --days hoặc --expires.")
    if args.days:
        expires = (datetime.date.today() + datetime.timedelta(days=args.days)).isoformat()
    elif args.expires:
        try:
            expires = datetime.date.fromisoformat(args.expires).isoformat()
        except ValueError:
            raise KeygenError("--expires phải có dạng YYYY-MM-DD.")
    payload, content = issue_license(args.machine, expires, args.name or "", args.key)

    out = Path(args.out) if args.out else ISSUED_DIR / payload["machine_id"] / "license.key"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")

    # Bản để đẩy lên máy chủ: tools/issued/public/<slug>.key  (copy cả thư mục này vào thư mục "licenses" của server)
    pub_dir = ISSUED_DIR / "public"
    pub_dir.mkdir(parents=True, exist_ok=True)
    pub_file = pub_dir / f"{license_slug(payload['machine_id'])}.key"
    pub_file.write_text(content, encoding="utf-8")

    ISSUED_DIR.mkdir(exist_ok=True)
    log = ISSUED_DIR / "issued.csv"
    new_file = not log.exists()
    with open(log, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["issued", "machine_id", "expires", "name"])
        w.writerow([payload["issued"], payload["machine_id"], expires or "vĩnh viễn", payload["name"]])

    print(f"✔ Đã cấp license cho máy {payload['machine_id']} "
          f"(hết hạn: {expires or 'vĩnh viễn'}{', ' + payload['name'] if payload['name'] else ''})")
    print(f"  File gửi cho khách (thủ công): {out}")
    print(f"  File đẩy lên server (khách tự nhận): {pub_file}")


def sign_update(zip_path, version, key_path=None):
    """Ký file zip phát hành -> ghi <zip>.sig. Trả về đường dẫn file .sig."""
    zip_path = Path(zip_path)
    if not zip_path.is_file():
        raise KeygenError(f"Không thấy {zip_path}")
    key = load_private_key(key_path)
    version = str(version).lstrip("vV")
    sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    signature = key.sign(UPDATE_DOMAIN + f"{APP_ID}|{version}|{sha}".encode("utf-8"))
    sig_path = zip_path.with_name(zip_path.name + ".sig")
    sig_path.write_text(json.dumps({
        "app": APP_ID, "version": version, "sha256": sha,
        "signature": base64.b64encode(signature).decode("ascii"),
    }, indent=2), encoding="utf-8")
    return sig_path


def cmd_sign_update(args):
    sig = sign_update(args.zip, args.version, args.key)
    print(f"✔ Đã ký: {sig}")


def main():
    ap = argparse.ArgumentParser(description="Công cụ khóa/license/chữ ký (chỉ dành cho nhà phát hành)")
    ap.add_argument("--key", help="đường dẫn khóa riêng tư (mặc định tools/secret/private_key.pem)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="tạo cặp khóa Ed25519 (một lần)").set_defaults(func=cmd_init)

    p = sub.add_parser("issue", help="cấp license.key cho một mã máy")
    p.add_argument("--machine", required=True, help="mã máy khách gửi")
    p.add_argument("--days", type=int, help="hết hạn sau N ngày")
    p.add_argument("--expires", help="hết hạn vào ngày YYYY-MM-DD (bỏ trống cả hai = vĩnh viễn)")
    p.add_argument("--name", help="ghi chú tên khách (nằm trong license)")
    p.add_argument("--out", help="đường dẫn file xuất (mặc định tools/issued/<mã máy>/license.key)")
    p.set_defaults(func=cmd_issue)

    p = sub.add_parser("sign-update", help="ký file zip cập nhật")
    p.add_argument("zip")
    p.add_argument("--version", required=True)
    p.set_defaults(func=cmd_sign_update)

    args = ap.parse_args()
    try:
        args.func(args)
    except KeygenError as e:
        print(f"\n[LỖI] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()