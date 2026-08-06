#!/usr/bin/env python3
"""
Refresh xAI/Grok tokens directly from 9Router SQLite DB (PARALLEL).
===============================================================
Scans all grok-cli connections, refreshes expired access tokens.

HTTP refresh dijalankan paralel (ThreadPoolExecutor) karena murni network I/O.
Penulisan ke SQLite tetap diserialkan di thread utama (SQLite tidak aman
untuk ditulis banyak thread sekaligus) — jadi cepat sekaligus aman.

Usage:
  python3 tools/refresh_db.py                 # refresh semua yang expired
  python3 tools/refresh_db.py --all           # refresh SEMUA (walau masih valid)
  python3 tools/refresh_db.py --check         # cek saja, tanpa refresh
  python3 tools/refresh_db.py -c 16           # atur jumlah paralel (default 8)
"""

import json, sqlite3, sys, os, time, argparse, base64, threading
import requests
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.expanduser("~/.9router/db/data.sqlite")
CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
TOKEN_URL = "https://auth.x.ai/oauth2/token"

# Session dibagi antar-thread (requests.Session thread-safe untuk request
# sederhana) + connection pool cukup besar agar tidak jadi bottleneck.
_session = requests.Session()
_session.mount("https://", requests.adapters.HTTPAdapter(
    pool_connections=64, pool_maxsize=64, max_retries=0
))

_print_lock = threading.Lock()


def log(msg):
    with _print_lock:
        print(msg, flush=True)


def get_jwt_exp(token):
    """Decode JWT dan kembalikan timestamp expiry (0 kalau invalid)."""
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return 0
        payload = parts[1] + '=' * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return claims.get('exp', 0)
    except Exception:
        return 0


def refresh_token(refresh_tok):
    """Panggil endpoint OAuth2 token xAI. Kembalikan dict token baru / None."""
    try:
        r = _session.post(TOKEN_URL, data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_tok,
            "client_id": CLIENT_ID,
        }, timeout=15)
        d = r.json()
        if "access_token" in d:
            return d
        return None
    except Exception:
        return None


def _build_updated_data(data, result):
    """Terapkan hasil refresh ke dict data (dipanggil di worker; hanya
    memodifikasi dict lokal milik baris itu sendiri — tidak ada state global)."""
    data["accessToken"] = result["access_token"]
    if result.get("refresh_token"):
        data["refreshToken"] = result["refresh_token"]
    if result.get("id_token"):
        data["providerSpecificData"] = data.get("providerSpecificData", {})
        data["providerSpecificData"]["idToken"] = result["id_token"]
    expires_in = result.get("expires_in", 21600)
    data["expiresIn"] = expires_in
    expires_at = datetime.now(timezone.utc).timestamp() + expires_in
    data["expiresAt"] = datetime.fromtimestamp(expires_at, timezone.utc).isoformat()
    data["testStatus"] = "active"
    data["errorCode"] = None
    data["lastRefreshAt"] = datetime.now(timezone.utc).isoformat()
    return data


def _worker(item):
    """Jalan di thread pool. HANYA network + olah dict lokal, TANPA sentuh DB.
    Return: (row_id, email, data, status) di mana status ∈ {ok, fail, no_rt}."""
    row_id, email, data = item
    rt = data.get("refreshToken", "")
    if not rt:
        return (row_id, email, data, "no_rt")
    result = refresh_token(rt)
    if result:
        return (row_id, email, _build_updated_data(data, result), "ok")
    data["testStatus"] = "expired"
    return (row_id, email, data, "fail")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="refresh semua walau masih valid")
    ap.add_argument("--check", action="store_true", help="cek saja, tanpa refresh")
    ap.add_argument("-c", "--concurrency", type=int, default=8,
                    help="jumlah refresh paralel (default 8)")
    args = ap.parse_args()

    if not os.path.exists(DB_PATH):
        print(f"❌ DB not found: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")  # lebih aman untuk baca+tulis
    cur = conn.cursor()
    cur.execute(
        "SELECT id, email, data FROM providerConnections "
        "WHERE provider='grok-cli' AND isActive=1"
    )
    rows = cur.fetchall()

    now = time.time()
    expired_rows, valid_count = [], 0
    for row_id, email, data_str in rows:
        try:
            data = json.loads(data_str)
        except Exception:
            data = {}
            expired_rows.append((row_id, email, data))
            continue
        token = data.get("accessToken", "")
        if get_jwt_exp(token) < now:
            expired_rows.append((row_id, email, data))
        else:
            valid_count += 1

    if args.all:
        target = [(rid, em, json.loads(ds) if ds else {}) for rid, em, ds in rows]
    else:
        target = expired_rows

    conc = max(1, args.concurrency)
    print(f"Total: {len(rows)} | Valid: {valid_count} | Expired: {len(expired_rows)}")
    print(f"Refreshing: {len(target)} accounts (paralel: {conc})...\n")

    if args.check:
        print("Check-only mode. No refresh performed.")
        conn.close()
        return

    if not target:
        print("Tidak ada yang perlu di-refresh.")
        conn.close()
        return

    t0 = time.time()
    ok = fail = 0
    now_iso = lambda: datetime.now(timezone.utc).isoformat()

    # Network paralel; commit DB di thread utama saat hasil berdatangan.
    with ThreadPoolExecutor(max_workers=conc) as ex:
        futures = [ex.submit(_worker, item) for item in target]
        done = 0
        for fut in as_completed(futures):
            row_id, email, data, status = fut.result()
            done += 1
            if status == "no_rt":
                log(f"  [{done}/{len(target)}] ❌ {email} — no refresh_token")
                fail += 1
                continue
            cur.execute(
                "UPDATE providerConnections SET data=?, updatedAt=? WHERE id=?",
                (json.dumps(data), now_iso(), row_id)
            )
            if status == "ok":
                log(f"  [{done}/{len(target)}] ✅ {email}")
                ok += 1
            else:
                log(f"  [{done}/{len(target)}] ❌ {email} — refresh failed")
                fail += 1

    conn.commit()
    conn.close()

    elapsed = time.time() - t0
    print(f"\n✅ {ok} refreshed, ❌ {fail} failed ({len(target)} targeted)")
    print(f"⏱️  {elapsed:.1f}s" + (f"  ≈ {len(target)/elapsed:.1f} akun/detik" if elapsed > 0 else ""))


if __name__ == "__main__":
    main()
