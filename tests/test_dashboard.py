import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from devdock.lib import dashboard as D  # noqa: E402

HOME = os.path.expanduser('~')


def sysd(ram=50, swap=0, free=50, mount='/'):
    return {'mem_used': ram, 'mem_total': 100, 'swap_used': swap, 'swap_total': 100 if swap is not None else 0, 'disks': [{'mount': mount, 'free': free, 'total': 100}]}


def titles(a):
    return [x['title'] for x in a]


class AttentionTest(unittest.TestCase):
    def test_all_clear(self):
        self.assertEqual(D.attention(sysd(), [], [], {'available': True, 'containers': []}, []), [])
        self.assertEqual(D.attention(), [])  # chưa có dữ liệu nào: không báo gì

    def test_memory_swap_disk_levels(self):
        a = D.attention(sysd(ram=95))
        self.assertEqual((a[0]['level'], a[0]['module']), ('bad', 'sysmon'))
        self.assertEqual(D.attention(sysd(ram=85))[0]['level'], 'warn')
        self.assertEqual(D.attention(sysd(ram=79)), [])
        self.assertIn('Swap', D.attention(sysd(swap=85))[0]['title'])
        d = D.attention(sysd(free=5))
        self.assertEqual((d[0]['level'], d[0]['module']), ('bad', 'cleaner'))
        self.assertEqual(D.attention(sysd(free=15))[0]['level'], 'warn')
        self.assertEqual(D.attention(sysd(free=5, mount='/mnt/usb')), [])  # chỉ quan tâm / và $HOME
        self.assertEqual(D.attention(sysd(free=5, mount=HOME))[0]['level'], 'bad')

    def test_services_failed_only(self):
        a = D.attention(services=[{'name': 'web', 'state': 'failed', 'rc': 2}, {'name': 'db', 'state': 'stopped'}, {'name': 'x', 'state': 'running'}])
        self.assertEqual(titles(a), ['Dev service “web” đã thoát lỗi'])
        self.assertEqual(a[0]['detail'], 'Mã thoát 2')

    def test_projects(self):
        ps = [{'name': 'a', 'git': True, 'ahead': 3}, {'name': 'b', 'git': True, 'dirty': 2}, {'name': 'c', 'git': True, 'untracked': 1}, {'name': 'd', 'git': True, 'behind': 1},
              {'name': 'e', 'git': False, 'ahead': 9}, {'name': 'f', 'git': True}]
        a = D.attention(projects=ps)
        self.assertEqual([x['level'] for x in a], ['warn', 'info', 'info'])
        self.assertIn('1 repo có commit chưa push', a[0]['title'])
        self.assertEqual(a[1]['detail'], 'b, c')
        many = [{'name': f'p{i}', 'git': True, 'ahead': 1} for i in range(6)]
        self.assertIn('và 3 mục khác', D.attention(projects=many)[0]['detail'])

    def test_secrets_and_port_conflicts(self):
        a = D.attention(secrets={'bad': 2, 'warn': 3, 'repos_with': 1})
        self.assertEqual((a[0]['level'], a[0]['module']), ('bad', 'secrets'))
        self.assertIn('2 bí mật', a[0]['title'])
        self.assertEqual(D.attention(secrets={'bad': 0, 'warn': 3, 'repos_with': 1})[0]['level'], 'info')
        self.assertEqual(D.attention(secrets={'bad': 0, 'warn': 0, 'repos_with': 0}), [])
        c = [{'level': 'busy', 'port': 3000, 'detail': 'Cổng 3000 đang bị node chiếm'}, {'level': 'dup', 'port': 1, 'detail': 'x'}]
        a = D.attention(conflicts=c)
        self.assertEqual((a[0]['level'], a[0]['module'], a[0]['title']), ('warn', 'ports', '1 dev service bị trùng cổng'))
        self.assertEqual(D.attention(conflicts=[c[1]]), [])

    def test_docker(self):
        self.assertIn('Docker chưa chạy', D.attention(docker={'available': False, 'containers': []})[0]['title'])
        a = D.attention(docker={'available': True, 'containers': [
            {'name': 'ok', 'state': 'running', 'status': 'Up 2 hours'}, {'name': 'sick', 'state': 'running', 'status': 'Up 1 hour (unhealthy)'},
            {'name': 'loop', 'state': 'restarting', 'status': 'Restarting (1) 5 seconds ago'}, {'name': 'old', 'state': 'exited', 'status': 'Exited (0) 3 weeks ago'},
            {'name': 'crash', 'state': 'exited', 'status': 'Exited (1) 30 seconds ago'}, {'name': 'oldcrash', 'state': 'exited', 'status': 'Exited (1) 8 weeks ago'},
            {'name': 'crash5', 'state': 'exited', 'status': 'Exited (137) 5 minutes ago'}, {'name': 'stopped', 'state': 'exited', 'status': 'Exited (143) 2 minutes ago'}]})
        self.assertEqual(len(a), 1)
        self.assertIn('4 container có vấn đề', a[0]['title'])
        self.assertEqual(a[0]['detail'], 'sick, loop, crash và 1 mục khác')  # crash5 (5 phút) cũng được tính; mã 143 (dừng thường) và crash cũ thì không

    def test_alerts_last_24h_only(self):
        now = time.time()
        a = D.attention(alerts=[{'t': now - 100, 'title': 'RAM thấp'}, {'t': now - 90000, 'title': 'cũ'}], now=now)
        self.assertEqual((a[0]['title'], a[0]['detail'], a[0]['module']), ('1 cảnh báo trong 24 giờ qua', 'RAM thấp', 'watch'))
        self.assertEqual(D.attention(alerts=[{'t': now - 90000, 'title': 'cũ'}], now=now), [])

    def test_sorted_by_severity(self):
        a = D.attention(sysd(ram=95), [{'name': 'a', 'git': True, 'dirty': 1}], [{'name': 's', 'state': 'failed', 'rc': 1}])
        self.assertEqual([x['level'] for x in a], ['bad', 'bad', 'info'])


class HelpersTest(unittest.TestCase):
    def test_greeting(self):
        self.assertEqual([D.greeting(h) for h in (6, 12, 15, 20, 2)], ['Chào buổi sáng', 'Chào buổi trưa', 'Chào buổi chiều', 'Chào buổi tối', 'Chào buổi tối'])

    def test_recent_projects_and_service_rows(self):
        ps = [{'name': 'old', 'last_ts': 1}, {'name': 'new', 'last_ts': 9}, {'name': 'none'}]
        self.assertEqual([p['name'] for p in D.recent_projects(ps, 2)], ['new', 'old'])
        svcs = [{'id': str(i), 'name': f's{i}'} for i in range(9)]
        rows, more = D.service_rows(svcs, {'5': {'state': 'running'}, '7': {'state': 'failed', 'rc': 3}}, 6)
        self.assertEqual([r['id'] for r in rows[:2]], ['5', '7'])
        self.assertEqual((len(rows), more, rows[1]['rc']), (6, 3, 3))


if __name__ == '__main__':
    unittest.main()
