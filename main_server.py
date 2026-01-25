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

# main.py dosyasının başındaki o kısmı bul ve bununla değiştir:

def get_db_connection():
    # 1. Önce Render'ın Environment Variable'ına bak (Cloud için)
    db_url = os.environ.get('DATABASE_URL')
    
    # 2. Eğer Cloud adresi varsa onu kullan
    if db_url:
        try:
            conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
            return conn
        except Exception as e:
            print(f"Cloud DB Hatası: {e}")
            return None
            
    # 3. Eğer Cloud yoksa (Local bilgisayardaysan) bunu kullan
    else:
        try:
            # Burası senin yerel ayarların
            conn = psycopg2.connect(
                dbname='ghost_db', 
                user='ghost_user', 
                password='ghost123', 
                host='localhost', 
                port='5432',
                cursor_factory=RealDictCursor
            )
            return conn
        except Exception as e:
            print(f"Local DB Hatası: {e}")
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

# main_server.py -> /api/login fonksiyonunun GÜNCEL HALİ

@app.post("/api/login")
async def api_login(payload: dict = Body(...)):
    email = payload.get("email")
    password = payload.get("password")
    hwid = payload.get("hwid") 
    device_name = payload.get("pc_name", "Unknown PC") 

    conn = get_db_connection()
    if not conn: return JSONResponse(content={"status": "error", "message": "DB Hatası"}, status_code=500)
    
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()
        
        settings = get_system_settings(cursor)
        
        if user:
            # --- 1. DURUM KONTROLÜ (YENİ EKLENDİ) ---
            # Eğer kullanıcı 'Aktif' değilse, şifreye bile bakmadan reddet.
            if user.get("status") != "Aktif":
                conn.close()
                return JSONResponse(content={"status": "error", "message": "Hesabınız PASİF durumdadır. Lütfen yönetici ile iletişime geçiniz."}, status_code=403)
            # ----------------------------------------

            input_hash = hashlib.sha256(password.encode()).hexdigest()
            if input_hash == user["password_hash"]:
                
                user_id = user["user_id"]
                limit = user.get("max_device_limit", 1) 
                if limit is None: limit = 1
                
                # --- 2. CİHAZ KONTROLÜ ---
                cursor.execute("SELECT * FROM user_devices WHERE user_id = %s AND hwid = %s", (user_id, hwid))
                existing_device = cursor.fetchone()
                
                if existing_device:
                    cursor.execute("UPDATE user_devices SET last_login = CURRENT_TIMESTAMP WHERE device_id = %s", (existing_device['device_id'],))
                    conn.commit()
                else:
                    cursor.execute("SELECT count(*) as cnt FROM user_devices WHERE user_id = %s", (user_id,))
                    res = cursor.fetchone()
                    if res['cnt'] < limit:
                        cursor.execute("INSERT INTO user_devices (user_id, hwid, device_name) VALUES (%s, %s, %s)", (user_id, hwid, device_name))
                        conn.commit()
                    else:
                        conn.close()
                        return JSONResponse(content={"status": "error", "message": f"Cihaz Limiti Doldu! (Maks: {limit} Cihaz)"}, status_code=403)

                # --- GİRİŞ BAŞARILI ---
                fake_token = f"{user['user_id']}"
                response_data = {
                    "status": "success",
                    "token": fake_token,
                    
                    # --- EKLENEN SATIR BURASI ---
                    "download_url": settings.get("download_url", ""), 
                    # ----------------------------
                    
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
        
        # 1. Önce Token'dan Kullanıcıyı ve İzinlerini Bul
        try: user_id = int(token)
        except: return {"scenarios": []}
        
        cursor.execute("SELECT allowed_groups FROM users WHERE user_id = %s", (user_id,))
        user_row = cursor.fetchone()
        
        if not user_row:
            conn.close()
            return {"scenarios": []}
            
        allowed_groups_str = user_row.get("allowed_groups") # Örn: "Stok,Cari" veya None
        
        # 2. SQL Sorgusunu Hazırla
        base_sql = """
            SELECT scenario_id as id, group_name, risk_title, description, 
                risk_message, legislation, risk_reason, solution_suggestion, 
                source_type, cost_per_run, is_active, cross_check_rule as cross_check
            FROM scenarios 
            WHERE is_active = TRUE
        """
        
        params = []
        
        # Eğer kullanıcının grup kısıtlaması VARSA, filtrele
        if allowed_groups_str and len(allowed_groups_str.strip()) > 0:
            # String'i listeye çevirip SQL IN yapısı kuruyoruz
            group_list = allowed_groups_str.split(',')
            base_sql += " AND group_name = ANY(%s)"
            params.append(group_list)
            
        # Kısıtlama yoksa (None ise) tümünü getirir (WHERE is_active = TRUE kalır)

        cursor.execute(base_sql, tuple(params))
        scenarios = cursor.fetchall()
        
        conn.close()
        return {"scenarios": scenarios}
        
    except Exception as e:
        print(f"Menü Hatası: {e}")
        if conn: conn.close()
        return {"scenarios": []}

@app.post("/api/get-code")
async def get_code(payload: dict = Body(...)):
    """
    TEKLİ ÇEKİM (GÜNCELLENDİ): 
    Artık krediyi BURADA DÜŞMÜYOR. Sadece bakiyeyi kontrol ediyor.
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

        # 1. Maliyet Kontrolü
        sql_query = """
            SELECT s.code_payload, 
                   COALESCE(g.cost_per_run, 50) as dynamic_cost
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
        
        # 2. Sadece Bakiye KONTROLÜ (Düşme Yok)
        cursor.execute("SELECT credits_balance FROM users WHERE user_id = %s", (user_id,))
        user = cursor.fetchone()
        
        if not user or user['credits_balance'] < cost:
            conn.close()
            return JSONResponse(content={"error": "YETERSİZ KREDİ"}, status_code=402)
        
        # NOT: Burada 'UPDATE users...' satırını kaldırdık!
        
        conn.close()
        return {"code": scenario["code_payload"], "cost": cost}

    except Exception as e:
        if conn: conn.close()
        return JSONResponse(content={"error": f"Sunucu Hatası: {str(e)}"}, status_code=500)

@app.post("/api/get-group-package")
async def get_group_package(payload: dict = Body(...)):
    """
    TOPLU ÇEKİM (GÜNCELLENDİ):
    Sadece bakiyeyi kontrol eder, krediyi düşmez.
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

        # 1. Maliyet Kontrolü
        cursor.execute("SELECT cost_per_run FROM scenario_groups WHERE group_name = %s", (group_name,))
        group_data = cursor.fetchone()
        cost = group_data['cost_per_run'] if group_data else 50
        
        # 2. Bakiye Kontrolü
        cursor.execute("SELECT credits_balance FROM users WHERE user_id = %s", (user_id,))
        user = cursor.fetchone()
        
        if not user or user['credits_balance'] < cost:
            conn.close()
            return JSONResponse(content={"error": "YETERSİZ KREDİ"}, status_code=402)
            
        # NOT: Para kesme kaldırıldı.
            
        # 3. Senaryoları Getir
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
            "cost_to_deduct": cost, # İstemciye ne kadar keseceğini bildiriyoruz
            "scenarios": scenarios
        }

    except Exception as e:
        if conn: conn.close()
        return JSONResponse(content={"error": str(e)}, status_code=500)

# --- YENİ EKLENEN ENDPOINT: İŞLEM TAMAMLANDI ONAYI ---
@app.post("/api/confirm-transaction")
async def confirm_transaction(payload: dict = Body(...)):
    """
    Kullanıcı analizi başarıyla bitirdiğinde burası çağrılır ve 
    KREDİ BURADA DÜŞER.
    """
    token = payload.get("token")
    item_id = payload.get("item_id") # scenario_id veya group_name
    item_type = payload.get("type")  # 'single' veya 'group'
    
    conn = get_db_connection()
    if not conn: return JSONResponse(content={"error": "DB Hatası"}, status_code=500)
    
    try:
        cursor = conn.cursor()
        user_id = int(token)
        
        cost = 0
        details_txt = ""
        scen_id_log = 0

        # Maliyeti Tekrar Hesapla (Güvenlik İçin)
        if item_type == 'group':
            cursor.execute("SELECT cost_per_run FROM scenario_groups WHERE group_name = %s", (item_id,))
            res = cursor.fetchone()
            cost = res['cost_per_run'] if res else 50
            details_txt = f"Group Run: {item_id}"
            
        elif item_type == 'single':
            # Single modda grup fiyatını bulmamız lazım
            # Önce senaryonun grubunu bul, sonra grubun fiyatını
            sql = """
                SELECT COALESCE(g.cost_per_run, 50) as cost 
                FROM scenarios s
                LEFT JOIN scenario_groups g ON s.group_name = g.group_name
                WHERE s.scenario_id = %s
            """
            cursor.execute(sql, (item_id,))
            res = cursor.fetchone()
            cost = res['cost'] if res else 50
            scen_id_log = int(item_id)
            details_txt = f"Single Run: {item_id}"

        # KREDİYİ DÜŞ VE LOGLA
        if cost > 0:
            cursor.execute("UPDATE users SET credits_balance = credits_balance - %s WHERE user_id = %s", (cost, user_id))
            
            cursor.execute("""
                INSERT INTO logs (user_id, action, scenario_id, credit_cost) 
                VALUES (%s, %s, %s, %s)
            """, (user_id, 'run_complete', scen_id_log, int(cost)))
            
            conn.commit()
            
        conn.close()
        return {"success": True, "deducted": cost}
        
    except Exception as e:
        print(f"Confirm Error: {e}")
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

# --- WEB ADMIN ---
# ✅ Bu kod dosya aramaz, her zaman çalışır
@app.get("/")
async def root():
    return {"message": "Ghost Server is Online 👻", "status": "active"}

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

@app.get("/api/check-version")
async def check_version():
    conn = get_db_connection()
    if not conn: return {"latest_version": "1.0.0"}
    
    try:
        cursor = conn.cursor()
        settings = get_system_settings(cursor)
        conn.close()
        
        return {
            "latest_version": settings.get("latest_version", "1.0.0"),
            "force_update": settings.get("force_update", "False"),
            "download_url": settings.get("download_url", ""),
            "main_exe_hash": settings.get("main_exe_hash", "UNKNOWN"),
            # --- YENİ EKLENEN KISIM ---
            # Eğer veritabanında bu ayar yoksa varsayılan olarak '.' (ana dizin) döner.
            "target_path": settings.get("update_target_path", ".") 
        }
    except Exception as e:
        if conn: conn.close()
        return {"latest_version": "1.0.0"}

# --- WEB STREAM İÇİN EKLENECEK KISIM ---

@app.post("/api/get-app-code")
async def get_app_code(payload: dict = Body(...)):
    """
    Bu fonksiyon, Ghost_Connector (istemci) tarafından çağrılır.
    Amacı: Asıl programın (MainApp/UI) Python kodunu metin olarak göndermektir.
    """
    # 1. Güvenlik Kontrolü (İsterseniz buraya bir şifre/token kontrolü ekleyebilirsiniz)
    # token = payload.get("token")
    # if token != "GIZLI_BIR_SIFRE": return JSONResponse(...)

    try:
        # 2. Gönderilecek Dosyayı Seçin
        # "full_app.py" -> İçinde Tkinter arayüzünüzün, Login ekranınızın olduğu dosya.
        # Bu dosya sunucuda 'main_server.py' ile AYNI KLASÖRDE durmalı.
        target_file = "full_app.py" 
        
        if not os.path.exists(target_file):
            return JSONResponse(content={"status": "error", "message": "Uygulama dosyası sunucuda bulunamadı!"}, status_code=404)

        with open(target_file, "r", encoding="utf-8") as f:
            source_code = f.read()

        # 3. Kodu Gönder (Connector bunu alıp 'exec' ile çalıştıracak)
        return HTMLResponse(content=source_code, media_type="text/plain")

    except Exception as e:
        return JSONResponse(content={"status": "error", "message": str(e)}, status_code=500)





