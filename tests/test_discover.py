import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from devdock.lib import discover as D  # noqa: E402


def w(path, text=''):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(text)


class DiscoverTest(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp())

    def find(self, cands, **kw):
        return [c for c in cands if all(c.get(k) == v for k, v in kw.items())]

    def test_node_scripts_ports_and_readiness(self):
        p = os.path.join(self.root, 'shop')
        w(os.path.join(p, 'web', 'package.json'), json.dumps({'name': 'web', 'scripts': {'dev': 'next dev -p 4003', 'start': 'next start', 'build': 'next build'}}))
        w(os.path.join(p, 'web', 'package-lock.json'))
        os.makedirs(os.path.join(p, 'web', 'node_modules'))
        w(os.path.join(p, 'ui', 'package.json'), json.dumps({'name': 'ui', 'scripts': {'dev': 'vite'}}))
        w(os.path.join(p, 'pnpm-lock.yaml'))
        w(os.path.join(p, 'lib', 'package.json'), json.dumps({'name': 'lib', 'scripts': {'build': 'tsc'}}))  # không có dev/start => bỏ qua
        c = D.discover([p])
        web, ui = self.find(c, name='web')[0], self.find(c, name='ui')[0]
        self.assertEqual((web['cmd'], web['port'], web['ready']), ('npm run dev', 4003, True))
        self.assertEqual((ui['cmd'], ui['port'], ui['ready']), ('pnpm run dev', 5173, False))
        self.assertIn('pnpm install', ui['note'])
        self.assertEqual(len(c), 2)
        self.assertEqual(web['title'], 'shop/web')
        self.assertEqual(ui['title'], 'shop/ui')

    def test_electron_is_desktop_not_default(self):
        p = os.path.join(self.root, 'app')
        w(os.path.join(p, 'package.json'), json.dumps({'name': 'app', 'scripts': {'start': 'env -u X electron . --no-sandbox'}}))
        c = D.discover([p])[0]
        self.assertEqual((c['kind'], c['default'], c['port']), ('desktop', False, 0))

    def test_compose_ports_env_defaults_and_infra(self):
        p = os.path.join(self.root, 'stack')
        w(os.path.join(p, 'docker-compose.yml'), 'services:\n  db:\n    image: postgres\n    ports: ["${PG:-5433}:5432"]\n  api:\n    build: .\n    ports:\n      - "127.0.0.1:8787:8787"\n  web:\n    image: x\n    ports: ["3000:3000/tcp"]\n')
        w(os.path.join(p, '.env.example'), 'A=1')
        c = D.discover([p])
        full = self.find(c, name='docker compose')[0]
        self.assertEqual((full['cmd'], full['port']), ('docker compose up', 3000))
        self.assertFalse(full['ready'])  # thiếu .env
        infra = [x for x in c if x['name'].startswith('hạ tầng')][0]
        self.assertEqual((infra['cmd'], infra['port']), ('docker compose up db', 5433))
        self.assertEqual(D.compose_ports(os.path.join(p, 'docker-compose.yml')), [('db', 5433), ('api', 8787), ('web', 3000)])

    def test_makefile_skips_detached_targets(self):
        p = os.path.join(self.root, 'mk')
        w(os.path.join(p, 'Makefile'), 'dev: ## chạy db\n\tdocker compose up -d --wait db\n\nrun: ## chạy server\n\tpython app.py\n\nup:\n\techo hi\n')
        c = D.discover([p])
        self.assertEqual([x['cmd'] for x in c], ['make run'])
        self.assertEqual(c[0]['note'], 'chạy server')

    def test_fastapi_django_spring_scripts(self):
        p = os.path.join(self.root, 'api')
        w(os.path.join(p, 'backend', 'app', 'main.py'), 'from fastapi import FastAPI\napp = FastAPI()\n')
        w(os.path.join(p, 'backend', 'pyproject.toml'))
        w(os.path.join(p, '.venv', 'bin', 'uvicorn'))
        w(os.path.join(p, 'site', 'manage.py'))
        w(os.path.join(p, 'be', 'pom.xml'), '<dependency>spring-boot-starter</dependency>')
        w(os.path.join(p, 'be', 'mvnw'))
        w(os.path.join(p, 'start.sh'))
        by = {x['name']: x for x in D.discover([p])}
        self.assertEqual(by['FastAPI']['cmd'], '../.venv/bin/uvicorn app.main:app --reload --port 8000')
        self.assertEqual(by['FastAPI']['cwd'], os.path.join(p, 'backend'))
        names = set(by)
        self.assertTrue({'FastAPI', 'Django runserver', 'Spring Boot', './start.sh'} <= names, names)
        sp = by['Spring Boot']
        self.assertEqual((sp['cmd'], sp['port']), ('./mvnw spring-boot:run', 8080))

    def test_fastapi_factory_and_module_level_create_app(self):
        p = os.path.join(self.root, 'f')
        w(os.path.join(p, 'a', 'app', 'main.py'), 'from fastapi import FastAPI\n\ndef create_app() -> FastAPI:\n    app = FastAPI()\n    return app\n')
        w(os.path.join(p, 'b', 'app', 'main.py'), 'from fastapi import FastAPI\n\ndef create_app() -> FastAPI:\n    return FastAPI()\n\napp = create_app()\n')
        by = {x['sub']: x for x in D.discover([p])}
        self.assertEqual(by['a']['cmd'], 'uvicorn app.main:create_app --factory --reload --port 8000')
        self.assertEqual(by['b']['cmd'], 'uvicorn app.main:app --reload --port 8000')

    def test_absolute_missing_path_in_script_flags_not_ready(self):
        p = os.path.join(self.root, 'x')
        w(os.path.join(p, 'package.json'), json.dumps({'name': 'x', 'scripts': {'dev': 'concurrently "vite" "bash /home/nonexistent-user/api.sh"'}}))
        os.makedirs(os.path.join(p, 'node_modules'))
        c = D.discover([p])[0]
        self.assertFalse(c['ready'])
        self.assertIn('/home/nonexistent-user/api.sh', c['note'])

    def test_skips_generated_dirs_and_packages(self):
        p = os.path.join(self.root, 'mono')
        w(os.path.join(p, 'node_modules', 'x', 'package.json'), json.dumps({'scripts': {'dev': 'x'}}))
        w(os.path.join(p, 'packages', 'ui', 'package.json'), json.dumps({'scripts': {'dev': 'x'}}))
        w(os.path.join(p, '.next', 'package.json'), json.dumps({'scripts': {'dev': 'x'}}))
        self.assertEqual(D.discover([p]), [])

    def test_to_service_and_duplicates(self):
        p = os.path.join(self.root, 'one')
        w(os.path.join(p, 'package.json'), json.dumps({'name': 'one', 'scripts': {'dev': 'vite --port 5175'}}))
        c = D.discover([p])[0]
        s = D.to_service(c)
        self.assertEqual((s['cmd'], s['port'], s['project'], s['cwd']), ('npm run dev', 5175, 'one', p))
        self.assertTrue(D.already_added(c, [s]))
        self.assertFalse(D.already_added(c, []))


if __name__ == '__main__':
    unittest.main()
