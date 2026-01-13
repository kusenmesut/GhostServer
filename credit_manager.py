import psycopg2
from psycopg2.extras import RealDictCursor

class CreditManager:
    @staticmethod
    def calculate_group_cost(cursor, group_name):
        # Eğer grup adı boşsa veya TÜMÜ ise toplam maliyeti hesapla (veya 0 dön)
        if not group_name or group_name == "TÜMÜ":
            cursor.execute("SELECT SUM(cost_per_run) as total FROM scenario_groups")
            row = cursor.fetchone()
            return row['total'] if row and row['total'] else 0
        else:
            cursor.execute("SELECT cost_per_run FROM scenario_groups WHERE group_name = %s", (group_name,))
            row = cursor.fetchone()
            # Eğer grup veritabanında yoksa maliyeti 0 kabul et (Hata vermesin)
            return row['cost_per_run'] if row else 0

    @staticmethod
    def process_deduction(conn, user_id, group_name):
        cursor = conn.cursor()
        try:
            # 1. Maliyet Bul
            cost = CreditManager.calculate_group_cost(cursor, group_name)
            
            # 2. Bakiye Kontrol
            cursor.execute("SELECT credits_balance FROM users WHERE user_id = %s", (user_id,))
            user = cursor.fetchone()
            current_balance = user['credits_balance'] if user else 0
            
            if current_balance < cost:
                return False, f"Yetersiz Bakiye! (Gereken: {cost}, Mevcut: {current_balance})", 0, current_balance, 402
            
            # 3. Düşüm İşlemi
            if cost > 0:
                cursor.execute("UPDATE users SET credits_balance = credits_balance - %s WHERE user_id = %s", (cost, user_id))
                cursor.execute("INSERT INTO logs (user_id, action, details, credit_cost) VALUES (%s, %s, %s, %s)", 
                               (user_id, 'run_group_audit', f"Grup: {group_name}", cost))
                conn.commit()
                current_balance -= cost
                
            return True, "İşlem Başarılı", cost, current_balance, 200
            
        except Exception as e:
            conn.rollback()
            return False, str(e), 0, 0, 500
        finally:
            cursor.close()
