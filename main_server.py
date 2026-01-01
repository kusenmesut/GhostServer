class ScenarioManager:
    """
    Sadece Bulut API ile konuşur.
    """
    @staticmethod
    def get_menu_from_cloud(token):
        try:
            # Timeout 30 saniye yapıldı
            resp = requests.get(f"{API_BASE_URL}/get-menu", params={"token": token}, timeout=30)
            if resp.status_code == 200: return resp.json().get("scenarios", [])
            return []
        except Exception as e: 
            print(f"Menü çekme hatası: {e}")
            return []

    @staticmethod
    def get_code_from_cloud(token, scenario_id):
        try:
            payload = {"token": token, "scenario_id": scenario_id}
            # Timeout 30 saniye yapıldı
            resp = requests.post(f"{API_BASE_URL}/get-code", json=payload, timeout=30)
            if resp.status_code == 200: return resp.json().get("code", "")
            return None
        except Exception as e: 
            print(f"Kod çekme hatası: {e}")
            return None
