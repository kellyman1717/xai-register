#!/usr/bin/env python3
"""
Refresh xAI/Grok OAuth Tokens
==========================
Usage:
  python refresh_tokens.py                      # accounts.json (default)
  python refresh_tokens.py accounts.json
  python refresh_tokens.py tokens/              # all token files
  python refresh_tokens.py tokens/alex.json     # single file
  python refresh_tokens.py --expired-only       # skip still-valid ATs
"""

import requests, json, sys, os, time, base64
from datetime import datetime, timezone

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(BASE, "config.json"), encoding="utf-8") as f:
    CFG = json.load(f)

CLIENT_ID = CFG.get("client_id", "b1a00492-073a-47ea-816f-4c329264a828")
TOKENS_DIR = CFG.get("tokens_dir", os.path.join(BASE, "tokens"))
if not os.path.isabs(TOKENS_DIR):
    TOKENS_DIR = os.path.join(BASE, TOKENS_DIR)
ACCOUNTS_JSON = os.path.join(BASE, "accounts.json")


def apply_tokens(data, d):
    data["access_token"] = d["access_token"]
    if d.get("refresh_token"):
        data["refresh_token"] = d["refresh_token"]
    if d.get("id_token"):
        data["id_token"] = d["id_token"]
    data["expires_in"] = d.get("expires_in", 21600)
    data["refreshed_at"] = datetime.now(timezone.utc).isoformat()
    return data


def at_exp(access_token):
    try:
        p = access_token.split(".")[1]
        p += "=" * (-len(p) % 4)
        return json.loads(base64.urlsafe_b64decode(p))["exp"]
    except Exception:
        return 0


def do_refresh(rt, retries=3):
    last = {}
    for attempt in range(retries):
        r = requests.post(
            "https://auth.x.ai/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": rt,
                "client_id": CLIENT_ID,
            },
            timeout=20,
        )
        try:
            d = r.json()
        except Exception:
            d = {"error": f"http_{r.status_code}", "error_description": r.text[:200]}
        if "access_token" in d:
            return d
        err = d.get("error", "")
        last = d
        if err == "temporarily_unavailable" and attempt < retries - 1:
            time.sleep(2 * (attempt + 1))
            continue
        if err == "invalid_grant":
            return d
        if attempt < retries - 1:
            time.sleep(1)
            continue
    return last


def refresh_one(filepath, expired_only=False):
    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)

    rt = data.get("refresh_token")
    if not rt:
        return "skip", "no refresh_token"

    if expired_only and at_exp(data.get("access_token", "")) > time.time() + 300:
        return "skip", "still valid"

    d = do_refresh(rt)
    if "access_token" in d:
        apply_tokens(data, d)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return "ok", None
    return "fail", d.get("error", "unknown")


def _save_accounts(path, accounts):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(accounts, f, indent=2)
    os.replace(tmp, path)


def refresh_accounts(path, expired_only=False):
    with open(path, encoding="utf-8") as f:
        accounts = json.load(f)

    ok = fail = skip = 0
    total = len(accounts)
    print(f"Refreshing {total} accounts from {path}…", flush=True)
    for i, acc in enumerate(accounts, 1):
        email = acc.get("email", f"#{i}")
        # prefer fresher RT from tokens/
        local = email.split("@")[0]
        tpath = os.path.join(TOKENS_DIR, f"{local}.json")
        td = None
        if os.path.isfile(tpath):
            with open(tpath, encoding="utf-8") as f:
                td = json.load(f)
            if td.get("refresh_token"):
                # use token file RT if present (may have been rotated)
                acc["refresh_token"] = td["refresh_token"]
                if td.get("access_token") and not acc.get("access_token"):
                    acc["access_token"] = td["access_token"]

        rt = acc.get("refresh_token")
        if not rt:
            print(f"  ⏭  [{i}/{total}] {email}: no refresh_token", flush=True)
            skip += 1
            continue

        if expired_only and at_exp(acc.get("access_token") or (td or {}).get("access_token", "")) > time.time() + 300:
            skip += 1
            if i % 100 == 0:
                print(f"  … [{i}/{total}] ok={ok} fail={fail} skip={skip}", flush=True)
            continue

        try:
            d = do_refresh(rt)
            if "access_token" not in d:
                err = d.get("error", "unknown")
                print(f"  ❌ [{i}/{total}] {email}: {err}", flush=True)
                fail += 1
                continue
            apply_tokens(acc, d)
            if td is not None or os.path.isfile(tpath):
                if td is None:
                    td = {}
                apply_tokens(td, d)
                with open(tpath, "w", encoding="utf-8") as f:
                    json.dump(td, f, indent=2)
            ok += 1
            if i % 25 == 0 or i == total:
                print(f"  … [{i}/{total}] ok={ok} fail={fail} skip={skip}", flush=True)
                _save_accounts(path, accounts)
        except Exception as e:
            print(f"  ❌ [{i}/{total}] {email}: {e}", flush=True)
            fail += 1
        time.sleep(0.25)

    _save_accounts(path, accounts)
    print(f"\n✅ {ok} refreshed, ❌ {fail} failed, ⏭ {skip} skipped ({total} total) → {path}", flush=True)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    expired_only = "--expired-only" in flags
    target = args[0] if args else ACCOUNTS_JSON

    if os.path.isfile(target):
        with open(target, encoding="utf-8") as f:
            peek = json.load(f)
        if isinstance(peek, list):
            refresh_accounts(target, expired_only=expired_only)
        else:
            status, detail = refresh_one(target, expired_only=expired_only)
            mark = {"ok": "✅", "fail": "❌", "skip": "⏭"}[status]
            print(f"{mark} {target}" + (f" ({detail})" if detail else ""))
        return

    if os.path.isdir(target):
        files = sorted(f for f in os.listdir(target) if f.endswith(".json"))
        ok = fail = skip = 0
        print(f"Refreshing {len(files)} files in {target}…", flush=True)
        for i, fname in enumerate(files, 1):
            status, detail = refresh_one(os.path.join(target, fname), expired_only=expired_only)
            if status == "ok":
                ok += 1
            elif status == "skip":
                skip += 1
            else:
                fail += 1
                print(f"  ❌ [{i}/{len(files)}] {fname}: {detail}", flush=True)
            if i % 25 == 0:
                print(f"  … [{i}/{len(files)}] ok={ok} fail={fail} skip={skip}", flush=True)
            time.sleep(0.25)
        print(f"\n✅ {ok} refreshed, ❌ {fail} failed, ⏭ {skip} skipped ({len(files)} total)", flush=True)
        return

    print(f"❌ Not found: {target}")


if __name__ == "__main__":
    main()
