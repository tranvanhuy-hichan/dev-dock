"""Test giao diện module Git: điều khiển thật trên repo tạm + remote bare (không đụng repo của người dùng)."""
import os
import shutil
import subprocess
import tempfile

from uihelp import check, pump, wait_for

ENV = {'GIT_AUTHOR_NAME': 'T', 'GIT_AUTHOR_EMAIL': 't@x.y', 'GIT_COMMITTER_NAME': 'T', 'GIT_COMMITTER_EMAIL': 't@x.y'}


def sh(path, *a):
    subprocess.run(['git', '-C', path, *a], check=True, capture_output=True, env={**os.environ, **ENV})


def w(path, rel, text):
    p = os.path.join(path, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w') as f:
        f.write(text)


def git_tests(win):
    os.environ.update(ENV)  # commit trong ứng dụng cần danh tính
    tmp = os.path.realpath(tempfile.mkdtemp(prefix='ddui-git-'))
    remote = os.path.join(tmp, 'remote.git')
    subprocess.run(['git', 'init', '-q', '--bare', '-b', 'main', remote], check=True)
    root = os.path.join(tmp, 'projects')
    repo = os.path.join(root, 'alpha')
    os.makedirs(repo)
    sh(repo, 'init', '-q', '-b', 'main')
    w(repo, 'a.txt', 'one\n')
    sh(repo, 'add', '.')
    sh(repo, 'commit', '-qm', 'first commit')
    sh(repo, 'remote', 'add', 'origin', remote)
    sh(repo, 'push', '-q', '-u', 'origin', 'main')
    # repo thứ hai có commit chưa push
    repo2 = os.path.join(root, 'beta')
    subprocess.run(['git', 'clone', '-q', remote, repo2], check=True)
    w(repo2, 'b.txt', 'b\n')
    sh(repo2, 'add', '.')
    sh(repo2, 'commit', '-qm', 'beta unpushed')
    try:
        win.store['project_roots'] = [root]
        win.select_module('git')
        pg = win.pages['git']
        pg.repos, pg.repo = [], None
        pg.refresh_repos(True)
        check('git page has header controls', pg.hdr_title is pg.tg and len(pg.hdr_end) == 2)
        check('repos listed', wait_for(lambda: len(pg.repos) == 2, 10), str([r['name'] for r in pg.repos]))
        check('first repo auto-selected', wait_for(lambda: pg.repo is not None and pg.st is not None, 10))
        # chọn alpha
        for r in pg._rows():
            if r.repo['name'] == 'alpha':
                pg.list.select_row(r)
        check('alpha selected + clean', wait_for(lambda: pg.repo['name'] == 'alpha' and pg.st and pg.st['files'] == [], 10))
        check('commit buttons disabled when clean', not pg.b_commit.get_sensitive())
        # ---- thay đổi: tạo file, stage, xem diff, commit
        w(repo, 'a.txt', 'one\ntwo\n')
        w(repo, 'new.txt', 'brand new\n')
        pg.refresh_status()
        check('changes detected', wait_for(lambda: pg.st and {(f['path'], f['area']) for f in pg.st['files']} == {('a.txt', 'unstaged'), ('new.txt', 'untracked')}, 10), str(pg.st and pg.st['files']))
        check('list has 2 files', pg.files.count() == 2)
        check('grouped: area headers + dir headers + files', pg.files.kinds() == ['area', 'dir', 'file', 'area', 'dir', 'file'] or pg.files.kinds().count('file') == 2 and 'area' in pg.files.kinds() and 'dir' in pg.files.kinds(), str(pg.files.kinds()))
        pg.files.select_path('a.txt')
        check('selection bar revealed', wait_for(lambda: pg.fbar.get_revealed(), 3))
        txt = lambda tv: tv.get_buffer().get_text(tv.get_buffer().get_start_iter(), tv.get_buffer().get_end_iter(), False)
        check('diff shown with added line', wait_for(lambda: '+two' in txt(pg.diff_tv), 8), txt(pg.diff_tv)[:80])
        start = pg.diff_tv.get_buffer().get_tag_table().lookup('add')
        check('diff has colour tags', start is not None)
        check('stage button visible for unstaged', pg.b_stage.get_visible() and not pg.b_unstage.get_visible())
        pg.stage_sel()
        check('stage works', wait_for(lambda: pg.st and ('a.txt', 'staged') in {(f['path'], f['area']) for f in pg.st['files']}, 10))
        check('commit disabled without message', not pg.b_commit.get_sensitive())
        pg.commit_buf.set_text('thêm dòng two', -1)
        check('commit enabled with staged + message', wait_for(lambda: pg.b_commit.get_sensitive(), 3))
        pg.stage_all()
        check('stage all', wait_for(lambda: pg.st and all(f['area'] == 'staged' for f in pg.st['files']) and len(pg.st['files']) == 2, 10))
        pg.unstage_all()
        check('unstage all', wait_for(lambda: pg.st and all(f['area'] != 'staged' for f in pg.st['files']), 10))
        pg.stage_all()
        wait_for(lambda: pg.st and all(f['area'] == 'staged' for f in pg.st['files']), 10)
        pg.commit(False)
        check('commit done + message cleared', wait_for(lambda: pg.st and pg.st['files'] == [] and pg.st['ahead'] == 1, 12), str(pg.st))
        pump(300)
        check('commit box cleared', pg.commit_buf.get_char_count() == 0)
        check('push button shows ahead count', 'Push ↑1' == pg.b_push.get_label(), pg.b_push.get_label())
        # ---- push
        pg.b_push.emit('clicked')
        check('push works', wait_for(lambda: pg.st and pg.st['ahead'] == 0, 15), str(pg.st))
        # ---- hoàn tác có xác nhận (gọi thao tác thật sau khi "xác nhận")
        w(repo, 'a.txt', 'dirty\n')
        pg.refresh_status()
        wait_for(lambda: pg.st and pg.st['files'], 8)
        d = {'path': 'a.txt', 'area': 'unstaged', 'code': 'M', 'key': ('unstaged', 'a.txt')}
        pg.op(lambda p: __import__('devdock.lib.gitops', fromlist=['x']).discard(p, d), 'Đã hoàn tác')
        check('discard restores file', wait_for(lambda: open(os.path.join(repo, 'a.txt')).read() == 'one\ntwo\n', 8))
        # ---- lịch sử
        pg.tg.set_active_name('history')
        check('history lists commits', wait_for(lambda: pg.hist.store.get_n_items() == 2, 10), str(pg.hist.store.get_n_items()))
        pg.hist.sel.set_selected(0)
        check('commit detail loaded', wait_for(lambda: 'thêm dòng two' in txt(pg.hist_diff_tv) and '+two' in txt(pg.hist_diff_tv), 8), txt(pg.hist_diff_tv)[:100])
        pg.hist_search.set_text('FIRST')
        check('history search (case-insensitive)', wait_for(lambda: pg.hist.store.get_n_items() == 1, 8))
        pg.hist_search.set_text('')
        # ---- nhánh
        pg.tg.set_active_name('branches')
        check('branches listed (local + remote)', wait_for(lambda: pg.br.store.get_n_items() >= 2, 10))
        pg.op(lambda p: __import__('devdock.lib.gitops', fromlist=['x']).checkout(p, 'topic', create=True), 'tạo')
        check('new branch is current', wait_for(lambda: pg.st and pg.st['branch'] == 'topic', 10))
        w(repo, 't.txt', 't\n')
        sh(repo, 'add', '.')
        sh(repo, 'commit', '-qm', 'topic work')
        sh(repo, 'switch', '-q', 'main')
        sh(repo, 'merge', '-q', 'topic')
        pg.load_branches()
        check('merged branch flagged', wait_for(lambda: any(r.d['name'] == 'topic' and r.d['merged'] for r in pg.br.store), 8))
        idx = next(i for i in range(pg.br.sorted.get_n_items()) if pg.br.sorted.get_item(i).d['name'] == 'topic')
        pg.br.sel.set_selected(idx)
        check('delete enabled for merged', wait_for(lambda: pg.b_delbr.get_sensitive() and pg.b_checkout.get_sensitive(), 4))
        idx = next(i for i in range(pg.br.sorted.get_n_items()) if pg.br.sorted.get_item(i).d['name'] == 'main')
        pg.br.sel.set_selected(idx)
        check('current branch: checkout/delete disabled', wait_for(lambda: not pg.b_checkout.get_sensitive() and not pg.b_delbr.get_sensitive(), 4))
        pg.op(lambda p: __import__('devdock.lib.gitops', fromlist=['x']).delete_branch(p, 'topic'), 'xoá')
        check('branch deleted', wait_for(lambda: not any(r.d['name'] == 'topic' for r in pg.br.store), 10))
        check('invalid branch name rejected in UI path', not __import__('devdock.lib.gitops', fromlist=['x']).valid_ref('--force'))
        # ---- stash
        w(repo, 'a.txt', 'stash me\n')
        pg.tg.set_active_name('stash')
        pg.stash_msg.set_text('wip stash')
        pg.stash_push()
        check('stash created', wait_for(lambda: pg.stash.store.get_n_items() == 1, 12))
        check('worktree clean after stash', open(os.path.join(repo, 'a.txt')).read() == 'one\ntwo\n')
        pg.stash.sel.set_selected(0)
        check('stash diff shown', wait_for(lambda: 'stash me' in txt(pg.stash_diff_tv), 8), txt(pg.stash_diff_tv)[:80])
        pg.stash_do('pop')
        check('stash pop restores + empties list', wait_for(lambda: pg.stash.store.get_n_items() == 0 and open(os.path.join(repo, 'a.txt')).read() == 'stash me\n', 12))
        # ---- đẩy tất cả (alpha đã merge topic cục bộ nên đồng bộ trước; chỉ còn beta chưa push)
        sh(repo, 'push', '-q')
        sh(repo2, 'pull', '-q', '--rebase')  # beta bắt kịp remote (alpha vừa push) rồi mới còn 1 commit chưa push
        from devdock.pages.gitpage import PushAllDialog
        dlg = PushAllDialog(pg)
        dlg.present(win)
        check('push-all lists repo with unpushed commits', wait_for(lambda: len(dlg.rows) == 1 and 'beta' in list(dlg.rows)[0], 10), str(list(dlg.rows)))
        check('push-all default all selected', all(c.get_active() for _r, c, _p in dlg.rows.values()) and dlg.go.get_sensitive())
        dlg.start()
        check('push-all finished ok', wait_for(lambda: not dlg.running and 'Xong: 1/1' in dlg.info.get_text(), 15), dlg.info.get_text())
        out = subprocess.run(['git', '-C', remote, 'log', '--format=%s', '-3', 'main'], capture_output=True, text=True).stdout
        check('remote received beta commit', 'beta unpushed' in out, out)
        dlg.close()
        # ---- dọn
    finally:
        win.store['project_roots'] = []
        shutil.rmtree(tmp, ignore_errors=True)


def git_group_tests(win):
    """Danh sách thay đổi nhóm theo khu vực → thư mục → file, gập/mở được, giữ chọn lựa khi làm mới."""
    os.environ.update(ENV)
    tmp = os.path.realpath(tempfile.mkdtemp(prefix='ddui-gitgrp-'))
    root = os.path.join(tmp, 'projects')
    repo = os.path.join(root, 'gamma')
    os.makedirs(repo)
    sh(repo, 'init', '-q', '-b', 'main')
    for rel in ('root.txt', 'src/a.py', 'src/b.py', 'docs/guide.md'):
        w(repo, rel, 'line1\n')
    sh(repo, 'add', '.')
    sh(repo, 'commit', '-qm', 'base')
    try:
        win.store['project_roots'] = [root]
        win.select_module('git')
        pg = win.pages['git']
        pg.repos, pg.repo = [], None
        pg.refresh_repos(True)
        wait_for(lambda: pg.repo is not None and pg.st is not None, 10)
        # staged: src/a.py ; unstaged: src/b.py, root.txt ; mới: docs/new.md, src/sub/c.py
        w(repo, 'src/a.py', 'line1\nplus\nplus2\n')
        sh(repo, 'add', 'src/a.py')
        w(repo, 'src/b.py', 'changed\n')
        w(repo, 'root.txt', 'line1\nmore\n')
        w(repo, 'docs/new.md', 'n\n')
        w(repo, 'src/sub/c.py', 'c\n')
        pg.refresh_status()
        check('status has 5 files', wait_for(lambda: pg.st and len(pg.st['files']) == 5 and pg.files.count() == 5, 10), str(pg.files.count()))
        kinds = pg.files.kinds()
        check('3 area headers (staged, unstaged, untracked)', kinds.count('area') == 3, str(kinds))
        dirs = [r.dir for r in pg.files.lb_rows() if r.kind == 'dir']
        check('dir headers grouped per area, root first', dirs == ['src', '', 'src', 'docs', 'src/sub'], str(dirs))
        pg.files.select_path('b.py') or pg.files.select_path('src/b.py')
        check('selection works in grouped list', pg.files.selected() and pg.files.selected()['path'] == 'src/b.py')
        txt = []
        for r in pg.files.lb_rows():
            if r.kind == 'file' and r.item['path'] == 'src/a.py':
                line = r.get_child()
                txt.append(' '.join(getattr(c, 'get_label', lambda: '')() or '' for c in _children(line)))
        check('numstat shown for staged file (+2 −0)', txt and '+2' in txt[0] and '0' in txt[0], str(txt))
        # gập/mở: bấm tiêu đề khu vực "Chưa stage" ẩn file bên dưới, mở lại thì hiện
        area_row = next(r for r in pg.files.lb_rows() if r.kind == 'area' and r.area == 'unstaged')
        files_unstaged = [r for r in pg.files.lb_rows() if r.kind == 'file' and r.area == 'unstaged']
        pg.files._toggle(None, area_row)
        pump(100)
        check('collapsing area hides its files', all(not r.get_child_visible() for r in files_unstaged) and files_unstaged)
        check('other areas remain visible', all(r.get_child_visible() for r in pg.files.lb_rows() if r.kind == 'file' and r.area != 'unstaged'))
        pg.refresh_status()
        pump(1500)
        check('collapse state survives refresh', all(not r.get_child_visible() for r in pg.files.lb_rows() if r.kind == 'file' and r.area == 'unstaged'))
        pg.files._toggle(None, next(r for r in pg.files.lb_rows() if r.kind == 'area' and r.area == 'unstaged'))
        pump(100)
        check('expanding shows files again', all(r.get_child_visible() for r in pg.files.lb_rows() if r.kind == 'file' and r.area == 'unstaged'))
        # gập một thư mục
        dir_row = next(r for r in pg.files.lb_rows() if r.kind == 'dir' and r.area == 'untracked' and r.dir == 'src/sub')
        pg.files._toggle(None, dir_row)
        pump(100)
        check('collapsing a dir hides only its files', not next(r for r in pg.files.lb_rows() if r.kind == 'file' and r.item['path'] == 'src/sub/c.py').get_child_visible()
              and next(r for r in pg.files.lb_rows() if r.kind == 'file' and r.item['path'] == 'docs/new.md').get_child_visible())
        # chọn lại giữ nguyên sau khi làm mới
        pg.files.select_path('src/b.py', 'unstaged')
        pg.refresh_status()
        pump(1500)
        sel = pg.files.selected()
        check('selection survives refresh', sel and sel['path'] == 'src/b.py' and sel['area'] == 'unstaged', str(sel))
        # stage một file thì nó chuyển sang nhóm "Đã stage"
        pg.stage_sel()
        check('staged file moves to staged group', wait_for(lambda: any(r.item['path'] == 'src/b.py' and r.item['area'] == 'staged' for r in pg.files.lb_rows() if r.kind == 'file'), 10))
    finally:
        win.store['project_roots'] = []
        shutil.rmtree(tmp, ignore_errors=True)


def _children(box):
    c = box.get_first_child()
    while c:
        yield c
        c = c.get_next_sibling()


TESTS = [git_tests, git_group_tests]
