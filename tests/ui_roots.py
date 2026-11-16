"""Test hộp thoại Nguồn project: nhiều thư mục gốc, project lẻ ngoài thư mục gốc, độ sâu quét, lưu và áp dụng cho các trang."""
import os
import subprocess
import tempfile

from uihelp import check, pump, wait_for


def mkrepo(path):
    os.makedirs(path)
    subprocess.run(['git', '-C', path, 'init', '-q'], check=True, capture_output=True)


def answer(win):
    d = win.get_visible_dialog()
    if d is not None:
        d.force_close()


def roots_tests(win):
    from devdock.lib import projects as P
    from devdock.pages.projects import RootsDialog
    ra, rb, lone = tempfile.mkdtemp(prefix='ddui-ra-'), tempfile.mkdtemp(prefix='ddui-rb-'), tempfile.mkdtemp(prefix='ddui-lone-')
    mkrepo(os.path.join(ra, 'alpha'))
    mkrepo(os.path.join(rb, 'beta'))
    mkrepo(os.path.join(ra, 'l1', 'l2', 'l3', 'gamma'))     # sâu 4 cấp: ngoài độ sâu mặc định 3
    plain = os.path.join(lone, 'standalone')                # project lẻ, không có .git/marker
    os.makedirs(plain)
    st = win.store
    saved = (list(st['project_roots']), list(st['project_extra']), st['scan_depth'])
    try:
        st['project_roots'], st['project_extra'], st['scan_depth'] = [], [], 3
        win.select_module('projects')
        pg = win.pages['projects']
        dlg = RootsDialog(pg)
        dlg.present(win)
        pump(200)
        check('empty config shows the default root as a real row', len(dlg.roots) == 1 and dlg.roots[0] == P.default_roots()[0], str(dlg.roots))
        check('dialog has add buttons and depth row', len(dlg.rows) >= 2 and int(dlg.depth.get_value()) == 3)
        # thêm 2 thư mục gốc + 1 project lẻ, bỏ thư mục mặc định
        dlg.roots[:] = [ra, rb]
        dlg.extra[:] = [plain]
        dlg.render()
        check('preview counts projects of the edited config', wait_for(lambda: dlg.found.get_subtitle() == '3 project', 8), dlg.found.get_subtitle())   # alpha, beta, standalone
        dlg.depth.set_value(5)
        check('deeper depth finds nested project', wait_for(lambda: dlg.found.get_subtitle() == '4 project', 8), dlg.found.get_subtitle())
        dlg.remove('extra', plain)
        check('removing a row updates the preview', wait_for(lambda: dlg.found.get_subtitle() == '3 project', 8), dlg.found.get_subtitle())
        dlg.extra.append(plain)
        dlg.depth.set_value(3)
        dlg.render()
        pump(300)
        dlg.save()
        check('save persists roots, extras, depth', st['project_roots'] == [ra, rb] and st['project_extra'] == [plain] and st['scan_depth'] == 3)
        check('projects page lists both roots and the lone project', wait_for(lambda: {p['name'] for p in pg.items} == {'alpha', 'beta', 'standalone'}, 10), str([p['name'] for p in pg.items]))
        check('scan_roots includes the lone project once', P.scan_roots(st) == [ra, rb, plain])
        # các trang khác dùng chung cấu hình
        from devdock.lib import envfiles as E
        open(os.path.join(plain, '.env'), 'w').write('K=1\n')
        check('env scanner follows the lone project', os.path.join(plain, '.env') in E.find_env_files(P.scan_roots(st)))
        win.select_module('git')
        gpg = win.pages['git']
        gpg.refresh_all(True)
        check('git page sees repos from both roots', wait_for(lambda: {'alpha', 'beta'} <= {p['name'] for p in gpg.repos}, 10), str([p['name'] for p in gpg.repos]))
        # độ sâu 5 áp dụng toàn cục
        st['scan_depth'] = 5
        P.scan_roots(st)
        check('depth applies globally', {os.path.basename(x) for x in P.find_projects([ra])} == {'alpha', 'gamma'})
        # '/' bị từ chối
        dlg2 = RootsDialog(pg)
        dlg2.roots[:] = [ra]
        check('dialog never lists "/" by default', '/' not in dlg2.roots)
        answer(win)
    finally:
        st['project_roots'], st['project_extra'], st['scan_depth'] = saved
        P.configure([], 3)
        for _ in range(3):
            answer(win)


TESTS = [roots_tests]
