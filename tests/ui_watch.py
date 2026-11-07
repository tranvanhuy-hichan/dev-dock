"""Test giao diện module Giám sát: cảnh báo, lịch sử, thông báo (giả), công tắc lưu bền, chạy nền/autostart, nút Dừng/Bỏ qua."""
import os
import subprocess
import sys
import tempfile
import time

from uihelp import check, pump, wait_for

GB = 1024 ** 3


def calm(**kw):
    s = {'t': 0, 'mem_total': 16 * GB, 'mem_avail': 8 * GB, 'mem_avail_pct': 50.0, 'swap_total': 8 * GB, 'swap_used': 0, 'swap_pct': 0.0,
         'disks': [{'mount': '/', 'free': 100 * GB, 'total': 200 * GB}], 'procs': [], 'services': [], 'port_owners': {}, 'containers': None}
    s.update(kw)
    return s


class Fake:
    def __init__(self):
        self.s = calm()

    def sample(self):
        return dict(self.s)


def watch_tests(win):
    from devdock.lib import watchdog as W
    svc, app = win.watch, win.get_application()
    fake = Fake()
    svc.sampler, svc.has_docker = fake, False
    notes = []
    win.notify = lambda title, body='', nid=None, urgent=False: notes.append((title, nid, urgent))  # không bắn thông báo thật
    clock = [1000.0]
    svc.engine.clock = lambda: clock[0]
    svc.engine.history.clear()
    svc.engine.last.clear()
    sleeper = None
    try:
        win.select_module('watch')
        pg = win.pages['watch']
        pump(300)
        check('watch page built with 3 tabs', pg.stack.get_visible_child_name() == 'overview' and len(pg.hdr_end) == 1)
        check('service installed + auto-timer off in test mode', svc.timer == 0)

        # ---- tick thủ công với mẫu êm: thẻ hiện số liệu
        svc.tick_now(sync=True)
        pump(100)
        pg.refresh()
        check('cards show sample (RAM 50%)', pg.c_ram[1].get_text() == '50%' and 'GB' in pg.c_disk[1].get_text(), pg.c_ram[1].get_text())
        check('no alerts yet', pg.c_alert[1].get_text() == '0' and not svc.engine.history)

        # ---- ổ đĩa đầy => cảnh báo ngay + lịch sử + thông báo
        fake.s = calm(disks=[{'mount': '/', 'free': 8 * GB, 'total': 200 * GB}])
        svc.tick_now(sync=True)
        pump(100)
        check('disk alert fired immediately', len(svc.engine.history) == 1 and svc.engine.history[0]['key'] == 'disk:/', str(svc.engine.history))
        check('desktop notification requested (urgent for error)', notes and notes[-1][0] == 'Ổ đĩa / sắp đầy' and notes[-1][2] is True, str(notes))
        pg.refresh()
        check('history list shown', pg.hist_stack.get_visible_child_name() == 'list' and pg.hist_info.get_text().startswith('1 '), pg.hist_info.get_text())
        check('24h counter updated', pg.c_alert[1].get_text() == '1', pg.c_alert[1].get_text())
        check('history persisted to disk', len(W.load_history()) == 1 and W.load_history()[0]['title'] == 'Ổ đĩa / sắp đầy')
        n = len(notes)
        svc.tick_now(sync=True)
        check('cooldown: same condition not re-notified', len(notes) == n)

        # ---- công tắc luật lưu bền và có hiệu lực
        fake.s = calm(swap_total=8 * GB, swap_used=int(7.5 * GB), swap_pct=93.0)
        pg.rule_rows['swap_high'].set_enable_expansion(False)
        check('rule switch persisted to watch.json', W.load_config()['rules']['swap_high']['enabled'] is False)
        clock[0] = 2000.0
        svc.tick_now(sync=True)
        clock[0] = 2100.0
        svc.tick_now(sync=True)
        check('disabled rule does not fire', not any(a['key'] == 'swap_high' for a in svc.engine.history))
        pg.rule_rows['swap_high'].set_enable_expansion(True)
        clock[0] = 2200.0
        svc.tick_now(sync=True)
        clock[0] = 2270.0
        svc.tick_now(sync=True)
        check('re-enabled rule fires after 1 minute', any(a['key'] == 'swap_high' for a in svc.engine.history))
        # ngưỡng chỉnh được và lưu bền
        pg.set_rule('ram_low', 'pct', 33)
        check('threshold persisted', W.load_config()['rules']['ram_low']['pct'] == 33)
        pg.set_general('cooldown_min', 5)
        check('cooldown persisted', W.load_config()['cooldown_min'] == 5)
        pg.set_rule('ram_low', 'pct', 10)
        pg.set_general('cooldown_min', 30)

        # ---- tiến trình ngốn: bảng, Bỏ qua, Dừng
        fake.s = calm(procs=[{'pid': os.getpid(), 'name': 'fakehog', 'rss': 3 * GB, 'cpu': 5.0, 'created': 0}])
        clock[0] = 3000.0
        svc.tick_now(sync=True)
        pg.refresh()
        pump(200)
        names = [r.d['name'] for r in pg.table.store]
        check('hog listed before it is old enough to alert', names == ['fakehog'] and not pg.table.store.get_item(0).d['alerted'], str(names))
        check('hog table visible', pg.hog_stack.get_visible_child_name() == 'table')
        pg.table.sel.set_selected(0)
        pump(100)
        check('selection bar shows hog', pg.bar.get_revealed())
        clock[0] = 3301.0
        svc.tick_now(sync=True)
        check('hog alert after 5 minutes', any(a['key'].startswith('hog_mem:') for a in svc.engine.history))
        pg.ignore_selected()
        pump(150)
        check('ignore removes from table + saves', not list(pg.table.store) and 'fakehog' in W.load_config()['ignore'], str(W.load_config()['ignore']))
        check('ignore list rendered', len(pg.ign_rows) == 1)
        pg.unignore('fakehog')
        check('unignore restores', W.load_config()['ignore'] == [] and not pg.ign_rows)

        sleeper = subprocess.Popen(['sleep', '300'])
        fake.s = calm(procs=[{'pid': sleeper.pid, 'name': 'sleep', 'rss': 2 * GB, 'cpu': 0.0, 'created': 0}])
        clock[0] = 4000.0
        svc.tick_now(sync=True)
        pg.refresh()
        pg.do_kill(sleeper.pid, 'sleep')
        check('kill button terminates the process', wait_for(lambda: sleeper.poll() is not None, 5), 'sleep vẫn chạy')

        # ---- dev service: lỗi vs người dùng dừng (ServiceManager thật)
        from devdock.lib import services as S
        tmp = tempfile.gettempdir()
        bad, good = S.new_service('lỗi', tmp, 'exit 3'), S.new_service('ổn', tmp, 'sleep 30')
        fake.s = calm()
        win.store['services'] = [bad, good]
        win.services.start(bad)
        win.services.start(good)
        check('service died by itself', wait_for(lambda: not win.services.is_running(bad['id']), 8))
        win.services.stop(good['id'])
        check('user-stopped service ended', wait_for(lambda: not win.services.is_running(good['id']), 8))
        before = len(svc.engine.history)
        clock[0] = 5000.0
        svc.tick_now(sync=True)
        new = svc.engine.history[:len(svc.engine.history) - before]
        check('crash alerts, user stop is silent', [a['key'] for a in new] == [f"svc:{bad['id']}"] and 'lỗi' in new[0]['title'], str([a['title'] for a in new]))
        win.services.forget(bad['id'])
        win.services.forget(good['id'])
        win.store['services'] = []

        # ---- chạy nền + autostart
        pg.bg_row.set_active(True)
        check('background persisted + app held', W.load_config()['background'] is True and app._held is True)
        check('close hides window instead of quitting', win.close_decision() == 'hide')
        handled = win._on_close()
        pump(150)
        check('close-request intercepted and window hidden', handled is True and not win.get_visible())
        win.present()
        pump(200)
        pg.auto_row.set_active(True)
        p = W.autostart_path()
        check('autostart file created in the isolated config dir', os.path.exists(p) and p.startswith(os.environ['XDG_CONFIG_HOME']), p)
        check('autostart Exec ends with --hidden', any(l.startswith('Exec=') and l.endswith(' --hidden') for l in open(p).read().splitlines()))
        check('autostart subtitle has no warning while background is on', 'chưa bật' not in pg.auto_row.get_subtitle())
        pg.bg_row.set_active(False)
        check('autostart subtitle warns once background is off', 'chưa bật' in pg.auto_row.get_subtitle(), pg.auto_row.get_subtitle()[-90:])
        pg.auto_row.set_active(False)
        check('autostart file removed', not os.path.exists(p))
        check('background off => quit semantics + hold released', W.load_config()['background'] is False and app._held is False and win.close_decision() == 'quit')

        # ---- xoá lịch sử
        pg.clear_history()
        check('clear history', svc.engine.history == [] and W.load_history() == [] and pg.hist_stack.get_visible_child_name() == 'empty')
    finally:
        if sleeper and sleeper.poll() is None:
            sleeper.kill()
        if 'notify' in win.__dict__:
            del win.notify
        svc.engine.clock = time.time


def hidden_flag_tests(win):
    """`devdock --hidden`: ẩn cửa sổ khi đã bật chạy nền; hiện bình thường khi chưa bật."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = ("import sys; sys.path.insert(0, %r)\n"
              "from gi.repository import GLib\nfrom devdock.app import App\napp = App()\n"
              "def look():\n    print('VISIBLE=%%s HELD=%%s' %% (app.win.get_visible() if app.win else None, app._held))\n    app.quit(); return False\n"
              "GLib.timeout_add(2500, look)\napp.run(['devdock', '--hidden'])\n") % root

    def run(background):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, 'cfg', 'devdock'))
        with open(os.path.join(d, 'cfg', 'devdock', 'watch.json'), 'w') as f:
            f.write('{"background": %s}' % ('true' if background else 'false'))
        env = {k: os.environ[k] for k in ('HOME', 'PATH', 'DISPLAY', 'WAYLAND_DISPLAY', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS', 'XDG_SESSION_TYPE') if k in os.environ}
        env.update(XDG_CONFIG_HOME=os.path.join(d, 'cfg'), XDG_DATA_HOME=os.path.join(d, 'data'), DEVDOCK_TEST='1')
        r = subprocess.run(['/usr/bin/python3', '-B', '-c', script], env=env, capture_output=True, text=True, timeout=60)
        return next((l for l in r.stdout.splitlines() if l.startswith('VISIBLE=')), r.stdout + r.stderr[-300:])
    on, off = run(True), run(False)
    check('--hidden + background: window not shown, app held', on == 'VISIBLE=False HELD=True', on)
    check('--hidden without background: window shown (not invisible)', off.startswith('VISIBLE=True') and 'HELD=False' in off, off)


TESTS = [watch_tests, hidden_flag_tests]
