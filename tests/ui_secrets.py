"""Test giao diện Secrets Scanner: quét repo thật (tạm), che giá trị, .env chưa ignore, thêm .gitignore, bỏ qua."""
import os
import subprocess
import tempfile

from uihelp import check, pump, wait_for

AWS = 'AKIA' + 'Q7XK2M9ZRT4WPL3N'


def rows_of(pg):
    return [pg.table.store.get_item(i).d for i in range(pg.table.store.get_n_items())]


def answer(win, resp):
    d = win.get_visible_dialog()
    if d is None:
        return False
    d.emit('response', resp)
    d.force_close()
    return True


def git(cwd, *a):
    subprocess.run(['git', '-C', cwd, '-c', 'user.name=t', '-c', 'user.email=t@t', *a], check=True, capture_output=True)


def secrets_tests(win):
    while win.get_visible_dialog() is not None:
        win.get_visible_dialog().force_close()
        pump(50)
    from devdock.lib import secrets as S
    root = tempfile.mkdtemp(prefix='ddui-sec-')
    repo = os.path.join(root, 'leaky')
    os.makedirs(os.path.join(repo, 'src'))
    git(repo, 'init', '-q')
    with open(os.path.join(repo, 'src', 'cfg.py'), 'w') as f:
        f.write(f'KEY = "{AWS}"\n')
    with open(os.path.join(repo, '.env'), 'w') as f:
        f.write('DB=1\n')
    git(repo, 'add', 'src')
    git(repo, 'commit', '-qm', 'x')
    clean = os.path.join(root, 'clean')
    os.makedirs(clean)
    git(clean, 'init', '-q')
    open(os.path.join(clean, 'a.txt'), 'w').write('hello\n')
    git(clean, 'add', '.')
    git(clean, 'commit', '-qm', 'x')
    win.store['project_roots'] = [root]
    win.select_module('secrets')
    pg = win.pages['secrets']
    pg.results = {}
    pg.render()
    check('empty state before scan', pg.c_repo[1].get_text() == '—' or pg.c_repo[1].get_text() == '0')
    pg.scan()
    check('scan finishes', wait_for(lambda: not pg.scanning and len(pg.results) == 2, 20), str(list(pg.results)))
    rows = rows_of(pg)
    check('finds committed AWS key', any(r['kind'] == 'AWS access key' and r['severity'] == 'bad' and r['file'] == 'src/cfg.py' for r in rows), str([r['kind'] for r in rows]))
    check('finds unignored .env', any(r['env'] == 'unignored' and r['file'] == '.env' for r in rows))
    check('full key never shown in table', not any(AWS in str(r) for r in rows) and not any(AWS in str(v) for v in pg.results.values()))
    check('stat cards', pg.c_repo[1].get_text() == '2' and pg.c_bad[1].get_text() == '1', f'{pg.c_repo[1].get_text()} {pg.c_bad[1].get_text()}')
    check('critical strip shown', pg.strip.get_visible())
    check('cache on disk has no full key', os.path.exists(S.cache_path()) and AWS not in open(S.cache_path()).read())
    # lọc
    pg.tg.set_active_name('env')
    pump(200)
    check('env filter', all(r['env'] for r in rows_of(pg)) and len(rows_of(pg)) == 1)
    pg.tg.set_active_name('bad')
    pump(200)
    check('critical filter', [r['kind'] for r in rows_of(pg)] == ['AWS access key'])
    # thêm .gitignore
    pg.tg.set_active_name('env')
    pump(200)
    pg.table.sel.set_selected(0)
    pump(200)
    check('gitignore button visible for env row', pg.b_ign.get_visible() and pg.bar.get_revealed())
    pg.add_gitignore()
    check('gitignore asks first', wait_for(lambda: win.get_visible_dialog() is not None, 3) and not os.path.exists(os.path.join(repo, '.gitignore')))
    answer(win, 'no')
    pump(200)
    check('declining leaves .gitignore untouched', not os.path.exists(os.path.join(repo, '.gitignore')))
    pg.add_gitignore()
    wait_for(lambda: win.get_visible_dialog() is not None, 3)
    answer(win, 'yes')
    check('.gitignore gets the entry', wait_for(lambda: os.path.exists(os.path.join(repo, '.gitignore')) and '.env' in open(os.path.join(repo, '.gitignore')).read(), 3))
    check('env finding disappears after rescan', wait_for(lambda: not any(r['env'] for r in rows_of(pg)), 8))
    check('.env file itself untouched', open(os.path.join(repo, '.env')).read() == 'DB=1\n')
    # bỏ qua
    pg.tg.set_active_name('all')
    pump(200)
    pg.table.sel.set_selected(0)
    pump(200)
    fp = pg.table.selected()['fp']
    pg.skip()
    wait_for(lambda: win.get_visible_dialog() is not None, 3)
    answer(win, 'yes')
    check('skip removes the row and persists', wait_for(lambda: not rows_of(pg), 3) and fp in S.load_ignored())
    pg.scan()
    check('ignored finding stays hidden after rescan', wait_for(lambda: not pg.scanning, 20) and not rows_of(pg))
    # dashboard nhận tóm tắt
    from devdock.lib import dashboard as DB
    check('attention from totals', DB.attention(secrets=S.totals(list(S.load_cache().values()))) == [])
    win.store['project_roots'] = []


TESTS = [secrets_tests]
