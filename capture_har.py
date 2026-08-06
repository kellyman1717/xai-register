#!/usr/bin/env python3
"""
HAR CAPTURE HELPER — OAuth device-approval flow
===============================================
Tujuan: merekam SATU alur login + approve manual (di browser, via DevTools)
supaya bisa dianalisis dan diubah jadi HTTP murni (tanpa browser).

Helper ini:
  1. Membuat device code + user_code baru yang VALID (via register.py).
  2. Mencetak URL approval untuk kamu buka di Chrome/Edge.
  3. Menunggu di latar sambil kamu login + klik Allow secara manual.
  4. Begitu token terbit, memberi tahu bahwa HAR-mu sudah lengkap & valid.

Cara pakai:
    python capture_har.py
    python capture_har.py --email akun@domainmu --password "PasswordAkun"

Catatan:
  - Pakai akun yang SUDAH ADA (mis. salah satu dari accounts.json) untuk login.
  - user_code kadaluarsa cepat (biasanya beberapa menit), jadi siapkan
    DevTools SEBELUM menjalankan helper ini.
"""
import sys
import argparse
from register import fetch_device_code, poll_device_token, DEF_PASS


def main():
    ap = argparse.ArgumentParser(description="HAR capture helper for device approval")
    ap.add_argument("--email", help="Email akun yang sudah ada (untuk login manual)")
    ap.add_argument("--password", help="Password akun (default: default_password di config)")
    ap.add_argument("--wait", type=int, default=300, help="Waktu tunggu approval (detik), default 300")
    args = ap.parse_args()

    email = args.email or "(akun apa saja yang sudah ada)"
    pw = args.password or DEF_PASS or "(password akunmu)"

    line = "=" * 66
    print(line)
    print("  HAR CAPTURE HELPER — OAuth Device Approval")
    print(line)

    print("\n[1/3] Meminta device code baru ke auth.x.ai ...")
    dc = fetch_device_code()
    if not dc:
        print("❌ Gagal ambil device code. Cek koneksi internet & config.json.")
        sys.exit(1)

    user_code = dc["user_code"]
    device_code = dc["device_code"]
    url = f"https://accounts.x.ai/oauth2/device?user_code={user_code}"

    print(f"      ✅ user_code: {user_code}")
    if dc.get("expires_in"):
        print(f"      ⏱  kadaluarsa dalam ~{dc['expires_in']}s — kerjakan segera!")

    print("\n[2/3] REKAM HAR — lakukan ini di Chrome/Edge:")
    print("      a. Tekan F12 → tab 'Network'")
    print("      b. Centang 'Preserve log' (WAJIB)")
    print("      c. Pastikan tombol rekam aktif (bulat merah)")
    print("      d. Buka URL berikut di tab yang sama:\n")
    print(f"         {url}\n")
    print(f"      e. Klik Continue → Login with email")
    print(f"         email    : {email}")
    print(f"         password : {pw}")
    print("      f. Selesaikan sampai muncul halaman 'device approved / done'")
    print("      g. Di panel Network: klik kanan → 'Save all as HAR with content'")
    print("         Simpan sebagai: grok-approve.har")

    print(f"\n[3/3] Menunggu approval (maks {args.wait}s) ...", flush=True)
    access, refresh, id_token = poll_device_token(device_code, max_wait=args.wait)

    print("\n" + line)
    if access:
        print("  ✅ APPROVAL BERHASIL — token terbit.")
        print("     Berarti HAR-mu berisi alur LENGKAP & valid.")
        print("     Kirim file 'grok-approve.har' untuk dianalisis.")
    else:
        print("  ⚠️  Belum ada token (timeout / belum di-approve / user_code kadaluarsa).")
        print("     Jalankan ulang: python capture_har.py  (dapat user_code baru)")
    print(line)


if __name__ == "__main__":
    main()
