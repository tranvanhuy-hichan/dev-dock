"""Test giao diện Port Manager: liệt kê cổng, chủ sở hữu, dừng an toàn có xác nhận, cảnh báo trùng cổng trước khi chạy service."""
import os
import subprocess
import sys
import tempfile

from uihelp import check, pump, wait_for

LISTEN = 'import socket,time\ns=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)\ns.bind(("127.0.0.1",0));s.listen();print(s.getsockname()[1],flush=True);time.sleep(120)'


def rows_of(pg):
    return [pg.table.store.get_item(i).d for i in range(pg.table.store.get_n_items())]


def answer(win, resp):
    d = win.get_visible_dialog()
    if d is None:
        return False
    d.emit('response', resp)
    d.force_close()
    return True


def ports_tests(win):
    while win.get_visible_dialog() is not None:  # dialog còn sót từ test trước
        win.get_visible_dialog().force_close()
        pump(50)
    from devdock.pages.services import start_checked
    d = tempfile.mkdtemp(prefix='ddui-port-')
    proc = subprocess.Popen([sys.executable, '-c', LISTEN], stdout=subprocess.PIPE, text=True, cwd=d)
    port = int(proc.stdout.readline())
    try:
        win.store['services'] = [{'id': 'p1', 'name': 'web', 'cwd': d, 'cmd': 'sleep 60', 'port': port, 'project': 't'}]
        win.select_module('ports')
        pg = win.pages['ports']
        pg.proj_paths = [d]
        pg.refresh()
        check('ports page lists the listening port', wait_for(lambda: any(r['port'] == port for r in rows_of(pg)), 10))
        row = next((r for r in rows_of(pg) if r['port'] == port), {})
        check('row has pid, owner, project', row.get('pid') == proc.pid and row.get('project') == d and 'python' in row.get('owner', ''), str(row.get('owner')))
        check('stat cards filled', pg.c_all[1].get_text().isdigit() and int(pg.c_all[1].get_text()) >= 1)
        check('conflict strip warns busy service port', wait_for(lambda: pg.warn.get_visible() and str(port) in pg.warn.text.get_text(), 5), pg.warn.text.get_text())
        # chọn dòng -> thanh hành động
        pg.search.set_text(str(port))
        pump(300)
        pg.table.sel.set_selected(0)
        pump(200)
        check('selecting row shows action bar with stop enabled', pg.bar.get_revealed() and pg.b_stop.get_sensitive())
        # filter
        pg.tg.set_active_name('docker')
        pump(200)
        check('docker filter hides plain process', not any(r['port'] == port for r in rows_of(pg)))
        pg.tg.set_active_name('dev')
        pump(200)
        check('dev filter shows project process', any(r['port'] == port for r in rows_of(pg)))
        # cảnh báo trước khi chạy service
        started = []
        start_checked(win, win.store['services'][0], lambda: started.append(1))
        check('start_checked asks when port busy', wait_for(lambda: win.get_visible_dialog() is not None, 5) and not started)
        check('cancel does not start', answer(win, 'no') and pump(200) is None and not started)
        start_checked(win, win.store['services'][0], lambda: started.append(1))
        wait_for(lambda: win.get_visible_dialog() is not None, 5)
        answer(win, 'go')
        check('"Vẫn chạy" starts anyway', wait_for(lambda: started == [1], 3))
        free_svc = {'id': 'p2', 'name': 'free-port', 'cwd': d, 'cmd': 'true', 'port': 0}
        start_checked(win, free_svc, lambda: started.append(2))
        check('no port -> starts immediately without dialog', wait_for(lambda: started == [1, 2], 3) and win.get_visible_dialog() is None)
        # dừng qua hộp xác nhận
        pg.table.sel.set_selected(0)
        pump(200)
        pg.free()
        check('free asks for confirmation', wait_for(lambda: win.get_visible_dialog() is not None, 3) and proc.poll() is None)
        answer(win, 'no')
        pump(200)
        check('declining keeps process alive', proc.poll() is None)
        pg.free()
        wait_for(lambda: win.get_visible_dialog() is not None, 3)
        answer(win, 'yes')
        check('confirming stops the process', wait_for(lambda: proc.poll() is not None, 6))
        pg.refresh()
        check('port disappears from list', wait_for(lambda: not any(r['port'] == port for r in rows_of(pg)), 8))
        # không dừng được chính DevDock
        from devdock.lib import ports as PT
        check('own pid protected', not PT.can_stop({'pid': os.getpid(), 'mine': True})[0])
    finally:
        proc.kill()
        proc.wait()
        win.store['services'] = []


TESTS = [ports_tests]
