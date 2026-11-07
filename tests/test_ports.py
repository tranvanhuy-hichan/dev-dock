import os
import socket
import subprocess
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from devdock.lib import ports as P  # noqa: E402


def raw(port, pid=0, name='x', proto='TCP', ip='0.0.0.0'):
    return {'port': port, 'protocol': proto, 'address': ip, 'pid': pid, 'process': name}


class ParseTest(unittest.TestCase):
    def test_container_ports(self):
        d = P.parse_container_ports(['0.0.0.0:5433->5432/tcp', '[::]:5433->5432/tcp', '80/tcp', '0.0.0.0:8000-8002->9000-9002/tcp'])
        self.assertEqual(d[5433], (5432, 'TCP'))
        self.assertEqual(d[8001], (9001, 'TCP'))
        self.assertNotIn(80, d)

    def test_project_of_longest(self):
        self.assertEqual(P.project_of('/h/p/a/src', ['/h/p', '/h/p/a']), '/h/p/a')
        self.assertEqual(P.project_of('/h/pp', ['/h/p']), '')


class LiveTest(unittest.TestCase):
    def setUp(self):
        self.proc = subprocess.Popen([sys.executable, '-c', 'import socket,time\ns=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)\n'
                                      's.bind(("127.0.0.1",0));s.listen();print(s.getsockname()[1],flush=True);time.sleep(60)'],
                                     stdout=subprocess.PIPE, text=True, cwd='/tmp')
        self.port = int(self.proc.stdout.readline())

    def tearDown(self):
        self.proc.kill()
        self.proc.wait()

    def test_snapshot_finds_owner_and_stop(self):
        rows = P.snapshot(project_paths=['/tmp'])
        r = next(x for x in rows if x['port'] == self.port)
        self.assertEqual(r['pid'], self.proc.pid)
        self.assertTrue(r['mine'] and r['local'])
        self.assertEqual(r['project'], '/tmp')
        self.assertTrue(P.can_stop(r)[0])
        self.assertEqual(P.stop_pid(self.proc.pid), 'stopped')
        self.proc.wait()
        self.assertFalse(any(x['port'] == self.port for x in P.snapshot()))

    def test_service_tree_owner(self):
        svc = [{'id': 's1', 'name': 'api', 'port': self.port}]
        rows = P.snapshot(svc, {'s1': {'pid': self.proc.pid}})
        r = next(x for x in rows if x['port'] == self.port)
        self.assertEqual((r['kind'], r['owner']), ('service', 'Dev service · api'))


class MergeTest(unittest.TestCase):
    def test_same_port_pid_merged(self):
        rows = P.snapshot(raw=[raw(53, 0, '—', ip='127.0.0.54'), raw(53, 0, '—', ip='0.0.0.0'), raw(53, 0, '—', ip='::'), raw(53, 0, '—', proto='UDP', ip='0.0.0.0'), raw(80, 5, 'nginx', ip='::1')])
        self.assertEqual([(r['port'], r['protocol']) for r in rows], [(53, 'TCP'), (53, 'UDP'), (80, 'TCP')])
        self.assertFalse(rows[0]['local'])
        self.assertEqual(rows[0]['addresses'], ['127.0.0.54', '0.0.0.0', '::'])
        self.assertIn('root', rows[0]['owner'])
        self.assertTrue(rows[2]['local'])


class SafetyTest(unittest.TestCase):
    def test_refuses(self):
        self.assertFalse(P.can_stop({'pid': 0})[0])
        self.assertFalse(P.can_stop({'pid': os.getpid(), 'mine': True})[0])
        self.assertFalse(P.can_stop({'pid': 4242, 'mine': False})[0])
        self.assertFalse(P.can_stop({'pid': 4242, 'mine': True, 'process': 'systemd'})[0])
        self.assertTrue(P.can_stop({'pid': 4242, 'mine': True, 'process': 'node'})[0])
        self.assertTrue(P.can_stop({'pid': 0, 'container': {'id': 'x'}})[0])
        with self.assertRaises(RuntimeError):
            P.stop_pid(os.getpid())
        with self.assertRaises(RuntimeError):
            P.stop_pid(1)

    def test_sigkill_only_with_force(self):
        p = subprocess.Popen([sys.executable, '-c', 'import signal,time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\nprint(1,flush=True)\ntime.sleep(60)'], stdout=subprocess.PIPE, text=True)
        p.stdout.readline()
        try:
            self.assertEqual(P.stop_pid(p.pid, wait=0.5), 'running')
            self.assertEqual(P.stop_pid(p.pid, wait=0.5, force=True), 'killed')
        finally:
            p.kill()
            p.wait()


class ConflictTest(unittest.TestCase):
    def test_busy_and_dup(self):
        svcs = [{'id': 'a', 'name': 'web', 'port': 3000}, {'id': 'b', 'name': 'api', 'port': 3000}, {'id': 'c', 'name': 'db', 'port': 5432}, {'id': 'd', 'name': 'x', 'port': 0}]
        rows = [{**raw(3000, 99, 'node'), 'owner': 'node'}]
        cs = P.conflicts(svcs, rows)
        self.assertEqual(sorted((c['level'], c['port']) for c in cs), [('busy', 3000), ('busy', 3000), ('dup', 3000)])
        self.assertEqual(P.conflicts(svcs, rows, running_ids={'a', 'b'}), [c for c in cs if c['level'] == 'dup'])
        self.assertEqual(P.check_start(svcs[2], svcs, rows), [])
        self.assertEqual(len(P.check_start(svcs[0], svcs, rows)), 1)
        self.assertEqual(P.check_start(svcs[3], svcs, rows), [])

    def test_dup_with_running_sibling_warns(self):
        svcs = [{'id': 'a', 'name': 'web', 'port': 3000}, {'id': 'b', 'name': 'api', 'port': 3000}]
        msgs = P.check_start(svcs[1], svcs, [], running_ids={'a'})
        self.assertEqual(len(msgs), 1)
        self.assertIn('web', msgs[0])

    def test_free_ports(self):
        self.assertEqual(P.free_ports([raw(3000), raw(3001)], 3000, 3010, 2), [3002, 3003])


if __name__ == '__main__':
    unittest.main()
