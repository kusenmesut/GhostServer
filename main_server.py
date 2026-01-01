import os
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, Request, Form, Depends
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
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

# --- YARDIMCI: LOGIN KONTROLÜ (COOKIE VEYA SESSION OLMADIĞI İÇİN BASİT) ---
# Not: Gerçekte JWT veya Session kullanılmalı. Şimdilik admin olduğunu varsayıyoruz.

# --- DASHBOARD (KOKPİT) ---
@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    conn = get_db_connection()
    if not conn: return "Veritabanı Bağlantı Hatası"
    
    cursor = conn.cursor()
    
    # 1. İstatistikleri Çek (Kartlar İçin)
    cursor.execute("SELECT COUNT(*) as count FROM users WHERE role='user'")
    total_users = cursor.fetchone()['count']
    
    cursor.execute("SELECT COUNT(*) as count FROM scenarios WHERE is_active=TRUE")
    active_scenarios = cursor.fetchone()['count']
    
    # 2. Senaryo Listesini Çek (Tablo İçin)
    cursor.execute("SELECT * FROM scenarios ORDER BY id DESC")
    scenarios = cursor.fetchall()
    
    conn.close()
    
    # İstatistik verilerini HTML'e gönder
    stats = {
        "users": total_users,
        "scenarios": active_scenarios,
        "revenue": total_users * 100, # Örnek ciro hesabı
        "errors": 0
    }
    
    return templates.TemplateResponse("admin_dashboard.html", {
        "request": request, 
        "stats": stats, 
        "scenarios": scenarios
    })

# --- SENARYO EKLEME İŞLEMİ (FORM POST) ---
@app.post("/admin/add-scenario")
async def add_scenario(
    request: Request,
    group_name: str = Form(...),
    risk_title: str = Form(...),
    description: str = Form(...),
    source_type: str = Form(...),
    code_payload: str = Form(...),
    risk_message: str = Form(...),
    legislation: str = Form(...),
    risk_reason: str = Form(...),
    solution_suggestion: str = Form(...),
    cost_per_run: int = Form(...)
):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            INSERT INTO scenarios 
            (group_name, risk_title, description, source_type, code_payload, 
             risk_message, legislation, risk_reason, solution_suggestion, cost_per_run)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (group_name, risk_title, description, source_type, code_payload,
              risk_message, legislation, risk_reason, solution_suggestion, cost_per_run))
        conn.commit()
    except Exception as e:
        print(f"Ekleme Hatası: {e}")
    finally:
        conn.close()
        
    return RedirectResponse(url="/admin/dashboard", status_code=303)

# --- WEB LOGIN ---
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
            if user["role"] == "admin":
                return RedirectResponse(url="/admin/dashboard", status_code=303)
            else:
                return templates.TemplateResponse("user_dashboard.html", {"request": request, "user": user})

    return templates.TemplateResponse("login.html", {"request": request, "error": "Hatalı Giriş"})