import os
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, Request, Form, Body
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
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

templates = Jinja2Templates(directory="templates")

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

# --- API ENDPOINTS ---

@app.post("/api/login")
async def api_login(payload: dict = Body(...)):
    email = payload.get("email")
    password = payload.get("password")
    hwid = payload.get("hwid") 

    conn = get_db_connection()
    if not conn: return JSONResponse(content={"status": "error", "message": "DB Hatası"}, status_code=500)
    
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()
        
        settings = get_system_settings(cursor)
        
        if user:
            input_hash = hashlib.sha256(password.encode()).hexdigest()
            if input_hash == user["password_hash"]:
                # HWID Kilit Kontrolü
                current_lock = user.get("hwid_lock")
                if not current_lock:
                    cursor.execute("UPDATE users SET hwid_lock = %s WHERE user_id = %s", (hwid, user["user_id"]))
                    conn.commit()
                elif current_lock != "UNKNOWN_HWID" and current_lock != hwid:
                    conn.close()
                    return JSONResponse(content={"status": "error", "message": "Yetkisiz Cihaz!"}, status_code=403)

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
        return JSONResponse(content={"status": "error", "message": "Hatalı Giriş"}, status_code=401)
    except Exception as e:
        if conn: conn.close()
        return JSONResponse(content={"status": "error", "message": str(e)}, status_code=500)

@app.get("/api/get-menu")
async def get_menu(token: str):
    conn = get_db_connection()
    if not conn: return {"scenarios": []}
    try:
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
    except Exception as e:
        print(f"Menü Hatası: {e}")
        conn.close()
        return {"scenarios": []}

@app.post("/api/get-code")
async def get_code(payload: dict = Body(...)):
    """
    TEKLİ ÇEKİM: Sadece tek bir senaryonun kodunu çeker.
    Maliyet: O senaryonun bağlı olduğu grubun maliyeti kadar düşer.
    """
    token = payload.get("token")
    scenario_id = payload.get("scenario_id")
    
    conn = get_db_connection()
    if not conn: return JSONResponse(content={"error": "Sunucu Bağlantısı Yok"}, status_code=503)

    try:
        cursor = conn.cursor()
        
        try: user_id = int(token)
        except: 
            conn.close()
            return JSONResponse(content={"error": "Token hatası"}, status_code=401)

        # 1. Senaryo ve Grup Fiyatını Bul
        sql_query = """
            SELECT s.code_payload, 
                   COALESCE(g.cost_per_run, 50) as dynamic_cost,
                   s.scenario_id
            FROM scenarios s
            LEFT JOIN scenario_groups g ON s.group_name = g.group_name
            WHERE s.scenario_id = %s
        """
        cursor.execute(sql_query, (scenario_id,))
        scenario = cursor.fetchone()
        
        if not scenario:
            conn.close()
            return JSONResponse(content={"error": "Senaryo bulunamadı"}, status_code=404)
            
        cost = scenario['dynamic_cost']
        
        # 2. Kullanıcı Kredisini Kontrol Et
        cursor.execute("SELECT credits_balance FROM users WHERE user_id = %s", (user_id,))
        user = cursor.fetchone()
        
        if not user:
            conn.close()
            return JSONResponse(content={"error": "Kullanıcı bulunamadı"}, status_code=401)

        if user['credits_balance'] < cost:
            conn.close()
            return JSONResponse(content={"error": "YETERSİZ KREDİ"}, status_code=402)
        
        # 3. Krediyi Düş ve Logla
        if cost > 0:
            cursor.execute("UPDATE users SET credits_balance = credits_balance - %s WHERE user_id = %s", (cost, user_id))
            
            cursor.execute("""
                INSERT INTO logs (user_id, action, scenario_id, credit_cost) 
                VALUES (%s, %s, %s, %s)
            """, (user_id, 'run_scenario', int(scenario_id), int(cost)))
            
            conn.commit()
        
        conn.close()
        return {"code": scenario["code_payload"]}

    except Exception as e:
        print(f"Kod Çekme Hatası: {e}")
        if conn: conn.close()
        return JSONResponse(content={"error": f"Sunucu Hatası: {str(e)}"}, status_code=500)

# --- YENİ EKLENEN TOPLU ÇEKİM FONKSİYONU ---
@app.post("/api/get-group-package")
async def get_group_package(payload: dict = Body(...)):
    """
    TOPLU ÇEKİM: Bir gruba ait TÜM senaryoları tek seferde çeker.
    Maliyet: Sadece grubun belirlediği tek bir ücret kesilir.
    """
    token = payload.get("token")
    group_name = payload.get("group_name")
    
    conn = get_db_connection()
    if not conn: return JSONResponse(content={"error": "Sunucu Bağlantısı Yok"}, status_code=503)

    try:
        cursor = conn.cursor()
        
        try: user_id = int(token)
        except: 
            conn.close()
            return JSONResponse(content={"error": "Token hatası"}, status_code=401)

        # 1. Grubun Maliyetini Bul
        cursor.execute("SELECT cost_per_run FROM scenario_groups WHERE group_name = %s", (group_name,))
        group_data = cursor.fetchone()
        
        # Grup bulunamazsa varsayılan 50 kredi
        cost = group_data['cost_per_run'] if group_data else 50
        
        # 2. Kullanıcı Bakiyesini Kontrol Et
        cursor.execute("SELECT credits_balance FROM users WHERE user_id = %s", (user_id,))
        user = cursor.fetchone()
        
        if not user or user['credits_balance'] < cost:
            conn.close()
            return JSONResponse(content={"error": "YETERSİZ KREDİ"}, status_code=402)
        
        # 3. Krediyi Düş (TEK SEFERLİK)
        if cost > 0:
            cursor.execute("UPDATE users SET credits_balance = credits_balance - %s WHERE user_id = %s", (cost, user_id))
            
            # Log Kaydı: Details sütunu yerine scenario_id'ye NULL veya özel kod, 
            # action'a 'run_group' yazıyoruz.
            # Tablo yapınızda 'details' sütunu yoksa, scenario_id alanını 0 olarak kullanabiliriz.
            cursor.execute("""
                INSERT INTO logs (user_id, action, scenario_id, credit_cost) 
                VALUES (%s, %s, %s, %s)
            """, (user_id, 'run_group', 0, int(cost)))
            
            conn.commit()
            
        # 4. Gruba Ait Tüm Senaryoları Çek
        cursor.execute("""
            SELECT scenario_id as id, risk_title, code_payload, source_type, 
                   risk_message, legislation, risk_reason, solution_suggestion, 
                   cross_check_rule as cross_check, is_pinned, group_name
            FROM scenarios 
            WHERE group_name = %s AND is_active = TRUE
        """, (group_name,))
        
        scenarios = cursor.fetchall()
        
        conn.close()
        return {
            "success": True, 
            "cost_deducted": cost,
            "scenarios": scenarios
        }

    except Exception as e:
        print(f"Grup Paket Hatası: {e}")
        if conn: conn.close()
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/api/get-balance")
async def get_balance(token: str):
    conn = get_db_connection()
    if not conn: return {"credits": 0}
    try:
        cursor = conn.cursor()
        user_id = int(token)
        cursor.execute("SELECT credits_balance FROM users WHERE user_id = %s", (user_id,))
        res = cursor.fetchone()
        conn.close()
        return {"credits": res["credits_balance"] if res else 0}
    except:
        conn.close()
        return {"credits": 0}

# --- WEB ADMIN ENDPOINTS ---
@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/web-login")
async def web_login(request: Request, email: str = Form(...), password: str = Form(...)):
    conn = get_db_connection()
    if not conn: return templates.TemplateResponse("login.html", {"request": request, "error": "Veritabanı Bağlantı Hatası"})
    
    try:
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
    except Exception as e:
        if conn: conn.close()
        return templates.TemplateResponse("login.html", {"request": request, "error": f"Hata: {e}"})

@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    return templates.TemplateResponse("admin_dashboard.html", {"request": request, "stats": {}, "scenarios": []})
