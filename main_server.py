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
            current_lock = user.get("hwid_lock")
            if not current_lock:
                cursor.execute("UPDATE users SET hwid_lock = %s WHERE user_id = %s", (hwid, user["user_id"]))
                conn.commit()
            elif current_lock != "UNKNOWN_HWID" and current_lock != hwid:
                conn.close()
                return JSONResponse(content={"status": "error", "message": "Lisans Hatası: Yetkisiz Cihaz!"}, status_code=403)

            fake_token = f"{user['user_id']}" 
            response_data = {
                "status": "success",
                "token": fake_token,
                "company": user.get("company_name"),
                "credits": user.get("credits_balance", 0),
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

@app.get("/api/get-menu")
async def get_menu(token: str):
    conn = get_db_connection()
    if not conn: return JSONResponse(content={"error": "Sunucu hatası"}, status_code=500)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT scenario_id as id, group_name, risk_title, description, 
               risk_message, legislation, risk_reason, solution_suggestion, 
               source_type, is_active, cross_check_rule as cross_check
        FROM scenarios WHERE is_active = TRUE
    """)
    scenarios = cursor.fetchall()
    conn.close()
    return {"scenarios": scenarios}

@app.post("/api/get-code")
async def get_code(payload: dict = Body(...)):
    token = payload.get("token")
    scenario_id = payload.get("scenario_id")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Sadece kodu döndür, kredi kontrolü 'deduct-credit' endpointinde yapıldı
    cursor.execute("SELECT code_payload FROM scenarios WHERE scenario_id = %s", (scenario_id,))
    scenario = cursor.fetchone()
    conn.close()

    if not scenario:
        return JSONResponse(content={"error": "Senaryo bulunamadı"}, status_code=404)
        
    return {"code": scenario["code_payload"]}

# --- YENİ EKLENEN ENDPOINT: KREDİ DÜŞME ---
@app.post("/api/deduct-credit")
async def deduct_credit(payload: dict = Body(...)):
    token = payload.get("token")
    group_name = payload.get("group_name")
    
    conn = get_db_connection()
    if not conn: return JSONResponse({"status": "error", "message": "Sunucu hatası"}, 500)
    cursor = conn.cursor()
    
    try:
        user_id = int(token)
    except:
        conn.close()
        return JSONResponse({"status": "error", "message": "Geçersiz Token"}, 401)

    # 1. Maliyeti Hesapla
    cost = 0
    if group_name == "TÜMÜ":
        # Tüm aktif grupların toplam maliyeti (Basitleştirilmiş: Tüm grupların toplamı)
        # Veya sabit bir "Tam Denetim" ücreti belirlenebilir. Şimdilik senaryo_groups tablosundaki her şeyin toplamını alalım.
        # Daha doğrusu: Aktif senaryosu olan grupları bulup toplayabiliriz.
        # Basitlik için: Tüm grupların toplamını alalım.
        cursor.execute("SELECT SUM(cost_per_run) as total FROM scenario_groups")
        row = cursor.fetchone()
        cost = row['total'] if row and row['total'] else 0
    else:
        cursor.execute("SELECT cost_per_run FROM scenario_groups WHERE group_name = %s", (group_name,))
        row = cursor.fetchone()
        cost = row['cost_per_run'] if row else 0

    # 2. Bakiyeyi Kontrol Et
    cursor.execute("SELECT credits_balance FROM users WHERE user_id = %s", (user_id,))
    user = cursor.fetchone()
    current_balance = user['credits_balance'] if user else 0
    
    if current_balance < cost:
        conn.close()
        return JSONResponse({
            "status": "error", 
            "message": f"Yetersiz Bakiye! (Gereken: {cost}, Mevcut: {current_balance})"
        }, 402)

    # 3. Krediyi Düş
    if cost > 0:
        cursor.execute("UPDATE users SET credits_balance = credits_balance - %s WHERE user_id = %s", (cost, user_id))
        cursor.execute("INSERT INTO logs (user_id, action, details, credit_cost) VALUES (%s, %s, %s, %s)", 
                       (user_id, 'run_group_audit', f"Grup: {group_name}", cost))
        conn.commit()

    conn.close()
    return {"status": "success", "deducted": cost, "remaining": current_balance - cost}

# --- WEB ADMIN ---
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
