#!/usr/bin/env python3
"""
Bantu meng-capture HAR langkah APPROVE device yang ASLI.
===============================================================
Login HTTP sudah terbukti jalan. Yang belum pasti: request "Allow" saat
menyetujui device — nama endpoint + field-nya. Skrip ini hanya:
  1. Mengambil device code baru & mencetak URL device.
  2. Polling token di latar; memberi tahu saat approve berhasil.

Kamu yang klik Allow di browser (yang SUDAH login) sambil merekam HAR.

Cara pakai:
  1. Di browser, login normal ke https://accounts.x.ai (akun buangan).
  2. Buka DevTools (F12) -> tab Network -> centang "Preserve log" + "Disable cache".
  3. Jalankan:  python3 capture_approve.py
  4. Salin URL device yang dicetak -> tempel di tab browser (yang sudah login).
  5. Klik Continue / Allow sampai muncul "device approved / done".
  6. Di Network: klik kanan -> "Save all as HAR with content" -> simpan
     sebagai grok-approve.har, lalu kirim ke chat.
"""
import register as R


def main():
    print("=== ambil device code ===")
    dc = R.fetch_device_code()
    if not dc:
        print("[X] gagal ambil device code")
        return
    user_code = dc["user_code"]
    device_code = dc["device_code"]
    url = f"https://accounts.x.ai/oauth2/device?user_code={user_code}"
    print("\n" + "=" * 60)
    print("  BUKA URL INI DI BROWSER YANG SUDAH LOGIN (sambil rekam HAR):")
    print("  " + url)
    print(f"  user_code: {user_code}")
    print("=" * 60)
    print("\nMenunggu approve (maks 5 menit)... klik Allow di browser.\n", flush=True)

    access, refresh, id_token = R.poll_device_token(device_code, max_wait=300)
    if access:
        print("\n[✓✓] APPROVE TERDETEKSI — token terbit. HAR-mu valid, kirim ke chat!")
    else:
        print("\n[!] Belum ada token. Kalau kamu sudah klik Allow, HAR tetap berguna — kirim saja.")


if __name__ == "__main__":
    main()
