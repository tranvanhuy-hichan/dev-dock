import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from devdock.lib import gitops as G  # noqa: E402

ENV = {'GIT_AUTHOR_NAME': 'T', 'GIT_AUTHOR_EMAIL': 't@x.y', 'GIT_COMMITTER_NAME': 'T', 'GIT_COMMITTER_EMAIL': 't@x.y', 'GIT_CONFIG_NOSYSTEM': '1'}


def sh(path, *a):
    subprocess.run(['git', '-C', path, *a], check=True, capture_output=True, env={**os.environ, **ENV})


def w(path, rel, text):
    p = os.path.join(path, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w') as f:
        f.write(text)


class Base(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ, ENV)
        self._env.start()
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.remote = os.path.join(self.tmp, 'remote.git')
        subprocess.run(['git', 'init', '-q', '--bare', '-b', 'main', self.remote], check=True)
        self.repo = os.path.join(self.tmp, 'work')
        os.makedirs(self.repo)
        sh(self.repo, 'init', '-q', '-b', 'main')
        w(self.repo, 'a.txt', 'one\n')
        sh(self.repo, 'add', '.')
        sh(self.repo, 'commit', '-qm', 'first')
        sh(self.repo, 'remote', 'add', 'origin', self.remote)
        sh(self.repo, 'push', '-q', '-u', 'origin', 'main')

    def tearDown(self):
        self._env.stop()
        subprocess.run(['rm', '-rf', self.tmp])

    def clone(self, name='other'):
        p = os.path.join(self.tmp, name)
        subprocess.run(['git', 'clone', '-q', self.remote, p], check=True)
        return p


class ParseTest(unittest.TestCase):
    def test_status_v2_records(self):
        raw = ('# branch.oid abc\0# branch.head feat\0# branch.upstream origin/feat\0# branch.ab +2 -1\0'
               '1 M. N... 100644 100644 100644 h h src/a.py\0'
               '1 .M N... 100644 100644 100644 h h b b.txt\0'
               '1 MM N... 100644 100644 100644 h h both.py\0'
               '2 R. N... 100644 100644 100644 h h R100 new.py\0old.py\0'
               'u UU N... 1 2 3 4 h1 h2 h3 conflict.py\0'
               '? new file.txt\0')
        s = G.parse_status_v2(raw)
        self.assertEqual((s['branch'], s['upstream'], s['ahead'], s['behind']), ('feat', 'origin/feat', 2, 1))
        by = [(f['path'], f['area'], f['code']) for f in s['files']]
        self.assertIn(('src/a.py', 'staged', 'M'), by)
        self.assertIn(('b b.txt', 'unstaged', 'M'), by)
        self.assertIn(('both.py', 'staged', 'M'), by)
        self.assertIn(('both.py', 'unstaged', 'M'), by)
        self.assertIn(('new.py', 'staged', 'R'), by)
        self.assertEqual([f['orig'] for f in s['files'] if f['path'] == 'new.py'], ['old.py'])
        self.assertIn(('conflict.py', 'conflict', 'U'), by)
        self.assertIn(('new file.txt', 'untracked', '?'), by)
        self.assertEqual(len(by), 7)

    def test_valid_ref(self):
        for ok in ('main', 'feature/x-1', 'v1.2.3', 'a_b'):
            self.assertTrue(G.valid_ref(ok), ok)
        for bad in ('', '--force', '-x', 'a b', 'a..b', 'a//b', 'x/', 'x.lock', 'a@{1}', 'a;rm', 'a\nb', 'a~1', 'a:b', '$(x)'):
            self.assertFalse(G.valid_ref(bad), bad)

    def test_friendly_errors(self):
        self.assertIn('danh tính', G._friendly('Please tell me who you are', ''))
        self.assertIn('Pull trước', G._friendly(' ! [rejected] main -> main (fetch first)', ''))
        self.assertIn('xác thực', G._friendly('fatal: could not read Username for ...: terminal prompts disabled', ''))


class NumstatTest(Base):
    def test_parse_numstat_formats(self):
        out = G.parse_numstat('3\t1\tsrc/a.py\0-\t-\timg.png\0' + '0\t0\t\0old.txt\0new.txt\0' + '12\t0\tdir with space/x y.txt\0')
        self.assertEqual(out['src/a.py'], (3, 1))
        self.assertEqual(out['img.png'], (None, None))
        self.assertEqual(out['new.txt'], (0, 0))
        self.assertEqual(out['dir with space/x y.txt'], (12, 0))
        self.assertEqual(G.parse_numstat(''), {})

    def test_status_includes_stats_for_staged_and_unstaged(self):
        repo = self.repo
        w(repo, 'f.txt', 'a\nb\nc\n')
        sh(repo, 'add', '.')
        sh(repo, 'commit', '-qm', 'base')
        w(repo, 'f.txt', 'a\nB\nc\nd\ne\n')        # 3 dòng thêm, 1 dòng xoá so với bản commit
        sh(repo, 'add', 'f.txt')
        w(repo, 'f.txt', 'a\nB\nc\nd\ne\nf\n')     # thêm 1 dòng chưa stage
        w(repo, 'new dir/n.txt', 'x\n')
        st = G.status(repo)
        self.assertEqual(st['stats'][('staged', 'f.txt')], (3, 1))
        self.assertEqual(st['stats'][('unstaged', 'f.txt')], (1, 0))
        self.assertNotIn(('untracked', 'new dir/n.txt'), st['stats'])  # file mới chưa theo dõi: không có số dòng


class FlowTest(Base):
    def test_status_stage_unstage_commit(self):
        w(self.repo, 'a.txt', 'one\ntwo\n')
        w(self.repo, 'sub/new.txt', 'x\n')
        st = G.status(self.repo)
        areas = {(f['path'], f['area']) for f in st['files']}
        self.assertEqual(areas, {('a.txt', 'unstaged'), ('sub/new.txt', 'untracked')})
        G.stage(self.repo, ['a.txt', 'sub/new.txt'])
        areas = {(f['path'], f['area']) for f in G.status(self.repo)['files']}
        self.assertEqual(areas, {('a.txt', 'staged'), ('sub/new.txt', 'staged')})
        G.unstage(self.repo, ['sub/new.txt'])
        self.assertIn(('sub/new.txt', 'untracked'), {(f['path'], f['area']) for f in G.status(self.repo)['files']})
        self.assertIn('first', G.log(self.repo)[0]['subject'])
        msg = G.commit(self.repo, 'second  commit')
        self.assertIn('second', msg)
        self.assertEqual(G.log(self.repo)[0]['subject'], 'second  commit')
        self.assertEqual(G.status(self.repo)['ahead'], 1)

    def test_diff_variants(self):
        w(self.repo, 'a.txt', 'one\nchanged\n')
        w(self.repo, 'n.txt', 'brand new\n')
        d = G.file_diff(self.repo, {'path': 'a.txt', 'area': 'unstaged'})
        self.assertIn('+changed', d)
        G.stage(self.repo, ['a.txt'])
        self.assertIn('+changed', G.file_diff(self.repo, {'path': 'a.txt', 'area': 'staged'}))
        self.assertEqual(G.file_diff(self.repo, {'path': 'a.txt', 'area': 'unstaged'}), '')
        self.assertIn('+brand new', G.file_diff(self.repo, {'path': 'n.txt', 'area': 'untracked'}))

    def test_commit_errors(self):
        with self.assertRaises(G.GitError):
            G.commit(self.repo, '   ')
        with self.assertRaises(G.GitError) as e:
            G.commit(self.repo, 'x')
        self.assertIn('stage', str(e.exception))

    def test_missing_identity_is_friendly(self):
        w(self.repo, 'a.txt', 'z\n')
        G.stage(self.repo, ['a.txt'])
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_AUTHOR') and not k.startswith('GIT_COMMITTER')}
        env.update(GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='user.useConfigOnly', GIT_CONFIG_VALUE_0='true', GIT_CONFIG_GLOBAL='/dev/null', HOME=self.tmp)
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(G.GitError) as e:
                G.commit(self.repo, 'x')
        self.assertIn('danh tính', str(e.exception))

    def test_discard_tracked_and_untracked(self):
        w(self.repo, 'a.txt', 'dirty\n')
        w(self.repo, 'junk.txt', 'j\n')
        G.discard(self.repo, {'path': 'a.txt', 'area': 'unstaged'})
        G.discard(self.repo, {'path': 'junk.txt', 'area': 'untracked'})
        self.assertEqual(open(os.path.join(self.repo, 'a.txt')).read(), 'one\n')
        self.assertFalse(os.path.exists(os.path.join(self.repo, 'junk.txt')))
        with self.assertRaises(G.GitError):
            G.discard(self.repo, {'path': 'a.txt', 'area': 'staged'})

    def test_unstage_in_empty_repo(self):
        e = os.path.join(self.tmp, 'empty')
        os.makedirs(e)
        sh(e, 'init', '-q', '-b', 'main')
        w(e, 'f.txt', 'x\n')
        G.stage(e, ['f.txt'])
        self.assertEqual(G.status(e)['files'][0]['area'], 'staged')
        G.unstage(e, ['f.txt'])
        self.assertEqual(G.status(e)['files'][0]['area'], 'untracked')
        self.assertEqual(G.log(e), [])

    def test_push_pull_fetch_ahead_behind(self):
        other = self.clone()
        w(other, 'b.txt', 'from other\n')
        sh(other, 'add', '.')
        sh(other, 'commit', '-qm', 'other commit')
        sh(other, 'push', '-q')
        G.fetch(self.repo)
        self.assertEqual(G.status(self.repo)['behind'], 1)
        self.assertIn('Đã cập nhật', G.pull(self.repo))
        self.assertTrue(os.path.exists(os.path.join(self.repo, 'b.txt')))
        w(self.repo, 'c.txt', 'mine\n')
        G.stage(self.repo, ['c.txt'])
        G.commit(self.repo, 'mine')
        self.assertEqual(G.status(self.repo)['ahead'], 1)
        self.assertIn('Đã push', G.push(self.repo))
        self.assertEqual(G.status(self.repo)['ahead'], 0)
        self.assertEqual(G.pull(self.repo), 'Đã là mới nhất')

    def test_push_rejected_not_forced_and_pull_diverged(self):
        other = self.clone()
        w(other, 'o.txt', 'o\n')
        sh(other, 'add', '.')
        sh(other, 'commit', '-qm', 'o')
        sh(other, 'push', '-q')
        w(self.repo, 'm.txt', 'm\n')
        G.stage(self.repo, ['m.txt'])
        G.commit(self.repo, 'm')
        with self.assertRaises(G.GitError) as e:
            G.push(self.repo)
        self.assertIn('Pull trước', str(e.exception))
        G.fetch(self.repo)
        with self.assertRaises(G.GitError) as e2:
            G.pull(self.repo)
        self.assertIn('phân kỳ', str(e2.exception))
        # remote không bị ghi đè
        self.assertEqual(subprocess.run(['git', '-C', self.remote, 'log', '--format=%s', '-1'], capture_output=True, text=True).stdout.strip(), 'o')

    def test_push_sets_upstream_for_new_branch(self):
        G.checkout(self.repo, 'feature/x', create=True)
        w(self.repo, 'f.txt', 'f\n')
        G.stage(self.repo, ['f.txt'])
        G.commit(self.repo, 'f')
        self.assertIsNone(G.status(self.repo)['upstream'])
        self.assertIn('đặt upstream', G.push(self.repo))
        self.assertEqual(G.status(self.repo)['upstream'], 'origin/feature/x')

    def test_force_push_is_blocked(self):
        for args in (('push', '--force'), ('push', '-f', 'origin', 'main'), ('push', '--force-with-lease'), ('push', '--delete', 'origin', 'x')):
            with self.assertRaises(G.GitError, msg=str(args)):
                G.run(self.repo, *args, write=True)

    def test_push_all(self):
        r2 = self.clone('second')
        w(self.repo, 'p1.txt', '1\n')
        sh(self.repo, 'add', '.')
        sh(self.repo, 'commit', '-qm', 'p1')
        w(r2, 'p2.txt', '2\n')
        sh(r2, 'add', '.')
        sh(r2, 'commit', '-qm', 'p2')
        seen = []
        res = G.push_all([self.repo, r2], on_result=lambda *a: seen.append(a))
        self.assertEqual([r[1] for r in res], [True, False])  # repo thứ hai bị từ chối vì remote đã có commit mới (không force)
        self.assertEqual(len(seen), 2)


class HistoryBranchStashTest(Base):
    def test_log_search_show(self):
        for i in range(3):
            w(self.repo, f'f{i}.txt', f'{i}\n')
            sh(self.repo, 'add', '.')
            sh(self.repo, 'commit', '-qm', f'commit número {i} [x]')
        self.assertEqual(len(G.log(self.repo)), 4)
        self.assertEqual(len(G.log(self.repo, n=2)), 2)
        hit = G.log(self.repo, query='NÚMERO 1')  # không phân biệt hoa/thường, có dấu
        self.assertEqual([c['subject'] for c in hit], ['commit número 1 [x]'])
        self.assertEqual(len(G.log(self.repo, query='[x]')), 3)  # chuỗi cố định, không phải regex
        c = G.log(self.repo)[0]
        self.assertEqual((c['author'], len(c['sha']), c['ts'] > 0), ('T', 40, True))
        self.assertIn('HEAD', c['refs'])
        sh_txt = G.show(self.repo, c['sha'])
        self.assertIn('+2', sh_txt)
        for bad in ('--output=/tmp/x', 'HEAD; rm', '', 'zz'):
            with self.assertRaises(G.GitError):
                G.show(self.repo, bad)

    def test_branches_checkout_delete(self):
        G.checkout(self.repo, 'topic', create=True)
        w(self.repo, 't.txt', 't\n')
        sh(self.repo, 'add', '.')
        sh(self.repo, 'commit', '-qm', 'topic work')
        G.checkout(self.repo, 'main')
        bs = {b['name']: b for b in G.branches(self.repo)}
        self.assertTrue(bs['main']['current'])
        self.assertFalse(bs['topic']['merged'])
        self.assertIn('origin/main', bs)
        self.assertTrue(bs['origin/main']['remote'])
        self.assertEqual(bs['main']['upstream'], 'origin/main')
        with self.assertRaises(G.GitError):
            G.delete_branch(self.repo, 'topic')  # chưa merge => -d từ chối
        sh(self.repo, 'merge', '-q', 'topic')
        self.assertTrue({b['name']: b for b in G.branches(self.repo)}['topic']['merged'])
        G.delete_branch(self.repo, 'topic')
        self.assertNotIn('topic', {b['name'] for b in G.branches(self.repo)})
        with self.assertRaises(G.GitError):
            G.delete_branch(self.repo, 'main')  # đang đứng trên nhánh

    def test_checkout_remote_creates_tracking(self):
        other = self.clone()
        sh(other, 'switch', '-q', '-c', 'rb')
        w(other, 'r.txt', 'r\n')
        sh(other, 'add', '.')
        sh(other, 'commit', '-qm', 'rb')
        sh(other, 'push', '-q', '-u', 'origin', 'rb')
        G.fetch(self.repo)
        G.checkout(self.repo, 'origin/rb', remote=True)
        st = G.status(self.repo)
        self.assertEqual((st['branch'], st['upstream']), ('rb', 'origin/rb'))

    def test_bad_branch_names_rejected(self):
        for bad in ('--force', '-b', 'a b', 'x;y', '', 'a..b'):
            for fn in (lambda n: G.checkout(self.repo, n), lambda n: G.checkout(self.repo, n, create=True), lambda n: G.delete_branch(self.repo, n)):
                with self.assertRaises(G.GitError, msg=bad):
                    fn(bad)
        self.assertEqual(G.status(self.repo)['branch'], 'main')

    def test_checkout_blocked_by_dirty_changes_is_friendly(self):
        G.checkout(self.repo, 'other', create=True)
        w(self.repo, 'a.txt', 'other branch\n')
        sh(self.repo, 'commit', '-qam', 'x')
        G.checkout(self.repo, 'main')
        w(self.repo, 'a.txt', 'dirty local\n')
        with self.assertRaises(G.GitError) as e:
            G.checkout(self.repo, 'other')
        self.assertIn('stash', str(e.exception))

    def test_stash_cycle(self):
        w(self.repo, 'a.txt', 'stashed change\n')
        w(self.repo, 'u.txt', 'untracked\n')
        with self.assertRaises(G.GitError):
            G.stash_push(self.repo, 'x') if not G.status(self.repo)['files'] else (_ for _ in ()).throw(G.GitError('skip'))
        G.stash_push(self.repo, 'my stash', include_untracked=True)
        self.assertEqual(G.status(self.repo)['files'], [])
        sl = G.stash_list(self.repo)
        self.assertEqual(len(sl), 1)
        self.assertIn('my stash', sl[0]['message'])
        self.assertIn('stashed change', G.stash_show(self.repo, 'stash@{0}'))
        G.stash_apply(self.repo, 'stash@{0}')
        self.assertTrue(G.status(self.repo)['files'])
        self.assertEqual(len(G.stash_list(self.repo)), 1)
        sh(self.repo, 'checkout', '-q', '--', '.')
        sh(self.repo, 'clean', '-fq')
        G.stash_pop(self.repo, 'stash@{0}')
        self.assertEqual(G.stash_list(self.repo), [])
        G.stash_push(self.repo, 'again', include_untracked=True)
        G.stash_drop(self.repo, 'stash@{0}')
        self.assertEqual(G.stash_list(self.repo), [])

    def test_stash_nothing_and_bad_ref(self):
        with self.assertRaises(G.GitError):
            G.stash_push(self.repo, 'nothing')
        for bad in ('stash@{0};rm', '--all', 'HEAD', 'stash@{x}', ''):
            for fn in (G.stash_apply, G.stash_pop, G.stash_drop, G.stash_show):
                with self.assertRaises(G.GitError, msg=bad):
                    fn(self.repo, bad)


class SafetyTest(Base):
    def test_malicious_fsmonitor_not_executed_by_status(self):
        marker = os.path.join(self.tmp, 'pwned')
        with open(os.path.join(self.repo, '.git', 'config'), 'a') as f:
            f.write(f'[core]\n\tfsmonitor = touch {marker}; echo\n')
        G.status(self.repo)
        G.log(self.repo)
        G.branches(self.repo)
        self.assertFalse(os.path.exists(marker))

    def test_paths_with_null_or_empty_rejected(self):
        for fn in (G.stage, G.unstage):
            for bad in ([], [''], ['a\0b']):
                with self.assertRaises(G.GitError):
                    fn(self.repo, bad)

    def test_not_a_repo_and_missing_dir(self):
        with self.assertRaises(G.GitError):
            G.status(self.tmp)
        with self.assertRaises(G.GitError):
            G.status('/không/tồn/tại')


if __name__ == '__main__':
    unittest.main()
