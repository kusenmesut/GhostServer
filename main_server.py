import os
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, Body
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import hashlib

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db_connection():
    db_url = os.environ.get('DATABASE_URL')
    if not db_url: return None
    try:
        conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
        return conn
    except Exception as e:
        print(f"DB Hatası: {e}")
        return None

# --- YARDIMCI FONKSİYON: AYARLARI ÇEK ---
def get_system_settings(cursor):
    cursor.execute("SELECT * FROM system_settings")
    rows = cursor.fetchall()
    settings = {row['setting_key']: row['setting_value'] for row in rows}
    return settings

# 1. LOGIN API (GÜVENLİK EKLENDİ)
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
    
    # Sistem ayarlarını çek (Hash ve Versiyon kontrolü için)
    settings = get_system_settings(cursor)
    
    if user:
        input_hash = hashlib.sha256(password.encode()).hexdigest()
        if input_hash == user["password_hash"]:
            
            # HWID Kontrolü
            current_lock = user.get("hwid_lock")
            if not current_lock:
                cursor.execute("UPDATE users SET hwid_lock = %s WHERE user_id = %s", (hwid, user["user_id"]))
                conn.commit()
            elif current_lock != "UNKNOWN_HWID" and current_lock != hwid:
                conn.close()
                return JSONResponse(content={"status": "error", "message": "Yetkisiz Cihaz!"}, status_code=403)

            fake_token = f"{user['user_id']}" # Basit ID bazlı token
            
            response_data = {
                "status": "success",
                "token": fake_token,
                "company": user.get("company_name"),
                "credits": user.get("credits_balance", 0),
                # GÜVENLİK VERİLERİ CLIENT'A GİDİYOR
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
    return JSONResponse(content={"status": "error", "message": "Hatalı Giriş"}, status_code=401)

# 2. MENU GETİR
@app.get("/api/get-menu")
async def get_menu(token: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT scenario_id as id, group_name, risk_title, description, 
               risk_message, legislation, risk_reason, solution_suggestion, 
               source_type, cost_per_run, is_active, cross_check_rule as cross_check
        FROM scenarios WHERE is_active = TRUE
    """)
    scenarios = cursor.fetchall()
    conn.close()
    return {"scenarios": scenarios}

# 3. KOD ÇEK (KREDİ SİSTEMİ EKLENDİ)
@app.post("/api/get-code")
async def get_code(payload: dict = Body(...)):
    token = payload.get("token")  # Bu örnekte token = user_id
    scenario_id = payload.get("scenario_id")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        user_id = int(token)
    except:
        return JSONResponse(content={"error": "Geçersiz Token"}, status_code=401)

    # 1. Senaryoyu ve Maliyetini Bul
    cursor.execute("SELECT code_payload, cost_per_run FROM scenarios WHERE scenario_id = %s", (scenario_id,))
    scenario = cursor.fetchone()
    
    if not scenario:
        conn.close()
        return JSONResponse(content={"error": "Senaryo yok"}, status_code=404)
        
    cost = scenario['cost_per_run'] or 0
    
    # 2. Kullanıcının Kredisine Bak
    cursor.execute("SELECT credits_balance FROM users WHERE user_id = %s", (user_id,))
    user = cursor.fetchone()
    
    if not user or user['credits_balance'] < cost:
        conn.close()
        return JSONResponse(content={"error": "YETERSİZ KREDİ! Lütfen kredi yükleyin."}, status_code=402)
    
    # 3. Krediyi Düş ve Log Yaz
    try:
        # Kredi Düş
        cursor.execute("UPDATE users SET credits_balance = credits_balance - %s WHERE user_id = %s", (cost, user_id))
        
        # Log Yaz
        cursor.execute("INSERT INTO logs (user_id, action, details) VALUES (%s, %s, %s)", 
                       (user_id, 'run_scenario', f"Scenario ID: {scenario_id} - Cost: {cost}"))
        
        conn.commit()
    except Exception as e:
        conn.close()
        return JSONResponse(content={"error": f"İşlem Hatası: {e}"}, status_code=500)
    
    conn.close()
    # 4. Kodu Teslim Et
    return {"code": scenario["code_payload"]}
# --- main_server.py içine ekle ---

# 4. BAKİYE SORGULAMA (YENİ)
@app.get("/api/get-balance")
async def get_balance(token: str):
    conn = get_db_connection()
    if not conn: return JSONResponse(content={"error": "DB Hatası"}, status_code=500)
    
    cursor = conn.cursor()
    try:
        # Token şu an user_id olarak kullanılıyor
        user_id = int(token)
        cursor.execute("SELECT credits_balance FROM users WHERE user_id = %s", (user_id,))
        res = cursor.fetchone()
        conn.close()
        
        if res:
            return {"credits": res["credits_balance"]}
        else:
            return {"credits": 0}
    except:
        conn.close()
        return {"credits": 0}
