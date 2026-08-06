#!/usr/bin/env python3
"""
HTTP-only OAuth device approval (NO browser) — experimental
==========================================================
Bypasses the browser login by REUSING the authenticated session created during
account registration (CreateUserAndSession). If that session is logged in, we
can hit the device verify + approve endpoints directly over HTTP and skip the
Castle-protected browser login entirely.

How the session cookie is established (observed from a real capture):
    createSession returns a `cookieSetterUrl` of the form
        https://auth.grokipedia.com/set-cookie?q=<signed-JWT>
    Fetching it (following redirects) walks a chain
        grokipedia -> grokusercontent -> grok.com -> x.ai
    that plants the session cookie on the *.x.ai domains. The `q` JWT is
    HS256-signed by the server, so it CANNOT be forged locally — the server
    must hand it to us. We therefore look for that URL inside the
    CreateUserAndSession response (which is grpc-web-text = base64/protobuf,
    so we base64-decode before searching).

Flow (mirrors the browser, minus the login form):
    POST auth.x.ai/oauth2/device/verify   (user_code)               -> 303
    POST auth.x.ai/oauth2/device/approve  (user_code, action=allow)  -> 303 -> /device/done

FAIL SAFE: if the session is not authenticated (verify -> /sign-in) or anything
errors, returns False so the caller falls back to the browser approver.
"""
import re
import base64
import logging
import requests

log = logging.getLogger("xai-register")

AUTH_BASE = "https://auth.x.ai"
DEVICE_REFERER = "https://accounts.x.ai/oauth2/device?user_code={code}"

# Precise cookie-setter URL (host/set-cookie?q=<token>) and a looser fallback.
_SETTER_URL_RE = re.compile(r"https://[A-Za-z0-9.\-]+/set-cookie\?q=[A-Za-z0-9._~%\-]+")
_ANY_SETCOOKIE_RE = re.compile(r"https://[A-Za-z0-9._/\-]*set-cookie[A-Za-z0-9._%/=?&+~\-]*")
_ANY_URL_RE = re.compile(r"https://[A-Za-z0-9._/\-]+")


def _loc(resp):
    return resp.headers.get("location") or resp.headers.get("Location") or ""


def _candidate_texts(resp):
    """Return several textual views of a response so we can search for URLs even
    when the body is grpc-web-text (base64) or raw protobuf bytes."""
    texts = []
    try:
        if resp is not None and resp.text:
            texts.append(resp.text)
    except Exception:
        pass
    try:
        raw = getattr(resp, "content", b"") or b""
    except Exception:
        raw = b""
    if raw:
        texts.append(raw.decode("latin-1", "ignore"))
        # grpc-web-text bodies are base64 — try to decode and search inside.
        try:
            compact = re.sub(rb"\s", b"", raw)
            pad = b"=" * (-len(compact) % 4)
            dec = base64.b64decode(compact + pad)
            texts.append(dec.decode("latin-1", "ignore"))
        except Exception:
            pass
    return texts


def _find_setter_url(resp):
    for t in _candidate_texts(resp):
        m = _SETTER_URL_RE.search(t) or _ANY_SETCOOKIE_RE.search(t)
        if m:
            return m.group(0)
    return None


def _write_debug(path, lines):
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            for ln in lines:
                f.write(ln + "\n")
    except Exception:
        pass


def establish_session_cookie(session, create_response, ua=None, debug_path=None):
    """Best-effort: find the server-provided cookie-setter URL in the account
    creation response and fetch it (following redirects) so the session carries
    the auth cookie for *.x.ai. Returns True if a setter URL was fetched.
    """
    url = _find_setter_url(create_response)

    if debug_path:
        urls = set()
        for t in _candidate_texts(create_response):
            urls.update(_ANY_URL_RE.findall(t)[:60])
        dbg = ["=== CreateUserAndSession diagnostics ===",
               f"setter_url_found: {bool(url)}"]
        if url:
            dbg.append(f"setter_url_host: {url.split('?')[0]}")
        dbg.append("https URLs seen in response:")
        dbg += [f"  {u}" for u in sorted(urls)]
        dbg.append("session cookies BEFORE setter:")
        dbg += [f"  {c.name} (domain={c.domain})" for c in session.cookies]
        # reset file at this first write
        try:
            open(debug_path, "w", encoding="utf-8").close()
        except Exception:
            pass
        _write_debug(debug_path, dbg)

    if not url:
        return False

    headers = {"User-Agent": ua} if ua else {}
    try:
        session.get(url, headers=headers, allow_redirects=True, timeout=20)
        log.info("http-approve: fetched cookie-setter chain to establish session")
        _write_debug(debug_path, ["session cookies AFTER setter:"] +
                     [f"  {c.name} (domain={c.domain})" for c in session.cookies])
        return True
    except requests.RequestException as e:
        log.debug(f"http-approve: cookie-setter fetch failed: {e.__class__.__name__}")
        return False


def approve_device_http(session, user_code, user_id="", ua=None, debug_path=None):
    """Approve an OAuth device using an already-authenticated requests.Session.

    Mirrors the exact browser flow observed in a real HAR capture:
        POST auth.x.ai/oauth2/device/verify   (user_code)  -> 303 -> consent page
        GET  accounts.x.ai/oauth2/device/consent?user_code  (renders consent)
        POST auth.x.ai/oauth2/device/approve  (user_code, action=allow, ...) -> 303 -> device/done

    The consent-page GET is REQUIRED: skipping it makes the server treat the
    later approve as invalid, so the token endpoint returns invalid_grant.
    We reproduce it by letting `verify` follow its redirect (allow_redirects=True),
    which lands on the consent page just like the browser.

    Returns True if the device reached the approved/done state, else False
    (caller should fall back to the browser approver).
    """
    # Headers matched to the real browser navigation requests.
    headers = {
        "Origin": "https://accounts.x.ai",
        "Referer": "https://accounts.x.ai/",
        "Content-Type": "application/x-www-form-urlencoded",
        "Cache-Control": "no-cache",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Site": "same-site",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
    }
    if ua:
        headers["User-Agent"] = ua

    _REDIRECTS = (301, 302, 303, 307, 308)

    # 1) verify — follow the 303 through to the consent page (mirrors browser).
    try:
        rv = session.post(
            f"{AUTH_BASE}/oauth2/device/verify",
            data={"user_code": user_code},
            headers=headers,
            allow_redirects=True,
            timeout=20,
        )
    except requests.RequestException as e:
        log.warning(f"http-approve: verify request failed ({e.__class__.__name__})")
        return False

    final_url = (rv.url or "").lower()
    chain = " -> ".join([h.url for h in rv.history] + [rv.url])
    _write_debug(debug_path, ["=== device/verify (+consent GET) ===",
                              f"final_status: {rv.status_code}",
                              f"final_url: {rv.url}",
                              f"redirect_chain: {chain[:400]}"])

    if "sign-in" in final_url or "sign_in" in final_url:
        log.warning("http-approve: session not authenticated (verify → sign-in) — falling back to browser")
        return False

    if "consent" not in final_url:
        log.warning(f"http-approve: verify did not reach consent page (landed on {rv.url!r})")
        # keep going — some flows may still allow approve, but this is a red flag.

    # 2) approve — grant the device (params verified against real browser HAR).
    try:
        ra = session.post(
            f"{AUTH_BASE}/oauth2/device/approve",
            data={
                "user_code": user_code,
                "action": "allow",
                "principal_type": "User",
                "principal_id": user_id or "",
            },
            headers=headers,
            allow_redirects=False,
            timeout=20,
        )
    except requests.RequestException as e:
        log.warning(f"http-approve: approve request failed ({e.__class__.__name__})")
        return False

    loc2 = _loc(ra)
    _write_debug(debug_path, ["=== device/approve ===",
                              f"status: {ra.status_code}",
                              f"location: {loc2[:120]}"])

    ok = ra.status_code in _REDIRECTS and "device/done" in loc2
    if ok:
        log.info("http-approve: device approved via HTTP ✅")
    else:
        log.warning(f"http-approve: approve HTTP {ra.status_code}, location={loc2[:80]!r}")
    return ok
