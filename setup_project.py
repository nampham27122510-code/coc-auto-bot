"""Tạo cấu trúc thư mục rỗng cho dự án (chạy 1 lần nếu cần)."""
import os

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "coc_auto_bot")
for d in ("assets", "core", "gui"):
    os.makedirs(os.path.join(ROOT, d), exist_ok=True)
for f in ("core/__init__.py", "gui/__init__.py"):
    open(os.path.join(ROOT, f), "a").close()
print("Đã tạo cấu trúc thư mục tại:", ROOT)
