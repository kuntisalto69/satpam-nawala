import os
import json
import time
import requests
import sys
import subprocess
from datetime import datetime, timedelta, timezone
from flask import Flask, Response

# --- AUTO INSTALL PLAYWRIGHT ---
def ensure_playwright_installed():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        subprocess.call([sys.executable, "-m", "pip", "install", "playwright"])
    
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            p.chromium.launch(headless=True).close()
        except Exception:
            subprocess.call([sys.executable, "-m", "playwright", "install", "chromium"])

ensure_playwright_installed()
from playwright.sync_api import sync_playwright

app = Flask(__name__)

# --- KONFIGURASI ---
CF_API_TOKEN = "YAHVlmAL47gnHM2roQ8KSW8uOEnfWIeRjdO6b9ua"
CF_ACCOUNT_ID = "eb4b3a7ff38dbf069f2ecc29ae6637e4"
KV_NAMESPACE_ID = "7c6ae9f3416f4fdebd7f5a1ba437d917"
TELEGRAM_TOKEN_IPOS = "8222594585:AAHTZNHgwUm6bTvpt5DieR-5vFks4rhKHjE"
CHAT_ID_IPOS = "6117482148"

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

def run_playwright_check():
    global log_buffer
    log_buffer = "" 
    log("SYSTEM", "Membuka Nawala Checker (Source: nawala.in)")
    
    REAL_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    global_report, ada_perubahan = [], False

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"])
            context = browser.new_context(user_agent=REAL_USER_AGENT, viewport={"width": 1280, "height": 720})
            page = context.new_page()

            berhasil_muat = False
            for i in range(3): # Kita kasih jatah 3x percobaan refresh
                try:
                    log("SYSTEM", f"Mencoba memuat nawala.in (Percobaan ke-{i+1})...")
                    # Tunggu sampai networkidle (semua elemen beres dimuat)
                    page.goto("https://nawala.in/", timeout=20000, wait_until="networkidle")
                    
                    # CEK: Apakah kotak input (textarea) sudah muncul di layar?
                    # Ini kunci buat mastiin kita nggak kejebak di halaman Error 522
                    textarea_exist = page.query_selector("textarea")
                    
                    if textarea_exist:
                        log("SUCCESS", "Berhasil masuk ke Nawala.in dan siap input!")
                        berhasil_muat = True
                        break
                    else:
                        log("WARN", f"Masuk ke web tapi halaman error/kosong. Coba lagi...")
                except Exception as e:
                    log("WARN", f"Gagal/Lemot di percobaan ke-{i+1}: {str(e)}")
                
                if i < 2: time.sleep(5) # Jeda 5 detik sebelum refresh ulang

            if not berhasil_muat:
                log("ERROR", "Nawala.in tetap tidak bisa dibuka setelah 3x percobaan.")
                browser.close()
                return log_buffer

            page.wait_for_selector("textarea", timeout=20000)
            
            semua_domain_target = []
            brand_map = {}
            for target in TARGETS_IPOS:
                domains = get_kv(target['key'])
                if domains:
                    semua_domain_target.extend(domains)
                    brand_map[target['name']] = domains

            if semua_domain_target:
                log("SYSTEM", f"Memulai cek {len(semua_domain_target)} domain sekaligus...")
                page.locator("textarea").first.fill("\n".join(semua_domain_target))
                page.locator("button").filter(has_text="Check Status").click(force=True)
                
                try:
                    # Tunggu tabel muncul
                    page.wait_for_selector("table tbody tr", timeout=20000)
                    
                    # --- JEDA SAKTI 15 DETIK (BIAR STATUS MERAH MUNCUL SEMPURNA) ---
                    log("SYSTEM", "Menunggu Nawala memproses status (Jeda 15 detik)...")
                    time.sleep(15.0)
                    
                    rows = page.locator("table tbody tr").all()
                    results_from_page = []
                    for row in rows:
                        cols = row.locator("td").all()
                        if len(cols) >= 3:
                            d_name = cols[1].inner_text().strip().lower()
                            # Ambil semua teks dan kode HTML di kolom status
                            d_text = cols[2].inner_text().strip().lower()
                            d_html = cols[2].inner_html().lower()
                            
                            # Cek indikator blokir: kata 'blocked', simbol '✕', atau kata 'positif'
                            is_hit = any(x in d_text or x in d_html for x in ["blocked", "✕", "positif"])
                            results_from_page.append({"domain": d_name, "is_blocked": is_hit})

                    for target in TARGETS_IPOS:
                        log("INFO", f"Cek Brand: {target['name']}")
                        brand_domains = brand_map.get(target['name'], [])
                        active, removed = [], []
                        for d in brand_domains:
                            d_l = d.lower().strip()
                            found = False
                            for res in results_from_page:
                                # Pencocokan fleksibel (mengatasi www atau spasi)
                                if d_l in res['domain'] or res['domain'] in d_l:
                                    found = True
                                    if res['is_blocked']:
                                        removed.append(d)
                                        log("WARN", f"🔴 STATUS: IPOS ➜ {d} [AUTO DELETE]")
                                    else:
                                        active.append(d)
                                        log("SUCCESS", f"🟢 STATUS: AMAN ➜ {d}")
                                    break
                            if not found:
                                active.append(d)
                                log("INFO", f"🟡 STATUS: SKIP ➜ {d}")

                        if removed:
                            update_kv(target['key'], active)
                            ada_perubahan = True
                        global_report.append({"name": target["name"], "active": active, "removed": removed})

                except Exception as e:
                    log("ERROR", f"Tabel tidak muncul atau error: {e}")

            browser.close()
            
            if ada_perubahan:
                log("INFO", "Mengirim laporan Telegram...")
                waktu_str = (datetime.now(timezone.utc) + timedelta(hours=7)).strftime("%d/%m/%Y, %H:%M:%S WIB")
                garis = "---------------------------------------------------------------------"
                msg = f"📅 Waktu: {waktu_str}\n🌐 Source: https://nawala.in\n\n"
                for r in global_report:
                    msg += f"🍄 UPDATE LINK [{r['name']}]\n{garis}\n"
                    for d in r['removed']: msg += f"🔴 {d} - IPOS\n"
                    for d in r['active']: msg += f"🟢 {d}\n"
                    msg += f"{garis}\n"
                send_and_pin(TELEGRAM_TOKEN_IPOS, CHAT_ID_IPOS, msg)

    except Exception as e:
        log("ERROR", f"CRITICAL CRASH: {e}")
    return log_buffer

@app.route('/jalankan-patroli')
def endpoint_patroli():
    hasil_log = run_playwright_check()
    return Response(f"<pre style='background:#1e1e1e; color:#00ff00; padding:20px; font-family:monospace; font-size:14px;'>{hasil_log}</pre>", mimetype='text/html')

@app.route('/')
def home():
    return "Satpam Nawala Aktif! Akses /jalankan-patroli"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
