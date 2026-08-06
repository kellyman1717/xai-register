# xai-register

CLI untuk registrasi akun xAI/Grok dengan dashboard Rich, dukungan batch, dan penyimpanan token lokal.

> Gunakan hanya pada domain email, akun, dan layanan yang Anda miliki atau berwenang untuk kelola. Pastikan penggunaan sesuai ketentuan layanan terkait.

## Fitur

- Mode interaktif dengan panel `Log` dan `Tracking`.
- Input jumlah akun, paralel worker, dan mode email langsung di dalam panel `Tracking`.
- Dukungan batch berulang; total sesi dan riwayat batch tetap terlihat.
- Solver Turnstile melalui Boterdrop atau Camoufox.
- Penyimpanan akun dan token lokal.
- Push opsional ke FoxRouters dan 9Router.

## Struktur proyek

Entry point tetap berada di root agar perintah utama tetap sederhana:

```text
xai-register/
├── register.py          # registrasi interaktif / single run
├── bulk.py              # registrasi bulk
├── core/                # modul internal runtime
│   ├── approve.py
│   ├── approve_http.py
│   ├── http_login.py
│   ├── router9.py
│   ├── solver.py
│   ├── ui.py
│   └── _interactive.py
└── tools/               # utility pemeliharaan opsional
    ├── inject_9router.py
    ├── refresh_db.py
    └── refresh_tokens.py
```

File akun, token, database, dan log diagnosis tetap berada di luar repository melalui `.gitignore`.

## Persyaratan

- Python 3.10 atau lebih baru.
- Domain email catch-all yang dapat menerima OTP.
- Cloudflare Email Routing dan D1 untuk menyimpan email OTP.
- CloakBrowser dan Camoufox sesuai kebutuhan solver.

## Instalasi

### Windows PowerShell

```powershell
git clone https://github.com/kellyman1717/xai-register.git
Set-Location xai-register

py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item config.example.json config.json
```

Jika PowerShell memblokir aktivasi virtual environment, jalankan PowerShell sebagai user Anda dan gunakan kebijakan yang sesuai dengan lingkungan kerja. Anda juga dapat menjalankan `.\.venv\Scripts\python.exe` langsung tanpa aktivasi.

### Linux/macOS

```bash
git clone https://github.com/kellyman1717/xai-register.git
cd xai-register

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp config.example.json config.json
```

## Konfigurasi

Edit `config.json` di komputer lokal. File ini sengaja di-ignore oleh Git dan tidak boleh di-push.

Minimal isi yang perlu disesuaikan:

```json
{
  "d1": {
    "url": "https://api.cloudflare.com/client/v4/accounts/ACCOUNT_ID/d1/database/DB_ID/query",
    "token": "CLOUDFLARE_D1_API_TOKEN"
  },
  "email_domains": ["mail.example.com"],
  "default_password": "GUNAKAN_PASSWORD_LOKAL_YANG_KUAT"
}
```

Field penting:

- `d1.url`: endpoint query Cloudflare D1 milik Anda.
- `d1.token`: API token Cloudflare dengan izin query D1 yang diperlukan.
- `email_domains`: domain catch-all yang digunakan untuk alamat akun.
- `default_password`: password default lokal untuk mode interaktif.
- `boterdrop_url`: isi jika menjalankan Boterdrop Solver; kosongkan untuk memakai Camoufox bawaan.
- `turnstile_solver.pool_size`: jumlah solver Camoufox paralel.
- `router9.enabled`: `true` hanya jika 9Router lokal sudah dikonfigurasi.

Jangan menaruh token Cloudflare, password produksi, access token, refresh token, atau database lokal di `config.example.json`, README, issue, atau commit.

## Menyiapkan email OTP

1. Tambahkan domain ke Cloudflare.
2. Aktifkan Email Routing dan arahkan catch-all ke Worker Anda.
3. Simpan email masuk ke database D1.
4. Buat API token Cloudflare dengan izin query D1.
5. Pastikan Worker menyimpan data yang dapat dicari dengan query seperti berikut:

```sql
SELECT subject FROM email WHERE to_email = ?
```

Kode mengambil OTP dari subject email dengan format `XX-XXX`, misalnya `A9E-WJR`.

## Menjalankan

### Mode interaktif

```powershell
python register.py
# atau
python bulk.py
```

Dashboard akan meminta:

1. Jumlah akun.
2. Jumlah worker paralel atau `auto`.
3. Email acak atau manual.
4. Apakah hasil akan di-inject ke 9Router.
5. Apakah ingin membuat batch berikutnya.

Semua pertanyaan dan input diketik langsung di dalam kotak `Tracking`. Tekan `Ctrl+C` untuk menghentikan sesi.

### Perintah non-interaktif

```powershell
# Satu akun dengan parameter eksplisit
python register.py --email user@mail.example.com --password "PasswordLokal!"

# Beberapa akun dengan dua worker
python register.py -n 5 -c 2

# Bulk registration
python bulk.py 100 -c 2
python bulk.py 500 -c auto

# Tanpa dashboard
python register.py -n 5 --no-ui
```

Gunakan password melalui konfigurasi lokal atau parameter yang aman bagi lingkungan Anda. Jangan menyimpan password di script atau command history bila terminal Anda mencatat riwayat perintah.

### Utility pemeliharaan opsional

```powershell
# Refresh token yang tersimpan di accounts.json atau tokens/
python tools/refresh_tokens.py --expired-only

# Refresh koneksi token di database 9Router
python tools/refresh_db.py --check

# Inject file token ke database 9Router
python tools/inject_9router.py
```

## File hasil lokal

File berikut dibuat saat program berjalan dan sengaja tidak masuk repository:

- `accounts.json`: email dan password lokal.
- `accounts.jsonl`: log akun.
- `accounts_tokens.json`: email dan token.
- `tokens/`: file OAuth token per akun.
- `*_debug.txt`: log diagnosis.

Jika salah satu token atau password pernah terlanjur dipublikasikan, segera rotasi/revoke kredensial tersebut dan buat yang baru. Menghapus file pada commit terbaru tidak menghapusnya dari riwayat Git yang sudah ter-push.

## Pengujian dasar

```powershell
python -m compileall core register.py bulk.py tools
python core/_interactive.py
```

Perintah tersebut memeriksa sintaks dan self-check prompt interaktif tanpa membuat akun baru.

## Lisensi

Lihat [LICENSE](LICENSE).
