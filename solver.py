#!/usr/bin/env python3
"""
Internal Turnstile Solver — Camoufox-based
===========================================
Self-contained solver, no external server needed.
Uses Camoufox (stealth browser) to solve Cloudflare Turnstile in-process.

Multi-threaded with browser pool for parallel solving.
"""

import time, logging, threading, queue, json, sys

log = logging.getLogger("grok-solver")

# ─── Lazy imports (installed on demand) ───
_camoufox = None

def _ensure_camoufox():
    global _camoufox
    if _camoufox is None:
        try:
            import camoufox
            _camoufox = camoufox
        except ImportError:
            log.error("camoufox not installed. Run: pip install camoufox")
            raise
    return _camoufox

_IS_WINDOWS = sys.platform == "win32"

# ─── Browser args ─────────────────────────────────────────────

_BROWSER_ARGS = {
    "headless": True,
    "humanize": False,
    "args": [
        "--disable-gpu",
        "--disable-extensions",
        "--disable-background-networking",
        "--disable-sync",
        "--disable-translate",
        "--disable-default-apps",
        "--mute-audio",
        "--no-first-run",
        "--disable-features=TranslateUI",
    ] + ([] if _IS_WINDOWS else [
        "--no-sandbox",
        "--disable-dev-shm-usage",
    ]),
}

# ─── Browser Pool ─────────────────────────────────────────────

class BrowserPool:
    """Pool of reusable Camoufox browser instances."""

    def __init__(self, size=2):
        self.size = size
        self._pool = queue.Queue(maxsize=size)
        self._count = 0
        self._lock = threading.Lock()
        self._closed = False

    def _create_browser(self):
        cf = _ensure_camoufox()
        browser = cf.launch(**_BROWSER_ARGS)
        return browser

    def get(self, timeout=10):
        try:
            browser = self._pool.get(timeout=timeout)
            return browser
        except queue.Empty:
            with self._lock:
                if self._count < self.size:
                    self._count += 1
                    return self._create_browser()
            return self._pool.get(timeout=timeout)

    def put(self, browser):
        if self._closed:
            try:
                browser.close()
            except Exception:
                pass
            return
        try:
            self._pool.put_nowait(browser)
        except queue.Full:
            try:
                browser.close()
            except Exception:
                pass

    def close(self):
        self._closed = True
        while not self._pool.empty():
            try:
                browser = self._pool.get_nowait()
                browser.close()
            except Exception:
                pass


# ─── Turnstile Solver ─────────────────────────────────────────

# Default pool — created lazily
_pool = None
_pool_lock = threading.Lock()
_pool_size = 2


def configure(pool_size=2, headless=True):
    """Configure the solver before first use."""
    global _pool_size
    _pool_size = pool_size
    _BROWSER_ARGS["headless"] = headless


def _get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = BrowserPool(size=_pool_size)
    return _pool


TURNSTILE_SCRIPT = """
(async () => {
    // Wait for turnstile to render
    const maxWait = 15000;
    const start = Date.now();
    let token = null;

    while (Date.now() - start < maxWait) {
        // Try to find turnstile iframe and get token
        const iframes = document.querySelectorAll('iframe[src*="challenges.cloudflare.com"]');
        for (const iframe of iframes) {
            try {
                const doc = iframe.contentDocument || iframe.contentWindow.document;
                const inputs = doc.querySelectorAll('input[name="cf-turnstile-response"]');
                for (const input of inputs) {
                    if (input.value && input.value.length > 10) {
                        token = input.value;
                        break;
                    }
                }
            } catch(e) {}
            if (token) break;
        }

        // Also check main document
        if (!token) {
            const inputs = document.querySelectorAll('input[name="cf-turnstile-response"]');
            for (const input of inputs) {
                if (input.value && input.value.length > 10) {
                    token = input.value;
                    break;
                }
            }
        }

        // Check for turnstile callback
        if (!token && window.__cfTurnstileToken) {
            token = window.__cfTurnstileToken;
        }

        if (token) break;
        await new Promise(r => setTimeout(r, 200));
    }

    return token;
})()
"""


def solve_turnstile(url="https://accounts.x.ai/", sitekey="0x4AAAAAAAhr9JGVDZbrZOo0", timeout=30):
    """
    Solve Cloudflare Turnstile using Camoufox browser.
    Returns token string or None on failure.
    """
    t0 = time.time()
    pool = _get_pool()
    browser = None
    page = None

    try:
        browser = pool.get()
        page = browser.new_page()
        page.set_default_timeout(15000)

        # Navigate to target
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        time.sleep(1)

        # Wait for turnstile challenge + solve
        token = None
        deadline = time.time() + timeout

        while time.time() < deadline:
            try:
                result = page.evaluate(TURNSTILE_SCRIPT)
                if result and len(result) > 10:
                    token = result
                    break
            except Exception:
                pass

            # Click the turnstile checkbox if visible
            try:
                page.click("iframe[src*='challenges.cloudflare.com']", timeout=1000)
            except Exception:
                pass

            time.sleep(0.5)

        elapsed = time.time() - t0
        if token:
            log.debug(f"Turnstile solved: {elapsed:.1f}s")
            return token

        log.debug(f"Turnstile timeout after {elapsed:.1f}s")
        return None

    except Exception as e:
        log.debug(f"Turnstile error: {e}")
        return None
    finally:
        if page:
            try:
                page.close()
            except Exception:
                pass
        if browser:
            pool.put(browser)


def shutdown():
    """Close all browser instances."""
    global _pool
    if _pool:
        _pool.close()
        _pool = None


# ─── Direct HTTP-style interface (drop-in replacement for Boterdrop-Solver API) ──

_solve_queue = {}
_solve_counter = 0
_solve_lock = threading.Lock()


def solve_async(url, sitekey):
    """Submit turnstile task async, returns task_id (like Boterdrop-Solver API)."""
    global _solve_counter
    with _solve_lock:
        _solve_counter += 1
        task_id = str(_solve_counter)

    def _worker():
        token = solve_turnstile(url, sitekey)
        with _solve_lock:
            _solve_queue[task_id] = {"status": "success" if token else "error", "value": token}

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return task_id


def get_result(task_id):
    """Poll result by task_id (like Boterdrop-Solver API)."""
    with _solve_lock:
        if task_id in _solve_queue:
            return _solve_queue.pop(task_id)
    return None


def solve_sync(url, sitekey, poll_interval=1, timeout=30):
    """Solve turnstile synchronously — blocks until done."""
    task_id = solve_async(url, sitekey)
    for _ in range(timeout):
        time.sleep(poll_interval)
        result = get_result(task_id)
        if result:
            return result.get("value") if result["status"] == "success" else None
    return None
