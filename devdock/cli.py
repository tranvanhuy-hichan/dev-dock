"""Lệnh dòng lệnh không cần giao diện: `devdock --add-services [--all] [--dry-run]`."""
import os
import sys

from .lib import discover as DC
from .lib import projects as P
from .store import Store


def other_devdock_running():
    """PID của một DevDock khác đang chạy (nếu có), để không ghi đè cài đặt của nó."""
    try:
        import psutil
    except ImportError:
        return None
    me = os.getpid()
    for p in psutil.process_iter(['pid', 'cmdline']):
        try:
            cmd = p.info['cmdline'] or []
            if p.info['pid'] != me and 'devdock' in cmd and '-m' in cmd and '--add-services' not in cmd:
                return p.info['pid']
        except (psutil.Error, TypeError):
            continue
    return None


def add_services(store, only_ready=True, dry_run=False, out=print):
    """Quét project và thêm dev server chưa có vào store. Trả về số mục đã thêm."""
    roots = store['project_roots'] or P.default_roots()
    cands = DC.discover_roots(roots)
    new = [c for c in cands if not DC.already_added(c, store['services']) and (c['default'] and c['ready'] if only_ready else True)]
    skipped = [c for c in cands if c not in new and not DC.already_added(c, store['services'])]
    for c in new:
        out(f"  + {c['title']:<52} {c['cmd'][:60]}" + (f"  :{c['port']}" if c['port'] else ''))
    for c in skipped:
        out(f"  - {c['title']:<52} (bỏ qua: {c['note'] or 'không chọn sẵn'})")
    if not dry_run and new:
        store['services'].extend(DC.to_service(c) for c in new)
        store.save()
    return len(new)


def main(argv):
    if '--add-services' not in argv:
        return None
    dry = '--dry-run' in argv
    pid = other_devdock_running()
    if pid and not dry:
        print(f'DevDock đang chạy (PID {pid}). Hãy đóng DevDock rồi chạy lại lệnh này, nếu không app sẽ ghi đè cài đặt.', file=sys.stderr)
        return 2
    n = add_services(Store(), only_ready='--all' not in argv, dry_run=dry)
    print(f"{'Sẽ thêm' if dry else 'Đã thêm'} {n} dev server. Mở DevDock → Dev Services để xem.")
    return 0
