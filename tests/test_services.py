import os
import socket
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from devdock.lib import services as S  # noqa: E402


def wait(cond, t=8.0):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


class ManagerTest(unittest.TestCase):
    def setUp(self):
        self.m = S.ServiceManager()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        self.m.shutdown()

    def svc(self, cmd):
        return S.new_service('t', self.tmp, cmd)

    def test_runs_and_captures_output_then_exit(self):
        s = self.svc('echo hello; echo oops 1>&2')
        self.m.start(s)
        self.assertTrue(wait(lambda: self.m.status(s['id'])['state'] != 'running'))
        lines = [ln for _, ln in self.m.runs[s['id']].since(0)[0]]
        self.assertIn('hello', lines)
        self.assertIn('oops', lines)  # stderr được gộp
        self.assertEqual(self.m.status(s['id'])['state'], 'stopped')

    def test_failed_exit_code_flagged(self):
        s = self.svc('exit 3')
        self.m.start(s)
        self.assertTrue(wait(lambda: self.m.status(s['id'])['state'] == 'failed'))
        self.assertEqual(self.m.status(s['id'])['rc'], 3)

    def test_stop_kills_whole_process_tree(self):
        s = self.svc('sleep 300 & sleep 300; wait')
        self.m.start(s)
        self.assertTrue(wait(lambda: self.m.is_running(s['id'])))
        pgid = self.m.runs[s['id']].proc.pid
        time.sleep(0.8)
        self.m.stop(s['id'])
        self.assertTrue(wait(lambda: not self.m.is_running(s['id'])))
        def group_alive():
            try:
                os.killpg(pgid, 0)
                return True
            except ProcessLookupError:
                return False
        self.assertTrue(wait(lambda: not group_alive(), 6), 'tiến trình con còn sót')

    def test_double_start_and_bad_input(self):
        s = self.svc('sleep 30')
        self.m.start(s)
        with self.assertRaises(RuntimeError):
            self.m.start(s)
        with self.assertRaises(RuntimeError):
            self.m.start(S.new_service('x', '/không/tồn/tại', 'ls'))
        with self.assertRaises(RuntimeError):
            self.m.start(S.new_service('x', self.tmp, '   '))

    def test_restart_gets_new_pid(self):
        s = self.svc('sleep 30')
        self.m.start(s)
        pid1 = self.m.status(s['id'])['pid']
        self.m.restart(s)
        self.assertNotEqual(self.m.status(s['id'])['pid'], pid1)

    def test_incremental_logs(self):
        s = self.svc('for i in 1 2 3; do echo l$i; done')
        self.m.start(s)
        self.assertTrue(wait(lambda: self.m.status(s['id'])['state'] != 'running'))
        run = self.m.runs[s['id']]
        first, seq = run.since(0)
        again, _ = run.since(seq)
        self.assertEqual(again, [])
        self.assertGreaterEqual(len(first), 4)


class HelpersTest(unittest.TestCase):
    def test_port_open_and_owner(self):
        srv = socket.socket()
        srv.bind(('127.0.0.1', 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            self.assertTrue(S.port_open(port))
            owner = S.port_owner(port)
            self.assertEqual(owner[0], os.getpid())
        finally:
            srv.close()
        self.assertFalse(S.port_open(port))

    def test_detect_commands(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, 'package.json'), 'w') as f:
                f.write('{"scripts":{"dev":"vite","build":"x","start":"node a"}}')
            open(os.path.join(d, 'pnpm-lock.yaml'), 'w').close()
            open(os.path.join(d, 'docker-compose.yml'), 'w').close()
            cmds = [c for _, c in S.detect_commands(d)]
            self.assertEqual(cmds, ['pnpm run dev', 'pnpm run start', 'docker compose up'])
        self.assertEqual(S.detect_commands('/không/có'), [])

    def test_guess_port(self):
        self.assertEqual(S.guess_port('vite --port 5173', ''), 5173)
        self.assertEqual(S.guess_port('PORT=3000 npm start', ''), 3000)
        self.assertEqual(S.guess_port('npm start', ''), 0)


if __name__ == '__main__':
    unittest.main()
