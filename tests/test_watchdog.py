import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from devdock.lib import watchdog as W  # noqa: E402
from devdock.lib.services import ServiceManager, new_service  # noqa: E402

GB = 1024 ** 3


def sample(avail=50, swap=0, disks=None, procs=None, services=None, owners=None, containers=None):
    return {'t': 0, 'mem_total': 16 * GB, 'mem_avail': int(16 * GB * avail / 100), 'mem_avail_pct': avail, 'swap_total': 8 * GB, 'swap_used': int(8 * GB * swap / 100), 'swap_pct': swap,
            'disks': disks if disks is not None else [{'mount': '/', 'free': 100 * GB, 'total': 200 * GB}], 'procs': procs or [], 'services': services or [], 'port_owners': owners or {}, 'containers': containers}


def proc(name='app', pid=1, rss=100 * 1024 ** 2, cpu=1.0):
    return {'pid': pid, 'name': name, 'rss': rss, 'cpu': cpu, 'created': 0}


def engine(**over):
    cfg = W.load_config('/nonexistent')
    for k, v in over.items():
        if k in cfg['rules']:
            cfg['rules'][k].update(v)
        else:
            cfg[k] = v
    notes = []
    return W.Engine(cfg, notify=notes.append), notes


class RamTest(unittest.TestCase):
    def test_low_ram_needs_a_full_minute(self):
        e, notes = engine()
        self.assertEqual(e.tick(sample(avail=5), 0), [])
        self.assertEqual(e.tick(sample(avail=5), 30), [])
        a = e.tick(sample(avail=5, procs=[proc('firefox', 7, 3 * GB)]), 60)
        self.assertEqual(len(a), 1)
        self.assertIn('firefox', a[0]['body'])  # nêu tiến trình dùng nhiều nhất
        self.assertEqual(len(notes), 1)
        self.assertEqual(len(e.history), 1)

    def test_recovery_resets_the_timer(self):
        e, notes = engine()
        e.tick(sample(avail=5), 0)
        e.tick(sample(avail=50), 40)       # hồi phục
        e.tick(sample(avail=5), 50)        # bắt đầu lại
        self.assertEqual(e.tick(sample(avail=5), 100), [])   # mới 50s
        self.assertEqual(len(e.tick(sample(avail=5), 115)), 1)

    def test_cooldown_blocks_repeats_then_allows(self):
        e, notes = engine()
        for t in (0, 60):
            e.tick(sample(avail=5), t)
        self.assertEqual(len(notes), 1)
        for t in range(70, 1800, 10):      # < 30 phút: không báo lại
            e.tick(sample(avail=5), t)
        self.assertEqual(len(notes), 1)
        e.tick(sample(avail=5), 1861)      # đã quá 30 phút kể từ lần báo (t=60)
        self.assertEqual(len(notes), 2)

    def test_disabled_rule_never_fires(self):
        e, notes = engine(ram_low={'enabled': False}, swap_high={'enabled': False})
        for t in (0, 60, 120):
            e.tick(sample(avail=1, swap=99), t)
        self.assertEqual(notes, [])

    def test_thresholds_are_configurable(self):
        e, notes = engine(ram_low={'pct': 40, 'minutes': 0})
        e.tick(sample(avail=30), 0)
        self.assertEqual(len(notes), 1)

    def test_swap(self):
        e, notes = engine()
        e.tick(sample(swap=90), 0)
        e.tick(sample(swap=90), 61)
        self.assertEqual([n['key'] for n in notes], ['swap_high'])


class DiskTest(unittest.TestCase):
    def test_by_percent_and_by_gb_immediately(self):
        e, notes = engine()
        e.tick(sample(disks=[{'mount': '/', 'free': 15 * GB, 'total': 200 * GB}]), 0)   # 7.5% < 10%
        e.tick(sample(disks=[{'mount': '/home', 'free': 4 * GB, 'total': 2000 * GB}]), 0)  # < 5 GB dù % cao
        self.assertEqual(sorted(n['key'] for n in notes), ['disk:/', 'disk:/home'])
        e2, n2 = engine()
        e2.tick(sample(disks=[{'mount': '/', 'free': 100 * GB, 'total': 200 * GB}]), 0)
        self.assertEqual(n2, [])


class HogTest(unittest.TestCase):
    def test_memory_hog_over_time_and_listed_before_alert(self):
        e, notes = engine()
        big = [proc('diskcleaner', 9, 2 * GB)]
        e.tick(sample(procs=big), 0)
        self.assertEqual(notes, [])
        h = e.hogs()
        self.assertEqual((h[0]['name'], h[0]['kind'], h[0]['alerted']), ('diskcleaner', 'ram', False))
        e.tick(sample(procs=big), 299)
        self.assertEqual(notes, [])
        e.tick(sample(procs=big), 300)
        self.assertEqual(len(notes), 1)
        self.assertIn('diskcleaner', notes[0]['body'])
        self.assertTrue(e.hogs()[0]['alerted'])

    def test_drop_below_threshold_resets(self):
        e, notes = engine()
        e.tick(sample(procs=[proc('x', 1, 2 * GB)]), 0)
        e.tick(sample(procs=[proc('x', 1, 100 * 1024 ** 2)]), 200)
        e.tick(sample(procs=[proc('x', 1, 2 * GB)]), 250)
        e.tick(sample(procs=[proc('x', 1, 2 * GB)]), 400)   # mới 150s kể từ lần vượt lại
        self.assertEqual(notes, [])

    def test_cpu_hog_needs_three_minutes(self):
        e, notes = engine()
        busy = [proc('node', 5, 200 * 1024 ** 2, 95.0)]
        e.tick(sample(procs=busy), 0)
        e.tick(sample(procs=busy), 179)
        self.assertEqual(notes, [])
        e.tick(sample(procs=busy), 180)
        self.assertEqual(notes[0]['title'], 'Tiến trình ngốn CPU')

    def test_ignore_list_case_insensitive(self):
        e, notes = engine(ignore=['LLAMA-server'])
        big = [proc('llama-server', 3, 4 * GB, 100)]
        for t in (0, 300, 600):
            e.tick(sample(procs=big), t)
        self.assertEqual(notes, [])
        self.assertEqual(e.hogs(), [])


class ServiceTest(unittest.TestCase):
    def svc(self, running, rc=None, user=False, run_id=1.0, port=0):
        return {'id': 's1', 'name': 'web', 'port': port, 'running': running, 'pid': 5 if running else None, 'run_id': run_id, 'rc': rc, 'user_stopped': user}

    def test_crash_alerts_once_per_run(self):
        e, notes = engine()
        e.tick(sample(services=[self.svc(True)]), 0)
        e.tick(sample(services=[self.svc(False, rc=3)]), 10)
        e.tick(sample(services=[self.svc(False, rc=3)]), 20)
        self.assertEqual(len(notes), 1)
        self.assertIn('lỗi', notes[0]['title'])
        self.assertEqual(notes[0]['level'], 'error')

    def test_user_stop_is_silent(self):
        e, notes = engine()
        e.tick(sample(services=[self.svc(True)]), 0)
        e.tick(sample(services=[self.svc(False, rc=-15, user=True)]), 10)
        self.assertEqual(notes, [])

    def test_killed_externally_and_clean_exit(self):
        e, notes = engine()
        e.tick(sample(services=[self.svc(False, rc=-9, run_id=1.0)]), 0)
        self.assertIn('kill', notes[0]['body'])
        e2, n2 = engine()
        e2.tick(sample(services=[self.svc(False, rc=0)]), 0)
        self.assertEqual(n2[0]['title'], 'web đã dừng')

    def test_never_started_service_is_silent(self):
        e, notes = engine()
        e.tick(sample(services=[self.svc(False, run_id=None)]), 0)
        self.assertEqual(notes, [])

    def test_port_conflict_ignores_our_own_process(self):
        e, notes = engine()
        ours = sample(services=[self.svc(True, port=3000)], owners={3000: {'pid': 5, 'name': 'node', 'ours': True}})
        e.tick(ours, 0)
        e.tick(ours, 30)
        self.assertEqual(notes, [])
        theirs = sample(services=[self.svc(True, port=3000)], owners={3000: {'pid': 99, 'name': 'nginx', 'ours': False}})
        e.tick(theirs, 100)
        self.assertEqual(notes, [])        # cần giữ ≥10s
        e.tick(theirs, 115)
        self.assertIn('nginx', notes[0]['body'])


class DockerTest(unittest.TestCase):
    def c(self, state, status='Up 1 hour', cid='c1'):
        return {'id': cid, 'name': 'db', 'state': state, 'status': status}

    def test_abnormal_exit_vs_clean_exit(self):
        e, notes = engine()
        e.tick(sample(containers=[self.c('running')]), 0)
        e.tick(sample(containers=[self.c('exited', 'Exited (137) 2 seconds ago')]), 30)
        self.assertEqual(len(notes), 1)
        self.assertIn('137', notes[0]['body'])
        e2, n2 = engine()
        e2.tick(sample(containers=[self.c('running')]), 0)
        e2.tick(sample(containers=[self.c('exited', 'Exited (0) 2 seconds ago')]), 30)
        self.assertEqual(n2, [])   # tắt bình thường
        e2.tick(sample(containers=[self.c('running')]), 60)
        e2.tick(sample(containers=[self.c('exited', 'Exited (143) 1 second ago')]), 90)
        self.assertEqual(n2, [])   # SIGTERM gọn gàng

    def test_already_exited_at_startup_is_silent(self):
        e, notes = engine()
        e.tick(sample(containers=[self.c('exited', 'Exited (1) 3 days ago')]), 0)
        self.assertEqual(notes, [])

    def test_unhealthy_and_state_kept_between_polls(self):
        e, notes = engine()
        e.tick(sample(containers=[self.c('running', 'Up 2 hours (unhealthy)')]), 0)
        self.assertEqual(len(notes), 1)
        e.tick(sample(containers=None), 10)       # tick không hỏi Docker
        e.tick(sample(containers=[self.c('running')]), 30)
        e.tick(sample(containers=[self.c('exited', 'Exited (1) now')]), 40)
        self.assertEqual(len(notes), 2)           # trạng thái 'running' trước đó vẫn nhớ qua tick None

    def test_restart_loop_needs_a_minute_across_polls(self):
        e, notes = engine()
        r = [self.c('restarting', 'Restarting (1) 5 seconds ago')]
        e.tick(sample(containers=r), 0)
        e.tick(sample(containers=None), 30)
        e.tick(sample(containers=None), 61)
        self.assertEqual([n['key'] for n in notes if n['key'].startswith('restart')], ['restart:c1'])


class HistoryTest(unittest.TestCase):
    def test_capped_and_persisted_atomically(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, 'h.json')
        e, _ = engine(cooldown_min=0)
        e.on_history = lambda h: W.save_history(h, path)
        for i in range(250):
            e.tick(sample(disks=[{'mount': f'/m{i}', 'free': GB, 'total': 200 * GB}]), i)
        self.assertEqual(len(e.history), 200)
        self.assertEqual(len(W.load_history(path)), 200)
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), '0o600')
        self.assertEqual([f for f in os.listdir(d) if f.startswith('.watch-')], [])
        e.clear_history()
        self.assertEqual(W.load_history(path), [])

    def test_notify_exception_does_not_break_engine(self):
        cfg = W.load_config('/x')
        e = W.Engine(cfg, notify=lambda a: 1 / 0)
        self.assertEqual(len(e.tick(sample(disks=[{'mount': '/', 'free': GB, 'total': 200 * GB}]), 0)), 1)


class ConfigTest(unittest.TestCase):
    def test_roundtrip_and_sanitising(self):
        p = os.path.join(tempfile.mkdtemp(), 'watch.json')
        cfg = W.load_config(p)
        cfg['rules']['ram_low']['pct'] = 25
        cfg['ignore'] = ['code']
        W.save_config(cfg, p)
        again = W.load_config(p)
        self.assertEqual((again['rules']['ram_low']['pct'], again['ignore']), (25, ['code']))
        with open(p, 'w') as f:
            json.dump({'rules': {'ram_low': {'pct': 'abc', 'enabled': 0}, 'bogus': {}}, 'cooldown_min': -5, 'ignore': ['ok', 7, '']}, f)
        c = W.load_config(p)
        self.assertEqual((c['rules']['ram_low']['pct'], c['rules']['ram_low']['enabled'], c['cooldown_min'], c['ignore']), (10, False, 0, ['ok']))
        with open(p, 'w') as f:
            f.write('{broken')
        self.assertEqual(W.load_config(p)['cooldown_min'], 30)


class AutostartTest(unittest.TestCase):
    def setUp(self):
        self.old = os.environ.get('XDG_CONFIG_HOME')
        os.environ['XDG_CONFIG_HOME'] = tempfile.mkdtemp()

    def tearDown(self):
        if self.old is None:
            del os.environ['XDG_CONFIG_HOME']
        else:
            os.environ['XDG_CONFIG_HOME'] = self.old

    def test_create_and_remove(self):
        self.assertFalse(W.autostart_enabled())
        W.set_autostart(True)
        p = W.autostart_path()
        self.assertTrue(p.startswith(os.environ['XDG_CONFIG_HOME']) and W.autostart_enabled())
        txt = open(p).read()
        exec_line = next(l for l in txt.splitlines() if l.startswith('Exec='))
        self.assertTrue(exec_line.endswith(' --hidden') and 'devdock' in exec_line, exec_line)
        self.assertIn('X-GNOME-Autostart-enabled=true', txt)
        W.set_autostart(True)   # lặp lại không lỗi
        W.set_autostart(False)
        self.assertFalse(W.autostart_enabled())
        W.set_autostart(False)  # xoá khi không có cũng không lỗi


class RealPartsTest(unittest.TestCase):
    def test_sampler_returns_sane_data(self):
        s = W.SystemSampler()
        s.sample()
        d = s.sample()
        self.assertTrue(0 < d['mem_avail_pct'] <= 100 and d['mem_total'] > 0)
        self.assertTrue(d['disks'] and all(x['total'] > 0 for x in d['disks']))
        self.assertTrue(all({'pid', 'name', 'rss', 'cpu'} <= set(p) for p in d['procs']))

    def test_user_stop_tracking_and_service_samples(self):
        m = ServiceManager()
        W.track_user_stops(m)
        W.track_user_stops(m)  # idempotent
        a = new_service('a', tempfile.gettempdir(), 'sleep 30')
        b = new_service('b', tempfile.gettempdir(), 'exit 3')
        try:
            m.start(a)
            m.start(b)
            end = time.time() + 6
            while time.time() < end and m.is_running(b['id']):
                time.sleep(0.05)
            m.stop(a['id'])
            while time.time() < end and m.is_running(a['id']):
                time.sleep(0.05)
            svcs, _ = W.service_samples(m, [a, b])
            by = {x['id']: x for x in svcs}
            self.assertTrue(by[a['id']]['user_stopped'] and not by[a['id']]['running'])
            self.assertFalse(by[b['id']]['user_stopped'])
            self.assertEqual(by[b['id']]['rc'], 3)
            e, notes = engine()
            e.tick(sample(services=svcs), 0)
            self.assertEqual([n['key'] for n in notes], [f"svc:{b['id']}"])   # chỉ service lỗi, không báo service do người dùng dừng
        finally:
            m.shutdown()


if __name__ == '__main__':
    unittest.main()
