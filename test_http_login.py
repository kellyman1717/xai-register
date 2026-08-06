#!/usr/bin/env python3
"""
Alat uji jalur HTTP: login (createSession) + device approve — TANPA browser approval.

Hasil sejauh ini:
  - Login createSession BERHASIL via HTTP tanpa castle.
  - Respons memuat cookieSetterUrl -> harus di-GET agar cookie sesi asli terpasang.

Pakai akun BUANGAN.

Contoh:
  python3 test_http_login.py --email AKUN@domain --password 'PASS'
"""
import argparse
import logging
import sys

import requests

import register as R
from http_login import create_session, has_auth_cookie, auth_cookie_names
from approve_http import approve_device_http

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("xai-register")

DBG = "http_login_debug.txt"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--castle-file", help="opsional: file berisi castleRequestToken")
    ap.add_argument("--no-approve", action="store_true")
    args = ap.parse_args()

    castle = ""
    if args.castle_file:
        try:
            with open(args.castle_file, encoding="utf-8") as f:
                castle = f.read().strip()
            print(f"[i] castle token dimuat: {len(castle)} char")
        except FileNotFoundError:
            print(f"[!] {args.castle_file} tidak ada — lanjut TANPA castle")
    else:
        print("[i] TANPA castle token")

    open(DBG, "w").close()

    s = requests.Session()
    s.headers.update({"User-Agent": R.UA})

    print("\n=== 1) SOLVE TURNSTILE ===")
    try:
        ts = R.solve_turnstile()
    except Exception as e:
        print(f"[X] solve_turnstile gagal: {e}")
        sys.exit(1)
    if not ts:
        print("[X] turnstile kosong")
        sys.exit(1)
    print(f"[✓] turnstile token: {len(ts)} char")

    print("\n=== 2) LOGIN createSession (HTTP) + cookie-setter ===")
    ok, resp = create_session(
        s, args.email, args.password, ts,
        castle_token=castle, ua=R.UA, debug_path=DBG,
    )
    status = resp.status_code if resp is not None else "no-response"
    print(f"    http_status : {status}")
    print(f"    auth_cookie : {has_auth_cookie(s)}")
    print(f"    cookies     : {auth_cookie_names(s)}")

    if not ok:
        print("\n[HASIL] Sesi belum terautentikasi (cookie sesi tak terpasang).")
        print("        Lihat http_login_debug.txt: cek setter_url_found & redirect chain.")
        sys.exit(2)

    print("\n[✓] SESI TERAUTENTIKASI via HTTP (cookie sesi terpasang).")

    if args.no_approve:
        return

    print("\n=== 3) DEVICE CODE ===")
    dev = R.fetch_device_code()
    device_code = dev["device_code"]
    user_code = dev["user_code"]
    print(f"    user_code   : {user_code}")

    print("\n=== 4) DEVICE APPROVE (HTTP) ===")
    approved = approve_device_http(s, user_code, ua=R.UA, debug_path=DBG)
    print(f"    approve_device_http -> {approved}")

    if not approved:
        print("\n[!] Approve gagal — STOP (tidak poll, biar tak menggantung).")
        print("    Login sudah OK; kemungkinan endpoint device/verify|approve beda.")
        print("    Lihat http_login_debug.txt (status + location device/verify & approve).")
        sys.exit(3)

    print("\n=== 5) POLL TOKEN (maks 45 dtk) ===")
    access, refresh, id_token = R.poll_device_token(device_code, max_wait=45)
    if access:
        print("[✓✓] TOKEN TERBIT — JALUR HTTP PENUH BERHASIL (0 browser)!")
        print(f"     access_token: {access[:24]}... refresh: {'ada' if refresh else 'tidak'}")
    else:
        print("[X] Token TIDAK terbit — approve kemungkinan dianggap 'deny'.")
        print("    Login+cookie sudah OK; tinggal endpoint/param APPROVE yang perlu dipastikan.")
        print("    -> Jalankan capture_approve.py untuk merekam request 'Allow' asli.")


if __name__ == "__main__":
    main()
