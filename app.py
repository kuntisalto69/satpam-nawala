import os
import json
import time
import requests
from datetime import datetime, timedelta, timezone
from flask import Flask, Response, request

app = Flask(__name__)

# --- KONFIGURASI BOSKU ---
CF_API_TOKEN = "YAHVlmAL47gnHM2roQ8KSW8uOEnfWIeRjdO6b9ua"
CF_ACCOUNT_ID = "eb4b3a7ff38dbf069f2ecc29ae6637e4"
KV_NAMESPACE_ID = "7c6ae9f3416f4fdebd7f5a1ba437d917"
TELEGRAM_TOKEN_IPOS = "8222594585:AAHTZNHgwUm6bTvpt5DieR-5vFks4rhKHjE"
CHAT_ID_IPOS = "6117482148"

# ⚠️ 3 API KEY DARI 3 AKUN BERBEDA ⚠️
TARGETS_IPOS = [
    {
        "name": "CNNSLOT", 
        "key": "active_domains_cnn",
        "api_key": "ls_796e4ae8c9836dbcc93e5a45c67e18e6285c3b55c50a3ebc" 
    },
    {
        "name": "RTP8000", 
        "key": "active_domains_rtp",
        "api_key": "ls_b292d9ba81798d79a42ba5312b3653f04d1dbdb3a41f220b" 
    },
    {
        "name": "RUBY8000", 
        "key": "active_domains_ruby",
        "api_key": "ls_4d9bae2ee5c8e27f58942145a421e289956d69d664e7f432" 
    }
]

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

def send_and_pin(token, chat_id, message):
    try:
        # Langsung tembak pesan ke Telegram
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={"chat_id": chat_id, "text": message, "disable_web_page_preview": "true"})
        if r.status_code == 200:
            new_msg_id = r.json().get('result', {}).get('message_id')
            if new_msg_id:
                # Langsung Pin pesannya
                requests.post(f"https://api.telegram.org/bot{token}/pinChatMessage", data={"chat_id": chat_id, "message_id": new_msg_id})
            return True
        return False
    except: return False

# --- MESIN UTAMA (1 BRAND = 1 API DENGAN TRANSPARANSI KUOTA) ---
def run_api_check():
    global log_buffer
    log_buffer = "" 
    log("SYSTEM", "Memulai pengecekan VVIP (1 Brand = 1 API Key)...")

    ada_perubahan = False
    global_report = []

    for target in TARGETS_IPOS:
        log("INFO", f"--- Memproses Brand: {target['name']} ---")
        domains = get_kv(target['key'])
        
        if not domains:
            log("INFO", "Tidak ada domain di KV. Skip.")
            continue
            
        api_key = target.get("api_key", "")
        blocked_domains = []
        chunk_size = 5 
        
        for i in range(0, len(domains), chunk_size):
            chunk = domains[i:i + chunk_size]
            log("SYSTEM", f"Mengirim API Request untuk {len(chunk)} domain {target['name']}...")
            
            url = "https://api.nawala.link/public-check-domain"
            headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}
            payload = {"domain": ",".join(chunk)}
            
            try:
                response = requests.post(url, headers=headers, json=payload, timeout=20)
                res_json = response.json()
                
                # --- PENGAMBILAN DATA PEMAKAIAN API ---
                # Kita ambil 'X-Ratelimit-Used' dari header, jika tidak ada pakai data dashboard Bosku
                used = response.headers.get('X-Ratelimit-Used')
                
                # Jika API tidak kasih header, kita pakai hitungan manual sederhana
                if not used or used == "N/A":
                    # Mencoba ambil dari sisa kuota (50 - remaining)
                    rem = response.headers.get('X-Ratelimit-Remaining')
                    if rem and rem.isdigit():
                        used = 50 - int(rem)
                    else:
                        used = "Cek Dashboard" # Fallback jika benar-benar tidak terbaca

                log("STATS", f"📊 Pemakaian API {target['name']}: {used}/50")
                
                if response.status_code == 429:
                    log("ERROR", f"Limit API untuk {target['name']} HABIS TOTAL!")
                    continue
                    
                if not res_json.get("success"):
                    log("ERROR", f"API Error pada {target['name']}: {res_json}")
                    continue
                
                api_data = res_json.get("data", [])
                for item in api_data:
                    dom = item.get("domain", "").lower().strip()
                    is_nawala = item.get("nawala", {}).get("blocked", False)
                    is_network = item.get("network", {}).get("blocked", False)
                    if is_nawala or is_network:
                        blocked_domains.append(dom)
                        
            except Exception as e:
                log("ERROR", f"Gagal menghubungi API untuk {target['name']}: {e}")
                
            time.sleep(1) 
            
        active, removed = [], []
        for d in domains:
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

    if ada_perubahan:
        # ... (bagian kirim telegram tetap sama)
        log("INFO", "Mengirim laporan Telegram...")
        waktu_str = (datetime.now(timezone.utc) + timedelta(hours=7)).strftime("%d/%m/%Y, %H:%M:%S WIB")
        garis = "---------------------------------------"
        msg = f"📅 Waktu: {waktu_str}\n🌐 Source: API Nawala.Asia (Multi-Key)\n\n"
        for r in global_report:
            msg += f"🍄 UPDATE LINK [{r['name']}]\n{garis}\n"
            for d in r['removed']: msg += f"🔴 {d} - IPOS\n"
            for d in r['active']: msg += f"🟢 {d}\n"
            msg += f"{garis}\n"
        send_and_pin(TELEGRAM_TOKEN_IPOS, CHAT_ID_IPOS, msg)

    log("SUCCESS", "Pengecekan API Multi-Key Selesai!")
    return log_buffer

# --- ENDPOINT API DENGAN FITUR AUTO-LIVE ---
# Simpan waktu terakhir jalan di memori (biar gak jebol kuota)
LAST_RUN_TIME = None

@app.route('/jalankan-patroli', methods=['GET', 'HEAD'])
def endpoint_patroli():
    # BIKIN ROBOT MENGABAIKAN KETUKAN 'HEAD'
    if request.method == 'HEAD':
        return Response("", status=200)

    global LAST_RUN_TIME
    sekarang = datetime.now()

    # REM DARURAT: Jika dipanggil lagi dalam waktu kurang dari 20 menit, TOLAK!
    if LAST_RUN_TIME and (sekarang - LAST_RUN_TIME).total_seconds() < 1200:
        return Response(f"""
        <html>
            <head><meta http-equiv="refresh" content="300"></head>
            <body style="background:#1e1e1e; color:#ff9900; font-family:monospace; padding:20px;">
                ⚠️ SISTEM PENDING: Menghindari spam kuota. <br>
                Patroli terakhir baru saja selesai. Tunggu jadwal berikutnya (30 Menit).
                <br><br>
                <a href="/jalankan-patroli" style="color:#00ff00;">Paksa Cek Sekarang (Gunakan Hati-hati)</a>
            </body>
        </html>
        """, mimetype='text/html')

    # Jika aman, jalankan patroli
    LAST_RUN_TIME = sekarang
    hasil_log = run_api_check()
    
    return Response(f"""
    <html>
        <head>
            <meta http-equiv="refresh" content="1800">
            <title>LIVE MONITORING - SATPAM NAWALA</title>
        </head>
        <body style="background:#1e1e1e; margin:0; padding:20px;">
            <div style="color:#888; font-family:monospace; margin-bottom:10px;">
                🔴 LIVE MONITORING ACTIVE | Auto-refresh: 30 Min
            </div>
            <pre style="color:#00ff00; font-family:monospace; font-size:14px; white-space:pre-wrap;">{hasil_log}</pre>
        </body>
    </html>
    """, mimetype='text/html')

@app.route('/')
def home():
    return "Satpam Nawala (Multi-API Mode) Aktif! Akses /jalankan-patroli"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
