#!/usr/bin/env python3
"""
Diagnostik token device-flow.
=============================
Approve (browser MAUPUN HTTP) mencapai device/done tapi token = access_denied.
Skrip ini mengisolasi penyebabnya dalam SATU kali jalan, menguji:
  1. full scope  + principal_id terisi (user id dari cookie sso)
  2. full scope  + principal_id KOSONG (persis seperti browser)
  3. minimal scope + principal_id terisi
Dan mencetak respons PENUH dari endpoint /oauth2/token untuk tiap varian.

Pakai akun buangan:
  python3 diag_token.py --email AKUN@domain --password 'PASS'
"""
import argparse
import base64
import json
import sys
import time

import requests

import register as R
from http_login import create_session, has_auth_cookie, auth_cookie_names
from approve_http import approve_device_http

DBG = "diag_debug.txt"
TOKEN_URL = "https://auth.x.ai/oauth2/token"
CODE_URL = "https://auth.x.ai/oauth2/device/code"


def dec_jwt(tok):
    try:
        p = tok.split(".")[1]
        p += "=" * (-len(p) % 4)
        return json.loads(base64.urlsafe_b64decode(p))
    except Exception:
        return {}


def fetch_code(scope):
    r = requests.post(CODE_URL, data={"client_id": R.CLIENT_ID, "scope": scope}, timeout=15)
    print(f"    device/code HTTP {r.status_code}")
    try:
        d = r.json()
    except Exception:
        print("    resp:", (r.text or "")[:200])
        return None
    if "device_code" not in d:
        print("    resp:", json.dumps(d)[:300])
        return None
    return d


def poll_full(device_code, max_wait=24):
    """Poll token; cetak respons PENUH sekali, lalu lanjut sampai jelas."""
    t0 = time.time()
    first = True
    while time.time() - t0 < max_wait:
        try:
            r = requests.post(
                TOKEN_URL,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": device_code,
                    "client_id": R.CLIENT_ID,
                },
                timeout=15,
            )
            try:
                d = r.json()
            except Exception:
                d = {"_raw": (r.text or "")[:200]}
        except Exception as e:
            print("    token poll error:", e.__class__.__name__)
            time.sleep(3)
            continue
        if first:
            print(f"    token resp [{r.status_code}]: {json.dumps(d)[:400]}")
            first = False
        if "access_token" in d:
            return d["access_token"]
        err = d.get("error")
        if err not in (None, "authorization_pending", "slow_down"):
            return None
        time.sleep(3)
    return None


def run_variant(s, label, scope, principal_id):
    print("\n" + "=" * 62)
    print(f"VARIAN {label}")
    print(f"  scope        = {scope}")
    print(f"  principal_id = {principal_id!r}")
    d = fetch_code(scope)
    if not d:
        print("  [X] gagal ambil device code")
        return False
    uc = d["user_code"]
    dcode = d["device_code"]
    print(f"  user_code    = {uc}")
    ok = approve_device_http(s, uc, user_id=principal_id, ua=R.UA, debug_path=DBG)
    print(f"  approve      -> {ok}")
    if not ok:
        print("  [X] approve gagal (lihat diag_debug.txt)")
        return False
    access = poll_full(dcode, max_wait=24)
    if access:
        print(f"  [✓✓] TOKEN TERBIT! access={access[:28]}...")
        return True
    print("  [X] token ditolak untuk varian ini")
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    args = ap.parse_args()

    open(DBG, "w").close()
    s = requests.Session()
    s.headers.update({"User-Agent": R.UA})

    print("=== SOLVE TURNSTILE ===")
    ts = R.solve_turnstile()
    if not ts:
        print("[X] turnstile kosong")
        sys.exit(1)
    print(f"turnstile: {len(ts)} char")

    print("\n=== LOGIN (createSession + cookie-setter) ===")
    ok, resp = create_session(s, args.email, args.password, ts, ua=R.UA, debug_path=DBG)
    print("auth_cookie:", has_auth_cookie(s))
    print("cookies    :", auth_cookie_names(s))
    if not ok:
        print("[X] login gagal")
        sys.exit(2)

    # Decode SEMUA cookie sso untuk cari user id.
    user_id = ""
    for c in s.cookies:
        if c.name in ("sso", "sso-rw"):
            payload = dec_jwt(c.value)
            if payload:
                print(f"\n[{c.name}@{c.domain}] payload keys: {list(payload.keys())}")
                print(f"    payload: {json.dumps(payload)[:400]}")
                if not user_id:
                    user_id = (payload.get("sub") or payload.get("user_id")
                               or payload.get("uid") or payload.get("id") or "")
    print(f"\n[user_id terdeteksi] = {user_id!r}")

    FULL = R.SCOPE
    MIN = "openid profile email offline_access"

    got = False
    got |= run_variant(s, "1) full scope + principal_id TERISI", FULL, user_id)
    if not got:
        got |= run_variant(s, "2) full scope + principal_id KOSONG (=browser)", FULL, "")
    if not got:
        got |= run_variant(s, "3) minimal scope + principal_id TERISI", MIN, user_id)

    print("\n" + "=" * 62)
    if got:
        print("[HASIL] Salah satu varian BERHASIL menerbitkan token ✅")
        print("        -> beri tahu varian mana yang ✓✓; itu yang akan saya pakai.")
    else:
        print("[HASIL] Semua varian ditolak. Tempel seluruh output ini + isi diag_debug.txt.")


if __name__ == "__main__":
    main()
