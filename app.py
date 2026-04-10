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

# ⚠️ SETUP VVIP: 3 BRAND DENGAN API CADANGAN (FAILOVER) ⚠️
TARGETS_IPOS = [
    {
        "name": "CNNSLOT", 
        "key": "active_domains_cnn",
        "api_keys": [
            "ls_796e4ae8c9836dbcc93e5a45c67e18e6285c3b55c50a3ebc", # API 1 (Utama)
            "ls_dc60f07366892ea3ee2407a891d7f1e7a82008e7b92bb2e2"  # API 4 (Cadangan)
        ]
    },
    {
        "name": "RTP8000", 
        "key": "active_domains_rtp",
        "api_keys": [
            "ls_b292d9ba81798d79a42ba5312b3653f04d1dbdb3a41f220b", # API 2 (Utama)
            "ls_b7ac342740d2e7c434e7f589bbeae834f51b1634e1692b80"  # API 5 (Cadangan)
        ]
    },
    {
        "name": "RUBY8000", 
        "key": "active_domains_ruby",
        "api_keys": [
            "ls_4d9bae2ee5c8e27f58942145a421e289956d69d664e7f432", # API 3 (Utama)
            "ls_d853e6be788dc6ac388215849737d2bbaaa9ec00cc02ceaa"  # API 6 (Cadangan)
        ]
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
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={"chat_id": chat_id, "text": message, "disable_web_page_preview": "true"})
        if r.status_code == 200:
            new_msg_id = r.json().get('result', {}).get('message_id')
            if new_msg_id:
                requests.post(f"https://api.telegram.org/bot{token}/pinChatMessage", data={"chat_id": chat_id, "message_id": new_msg_id})
            return True
        return False
    except: return False

# --- MESIN UTAMA (MULTI-KEY & AUTO FAILOVER) ---
def run_api_check():
    global log_buffer
    log_buffer = "" 
    log("SYSTEM", "Memulai pengecekan VVIP (Mode Auto-Cadangan API)...")

    ada_perubahan = False
    global_report = []

    for target in TARGETS_IPOS:
        log("INFO", f"--- Memproses Brand: {target['name']} ---")
        domains = get_kv(target['key'])
        
        if not domains:
            log("INFO", "Tidak ada domain di KV. Skip.")
            continue
            
        api_keys = target.get("api_keys", [])
        active_key_idx = 0 
        blocked_domains = []
        chunk_size = 5 
        
        for i in range(0, len(domains), chunk_size):
            chunk = domains[i:i + chunk_size]
            chunk_berhasil = False
            
            # LOOP FAILOVER: Terus tembak sampai berhasil atau semua API cadangan habis
            while not chunk_berhasil and active_key_idx < len(api_keys):
                current_api_key = api_keys[active_key_idx]
                log("SYSTEM", f"Mengirim API Request ({len(chunk)} domain) via API Key ke-{active_key_idx + 1}...")
                
                url = "https://api.nawala.link/public-check-domain"
                headers = {"X-Api-Key": current_api_key, "Content-Type": "application/json"}
                payload = {"domain": ",".join(chunk)}
                
                try:
                    response = requests.post(url, headers=headers, json=payload, timeout=20)
                    res_json = response.json()
                    
                    used = response.headers.get('X-Ratelimit-Used')
                    if not used or used == "N/A":
                        rem = response.headers.get('X-Ratelimit-Remaining')
                        if rem and rem.isdigit(): used = 50 - int(rem)
                        else: used = "Cek Dashboard"

                    log("STATS", f"📊 Pemakaian API {target['name']} (Key {active_key_idx + 1}): {used}/50")
                    
                    # JIKA LIMIT HABIS, GANTI SENJATA!
                    if response.status_code == 429:
                        log("WARN", f"⚠️ Limit API ke-{active_key_idx + 1} HABIS TOTAL! Beralih ke API Cadangan...")
                        active_key_idx += 1 
                        time.sleep(2)
                        continue 
                        
                    if not res_json.get("success"):
                        log("ERROR", f"API Error pada {target['name']}: {res_json}")
                        break 
                    
                    # JIKA SUKSES
                    chunk_berhasil = True
                    api_data = res_json.get("data", [])
                    for item in api_data:
                        dom = item.get("domain", "").lower().strip()
                        if item.get("nawala", {}).get("blocked") or item.get("network", {}).get("blocked"):
                            blocked_domains.append(dom)
                            
                except Exception as e:
                    log("ERROR", f"Gagal menghubungi API: {e}")
                    break
            
            if not chunk_berhasil:
                log("ERROR", f"🚨 SEMUA CADANGAN API {target['name']} HABIS! Melewati sisa domain brand ini.")
                break 
                
            time.sleep(1) 
            
        active, removed = [], []
        for d in domains:
            if d.lower().strip() in blocked_domains:
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
        log("INFO", "Mengirim laporan Telegram...")
        waktu_str = (datetime.now(timezone.utc) + timedelta(hours=7)).strftime("%d/%m/%Y, %H:%M:%S WIB")
        garis = "---------------------------------------"
        msg = f"📅 Waktu: {waktu_str}\n🌐 Source: Nawala (Auto-Failover Mode)\n\n"
        for r in global_report:
            msg += f"🍄 UPDATE LINK [{r['name']}]\n{garis}\n"
            for d in r['removed']: msg += f"🔴 {d} - IPOS\n"
            for d in r['active']: msg += f"🟢 {d}\n"
            msg += f"{garis}\n"
        send_and_pin(TELEGRAM_TOKEN_IPOS, CHAT_ID_IPOS, msg)

    log("SUCCESS", "Pengecekan VVIP Selesai!")
    return log_buffer

# --- ENDPOINT API DENGAN FITUR AUTO-LIVE ---
# Simpan waktu terakhir jalan di memori
LAST_RUN_TIME = None
# Simpan layar hijau terakhir biar bisa ditampilin ulang kalau lagi Cooldown
LAST_LOG_OUTPUT = "Sistem baru menyala. Memuat data patroli..."

@app.route('/jalankan-patroli', methods=['GET', 'HEAD'])
def endpoint_patroli():
    if request.method == 'HEAD':
        return Response("", status=200)

    global LAST_RUN_TIME, LAST_LOG_OUTPUT
    sekarang = datetime.now()

    # CEK COOLDOWN (800 detik / 13.3 menit)
    if LAST_RUN_TIME and (sekarang - LAST_RUN_TIME).total_seconds() < 800:
        # JIKA LAGI COOLDOWN: 
        # Jangan tembak API, tapi JANGAN kasih layar jelek. 
        # Kasih layar yang sama, dan hitung waktu yang udah berlalu!
        time_passed = int((sekarang - LAST_RUN_TIME).total_seconds())
        hasil_log = LAST_LOG_OUTPUT
        status_teks = "⚠️ PENDING (CEGAH SPAM KETUKAN GANDA)"
        warna_status = "#ff9900" # Warna orange biar tau lagi nahan spam
    else:
        # JIKA AMAN: Jalankan patroli baru
        LAST_RUN_TIME = sekarang
        LAST_LOG_OUTPUT = run_api_check() # Simpan hasil hijau-hijaunya
        hasil_log = LAST_LOG_OUTPUT
        time_passed = 0
        status_teks = "🟢 LIVE MONITORING ACTIVE"
        warna_status = "#00ff00" # Warna hijau

    # TAMPILAN FUTURISTIK (SELALU MUNCUL MESKI LAGI COOLDOWN)
    return Response(f"""
    <html>
        <head>
            <meta http-equiv="refresh" content="{900 - time_passed if time_passed < 900 else 900}">
            <title>LIVE MONITORING - SATPAM NAWALA</title>
            <style>
                body {{ background:#1e1e1e; color:#00ff00; font-family:monospace; margin:0; padding:20px; }}
                .header-box {{ border-bottom: 1px dashed #444; padding-bottom: 15px; margin-bottom: 15px; }}
                .title-bar {{ display: flex; justify-content: space-between; color: #888; font-size: 14px; margin-bottom: 10px; }}
                .progress-bg {{ background: #333; width: 100%; height: 6px; border-radius: 3px; overflow: hidden; }}
                .progress-fill {{ background: #00ff00; height: 100%; width: 0%; box-shadow: 0 0 10px #00ff00; transition: width 1s linear; }}
                .timer {{ color: #00ff00; font-weight: bold; }}
                .status-badge {{ color: {warna_status}; font-weight:bold; }}
            </style>
        </head>
        <body>
            <div class="header-box">
                <div class="title-bar">
                    <div class="status-badge">{status_teks} | Interval: 15 Menit</div>
                    <div class="timer" id="countdown-text">Memuat...</div>
                </div>
                <div class="progress-bg">
                    <div class="progress-fill" id="progress-bar"></div>
                </div>
            </div>
            
            <pre style="color:#00ff00; font-family:monospace; font-size:14px; white-space:pre-wrap;">{hasil_log}</pre>

            <script>
                // Animasi Hitung Mundur 15 Menit
                let totalSeconds = 900; 
                let timePassed = {time_passed}; // Lanjutin bar dari waktu yang tersisa
                
                function updateTimer() {{
                    if (timePassed > totalSeconds) timePassed = totalSeconds;
                    
                    let timeLeft = totalSeconds - timePassed;
                    let percentage = (timePassed / totalSeconds) * 100;
                    
                    // Update Lebar Bar Hijau
                    document.getElementById('progress-bar').style.width = percentage + '%';
                    
                    // Update Teks Menit & Detik
                    let m = Math.floor(timeLeft / 60);
                    let s = timeLeft % 60;
                    let s_display = s < 10 ? "0" + s : s;
                    
                    document.getElementById('countdown-text').innerText = "Patroli Berikutnya: " + m + ":" + s_display + " (" + percentage.toFixed(1) + "%)";
                    
                    timePassed++;
                }}
                
                updateTimer(); // Jalan langsung pas buka web
                setInterval(updateTimer, 1000); // Lanjut update per detik
            </script>
        </body>
    </html>
    """, mimetype='text/html')

@app.route('/')
def home():
    return "Satpam Nawala VVIP (Auto-Failover Mode) Aktif! Akses /jalankan-patroli"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
