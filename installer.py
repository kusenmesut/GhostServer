import tkinter as tk
from tkinter import ttk, messagebox
import requests
import zipfile
import os
import sys
import threading
import subprocess
import winshell  # pip install pywin32
from win32com.client import Dispatch
import pythoncom 
import shutil
import urllib3 # <--- EKLENDİ

# --- UYARILARI GİZLE (Sessiz Mod) ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
# ------------------------------------

# --- AYARLAR ---
SERVER_URL = "https://ghostserver-rgyz.onrender.com"
API_CHECK_URL = f"{SERVER_URL}/api/check-version"
APP_NAME = "Ghost Auditor"
INSTALL_PATH = os.path.join("C:\\", "GhostAuditor") 

class GhostInstaller:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(f"{APP_NAME} Kurulum Sihirbazı")
        self.root.geometry("450x350")
        self.root.resizable(False, False)
        self.root.configure(bg="#2d3436")
        self.center_window()
        self.setup_ui()
        
    def center_window(self):
        ws = self.root.winfo_screenwidth()
        hs = self.root.winfo_screenheight()
        x = (ws/2) - (450/2)
        y = (hs/2) - (350/2)
        self.root.geometry('+%d+%d' % (x, y))

    def setup_ui(self):
        tk.Label(self.root, text="🚀", bg="#2d3436", fg="white", font=("Segoe UI", 40)).pack(pady=(20, 10))
        tk.Label(self.root, text=f"{APP_NAME} Kuruluyor", bg="#2d3436", fg="#00cec9", font=("Segoe UI", 16, "bold")).pack()
        
        self.lbl_info = tk.Label(self.root, text="Kurulum başlatılıyor...", 
                                 bg="#2d3436", fg="#b2bec3", font=("Segoe UI", 10))
        self.lbl_info.pack(pady=10)

        self.progress = ttk.Progressbar(self.root, orient="horizontal", length=350, mode="determinate")
        self.progress.pack(pady=20)
        
        self.btn_action = tk.Button(self.root, text="KURULUMU BAŞLAT", bg="#0984e3", fg="white", 
                                    font=("Segoe UI", 10, "bold"), relief="flat", command=self.start_installation)
        self.btn_action.pack(pady=10, ipady=5, ipadx=20)

        self.root.after(1000, self.start_installation)

    def start_installation(self):
        self.btn_action.config(state="disabled", text="KURULUYOR...")
        threading.Thread(target=self.worker, daemon=True).start()

    def worker(self):
        try:
            # 1. KLASÖR OLUŞTUR
            if not os.path.exists(INSTALL_PATH):
                try: os.makedirs(INSTALL_PATH)
                except PermissionError: raise Exception("Erişim Reddedildi! Yönetici Olarak Çalıştırın.")

            # 2. LİNK AL
            self.update_status("Sunucuya bağlanılıyor...", 10)
            resp = requests.get(API_CHECK_URL, verify=False, timeout=10)
            if resp.status_code != 200: raise Exception("Sunucu hatası.")
            
            download_url = resp.json().get("download_url")
            if not download_url: raise Exception("İndirme linki yok!")

            # 3. İNDİR
            self.update_status("Dosyalar indiriliyor...", 30)
            zip_path = os.path.join(INSTALL_PATH, "setup.zip")
            with requests.get(download_url, stream=True, verify=False) as r:
                r.raise_for_status()
                with open(zip_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)

            # 4. ZIP AÇ
            self.update_status("Dosyalar çıkartılıyor...", 60)
            with zipfile.ZipFile(zip_path, 'r') as z:
                z.extractall(INSTALL_PATH)
            
            try: os.remove(zip_path)
            except: pass

            # 5. KLASÖR YAPISINI DÜZELT (Flatten)
            # Zip içinden tek bir klasör çıkarsa (örn: GhostServer-main), içindekileri ana dizine al
            items = os.listdir(INSTALL_PATH)
            if len(items) == 1 and os.path.isdir(os.path.join(INSTALL_PATH, items[0])):
                nested_folder = os.path.join(INSTALL_PATH, items[0])
                for file_name in os.listdir(nested_folder):
                    shutil.move(os.path.join(nested_folder, file_name), INSTALL_PATH)
                os.rmdir(nested_folder)

            # 6. HEDEF BELİRLE
            target_exe = os.path.join(INSTALL_PATH, "launcher.exe")
            if not os.path.exists(target_exe):
                target_exe = os.path.join(INSTALL_PATH, "launcher.py")

            # 7. KISAYOL VE BAŞLATMA
            self.update_status("Kısayol oluşturuluyor...", 80)
            self.create_shortcut(target_exe)

            self.update_status("Kurulum Tamamlandı!", 100)
            messagebox.showinfo("Başarılı", f"Kurulum tamamlandı!\nKonum: {INSTALL_PATH}")
            
            self.launch_app(target_exe)
            self.root.quit()

        except Exception as e:
            # Hata detayını konsola yazma (kullanıcı görmesin)
            messagebox.showerror("Hata", f"{e}")
            self.lbl_info.config(text="Kurulum Başarısız", fg="#d63031")
            self.btn_action.config(state="normal", text="TEKRAR DENE")

    def create_shortcut(self, target_path):
        try:
            pythoncom.CoInitialize()
            desktop = winshell.desktop()
            path = os.path.join(desktop, f"{APP_NAME}.lnk")
            shell = Dispatch('WScript.Shell')
            shortcut = shell.CreateShortcut(path)
            shortcut.TargetPath = target_path
            shortcut.WorkingDirectory = os.path.dirname(target_path)
            shortcut.IconLocation = target_path
            shortcut.save()
        except Exception as e:
            pass

    def launch_app(self, target_path):
        try:
            work_dir = os.path.dirname(target_path)
            if target_path.endswith(".py"):
                # Konsolsuz pythonw.exe
                python_exe = sys.executable.replace("python.exe", "pythonw.exe")
                if not os.path.exists(python_exe): python_exe = sys.executable
                subprocess.Popen([python_exe, target_path], cwd=work_dir)
            else:
                subprocess.Popen([target_path], cwd=work_dir)
        except: pass

    def update_status(self, text, val):
        self.root.after(0, lambda: self.lbl_info.config(text=text, fg="white"))
        self.root.after(0, lambda: self.progress.configure(value=val))

if __name__ == "__main__":
    app = GhostInstaller()
    app.root.mainloop()
