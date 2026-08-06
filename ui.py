import sys
import os
import threading
import time
import builtins
import logging

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


class _LogSink:
    """Redirect target that routes every written line into a Dashboard."""

    def __init__(self, dash):
        self._dash = dash

    def write(self, s):
        s = s.rstrip("\n")
        if s:
            self._dash.log(s)

    def flush(self):
        pass


class Dashboard:
    """2-panel live dashboard (left=log, right=stats)."""

    def __init__(self, total, max_logs=400):
        self.total = max(total, 1)
        self._ok = 0
        self._fail = 0
        self._batch_no = 0
        self._session_total = 0
        self._session_ok = 0
        self._session_fail = 0
        self._history = []
        self._status = "Menunggu batch pertama"
        self._prompt_label = ""
        self._prompt_value = ""
        self._prompt_active = False
        self._start = time.time()
        self._max_logs = max_logs
        self._logs = []
        self._lock = threading.Lock()
        self._orig_out = sys.stdout
        self._orig_err = sys.stderr
        self._console = Console(file=sys.stderr, force_terminal=True)
        self._live = None
        self._prev_hook = None
        self._prev_print = None
        self._log_handler = None

    # ── stats / log (thread-safe) ────────────────────────────
    def log(self, line):
        with self._lock:
            self._logs.append(line)
            if len(self._logs) > self._max_logs:
                del self._logs[: len(self._logs) - self._max_logs]

    def bump(self, ok=True):
        with self._lock:
            if ok:
                self._ok += 1
                self._session_ok += 1
            else:
                self._fail += 1
                self._session_fail += 1
            self._sync_history_locked()
            if self._history and self._ok + self._fail >= self.total:
                self._history[-1]["state"] = "selesai"
                self._status = (
                    f"Batch {self._batch_no} selesai "
                    f"({self._ok + self._fail}/{self.total})"
                )

    def _normalise_total(self, total):
        try:
            return max(int(total), 1)
        except (TypeError, ValueError):
            return 1

    def begin_batch(self, total):
        """Start a batch while keeping the session tracking history."""
        batch_total = self._normalise_total(total)
        with self._lock:
            if self._history and self._history[-1]["state"] == "berjalan":
                self._history[-1]["state"] = "terhenti"
                self._sync_history_locked()

            self._batch_no += 1
            self.total = batch_total
            self._ok = 0
            self._fail = 0
            self._logs.clear()
            self._prompt_label = ""
            self._prompt_value = ""
            self._prompt_active = False
            self._start = time.time()
            self._session_total += batch_total
            self._history.append({
                "number": self._batch_no,
                "total": batch_total,
                "ok": 0,
                "fail": 0,
                "done": 0,
                "state": "berjalan",
            })
            self._status = f"Batch {self._batch_no} berjalan"

    def reset(self, total):
        """Backward-compatible alias used by the interactive session."""
        self.begin_batch(total)

    def _sync_history_locked(self):
        if not self._history:
            return
        current = self._history[-1]
        current["ok"] = self._ok
        current["fail"] = self._fail
        current["done"] = self._ok + self._fail

    def finish_batch(self, success=None, failed=None):
        """Mark the active batch complete and keep its result in Tracking."""
        with self._lock:
            if not self._history:
                return

            if success is not None:
                success = max(int(success), 0)
                self._session_ok += success - self._ok
                self._ok = success
            if failed is not None:
                failed = max(int(failed), 0)
                self._session_fail += failed - self._fail
                self._fail = failed

            self._sync_history_locked()
            self._history[-1]["state"] = "selesai"
            done = self._ok + self._fail
            self._status = f"Batch {self._batch_no} selesai ({done}/{self.total})"

    def set_status(self, status):
        """Show a short lifecycle/input status in the Tracking panel."""
        with self._lock:
            self._status = str(status)

    def _set_prompt_value(self, value):
        with self._lock:
            self._prompt_value = value

    def _read_embedded_input(self):
        """Read a line without echoing outside the Rich Live layout."""
        value = ""

        def handle_key(key):
            nonlocal value
            if key in ("\r", "\n"):
                return True
            if key in ("\x03",):
                raise KeyboardInterrupt
            if key in ("\x04",):
                raise EOFError
            if key in ("\b", "\x7f"):
                value = value[:-1]
            elif key.isprintable():
                value += key
            self._set_prompt_value(value)
            return False

        if os.name == "nt":
            import msvcrt

            while True:
                key = msvcrt.getwch()
                if key in ("\x00", "\xe0"):
                    # Consume the second code unit for arrows/function keys.
                    msvcrt.getwch()
                    continue
                if handle_key(key):
                    return value

        import termios
        import tty

        fd = sys.stdin.fileno()
        previous = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            while True:
                key = sys.stdin.read(1)
                if handle_key(key):
                    return value
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, previous)

    def prompt(self, text):
        """Read input inside Tracking when the terminal supports raw keys."""
        label = " ".join(str(text).strip().split())
        with self._lock:
            self._prompt_label = label
            self._prompt_value = ""
            self._prompt_active = True
            self._status = f"Menunggu input: {label}"

        was_live = self._live is not None
        stdin_is_tty = getattr(sys.stdin, "isatty", lambda: False)()
        if was_live and stdin_is_tty:
            try:
                answer = self._read_embedded_input()
            except (KeyboardInterrupt, EOFError):
                with self._lock:
                    self._prompt_active = False
                    self._status = f"Input dibatalkan: {label}"
                raise
            with self._lock:
                self._prompt_value = answer
                self._prompt_active = False
                self._status = f"Input selesai: {label}"
            return answer

        # Piped/non-TTY execution cannot receive raw key events. Keep the
        # original behavior there, while avoiding a duplicated prompt when
        # the dashboard had already rendered it before pausing.
        if was_live:
            self.stop()
        try:
            answer = input(text if not was_live else "")
        except (KeyboardInterrupt, EOFError):
            with self._lock:
                self._prompt_active = False
                self._status = f"Input dibatalkan: {label}"
            raise
        finally:
            if was_live:
                self.start(refresh=True)
        with self._lock:
            self._prompt_value = answer
            self._prompt_active = False
            self._status = f"Input selesai: {label}"
        return answer

    # ── lifecycle ────────────────────────────────────────────
    def start(self, refresh=False):
        # Route stdout AND stderr into the dashboard so every print/log
        if self._live:
            self.stop()
            self._live = None

        # (register, solver, approve) shows up on the left panel.
        self._orig_out = sys.stdout
        self._orig_err = sys.stderr
        self._live = Live(get_renderable=self._render, console=self._console,
                          refresh_per_second=6, screen=False)
        self._live.start(refresh=refresh)
        # Ensure streams & UI are restored even on an unhandled error / Ctrl+C.
        self._prev_hook = sys.excepthook
        sys.excepthook = self._crash_hook

        # Monkey-patch print so every print() is captured into the log buffer.
        self._prev_print = builtins.print
        builtins.print = self._print

        # Also route the stdlib logging into the dashboard log buffer.
        self._log_handler = logging.StreamHandler(_LogSink(self))
        self._log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        root = logging.getLogger()
        # Remove existing stream handlers that write to the original stdout/stderr
        # so logs don't duplicate to the terminal outside the dashboard.
        for h in list(root.handlers):
            if isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) in (self._orig_out, self._orig_err):
                root.removeHandler(h)
        if self._log_handler not in root.handlers:
            root.addHandler(self._log_handler)

    def _print(self, *args, **kwargs):
        sep = kwargs.get("sep", " ")
        end = kwargs.get("end", "\n")
        s = sep.join(str(a) for a in args)
        if end:
            s += end.rstrip("\n")
        self.log(s)
        if kwargs.get("flush", False):
            pass

    def _crash_hook(self, etype, evalue, tb):
        # Restore terminal before printing the traceback so it is not swallowed.
        try:
            if self._live:
                self._live.stop()
        except Exception:
            pass
        if self._prev_print is not None:
            builtins.print = self._prev_print
            self._prev_print = None
        if self._log_handler is not None:
            try:
                logging.getLogger().removeHandler(self._log_handler)
            except Exception:
                pass
            self._log_handler = None
        sys.stdout = self._orig_out
        sys.stderr = self._orig_err
        if self._prev_hook:
            sys.excepthook = self._prev_hook
            self._prev_hook(etype, evalue, tb)
        else:
            import traceback
            traceback.print_exception(etype, evalue, tb)

    def stop(self):
        live, self._live = self._live, None
        if live:
            live.stop()
        if self._prev_print is not None:
            builtins.print = self._prev_print
            self._prev_print = None
        if self._log_handler is not None:
            try:
                logging.getLogger().removeHandler(self._log_handler)
            except Exception:
                pass
            self._log_handler = None
        # restore original streams
        sys.stdout = self._orig_out
        sys.stderr = self._orig_err
        if self._prev_hook:
            sys.excepthook = self._prev_hook
            self._prev_hook = None

    # ── rendering ────────────────────────────────────────────
    def _render(self):
        with self._lock:
            logs = list(self._logs[-40:])
            ok, fail = self._ok, self._fail
            batch_no = self._batch_no
            status = self._status
            session_total = self._session_total
            session_ok = self._session_ok
            session_fail = self._session_fail
            history = [dict(item) for item in self._history[-5:]]
            prompt_label = self._prompt_label
            prompt_value = self._prompt_value
            prompt_active = self._prompt_active
        elapsed = time.time() - self._start
        done = ok + fail
        total = self.total

        body = "".join(ln + "\n" for ln in logs) or "(menunggu log...)\n"
        log_panel = Panel(Text(body, style="default", no_wrap=False),
                          title="📜 Log", border_style="cyan")

        t = Table(show_header=False, box=None, padding=(0, 2))
        t.add_row("Status", status)
        if prompt_label:
            t.add_row("Input", Text(prompt_label))
            cursor = "█" if prompt_active else ""
            t.add_row("", Text(f"> {prompt_value}{cursor}"))
        t.add_row("Batch", f"#{batch_no}" if batch_no else "belum dimulai")
        t.add_row("🎯 Total akun", str(total))
        t.add_row("✅ Sukses", str(ok))
        t.add_row("❌ Gagal", str(fail))
        t.add_row("📈 Selesai", f"{done}/{total}")
        t.add_row("───", "───")
        t.add_row("⏱ Elapsed", f"{elapsed:.1f}s")
        if elapsed > 0 and (ok or fail):
            t.add_row("🚀 Rate", f"{done / elapsed * 60:.1f} akun/menit")
        pct = done / total * 100
        t.add_row("📊 Progress", f"{pct:.0f}%")
        bar_len = 20
        filled = int(bar_len * min(pct, 100) / 100)
        t.add_row("", "█" * filled + "░" * (bar_len - filled) + f" {pct:.0f}%")
        if session_total:
            session_done = session_ok + session_fail
            t.add_row("Total sesi", str(session_total))
            t.add_row("Selesai sesi", f"{session_done}/{session_total}")
        if history:
            t.add_row("Riwayat", "")
            for item in history:
                result = f"{item['done']}/{item['total']} ok={item['ok']} fail={item['fail']}"
                t.add_row(f"  #{item['number']}", result)
        stats_panel = Panel(t, title="\U0001F4CA Tracking", border_style="green")

        layout = Layout()
        layout.split_row(
            Layout(log_panel, name="log", ratio=3),
            Layout(stats_panel, name="stats", ratio=2),
        )
        return layout
