# CoC Auto Bot (LDPlayer 9)

## Cài đặt
1. Cài Python 3.10+ và Tesseract OCR (mặc định `C:\Program Files\Tesseract-OCR`).
2. `pip install -r requirements.txt`
3. Bật **ADB debugging** trong Cài đặt LDPlayer (Local connection).
4. Đặt 4 ảnh mẫu (cắt từ ảnh chụp màn hình 960x540) vào `assets/`:
   `button_attack.png`, `button_next.png`, `button_retry.png`, `builder_icon.png`.
5. Chỉnh `LOOT_REGIONS` trong `config.py` cho khớp vị trí số Vàng/Dầu/Dầu đen trên màn hình.

## Chạy
```
cd coc_auto_bot
python main.py
```

## Còn thiếu
- `BotLogic.deploy_troops()` chưa cài đặt (thả quân).
