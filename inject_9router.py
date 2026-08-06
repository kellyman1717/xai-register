#!/usr/bin/env python3
"""Inject xAI/Grok tokens directly into 9Router SQLite DB."""

import sqlite3, json, sys, os, uuid
from datetime import datetime, timezone, timedelta

# Ensure emoji / Unicode output works on Windows consoles (cp1252/cp437).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

DB_PATH = os.path.expanduser("~/.9router/db/data.sqlite")
TOKENS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tokens")
SCOPE = "openid profile email offline_access grok-cli:access api:access conversations:read conversations:write"

def inject_token(token_file, db_path=DB_PATH):
    with open(token_file, encoding="utf-8") as f:
        tok = json.load(f)

    email = tok.get("email") or os.path.basename(token_file).replace(".json", "")
    if "@" not in email:
        # Try to extract from id_token or access_token
        import base64
        try:
            payload = tok["id_token"].split(".")[1] + "==="
            claims = json.loads(base64.urlsafe_b64decode(payload))
            email = claims.get("email", email)
        except Exception:
            pass

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Check if already exists
    cur.execute("SELECT id FROM providerConnections WHERE email=? AND provider='grok-cli'", (email,))
    existing = cur.fetchone()
    if existing:
        print(f"  SKIP {email} — already in DB (id={existing[0][:8]}..)")
        conn.close()
        return False

    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=tok.get("expires_in", 21600))).isoformat().replace("+00:00", "Z")

    data = {
        "displayName": email.split("@")[0].replace(".", " ").replace("_", " ").title(),
        "accessToken": tok["access_token"],
        "refreshToken": tok.get("refresh_token", ""),
        "expiresAt": expires_at,
        "scope": tok.get("scope", SCOPE),
        "testStatus": "active",
        "expiresIn": tok.get("expires_in", 21600),
        "providerSpecificData": {
            "authMethod": "device_code",
            "idToken": tok.get("id_token", ""),
            "email": email,
            "userId": "",
            "hasGrokCodeAccess": True,
            "subscriptionTier": None,
        },
    }

    conn_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    cur.execute(
        """INSERT INTO providerConnections
           (id, provider, authType, name, email, priority, isActive, data, createdAt, updatedAt)
           VALUES (?, 'grok-cli', 'oauth', ?, ?, 5, 1, ?, ?, ?)""",
        (conn_id, email, email, json.dumps(data), now, now),
    )
    conn.commit()
    conn.close()
    print(f"  ✅ {email} → id={conn_id[:8]}..")
    return True


def inject_all(tokens_dir=TOKENS_DIR):
    files = sorted(f for f in os.listdir(tokens_dir) if f.endswith(".json"))
    if not files:
        print(f"No token files in {tokens_dir}")
        return

    print(f"Injecting {len(files)} tokens from {tokens_dir}")
    ok, skip = 0, 0
    for fname in files:
        result = inject_token(os.path.join(tokens_dir, fname))
        if result:
            ok += 1
        else:
            skip += 1
    print(f"\nDone: {ok} injected, {skip} skipped")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        inject_token(sys.argv[1])
    else:
        inject_all()
