"""Test trang Hiệu năng: tự phát hiện năng lực, chỉnh tạm rồi áp dụng một lần (hỏi xác nhận), chế độ nhanh, thông báo thiếu driver. Dùng cây sysfs giả
và bộ chạy giả: KHÔNG gọi pkexec thật, KHÔNG đụng sysfs thật."""
import os

from uihelp import check, pump, wait_for


def answer(win, resp):
    d = win.get_visible_dialog()
    if d is None:
        return False
    d.emit('response', resp)
    d.force_close()
    return True


def reload(pg, cond, timeout=8):
    """Làm mới (ép đồng bộ điều khiển) tới khi điều kiện đúng; lượt làm mới đang chạy dở có thể bỏ qua lời gọi."""
    ok = wait_for(lambda: (pg.refresh(force=True), cond())[1], timeout, 250)
    wait_for(lambda: not pg.busy, 5)    # chờ lượt làm mới ép buộc cuối cùng xong để nó không xoá chỉnh sửa tạm của bước sau
    return ok


def perf_tests(win):
    while win.get_visible_dialog() is not None:
        win.get_visible_dialog().force_close()
        pump(50)
    from test_perf import fake_root, priv_runner, w
    from devdock.lib import perf as F
    root = fake_root()
    calls = []
    inner = priv_runner(root)
    win.select_module('perf')
    pg = win.pages['perf']
    pg.root, pg.runner = root, (lambda argv: (calls.append(argv), inner(argv))[1])
    prof_calls = []
    state = {'cur': 'performance'}

    def fake_prof(argv):
        if argv[1] == 'list':
            return True, ''.join(f"{'*' if p == state['cur'] else ' '} {p}:\n    CpuDriver:\tintel_pstate\n\n" for p in ('performance', 'balanced', 'power-saver'))
        prof_calls.append(argv)
        state['cur'] = argv[2]
        return True, ''
    pg.prof_run = fake_prof
    check('page reads fake machine state', reload(pg, lambda: pg.state is not None and pg.state['temps']['cpu'] == 96.0 and pg.profile == 'performance'))
    check('temp card shows 96°C and hot strip', pg.c_temp[1].get_text() == '96°C' and pg.hot.get_visible(), pg.c_temp[1].get_text())
    check('fan card shows rpm', pg.c_fan[1].get_text() == '3582 RPM', pg.c_fan[1].get_text())
    check('battery card shows capacity and limit', '97%' == pg.c_bat[1].get_text() and 'dừng sạc ở 100%' in pg.c_bat[2].get_text())
    check('msi + battery groups visible, no driver strip', pg.g_msi.get_visible() and pg.g_bat.get_visible() and not pg.msi_strip.get_visible())
    check('combos list the driver modes', pg.choices['fan_mode'] == ['auto', 'silent', 'basic', 'advanced'] and pg.choices['profile'] == ['performance', 'balanced', 'power-saver'])
    check('controls reflect state', pg.r_turbo.get_active() and int(pg.r_max.get_value()) == 100 and pg.r_fan.get_selected() == 0 and not pg.r_cool.get_active())
    # chỉnh tạm
    check('no pending at start', not pg.pending and not pg.bar.get_revealed())
    pg.r_turbo.set_active(False)
    pg.r_max.set_value(70)
    pg.r_fan.set_selected(1)
    pg.r_cool.set_active(True)
    pg.r_chg.set_value(80)
    pump(200)
    check('edits are staged, not applied', len(pg.pending) == 5 and pg.bar.get_revealed() and not calls and F.read_state(root)['cpu']['turbo'] is True, f'{pg.pending} revealed={pg.bar.get_revealed()} calls={len(calls)}')
    check('bar summarizes changes', 'Turbo Boost: tắt' in pg.bar_text.get_text() and 'Dừng sạc ở 80%' in pg.bar_text.get_text(), pg.bar_text.get_text())
    pump(2300)    # làm mới định kỳ không được ghi đè thay đổi đang chỉnh
    check('periodic refresh keeps staged edits', len(pg.pending) == 5 and not pg.r_turbo.get_active())
    pg.r_turbo.set_active(True)
    check('reverting a control drops it from pending', 'turbo' not in pg.pending and len(pg.pending) == 4)
    pg.r_turbo.set_active(False)
    pump(100)
    # áp dụng: hỏi xác nhận trước
    pg.apply_pending()
    check('apply asks first and shows the changes', wait_for(lambda: win.get_visible_dialog() is not None, 3) and not calls)
    answer(win, 'no')
    pump(200)
    check('declining writes nothing', not calls and F.read_state(root)['cpu']['turbo'] is True)
    pg.apply_pending()
    wait_for(lambda: win.get_visible_dialog() is not None, 3)
    answer(win, 'yes')
    check('confirming applies everything with ONE privileged call', wait_for(lambda: len(calls) == 1 and F.read_state(root)['cpu']['turbo'] is False, 8), str(len(calls)))
    after = F.read_state(root)
    check('values really written', after['cpu']['max_pct'] == 70 and after['msi']['fan_mode'] == 'silent' and after['msi']['cooler_boost'] is True and after['battery']['end'] == 80)
    check('pending cleared and UI shows new state', wait_for(lambda: not pg.pending and not pg.bar.get_revealed() and not pg.r_turbo.get_active() and pg.r_fan.get_selected() == 1, 5))
    # chỉ dùng lệnh cố định, không có shell injection
    check('command uses only whitelisted sysfs writes', calls[0][:3] == ['pkexec', '/bin/sh', '-c'] and all(('/sys/devices/' in part or '/sys/class/' in part) for part in calls[0][3].split(' && ')))
    # chế độ nguồn: đổi ngay, không cần quyền root
    n = len(calls)
    pg.r_prof.set_selected(2)
    check('profile change is applied without privilege', wait_for(lambda: prof_calls == [['powerprofilesctl', 'set', 'power-saver']], 5) and len(calls) == n)
    # chế độ nhanh
    calls.clear()
    reload(pg, lambda: pg.profile == 'power-saver', 5)
    pg.apply_preset('max')
    check('preset asks to confirm and warns about heat', wait_for(lambda: win.get_visible_dialog() is not None, 3) and not calls)
    answer(win, 'yes')
    check('preset applies profile + settings', wait_for(lambda: F.read_state(root)['cpu']['turbo'] is True and F.read_state(root)['msi']['shift_mode'] == 'turbo' and state['cur'] == 'performance', 8))
    check('preset made one privileged call', len(calls) == 1)
    reload(pg, lambda: pg.profile == 'performance' and pg.state['msi']['shift_mode'] == 'turbo')
    pg.apply_preset('max')
    pump(300)
    check('applying an already-active preset changes nothing', win.get_visible_dialog() is None and len(calls) == 1)
    # nếu hệ thống từ chối giá trị (driver âm thầm bỏ qua) phải báo lỗi
    calls.clear()
    reload(pg, lambda: pg.state['msi']['fan_mode'] == 'silent')
    orig = pg.runner
    pg.runner = lambda argv: (calls.append(argv), (0, ''))[1]   # "thành công" nhưng không ghi gì
    pg.r_fan.set_selected(3)
    pump(100)
    pg.apply_pending()
    wait_for(lambda: win.get_visible_dialog() is not None, 3)
    answer(win, 'yes')
    wait_for(lambda: len(calls) == 1, 5)
    pump(500)
    check('silently rejected value is detected, state not faked', F.read_state(root)['msi']['fan_mode'] != 'advanced' and pg.r_fan.get_selected() != 3)
    pg.runner = orig
    # polkit huỷ -> không đổi gì
    calls.clear()
    pg.runner = lambda argv: (126, '')
    reload(pg, lambda: not pg.pending)
    pg.r_turbo.set_active(False)
    pg.apply_pending()
    wait_for(lambda: win.get_visible_dialog() is not None, 3)
    answer(win, 'yes')
    pump(600)
    check('cancelled password leaves system unchanged', F.read_state(root)['cpu']['turbo'] is True)
    # thiếu driver msi-ec: ẩn nhóm quạt và hiện hướng dẫn
    root2 = fake_root(msi=False, battery=False)
    pg.root, pg.runner = root2, None
    check('without msi-ec: group hidden and instructions shown', reload(pg, lambda: pg.state['msi'] is None and not pg.g_msi.get_visible() and pg.msi_strip.get_visible() and not pg.g_bat.get_visible()))
    check('without msi-ec: cpu controls still available', pg.g_cpu.get_visible() and pg.r_turbo.get_visible())
    prof, s = F.preset_settings('quiet', pg.state, pg.profs)
    check('presets skip fan settings when unsupported', 'fan_mode' not in s and 'turbo' in s)
    # máy trống hoàn toàn: không lỗi, không điều khiển
    pg.root = os.path.join(root2, 'nothing-here')
    check('machine with nothing supported does not crash', reload(pg, lambda: pg.state['cpu']['turbo'] is None and not pg.g_cpu.get_visible() and pg.c_temp[1].get_text() == '—'))
    pg.root, pg.runner, pg.prof_run = '/', None, None
    w(root, '/x', 'x')


TESTS = [perf_tests]
