import os
import json
import requests
import threading
from datetime import datetime, timedelta, timezone
from flask import Flask, Response

app = Flask(__name__)

# --- KONFIGURASI BOSKU ---
CF_API_TOKEN = "YAHVlmAL47gnHM2roQ8KSW8uOEnfWIeRjdO6b9ua"
CF_ACCOUNT_ID = "eb4b3a7ff38dbf069f2ecc29ae6637e4"
KV_NAMESPACE_ID = "7c6ae9f3416f4fdebd7f5a1ba437d917"
TELEGRAM_TOKEN_IPOS = "8222594585:AAHTZNHgwUm6bTvpt5DieR-5vFks4rhKHjE"
CHAT_ID_IPOS = "6117482148"

# ⚠️ MASUKKAN API KEY NAWALA.ASIA DI SINI ⚠️
NAWALA_API_KEY = "ls_587523224c44bdff3b0ab9205826288aa7e54c51a82badfe" 

TARGETS_IPOS = [
    {"name": "CNNSLOT", "key": "active_domains_cnn"},
    {"name": "RTP8000", "key": "active_domains_rtp"},
    {"name": "RUBY8000", "key": "active_domains_ruby"}
]

HISTORY_FILE = "bot_history.json"
log_buffer = ""

def log(type_msg, msg):
    global log_buffer
    timestamp = (datetime.now(timezone.utc) + timedelta(hours=7)).strftime("%H:%M:%S")
    line = f"[{timestamp}] [{type_msg}]  {msg}\n"
    print(line, end="")
    log_buffer += line

# --- FUNGSI CLOUDFLARE & TELEGRAM ---
def get_kv(key_name):
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/storage/kv/namespaces/{KV_NAMESPACE_ID}/values/{key_name}"
    try:
        r = requests.get(url, headers={"Authorization": f"Bearer {CF_API_TOKEN}"})
        return r.json() if r.status_code == 200 else []
    except: return []

def update_kv(key_name, new_list):
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/storage/kv/namespaces/{KV_NAMESPACE_ID}/values/{key_name}"
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"}
    requests.put(url, headers=headers, data=json.dumps(new_list))

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r') as f: return json.load(f)
        except: return {}
    return {}

def save_history(data):
    with open(HISTORY_FILE, 'w') as f: json.dump(data, f)

def send_and_pin(token, chat_id, message):
    try:
        history = load_history()
        history_key = f"{token[-10:]}_{chat_id}" 
        if history_key in history:
            try: requests.post(f"https://api.telegram.org/bot{token}/deleteMessage", data={"chat_id": chat_id, "message_id": history[history_key]})
            except: pass
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={"chat_id": chat_id, "text": message, "disable_web_page_preview": "true"})
        if r.status_code == 200:
            new_msg_id = r.json().get('result', {}).get('message_id')
            if new_msg_id:
                requests.post(f"https://api.telegram.org/bot{token}/pinChatMessage", data={"chat_id": chat_id, "message_id": new_msg_id})
                history[history_key] = new_msg_id
                save_history(history)
            return True
        return False
    except: return False

# --- MESIN UTAMA (VIA API NAWALA) ---
def run_api_check():
    global log_buffer
    log_buffer = "" 
    log("SYSTEM", "Memulai pengecekan via VVIP API Nawala.Asia...")

    semua_domain_target = []
    brand_map = {}
    
    # 1. Ambil domain dari KV
    for target in TARGETS_IPOS:
        domains = get_kv(target['key'])
        if domains:
            semua_domain_target.extend(domains)
            brand_map[target['name']] = domains

    if not semua_domain_target:
        log("INFO", "Tidak ada domain untuk dicek.")
        return log_buffer

    # 2. Tembak ke API Nawala
    log("SYSTEM", f"Mengirim {len(semua_domain_target)} domain ke API...")
    url = "https://api.nawala.link/public-check-domain"
    headers = {
        "X-Api-Key": NAWALA_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "domain": ",".join(semua_domain_target)
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=20)
        res_json = response.json()
        
        if response.status_code == 429:
            log("ERROR", f"Limit API Habis! Tersisa: {res_json.get('remaining', 0)}")
            return log_buffer
            
        if not res_json.get("success"):
            log("ERROR", f"API Error: {res_json}")
            return log_buffer
            
    except Exception as e:
        log("ERROR", f"Gagal menghubungi API Nawala: {e}")
        return log_buffer

    # 3. Proses Hasil JSON dari API
    api_data = res_json.get("data", [])
    blocked_domains = []
    
    for item in api_data:
        dom = item.get("domain", "").lower().strip()
        is_nawala = item.get("nawala", {}).get("blocked", False)
        is_network = item.get("network", {}).get("blocked", False)
        
        # Jika salah satu sistem mendeteksi blokir, catat!
        if is_nawala or is_network:
            blocked_domains.append(dom)

    # 4. Distribusi Hasil & Update KV
    ada_perubahan = False
    global_report = []

    for target in TARGETS_IPOS:
        log("INFO", f"Cek Brand: {target['name']}")
        brand_domains = brand_map.get(target['name'], [])
        active, removed = [], []
        
        for d in brand_domains:
            d_l = d.lower().strip()
            if d_l in blocked_domains:
                removed.append(d)
                log("WARN", f"🔴 STATUS: IPOS ➜ {d} [AUTO DELETE]")
            else:
                active.append(d)
                log("SUCCESS", f"🟢 STATUS: AMAN ➜ {d}")

        if removed:
            update_kv(target['key'], active)
            ada_perubahan = True
        global_report.append({"name": target["name"], "active": active, "removed": removed})

    # 5. Kirim Laporan ke Telegram
    if ada_perubahan:
        log("INFO", "Mengirim laporan Telegram...")
        waktu_str = (datetime.now(timezone.utc) + timedelta(hours=7)).strftime("%d/%m/%Y, %H:%M:%S WIB")
        garis = "---------------------------------------"
        msg = f"📅 Waktu: {waktu_str}\n🌐 Source: API Nawala.Asia\n\n"
        
        for r in global_report:
            msg += f"🍄 UPDATE LINK [{r['name']}]\n{garis}\n"
            for d in r['removed']: msg += f"🔴 {d} - IPOS\n"
            for d in r['active']: msg += f"🟢 {d}\n"
            msg += f"{garis}\n"
            
        send_and_pin(TELEGRAM_TOKEN_IPOS, CHAT_ID_IPOS, msg)

    log("SUCCESS", "Pengecekan API Selesai!")
    return log_buffer

# --- ENDPOINT API ---
@app.route('/jalankan-patroli')
def endpoint_patroli():
    # Jalankan di Background agar Cron-Job tidak Timeout
    thread = threading.Thread(target=run_api_check)
    thread.start()
    return Response("<h3 style='color:green; font-family:monospace;'>🚀 Patroli API Nawala sedang berjalan di latar belakang... Cek log server/Telegram!</h3>", mimetype='text/html', status=200)

@app.route('/')
def home():
    return "Satpam Nawala (Versi API) Aktif! Akses /jalankan-patroli"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
