import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from devdock.lib import projects as P  # noqa: E402


def w(path, text):
    with open(path, 'w') as f:
        f.write(text)


def git(d, *a):
    subprocess.run(['git', '-C', d, '-c', 'user.email=a@b.c', '-c', 'user.name=T', *a], check=True, capture_output=True)


class ParseTest(unittest.TestCase):
    def test_status_lines(self):
        s = P.parse_status('## main...origin/main [ahead 2, behind 1]\n M a.py\nA  b.py\n?? c.txt\n')
        self.assertEqual((s['branch'], s['upstream'], s['ahead'], s['behind'], s['dirty'], s['untracked']), ('main', 'origin/main', 2, 1, 2, 1))
        self.assertEqual(P.parse_status('## dev\n')['branch'], 'dev')
        self.assertEqual(P.parse_status('## No commits yet on master\n')['branch'], 'master')
        self.assertTrue(P.parse_status('## HEAD (no branch)\n')['detached'])
        self.assertEqual(P.parse_status('')['dirty'], 0)

    def test_ago(self):
        import time
        self.assertEqual(P.ago(0), '—')
        self.assertEqual(P.ago(time.time() - 5), 'vừa xong')
        self.assertIn('ngày', P.ago(time.time() - 86400 * 3))


class RealRepoTest(unittest.TestCase):
    def test_find_and_inspect(self):
        with tempfile.TemporaryDirectory() as root:
            a = os.path.join(root, 'group', 'a')
            b = os.path.join(root, 'b')
            os.makedirs(a)
            os.makedirs(b)
            git(a, 'init', '-q', '-b', 'main')
            w(os.path.join(a, 'package.json'), '{}')
            git(a, 'add', '.')
            git(a, 'commit', '-qm', 'first commit')
            w(os.path.join(a, 'new.txt'), 'x')           # untracked
            w(os.path.join(a, 'package.json'), '{"x":1}')  # sửa
            w(os.path.join(b, 'Cargo.toml'), '')          # không có git
            os.makedirs(os.path.join(a, 'node_modules', 'inner', '.git'))  # không được coi là project
            found = P.find_projects([root])
            self.assertEqual(sorted(found), sorted([os.path.realpath(a), os.path.realpath(b)]))
            ps = {p['name']: p for p in P.scan([root])}
            self.assertEqual((ps['a']['branch'], ps['a']['dirty'], ps['a']['untracked'], ps['a']['lang']), ('main', 1, 1, 'Node'))
            self.assertEqual(ps['a']['last_msg'], 'first commit')
            self.assertFalse(ps['b']['git'])
            self.assertEqual(ps['b']['lang'], 'Rust')

    def test_malicious_fsmonitor_not_executed(self):
        with tempfile.TemporaryDirectory() as d:
            git(d, 'init', '-q')
            marker = os.path.join(d, 'pwned')
            with open(os.path.join(d, '.git', 'config'), 'a') as f:
                f.write(f'[core]\n\tfsmonitor = touch {marker}; echo\n')
            P.inspect(d)
            self.assertFalse(os.path.exists(marker), 'git config của repo không được phép chạy lệnh')

    def test_build_dirs_follow_cleaner_rules(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, 'node_modules'))
            os.makedirs(os.path.join(d, 'bin'))  # không có .csproj => không phải thư mục build
            self.assertEqual([os.path.basename(x) for x, _ in P.build_dirs(d)], ['node_modules'])


if __name__ == '__main__':
    unittest.main()
