import os
import time
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, Request, Form
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
import hashlib

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# --- VERİTABANI BAĞLANTISI ---
def get_db_connection():
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        print("UYARI: DATABASE_URL bulunamadı!")
        return None
    try:
        conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
        return conn
    except Exception as e:
        print(f"DB Bağlantı Hatası: {e}")
        return None

# --- TABLOLARI OLUŞTUR (BAŞLANGIÇTA) ---
@app.on_event("startup")
def startup_db_init():
    conn = get_db_connection()
    if not conn: return
    
    cursor = conn.cursor()
    try:
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        # Senaryolar (Kodlar burada saklanacak)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS scenarios (
                id SERIAL PRIMARY KEY,
                group_name TEXT,
                risk_title TEXT NOT NULL,
                code_payload TEXT,
                description TEXT,
                cost_per_run INTEGER DEFAULT 1,
                is_active BOOLEAN DEFAULT TRUE
            );
        """)

        # Varsayılan Admin (Şifre: 123456)
        admin_pass = hashlib.sha256("123456".encode()).hexdigest()
        cursor.execute("""
            INSERT INTO users (email, password_hash, role, credits, company_name)
            VALUES (%s, %s, 'admin', 9999, 'System Admin')
            ON CONFLICT (email) DO NOTHING;
        """, ("admin@ghost.com", admin_pass))
        
        conn.commit()
        print("✅ Veritabanı tabloları ve Admin hesabı hazır.")
    except Exception as e:
        print(f"Tablo oluşturma hatası: {e}")
    finally:
        cursor.close()
        conn.close()

# --- WEB ARAYÜZLERİ ---

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
                return templates.TemplateResponse("admin_dashboard.html", {"request": request, "user": user})
            else:
                return templates.TemplateResponse("user_dashboard.html", {"request": request, "user": user})

    return templates.TemplateResponse("login.html", {"request": request, "error": "Hatalı Giriş Bilgileri"})

# --- API (LAUNCHER İÇİN) ---
@app.post("/auth/login")
def api_login(data: dict):
    email = data.get("email")
    password = data.get("password")
    hwid = data.get("hwid")
    
    conn = get_db_connection()
    if not conn: return {"status": "error", "message": "Sunucu hatası"}
    
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
    user = cursor.fetchone()
    conn.close()
    
    if not user: return {"status": "error", "message": "Kullanıcı yok"}
    
    if hashlib.sha256(password.encode()).hexdigest() == user['password_hash']:
        return {"status": "success", "token": f"TOKEN_{user['id']}", "credits": user['credits']}
    
    return {"status": "error", "message": "Şifre yanlış"}