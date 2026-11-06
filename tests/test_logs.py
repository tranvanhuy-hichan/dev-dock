import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from devdock.lib import logsrc as L  # noqa: E402
from devdock.lib.services import ServiceManager, new_service  # noqa: E402


def wait(cond, t=6.0):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


class LevelTest(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(L.level_of('2024-01-01 ERROR something broke'), 'error')
        self.assertEqual(L.level_of('Traceback (most recent call last):'), 'error')
        self.assertEqual(L.level_of('request failed with 500'), 'error')
        self.assertEqual(L.level_of('[WARN] disk almost full'), 'warn')
        self.assertEqual(L.level_of('DEBUG cache hit'), 'debug')
        self.assertEqual(L.level_of('INFO started'), 'info')
        self.assertIsNone(L.level_of('plain message'))
        self.assertIsNone(L.level_of('terrific performance'))  # không khớp nhầm "err" trong từ khác
        self.assertEqual(L.level_of('{"level":"error","msg":"x"}'), 'error')
        self.assertEqual(L.level_of('{"level":50,"msg":"x"}'), 'error')
        self.assertEqual(L.level_of('{"severity":"WARNING"}'), 'warn')
        self.assertIsNone(L.level_of('{not json}'))

    def test_query(self):
        m = L.compile_query('Boom')
        self.assertTrue(m('a boom b'))
        self.assertFalse(m('nothing'))
        self.assertTrue(L.compile_query('')('x'))
        self.assertTrue(L.compile_query(r'^\d+ ok', True)('12 ok'))
        with self.assertRaises(ValueError):
            L.compile_query('(', True)


class StreamTest(unittest.TestCase):
    def test_tail_file_follows_new_lines(self):
        with tempfile.NamedTemporaryFile('w', suffix='.log', delete=False) as f:
            f.write('one\ntwo\n')
        try:
            s = L.open_stream(('file', f.name))
            got = []
            self.assertTrue(wait(lambda: (got.extend(s.drain()), 'two' in got)[1]))
            with open(f.name, 'a') as g:
                g.write('three\n')
            self.assertTrue(wait(lambda: (got.extend(s.drain()), 'three' in got)[1]), got)
            s.stop()
            self.assertIsNotNone(s.proc.poll())
        finally:
            os.unlink(f.name)

    def test_missing_file_reports_error(self):
        s = L.open_stream(('file', '/không/có/file.log'))
        self.assertIn('Không phải file', s.error)

    def test_missing_binary_reports_error(self):
        s = L.ProcStream(['/không/có/binary'])
        self.assertTrue(s.closed and 'Không chạy được' in s.error)

    def test_service_stream(self):
        m = ServiceManager()
        svc = new_service('x', tempfile.gettempdir(), 'echo a; echo b')
        m.start(svc)
        s = L.open_stream(('service', svc['id']), m)
        got = []
        self.assertTrue(wait(lambda: (got.extend(s.drain()), 'b' in got)[1]), got)
        self.assertEqual(s.drain(), [])
        m.shutdown()

    def test_argv_builders_do_not_use_shell(self):
        self.assertEqual(L.docker_argv('abc')[:3], ['docker', 'logs', '-f'])
        self.assertEqual(L.file_argv('-weird name')[-2:], ['--', '-weird name'])  # tên bắt đầu bằng '-' không thành tuỳ chọn
        self.assertIn('--user', L.journal_argv(user=True))


if __name__ == '__main__':
    unittest.main()
