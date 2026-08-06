#!/usr/bin/env python3
"""Auto-inject grok tokens into the 9Router SQLite DB (optional).

Enabled only when config has:
  "router9": {"enabled": true, "db_path": "~/.9router/db/data.sqlite"}

Exposes:
  - push(account_dict)   -> bool   : push one account (email + tokens)
  - push_batch(accounts) -> int    : push a list of accounts
  - inject_token(file)   -> bool   : push one token file from tokens/
  - inject_all()         -> int    : push all token files in tokens/
  - main()                          : CLI (inject_token <file> | inject_all)

The SQLite layout mirrors what inject_9router.py already writes, so the two
approaches are interchangeable. Duplicate emails (provider='grok-cli') are
skipped.
"""

import json, os, sqlite3, sys, uuid
from datetime import datetime, timedelta, timezone

# Ensure emoji / Unicode output works on Windows consoles (cp1252/cp437).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

DEFAULT_DB_PATH = os.path.expanduser("~/.9router/db/data.sqlite")
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKENS_DIR = os.path.join(PROJECT_DIR, "tokens")
SCOPE = "openid profile email offline_access grok-cli:access api:access conversations:read conversations:write"


def _load_config():
    path = os.path.join(PROJECT_DIR, "config.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _email_from_token(token):
    """Best-effort email: from 'email' key, then id_token claims, then filename."""
    email = token.get("email") or ""
    if "@" not in email and token.get("id_token"):
        try:
            payload = token["id_token"].split(".")[1] + "==="
            claims = json.loads(__import__("base64").urlsafe_b64decode(payload))
            email = claims.get("email", "")
        except Exception:
            pass
    return email


def _insert_connection(cur, email, token):
    conn_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=token.get("expires_in", 21600))
    ).isoformat().replace("+00:00", "Z")

    data = {
        "displayName": email.split("@")[0].replace(".", " ").replace("_", " ").title(),
        "accessToken": token["access_token"],
        "refreshToken": token.get("refresh_token", ""),
        "expiresAt": expires_at,
        "scope": token.get("scope", SCOPE),
        "testStatus": "active",
        "expiresIn": token.get("expires_in", 21600),
        "providerSpecificData": {
            "authMethod": "device_code",
            "idToken": token.get("id_token", ""),
            "email": email,
            "userId": "",
            "hasGrokCodeAccess": True,
            "subscriptionTier": None,
        },
    }

    cur.execute(
        """INSERT INTO providerConnections
           (id, provider, authType, name, email, priority, isActive, data, createdAt, updatedAt)
           VALUES (?, 'grok-cli', 'oauth', ?, ?, 5, 1, ?, ?, ?)""",
        (conn_id, email, email, json.dumps(data), now, now),
    )
    return conn_id


def push(account, db_path=DEFAULT_DB_PATH):
    """Push one account dict {email, access_token, refresh_token, id_token, expires_in}.

    Returns True if inserted, False if skipped/duplicate/failed.
    """
    email = account.get("email") or _email_from_token(account)
    if "@" not in email or not account.get("access_token"):
        print(f"  ❌ 9Router: skip (email/token tidak lengkap) — {account.get('email', '?')}")
        return False

    try:
        conn = sqlite3.connect(db_path, timeout=15)
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM providerConnections WHERE email=? AND provider='grok-cli'",
            (email,),
        )
        if cur.fetchone():
            print(f"  SKIP {email} — sudah ada di DB 9Router")
            conn.close()
            return False

        _insert_connection(cur, email, account)
        conn.commit()
        conn.close()
        print(f"  ✅ 9Router: {email} → ter-inject")
        return True
    except Exception as e:
        print(f"  ❌ 9Router: {email} — {e}")
        return False


def push_batch(accounts, db_path=DEFAULT_DB_PATH):
    """Push a list of account dicts. Returns number of new inserts."""
    if not accounts:
        return 0
    ok = 0
    for acc in accounts:
        if push(acc, db_path):
            ok += 1
    return ok


def inject_token(token_file, db_path=DEFAULT_DB_PATH):
    """Push one token JSON file (same format as tokens/*.json)."""
    with open(token_file, encoding="utf-8") as f:
        tok = json.load(f)
    email = tok.get("email") or _email_from_token(tok) or os.path.basename(token_file).replace(".json", "")
    return push({"email": email, **tok}, db_path)


def inject_all(tokens_dir=TOKENS_DIR, db_path=DEFAULT_DB_PATH):
    files = sorted(f for f in os.listdir(tokens_dir) if f.endswith(".json"))
    if not files:
        print(f"Tidak ada token files di {tokens_dir}")
        return 0
    print(f"Inject {len(files)} token dari {tokens_dir} ke 9Router…")
    ok = sum(1 for fname in files if inject_token(os.path.join(tokens_dir, fname), db_path))
    print(f"Selesai: {ok} di-inject, {len(files) - ok} skip")
    return ok


def enabled(db_path=None):
    """Whether 9Router auto-push is on: router9.enabled true + db path exists."""
    cfg = _load_config().get("router9", {})
    if not cfg.get("enabled"):
        return False
    if not db_path:
        db_path = os.path.expanduser(cfg.get("db_path", DEFAULT_DB_PATH))
    return os.path.exists(db_path)


def get_db_path():
    cfg = _load_config().get("router9", {})
    return os.path.expanduser(cfg.get("db_path", DEFAULT_DB_PATH))


def main():
    db_path = get_db_path()
    if len(sys.argv) > 1:
        inject_token(sys.argv[1], db_path)
    else:
        inject_all(db_path=db_path)


if __name__ == "__main__":
    main()
