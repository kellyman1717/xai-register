#!/usr/bin/env python3
"""
HTTP login (createSession) — tanpa browser untuk bagian HTTP-nya
===============================================================
Berdasarkan HAR asli: login xAI = POST https://accounts.x.ai/api/rpc
dengan JSON { rpc: "createSession", ... , turnstileToken, castleRequestToken }.

Hasil uji nyata:
  - castleRequestToken TIDAK wajib (boleh kosong). Cukup turnstileToken.
  - Respons createSession memuat "cookieSetterUrl":
        https://auth.grokipedia.com/set-cookie?q=<JWT>
    Cookie sesi ASLI baru terpasang setelah URL ini di-GET (mengikuti redirect
    lintas domain sampai *.x.ai). Tanpa itu, hanya ada cookie __cf_bm /
    last-logged-in-with yang BUKAN penanda login.
"""
import json
import logging
import re

import requests

log = logging.getLogger("xai-register")

RPC_URL = "https://accounts.x.ai/api/rpc"

# Cookie yang BUKAN penanda login.
_NON_AUTH_COOKIES = {
    "__cf_bm", "cf_clearance", "__cflb", "_cfuvid",
    "last-logged-in-with", "cf_chl_rc_m",
}

_SETTER_URL_RE = re.compile(r"https://[A-Za-z0-9.\-]+/set-cookie\?q=[A-Za-z0-9._~%\-]+")


def has_auth_cookie(session):
    for c in session.cookies:
        if c.name not in _NON_AUTH_COOKIES:
            return True
    return False


def has_xai_cookie(session):
    """True when the session actually carries an x.ai auth cookie (not just the
    SSO cookies on grokiperdia/grok domains). This is what device-approval needs."""
    for c in session.cookies:
        d = (c.domain or "").lower()
        if d == "accounts.x.ai" or d == ".x.ai" or d.endswith(".x.ai"):
            if c.name not in _NON_AUTH_COOKIES:
                return True
    return False


def auth_cookie_names(session):
    return sorted(f"{c.name}@{c.domain}" for c in session.cookies)


def _extract_setter_url(resp):
    """Ambil cookieSetterUrl dari respons createSession (JSON dulu, lalu regex)."""
    if resp is None:
        return None
    try:
        data = resp.json()
        url = data.get("cookieSetterUrl") or data.get("cookie_setter_url")
        if url:
            return url
        # kadang bersarang
        for v in data.values():
            if isinstance(v, dict):
                u = v.get("cookieSetterUrl") or v.get("cookie_setter_url")
                if u:
                    return u
    except Exception:
        pass
    try:
        m = _SETTER_URL_RE.search(resp.text or "")
        if m:
            return m.group(0)
    except Exception:
        pass
    return None


def establish_from_setter(session, setter_url, ua=None, debug_path=None):
    """GET cookieSetterUrl (ikut redirect) supaya cookie sesi *.x.ai terpasang.

    The browser follows the chain all the way to auth.x.ai/set-cookie, which is
    what finally plants the x.ai session cookie. We mirror the browser's headers
    (Referer + Sec-Fetch) because Cloudflare WAF returns 403 without them.
    """
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://accounts.x.ai/",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "cross-site",
        "Upgrade-Insecure-Requests": "1",
    }
    if ua:
        headers["User-Agent"] = ua
    try:
        r = session.get(setter_url, headers=headers, allow_redirects=True, timeout=25)
        chain = " -> ".join(
            [resp.url.split("/")[2] for resp in r.history] + [r.url.split("/")[2]]
        )
        log.info(f"http-login: cookie-setter chain: {chain} (final={r.status_code})")
        if debug_path:
            with open(debug_path, "a", encoding="utf-8") as f:
                f.write(f"setter_final_status: {r.status_code}\n")
                f.write(f"setter_redirect_chain: {chain}\n")
                f.write(f"xai_cookie_present: {has_xai_cookie(session)}\n")
        return r.status_code == 200 or has_xai_cookie(session)
    except requests.RequestException as e:
        log.warning(f"http-login: setter fetch failed: {e.__class__.__name__}")
        return False


def create_session(session, email, password, turnstile_token,
                   castle_token="", ua=None, user_code=None, debug_path=None):
    """Login via createSession + pasang cookie sesi via cookieSetterUrl.

    Return (ok: bool, response|None). ok=True bila sesi kini benar-benar
    membawa cookie login (bukan sekadar __cf_bm / last-logged-in-with).
    """
    ref = "https://accounts.x.ai/sign-in?redirect=oauth2-provider"
    if user_code:
        ref += f"&return_to=/oauth2/device?user_code={user_code}&email=true"
    headers = {
        "Accept": "*/*",
        "Content-Type": "application/json",
        "Origin": "https://accounts.x.ai",
        "Referer": ref,
    }
    if ua:
        headers["User-Agent"] = ua

    body = {
        "rpc": "createSession",
        "req": {
            "createSessionRequest": {
                "credentials": {
                    "case": "emailAndPassword",
                    "value": {"email": email, "clearTextPassword": password},
                }
            },
            "turnstileToken": turnstile_token or "",
            "castleRequestToken": castle_token or "",
        },
    }

    try:
        r = session.post(RPC_URL, json=body, headers=headers, timeout=30)
    except requests.RequestException as e:
        log.warning(f"createSession request failed: {e.__class__.__name__}")
        return False, None

    setter_url = _extract_setter_url(r)

    if debug_path:
        try:
            with open(debug_path, "a", encoding="utf-8") as f:
                f.write("=== createSession ===\n")
                f.write(f"http_status: {r.status_code}\n")
                f.write(f"castle_sent: {'yes' if castle_token else 'no'}\n")
                f.write(f"setter_url_found: {bool(setter_url)}\n")
                if setter_url:
                    f.write(f"setter_host: {setter_url.split('?')[0]}\n")
                f.write(f"cookies_before_setter: {auth_cookie_names(session)}\n")
                f.write(f"resp_snippet: {(r.text or '')[:600]}\n")
        except Exception:
            pass

    # Login HTTP 200 tapi cookie sesi belum ada -> ikuti cookieSetterUrl.
    if r.status_code == 200 and setter_url:
        establish_from_setter(session, setter_url, ua=ua, debug_path=debug_path)

    ok = has_xai_cookie(session)

    if debug_path:
        try:
            with open(debug_path, "a", encoding="utf-8") as f:
                f.write(f"cookies_after_setter: {auth_cookie_names(session)}\n")
                f.write(f"auth_cookie_present: {ok}\n")
        except Exception:
            pass

    return ok, r
