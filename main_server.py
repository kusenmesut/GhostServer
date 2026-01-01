import os
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel
import hashlib
from datetime import datetime

app = FastAPI()
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

# =========================================================
# BÖLÜM A: WEB ARAYÜZÜ (KOKPİT / DASHBOARD)
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
            if user["role"] == "Admin":
                return RedirectResponse(url="/admin/dashboard", status_code=303)
            else:
                return templates.TemplateResponse("user_dashboard.html", {"request": request, "user": user})
    return templates.TemplateResponse("login.html", {"request": request, "error": "Hatalı Giriş"})

@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # İstatistikler
    cursor.execute("SELECT COUNT(*) as c FROM users WHERE role!='Admin'")
    total_users = cursor.fetchone()['c']
    
    cursor.execute("SELECT COUNT(*) as c FROM scenarios WHERE is_active=TRUE")
    active_scenarios = cursor.fetchone()['c']
    
    cursor.execute("SELECT SUM(credits_balance) as c FROM users")
    total_credits = cursor.fetchone()['c'] or 0
    
    cursor.execute("SELECT COUNT(*) as c FROM logs WHERE created_at > NOW() - INTERVAL '24 HOURS'")
    daily_runs = cursor.fetchone()['c']
    
    # Listeler
    cursor.execute("SELECT * FROM scenarios ORDER BY scenario_id DESC")
    scenarios = cursor.fetchall()
    
    cursor.execute("SELECT * FROM users ORDER BY user_id DESC LIMIT 50")
    users = cursor.fetchall()
    
    conn.close()
    
    stats = {
        "users": total_users,
        "scenarios": active_scenarios,
        "credits": total_credits,
        "daily_runs": daily_runs
    }
    
    return templates.TemplateResponse("admin_dashboard.html", {
        "request": request, 
        "scenarios": scenarios,
        "users": users,
        "stats": stats
    })

# --- SENARYO EKLEME / GÜNCELLEME ---
@app.post("/admin/save-scenario")
async def save_scenario(
    scenario_id: str = Form(None),
    risk_title: str = Form(...),
    group_name: str = Form(...),
    source_type: str = Form(...),
    code_payload: str = Form(...),
    risk_message: str = Form(""),
    legislation: str = Form(""),
    risk_reason: str = Form(""),
    solution_suggestion: str = Form(""),
    cross_check_rule: str = Form(""),
    cost_per_run: int = Form(1),
    is_active: bool = Form(True),
    is_pinned: bool = Form(False)
):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if scenario_id and scenario_id.isdigit():
        # GÜNCELLEME
        cursor.execute("""
            UPDATE scenarios SET 
                risk_title=%s, group_name=%s, source_type=%s, code_payload=%s,
                risk_message=%s, legislation=%s, risk_reason=%s, solution_suggestion=%s,
                cross_check_rule=%s, cost_per_run=%s, is_active=%s, is_pinned=%s
            WHERE scenario_id=%s
        """, (risk_title, group_name, source_type, code_payload, risk_message, legislation, 
              risk_reason, solution_suggestion, cross_check_rule, cost_per_run, is_active, is_pinned, int(scenario_id)))
    else:
        # YENİ EKLEME
        cursor.execute("""
            INSERT INTO scenarios 
            (risk_title, group_name, source_type, code_payload, risk_message, legislation, 
             risk_reason, solution_suggestion, cross_check_rule, cost_per_run, is_active, is_pinned)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (risk_title, group_name, source_type, code_payload, risk_message, legislation, 
              risk_reason, solution_suggestion, cross_check_rule, cost_per_run, is_active, is_pinned))
        
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin/dashboard", status_code=303)

# --- KULLANICI İŞLEMLERİ (Kredi Yükle / HWID Sıfırla) ---
@app.post("/admin/user-action")
async def user_action(
    user_id: int = Form(...),
    action: str = Form(...), # 'add_credits' veya 'reset_hwid'
    amount: int = Form(0)
):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if action == "add_credits":
        cursor.execute("UPDATE users SET credits_balance = credits_balance + %s WHERE user_id=%s", (amount, user_id))
    elif action == "reset_hwid":
        cursor.execute("UPDATE users SET hwid_lock = NULL WHERE user_id=%s", (user_id,))
    
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin/dashboard", status_code=303)


# =========================================================
# BÖLÜM B: API (MÜŞTERİ TARAFI - LAUNCHER VE ENGINE)
# =========================================================

class LoginRequest(BaseModel):
    email: str
    password: str
    hwid: str
    version: str # Client versiyonu

class CodeRequest(BaseModel):
    token: str
    scenario_id: int

# 1. GİRİŞ VE GÜVENLİK KONTROLÜ
@app.post("/api/login")
def api_login(req: LoginRequest):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. Versiyon Kontrolü (SYSTEM SETTINGS)
    cursor.execute("SELECT setting_value FROM system_settings WHERE setting_key='latest_version'")
    latest_ver = cursor.fetchone()['setting_value']
    cursor.execute("SELECT setting_value FROM system_settings WHERE setting_key='force_update'")
    force_update = cursor.fetchone()['setting_value']
    
    if req.version != latest_ver and force_update == '1':
        conn.close()
        return JSONResponse(status_code=426, content={"message": "Güncelleme Gerekli!", "url": "https://ghost.com/indir"})

    # 2. Kullanıcı Doğrulama
    cursor.execute("SELECT * FROM users WHERE email = %s", (req.email,))
    user = cursor.fetchone()
    
    if not user:
        conn.close()
        return JSONResponse(status_code=401, content={"message": "Kullanıcı bulunamadı"})
        
    if user['status'] != 'Aktif':
        conn.close()
        return JSONResponse(status_code=403, content={"message": f"Hesabınız: {user['status']}"})

    input_hash = hashlib.sha256(req.password.encode()).hexdigest()
    if input_hash != user['password_hash']:
        conn.close()
        return JSONResponse(status_code=401, content={"message": "Hatalı Şifre"})
        
    # 3. HWID (Donanım Kilidi) Kontrolü
    if user['hwid_lock'] is None:
        cursor.execute("UPDATE users SET hwid_lock = %s WHERE user_id = %s", (req.hwid, user['user_id']))
        conn.commit()
    elif user['hwid_lock'] != req.hwid:
        conn.close()
        return JSONResponse(status_code=403, content={"message": "Lisans Hatası: Bu hesap başka bir bilgisayara kilitli!"})
        
    conn.close()
    
    token = f"TOKEN_{user['user_id']}_{req.hwid}"
    return {
        "status": "success", 
        "token": token, 
        "credits": user['credits_balance'], 
        "company": user['company_name'],
        "role": user['role']
    }

# 2. MENÜ GETİR (Client için)
@app.get("/api/get-menu")
def get_menu(token: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    # Sadece aktif senaryoların başlıklarını gönder (Kodlar gitmez)
    cursor.execute("""
        SELECT scenario_id as id, group_name, risk_title, source_type, risk_message, 
               legislation, risk_reason, solution_suggestion, cross_check_rule as cross_check, 
               cost_per_run, is_pinned
        FROM scenarios 
        WHERE is_active = TRUE
    """)
    scenarios = cursor.fetchall()
    conn.close()
    return {"scenarios": scenarios}

# 3. KOD ÇEK (Kredi Düşer ve Loglar)
@app.post("/api/get-code")
def get_code(req: CodeRequest):
    try:
        user_id = int(req.token.split('_')[1])
    except:
        return JSONResponse(status_code=401, content={"message": "Geçersiz Token"})
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Kredi Kontrolü
    cursor.execute("SELECT credits_balance FROM users WHERE user_id = %s", (user_id,))
    user = cursor.fetchone()
    if not user or user['credits_balance'] <= 0:
        conn.close()
        return JSONResponse(status_code=402, content={"message": "Yetersiz Kredi!"})
        
    # Senaryo ve Kod Çekme
    cursor.execute("SELECT code_payload, cost_per_run FROM scenarios WHERE scenario_id = %s", (req.scenario_id,))
    scen = cursor.fetchone()
    
    if not scen:
        conn.close()
        return JSONResponse(status_code=404, content={"message": "Senaryo yok"})
        
    cost = scen['cost_per_run']
    
    # İşlem: Kredi Düş + Log Yaz
    cursor.execute("UPDATE users SET credits_balance = credits_balance - %s WHERE user_id = %s", (cost, user_id))
    cursor.execute("""
        INSERT INTO logs (user_id, action, scenario_id, created_at) 
        VALUES (%s, 'RUN_SCENARIO', %s, NOW())
    """, (user_id, req.scenario_id))
    
    conn.commit()
    conn.close()
    
    return {"code": scen['code_payload']}
