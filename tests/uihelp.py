"""Tiện ích dùng chung cho các test giao diện (tests/ui_*.py)."""
from gi.repository import GLib

RESULTS = []


def check(name, cond, detail=''):
    RESULTS.append((name, bool(cond), detail))


def pump(ms=150):
    loop = GLib.MainLoop()
    GLib.timeout_add(ms, loop.quit)
    loop.run()


def wait_for(cond, timeout=10.0, step=100):
    """Chạy vòng lặp GTK tới khi cond() đúng hoặc hết thời gian. Trả về cond() cuối cùng."""
    import time
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        pump(step)
    return bool(cond())
