import os
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, Request, Form, Body
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import hashlib

app = FastAPI()

# --- CORS AYARLARI ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="templates")

# --- VERİTABANI BAĞLANTISI ---
def get_db_connection():
    db_url = os.environ.get('DATABASE_URL')
    if not db_url: return None
    try:
        conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
        return conn
    except Exception as e:
        print(f"DB Hatası: {e}")
        return None

def get_system_settings(cursor):
    """Sistem ayarlarını (versiyon, hash vb.) çeker."""
    try:
        cursor.execute("SELECT * FROM system_settings")
        rows = cursor.fetchall()
        settings = {row['setting_key']: row['setting_value'] for row in rows}
        return settings
    except:
        return {}

# =========================================================
# 🚀 API ENDPOINTS
# =========================================================

# 1. GİRİŞ VE LİSANS KONTROLÜ
@app.post("/api/login")
async def api_login(payload: dict = Body(...)):
    email = payload.get("email")
    password = payload.get("password")
    hwid = payload.get("hwid") 

    conn = get_db_connection()
    if not conn: return JSONResponse(content={"status": "error", "message": "DB Hatası"}, status_code=500)
    
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
    user = cursor.fetchone()
    
    settings = get_system_settings(cursor)
    
    if user:
        input_hash = hashlib.sha256(password.encode()).hexdigest()
        if input_hash == user["password_hash"]:
            # HWID (Donanım Kilidi) Kontrolü
            current_lock = user.get("hwid_lock")
            
            # İlk girişse kilitle
            if not current_lock:
                cursor.execute("UPDATE users SET hwid_lock = %s WHERE user_id = %s", (hwid, user["user_id"]))
                conn.commit()
            # Kilitliyse ve farklı bir cihazsa reddet
            elif current_lock != "UNKNOWN_HWID" and current_lock != hwid:
                conn.close()
                return JSONResponse(content={"status": "error", "message": "Lisans Hatası: Yetkisiz Cihaz!"}, status_code=403)

            # Başarılı Giriş
            fake_token = f"{user['user_id']}" 
            response_data = {
                "status": "success",
                "token": fake_token,
                "company": user.get("company_name"),
                "credits": user.get("credits_balance", 0), # Gösterge amaçlı kaldı
                "security": {
                    "latest_version": settings.get("latest_version", "1.0.0"),
                    "main_exe_hash": settings.get("main_exe_hash", ""),
                    "force_update": settings.get("force_update", "False"),
                    "download_url": settings.get("download_url", "")
                }
            }
            conn.close()
            return JSONResponse(content=response_data)
    
    conn.close()
    return JSONResponse(content={"status": "error", "message": "Hatalı E-posta veya Şifre"}, status_code=401)

# 2. MENÜYÜ GETİR
@app.get("/api/get-menu")
async def get_menu(token: str):
    conn = get_db_connection()
    if not conn: return JSONResponse(content={"error": "Sunucu hatası"}, status_code=500)
    
    cursor = conn.cursor()
    # cost_per_run bilgisini hala çekiyoruz ama istemcide sadece bilgi amaçlı durabilir
    cursor.execute("""
        SELECT scenario_id as id, group_name, risk_title, description, 
               risk_message, legislation, risk_reason, solution_suggestion, 
               source_type, cost_per_run, is_active, cross_check_rule as cross_check
        FROM scenarios WHERE is_active = TRUE
    """)
    scenarios = cursor.fetchall()
    conn.close()
    return {"scenarios": scenarios}

# 3. KODU GETİR (KREDİ DÜŞME MANTIĞI KALDIRILDI)
@app.post("/api/get-code")
async def get_code(payload: dict = Body(...)):
    token = payload.get("token")
    scenario_id = payload.get("scenario_id")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try: user_id = int(token)
    except: 
        conn.close()
        return JSONResponse(content={"error": "Geçersiz Token"}, status_code=401)

    # Sadece Kodu Çek (Maliyet kontrolü ve bakiye düşme yok)
    cursor.execute("SELECT code_payload FROM scenarios WHERE scenario_id = %s", (scenario_id,))
    scenario = cursor.fetchone()
    
    conn.close()

    if not scenario:
        return JSONResponse(content={"error": "Senaryo bulunamadı"}, status_code=404)
        
    # Kod payload'ını istemciye gönder
    return {"code": scenario["code_payload"]}

# 4. BAKİYE SORGULA
@app.get("/api/get-balance")
async def get_balance(token: str):
    conn = get_db_connection()
    if not conn: return {"credits": 0}
    cursor = conn.cursor()
    try:
        user_id = int(token)
        cursor.execute("SELECT credits_balance FROM users WHERE user_id = %s", (user_id,))
        res = cursor.fetchone()
        conn.close()
        return {"credits": res["credits_balance"] if res else 0}
    except:
        conn.close()
        return {"credits": 0}

# =========================================================
# 🌐 WEB ADMIN PANELİ
# =========================================================

@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/web-login")
async def web_login(request: Request, email: str = Form(...), password: str = Form(...)):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
    user = cursor.fetchone()
    conn.close()
    if user:
        input_hash = hashlib.sha256(password.encode()).hexdigest()
        if input_hash == user["password_hash"]:
            if user["role"] == "admin": return RedirectResponse(url="/admin/dashboard", status_code=303)
            else: return templates.TemplateResponse("user_dashboard.html", {"request": request, "user": user})
    return templates.TemplateResponse("login.html", {"request": request, "error": "Hatalı Giriş"})

@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    return templates.TemplateResponse("admin_dashboard.html", {"request": request, "stats": {}, "scenarios": []})
