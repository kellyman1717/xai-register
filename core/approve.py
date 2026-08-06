#!/usr/bin/env python3
"""
CloakBrowser OAuth Device Approval (v7 OPTIMIZED)
==================================================
Faster browser automation with reduced sleeps and timeouts.

Optimizations:
  - Reduced sleep delays (0.5-1s instead of 2-3s)
  - Shorter timeouts
  - Smarter polling for login state
  - Minimal browser args for faster launch
"""

import time, re, json, logging, sys
import cloakbrowser

log = logging.getLogger("xai-register")

# Cross-platform Chromium args: Linux & Windows
_IS_WINDOWS = sys.platform == "win32"

_LINUX_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-ipc-flooding-protection",
]

_COMMON_ARGS = [
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-sync",
    "--disable-translate",
    "--disable-default-apps",
    "--mute-audio",
    "--no-first-run",
    "--disable-features=TranslateUI",
]

CHROMIUM_ARGS = _COMMON_ARGS if _IS_WINDOWS else _LINUX_ARGS + _COMMON_ARGS


def _dismiss_cookies(page):
    for label in ("Accept All Cookies", "Accept All", "Reject All"):
        try:
            page.click(f"button:has-text('{label}')", timeout=1000)
            time.sleep(0.2)
            return
        except Exception:
            pass


def setup_turnstile_init(page, ts_token):
    mock = f"""(function(){{
        var T={json.dumps(ts_token)};
        window._turnstile_mock = {{
            render: function(c, p) {{
                var id = 'w' + Math.random();
                setTimeout(function() {{
                    if (p && p.callback) p.callback(T);
                    var i = document.querySelector('input[name="cf-turnstile-response"]');
                    if (i) {{
                        var s = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                        s.call(i, T);
                        i.dispatchEvent(new Event('input', {{bubbles: true}}));
                        i.dispatchEvent(new Event('change', {{bubbles: true}}));
                    }}
                }}, 30);
                return id;
            }},
            getResponse: function() {{ return T; }},
            reset: function() {{}},
            remove: function() {{}},
            execute: function() {{}},
            isExpired: function() {{ return false; }}
        }};
        Object.defineProperty(window, 'turnstile', {{
            get: function() {{ return window._turnstile_mock; }},
            set: function(v) {{}},
            configurable: true
        }});
    }})();"""
    page.add_init_script(mock)


def _inject_turnstile(page, token):
    page.evaluate("""(token) => {
        window.turnstile = {
            render: function(c, p) {
                var id = 'w' + Math.random();
                setTimeout(function() {
                    if (p && p.callback) p.callback(token);
                    var i = document.querySelector('input[name="cf-turnstile-response"]');
                    if (i) {
                        var s = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                        s.call(i, token);
                        i.dispatchEvent(new Event('input', {bubbles: true}));
                        i.dispatchEvent(new Event('change', {bubbles: true}));
                    }
                }, 30);
                return id;
            },
            getResponse: function() { return token; },
            reset: function() {},
            remove: function() {},
            execute: function() {},
            isExpired: function() { return false; }
        };
        var inputs = document.querySelectorAll('input[name="cf-turnstile-response"]');
        inputs.forEach(i => {
            var setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
            setter.call(i, token);
            i.dispatchEvent(new Event('input', {bubbles: true}));
            i.dispatchEvent(new Event('change', {bubbles: true}));
        });
    }""", token)


def approve_device(user_code, email, password, ts_token, proxy=None):
    browser = None
    t0 = time.time()
    try:
        launch_kwargs = dict(headless=True, args=CHROMIUM_ARGS, humanize=False)
        if proxy:
            launch_kwargs["proxy"] = proxy

        browser = cloakbrowser.launch(**launch_kwargs)
        page = browser.new_page()
        page.set_default_timeout(8000)  # reduced from 10000

        setup_turnstile_init(page, ts_token)

        # Step 1: Device page (reduced timeout)
        page.goto(
            f"https://accounts.x.ai/oauth2/device?user_code={user_code}",
            wait_until="domcontentloaded",
            timeout=20000,
        )
        time.sleep(1)  # reduced from 2s
        _dismiss_cookies(page)

        # Step 2: Continue (device page pre-login)
        for label in ("Continue", "Tiếp tục", "Lanjutkan"):
            try:
                page.click(f"button:has-text('{label}')", timeout=2000)
                time.sleep(1.5)  # reduced from 3s
                break
            except Exception:
                pass
        _dismiss_cookies(page)

        # Step 3: Login with email
        try:
            page.click("button:has-text('Login with email')", timeout=5000)
            time.sleep(1)  # reduced from 2s
        except Exception as e:
            log.warning(f"email btn: {e}")

        # Step 4: Fill email → Next
        try:
            page.fill("input[type=email]", email, timeout=4000)
        except Exception:
            try:
                page.locator("input[name=email]").fill(email)
            except Exception as e:
                log.warning(f"email: {e}")
        time.sleep(0.2)
        try:
            page.click("button:has-text('Next')", timeout=4000)
        except Exception:
            page.press("input[type=email]", "Enter")
        try:
            page.wait_for_selector("input[type=password]", state="visible", timeout=8000)
        except Exception:
            time.sleep(1.5)

        # Step 5: Fill password + turnstile
        try:
            page.fill("input[type=password]", password, timeout=5000)
            time.sleep(0.2)
        except Exception as e:
            log.warning(f"password: {e}")
        _inject_turnstile(page, ts_token)

        # Step 6: Click Login
        try:
            page.click("button:has-text('Login')", timeout=4000)
        except Exception:
            try:
                page.click("button[type=submit]", timeout=2000)
            except Exception:
                pass

        # Step 7: Wait for post-login state — faster polling (1s instead of 2s)
        logged_in = False
        for i in range(15):
            time.sleep(1)  # faster polling
            try:
                signout = page.locator("button:has-text('Sign out'), a:has-text('Sign out')")
                if signout.count() > 0:
                    logged_in = True
                    log.info(f"approve: logged in ({time.time()-t0:.0f}s)")
                    break
            except:
                pass
            if "/sign-in" not in page.url:
                logged_in = True
                log.info(f"approve: navigated → {page.url} ({time.time()-t0:.0f}s)")
                break
            if i == 3:
                _inject_turnstile(page, ts_token)
                try:
                    page.click("button:has-text('Login')", timeout=2000)
                except: pass

        if not logged_in:
            log.warning(f"approve: login failed ({time.time()-t0:.0f}s)")
            return False

        # Step 8: Click Continue/Allow on consent page — faster loop
        for attempt in range(10):
            url = page.url
            if "device/done" in url:
                log.info(f"approve: DONE ({time.time()-t0:.0f}s)")
                return True

            clicked = False
            for label in ("Continue", "Allow", "Izinkan"):
                try:
                    btn = page.locator(f"button:has-text('{label}')").first
                    if btn.is_visible():
                        btn.click(timeout=1500)
                        clicked = True
                        log.info(f"approve: clicked '{label}' ({time.time()-t0:.0f}s)")
                        time.sleep(1.5)  # reduced from 3s
                        break
                except Exception:
                    pass

            if not clicked:
                time.sleep(1)  # reduced from 2s

            url = page.url
            if "device/done" in url:
                log.info(f"approve: DONE ({time.time()-t0:.0f}s)")
                return True

        # Final check
        url = page.url
        if "device/done" in url:
            return True
        if "/sign-in" not in url and "error" not in url:
            log.info(f"approve: likely success → {url} ({time.time()-t0:.0f}s)")
            return True

        log.warning(f"approve: final: {url} ({time.time()-t0:.0f}s)")
        return False

    except Exception as e:
        log.error(f"approve: error: {e} ({time.time()-t0:.0f}s)")
        return False
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass
