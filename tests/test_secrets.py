import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from devdock.lib import secrets as S  # noqa: E402

# khoá giả ghép lúc chạy để file test không chứa chuỗi giống khoá thật
AWS = 'AKIA' + 'Q7XK2M9ZRT4WPL3N'
GH = 'ghp_' + 'aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bC1dE3fG5'
STRIPE = 'sk_' + 'live_' + '51Hx9aKd83LmQwErTy72'


def git(cwd, *a):
    subprocess.run(['git', '-C', cwd, '-c', 'user.name=t', '-c', 'user.email=t@t', *a], check=True, capture_output=True)


class ScanTextTest(unittest.TestCase):
    def kinds(self, text):
        return [k for _l, k, _s, _v in S.scan_text(text)]

    def test_known_formats(self):
        self.assertEqual(self.kinds(f'key = "{AWS}"'), ['AWS access key'])
        self.assertEqual(self.kinds(f'x={GH}'), ['GitHub token'])
        self.assertEqual(self.kinds(f'STRIPE={STRIPE}'), ['Stripe live key'])
        self.assertEqual(self.kinds('-----BEGIN RSA PRIVATE KEY-----'), ['Private key'])  # devdock:ignore

    def test_placeholders_ignored(self):
        for t in ['API_KEY=your-api-key-here', 'TOKEN=${TOKEN}', 'password = "changeme123456"', 'AKIAEXAMPLEEXAMPLE12',
                  'secret: <your-secret-value>', 'api_key = os.environ["API_KEY"]', 'DB=postgres://user:password@localhost/db']:
            self.assertEqual(self.kinds(t), [], t)

    def test_generic_high_entropy(self):
        self.assertEqual(self.kinds('client_secret = "Zq8Lw3Xn7Rb2Vt9Kc4Mj"'), ['Khoá/mật khẩu gán cứng'])  # devdock:ignore
        for code in ['jwtTokenProvider = createProvider(config)', 'self.api_key = settings.openai_api_key', 'const token = localStorage.getItem("authToken")',
                     'refreshToken: string = ""', 'password_hash_algorithm_name', 'access_token = request.headers.get("x")']:
            self.assertEqual(self.kinds(code), [], code)
        self.assertEqual(S.scan_text('token = "Zq8Lw3Xn7Rb2Vt9Kc4Mj"', 'docs/api.md'), [])
        self.assertEqual(S.scan_text('token = "Zq8Lw3Xn7Rb2Vt9Kc4Mj"', 'tests/test_a.py'), [])
        self.assertEqual(self.kinds('token = aaaaaaaaaaaaaaaaaaaa'), [])  # entropy thấp

    def test_db_url_password(self):
        self.assertEqual(self.kinds('url = "postgres://admin:Xk29fQzP81@db.prod.internal:5432/app"'), ['Mật khẩu trong URL'])  # devdock:ignore
        self.assertEqual(self.kinds('url = "postgres://admin:Xk29fQzP81@localhost:5432/app"'), [])

    def test_inline_ignore_marker(self):
        self.assertEqual(self.kinds(f'k = {AWS}  # devdock:ignore'), [])

    def test_mask_never_full(self):
        m = S.mask(GH)
        self.assertNotIn(GH, m)
        self.assertTrue(m.startswith('ghp_') and '…' in m)
        self.assertEqual(S.mask('short'), '••••••••')


class RepoTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        os.environ['XDG_DATA_HOME'] = tempfile.mkdtemp()
        git(self.d, 'init', '-q')

    def w(self, rel, text):
        p = os.path.join(self.d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w') as f:
            f.write(text)

    def test_finds_committed_key_and_env(self):
        self.w('src/config.py', f'AWS = "{AWS}"\n')
        self.w('.env', 'API_TOKEN=Zq8Lw3Xn7Rb2Vt9Kc4Mj\nDEBUG=1\n')
        self.w('.env.worker.example', 'API_TOKEN=Zq8Lw3Xn7Rb2Vt9Kc4Mj\n')
        self.w('.env.example', 'API_TOKEN=\n')
        self.w('README.md', 'hello\n')
        git(self.d, 'add', 'src', '.env', '.env.example', '.env.worker.example', 'README.md')
        git(self.d, 'commit', '-qm', 'x')
        r = S.scan_repo(self.d)
        self.assertEqual([(f['file'], f['kind'], f['tracked']) for f in r['findings'] if f['kind'].startswith('AWS')], [('src/config.py', 'AWS access key', True)])
        envs = {e['file']: e for e in r['env']}
        self.assertEqual(set(envs), {'.env'})
        self.assertEqual((envs['.env']['status'], envs['.env']['severity']), ('tracked', 'bad'))
        self.assertNotIn(AWS, str(r))
        self.assertEqual(r['findings'][0]['severity'], 'bad')

    def test_unignored_then_ignored(self):
        self.w('.env', 'A=1\n')
        r = S.scan_repo(self.d)
        self.assertEqual([(e['file'], e['status']) for e in r['env']], [('.env', 'unignored')])
        self.assertTrue(S.add_to_gitignore(self.d, '.env'))
        self.assertFalse(S.add_to_gitignore(self.d, '.env'))  # idempotent
        self.assertEqual(S.scan_repo(self.d)['env'], [])
        self.assertIn('.env\n', open(os.path.join(self.d, '.gitignore')).read())

    def test_history_env(self):
        self.w('.env', 'A=1\n')
        git(self.d, 'add', '.env'); git(self.d, 'commit', '-qm', 'add')
        git(self.d, 'rm', '-q', '--cached', '.env'); git(self.d, 'commit', '-qm', 'rm')
        self.w('.gitignore', '.env\n')
        r = S.scan_repo(self.d)
        self.assertEqual([(e['file'], e['status']) for e in r['env']], [('.env', 'history')])

    def test_skips_node_modules_lock_and_binary(self):
        self.w('node_modules/x/a.js', f'k="{AWS}"\n')
        self.w('package-lock.json', f'"{AWS}"\n')
        with open(os.path.join(self.d, 'b.bin'), 'wb') as f:
            f.write(b'\0' + AWS.encode())
        git(self.d, 'add', '-f', '.')
        self.assertEqual(S.scan_repo(self.d)['findings'], [])

    def test_ignore_by_fingerprint_and_cache(self):
        self.w('a.py', f'k = "{AWS}"\n')
        git(self.d, 'add', '.')
        r = S.scan_repo(self.d)
        fp = r['findings'][0]['fp']
        S.add_ignored(fp)
        self.assertEqual(S.scan_repo(self.d, S.load_ignored())['findings'], [])
        S.save_result(r)
        self.assertEqual(S.load_cache()[self.d]['findings'][0]['fp'], fp)
        self.assertNotIn(AWS, open(S.cache_path()).read())
        self.assertEqual(S.totals([r])['bad'], 1)

    def test_not_a_repo(self):
        r = S.scan_repo(tempfile.mkdtemp())
        self.assertTrue(r['error'])


if __name__ == '__main__':
    unittest.main()
