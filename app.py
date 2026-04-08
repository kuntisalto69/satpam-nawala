import os
import json
import socket
import requests
from flask import Flask, Response
from datetime import datetime, timedelta, timezone

app = Flask(__name__)

# --- KONFIGURASI (SUDAH DISESUAIKAN) ---
CF_API_TOKEN = "YAHVlmAL47gnHM2roQ8KSW8uOEnfWIeRjdO6b9ua"
CF_ACCOUNT_ID = "eb4b3a7ff38dbf069f2ecc29ae6637e4"
KV_NAMESPACE_ID = "7c6ae9f3416f4fdebd7f5a1ba437d917"
TELEGRAM_TOKEN = "8222594585:AAHTZNHgwUm6bTvpt5DieR-5vFks4rhKHjE"
CHAT_ID = "6117482148"

TARGETS = [
    {"name": "CNNSLOT", "key": "active_domains_cnn"},
    {"name": "RTP8000", "key": "active_domains_rtp"},
    {"name": "RUBY8000", "key": "active_domains_ruby"}
]

def cek_nawala_dns(domain):
    """Cek status via DNS Lookup (Anti Error 522)"""
    try:
        # Kita tanya ke DNS Google apakah IP-nya sudah berubah ke IP Positif
        ip = socket.gethostbyname(domain)
        # Daftar IP Internet Positif / Nawala yang umum
        list_ip_nawala = ["103.22.201.2", "180.250.247.11", "118.98.95.50"]
        if any(ip.startswith(pref) for pref in ["103.", "180.", "118."]) or ip in list_ip_nawala:
            return "BLOCKED"
        return "SAFE"
    except:
        return "ERROR"

def get_kv(key_name):
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/storage/kv/namespaces/{KV_NAMESPACE_ID}/values/{key_name}"
    r = requests.get(url, headers={"Authorization": f"Bearer {CF_API_TOKEN}"})
    return r.json() if r.status_code == 200 else []

def update_kv(key_name, new_list):
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/storage/kv/namespaces/{KV_NAMESPACE_ID}/values/{key_name}"
    requests.put(url, headers={"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"}, data=json.dumps(new_list))

def kirim_tele(pesan):
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data={"chat_id": CHAT_ID, "text": pesan, "parse_mode": "Markdown"})

@app.route('/jalankan-patroli')
def patroli():
    output = "[SYSTEM] Memulai Patroli Mode DNS (Plan B)\n"
    ada_perubahan = False
    report_msg = f"📅 *UPDATE PATROLI DNS*\n{datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M:%S')} WIB\n\n"

    for target in TARGETS:
        domains = get_kv(target['key'])
        if not domains: continue
        
        output += f"\n[INFO] Checking {target['name']}...\n"
        active_list, removed_list = [], []

        for d in domains:
            status = cek_nawala_dns(d)
            if status == "BLOCKED":
                removed_list.append(d)
                output += f"🔴 {d} -> TERBLOKIR!\n"
            else:
                active_list.append(d)
                output += f"🟢 {d} -> AMAN\n"
        
        if removed_list:
            ada_perubahan = True
            update_kv(target['key'], active_list)
            report_msg += f"🍄 *{target['name']}*\n"
            for rm in removed_list: report_msg += f"🔴 {rm} - IPOS\n"
            for ac in active_list: report_msg += f"🟢 {ac}\n"
            report_msg += "------------------\n"

    if ada_perubahan:
        kirim_tele(report_msg)
        output += "\n[SUCCESS] Laporan dikirim ke Telegram."
    else:
        output += "\n[INFO] Semua domain aman. Tidak ada perubahan."

    return Response(f"<pre style='background:#1e1e1e; color:#00ff00; padding:20px;'>{output}</pre>", mimetype='text/html')

@app.route('/')
def home(): return "Satpam Nawala Mode DNS Aktif!"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
