import os
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, Request, Form
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel
import hashlib

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
# BÖLÜM A: WEB ARAYÜZÜ (ADMİN PANELİ)
# =========================================================

@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    conn = get_db_connection()
    if not conn: return "Veritabanı Bağlantı Hatası"
    
    cursor = conn.cursor()
    
    # İstatistikler
    try:
        cursor.execute("SELECT COUNT(*) as c FROM users WHERE role!='Admin'")
        total_users = cursor.fetchone()['c']
    except: total_users = 0
    
    try:
        cursor.execute("SELECT COUNT(*) as c FROM scenarios WHERE is_active=TRUE")
        active_scenarios = cursor.fetchone()['c']
    except: active_scenarios = 0
    
    # Listeler
    try:
        cursor.execute("SELECT * FROM scenarios ORDER BY scenario_id DESC")
        scenarios = cursor.fetchall()
    except: scenarios = []
    
    conn.close()
    
    stats = {
        "users": total_users,
        "scenarios": active_scenarios,
        "revenue": 0,
        "errors": 0
    }
    
    return templates.TemplateResponse("admin_dashboard.html", {
        "request": request, 
        "stats": stats, 
        "scenarios": scenarios
    })

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
    
    # Güncelleme veya Ekleme
    if scenario_id and scenario_id.isdigit():
        cursor.execute("""
            UPDATE scenarios SET 
                risk_title=%s, group_name=%s, source_type=%s, code_payload=%s,
                risk_message=%s, legislation=%s, risk_reason=%s, solution_suggestion=%s,
                cross_check_rule=%s, cost_per_run=%s, is_active=%s, is_pinned=%s
            WHERE scenario_id=%s
        """, (risk_title, group_name, source_type, code_payload, risk_message, legislation, 
              risk_reason, solution_suggestion, cross_check_rule, cost_per_run, is_active, is_pinned, int(scenario_id)))
    else:
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
            return RedirectResponse(url="/admin/dashboard", status_code=303)

    return templates.TemplateResponse("login.html", {"request": request, "error": "Hatalı Giriş"})

# =========================================================
# BÖLÜM B: API (LAUNCHER VE CLIENT İÇİN - EKSİK OLAN KISIM)
# =========================================================

class LoginRequest(BaseModel):
    email: str
    password: str
    hwid: str
    version: str = "1.0.0"

class CodeRequest(BaseModel):
    token: str
    scenario_id: int

# 1. GİRİŞ API (Launcher Buraya Bağlanır)
@app.post("/api/login")
def api_login(req: LoginRequest):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE email = %s", (req.email,))
    user = cursor.fetchone()
    
    if not user:
        conn.close()
        return JSONResponse(status_code=401, content={"message": "Kullanıcı bulunamadı"})
        
    input_hash = hashlib.sha256(req.password.encode()).hexdigest()
    if input_hash != user['password_hash']:
        conn.close()
        return JSONResponse(status_code=401, content={"message": "Hatalı Şifre"})
    
    # HWID Güncelleme/Kontrol (Basit)
    if user.get('hwid_lock') is None:
        cursor.execute("UPDATE users SET hwid_lock = %s WHERE user_id = %s", (req.hwid, user['user_id']))
        conn.commit()
    
    conn.close()
    token = f"TOKEN_{user['user_id']}_{req.hwid}"
    
    return {
        "status": "success", 
        "token": token, 
        "credits": user.get('credits_balance', 0), 
        "company": user.get('company_name', 'Firma'),
        "role": user.get('role', 'User')
    }

# 2. MENÜ GETİR (Client Menüyü Buradan Çeker)
@app.get("/api/get-menu")
def get_menu(token: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT scenario_id as id, group_name, risk_title, source_type, risk_message, 
                   legislation, risk_reason, solution_suggestion, cross_check_rule as cross_check, 
                   cost_per_run, is_pinned
            FROM scenarios 
            WHERE is_active = TRUE
        """)
        scenarios = cursor.fetchall()
    except Exception as e:
        print(f"Menü Hatası: {e}")
        scenarios = []
    
    conn.close()
    return {"scenarios": scenarios}

# 3. KOD ÇEK (Engine Buradan Kod Çeker)
@app.post("/api/get-code")
def get_code(req: CodeRequest):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Senaryo Kodunu Çek
    cursor.execute("SELECT code_payload, cost_per_run FROM scenarios WHERE scenario_id = %s", (req.scenario_id,))
    scen = cursor.fetchone()
    
    conn.close()
    
    if not scen:
        return JSONResponse(status_code=404, content={"message": "Senaryo yok"})
        
    return {"code": scen['code_payload']}
