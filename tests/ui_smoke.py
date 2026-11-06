"""Kiểm thử giao diện thật (cần màn hình): điều khiển widget và kiểm tra kết quả.
Chạy: DISPLAY/WAYLAND có sẵn, ví dụ  python3 tests/ui_smoke.py  (dùng XDG_* tạm để không đụng dữ liệu thật)."""
import os
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
tmp = tempfile.mkdtemp()
os.environ['XDG_CONFIG_HOME'] = tmp + '/cfg'
os.environ['XDG_DATA_HOME'] = tmp + '/data'

from gi.repository import GLib  # noqa: E402

from devdock import app as A  # noqa: E402
from devdock.ui import get_text, set_text  # noqa: E402

RESULTS = []


def check(name, cond, detail=''):
    RESULTS.append((name, bool(cond), detail))


def pump(ms=150):
    loop = GLib.MainLoop()
    GLib.timeout_add(ms, loop.quit)
    loop.run()


def services_tests(win):
    import tempfile as tf
    from devdock.lib import services as S
    win.select_module('services')
    pg = win.pages['services']
    pump(200)
    check('services empty state', pg.stack.get_visible_child_name() == 'empty')
    d = tf.mkdtemp()
    s = S.new_service('web', d, 'python3 -m http.server 38417', 38417)
    pg.save_service(None, 'web', d, 'python3 -m http.server 38417', 38417)
    pump(200)
    check('service saved + shown', pg.stack.get_visible_child_name() == 'detail' and len(pg.svcs) == 1)
    sid = pg.svcs[0]['id']
    pg.start()
    for _ in range(40):
        pump(150)
        if S.port_open(38417):
            break
    check('service running + port open', win.services.is_running(sid) and S.port_open(38417))
    pump(1300)
    check('status badge running', pg.d_badge.get_text() == 'Đang chạy', pg.d_badge.get_text())
    txt = pg.logbuf.get_text(pg.logbuf.get_start_iter(), pg.logbuf.get_end_iter(), False)
    check('log shown', 'Serving HTTP' in txt or 'http.server' in txt, txt[:120])
    check('stop enabled / start disabled', pg.b_stop.get_sensitive() and not pg.b_start.get_sensitive())
    pg.stop()
    for _ in range(40):
        pump(150)
        if not win.services.is_running(sid):
            break
    check('service stopped', not win.services.is_running(sid))
    check('port closed after stop', not S.port_open(38417))


def logs_tests(win):
    import tempfile as tf
    d = tf.mkdtemp()
    path = os.path.join(d, 'a.log')
    with open(path, 'w') as f:
        f.write('INFO start\nDEBUG detail\nWARN slow\nERROR boom one\nERROR boom two\n{"level":"error","msg":"j"}\nplain line\n')
    win.select_module('logs')
    pg = win.pages['logs']
    pg.open_source(('file', path))
    for _ in range(30):
        pump(150)
        if len(pg.all) >= 7:
            break
    check('logs loaded all lines', len(pg.all) == 7 and pg.shown == 7, f'{len(pg.all)}/{pg.shown}')
    pg.lvl_btns['info'].set_active(False)
    pg.lvl_btns['debug'].set_active(False)
    pg.lvl_btns['warn'].set_active(False)
    pump(100)
    check('level filter: only errors', pg.shown == 3, str(pg.shown))
    for b in pg.lvl_btns.values():
        b.set_active(True)
    pg.search.set_text('boom')
    pump(500)
    check('text filter', pg.shown == 2, str(pg.shown))
    pg.rx.set_active(True)
    pg.search.set_text('boom (one|two)$')
    pump(500)
    check('regex filter', pg.shown == 2, str(pg.shown))
    pg.search.set_text('(')
    pump(500)
    check('bad regex shows error', pg.err.get_visible())
    pg.search.set_text('')
    pg.rx.set_active(False)
    pump(400)
    pg.pause.set_active(True)
    with open(path, 'a') as f:
        f.write('ERROR later\n')
    pump(1200)
    check('paused keeps count', pg.shown == 7 and len(pg.pending) >= 1, f'{pg.shown} pending={len(pg.pending)}')
    pg.pause.set_active(False)
    pump(800)
    check('resume shows pending', pg.shown == 8, str(pg.shown))
    pg.clear()
    check('clear', pg.shown == 0 and len(pg.all) == 0)
    pump(300)  # header của ListBox đã được dựng
    pg.open_source(('file', path))
    pg.open_source(('docker', 'abc123', 'fake'))
    check('open_source works after headers exist', pg.source[:2] == ('docker', 'abc123'))


def env_tests(win):
    import tempfile as tf
    from devdock.lib import envfiles as E
    root = tf.mkdtemp()
    app = os.path.join(root, 'app')
    os.makedirs(app)
    with open(os.path.join(app, '.env'), 'w') as f:
        f.write('PORT=3000\nDB_PASSWORD=hunter2\n')
    with open(os.path.join(app, '.env.example'), 'w') as f:
        f.write('PORT=\nDB_PASSWORD=\nNEW_VAR=x\n')
    win.store['project_roots'] = [root]
    win.select_module('env')
    pg = win.pages['env']
    pg.files = []
    pg.refresh()
    for _ in range(30):
        pump(150)
        if pg.files:
            break
    check('env files found', len(pg.files) == 2, str(pg.files))
    pg.load_file(os.path.join(os.path.realpath(app), '.env'))
    pump(200)
    check('env vars listed', set(pg.data) == {'PORT', 'DB_PASSWORD'})
    check('secret masked by default', pg.show_val(pg.data['DB_PASSWORD']) == E.mask('hunter2') and pg.show_val(pg.data['PORT']) == '3000')
    pg.sw.set_active(True)
    check('reveal shows value', pg.show_val(pg.data['DB_PASSWORD']) == 'hunter2')
    check('missing-vs-example banner', pg.warn.get_revealed() and 'NEW_VAR' in pg.warn.get_title(), pg.warn.get_title())
    E.set_var(pg.cur, 'EXTRA', 'v v')
    pg.load_file(pg.cur)
    check('edit reflected', pg.data['EXTRA']['value'] == 'v v')
    pg.tg.set_active_name('cmp')
    pg.other.set_selected(0)
    pump(200)
    rows = {r.d['key']: r.d['status'] for r in pg.cmp_table.store}
    check('compare statuses', rows.get('NEW_VAR') == 'only_b' and rows.get('EXTRA') == 'only_a' and rows.get('PORT') == 'different', str(rows))
    n = E.add_missing(pg.cur, [('NEW_VAR', 'x')])
    check('add missing', n == 1 and 'NEW_VAR' in E.load(pg.cur))


def docker_tests(win):
    import subprocess
    import tempfile as tf
    from devdock.lib import dockercli as D
    name = 'devdock-ui-test'
    subprocess.run(['docker', 'rm', '-f', name], capture_output=True)
    r = subprocess.run(['docker', 'run', '-d', '--name', name, 'redis:7-alpine'], capture_output=True, text=True)
    if r.returncode:
        check('docker: không tạo được container thử (bỏ qua)', True, r.stderr)
        return
    comp_dir = tf.mkdtemp(prefix='ddui-')
    comp = os.path.join(comp_dir, 'docker-compose.yml')
    with open(comp, 'w') as f:
        f.write('services:\n  r:\n    image: redis:7-alpine\n    container_name: devdock-ui-compose\n')
    try:
        win.select_module('docker')
        pg = win.pages['docker']
        pg.tick_status()
        for _ in range(40):
            pump(150)
            if pg.ok:
                break
        check('docker connected', pg.ok)

        def find(n):
            for i in range(pg.ct.sorted.get_n_items()):
                if pg.ct.sorted.get_item(i).d['name'] == n:
                    return i, pg.ct.sorted.get_item(i).d
            return None, None

        def refresh_until(cond, tries=40):
            for _ in range(tries):
                pg.tick_containers(True)
                pump(300)
                if cond():
                    return True
            return False
        check('container listed running', refresh_until(lambda: (find(name)[1] or {}).get('state') == 'running'))
        i, _d = find(name)
        pg.ct.sel.set_selected(i)
        pump(100)
        check('selection enables stop, disables start', pg.cbtn['stop'].get_sensitive() and not pg.cbtn['start'].get_sensitive())
        pg.act('stop')
        check('stop works', refresh_until(lambda: (find(name)[1] or {}).get('state') == 'exited'))
        i, _d = find(name)
        pg.ct.sel.set_selected(i)
        pump(100)
        check('selection enables start', pg.cbtn['start'].get_sensitive() and not pg.cbtn['stop'].get_sensitive())
        pg.act('start')
        check('start works', refresh_until(lambda: (find(name)[1] or {}).get('state') == 'running'))
        pg.act('restart')
        pump(2500)
        check('restart keeps running', refresh_until(lambda: (find(name)[1] or {}).get('state') == 'running'))
        # nút Xem log chuyển sang Log Viewer với đúng nguồn
        pg.open_logs()
        pump(400)
        check('open logs jumps to Log Viewer', win.current == 'logs' and win.pages['logs'].source and win.pages['logs'].source[0] == 'docker')
        win.select_module('docker')
        # images
        pg.tick_images(True)
        pump(1200)
        check('images listed', pg.it.store.get_n_items() > 0)
        # compose
        pg.add_file(comp)
        pump(200)
        pg.clist.select_row(pg.clist.get_first_child())
        pump(200)
        check('compose file selectable', pg.sel_compose() == comp and pg.cp_btns[0].get_sensitive())
        pg.run_compose('up')
        for _ in range(60):
            pump(300)
            if 'compose' in pg.cp_out.get_buffer().get_text(pg.cp_out.get_buffer().get_start_iter(), pg.cp_out.get_buffer().get_end_iter(), False) and 'Đang chạy' not in pg.cp_out.get_buffer().get_text(pg.cp_out.get_buffer().get_start_iter(), pg.cp_out.get_buffer().get_end_iter(), False):
                break
        up = subprocess.run(['docker', 'ps', '--filter', 'name=devdock-ui-compose', '--format', '{{.Names}}'], capture_output=True, text=True).stdout.strip()
        check('compose up created container', up == 'devdock-ui-compose', up)
        pg.run_compose('down')
        for _ in range(60):
            pump(300)
            if not subprocess.run(['docker', 'ps', '-a', '--filter', 'name=devdock-ui-compose', '--format', '{{.Names}}'], capture_output=True, text=True).stdout.strip():
                break
        gone = not subprocess.run(['docker', 'ps', '-a', '--filter', 'name=devdock-ui-compose', '--format', '{{.Names}}'], capture_output=True, text=True).stdout.strip()
        check('compose down removed container', gone)
        # xoá container qua D.action (hộp thoại xác nhận cần người bấm)
        D.action(name, 'remove')
        check('removed', refresh_until(lambda: find(name)[1] is None))
    finally:
        subprocess.run(['docker', 'rm', '-f', name, 'devdock-ui-compose'], capture_output=True)


def cleaner_tests(win):
    import shutil
    import tempfile as tf
    from devdock.cleaner import scanner
    proj = tf.mkdtemp(prefix='dd-ui-test-', dir=os.path.expanduser('~'))
    with open(os.path.join(proj, 'package.json'), 'w') as f:
        f.write('{}')
    os.makedirs(os.path.join(proj, 'node_modules'))
    with open(os.path.join(proj, 'node_modules', 'big.bin'), 'wb') as f:
        f.write(b'\0' * (6 * 1024 * 1024))
    keep = os.path.join(proj, 'package.json')
    try:
        win.select_module('cleaner')
        pg = win.pages['cleaner']
        pump(300)
        check('cleaner idle state', pg.phase == 'idle' and pg.disk and pg.disk['total'] > 0)
        pg.start_scan()
        for _ in range(200):
            pump(200)
            if pg.phase == 'ready':
                break
        check('scan finished', pg.phase == 'ready', pg.phase)
        mine = [i for i in pg.items if i.get('paths') and i['paths'][0] == os.path.join(proj, 'node_modules')]
        check('scan found my project', len(mine) == 1 and mine[0]['rebuild'] == 'npm install', str(mine)[:200])
        check('items sorted/valid', all(i['size'] > 0 and i['level'] in ('safe', 'review') for i in pg.items))
        check('safe preselected (not root)', all((i['id'] in pg.selected) == (i['level'] == 'safe' and i['kind'] != 'root') for i in pg.items))
        check('list rendered groups', pg.groups_box.get_first_child() is not None)
        pg.selected = {mine[0]['id']}  # chỉ dọn đúng project giả
        pg.do_clean(pg.chosen())
        for _ in range(100):
            pump(200)
            if pg.phase == 'done':
                break
        check('clean finished', pg.phase == 'done', pg.phase)
        check('node_modules actually deleted', not os.path.exists(os.path.join(proj, 'node_modules')))
        check('source files kept', os.path.exists(keep))
        check('item removed from list', all(i['id'] != mine[0]['id'] for i in pg.items))
        check('stats saved', win.store['clean_count'] >= 1 and win.store['total_freed'] >= 6 * 1024 * 1024)
        pg.stack.set_visible_child_name('tree')
        for _ in range(100):
            pump(200)
            if pg.tree_stack.get_visible_child_name() == 'map':
                break
        check('treemap loaded', pg.tree_stack.get_visible_child_name() == 'map' and len(pg.treemap.entries) > 0)
    finally:
        shutil.rmtree(proj, ignore_errors=True)


def main():
    app = A.App()

    def go(_app):
        win = app.win
        try:
            for fn in TESTS:
                fn(win)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            check('exception', False, traceback.format_exc()[-300:])
        app.quit()
    app.connect('activate', lambda a: GLib.idle_add(lambda: go(a) and False))
    app.run([])
    bad = [r for r in RESULTS if not r[1]]
    for n, ok, d in RESULTS:
        print(('PASS' if ok else 'FAIL'), n, ('' if ok else d))
    print(f'\n{len(RESULTS) - len(bad)}/{len(RESULTS)} passed')
    sys.exit(1 if bad else 0)


TESTS = [services_tests, logs_tests, env_tests, docker_tests, cleaner_tests]
if __name__ == '__main__':
    main()
