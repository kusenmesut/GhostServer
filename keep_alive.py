import os
import psycopg2

def ping_database():
    # GitHub Secrets'tan URL'yi alacağız
    db_url = os.environ.get("DATABASE_URL")
    
    if not db_url:
        print("HATA: DATABASE_URL bulunamadı!")
        return

    try:
        # Veritabanına bağlan
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        
        # Basit bir sorgu çalıştır (Veritabanını gıdıkla)
        cur.execute("SELECT 1;")
        cur.fetchone()
        
        print("BAŞARILI: Veritabanı dürtüldü, uyanık kalacak. ☕")
        
        cur.close()
        conn.close()
    except Exception as e:
        print(f"HATA OLUŞTU: {e}")
        # Hata olursa GitHub Action'ın 'başarısız' görünmesi için:
        exit(1)

if __name__ == "__main__":
    ping_database()
