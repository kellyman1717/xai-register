"""Small interactive prompts shared by register.py and bulk.py."""


def _read(prompt, input_fn=None):
    return (input if input_fn is None else input_fn)(prompt)


def prompt_positive_int(label, default, input_fn=None):
    while True:
        raw = _read(f"{label} [{default}]: ", input_fn).strip()
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            value = 0
        if value > 0:
            return value
        print("  Masukkan angka bulat positif.")


def prompt_concurrency(default="1", input_fn=None):
    while True:
        raw = _read(f"Paralel (angka atau auto) [{default}]: ", input_fn).strip().lower()
        if not raw:
            return default
        if raw == "auto":
            return raw
        try:
            value = int(raw)
        except ValueError:
            value = 0
        if value > 0:
            return str(value)
        print("  Masukkan angka positif atau 'auto'.")


def prompt_email_mode(input_fn=None):
    while True:
        raw = _read("Email [acak/manual] (acak): ", input_fn).strip().lower()
        if raw in ("", "acak", "random", "a", "r"):
            return "random"
        if raw in ("manual", "m"):
            return "manual"
        print("  Pilih 'acak' atau 'manual'.")


def prompt_emails(count, input_fn=None):
    emails = []
    for index in range(count):
        while True:
            email = _read(f"Email {index + 1}/{count}: ", input_fn).strip()
            local, separator, domain = email.partition("@")
            if separator and local and domain and "@" not in domain and " " not in email:
                emails.append(email)
                break
            print("  Format email tidak valid.")
    return emails


def prompt_yes_no(label, default=False, input_fn=None):
    hint = "Y/n" if default else "y/N"
    while True:
        raw = _read(f"{label} [{hint}]: ", input_fn).strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes", "ya", "1"):
            return True
        if raw in ("n", "no", "tidak", "0"):
            return False
        print("  Jawab y/ya atau n/tidak.")


def prompt_run(default_count, input_fn=None):
    count = prompt_positive_int("Jumlah akun", default_count, input_fn)
    concurrency = prompt_concurrency(input_fn=input_fn)
    emails = prompt_emails(count, input_fn) if prompt_email_mode(input_fn) == "manual" else None
    return count, concurrency, emails


def prompt_9router(input_fn=None):
    return prompt_yes_no("Inject hasil ke 9Router sekarang?", default=False, input_fn=input_fn)


def prompt_continue(input_fn=None):
    return prompt_yes_no("Buat batch lagi?", default=False, input_fn=input_fn)


def run_session(run_batch, default_count, dashboard=None, inject_batch=None):
    """Run repeatable interactive batches until the user chooses exit."""
    input_fn = dashboard.prompt if dashboard else None
    batch_number = 0
    if dashboard:
        dashboard.start(refresh=True)
        dashboard.set_status("Menunggu pengaturan batch pertama")
    try:
        while True:
            batch_number += 1
            if dashboard:
                dashboard.set_status(f"Menunggu pengaturan batch {batch_number}")
            count, concurrency, emails = prompt_run(default_count, input_fn)
            if dashboard:
                dashboard.begin_batch(count)
                email_mode = "manual" if emails else "acak"
                dashboard.set_status(
                    f"Batch aktif: {count} akun | paralel {concurrency} | email {email_mode}"
                )
            ok = run_batch(count, concurrency, emails)
            if dashboard:
                dashboard.finish_batch(success=len(ok), failed=count - len(ok))
            if ok and inject_batch and prompt_9router(input_fn):
                try:
                    added = inject_batch(ok)
                    print(f"9Router: {added} akun baru di-inject.")
                except Exception as error:
                    print(f"9Router gagal: {error}")
            if not prompt_continue(input_fn):
                if dashboard:
                    dashboard.set_status("Sesi selesai")
                break
            default_count = count
            if dashboard:
                dashboard.set_status("Menunggu batch berikutnya")
    finally:
        if dashboard:
            dashboard.stop()


if __name__ == "__main__":
    import builtins

    original_input = builtins.input
    try:
        answers = iter(["2", "3", "manual", "one@example.com", "two@example.com", "y", "n"])
        builtins.input = lambda _prompt="": next(answers)
        assert prompt_run(1) == (2, "3", ["one@example.com", "two@example.com"])
        assert prompt_9router() is True
        assert prompt_continue() is False
    finally:
        builtins.input = original_input
    print("self-check OK")
