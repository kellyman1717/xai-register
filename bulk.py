#!/usr/bin/env python3
"""Bulk xAI/Grok registration with optional interactive repeat mode."""

import argparse
import json
import os
import sys
import time

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
CORE_DIR = os.path.join(PROJECT_DIR, "core")
for _path in (PROJECT_DIR, CORE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from register import (
    ACCOUNTS_JSON,
    ACCOUNTS_TOKENS_JSON,
    BOTERDROP_URL,
    FOX_KEY,
    FOX_URL,
    GMAIL_PASSWORD,
    GMAIL_USER,
    R9_ENABLED,
    R9_DB_PATH,
    _auto_concurrency,
    log,
    push_to_foxrouter,
    register,
)
import router9
from _interactive import run_session


_BASE_DELAY = 1.5
_MAX_DELAY = 30


def _run_batch(total, concurrency, manual_emails, dashboard, inject_9router=None):
    batch_start_time = time.time()
    ok = []
    fail = 0
    consecutive_fails = 0
    batch_start = 0

    if str(concurrency).lower() == "auto":
        requested = _auto_concurrency()
    else:
        try:
            requested = int(concurrency)
        except (TypeError, ValueError):
            requested = 1
    workers = max(1, min(requested, total))

    print(f"🚀 Starting bulk registration: {total} accounts (All-in-One)")
    print(f"🧩 Solver: {'Boterdrop-Solver @ ' + BOTERDROP_URL if BOTERDROP_URL else 'built-in Camoufox'}")
    if workers > 1:
        print(f"⚡ Concurrency: {workers} parallel workers")
    print(f"🦊 FoxRouters: auto-push={'ON' if FOX_KEY and FOX_URL else 'OFF'}")
    print(f"🗄️  9Router: auto-inject={'ON' if R9_ENABLED and inject_9router is not False else 'OFF'}")
    print(f"BATCH_START 0/{total}")

    def _maybe_batch_push():
        nonlocal batch_start
        done = len(ok) + fail
        if done % 10 != 0 and done != total:
            return

        batch = [{
            "email": acc["email"],
            "access_token": acc["access_token"],
            "refresh_token": acc["refresh_token"],
            "id_token": acc.get("id_token", ""),
            "expires_in": 21600,
        } for acc in ok[batch_start:]]
        batch_start = len(ok)
        push_count = push_to_foxrouter(batch)
        r9_count = 0
        if R9_ENABLED and inject_9router is not False:
            r9_count = router9.push_batch(batch, R9_DB_PATH)

        print(f"\n{'=' * 60}")
        print(f"BATCH_DONE {done}/{total} — ✅ {len(ok)} ok, ❌ {fail} fail, 🦊 batch_push={push_count} total_ok={len(ok)}")
        if R9_ENABLED and inject_9router is not False:
            print(f"🗄️  9Router: +{r9_count} baru (batch)")
        print(f"{'=' * 60}\n")

    def _one(index):
        email = manual_emails[index] if manual_emails else None
        try:
            return register(email=email, inject_9router=inject_9router)
        except Exception as error:
            print(f"    ❌ exception: {error}", flush=True)
            return None

    if workers > 1:
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            results = executor.map(_one, range(total))
            for result in results:
                if result:
                    ok.append(result)
                else:
                    fail += 1
                if dashboard:
                    dashboard.bump(ok=bool(result))
                _maybe_batch_push()
    else:
        for index in range(total):
            result = _one(index)
            if result:
                ok.append(result)
                consecutive_fails = 0
            else:
                fail += 1
                consecutive_fails += 1

            if dashboard:
                dashboard.bump(ok=bool(result))
            _maybe_batch_push()

            if index < total - 1:
                delay = _BASE_DELAY if result else min(
                    _BASE_DELAY * (2 ** consecutive_fails), _MAX_DELAY
                )
                if not result:
                    log.info(f"Backoff: {delay}s after {consecutive_fails} consecutive failure(s)")
                time.sleep(delay)

    elapsed = time.time() - batch_start_time
    print(f"\n{'=' * 60}")
    print(f"ALL_DONE {len(ok)}/{total} registered, {fail} failed")
    print(f"{'=' * 60}")

    try:
        with open(ACCOUNTS_JSON, encoding="utf-8") as stream:
            saved = len(json.load(stream))
    except Exception:
        saved = len(ok)
    print(f"\n📁 Akun tersimpan: {saved} akun")
    print(f"⏱️  Total waktu: {elapsed:.1f}s")
    return ok


def main():
    parser = argparse.ArgumentParser(description="Bulk xAI/Grok Registration (All-in-One)")
    parser.add_argument("count", type=int, nargs="?", default=10,
                        help="Number of accounts to register")
    parser.add_argument("-c", "--concurrency", default="1",
                        help="Parallel workers: number or auto")
    parser.add_argument("--no-ui", action="store_true",
                        help="Matikan dashboard 2 panel")
    args = parser.parse_args()

    dashboard = None
    if not args.no_ui:
        from ui import Dashboard
        dashboard = Dashboard(total=args.count)

    # Gmail dottrick: kalau gmail.user dikonfigurasi tapi App Password kosong,
    # minta sekali sebelum worker jalan (password hanya di memori, tidak disimpan).
    if GMAIL_USER and not GMAIL_PASSWORD:
        import register as R
        from getpass import getpass
        R.GMAIL_PASSWORD = getpass("App Password Gmail (untuk OTP IMAP): ").strip()
        if not R.GMAIL_PASSWORD:
            print("⚠️  Tanpa App Password, OTP Gmail tidak akan bisa diambil.")
        GMAIL_PASSWORD = R.GMAIL_PASSWORD

    try:
        if len(sys.argv) == 1:
            run_session(
                lambda count, concurrency, emails: _run_batch(
                    count, concurrency, emails, dashboard, inject_9router=False
                ),
                args.count,
                dashboard,
                lambda accounts: router9.push_batch(accounts, R9_DB_PATH),
            )
        else:
            if dashboard:
                dashboard.begin_batch(args.count)
                dashboard.set_status(
                    f"Batch aktif: {args.count} akun | paralel {args.concurrency}"
                )
                dashboard.start(refresh=True)
            _run_batch(args.count, args.concurrency, None, dashboard)
    finally:
        try:
            from solver import shutdown
            shutdown()
        finally:
            if dashboard:
                dashboard.stop()


if __name__ == "__main__":
    main()
