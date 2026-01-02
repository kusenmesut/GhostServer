import os
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, Body
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import hashlib

app = FastAPI()

# Masaüstü uygulamasının erişimi için CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

# ============================================================
# 🖥️ API ENDPOINTS (LAUNCHER & ENGINE İÇİN)
# ============================================================

# 1. LOGIN API
@app.post("/api/login")
async def api_login(payload: dict = Body(...)):
    email = payload.get("email")
    password = payload.get("password")
    hwid = payload.get("hwid") 

    conn = get_db_connection()
    if not conn: return JSONResponse(content={"status": "error", "message": "Veritabanı hatası"}, status_code=500)
    
    cursor = conn.cursor()
    # ŞEMAYA UYGUN SÜTUN İSİMLERİ: user_id, password_hash, hwid_lock, credits_balance
    cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
    user = cursor.fetchone()
    
    if user:
        input_hash = hashlib.sha256(password.encode()).hexdigest()
        
        # Şifre Kontrolü
        if input_hash == user["password_hash"]:
            
            # --- HWID (DONANIM KİLİDİ) KONTROLÜ ---
            current_lock = user.get("hwid_lock")
            
            # 1. Kilit yoksa kilitle
            if not current_lock:
                cursor.execute("UPDATE users SET hwid_lock = %s WHERE user_id = %s", (hwid, user["user_id"]))
                conn.commit()
            
            # 2. Kilit var ama uyuşmuyorsa REDDET
            elif current_lock != "UNKNOWN_HWID" and current_lock != hwid:
                conn.close()
                return JSONResponse(content={"status": "error", "message": "Yetkisiz Cihaz! Bu hesap başka bilgisayara kilitli."}, status_code=403)

            # Başarılı Giriş - Token Üret (Basit)
            fake_token = f"user_{user['user_id']}_session"
            
            response_data = {
                "status": "success",
                "token": fake_token,
                "company": user.get("company_name", "Tanımsız Firma"),
                "credits": user.get("credits_balance", 0) # Şemaya uygun isim
            }
            conn.close()
            return JSONResponse(content=response_data)
    
    conn.close()
    return JSONResponse(content={"status": "error", "message": "E-posta veya şifre hatalı"}, status_code=401)

# 2. MENU GETİR
@app.get("/api/get-menu")
async def get_menu(token: str):
    if not token: return JSONResponse(content={"error": "Unauthorized"}, status_code=401)

    conn = get_db_connection()
    cursor = conn.cursor()
    # ŞEMAYA UYGUN SÜTUN İSİMLERİ: scenario_id, cross_check_rule
    cursor.execute("""
        SELECT scenario_id as id, group_name, risk_title, description, 
               risk_message, legislation, risk_reason, solution_suggestion, 
               source_type, cost_per_run, is_active, cross_check_rule as cross_check
        FROM scenarios 
        WHERE is_active = TRUE
    """)
    scenarios = cursor.fetchall()
    conn.close()
    
    return {"scenarios": scenarios}

# 3. KOD ÇEK
@app.post("/api/get-code")
async def get_code(payload: dict = Body(...)):
    # token = payload.get("token") # İlerde kontrol edilecek
    scenario_id = payload.get("scenario_id")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    # ŞEMAYA UYGUN SORGUSU: scenario_id
    cursor.execute("SELECT code_payload FROM scenarios WHERE scenario_id = %s", (scenario_id,))
    result = cursor.fetchone()
    conn.close()
    
    if result:
        return {"code": result["code_payload"]}
    else:
        return JSONResponse(content={"error": "Senaryo bulunamadı"}, status_code=404)

@app.get("/")
def home():
    return {"message": "Ghost Server API Aktif"}
