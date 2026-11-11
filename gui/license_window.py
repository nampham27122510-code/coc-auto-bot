# -*- coding: utf-8 -*-
"""gui/license_window.py - Cửa sổ kích hoạt: chỉ hiện mã máy + nút liên hệ Zalo.
Phần mềm tự dò license trên máy chủ, khi nhà phát hành cấp xong sẽ tự đóng và chạy tiếp.
Chỉ mở khi license thiếu/sai."""
import threading
import time
import webbrowser

import customtkinter as ctk

from core import license as lic

ZALO_PHONE = "0857571258"
ZALO_URL = f"https://zalo.me/{ZALO_PHONE}"
POLL_SECONDS = 15


def show_license_window(status):
    """Hiện cửa sổ kích hoạt. Trả về True nếu đã có license hợp lệ, False nếu người dùng đóng cửa sổ."""
    result = {"ok": False}

    win = ctk.CTk()
    win.title("COC Auto Bot - Kích hoạt bản quyền")
    win.geometry("560x430")
    win.resizable(False, False)

    ctk.CTkLabel(win, text="Kích hoạt bản quyền", font=ctk.CTkFont(size=20, weight="bold")).pack(pady=(18, 6))
    reason = ctk.CTkLabel(win, text=status.reason, text_color="#e5534b", wraplength=500, justify="center")
    reason.pack(pady=(0, 10))

    ctk.CTkLabel(win, text="Mã máy của bạn:").pack(anchor="w", padx=30)
    row = ctk.CTkFrame(win, fg_color="transparent")
    row.pack(fill="x", padx=30, pady=(4, 12))
    entry = ctk.CTkEntry(row, font=ctk.CTkFont(family="Consolas", size=16), height=38)
    entry.insert(0, status.machine_id or lic.get_machine_id())
    entry.configure(state="readonly")
    entry.pack(side="left", fill="x", expand=True)

    def copy_id():
        win.clipboard_clear()
        win.clipboard_append(entry.get())

    def on_copy():
        copy_id()
        copy_btn.configure(text="Đã chép ✔")
        win.after(1500, lambda: copy_btn.configure(text="Sao chép"))

    copy_btn = ctk.CTkButton(row, text="Sao chép", width=100, height=38, command=on_copy)
    copy_btn.pack(side="left", padx=(8, 0))

    ctk.CTkLabel(
        win, justify="left", wraplength=500,
        text=("Cách kích hoạt:\n"
              "1. Bấm \"Liên hệ nhà phát hành (Zalo)\" bên dưới để gửi mã máy (chỉ một lần).\n"
              "2. Giữ cửa sổ này mở, phần mềm sẽ TỰ kích hoạt khi bạn được cấp quyền."),
    ).pack(anchor="w", padx=30, pady=(0, 8))

    wait_lbl = ctk.CTkLabel(win, text=f"⏳ Đang chờ nhà phát hành cấp quyền... (tự kiểm tra mỗi {POLL_SECONDS} giây)",
                            text_color="#d4a017")
    wait_lbl.pack(anchor="w", padx=30, pady=(0, 12))

    def contact_zalo():
        copy_id()   # chép sẵn mã máy -> sang Zalo chỉ cần dán (Ctrl+V) rồi gửi
        reason.configure(text=f"Đã chép mã máy. Hãy DÁN (Ctrl+V) vào Zalo {ZALO_PHONE} và gửi cho nhà phát hành.",
                         text_color="#d4a017")
        try:
            webbrowser.open(ZALO_URL)
        except Exception:
            reason.configure(text=f"Không mở được Zalo. Hãy nhắn tới số {ZALO_PHONE} kèm mã máy ở trên.",
                             text_color="#e5534b")

    ctk.CTkButton(win, text=f"💬 Liên hệ nhà phát hành (Zalo {ZALO_PHONE})", height=42,
                  fg_color="#0068ff", hover_color="#0054cc", command=contact_zalo).pack(padx=30, fill="x")

    def finish_if_ok(st):
        if st is not None and st.ok:
            result["ok"] = True
            win.destroy()
        elif st is not None:
            reason.configure(text=st.reason, text_color="#e5534b")

    def recheck():
        st = lic.check_license()
        if not st.ok:
            st = lic.fetch_license_online() or st
        finish_if_ok(st)

    btns = ctk.CTkFrame(win, fg_color="transparent")
    btns.pack(pady=14)
    ctk.CTkButton(btns, text="Kiểm tra lại", width=130, fg_color="#555555", hover_color="#444444",
                  command=recheck).pack(side="left", padx=6)
    ctk.CTkButton(btns, text="Thoát", width=100, fg_color="#555555", hover_color="#444444",
                  command=win.destroy).pack(side="left", padx=6)

    # ---- Tự dò license trên máy chủ (chạy nền, không làm đơ giao diện) ----
    poll = {"busy": False, "result": None, "last": 0.0}

    def _worker():
        poll["result"] = lic.fetch_license_online()
        poll["busy"] = False

    def _tick():
        try:
            if not win.winfo_exists():
                return
        except Exception:
            return
        res = poll["result"]
        if res is not None:
            poll["result"] = None
            if res.ok:
                finish_if_ok(res)
                return
        if not poll["busy"] and time.monotonic() - poll["last"] >= POLL_SECONDS:
            poll["busy"] = True
            poll["last"] = time.monotonic()
            threading.Thread(target=_worker, daemon=True).start()
        win.after(1000, _tick)

    if lic.LICENSE_SERVER_URL.strip():
        win.after(500, _tick)
    else:
        wait_lbl.pack_forget()

    win.protocol("WM_DELETE_WINDOW", win.destroy)
    win.mainloop()
    return result["ok"]