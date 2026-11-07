import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from devdock import cli  # noqa: E402
from devdock.store import Store  # noqa: E402


def w(path, text=''):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(text)


class AddServicesTest(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp())
        os.makedirs(os.path.join(self.root, 'app', '.git'))
        w(os.path.join(self.root, 'app', 'ready', 'package.json'), json.dumps({'scripts': {'dev': 'vite'}}))
        os.makedirs(os.path.join(self.root, 'app', 'ready', 'node_modules'))
        w(os.path.join(self.root, 'app', 'notready', 'package.json'), json.dumps({'scripts': {'dev': 'vite'}}))
        w(os.path.join(self.root, 'app', 'desk', 'package.json'), json.dumps({'scripts': {'start': 'electron .'}}))
        self.store = Store(os.path.join(tempfile.mkdtemp(), 'settings.json'))
        self.store['project_roots'] = [self.root]

    def test_adds_only_ready_defaults_and_is_idempotent(self):
        lines = []
        n = cli.add_services(self.store, out=lines.append)
        self.assertEqual(n, 1)
        self.assertEqual([s['cwd'].split(os.sep)[-1] for s in self.store['services']], ['ready'])
        self.assertTrue(any('notready' in l and 'bỏ qua' in l for l in lines))
        self.assertTrue(any('desk' in l and 'bỏ qua' in l for l in lines))
        self.assertEqual(cli.add_services(self.store, out=lambda *_: None), 0)  # chạy lại không thêm trùng
        self.assertEqual(len(self.store['services']), 1)

    def test_all_flag_and_dry_run_and_persistence(self):
        self.assertEqual(cli.add_services(self.store, only_ready=False, dry_run=True, out=lambda *_: None), 3)
        self.assertEqual(self.store['services'], [])
        self.assertEqual(cli.add_services(self.store, only_ready=False, out=lambda *_: None), 3)
        again = Store(self.store.path)  # đã ghi ra file
        self.assertEqual(len(again['services']), 3)
        self.assertTrue(all(s['project'] == 'app' and s['id'] for s in again['services']))


if __name__ == '__main__':
    unittest.main()
