import json
import os
import stat
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from devdock.lib import depscan as D  # noqa: E402


def w(path, text=''):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(text)


NPM_OUTDATED = json.dumps({
    'react': {'current': '17.0.2', 'wanted': '17.0.2', 'latest': '19.1.0', 'dependent': 'app', 'location': '/x/node_modules/react'},
    'lodash': {'current': '4.17.20', 'wanted': '4.17.21', 'latest': '4.17.21', 'dependent': 'app'},
    'vite': {'current': '5.0.0', 'wanted': '5.4.1', 'latest': '5.4.1', 'dependent': 'app'},
    'dup': [{'current': '1.0.0', 'wanted': '1.0.0', 'latest': '2.0.0'}, {'current': '1.1.0', 'wanted': '1.1.0', 'latest': '2.0.0'}],
    'missing': {'wanted': '1.2.3', 'latest': '1.2.4', 'dependent': 'app'},
})

NPM_AUDIT = json.dumps({
    'auditReportVersion': 2,
    'vulnerabilities': {
        'minimist': {'name': 'minimist', 'severity': 'critical', 'isDirect': False, 'via': [{'source': 1, 'name': 'minimist', 'title': 'Prototype Pollution in minimist', 'url': 'https://github.com/advisories/GHSA-xvch', 'severity': 'critical'}],
                     'range': '<1.2.6', 'fixAvailable': True},
        'mkdirp': {'name': 'mkdirp', 'severity': 'critical', 'via': ['minimist'], 'range': '0.4.1 - 0.5.1', 'fixAvailable': {'name': 'mkdirp', 'version': '1.0.4', 'isSemVerMajor': True}},
        'tough': {'name': 'tough', 'severity': 'moderate', 'via': [{'title': 'ReDoS in tough-cookie', 'url': 'u', 'severity': 'moderate'}], 'fixAvailable': False},
    },
    'metadata': {'vulnerabilities': {'info': 0, 'low': 0, 'moderate': 1, 'high': 0, 'critical': 2, 'total': 3}},
})


class VersionTest(unittest.TestCase):
    def test_bump(self):
        self.assertEqual(D.bump('17.0.2', '19.1.0'), 'major')
        self.assertEqual(D.bump('5.0.0', '5.4.1'), 'minor')
        self.assertEqual(D.bump('4.17.20', '4.17.21'), 'patch')
        self.assertEqual(D.bump('1.0.0', '1.0.0'), '')
        self.assertEqual(D.bump('2.0.0', '1.0.0'), '')
        self.assertEqual(D.bump('^1.2.3', '2.0.0'), 'major')
        self.assertEqual(D.bump('', ''), '')


class ParserTest(unittest.TestCase):
    def test_npm_outdated_real_shape(self):
        out = D.parse_npm_outdated(NPM_OUTDATED, {'react': 'prod', 'vite': 'dev'})
        by = {}
        for e in out:
            by.setdefault(e['name'], []).append(e)
        self.assertEqual(by['react'][0]['bump'], 'major')
        self.assertEqual(by['react'][0]['type'], 'prod')
        self.assertEqual(by['vite'][0]['type'], 'dev')
        self.assertEqual(by['vite'][0]['bump'], 'minor')
        self.assertEqual(by['lodash'][0]['bump'], 'patch')
        self.assertEqual(len(by['dup']), 2)  # nhiều vị trí -> mỗi vị trí một dòng
        self.assertEqual(by['missing'][0]['current'], '')  # chưa cài
        self.assertEqual(by['missing'][0]['bump'], 'patch')
        self.assertEqual([e['bump'] for e in out][:3], ['major', 'major', 'major'])  # sắp xếp major trước

    def test_empty_and_noise(self):
        self.assertEqual(D.parse_npm_outdated(''), [])
        self.assertEqual(D.parse_npm_outdated('{}'), [])
        noisy = 'bash: cannot set terminal process group\nnpm warn config global `--global`\n' + NPM_OUTDATED + '\n'
        self.assertEqual(len(D.parse_npm_outdated(noisy)), 6)
        self.assertEqual(D.extract_json('xx [1,2] yy'), [1, 2])
        self.assertIsNone(D.extract_json('no json here {broken'))

    def test_network_errors(self):
        for txt in ('npm error code ENOTFOUND\nnpm error errno ENOTFOUND\nnpm error network request to https://registry.npmjs.org/x failed, reason: getaddrinfo ENOTFOUND registry.npmjs.org',
                    'npm error code EAI_AGAIN', 'getaddrinfo EAI_AGAIN registry.npmjs.org'):
            with self.assertRaises(D.NetError):
                D.parse_npm_outdated(txt)
            with self.assertRaises(D.NetError):
                D.parse_npm_audit(txt)
        err_json = json.dumps({'error': {'code': 'ENOTFOUND', 'summary': 'request to https://registry.npmjs.org failed, reason: getaddrinfo ENOTFOUND', 'detail': ''}})
        with self.assertRaises(D.NetError):
            D.parse_npm_audit(err_json)
        with self.assertRaises(D.NetError):
            D.parse_npm_outdated(err_json)
        with self.assertRaises(D.NetError):
            D.parse_pip_outdated('WARNING: Retrying ... Failed to establish a new connection: [Errno -3] Temporary failure in name resolution')

    def test_npm_audit(self):
        a = D.parse_npm_audit(NPM_AUDIT)
        self.assertEqual(a['counts'], {'critical': 2, 'high': 0, 'moderate': 1, 'low': 0, 'info': 0, 'total': 3})
        self.assertEqual([v['name'] for v in a['vulns']], ['minimist', 'mkdirp', 'tough'])
        self.assertEqual(a['vulns'][0]['title'], 'Prototype Pollution in minimist')
        self.assertEqual(a['vulns'][0]['fix'], 'có')
        self.assertEqual(a['vulns'][1]['fix'], 'mkdirp@1.0.4 (major)')
        self.assertIn('minimist', a['vulns'][1]['title'])  # chỉ phụ thuộc gián tiếp
        self.assertEqual(a['vulns'][2]['fix'], '')
        self.assertEqual(D.parse_npm_audit('{"vulnerabilities":{},"metadata":{"vulnerabilities":{"critical":0,"total":0}}}')['counts']['total'], 0)

    def test_audit_errors(self):
        with self.assertRaises(D.CheckError) as e:
            D.parse_npm_audit(json.dumps({'error': {'code': 'ENOLOCK', 'summary': 'This command requires an existing lockfile.'}}))
        self.assertIn('lockfile', str(e.exception))

    def test_pnpm(self):
        o = D.parse_pnpm_outdated(json.dumps({'react': {'current': '18.0.0', 'latest': '19.0.0', 'wanted': '18.3.1', 'isDeprecated': False, 'dependencyType': 'dependencies'},
                                              'vite': {'current': '5.0.0', 'latest': '5.1.0', 'wanted': '5.1.0', 'dependencyType': 'devDependencies'}}))
        self.assertEqual([(e['name'], e['type'], e['bump']) for e in o], [('react', 'prod', 'major'), ('vite', 'dev', 'minor')])
        a = D.parse_npm_audit(json.dumps({'advisories': {'1': {'module_name': 'foo', 'severity': 'high', 'title': 'Bad', 'patched_versions': '>=2.0.0', 'vulnerable_versions': '<2.0.0', 'url': 'u'}},
                                          'metadata': {'vulnerabilities': {'info': 0, 'low': 0, 'moderate': 0, 'high': 1, 'critical': 0}}}))
        self.assertEqual((a['counts']['high'], a['vulns'][0]['fix']), (1, '>=2.0.0'))

    def test_yarn_classic_ndjson(self):
        out = '{"type":"info","data":"Color legend"}\n{"type":"table","data":{"head":["Package","Current","Wanted","Latest","Package Type","URL"],"body":[["left-pad","1.0.0","1.0.0","2.0.0","dependencies","u"],["jest","29.0.0","29.7.0","29.7.0","devDependencies","u"]]}}\n'
        o = D.parse_yarn_outdated(out)
        self.assertEqual([(e['name'], e['type'], e['bump']) for e in o], [('left-pad', 'prod', 'major'), ('jest', 'dev', 'minor')])
        self.assertEqual(D.parse_yarn_outdated('{"type":"success","data":"Done"}\n'), [])
        aud = ('{"type":"auditAdvisory","data":{"resolution":{"id":1},"advisory":{"module_name":"lodash","severity":"high","title":"Prototype Pollution","patched_versions":">=4.17.21","vulnerable_versions":"<4.17.21","url":"u"}}}\n'
               '{"type":"auditSummary","data":{"vulnerabilities":{"info":0,"low":0,"moderate":0,"high":1,"critical":0},"dependencies":10}}\n')
        a = D.parse_yarn_audit(aud)
        self.assertEqual((a['counts']['high'], a['counts']['total'], a['vulns'][0]['name']), (1, 1, 'lodash'))

    def test_pip(self):
        o = D.parse_pip_outdated('[{"name": "Django", "version": "4.2.0", "latest_version": "5.1.0", "latest_filetype": "wheel"}, {"name": "requests", "version": "2.31.0", "latest_version": "2.31.1", "latest_filetype": "wheel"}]',
                                 {'django': 'prod'})
        self.assertEqual([(e['name'], e['type'], e['bump']) for e in o], [('Django', 'prod', 'major'), ('requests', 'transitive', 'patch')])
        self.assertEqual(D.parse_pip_outdated('[]'), [])
        a = D.parse_pip_audit(json.dumps({'dependencies': [{'name': 'django', 'version': '3.0', 'vulns': [{'id': 'PYSEC-1', 'fix_versions': ['3.2.1'], 'description': 'sqli'}]}, {'name': 'x', 'version': '1', 'vulns': []}]}))
        self.assertEqual((a['counts']['total'], a['vulns'][0]['fix']), (1, '3.2.1'))


class DetectTest(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp())

    def test_node_python_detection(self):
        p = os.path.join(self.root, 'shop')
        os.makedirs(os.path.join(p, '.git'))
        w(os.path.join(p, 'web', 'package.json'), json.dumps({'dependencies': {'react': '^18'}, 'devDependencies': {'vite': '^5'}}))
        w(os.path.join(p, 'web', 'pnpm-lock.yaml'))
        os.makedirs(os.path.join(p, 'web', 'node_modules'))
        w(os.path.join(p, 'ui', 'package.json'), json.dumps({'dependencies': {'x': '1'}}))
        w(os.path.join(p, 'empty', 'package.json'), json.dumps({'name': 'nodeps', 'scripts': {}}))
        w(os.path.join(p, 'api', 'requirements.txt'), '# c\nfastapi==0.100\nuvicorn[standard]>=0.2\n-r other.txt\n')
        w(os.path.join(p, 'api', '.venv', 'bin', 'python'))
        w(os.path.join(p, 'lib', 'pyproject.toml'), '[project]\nname="x"\ndependencies=["requests>=2", "pydantic_core"]\n[project.optional-dependencies]\ndev=["pytest"]\n')
        ts = {(t['sub'], t['eco']): t for t in D.find_targets([self.root])}
        self.assertEqual(set(ts), {('web', 'node'), ('ui', 'node'), ('api', 'python'), ('lib', 'python')})
        web = ts[('web', 'node')]
        self.assertEqual((web['pm'], web['lock'], web['installed'], web['deps']), ('pnpm', True, True, 2))
        self.assertEqual(web['declared'], {'react': 'prod', 'vite': 'dev'})
        ui = ts[('ui', 'node')]
        self.assertEqual((ui['pm'], ui['lock'], ui['installed']), ('npm', False, False))
        api = ts[('api', 'python')]
        self.assertEqual((api['installed'], api['deps'], api['venv']), (True, 2, os.path.join(p, 'api', '.venv')))
        lib = ts[('lib', 'python')]
        self.assertEqual((lib['installed'], lib['deps']), (False, 3))
        self.assertEqual(lib['declared']['pytest'], 'dev')
        self.assertEqual(lib['declared']['pydantic-core'], 'prod')
        self.assertEqual(D.install_cmd(web), 'pnpm install')
        self.assertEqual(D.install_cmd(ui), 'npm install')
        self.assertIn('requirements.txt', D.install_cmd(api))
        self.assertIn('-e .', D.install_cmd(lib))
        self.assertEqual(D.label(web), 'shop/web')

    def test_hoisted_node_modules_and_lock(self):
        p = os.path.join(self.root, 'mono')
        os.makedirs(os.path.join(p, '.git'))
        w(os.path.join(p, 'yarn.lock'))
        os.makedirs(os.path.join(p, 'node_modules'))
        w(os.path.join(p, 'app', 'package.json'), json.dumps({'dependencies': {'a': '1'}}))
        t = D.find_targets([self.root])[0]
        self.assertEqual((t['pm'], t['lock'], t['installed']), ('yarn', True, True))


def pid_alive(pid):
    try:
        with open(f'/proc/{pid}/stat') as f:
            return f.read().rsplit(')', 1)[1].split()[0] != 'Z'
    except (OSError, IndexError):
        return False


def make_fake_bin(dirpath):
    """npm/yarn/pnpm giả: hành vi chọn bằng biến FAKE_MODE (ok | offline | sleep | nolock)."""
    script = r'''#!/bin/bash
echo "$0 $@" >> "$FAKE_LOG"
case "$FAKE_MODE" in
  offline) echo "npm error code ENOTFOUND" >&2; echo "npm error network request failed, reason: getaddrinfo ENOTFOUND registry.npmjs.org" >&2; exit 1;;
  sleep) sleep 61 & echo $! > "$FAKE_LOG.sleeppid"; wait; exit 0;;
esac
case "$1" in
  outdated) cat "$FAKE_OUTDATED"; exit 1;;
  audit) cat "$FAKE_AUDIT"; exit 1;;
esac
exit 0
'''
    for name in ('npm', 'pnpm', 'yarn'):
        p = os.path.join(dirpath, name)
        w(p, script)
        os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)


class FakeNpmTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bin = os.path.join(self.tmp, 'bin')
        os.makedirs(self.bin)
        make_fake_bin(self.bin)
        w(os.path.join(self.tmp, 'outdated.json'), NPM_OUTDATED)
        w(os.path.join(self.tmp, 'audit.json'), NPM_AUDIT)
        self.env = {'DEVDOCK_DEPS_PATH_PREFIX': self.bin, 'FAKE_LOG': os.path.join(self.tmp, 'calls.log'), 'FAKE_OUTDATED': os.path.join(self.tmp, 'outdated.json'),
                    'FAKE_AUDIT': os.path.join(self.tmp, 'audit.json'), 'FAKE_MODE': 'ok'}
        self.old = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        self.proj = os.path.join(self.tmp, 'proj')
        w(os.path.join(self.proj, 'package.json'), json.dumps({'dependencies': {'react': '17'}, 'devDependencies': {'vite': '5'}}))
        w(os.path.join(self.proj, 'package-lock.json'), '{}')
        os.makedirs(os.path.join(self.proj, 'node_modules'))
        os.makedirs(os.path.join(self.proj, '.git'))
        self.t = D.find_targets([self.tmp])[0]

    def tearDown(self):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def calls(self):
        return open(self.env['FAKE_LOG']).read() if os.path.exists(self.env['FAKE_LOG']) else ''

    def test_exit_code_1_is_normal_and_results_parsed(self):
        res = D.check_target(self.t, timeout=60)
        self.assertEqual(res['status'], 'ok', res)
        s = D.summarize(res)
        self.assertEqual((s['outdated'], s['major'], s['vulns'], s['serious']), (6, 3, 3, 2))
        self.assertIn('outdated --json', self.calls())
        self.assertIn('audit --json', self.calls())
        self.assertTrue(any(e['name'] == 'vite' and e['type'] == 'dev' for e in res['outdated']))
        md = D.to_markdown(self.t, res)
        self.assertIn('| react |', md)
        self.assertIn('Prototype Pollution in minimist', md)

    def test_offline_is_reported_clearly(self):
        os.environ['FAKE_MODE'] = 'offline'
        res = D.check_target(self.t, timeout=60)
        self.assertEqual(res['status'], 'offline')
        self.assertIn('Không có mạng', res['errors'][0])

    def test_no_lockfile_skips_npm_audit(self):
        os.remove(os.path.join(self.proj, 'package-lock.json'))
        t = D.find_targets([self.tmp])[0]
        res = D.check_target(t, timeout=60)
        self.assertEqual(res['status'], 'ok')
        self.assertNotIn('audit', self.calls())
        self.assertTrue(any('lockfile' in n for n in res['notes']))

    def test_timeout_and_cancel_kill_process_group(self):
        os.environ['FAKE_MODE'] = 'sleep'
        t0 = time.time()
        res = D.check_target(self.t, timeout=2)
        self.assertLess(time.time() - t0, 15)
        self.assertEqual(res['status'], 'error')
        self.assertIn('Quá thời gian', res['errors'][0])
        time.sleep(0.5)
        spid = int(open(self.env['FAKE_LOG'] + '.sleeppid').read())
        self.assertFalse(pid_alive(spid), 'tiến trình con (sleep) phải bị kill cùng nhóm khi hết thời gian')
        cancel = threading.Event()
        out = {}
        th = threading.Thread(target=lambda: out.update(r=D.check_target(self.t, cancel=cancel, timeout=60)))
        th.start()
        time.sleep(1.5)
        cancel.set()
        th.join(15)
        self.assertFalse(th.is_alive())
        self.assertIn('huỷ', out['r']['errors'][0])
        time.sleep(0.5)
        self.assertFalse(pid_alive(int(open(self.env['FAKE_LOG'] + '.sleeppid').read())), 'huỷ phải kill cả tiến trình con')

    def test_bun_unsupported_and_python_without_venv(self):
        t = dict(self.t, pm='bun')
        self.assertEqual(D.check_target(t)['status'], 'unsupported')
        py = {'eco': 'python', 'path': self.proj, 'root': self.proj, 'declared': {}, 'pm': 'pip', 'installed': False}
        self.assertEqual(D.check_target(py)['status'], 'error')


class CacheTest(unittest.TestCase):
    def test_atomic_roundtrip_and_corrupt(self):
        path = os.path.join(tempfile.mkdtemp(), 'sub', 'c.json')
        D.save_result('a', {'x': 1}, path)
        D.save_result('b', {'y': 2}, path)
        self.assertEqual(D.load_cache(path), {'a': {'x': 1}, 'b': {'y': 2}})
        self.assertEqual([f for f in os.listdir(os.path.dirname(path)) if f.startswith('.deps-')], [])
        with open(path, 'w') as f:
            f.write('{bad')
        self.assertEqual(D.load_cache(path), {})
        D.save_result('c', {'z': 3}, path)
        self.assertEqual(D.load_cache(path), {'c': {'z': 3}})


@unittest.skipUnless(os.system("bash -ic 'command -v npm' >/dev/null 2>&1") == 0, 'cần npm thật')
class RealNpmTest(unittest.TestCase):
    def test_real_npm_outdated_on_tiny_tmp_project_no_install(self):
        """npm thật, project trống trong thư mục tạm: không cài gì, không ghi vào project thật. Có thể chạy khi offline => chấp nhận 'offline'."""
        d = tempfile.mkdtemp()
        w(os.path.join(d, 'package.json'), json.dumps({'name': 't', 'version': '1.0.0', 'dependencies': {'left-pad': '1.0.0'}}))
        w(os.path.join(d, 'package-lock.json'), json.dumps({'name': 't', 'lockfileVersion': 3, 'requires': True, 'packages': {'': {'name': 't', 'version': '1.0.0', 'dependencies': {'left-pad': '1.0.0'}}}}))
        os.makedirs(os.path.join(d, '.git'))
        t = D.find_targets([os.path.dirname(d)] if False else [d])
        # find_targets cần d là project: có .git => root chính nó
        t = [x for x in D.find_targets([os.path.dirname(d)]) if x['path'] == os.path.realpath(d)] or t
        self.assertTrue(t)
        res = D.check_target(t[0], timeout=90)
        self.assertIn(res['status'], ('ok', 'offline', 'error'))
        self.assertFalse(os.path.exists(os.path.join(d, 'node_modules')), 'không được cài gói')


if __name__ == '__main__':
    unittest.main()
