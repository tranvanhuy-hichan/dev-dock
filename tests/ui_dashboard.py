"""Test giao diện trang Dashboard: dữ liệu hiển thị, dev service chạy/dừng ngay tại chỗ, mục cần chú ý, công tắc tự khởi động."""
import os
import tempfile

from uihelp import check, pump, wait_for


def rows_of(lb):
    out, r = [], lb.get_first_child()
    while r:
        out.append(r)
        r = r.get_next_sibling()
    return out


def dashboard_tests(win):
    from devdock.app import MODULES
    from devdock.lib import watchdog as W
    check('dashboard is the first module and sidebar group', MODULES[0][0] == 'dashboard' and win.nav_rows()[0].mid == 'dashboard')
    win.select_module('dashboard')
    pg = win.pages['dashboard']
    check('dashboard shows system data', wait_for(lambda: pg.snap is not None and pg.g_cpu.text.endswith('%') and pg.g_mem.text.endswith('%'), 10), f'{pg.g_cpu.text} {pg.g_mem.text}')
    check('gauges animate to the value', wait_for(lambda: abs(pg.g_mem.shown - pg.g_mem.frac) < 0.01 and pg.g_mem.frac > 0, 5))
    check('disk card filled', 'trống' in pg.c_disk[2].get_text(), pg.c_disk[2].get_text())
    check('hero clock + chips', len(pg.clock.get_text()) == 5 and pg.clock.get_text()[2] == ':' and bool(pg._chip_sig), str(getattr(pg, '_chip_sig', None)))
    check('greeting set', pg.title.get_text().startswith('Chào'))
    from devdock.pages.dashboard import Gauge
    g = Gauge()
    g.set(0.95, '95%')
    check('gauge value clamps and stores text', g.frac == 0.95 and g.text == '95%')
    g.set(7, 'x')
    check('gauge clamps above 1', g.frac == 1.0)
    check('header refresh button', len(pg.hdr_end) == 1)
    pg.refresh_all()
    pump(200)
    # ---- dev service ngay trên dashboard
    d = tempfile.mkdtemp(prefix='ddui-dash-')
    win.store['services'] = [{'id': 's1', 'name': 'ngủ', 'cwd': d, 'cmd': 'sleep 60', 'port': 0, 'project': 't'}, {'id': 's2', 'name': 'hỏng', 'cwd': d, 'cmd': 'exit 3', 'port': 0, 'project': 't'}]
    pg.svc_sig = None
    pg.render_services()
    check('service card lists both services', len(rows_of(pg.svc_list)) == 2, str(len(rows_of(pg.svc_list))))
    pg.toggle_service({'id': 's1'}, False)
    pg.toggle_service({'id': 's2'}, False)
    check('start from dashboard works', wait_for(lambda: win.services.is_running('s1'), 8))
    check('failing service becomes failed', wait_for(lambda: win.services.status('s2')['state'] == 'failed', 8))
    pg._attn_sig = None
    pg.render_services()
    pg.render_attention()
    attn_titles = [r.get_title() for lst in [rows_of(pg.attn_box)[0]] if hasattr(lst, 'get_first_child') for r in rows_of(lst) if hasattr(r, 'get_title')]
    check('attention lists the failed service', any('hỏng' in t for t in attn_titles), str(attn_titles))
    check('running service sorted first', pg.svc_sig[0][0] == ('s1', 'running'), str(pg.svc_sig))
    pg.toggle_service({'id': 's1'}, True)
    check('stop from dashboard works', wait_for(lambda: not win.services.is_running('s1'), 8))
    win.services.forget('s1')
    win.services.forget('s2')
    # ---- mọi thứ ổn
    win.store['services'] = []
    pg.snap = {'host': 'h', 'os': 'o', 'uptime': 1, 'cpu_total': 5, 'cpu_cores': [1], 'load': [0, 0, 0], 'mem_used': 30, 'mem_total': 100, 'swap_used': 0, 'swap_total': 0,
               'disks': [{'mount': '/', 'free': 80, 'total': 100, 'name': 'x', 'fs': 'ext4'}], 'gpus': [], 'net_rx': 0, 'net_tx': 0}
    pg.projects, pg.docker, pg.alerts = [], {'available': True, 'containers': []}, []
    pg._attn_sig = None
    pg.render_attention()
    first = rows_of(pg.attn_box)[0]
    check('all-clear strip shown', 'Mọi thứ đều ổn' in first.text.get_text(), str(type(first)))
    pg.snap['mem_used'] = 95
    pg._attn_sig = None
    pg.render_attention()
    check('attention updates on RAM pressure', rows_of(pg.attn_box) and any('RAM' in r.get_title() for r in rows_of(rows_of(pg.attn_box)[0]) if hasattr(r, 'get_title')))
    # ---- tự khởi động (XDG_CONFIG_HOME tạm của bộ test: không đụng ~/.config thật)
    check('autostart off initially', not W.autostart_enabled() and not pg.auto_row.get_active())
    pg.auto_row.set_active(True)
    p = W.autostart_path()
    check('autostart file created', os.path.exists(p) and p.startswith(os.environ['XDG_CONFIG_HOME']), p)
    txt = open(p).read()
    check('autostart entry is valid', 'Name=DevDock\n' in txt and '--hidden' in txt and 'Type=Application' in txt and 'X-GNOME-Autostart-enabled=true' in txt, txt)
    check('autostart note explains behaviour', 'mỗi lần bạn đăng nhập' in pg.auto_note.get_text())
    pg.auto_row.set_active(False)
    check('autostart file removed', not os.path.exists(p) and not W.autostart_enabled())
    win.store['services'] = []


TESTS = [dashboard_tests]
