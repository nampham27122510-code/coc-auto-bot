import os
import sys
import subprocess
import requests

# Phiên bản hiện tại của ứng dụng (Tăng số này lên khi bạn làm bản mới)
CURRENT_VERSION = "v1.0.0" 

# Link GitHub API lấy release mới nhất
REPO_API_URL = "https://api.github.com/repos/nampham27122510-code/coc-auto-bot/releases/latest"

def check_for_updates():
    """Kiểm tra xem trên GitHub có bản mới hơn không."""
    try:
        response = requests.get(REPO_API_URL, timeout=5)
        if response.status_code == 200:
            data = response.json()
            latest_version = data.get("tag_name")
            
            # Nếu phiên bản trên GitHub khác phiên bản hiện tại
            if latest_version and latest_version != CURRENT_VERSION:
                for asset in data.get("assets", []):
                    # Tìm file zip chứa bản build mới
                    if asset["name"].endswith(".zip"):
                        return latest_version, asset["browser_download_url"]
    except Exception as e:
        print(f"Không thể kiểm tra cập nhật: {e}")
    return None, None

def apply_update(download_url):
    """Tải bản mới, giải nén ghi đè và khởi động lại app."""
    temp_zip = "update_temp.zip"
    
    # 1. Tải file ZIP bản mới về
    response = requests.get(download_url, stream=True)
    with open(temp_zip, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
            
    # 2. Tạo file script tạm (.bat) để ghi đè ứng dụng sau khi app đóng
    bat_script = f"""@echo off
timeout /t 2 /nobreak > nul
tar -xf {temp_zip} -C . --overwrite
del {temp_zip}
start "" "dist\\COC_Auto_Bot.exe"
del "%~f0"
"""
    with open("run_update.bat", "w", encoding="utf-8") as f:
        f.write(bat_script)
        
    # 3. Chạy file bat ẩn và ngắt chương trình hiện tại ngay lập tức
    subprocess.Popen(["cmd.exe", "/c", "run_update.bat"], creationflags=subprocess.CREATE_NO_WINDOW)
    sys.exit(0)