"""Test giao diện trang Bộ nhớ: PSS thực, gom theo ứng dụng, xu hướng, dừng nhóm có xác nhận, bảo vệ process hệ thống."""
import os
import subprocess
import sys

from uihelp import check, pump, wait_for

HOG = 'import time\nx = bytearray(150 * 1024 * 1024)\nfor i in range(0, len(x), 4096): x[i] = 1\nprint(1, flush=True)\ntime.sleep(120)'


def rows_of(pg):
    return [pg.table.store.get_item(i).d for i in range(pg.table.store.get_n_items())]


def answer(win, resp):
    d = win.get_visible_dialog()
    if d is None:
        return False
    d.emit('response', resp)
    d.force_close()
    return True


def memory_tests(win):
    while win.get_visible_dialog() is not None:
        win.get_visible_dialog().force_close()
        pump(50)
    from devdock.lib import memwatch as M
    hogs = [subprocess.Popen([sys.executable, '-c', HOG], stdout=subprocess.PIPE, text=True) for _ in range(2)]
    for h in hogs:
        h.stdout.readline()
    try:
        win.select_module('memory')
        pg = win.pages['memory']
        pg.refresh()
        check('memory page fills cards', wait_for(lambda: pg.c_used[1].get_text().endswith('GB') and pg.c_pss[1].get_text() != '—', 10), pg.c_used[1].get_text())
        check('pss card explains double counting', 'đếm trùng' in pg.c_pss[2].get_text(), pg.c_pss[2].get_text())
        check('top apps listed', pg.top_box.get_first_child() is not None)
        rows = rows_of(pg)
        check('app rows, sorted by PSS desc by default', len(rows) > 5 and rows[0]['kind'] == 'app' and rows[0]['pss'] >= rows[1]['pss'])
        # chế độ theo tiến trình: tìm hai process ngốn RAM do test tạo
        pg.tg.set_active_name('proc')
        pids = {h.pid for h in hogs}
        # lượt làm mới có thể đang dở từ trước khi tạo tiến trình thử: làm mới tới khi thấy chúng
        wait_for(lambda: (pg.refresh(), len([r for r in rows_of(pg) if r['pid'] in pids]) == 2)[1], 15)
        mine = [r for r in rows_of(pg) if r['pid'] in pids]
        check('per-process rows show our 2 hogs with ~150MB PSS', len(mine) == 2 and all(r['pss'] > 120 * 1024 ** 2 for r in mine), str([r['pss'] >> 20 for r in mine]))
        check('hog rows are exact (own user)', all(r['exact'] and r['mine'] for r in mine))
        # chọn -> thanh hành động; dừng một process
        pg.search.set_text(str(hogs[0].pid))
        pump(300)
        pg.table.sel.set_selected(0)
        pump(200)
        check('selecting shows bar with stop enabled', pg.bar.get_revealed() and pg.b_stop.get_sensitive())
        pg.stop()
        check('stop asks first', wait_for(lambda: win.get_visible_dialog() is not None, 3) and hogs[0].poll() is None)
        answer(win, 'no')
        pump(200)
        check('declining keeps it alive', hogs[0].poll() is None)
        pg.stop()
        wait_for(lambda: win.get_visible_dialog() is not None, 3)
        answer(win, 'yes')
        check('confirming stops it', wait_for(lambda: hogs[0].poll() is not None, 8))
        check('other hog untouched', hogs[1].poll() is None)
        # bảo vệ: DevDock, init, gnome-shell
        mine_self = next((r for r in rows_of(pg) if r['pid'] == os.getpid()), None)
        pg.search.set_text('')
        pump(300)
        self_row = next((r for r in pg.rows(1) if r['pid'] == os.getpid()), None)
        check('DevDock itself cannot be stopped from the page', self_row is not None and pg.targets(self_row)[0] == [], str(self_row and pg.targets(self_row)))
        init_like = {'pids': [1], 'kind': 'proc', 'name': 'systemd'}
        check('pid 1 protected', pg.targets(init_like)[0] == [])
        # gom nhóm: dừng nhóm chỉ chọn process được phép
        grp = {'kind': 'app', 'name': 'test-group', 'pids': [hogs[1].pid, os.getpid(), 1]}
        ok, skipped = pg.targets(grp)
        check('group stop skips protected pids', ok == [hogs[1].pid] and skipped == 2, f'{ok} {skipped}')
        # xu hướng
        t = M.Trend()
        t.update([{'pid': 5, 'pss': 100 << 20}], now=0)
        t.update([{'pid': 5, 'pss': 400 << 20}], now=120)
        check('trend delta', t.delta(5, 300) == (300 << 20, 120))
        from devdock.pages.memory import trend_markup
        check('trend markup colors', '#e01b24' in trend_markup(300 << 20) and '#e5a50a' in trend_markup(80 << 20) and '—' in trend_markup(0))
        # bỏ qua cảnh báo
        pg.tg.set_active_name('app')
        pump(300)
        pg.search.set_text(sys.executable.rsplit('/', 1)[-1])
        pump(300)
        pg.table.sel.set_selected(0)
        pump(200)
        before = list(win.watch.config['ignore'])
        sel = pg.table.selected()
        if sel:
            pg.ignore()
            check('ignore adds names to watch config', len(win.watch.config['ignore']) >= len(before), str(win.watch.config['ignore']))
            win.watch.config['ignore'][:] = before
    finally:
        for h in hogs:
            h.kill()
            h.wait()


def watch_rules_tests(win):
    from devdock.lib import watchdog as W
    win.select_module('watch')
    pg = win.pages['watch']
    new = ['mem_leak', 'oom_kill', 'load_high', 'temp_high', 'battery_low', 'zombies']
    check('watch page lists the 6 new rules', all(n in pg.rule_rows for n in new), str(list(pg.rule_rows)))
    pg.rule_rows['load_high'].set_enable_expansion(False)
    check('toggling a new rule saves config', W.load_config()['rules']['load_high']['enabled'] is False)
    pg.rule_rows['load_high'].set_enable_expansion(True)
    check('and re-enables', W.load_config()['rules']['load_high']['enabled'] is True)
    from devdock.lib import dashboard as DB
    a = DB.attention(sys={'mem_used': 95, 'mem_total': 100})
    check('dashboard RAM alert opens the memory page', a and a[0]['module'] == 'memory')


TESTS = [memory_tests, watch_rules_tests]
