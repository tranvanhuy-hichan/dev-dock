"""Test giao diện module Dependency: fixture tạm + npm GIẢ (qua DEVDOCK_DEPS_PATH_PREFIX), không đụng project thật."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from uihelp import check, pump, wait_for  # noqa: E402


def w(path, text=''):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(text)


def deps_tests(win):
    from test_depscan import NPM_AUDIT, NPM_OUTDATED, make_fake_bin, pid_alive
    from devdock.lib import depscan as D
    from devdock.lib import projects as P

    tmp = os.path.realpath(tempfile.mkdtemp(prefix='ddui-deps-'))
    root = os.path.join(tmp, 'work')
    fake = os.path.join(tmp, 'bin')
    os.makedirs(fake)
    make_fake_bin(fake)
    w(os.path.join(tmp, 'outdated.json'), NPM_OUTDATED)
    w(os.path.join(tmp, 'audit.json'), NPM_AUDIT)
    # project A: Node đã cài + lockfile; B: Node chưa cài + lockfile; C: Python không có venv
    for name, installed in (('alpha', True), ('beta', False)):
        p = os.path.join(root, name)
        os.makedirs(os.path.join(p, '.git'))
        w(os.path.join(p, 'package.json'), json.dumps({'dependencies': {'react': '17'}, 'devDependencies': {'vite': '5'}}))
        w(os.path.join(p, 'package-lock.json'), '{}')
        if installed:
            os.makedirs(os.path.join(p, 'node_modules'))
    py = os.path.join(root, 'gamma')
    os.makedirs(os.path.join(py, '.git'))
    w(os.path.join(py, 'requirements.txt'), 'fastapi\nuvicorn\n')

    env = {'DEVDOCK_DEPS_PATH_PREFIX': fake, 'FAKE_LOG': os.path.join(tmp, 'calls.log'), 'FAKE_OUTDATED': os.path.join(tmp, 'outdated.json'),
           'FAKE_AUDIT': os.path.join(tmp, 'audit.json'), 'FAKE_MODE': 'ok'}
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    old_term = P.open_terminal
    opened = []
    P.open_terminal = lambda path=None, cmd=None: opened.append((path, cmd))
    try:
        win.store['project_roots'] = [root]
        win.select_module('deps')
        pg = win.pages['deps']
        pg.rescan(True)
        wait_for(lambda: len(pg.targets) == 3, 15)
        check('deps: targets found', sorted(D.label(t) for t in pg.targets) == ['alpha', 'beta', 'gamma'], str([D.label(t) for t in pg.targets]))
        check('deps: cards (3 targets, 2 not installed)', pg.s_proj[1].get_text() == '3' and pg.s_miss[1].get_text() == '2', f"{pg.s_proj[1].get_text()} {pg.s_miss[1].get_text()}")
        check('deps: header has refresh button', len(pg.hdr_end) == 1)
        # ---- bộ lọc
        pg.dd.set_selected(2)
        pump(200)
        check('deps: filter "chưa cài" shows beta+gamma', pg.table.store.get_n_items() == 2, str(pg.table.store.get_n_items()))
        pg.dd.set_selected(3)
        pump(200)
        check('deps: filter "chưa kiểm tra" shows all 3', pg.table.store.get_n_items() == 3)
        pg.dd.set_selected(0)
        pump(200)
        # ---- kiểm tra tất cả (npm giả: exit code 1 là bình thường)
        pg.check_all_btn.emit('clicked')
        check('deps: button turns into cancel while checking', wait_for(lambda: pg.checking, 3) and pg.check_all_btn.get_label() == 'Huỷ')
        wait_for(lambda: not pg.checking, 40)
        by = {D.label(t): pg.results.get(t['key']) for t in pg.targets}
        check('deps: node results ok', all(by[n] and by[n]['status'] == 'ok' for n in ('alpha', 'beta')), str({k: (v or {}).get('status') for k, v in by.items()}))
        check('deps: python without venv -> clear error', by['gamma'] and by['gamma']['status'] == 'error' and '.venv' in by['gamma']['errors'][0], str(by['gamma']))
        s = D.summarize(by['alpha'])
        check('deps: summary numbers', (s['outdated'], s['major'], s['vulns'], s['serious']) == (6, 3, 3, 2), str(s))
        check('deps: cards updated after check', pg.s_out[1].get_text() == '12' and pg.s_vul[1].get_text() == '6', f"{pg.s_out[1].get_text()} {pg.s_vul[1].get_text()}")
        check('deps: finished UI state restored', pg.check_all_btn.get_label() == 'Kiểm tra tất cả' and not pg.progress.get_visible() and not pg.running)
        check('deps: results cached to disk', D.load_cache().get(next(t['key'] for t in pg.targets if t['sub'] == '' and t['project'] == 'alpha'), {}).get('status') == 'ok')
        # ---- bộ lọc sau kiểm tra
        pg.dd.set_selected(1)
        pump(200)
        check('deps: filter "có vấn đề" shows alpha+beta', pg.table.store.get_n_items() == 2, str(pg.table.store.get_n_items()))
        pg.dd.set_selected(4)
        pump(200)
        check('deps: filter "lỗi" shows gamma', pg.table.store.get_n_items() == 1 and pg.table.sorted.get_item(0).d['t']['project'] == 'gamma')
        pg.dd.set_selected(0)
        pump(200)
        # ---- khung chi tiết
        idx = next(i for i in range(pg.table.sorted.get_n_items()) if pg.table.sorted.get_item(i).d['t']['project'] == 'beta')
        pg.table.sel.set_selected(idx)
        pump(400)
        check('deps: detail opens with outdated + vulns', pg.split.get_show_sidebar() and pg.detail.out_table.store.get_n_items() == 6 and pg.detail.vuln_table.store.get_n_items() == 3)
        check('deps: detail strip warns not installed', pg.detail.strip.get_visible() and 'Chưa cài' in pg.detail.strip.text.get_text(), pg.detail.strip.text.get_text())
        pg.detail.tg.set_active_name('vuln')
        pump(150)
        pg.table.set_rows(pg.table.store and [r.d for r in pg.table.store])  # làm mới bảng không được reset tab chi tiết
        pump(200)
        check('deps: detail tab survives refresh', pg.detail.tg.get_active_name() == 'vuln' and pg.split.get_show_sidebar())
        check('deps: action bar revealed', pg.bar.get_revealed())
        # ---- hành động: cài (không tự chạy, chỉ mở terminal với lệnh cố định) + copy
        pg.open_install()
        check('deps: install opens terminal with fixed command', len(opened) == 1 and opened[0][0].endswith('beta') and 'npm install' in opened[0][1][2] and opened[0][1][:2] == ['bash', '-ic'], str(opened))
        pg.copy_md()
        check('deps: copy markdown does not raise', True)
        # ---- offline: một project báo rõ ràng, không treo
        os.environ['FAKE_MODE'] = 'offline'
        pg.table.sel.set_selected(idx)
        pump(200)
        pg.check_selected()
        wait_for(lambda: not pg.checking, 40)
        r = pg.results[next(t['key'] for t in pg.targets if t['project'] == 'beta')]
        check('deps: offline reported clearly', r['status'] == 'offline' and 'Không có mạng' in r['errors'][0], str(r['status']))
        row = next(x for x in pg.rows() if x['t']['project'] == 'beta')
        check('deps: offline shown in table', 'không có mạng' in pg.out_text(row), pg.out_text(row))
        # ---- huỷ giữa chừng: tiến trình con bị kill, kết quả cũ không bị ghi đè
        os.environ['FAKE_MODE'] = 'sleep'
        before = dict(pg.results)
        pg.check_all_btn.emit('clicked')
        wait_for(lambda: pg.checking, 3)
        pump(1500)
        pg.check_all_btn.emit('clicked')  # lúc này là nút Huỷ
        ok = wait_for(lambda: not pg.checking, 20)
        check('deps: cancel stops quickly', ok)
        check('deps: cancel keeps old results', {k: v['checked_at'] for k, v in pg.results.items()} == {k: v['checked_at'] for k, v in before.items()})
        pump(1000)
        spids = [int(open(env['FAKE_LOG'] + '.sleeppid').read())] if os.path.exists(env['FAKE_LOG'] + '.sleeppid') else []
        check('deps: cancelled child processes are gone (no stray sleep)', bool(spids) and not any(pid_alive(p) for p in spids), str(spids))
    finally:
        P.open_terminal = old_term
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        win.store['project_roots'] = []


TESTS = [deps_tests]
