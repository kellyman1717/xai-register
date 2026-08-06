#!/usr/bin/env python3
"""
xAI/Grok Account Registration — All-in-One
==========================================
Pure HTTP registration via xAI gRPC-Web API + CloakBrowser OAuth approval.
Turnstile solver built-in (Camoufox) — no external solver server needed.

Optimizations:
  - Internal Turnstile solver via solver.py (Camoufox, no separate repo)
  - Parallel Turnstile solving (prefetch approval TS while registering)
  - Faster polling intervals
  - Cross-platform (Windows/Linux auto-detect)

Requirements:
  pip install requests cloakbrowser camoufox

Usage:
  python3 register.py                           # 1 random account
  python3 register.py -n 5                      # 5 random accounts
  python3 register.py --email x@domain.com --password 'P@ss'
"""

import requests, json, base64, time, uuid, re, sys, os, random, argparse, logging, threading
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
CORE_DIR = os.path.join(BASE, "core")
if CORE_DIR not in sys.path:
    sys.path.insert(0, CORE_DIR)

import router9  # optional auto-push ke 9Router DB (aktif via config router9.enabled)
from _interactive import run_session


# ponytail: interactive mode suppresses per-account 9Router push and batches it after confirmation.

# Ensure emoji / Unicode output works on Windows consoles (cp1252/cp437).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("xai-register")

def _load_config():
    config_path = os.path.join(BASE, "config.json")
    if not os.path.exists(config_path):
        log.error(f"config.json not found at {config_path}")
        log.error("Copy config.example.json → config.json and fill in your values:")
        log.error("  cp config.example.json config.json")
        sys.exit(1)
    with open(config_path, encoding="utf-8") as f:
        try:
            cfg = json.load(f)
        except json.JSONDecodeError as e:
            log.error(f"config.json is not valid JSON: {e}")
            sys.exit(1)
    required = {"d1": ["url", "token"], "email_domains": []}
    for key, subkeys in required.items():
        if key not in cfg:
            log.error(f"Missing required config key: '{key}'. See config.example.json")
            sys.exit(1)
        for sk in subkeys:
            if sk not in cfg[key]:
                log.error(f"Missing required config: '{key}.{sk}'. See config.example.json")
                sys.exit(1)
    if not cfg.get("default_password"):
        log.warning("No default_password set — you must provide --password for every run")
    return cfg


CFG = _load_config()

D1_URL           = CFG["d1"]["url"]
D1_TOKEN         = CFG["d1"]["token"]
CLIENT_ID        = CFG.get("client_id", "b1a00492-073a-47ea-816f-4c329264a828")
SITEKEY          = CFG.get("sitekey", "0x4AAAAAAAhr9JGVDZbrZOo0")
DOMAINS          = CFG.get("email_domains", [CFG.get("email_domain", "yourdomain.com")])
DOMAIN           = DOMAINS[0]
DEF_PASS         = CFG.get("default_password", "")
TOKENS_DIR       = CFG.get("tokens_dir", os.path.join(BASE, "tokens"))
ACCTS_FILE       = CFG.get("accounts_file", os.path.join(BASE, "accounts.jsonl"))
ACCOUNTS_JSON        = CFG.get("accounts_json", os.path.join(BASE, "accounts.json"))            # file 1: email + password
ACCOUNTS_TOKENS_JSON = CFG.get("accounts_tokens_json", os.path.join(BASE, "accounts_tokens.json"))  # file 2: email + tokens
HTTP_APPROVE     = CFG.get("http_approve", False)  # experimental: approve device via HTTP (no browser)
HTTP_APPROVE_DBG = os.path.join(BASE, "http_approve_debug.txt") if CFG.get("http_approve_debug", True) else None
FOX_CFG          = CFG.get("foxrouter", {})
FOX_URL          = FOX_CFG.get("url", "")
FOX_KEY          = FOX_CFG.get("key", "")

# 9Router auto-push (opsional — aktif jika router9.enabled=true dan DB ada)
R9_ENABLED  = router9.enabled()
R9_DB_PATH  = router9.get_db_path()

# Boterdrop-Solver (external Turnstile solver server, FastAPI)
BOTERDROP_URL    = CFG.get("boterdrop_url", "").rstrip("/")
# Cap how many /turnstile solves hit Boterdrop at once (its worker pool is
# limited; too many simultaneous submits => HTTP 429). Tune to match your
# Boterdrop-Solver capacity. Default is intentionally conservative.
BOTERDROP_MAX_CONC = int(CFG.get("boterdrop_max_concurrent", 3) or 3)
TS_REGISTER_RETRIES = int(CFG.get("ts_register_retries", 3) or 3)  # fresh-turnstile retries at CreateUserAndSession
_BOTERDROP_SEM   = threading.Semaphore(max(1, BOTERDROP_MAX_CONC))
TS_PAGE_URL      = CFG.get("ts_page_url", "https://accounts.x.ai/")

# Solver config
SOLVER_CFG       = CFG.get("turnstile_solver", {})
SOLVER_POOL_SIZE = SOLVER_CFG.get("pool_size", 2)
SOLVER_HEADLESS  = SOLVER_CFG.get("headless", True)

# Optimized timings
POLL_OTP      = 0.5   # OTP poll interval
POLL_TOKEN    = 0.3   # Token poll interval

SCOPE = "openid profile email offline_access grok-cli:access api:access conversations:read conversations:write"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"


# ─── Internal solver (lazy import) ─────────────────────────────

_solver = None

def _get_solver():
    global _solver
    if _solver is None:
        from solver import solve_sync, configure
        configure(pool_size=SOLVER_POOL_SIZE, headless=SOLVER_HEADLESS)
        _solver = solve_sync
    return _solver


# ─── Protobuf helpers ─────────────────────────────────────────

def _ev(v):
    b = []
    while v > 0x7F:
        b.append((v & 0x7F) | 0x80)
        v >>= 7
    b.append(v & 0x7F)
    return b

def _es(fn, val):
    enc = val.encode()
    return _ev((fn << 3) | 2) + _ev(len(enc)) + list(enc)

def _ei(fn, val):
    return _ev((fn << 3) | 0) + _ev(val)

def _em(fn, data):
    if isinstance(data, bytes):
        data = list(data)
    return _ev((fn << 3) | 2) + _ev(len(data)) + data

def _grpc(endpoint, payload, session):
    if isinstance(payload, list):
        payload = bytes(payload)
    frame = bytes([0]) + len(payload).to_bytes(4, "big") + payload
    return session.post(
        f"https://accounts.x.ai/auth_mgmt.AuthManagement/{endpoint}",
        headers={
            "Content-Type": "application/grpc-web-text+proto",
            "X-User-Agent": "grpc-web-javascript/0.1",
            "X-Grpc-Web": "1",
            "User-Agent": UA,
            "Origin": "https://accounts.x.ai",
            "Referer": "https://accounts.x.ai/",
        },
        data=base64.b64encode(frame).decode(),
        timeout=30,
    )


# ─── Turnstile solver — unified interface ─────────────────────

def solve_via_boterdrop(timeout=120):
    """Solve Turnstile via an external Boterdrop-Solver server (FastAPI).

    API:
      create: GET {BOTERDROP_URL}/turnstile?url=...&sitekey=...  -> {"task_id","status"}
      poll:   GET {BOTERDROP_URL}/result?id=<task_id>            -> {"status","value"}
                (HTTP 202 = still processing, 200 = done, 404/408 = expired/timeout)
    """
    if not BOTERDROP_URL:
        return None
    t0 = time.time()
    # Throttle concurrent submits so we don't overwhelm the Boterdrop worker
    # pool (which answers HTTP 429 when full).
    with _BOTERDROP_SEM:
        try:
            # ── create task (retry on 429 / 5xx with backoff) ──
            task_id = None
            attempt = 0
            while time.time() - t0 < timeout:
                attempt += 1
                try:
                    r = requests.get(
                        f"{BOTERDROP_URL}/turnstile",
                        params={"url": TS_PAGE_URL, "sitekey": SITEKEY},
                        timeout=20,
                    )
                except requests.exceptions.RequestException as e:
                    log.warning(
                        f"Boterdrop unreachable at {BOTERDROP_URL} — is the "
                        f"Boterdrop-Solver server running? ({e.__class__.__name__})"
                    )
                    return None
                if r.status_code == 429 or r.status_code >= 500:
                    # server busy — back off (capped) with jitter, then retry
                    time.sleep(min(5.0, 1.0 * attempt) + random.uniform(0, 0.8))
                    continue
                try:
                    data = r.json()
                except Exception:
                    log.debug(f"Boterdrop: create HTTP {r.status_code}, non-JSON body")
                    time.sleep(1 + random.uniform(0, 0.5))
                    continue
                # Some builds may return the token directly on the create call
                if data.get("status") == "success" and data.get("value"):
                    return data["value"]
                task_id = data.get("task_id")
                if task_id:
                    break
                log.debug(f"Boterdrop: no task_id in response: {data}")
                return None
            if not task_id:
                log.warning("Boterdrop: busy (429) — gave up creating task within timeout")
                return None

            # ── poll for result ──
            while time.time() - t0 < timeout:
                time.sleep(1)
                try:
                    rr = requests.get(f"{BOTERDROP_URL}/result", params={"id": task_id}, timeout=20)
                except requests.exceptions.RequestException:
                    continue
                if rr.status_code in (202, 429):
                    # still processing or momentarily rate-limited — keep waiting
                    continue
                if rr.status_code in (404, 408):
                    log.debug(f"Boterdrop: result HTTP {rr.status_code}")
                    return None
                try:
                    res = rr.json()
                except Exception:
                    continue
                status = res.get("status")
                if status == "success":
                    return res.get("value")
                if status in ("error", "failed"):
                    log.debug(f"Boterdrop: solve failed: {res}")
                    return None
            return None
        except Exception as e:
            log.debug(f"Boterdrop error: {e}")
            return None


def solve_turnstile():
    """
    Solve Cloudflare Turnstile.
    Priority:
      1. Boterdrop-Solver (external FastAPI server) when `boterdrop_url` is set
      2. Internal solver.py (Camoufox) — only when no Boterdrop server is set
      3. 2captcha / Capsolver (only if a key is configured)
    """
    t0 = time.time()

    # ── Boterdrop-Solver (external FastAPI server) ──
    if BOTERDROP_URL:
        try:
            print("    [ts] boterdrop...", end="", flush=True)
            token = solve_via_boterdrop()
            if token:
                print(f" ✅ ({time.time()-t0:.0f}s)", flush=True)
                return token
            print(" failed", flush=True)
        except Exception as e:
            log.debug(f"Boterdrop error: {e}")
            print(f" ❌ {e}", flush=True)

    # ── Internal solver (Camoufox) — skipped when Boterdrop is configured ──
    if not BOTERDROP_URL:
        try:
            print("    [ts] internal...", end="", flush=True)
            solve = _get_solver()
            token = solve(
                url="https://accounts.x.ai/",
                sitekey=SITEKEY,
                poll_interval=1,
                timeout=30,
            )
            if token:
                print(f" ✅ ({time.time()-t0:.0f}s)", flush=True)
                return token
            print(" timeout", flush=True)
        except ImportError:
            print(" ❌ camoufox not installed", flush=True)
            log.error("Install: pip install camoufox")
        except Exception as e:
            log.debug(f"Internal solver error: {e}")
            print(f" ❌ {e}", flush=True)

    # ── Fallback: 2captcha ──
    twocaptcha_key = CFG.get("twocaptcha_key", os.environ.get("TWOCAPTCHA_API_KEY", ""))
    if twocaptcha_key:
        try:
            print("    [ts] 2captcha...", end="", flush=True)
            r = requests.post(
                "https://2captcha.com/in.php",
                json={"key": twocaptcha_key, "method": "turnstile",
                      "sitekey": SITEKEY, "pageurl": "https://accounts.x.ai/", "json": 1},
                timeout=10,
            )
            resp = r.json()
            tid = resp.get("request")
            if tid and tid not in ("ERROR_WRONG_USER_KEY",):
                for _ in range(25):
                    time.sleep(3)
                    r2 = requests.get(
                        "https://2captcha.com/res.php",
                        params={"key": twocaptcha_key, "action": "get", "id": tid, "json": 1},
                        timeout=10,
                    )
                    d2 = r2.json()
                    if d2.get("request") and d2["request"] not in ("CAPCHA_NOT_READY",):
                        tok = d2["request"]
                        print(f" ✅ ({time.time()-t0:.0f}s)", flush=True)
                        return tok
                print(" timeout", flush=True)
        except Exception as e:
            log.debug(f"2captcha error: {e}")

    # ── Fallback: Capsolver ──
    capsolver_key = CFG.get("capsolver_key", "")
    if capsolver_key:
        try:
            print("    [ts] capsolver...", end="", flush=True)
            r = requests.post(
                "https://api.capsolver.com/createTask",
                json={"clientKey": capsolver_key, "task": {
                    "type": "AntiTurnstileTaskProxyLess",
                    "websiteURL": "https://accounts.x.ai/",
                    "websiteKey": SITEKEY,
                }},
                timeout=10,
            )
            resp = r.json()
            tid = resp.get("taskId")
            if tid:
                for _ in range(30):
                    time.sleep(1)
                    r2 = requests.post(
                        "https://api.capsolver.com/getTaskResult",
                        json={"clientKey": capsolver_key, "taskId": tid},
                        timeout=10,
                    )
                    result = r2.json()
                    if result.get("status") == "ready":
                        tok = result["solution"]["token"]
                        print(f" ✅ ({time.time()-t0:.0f}s)", flush=True)
                        return tok
                print(" timeout", flush=True)
        except Exception as e:
            log.debug(f"Capsolver error: {e}")

    return None


# ─── OTP polling (Cloudflare D1) ──────────────────────────────

def poll_otp(email, wait=90):
    headers = {"Authorization": f"Bearer {D1_TOKEN}", "Content-Type": "application/json"}
    seen = set()
    t0 = time.time()
    while time.time() - t0 < wait:
        try:
            r = requests.post(
                D1_URL, headers=headers,
                json={"sql": "SELECT subject, create_time FROM email WHERE to_email = ? ORDER BY email_id DESC LIMIT 5",
                      "params": [email]},
                timeout=10,
            )
            for row in r.json().get("result", [{}])[0].get("results", []):
                m = re.search(r"\b([A-Z0-9]{2,4}-[A-Z0-9]{2,4})\b", row.get("subject", ""))
                if m:
                    k = f"{m.group(1)}_{row.get('create_time', '')}"
                    if k not in seen:
                        seen.add(k)
                        return m.group(1)
        except Exception as e:
            log.debug(f"OTP poll error: {e}")
        time.sleep(POLL_OTP)
    return None


# ─── Device code helpers ──────────────────────────────────────

def fetch_device_code(retries=3):
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(
                "https://auth.x.ai/oauth2/device/code",
                data={"client_id": CLIENT_ID, "scope": SCOPE},
                timeout=10,
            )
            if resp.status_code != 200:
                print(f"    [device] try {attempt}/{retries}: HTTP {resp.status_code}")
                time.sleep(1 * attempt)
                continue
            dr = resp.json()
            if "user_code" in dr and "device_code" in dr:
                return dr
        except Exception as e:
            print(f"    [device] try {attempt}/{retries}: {e}")
            time.sleep(1 * attempt)
    return None


def poll_device_token(device_code, max_wait=90):
    t0 = time.time()
    for _ in range(int(max_wait / POLL_TOKEN)):
        try:
            resp = requests.post(
                "https://auth.x.ai/oauth2/token",
                data={"grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                      "device_code": device_code, "client_id": CLIENT_ID},
                timeout=10,
            )
            d = resp.json()
            if "access_token" in d:
                return d["access_token"], d.get("refresh_token", ""), d.get("id_token", "")
            err = d.get("error")
            if err not in (None, "authorization_pending", "slow_down"):
                print(f"    ❌ token: {err} {d.get('error_description', '')[:80]}")
                return None, None, None
            if err == "slow_down":
                time.sleep(2)
        except Exception as e:
            log.debug(f"Token poll error: {e}")
        time.sleep(POLL_TOKEN)  # poll ASAP first, then back off briefly
    print(f"    ❌ token timeout ({max_wait}s)")
    return None, None, None


# ─── Email generator ──────────────────────────────────────────

_NAMES = [
    "alex", "sam", "jordan", "taylor", "morgan", "kai", "aria", "nova", "luna", "milo",
    "leo", "iris", "ruby", "jade", "max", "nora", "emma", "liam", "noah", "ethan",
    "owen", "ella", "chloe", "mason", "lucas", "sofia", "maya", "zoe", "ivy", "cole",
    "luke", "grace", "oliver", "elijah", "theo", "oscar", "felix", "marcus", "sean",
]
_ADJ = [
    "swift", "bright", "calm", "deep", "fast", "bold", "cool", "wild", "keen", "soft",
    "warm", "pure", "vast", "wise", "true", "gold", "slate", "amber", "storm", "frost",
    "ember", "dawn", "dusk", "solar", "neon", "glow", "echo", "apex", "core", "flux",
    "wave", "iron", "onyx", "opal", "pearl", "ocean", "cloud", "mist", "vale", "peak",
]


def rand_email():
    domain = random.choice(DOMAINS)
    # High-entropy suffix so we don't collide with already-registered emails.
    # (~ tens of millions of variants per name combo instead of just 99.)
    tail = f"{random.randint(100, 9999)}{random.choice('abcdefghijklmnopqrstuvwxyz')}{random.randint(10, 99)}"
    s = random.randint(0, 2)
    if s == 0:
        return f"{random.choice(_NAMES)}.{random.choice(_NAMES)}{tail}@{domain}"
    if s == 1:
        return f"{random.choice(_ADJ)}.{random.choice(_NAMES)}{tail}@{domain}"
    return f"{random.choice(_NAMES)}_{random.choice(_ADJ)}{tail}@{domain}"


def dec_jwt(token):
    try:
        payload = token.split(".")[1] + "==="
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


# ─── FoxRouters push ──────────────────────────────────────────

def push_to_foxrouter(accounts):
    if not accounts or not FOX_KEY or not FOX_URL:
        return 0
    try:
        r = requests.post(
            f"{FOX_URL}/accounts/import/bulk",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {FOX_KEY}"},
            json={"accounts": accounts},
            timeout=60,
        )
        if r.status_code == 200:
            data = r.json()
            added = data.get("added", 0)
            updated = data.get("updated", 0)
            print(f"    🦊 FoxRouters: +{added} new, ~{updated} updated")
            return added + updated
        print(f"    ❌ FoxRouters: {r.status_code} {r.text[:120]}")
        return 0
    except Exception as e:
        print(f"    ❌ FoxRouters: {e}")
        return 0


# ─── Prefetch turnstile (parallel) ────────────────────────────

def _prefetch_turnstile(result_holder):
    try:
        result_holder["token"] = solve_turnstile()
    except Exception as e:
        result_holder["error"] = str(e)


# ─── Main registration ────────────────────────────────────────

_BASE_DELAY = 1.5
_MAX_DELAY  = 30

# Serialize writes to shared output files so parallel workers don't interleave.
_write_lock = threading.Lock()


def _migrate_split_account_files():
    """One-time split of the old accounts.json (token format) into two files:
      - accounts.json        -> [{email, password}, ...]
      - accounts_tokens.json -> [{email, access_token, refresh_token, id_token, expires_in}, ...]
    Safe & idempotent. Runs at import time before any registration.
    """
    # 1. Preserve old token-format accounts.json as accounts_tokens.json (if not already split).
    if os.path.exists(ACCOUNTS_JSON) and not os.path.exists(ACCOUNTS_TOKENS_JSON):
        try:
            with open(ACCOUNTS_JSON, encoding="utf-8") as f:
                old = json.load(f)
            if isinstance(old, list) and old and any("access_token" in a for a in old):
                os.replace(ACCOUNTS_JSON, ACCOUNTS_TOKENS_JSON)
        except Exception as e:
            log.debug(f"migration: read old accounts.json failed: {e}")

    # 2. Rebuild accounts.json (email+password) from accounts.jsonl if it is missing/empty,
    #    so existing accounts are present in the new credential file too.
    if (not os.path.exists(ACCOUNTS_JSON)) or os.path.getsize(ACCOUNTS_JSON) == 0:
        seen = {}
        if os.path.exists(ACCTS_FILE):
            try:
                with open(ACCTS_FILE, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except Exception:
                            continue
                        em = rec.get("email")
                        pwp = rec.get("password")
                        if em and pwp is not None:
                            seen[em] = {"email": em, "password": pwp}
            except Exception as e:
                log.debug(f"migration: read accounts.jsonl failed: {e}")
        try:
            with open(ACCOUNTS_JSON, "w", encoding="utf-8") as f:
                json.dump(list(seen.values()), f, indent=2)
        except Exception as e:
            log.debug(f"migration: write accounts.json failed: {e}")


_migrate_split_account_files()


def _auto_concurrency(per_worker_ram_gb=1.5, cpu_factor=0.6, hard_cap=16):
    """Pick a safe number of parallel workers from available CPU/RAM.

    Each worker drives ~2 browsers (approval Chromium + Boterdrop Camoufox),
    so it is bounded by both CPU threads and free RAM. GPU is intentionally
    not used: the workload is network/browser-wait bound and headless runs
    with --disable-gpu, so a GPU would not speed anything up.
    """
    cpu = os.cpu_count() or 4
    cpu_based = max(1, int(cpu * cpu_factor))
    try:
        import psutil
        avail_gb = psutil.virtual_memory().available / (1024 ** 3)
        ram_based = max(1, int(avail_gb / per_worker_ram_gb))
        workers = min(cpu_based, ram_based, hard_cap)
        log.info(f"Auto-concurrency: CPU threads={cpu}, free RAM={avail_gb:.0f}GB -> {workers} workers")
    except Exception:
        workers = min(cpu_based, 8, hard_cap)
        log.info(f"Auto-concurrency: CPU threads={cpu} (install psutil for RAM-aware tuning) -> {workers} workers")
    return max(1, workers)


def register(email=None, password=None, max_retries=2, inject_9router=None):
    """Register a single xAI/Grok account."""
    from approve import approve_device

    for attempt in range(1, max_retries + 1):
        t0 = time.time()
        pw = password or DEF_PASS
        if not email:
            email = rand_email()
        if attempt > 1:
            print(f"\n  🔄 retry {attempt}/{max_retries} — {email}")
        else:
            print(f"\n  {email}")

        s = requests.Session()
        s.headers.update({"User-Agent": UA})

        # Prefetch BOTH turnstile tokens in the background:
        #   ts1 → used at [4] register  (overlaps with OTP polling — big speedup)
        #   ts2 → used at [5]/[6] approve (overlaps with the whole register phase)
        # If a prefetched token turns out stale by use time, the existing
        # re-solve retry loop (TS_REGISTER_RETRIES) falls back to a fresh solve.
        ts1_holder = {}
        ts1_thread = threading.Thread(target=_prefetch_turnstile, args=(ts1_holder,), daemon=True)
        ts1_thread.start()
        ts2_holder = {}
        ts2_thread = threading.Thread(target=_prefetch_turnstile, args=(ts2_holder,), daemon=True)
        ts2_thread.start()

        # 1. Send OTP
        s.get("https://accounts.x.ai/sign-up", timeout=10)
        _grpc("CreateEmailValidationCode", bytes(_es(1, email)), s)

        # 2. Poll OTP
        otp = poll_otp(email)
        if not otp:
            print("    ❌ no OTP (check D1 config / email routing)")
            if attempt < max_retries:
                time.sleep(2)
                continue
            return None
        print(f"    [1] OTP: {otp} ✅")

        # 3. Verify email
        _grpc("VerifyEmailValidationCode", bytes(_es(1, email) + _es(2, otp)), s)

        # 4. Register — reuse the turnstile token prefetched in parallel with the
        #    OTP wait above; if xAI rejects it as stale, re-solve a FRESH token
        #    (existing retry loop). Wait for the prefetch (bounded) so we reuse it.
        ts1_thread.join(timeout=25)
        registered = False
        r = None
        for ts_try in range(1, TS_REGISTER_RETRIES + 1):
            if ts_try == 1 and ts1_holder.get("token"):
                ts = ts1_holder["token"]
                print("    [2] turnstile ✅ (prefetched)")
            else:
                ts = solve_turnstile()
                if not ts:
                    print("    ❌ turnstile")
                    break
                print("    [2] turnstile ✅" if ts_try == 1
                      else f"    [2] turnstile ✅ (re-solve {ts_try}/{TS_REGISTER_RETRIES})")
            aa = _es(1, ts)
            cur = _es(1, "Test") + _es(2, "User") + _es(3, email) + _es(5, pw) + _ei(6, 1)
            outer = _em(1, cur) + _em(6, aa) + _es(9, otp) + _es(10, str(uuid.uuid4()))
            r = _grpc("CreateUserAndSession", bytes(outer), s)
            if re.findall(r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", r.text):
                registered = True
                break
            low = (r.text or "").lower()
            if any(k in low for k in ("exist", "already", "taken", "in use")):
                print("    ❌ email already exists — akan pakai email baru")
                break  # re-solving won't help; outer loop will pick a fresh email
            print(f"    ⚠️  register ditolak (turnstile basi?) — solve ulang {ts_try}/{TS_REGISTER_RETRIES}")
        if not registered:
            print("    ❌ register failed (email may exist or turnstile rejected)")
            if attempt < max_retries:
                email = None  # pilih email baru di percobaan berikutnya
                time.sleep(2)
                continue
            return None
        print(f"    [3] registered ✅ ({time.time()-t0:.0f}s)")

        # 5. Device code
        dc = fetch_device_code()
        if not dc:
            print("    ❌ device code failed")
            if attempt < max_retries:
                time.sleep(2)
                continue
            return None
        device_code, user_code = dc["device_code"], dc["user_code"]
        print(f"    [4] device: {user_code}")

        # Wait for parallel turnstile (used for HTTP login via createSession).
        ts2_thread.join(timeout=30)
        ts2 = ts2_holder.get("token")
        if not ts2:
            print("    [ts] parallel timed out, retrying...", flush=True)
            ts2 = solve_turnstile()
        if not ts2:
            print("    ❌ turnstile (approve)")
            if attempt < max_retries:
                time.sleep(2)
                continue
            return None
        print(f"    [5] turnstile-2 ✅")

        # 6. Approve — login the freshly-created account over HTTP (createSession)
        #    and follow the cookie-setter chain all the way to auth.x.ai so the
        #    session carries the x.ai cookie, then verify+approve the device.
        #    Fall back to the browser if the HTTP path fails (e.g. Cloudflare WAF).
        print(f"    [6] approving...")
        approved = False
        if HTTP_APPROVE:
            try:
                from http_login import create_session
                ok_login, _ = create_session(s, email, pw, ts2, ua=UA,
                                             user_code=user_code, debug_path=HTTP_APPROVE_DBG)
                if ok_login:
                    print("    [6] HTTP session established ✅")
                    from approve_http import approve_device_http
                    if approve_device_http(s, user_code, ua=UA, debug_path=HTTP_APPROVE_DBG):
                        approved = True
                        print("    [6] approved via HTTP (no browser) ✅")
                    else:
                        print("    [6] HTTP approve step failed → browser fallback")
                else:
                    print("    [6] HTTP login failed → browser fallback")
            except Exception as _e:
                log.warning(f"HTTP approve error, will fall back to browser: {_e}")
        if not approved:
            # Browser fallback — re-solve a fresh ts if the prefetched one was
            # consumed by createSession above.
            if HTTP_APPROVE:
                print("    [ts] fresh turnstile for browser...", flush=True)
                ts2 = solve_turnstile()
                if not ts2:
                    print("    ❌ turnstile (approve browser)")
                    if attempt < max_retries:
                        time.sleep(2)
                        continue
                    return None
            approved = approve_device(user_code, email, pw, ts2)
        if not approved:
            print(f"    ❌ approve failed")
            if attempt < max_retries:
                email = None
                time.sleep(3)
                continue
            return None

        # 7. Poll token
        access, refresh, id_token = poll_device_token(device_code)
        if not access:
            print(f"    ❌ token poll failed")
            if attempt < max_retries:
                time.sleep(2)
                continue
            return None

        elapsed = time.time() - t0
        out = {
            "email": email, "password": pw,
            "access_token": access, "refresh_token": refresh,
            "id_token": id_token or "", "expires_in": 21600,
            "user_id": dec_jwt(access).get("sub", ""),
            "elapsed": round(elapsed, 1),
        }

        # Save token + accounts log (locked so parallel workers don't clash)
        fname = email.split("@")[0]
        with _write_lock:
            os.makedirs(TOKENS_DIR, exist_ok=True)
            with open(os.path.join(TOKENS_DIR, f"{fname}.json"), "w", encoding="utf-8") as f:
                json.dump({
                    "access_token": out["access_token"],
                    "refresh_token": out["refresh_token"],
                    "id_token": out["id_token"],
                    "expires_in": out["expires_in"],
                    "token_type": "Bearer",
                    "scope": SCOPE,
                }, f, indent=2)

            with open(ACCTS_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "email": email, "password": pw,
                    "user_id": out["user_id"],
                    "token_file": f"tokens/{fname}.json",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }) + "\n")

            # File 1: accounts.json — email + password (dedupe by email)
            _creds = []
            if os.path.exists(ACCOUNTS_JSON):
                try:
                    with open(ACCOUNTS_JSON, encoding="utf-8") as f:
                        _creds = json.load(f)
                    if not isinstance(_creds, list):
                        _creds = []
                except Exception:
                    _creds = []
            _creds = [a for a in _creds if a.get("email") != email]
            _creds.append({"email": email, "password": pw})
            with open(ACCOUNTS_JSON, "w", encoding="utf-8") as f:
                json.dump(_creds, f, indent=2)

            # File 2: accounts_tokens.json — email + tokens (dedupe by email)
            _toks = []
            if os.path.exists(ACCOUNTS_TOKENS_JSON):
                try:
                    with open(ACCOUNTS_TOKENS_JSON, encoding="utf-8") as f:
                        _toks = json.load(f)
                    if not isinstance(_toks, list):
                        _toks = []
                except Exception:
                    _toks = []
            _toks = [a for a in _toks if a.get("email") != email]
            _toks.append({
                "email": email,
                "access_token": out["access_token"],
                "refresh_token": out["refresh_token"],
                "id_token": out["id_token"],
                "expires_in": out["expires_in"],
            })
            with open(ACCOUNTS_TOKENS_JSON, "w", encoding="utf-8") as f:
                json.dump(_toks, f, indent=2)

        print(f"    [7] tokens ✅ ({elapsed:.0f}s)")
        push_to_foxrouter([{**{k: out[k] for k in ("email","access_token","refresh_token","id_token")}, "expires_in": 21600}])
        if R9_ENABLED and inject_9router is not False:
            router9.push({**{k: out[k] for k in ("email","access_token","refresh_token","id_token")}, "expires_in": 21600}, R9_DB_PATH)
        return out

    return None


def _run_batch(count, concurrency, manual_emails=None, dashboard=None,
               explicit_email=None, password=None, inject_9router=None):
    """Run one registration batch and return successful accounts."""
    _run_start = time.time()
    print(f"🚀 xAI/Grok Registration — {count} account(s)")
    print(f"📧 Domain: {DOMAIN}")
    print(f"🔑 Password: {'*' * len(password or DEF_PASS) if password or DEF_PASS else '(must provide --password)'}")
    if BOTERDROP_URL:
        print(f"🧩 Solver: Boterdrop-Solver @ {BOTERDROP_URL}")
    else:
        print(f"🧩 Solver: built-in Camoufox (pool={SOLVER_POOL_SIZE})")

    ok = []
    if str(concurrency).lower() == "auto":
        requested = _auto_concurrency()
    else:
        try:
            requested = int(concurrency)
        except (TypeError, ValueError):
            requested = 1
    workers = max(1, min(requested, count))
    if workers > 1 and explicit_email:
        log.warning("--email is ignored when --concurrency > 1 (emails are auto-generated)")

    def _email_for(index):
        return manual_emails[index] if manual_emails else (
            explicit_email if index == 0 and explicit_email else None
        )

    if workers > 1:
        import concurrent.futures
        print(f"⚡ Concurrency: {workers} parallel workers")
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(register, _email_for(i), password,
                                 inject_9router=inject_9router)
                       for i in range(count)]
            for fut in concurrent.futures.as_completed(futures):
                try:
                    r = fut.result()
                except Exception as e:
                    log.error(f"worker error: {e}")
                    r = None
                if r:
                    ok.append(r)
                if dashboard:
                    dashboard.bump(ok=bool(r))
    else:
        consecutive_fails = 0
        for i in range(count):
            r = register(email=_email_for(i), password=password,
                         inject_9router=inject_9router)
            if r:
                ok.append(r)
                consecutive_fails = 0
            if dashboard:
                dashboard.bump(ok=bool(r))
            if i < count - 1:
                delay = _BASE_DELAY if r else min(_BASE_DELAY * (2 ** (consecutive_fails + 1)), _MAX_DELAY)
                if not r:
                    consecutive_fails += 1
                    log.info(f"Backoff: {delay}s after {consecutive_fails} consecutive failure(s)")
                time.sleep(delay)

    bulk = [{"email": r["email"], "access_token": r["access_token"],
             "refresh_token": r["refresh_token"], "id_token": r["id_token"],
             "expires_in": 21600} for r in ok]

    print(f"\n{'='*60}")
    print(f"  {len(ok)}/{count} done")
    print(f"{'='*60}")
    if bulk:
        print(f"\n📁 Akun tersimpan:")
        print(f"   • {os.path.basename(ACCOUNTS_JSON)}        (email + password)")
        print(f"   • {os.path.basename(ACCOUNTS_TOKENS_JSON)} (email + token)")

    elapsed = time.time() - _run_start
    _m, _s = divmod(int(elapsed), 60)
    _h, _m = divmod(_m, 60)
    tstr = (f"{_h}j " if _h else "") + (f"{_m}m " if (_h or _m) else "") + f"{_s}d"
    print(f"\n⏱️  Total waktu: {tstr} ({elapsed:.1f}s)")
    if ok:
        print(f"⚡ Rata-rata: {elapsed/len(ok):.1f}s/akun  ≈ {len(ok)/elapsed*60:.1f} akun/menit")

    return ok


# ─── CLI ──────────────────────────────────────────────────────

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="xAI/Grok Account Registration (All-in-One)")
    p.add_argument("-n", "--count", type=int, default=1, help="Number of accounts")
    p.add_argument("-c", "--concurrency", default="1",
                   help="Parallel workers: a number, or 'auto' to auto-detect from CPU/RAM (default 1)")
    p.add_argument("--email", help="Specific email")
    p.add_argument("--password", help="Specific password")
    p.add_argument("--no-ui", action="store_true",
                   help="Matikan dashboard 2 panel (output biasa)")
    args = p.parse_args()

    if len(sys.argv) == 1:
        from ui import Dashboard
        dashboard = Dashboard(total=args.count)

        def _interactive_batch(count, concurrency, emails):
            return _run_batch(count, concurrency, emails, dashboard,
                              inject_9router=False)

        try:
            run_session(
                _interactive_batch,
                args.count,
                dashboard,
                lambda accounts: router9.push_batch(accounts, R9_DB_PATH),
            )
        finally:
            try:
                from solver import shutdown
                shutdown()
            except Exception:
                pass
        raise SystemExit

    _run_start = time.time()

    dashboard = None
    if not args.no_ui:
        from ui import Dashboard
        dashboard = Dashboard(total=args.count)
        dashboard.begin_batch(args.count)
        dashboard.set_status(
            f"Batch aktif: {args.count} akun | paralel {args.concurrency}"
        )
        dashboard.start(refresh=True)

    _run_batch(args.count, args.concurrency, None, dashboard,
               explicit_email=args.email, password=args.password)

    if dashboard:
        dashboard.stop()

    try:
        from solver import shutdown
        shutdown()
    except Exception:
        pass
    raise SystemExit
