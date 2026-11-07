"""Kiểm thử giao diện thật (cần màn hình): điều khiển widget và kiểm tra kết quả.
Chạy: DISPLAY/WAYLAND có sẵn, ví dụ  python3 tests/ui_smoke.py  (dùng XDG_* tạm để không đụng dữ liệu thật)."""
import os
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
tmp = tempfile.mkdtemp()
os.environ['DEVDOCK_TEST'] = '1'
os.environ['XDG_CONFIG_HOME'] = tmp + '/cfg'
os.environ['XDG_DATA_HOME'] = tmp + '/data'

from gi.repository import GLib  # noqa: E402

from devdock import app as A  # noqa: E402
from devdock.ui import get_text, set_text  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from uihelp import RESULTS, check, pump  # noqa: E402


def projects_tests(win):
    import subprocess
    import tempfile as tf
    root = tf.mkdtemp(prefix='ddui-proj-')
    a = os.path.join(root, 'alpha')
    os.makedirs(a)
    subprocess.run(['git', '-C', a, 'init', '-q', '-b', 'main'], check=True)
    with open(os.path.join(a, 'package.json'), 'w') as f:
        f.write('{}')
    subprocess.run(['git', '-C', a, 'add', '.'], check=True)
    subprocess.run(['git', '-C', a, '-c', 'user.email=a@b.c', '-c', 'user.name=T', 'commit', '-qm', 'init'], check=True)
    with open(os.path.join(a, 'dirty.txt'), 'w') as f:
        f.write('x')
    os.makedirs(os.path.join(root, 'beta'))
    with open(os.path.join(root, 'beta', 'Cargo.toml'), 'w') as f:
        f.write('')
    win.store['project_roots'] = [root]
    win.select_module('projects')
    pg = win.pages['projects']
    for _ in range(80):  # chờ lượt quét tự động lúc mở trang xong
        if not pg.busy:
            break
        pump(150)
    pg.items = []
    pg.refresh()
    for _ in range(40):
        pump(150)
        if len(pg.items) == 2:
            break
    check('projects found', sorted(p['name'] for p in pg.items) == ['alpha', 'beta'], str([p['name'] for p in pg.items]))
    al = next(p for p in pg.items if p['name'] == 'alpha')
    check('git status parsed', al['branch'] == 'main' and al['untracked'] == 1 and al['lang'] == 'Node')
    check('header has refresh + root buttons', len(pg.hdr_end) == 2)
    pg.table.sel.set_selected(0)
    pump(100)
    check('selection reveals action bar', pg.bar.get_revealed())
    pg.flt = 1
    pg.render()
    pump(100)
    check('filter dirty shows only alpha', pg.table.store.get_n_items() == 1)
    pg.flt = 0
    pg.render()
    win.store['project_roots'] = []


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


def discover_ui_tests(win):
    import json as js
    import tempfile as tf
    root = tf.mkdtemp(prefix='ddui-disc-')
    p = os.path.join(root, 'shop')
    os.makedirs(os.path.join(p, '.git'))  # dấu hiệu repo => shop là project, web là thư mục con
    os.makedirs(os.path.join(p, 'web'))
    with open(os.path.join(p, 'web', 'package.json'), 'w') as f:
        js.dump({'name': 'web', 'scripts': {'dev': 'next dev -p 4010'}}, f)
    win.store['project_roots'] = [root]
    win.store['services'] = []
    win.select_module('services')
    pg = win.pages['services']
    pg.reload()
    from devdock.pages.services import DiscoverDialog
    dlg = DiscoverDialog(pg)
    dlg.present(win)
    for _ in range(40):
        pump(150)
        if dlg.cands:
            break
    check('dialog found candidate', len(dlg.cands) == 1 and dlg.cands[0]['cmd'] == 'npm run dev' and dlg.cands[0]['port'] == 4010, str(dlg.cands))
    check('not-ready candidate not preselected', dlg.add_btn.get_sensitive() is False)
    dlg.select(lambda c: True)
    check('add button counts', dlg.add_btn.get_sensitive() and '1' in dlg.add_btn.get_label())
    dlg.add()
    pump(300)
    check('service added with project', len(pg.svcs) == 1 and pg.svcs[0]['project'] == 'shop' and pg.svcs[0]['port'] == 4010, str(pg.svcs))
    check('setup strip warns missing node_modules', pg.d_note.get_visible() and 'npm install' in pg.d_note.text.get_text(), pg.d_note.text.get_text())
    dlg2 = DiscoverDialog(pg)
    dlg2.present(win)
    for _ in range(40):
        pump(150)
        if dlg2.cands:
            break
    check('already added is flagged', all(added for _c, _k, added in dlg2.checks.values()))
    dlg2.close()
    win.store['services'] = []
    win.store['project_roots'] = []


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
    pg.reveal_btn.set_active(True)
    check('reveal shows value', pg.show_val(pg.data['DB_PASSWORD']) == 'hunter2')
    check('missing-vs-example strip', pg.warn.get_visible() and 'NEW_VAR' in (pg.warn.text.get_tooltip_text() or ''), str(pg.warn.text.get_tooltip_text()))
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
        pg.detail.open_viewer()
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


def docker_ext_tests(win):
    import subprocess
    from devdock.lib import dockercli as D
    from devdock.pages.docker import RunDialog
    name, vol, net = 'devdock-ui-ext', 'devdock-ui-ext-vol', 'devdock-ui-ext-net'
    for cmd in (['rm', '-f', name], ['volume', 'rm', '-f', vol], ['network', 'rm', net]):
        subprocess.run(['docker', *cmd], capture_output=True)
    try:
        subprocess.run(['docker', 'volume', 'create', vol], capture_output=True, check=True)
        subprocess.run(['docker', 'network', 'create', net], capture_output=True, check=True)
        win.select_module('docker')
        pg = win.pages['docker']
        pg.tick_status()
        for _ in range(40):
            pump(150)
            if pg.ok:
                break
        check('docker connected (ext)', pg.ok)
        # --- chạy container từ Images qua RunDialog
        dlg = RunDialog(pg, 'redis:7-alpine')
        dlg.name.set_text(name)
        dlg.env.set_text('FOO=bar, SECRET_TOKEN=abc')
        dlg.ports.set_text('bad')
        dlg.run()
        check('run dialog rejects bad port', 'không hợp lệ' in dlg.err.get_text(), dlg.err.get_text())
        dlg.ports.set_text('')
        dlg.run()
        for _ in range(60):
            pump(200)
            if subprocess.run(['docker', 'ps', '--filter', f'name={name}', '--format', '{{.Names}}'], capture_output=True, text=True).stdout.strip():
                break
        check('run dialog started container', subprocess.run(['docker', 'ps', '--filter', f'name={name}', '--format', '{{.Names}}'], capture_output=True, text=True).stdout.strip() == name)
        # --- chi tiết + lỗi reset tab khi bảng làm mới
        pg.stack.set_visible_child_name('containers')
        row = None
        for _ in range(40):
            pg.tick_containers(True)
            pump(300)
            for i in range(pg.ct.sorted.get_n_items()):
                if pg.ct.sorted.get_item(i).d['name'] == name:
                    row = i
            if row is not None:
                break
        pg.ct.sel.set_selected(row)
        for _ in range(40):
            pump(150)
            if pg.detail.info:
                break
        check('detail shows inspect info', pg.detail.info and pg.detail.info['name'] == name and ('FOO', 'bar') in pg.detail.info['env'])
        check('sidebar shown on selection', pg.split.get_show_sidebar())
        pg.detail.tg.set_active_name('env')
        pump(200)
        sec = [r.d for r in pg.detail.env_table.store if r.d['k'] == 'SECRET_TOKEN']
        check('env secret flagged', sec and sec[0]['secret'])
        pg.detail.tg.set_active_name('stats')
        for _ in range(3):
            pg.tick_containers(True)
            pump(800)
        check('detail tab survives table refresh', pg.detail.tg.get_active_name() == 'stats' and pg.split.get_show_sidebar())
        for _ in range(30):
            pump(300)
            if pg.detail.cpu_h:
                break
        check('live stats collected', len(pg.detail.cpu_h) >= 1)
        pg.detail.tg.set_active_name('logs')
        pump(1200)
        check('log stream opened', pg.detail.stream is not None)
        pg.detail.tg.set_active_name('info')
        check('log stream closed when leaving tab', pg.detail.stream is None)
        # --- project action không hợp lệ bị chặn, volume/network
        pg.stack.set_visible_child_name('volumes')
        pg.tick_volumes(True)
        pg.stack.set_visible_child_name('networks')
        pg.tick_networks(True)
        for _ in range(60):
            pump(300)
            if any(r.d['name'] == vol for r in pg.vt.store) and any(r.d['name'] == net for r in pg.nt.store):
                break
        check('volumes tab lists new volume', any(r.d['name'] == vol for r in pg.vt.store))
        check('networks tab lists new network + builtin flag', any(r.d['name'] == net for r in pg.nt.store) and any(r.d['builtin'] for r in pg.nt.store))
        pg.stack.set_visible_child_name('overview')
        pg.tick_df(True)
        pg.tick_overview(True)
        for _ in range(40):
            pump(300)
            if pg.c_disk[1].get_text() != '—' and pg.stats_table.store.get_n_items() > 0:
                break
        check('overview: disk usage shown', pg.c_disk[1].get_text() != '—', pg.c_disk[1].get_text())
        check('overview: live stats table has my container', any(r.d['name'] == name for r in pg.stats_table.store))
        # compose được phát hiện từ nhãn container
        check('compose discovery list is list', isinstance(pg.discovered, list))
    finally:
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True)
        subprocess.run(['docker', 'volume', 'rm', '-f', vol], capture_output=True)
        subprocess.run(['docker', 'network', 'rm', net], capture_output=True)


def ollama_tests(win):
    import sys as _s
    _s.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
    from mock_ollama import make_server
    from devdock.lib import ollama as O
    from devdock.pages.ollama import MessageView, md_to_pango, split_blocks
    srv, st = make_server()
    win.store['ollama_host'] = f'http://127.0.0.1:{srv.server_address[1]}'
    try:
        win.select_module('ollama')
        pg = win.pages['ollama']
        pg.client = O.Client(win.store['ollama_host'])
        pg.refresh(True)
        for _ in range(40):
            pump(150)
            if pg.up:
                break
        check('ollama connected', pg.up is True and pg.version == '9.9.9-mock')
        check('models table has 3 models', pg.mt.store.get_n_items() == 3)
        check('chat dropdown excludes embedding model', pg.chat_models() == ['think:4b', 'tiny:1b'], str(pg.chat_models()))
        check('running model flagged', 'tiny:1b' in pg.running)
        # ---- chat với model không suy nghĩ
        pg.new_chat('tiny:1b')
        pump(100)
        check('think switch hidden for non-thinking model', not pg.think_box.get_visible())
        pg.input.get_buffer().set_text('hello there', -1)
        pg.send()
        for _ in range(60):
            pump(150)
            if not pg.streaming:
                break
        msgs = pg.chat['messages']
        check('chat reply streamed + saved', [m['role'] for m in msgs] == ['user', 'assistant'] and msgs[1]['content'] == 'Echo: hello there', str(msgs))
        check('stats saved', msgs[1].get('stats', {}).get('tokens') == 3)
        check('think flag not sent to non-thinking model', 'think' not in st.chat_requests[-1])
        check('chat persisted to disk', any(c['id'] == pg.chat['id'] and len(c['messages']) == 2 for c in pg.chats.chats))
        check('chat title from first message', pg.chat['title'] == 'hello there', pg.chat['title'])
        # ---- model suy nghĩ + system prompt + context
        pg.new_chat('think:4b')
        pump(100)
        for i in range(pg.model_dd.get_model().get_n_items()):
            if pg.model_dd.get_model().get_string(i) == 'think:4b':
                pg.model_dd.set_selected(i)
        check('think switch visible for thinking model', pg.think_box.get_visible())
        pg.chat['system'] = 'Be brief'
        pg.input.get_buffer().set_text('what is 2+2', -1)
        pg.send()
        for _ in range(60):
            pump(150)
            if not pg.streaming:
                break
        req = st.chat_requests[-1]
        check('system prompt first, think sent', req['messages'][0] == {'role': 'system', 'content': 'Be brief'} and req.get('think') is True and req['model'] == 'think:4b', str(req)[:200])
        am = pg.chat['messages'][-1]
        check('thinking stored separately', am.get('thinking') == 'Hmm let me think' and am['content'] == 'Echo: what is 2+2', str(am))
        # ---- nhiều lượt: lịch sử được gửi lại
        pg.input.get_buffer().set_text('and 3+3', -1)
        pg.send()
        for _ in range(60):
            pump(150)
            if not pg.streaming:
                break
        roles = [m['role'] for m in st.chat_requests[-1]['messages']]
        check('multi-turn history sent', roles == ['system', 'user', 'assistant', 'user'], str(roles))
        check('assistant history excludes thinking text', all('thinking' not in m for m in st.chat_requests[-1]['messages']))
        # ---- Enter gửi, Shift+Enter không; IME nuốt Enter thì không gửi
        buf = pg.input.get_buffer()
        buf.set_text('via enter', -1)
        n0, off = buf.get_char_count(), buf.get_char_count()
        buf.insert(buf.get_end_iter(), '\n')  # TextView chèn xuống dòng khi Enter
        pg.enter_check(n0, off)
        for _ in range(60):
            pump(150)
            if not pg.streaming:
                break
        check('Enter sends and strips newline', st.chat_requests[-1]['messages'][-1]['content'] == 'via enter', st.chat_requests[-1]['messages'][-1]['content'])
        n_before = len(st.chat_requests)
        buf.set_text('composing', -1)
        pg.enter_check(buf.get_char_count(), buf.get_char_count())  # IME đã nuốt Enter: không có ký tự mới
        pump(300)
        check('Enter swallowed by IME does not send', len(st.chat_requests) == n_before and not pg.streaming)
        buf.set_text('', -1)
        # ---- dừng giữa chừng
        pg.input.get_buffer().set_text(' '.join(['word'] * 300), -1)
        pg.send()
        pump(400)
        was = pg.streaming
        pg.send_or_stop()
        for _ in range(40):
            pump(150)
            if not pg.streaming:
                break
        check('stop mid-stream works', was and not pg.streaming and pg.chat['messages'][-1]['content'].endswith('[đã dừng]'), pg.chat['messages'][-1]['content'][-30:])
        # ---- lịch sử
        n_chats = len(pg.chats.chats)
        pg.delete_chat(pg.chat['id'])
        pump(200)
        check('delete chat', len(pg.chats.chats) <= n_chats and pg.chat is not None)
        # ---- tải model có tiến trình
        pg.stack.set_visible_child_name('models')
        pg.pull('fresh:1b')
        for _ in range(60):
            pump(150)
            if 'fresh:1b' not in pg.pulls and any(m['name'] == 'fresh:1b' for m in pg.tags):
                break
        check('pull finished and model listed', any(m['name'] == 'fresh:1b' for m in pg.tags))
        check('pull row cleaned up', not pg.pulls and not pg.pull_list.get_visible())
        pg.pull('bad name!')
        check('invalid name rejected, nothing started', not pg.pulls)
        # ---- dỡ / xoá
        pg.mt.sel.set_selected(next(i for i in range(pg.mt.sorted.get_n_items()) if pg.mt.sorted.get_item(i).d['name'] == 'tiny:1b'))
        pump(100)
        check('unload enabled for running model', pg.mb['unload'].get_sensitive())
        pg.unload()
        for _ in range(30):
            pump(150)
            if 'tiny:1b' not in pg.running:
                break
        check('unload works', 'tiny:1b' not in pg.running)
        pg.mt.sel.set_selected(next(i for i in range(pg.mt.sorted.get_n_items()) if pg.mt.sorted.get_item(i).d['name'] == 'embed:m'))
        pump(100)
        check('chat button disabled for embedding model', not pg.mb['chat'].get_sensitive())
        bgdone = []
        from devdock.ui import bg
        bg(lambda: pg.client.delete('embed:m'), lambda r, e: bgdone.append(e))
        for _ in range(30):
            pump(150)
            if bgdone:
                break
        pg.refresh(True)
        pump(600)
        check('delete model works', all(m['name'] != 'embed:m' for m in pg.tags))
        # ---- hiển thị tin nhắn
        check('markdown: bold, code, bullet', all(x in md_to_pango('**a** `b` \n- c') for x in ('<b>a</b>', '<tt>b</tt>', '•')))
        check('markdown escapes markup', '<script>' not in md_to_pango('<script>x</script>') and '&lt;script&gt;' in md_to_pango('<script>'))
        check('code fence split (open fence = code)', [b[0] for b in split_blocks('a\n```py\nx=1')] == ['text', 'code'] and split_blocks('a\n```py\nx=1')[1][2] == 'py')
        mv = MessageView('assistant')
        mv.set('Hello ```python\nprint(1)\n``` bye')
        mv.set('Hello ```python\nprint(12)\n``` bye!')  # cập nhật tại chỗ
        check('message view updates code in place', [b[0] for b in mv.blocks] == ['text', 'code', 'text'] and mv.body.get_first_child().get_next_sibling().code_text == 'print(12)')
        mv2 = MessageView('assistant')
        mv2.set('<think>deep</think>answer')
        check('inline <think> goes to expander', mv2.think_exp.get_visible() and mv2.text == 'answer')
        # ---- máy chủ tắt => trang báo lỗi rõ ràng
        srv.shutdown()
        srv.server_close()
        pg.refresh(True)
        for _ in range(40):
            pump(150)
            if pg.up is False:
                break
        check('server down shows friendly page', pg.up is False and pg.outer.get_visible_child_name() == 'down' and 'Ollama' in pg.down.get_description())
    finally:
        win.store['ollama_host'] = ''
        try:
            srv.shutdown()
        except Exception:  # noqa: BLE001
            pass


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


BASE_TESTS = [projects_tests, services_tests, discover_ui_tests, logs_tests, env_tests, docker_tests, docker_ext_tests, ollama_tests, cleaner_tests]


def collect():
    import importlib
    only = [x for x in os.environ.get('UI_ONLY', '').split(',') if x]
    tests = [] if only else list(BASE_TESTS)
    here = os.path.dirname(os.path.abspath(__file__))
    for fn in sorted(os.listdir(here)):
        if fn.startswith('ui_') and fn.endswith('.py') and fn != 'ui_smoke.py' and (not only or fn[:-3] in only):
            tests += importlib.import_module(fn[:-3]).TESTS
    return tests


TESTS = collect()

if __name__ == '__main__':
    main()
