import os
import json
import time
import requests
import sys
import subprocess
from datetime import datetime, timedelta, timezone
from flask import Flask, Response

# --- AUTO INSTALL PLAYWRIGHT (WAJIB UNTUK RENDER) ---
def ensure_playwright_installed():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "playwright"])
    
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            p.chromium.launch(headless=True).close()
        except Exception:
            subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])

ensure_playwright_installed()
from playwright.sync_api import sync_playwright

# --- INISIALISASI FLASK ---
app = Flask(__name__)

# --- KONFIGURASI BOSKU (SUDAH VALID) ---
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

def chunk_list(lst, n):
    for i in range(0, len(lst), n): yield lst[i:i + n]

# --- MESIN PLAYWRIGHT DENGAN SISTEM RETRY ---
def run_playwright_check():
    global log_buffer
    log_buffer = "" 
    log("SYSTEM", "Membuka Nawala Checker (Source: nawala.in)")
    
    REAL_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    global_report, ada_perubahan = [], False

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"])
            context = browser.new_context(user_agent=REAL_USER_AGENT, viewport={"width": 1280, "height": 720})
            page = context.new_page()
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            # --- SISTEM RETRY AGRESIF (5 DETIK) ---
            berhasil_muat = False
            for i in range(3):
                try:
                    log("SYSTEM", f"Mencoba memuat nawala.in (Percobaan ke-{i+1})...")
                    page.set_extra_http_headers({"Accept-Language": "en-US,en;q=0.9"})
                    
                    # Cuma nunggu 5 detik! Kalau lewat, langsung skip ke retry berikutnya
                    response = page.goto("https://nawala.in/", timeout=5000, wait_until="commit")
                    
                    if response and response.status < 500:
                        log("SUCCESS", "Berhasil masuk ke Nawala.in!")
                        berhasil_muat = True
                        break
                    else:
                        log("WARN", f"Status Server: {response.status if response else 'No Response'}. Mengulang...")
                except Exception as e:
                    log("WARN", f"Gagal/Lemot di percobaan ke-{i+1} (Timeout 5s).")
                
                # Nunggu 3 detik aja biar nggak kelamaan
                if i < 2: time.sleep(3) 

            if not berhasil_muat:
                log("ERROR", "Nawala.in lemot/down parah setelah 3x coba.")
                browser.close()
                return log_buffer
            
            try:
                page.wait_for_selector("textarea", timeout=20000)
            except:
                log("ERROR", "Kotak input tidak ditemukan.")
                browser.close()
                return log_buffer

            # --- MULAI CEK DOMAIN ---
            for target in TARGETS_IPOS:
                try:
                    domains = get_kv(target['key'])
                    if not domains: continue

                    log("INFO", f"Cek Brand: {target['name']}")
                    textarea = page.locator("textarea").first
                    check_button = page.locator("button").filter(has_text="Check Status")

                    removed, active = [], []
                    for batch in list(chunk_list(domains, 20)):
                        try:
                            textarea.fill("") 
                            textarea.fill("\n".join(batch))
                            check_button.click()
                            
                            page.wait_for_selector("table tbody tr", timeout=20000)
                            time.sleep(2.0) 
                            rows = page.locator("table tbody tr").all()
                            
                            status_map = {} 
                            for row in rows:
                                cols = row.locator("td").all()
                                if len(cols) >= 3:
                                    status_map[cols[1].inner_text().strip().lower()] = cols[2].inner_text().strip().lower()
                            
                            for d in batch:
                                d_lower = d.lower()
                                current_status = "aman" 
                                for map_dom, map_stat in status_map.items():
                                    if d_lower in map_dom:
                                        current_status = map_stat
                                        break
                                
                                if "blocked" in current_status:
                                    removed.append(d)
                                    log("WARN", f"🔴 STATUS: IPOS ➜ {d} [AUTO DELETE KV]")
                                else:
                                    active.append(d)
                                    log("SUCCESS", f"🟢 STATUS: AMAN ➜ {d}")
                        except Exception as e:
                            log("ERROR", "Tabel hasil lambat/tidak muncul.")
                            for d in batch: active.append(d)
                    
                    if removed:
                        update_kv(target['key'], active)
                        ada_perubahan = True
                    global_report.append({"name": target["name"], "active": active, "removed": removed})
                except Exception as e: log("ERROR", f"Error {target['name']}: {e}")

            browser.close()
            
            if ada_perubahan:
                log("INFO", "Mengirim laporan Telegram...")
                waktu_str = (datetime.now(timezone.utc) + timedelta(hours=7)).strftime("%d/%m/%Y, %H:%M:%S WIB")
                msg = f"📅 Waktu: {waktu_str}\n🌐 Source: TrustPositif - https://nawala.in/\n\n"
                for i, r in enumerate(global_report):
                    msg += f"🍄 UPDATE LINK [{r['name']}]\n"
                    for d in r['removed']: msg += f"🔴 {d} - IPOS\n"
                    for d in r['active']: msg += f"🟢 {d}\n"
                    if i < len(global_report) - 1: msg += "------------------\n"
                send_and_pin(TELEGRAM_TOKEN_IPOS, CHAT_ID_IPOS, msg)

    except Exception as e:
        log("ERROR", f"CRITICAL CRASH: {e}")
    
    return log_buffer

# --- ENDPOINT API ---
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
