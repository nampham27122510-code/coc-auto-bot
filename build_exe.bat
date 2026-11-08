@echo off
chcp 65001 >nul
title Đang đóng gói CoC Auto Bot thành File EXE 1-Click

echo ==================================================
echo 🚀 BẮT ĐẦU TỰ ĐỘNG ĐÓNG GÓI COC AUTO BOT
echo ==================================================

:: 1. Kiểm tra & cài đặt các thư viện cần thiết để Build
python -m pip install --upgrade pip
pip install pyinstaller customtkinter opencv-python pytesseract pillow numpy

:: 2. Xóa các bản build cũ
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

:: 3. Đóng gói ứng dụng thành 1 file EXE duy nhất
pyinstaller --clean COC_Auto_Bot.spec

echo ==================================================
echo ✅ ĐÓNG GÓI HOÀN TẤT!
echo 📁 File EXE duy nhất nằm trong thư mục: dist\COC_Auto_Bot.exe
echo ==================================================
pause