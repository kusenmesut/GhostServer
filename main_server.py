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

# --- 1. VERİTABANI BAĞLANTISI ---
def get_db_connection():
    db_url = os.environ.get('DATABASE_URL')
    if not db_url: return None
    try:
        conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
        return conn
    except Exception as e:
        print(f"DB Hatası: {e}")
        return None

# --- 2. BAŞLANGIÇ: TABLOLARI OLUŞTUR ---
@app.on_event("startup")
def startup_db_init():
    conn = get_db_connection()
    if not conn: return
    cursor = conn.cursor()
    
    # Kullanıcılar
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            credits INTEGER DEFAULT 0,
            hwid TEXT,
            role TEXT DEFAULT 'user',
            company_name TEXT,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    
    # Senaryolar (Tüm detaylarıyla)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scenarios (
            id SERIAL PRIMARY KEY,
            group_name TEXT,
            risk_title TEXT NOT NULL,
            source_type TEXT DEFAULT 'HAM VERI',
            code_payload TEXT,
            risk_message TEXT,
            legislation TEXT,
            risk_reason TEXT,
            solution_suggestion TEXT,
            cross_check TEXT,
            cost_per_run INTEGER DEFAULT 1,
            is_active BOOLEAN DEFAULT TRUE,
            is_pinned BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    
    # Loglar
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id SERIAL PRIMARY KEY,
            user_id INTEGER,
            scenario_id INTEGER,
            action TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    
    # Default Admin
    admin_pass = hashlib.sha256("123456".encode()).hexdigest()
    cursor.execute("""
        INSERT INTO users (email, password_hash, role, credits, company_name)
        VALUES (%s, %s, 'admin', 9999, 'Sistem Yöneticisi')
        ON CONFLICT (email) DO NOTHING;
    """, ("admin@ghost.com", admin_pass))
    
    conn.commit()
    conn.close()
    print("✅ Sistem Hazır: Tablolar ve Admin Kontrol Edildi.")

# =========================================================
# BÖLÜM A: WEB ARAYÜZÜ (YÖNETİCİ PANELİ)
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
            if user["role"] == "admin":
                return RedirectResponse(url="/admin/dashboard", status_code=303)
            else:
                return templates.TemplateResponse("user_dashboard.html", {"request": request, "user": user})
    return templates.TemplateResponse("login.html", {"request": request, "error": "Hatalı Giriş"})

@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # İstatistikler
    cursor.execute("SELECT COUNT(*) as c FROM scenarios")
    total = cursor.fetchone()['c']
    cursor.execute("SELECT COUNT(*) as c FROM scenarios WHERE is_active=TRUE")
    active = cursor.fetchone()['c']
    cursor.execute("SELECT SUM(cost_per_run) as c FROM scenarios")
    total_cost = cursor.fetchone()['c'] or 0
    
    # Senaryo Listesi
    cursor.execute("SELECT * FROM scenarios ORDER BY id DESC")
    scenarios = cursor.fetchall()
    conn.close()
    
    stats = {"total": total, "active": active, "passive": total-active, "total_cost": total_cost}
    return templates.TemplateResponse("admin_dashboard.html", {"request": request, "scenarios": scenarios, "stats": stats})

@app.post("/admin/add-scenario")
async def add_scenario_web(
    id: str = Form(None), # Eğer ID gelirse UPDATE yapar
    group_name: str = Form(...),
    risk_title: str = Form(...),
    source_type: str = Form(...),
    code_payload: str = Form(...),
    cost_per_run: int = Form(...),
    risk_message: str = Form(""),
    legislation: str = Form(""),
    risk_reason: str = Form(""),
    solution_suggestion: str = Form(""),
    cross_check: str = Form(""),
    is_active: bool = Form(True), # Checkbox gelmezse False olur, Form'da value="true" olmalı
    is_pinned: bool = Form(False)
):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Checkbox düzeltmesi (HTML formlarında işaretli değilse veri gelmez)
    # FastAPI Form bool dönüşümünü otomatik yapar ama dikkatli olmak lazım.
    
    if id and id.strip():
        # GÜNCELLEME (UPDATE)
        cursor.execute("""
            UPDATE scenarios SET 
                group_name=%s, risk_title=%s, source_type=%s, code_payload=%s,
                risk_message=%s, legislation=%s, risk_reason=%s, solution_suggestion=%s,
                cross_check=%s, cost_per_run=%s, is_active=%s, is_pinned=%s
            WHERE id=%s
        """, (group_name, risk_title, source_type, code_payload, risk_message, legislation, 
              risk_reason, solution_suggestion, cross_check, cost_per_run, is_active, is_pinned, id))
    else:
        # YENİ EKLEME (INSERT)
        cursor.execute("""
            INSERT INTO scenarios 
            (group_name, risk_title, source_type, code_payload, risk_message, legislation, 
             risk_reason, solution_suggestion, cross_check, cost_per_run, is_active, is_pinned)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (group_name, risk_title, source_type, code_payload, risk_message, legislation, 
              risk_reason, solution_suggestion, cross_check, cost_per_run, is_active, is_pinned))
        
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin/dashboard", status_code=303)

@app.post("/admin/delete-scenario")
async def delete_scenario_web(id: int = Form(...)):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM scenarios WHERE id = %s", (id,))
    conn.commit()
    conn.close()
    return {"status": "success"}


# =========================================================
# BÖLÜM B: API (MASAÜSTÜ İSTEMCİSİ İÇİN)
# =========================================================

class LoginRequest(BaseModel):
    email: str
    password: str
    hwid: str

class CodeRequest(BaseModel):
    token: str
    scenario_id: int

# 1. Müşteri Girişi ve Lisans Kontrolü
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
        
    # HWID Kontrolü (İlk girişse kaydet, değilse kontrol et)
    if user['hwid'] is None:
        cursor.execute("UPDATE users SET hwid = %s WHERE id = %s", (req.hwid, user['id']))
        conn.commit()
    elif user['hwid'] != req.hwid:
        conn.close()
        return JSONResponse(status_code=403, content={"message": "Lisans Hatası: Farklı cihaz algılandı!"})
        
    conn.close()
    # Basit Token (Prodüksiyonda JWT olmalı)
    token = f"TOKEN_{user['id']}_{req.hwid}"
    return {"status": "success", "token": token, "credits": user['credits'], "company": user['company_name']}

# 2. Menüyü Getir (SADECE Başlıklar - KOD YOK!)
@app.get("/api/get-menu")
def get_menu(token: str):
    # Token kontrolü yapılmalı (Basitleştirildi)
    if not token.startswith("TOKEN_"):
         return JSONResponse(status_code=401, content={"message": "Yetkisiz Erişim"})
         
    conn = get_db_connection()
    cursor = conn.cursor()
    # Code_payload sütununu ÇEKMİYORUZ. Güvenlik burada başlıyor.
    cursor.execute("""
        SELECT id, group_name, risk_title, source_type, risk_message, 
               legislation, risk_reason, solution_suggestion, cross_check, cost_per_run, is_pinned
        FROM scenarios 
        WHERE is_active = TRUE
    """)
    scenarios = cursor.fetchall()
    conn.close()
    return {"scenarios": scenarios}

# 3. Kodu Getir (Şifreli Payload - Sadece Çalıştırma Anında)
@app.post("/api/get-code")
def get_code(req: CodeRequest):
    # 1. Token'dan User ID'yi bul
    try:
        user_id = int(req.token.split('_')[1])
    except:
        return JSONResponse(status_code=401, content={"message": "Geçersiz Token"})
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 2. Kullanıcının kredisini kontrol et
    cursor.execute("SELECT credits FROM users WHERE id = %s", (user_id,))
    user = cursor.fetchone()
    if not user or user['credits'] <= 0:
        conn.close()
        return JSONResponse(status_code=402, content={"message": "Yetersiz Kredi! Lütfen yükleme yapın."})
        
    # 3. Senaryoyu ve Maliyeti Çek
    cursor.execute("SELECT code_payload, cost_per_run FROM scenarios WHERE id = %s", (req.scenario_id,))
    scen = cursor.fetchone()
    
    if not scen:
        conn.close()
        return JSONResponse(status_code=404, content={"message": "Senaryo bulunamadı"})
        
    # 4. Krediyi Düş ve Logla
    cost = scen['cost_per_run']
    cursor.execute("UPDATE users SET credits = credits - %s WHERE id = %s", (cost, user_id))
    cursor.execute("INSERT INTO logs (user_id, scenario_id, action) VALUES (%s, %s, 'RUN')", (user_id, req.scenario_id))
    conn.commit()
    conn.close()
    
    # 5. Kodu Gönder (Burada kod ekstradan AES ile şifrelenebilir)
    return {"code": scen['code_payload'], "remaining_credits": user['credits'] - cost}
