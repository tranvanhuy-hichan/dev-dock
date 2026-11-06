import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from devdock.lib import envfiles as E  # noqa: E402


class ParseTest(unittest.TestCase):
    def test_formats(self):
        text = '# comment\nA=1\nexport B="hello world"  # inline\nC=\'single # not comment\'\nD=val # trailing\nE=\n\nF=a=b=c\nG="line1\nline2"\nH="say \\"hi\\""\n'
        d = {e['key']: e['value'] for e in E.parse(text)}
        self.assertEqual(d['A'], '1')
        self.assertEqual(d['B'], 'hello world')
        self.assertEqual(d['C'], 'single # not comment')
        self.assertEqual(d['D'], 'val')
        self.assertEqual(d['E'], '')
        self.assertEqual(d['F'], 'a=b=c')
        self.assertEqual(d['G'], 'line1\nline2')
        self.assertEqual(d['H'], 'say "hi"')
        self.assertNotIn('# comment', d)

    def test_url_value_with_hash_kept(self):
        d = {e['key']: e['value'] for e in E.parse('URL=http://x/#anchor\n')}
        self.assertEqual(d['URL'], 'http://x/#anchor')  # '#' không có khoảng trắng phía trước là một phần giá trị

    def test_secret_and_mask(self):
        self.assertTrue(E.is_secret('DB_PASSWORD'))
        self.assertTrue(E.is_secret('STRIPE_API_KEY'))
        self.assertTrue(E.is_secret('X', 'postgres://user:pw@host/db'))
        self.assertFalse(E.is_secret('PORT', '3000'))
        self.assertEqual(E.mask(''), '')
        self.assertNotIn('abc', E.mask('abcdef'))


class EditTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, '.env')
        with open(self.p, 'w') as f:
            f.write('# header\nA=1\nexport B=2\n\nC="x y"\n')
        os.chmod(self.p, 0o600)

    def test_set_replace_and_append_keep_comments(self):
        E.set_var(self.p, 'A', 'new value')
        E.set_var(self.p, 'B', '9')
        E.set_var(self.p, 'Z', 'last')
        t = E.read(self.p)
        self.assertIn('# header', t)
        self.assertIn('A="new value"', t)
        self.assertIn('export B=9', t)
        self.assertTrue(t.endswith('Z=last\n'))
        self.assertEqual(E.load(self.p)['A'], 'new value')

    def test_roundtrip_special_chars(self):
        for v in ('a b', 'with "quote"', 'back\\slash', 'hash # tag', "it's", '$HOME', 'multi\nline', ''):
            E.set_var(self.p, 'K', v)
            self.assertEqual(E.load(self.p)['K'], v, repr(v))

    def test_delete_and_permissions_preserved(self):
        E.delete_var(self.p, 'A')
        self.assertNotIn('A', E.load(self.p))
        self.assertIn('B', E.load(self.p))
        self.assertEqual(stat.S_IMODE(os.stat(self.p).st_mode), 0o600)
        self.assertEqual([f for f in os.listdir(self.d) if f.startswith('.env-edit')], [], 'không để lại file tạm')

    def test_bad_key_rejected_and_file_untouched(self):
        before = E.read(self.p)
        for bad in ('1A', 'A B', 'A=B', '', 'A\nB=1'):
            with self.assertRaises(ValueError):
                E.set_var(self.p, bad, 'x')
        self.assertEqual(E.read(self.p), before)

    def test_add_missing(self):
        self.assertEqual(E.add_missing(self.p, [('A', 'x'), ('NEW1', 'v'), ('NEW2', '')]), 2)
        d = E.load(self.p)
        self.assertEqual((d['A'], d['NEW1'], d['NEW2']), ('1', 'v', ''))
        self.assertEqual(E.add_missing(self.p, [('NEW1', 'z')]), 0)


class DiscoverTest(unittest.TestCase):
    def test_find_and_diff(self):
        with tempfile.TemporaryDirectory() as root:
            app = os.path.join(root, 'app')
            os.makedirs(os.path.join(app, 'node_modules', 'x'))
            os.makedirs(os.path.join(root, 'api'))
            for p, c in (('app/.env', 'A=1\nB=2\n'), ('app/.env.example', 'A=\nB=\nC=\n'), ('app/node_modules/x/.env', 'Z=1'),
                         ('api/.env.production', 'P=1\n'), ('api/notenv.txt', '')):
                with open(os.path.join(root, p), 'w') as f:
                    f.write(c)
            files = E.find_env_files([root])
            rel = sorted(os.path.relpath(f, os.path.realpath(root)) for f in files)
            self.assertEqual(rel, ['api/.env.production', 'app/.env', 'app/.env.example'])
            real = [f for f in files if f.endswith('app/.env')][0]
            ex = E.example_for(real, files)
            self.assertTrue(ex.endswith('.env.example'))
            self.assertEqual(E.missing_keys(real, ex), ['C'])
            rows = E.diff({'A': '1', 'B': '2', 'X': 'x'}, {'A': '1', 'B': '3', 'Y': 'y'})
            self.assertEqual([(r[0], r[1]) for r in rows], [('B', 'different'), ('X', 'only_a'), ('Y', 'only_b'), ('A', 'same')])


if __name__ == '__main__':
    unittest.main()
