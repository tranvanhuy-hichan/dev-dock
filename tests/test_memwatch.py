import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from devdock.lib import memwatch as M  # noqa: E402

MB = 1024 ** 2


def p(pid=1, name='x', app=None, pss=10 * MB, rss=20 * MB, swap=0, exact=True, mine=True):
    return {'pid': pid, 'ppid': 0, 'name': name, 'app': app or name, 'pss': pss, 'rss': rss, 'swap': swap, 'exact': exact, 'mine': mine, 'uss': 0, 'shared': 0}


class NamingTest(unittest.TestCase):
    def test_app_name(self):
        self.assertEqual(M.app_name('chrome', '', 'app-gnome-google\\x2dchrome-4242.scope'), 'Google Chrome')
        self.assertEqual(M.app_name('code', '/usr/share/code/code --type=renderer'), 'VS Code')
        self.assertEqual(M.app_name('node', '/home/u/.vscode-server/bin/x/node server.js'), 'VS Code')
        self.assertEqual(M.app_name('Web Content', ''), 'Firefox')
        self.assertEqual(M.app_name('llama-server', ''), 'Ollama')
        self.assertEqual(M.app_name('postgres', '/usr/lib/postgresql/16/bin/postgres'), 'postgres')


class GroupTest(unittest.TestCase):
    def test_group_sums_and_sorts(self):
        g = M.group([p(1, 'a', 'Chrome', 100 * MB), p(2, 'b', 'Chrome', 300 * MB), p(3, 'c', 'Slack', 50 * MB, exact=False), p(4, 'd', 'Slack', 10 * MB, mine=False)])
        self.assertEqual([x['app'] for x in g], ['Chrome', 'Slack'])
        c = g[0]
        self.assertEqual((c['count'], c['pss'], c['pids'], c['top']['pid']), (2, 400 * MB, [1, 2], 2))
        self.assertTrue(c['exact'] and c['mine'])
        self.assertFalse(g[1]['exact'] or g[1]['mine'])

    def test_summary_counts_approximate(self):
        s = M.summary([p(1, pss=100 * MB), p(2, pss=50 * MB, exact=False)])
        self.assertEqual((s['pss'], s['approx']), (150 * MB, 1))
        self.assertGreater(s['total'], 0)


class RealProcTest(unittest.TestCase):
    def test_rollup_of_self_and_pss_le_rss(self):
        r = M.read_rollup(os.getpid())
        self.assertIsNotNone(r)
        self.assertGreater(r['pss'], 0)
        procs = M.snapshot(own_user_only=True)
        me = next(x for x in procs if x['pid'] == os.getpid())
        self.assertTrue(me['exact'] and me['mine'])
        self.assertLessEqual(me['pss'], me['rss'] * 1.05)
        self.assertEqual(M.read_rollup(2 ** 22 - 1), None)

    def test_cpu_field_and_group_sum(self):
        procs = M.snapshot(own_user_only=True)
        self.assertTrue(all('cpu' in x for x in procs))
        g = M.group([{**p(1, 'a', 'X'), 'cpu': 30.0}, {**p(2, 'b', 'X'), 'cpu': 45.5}, p(3, 'c', 'Y')])
        self.assertEqual({x['app']: x['cpu'] for x in g}, {'X': 75.5, 'Y': 0.0})

    def test_pss_sum_is_below_rss_sum(self):
        procs = [x for x in M.snapshot() if x['exact']]
        self.assertLess(sum(x['pss'] for x in procs), sum(x['rss'] for x in procs))  # RSS đếm trùng bộ nhớ chia sẻ


class TrendTest(unittest.TestCase):
    def test_delta(self):
        t = M.Trend()
        for i, v in enumerate([100, 110, 130, 160]):
            t.update([p(7, pss=v * MB)], now=i * 100)
        self.assertEqual(t.delta(7, 300, now=300), (60 * MB, 300))
        self.assertEqual(t.delta(7, 150, now=300), (30 * MB, 100))
        self.assertEqual(t.delta(99), (0, 0))
        t.update([], now=400)
        self.assertEqual(t.h, {})


class KillTest(unittest.TestCase):
    def test_can_kill_rules(self):
        self.assertFalse(M.can_kill(p(1))[0])
        self.assertFalse(M.can_kill(p(os.getpid()))[0])
        self.assertFalse(M.can_kill(p(500, mine=False))[0])
        self.assertFalse(M.can_kill(p(500, 'gnome-shell'))[0])
        self.assertTrue(M.can_kill(p(500, 'node'))[0])

    def test_stop_many_term_and_force(self):
        ok = subprocess.Popen(['sleep', '60'])
        stub = subprocess.Popen([sys.executable, '-c', 'import signal,time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\nprint(1,flush=True)\ntime.sleep(60)'], stdout=subprocess.PIPE, text=True)
        stub.stdout.readline()
        try:
            r = M.stop_many([ok.pid, stub.pid], wait=0.6)
            self.assertEqual(r['stopped'], [ok.pid])
            self.assertEqual(r['alive'], [stub.pid])
            r2 = M.stop_many([stub.pid], wait=0.3, force=True)
            self.assertEqual((r2['stopped'], r2['alive']), ([stub.pid], []))
        finally:
            for x in (ok, stub):
                x.kill()
                x.wait()

    def test_never_stops_self_or_init(self):
        r = M.stop_many([os.getpid(), 1])
        self.assertEqual(sorted(r['denied']), [1, os.getpid()])

    def test_ancestors_excludes_self(self):
        a = M.ancestors()
        self.assertNotIn(os.getpid(), a)
        self.assertIn(os.getppid(), a)


if __name__ == '__main__':
    unittest.main()
