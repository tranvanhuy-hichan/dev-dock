import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from devdock.lib import dockercli as D  # noqa: E402


def have_docker():
    return D.status()['available']


class PureTest(unittest.TestCase):
    def test_parse_size(self):
        self.assertEqual(D.parse_size('137MB'), 137_000_000)
        self.assertEqual(D.parse_size('1.5GiB'), int(1.5 * 1024 ** 3))
        self.assertEqual(D.parse_size('12kB'), 12_000)
        self.assertEqual(D.parse_size('0B'), 0)
        self.assertEqual(D.parse_size('???'), 0)
        self.assertEqual(D.parse_size(''), 0)

    def test_run_argv_builds_safe_list(self):
        a = D.run_argv('nginx:alpine', name='web-1', ports=['8080:80', '127.0.0.1:9000:9000/tcp'], env=['A=1', 'B=x y=z'], cmd='sleep 10', rm=True)
        self.assertEqual(a, ['run', '-d', '--name', 'web-1', '-p', '8080:80', '-p', '127.0.0.1:9000:9000/tcp', '-e', 'A=1', '-e', 'B=x y=z', '--rm', 'nginx:alpine', 'sleep', '10'])

    def test_run_argv_rejects_injection(self):
        for kw in ({'image': 'nginx; rm -rf /'}, {'image': '--privileged'}, {'image': 'x', 'name': '--rm'}, {'image': 'x', 'name': 'a b'},
                   {'image': 'x', 'ports': ['80']}, {'image': 'x', 'ports': ['8080:80 -v /:/host']}, {'image': 'x', 'env': ['1BAD=x']}, {'image': 'x', 'env': ['A B=x']},
                   {'image': 'x', 'cmd': "unterminated 'quote"}):
            with self.assertRaises(D.DockerError, msg=str(kw)):
                D.run_argv(**kw)

    def test_project_action_validation(self):
        for p, a in (('x', 'rm -rf'), ('../etc', 'stop'), ('a b', 'stop'), ('', 'stop'), ('ok', 'up')):
            with self.assertRaises(D.DockerError):
                D.project_action(p, a)
        with self.assertRaises(D.DockerError):
            D.network_remove('bridge')

    def test_inspect_normalizes(self):
        raw = '[{"Id":"abc","Name":"/web","Created":"c","State":{"Status":"running","Running":true,"StartedAt":"s","Health":{"Status":"healthy"}},' \
              '"Config":{"Image":"nginx","Cmd":["nginx","-g"],"Env":["A=1","B"],"Labels":{"com.docker.compose.project":"p","com.docker.compose.service":"web","com.docker.compose.project.config_files":"/a/dc.yml,/a/o.yml"}},' \
              '"HostConfig":{"RestartPolicy":{"Name":"always"}},"RestartCount":2,' \
              '"Mounts":[{"Type":"volume","Name":"v1","Destination":"/data","RW":true}],' \
              '"NetworkSettings":{"Ports":{"80/tcp":[{"HostIp":"0.0.0.0","HostPort":"8080"}],"443/tcp":null},"Networks":{"bridge":{"IPAddress":"172.17.0.2","Gateway":"172.17.0.1"}}}}]'
        with mock.patch.object(D, '_run', return_value=mock.Mock(stdout=raw, stderr='', returncode=0)):
            i = D.inspect('abc')
        self.assertEqual((i['name'], i['health'], i['restart'], i['restarts'], i['project'], i['service']), ('web', 'healthy', 'always', 2, 'p', 'web'))
        self.assertEqual(i['env'], [('A', '1'), ('B', '')])
        self.assertEqual(i['ports'][0], {'container': '80/tcp', 'host_ip': '0.0.0.0', 'host_port': '8080'})
        self.assertEqual(i['ports'][1]['host_port'], '')
        self.assertEqual(i['compose_files'], ['/a/dc.yml', '/a/o.yml'])
        self.assertEqual(i['mounts'][0]['src'], 'v1')


@unittest.skipUnless(have_docker(), 'cần Docker đang chạy')
class RealDockerTest(unittest.TestCase):
    def test_system_df_volumes_networks_real(self):
        df = D.system_df()
        self.assertTrue(any(r['type'] == 'Images' for r in df))
        self.assertTrue(all(isinstance(r['size'], int) for r in df))
        self.assertIsInstance(D.volumes(), list)
        nets = D.networks()
        self.assertTrue(any(n['name'] == 'bridge' and n['builtin'] for n in nets))

    def test_volume_and_network_lifecycle(self):
        vol, net = 'devdock-test-vol', 'devdock-test-net'
        subprocess.run(['docker', 'volume', 'rm', '-f', vol], capture_output=True)
        subprocess.run(['docker', 'network', 'rm', net], capture_output=True)
        try:
            subprocess.run(['docker', 'volume', 'create', vol], capture_output=True, check=True)
            subprocess.run(['docker', 'network', 'create', net], capture_output=True, check=True)
            self.assertIn(vol, [v['name'] for v in D.volumes()])
            self.assertIn(net, [n['name'] for n in D.networks()])
            D.volume_remove(vol)
            D.network_remove(net)
            self.assertNotIn(vol, [v['name'] for v in D.volumes()])
            self.assertNotIn(net, [n['name'] for n in D.networks()])
        finally:
            subprocess.run(['docker', 'volume', 'rm', '-f', vol], capture_output=True)
            subprocess.run(['docker', 'network', 'rm', net], capture_output=True)

    def test_run_inspect_stats_actions_real(self):
        name = 'devdock-test-run'
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True)
        try:
            cid = D.run_container('redis:7-alpine', name=name, ports=['127.0.0.1:0:6379'] if False else [], env=['FOO=bar'])
            self.assertTrue(cid)
            i = D.inspect(name)
            self.assertTrue(i['running'])
            self.assertIn(('FOO', 'bar'), i['env'])
            self.assertEqual(i['restart'], 'no')
            s = D.stats_one(name)
            self.assertIn('cpu', s)
            D.action(name, 'stop')
            with self.assertRaises(D.DockerError):
                D.stats_one(name)
            self.assertFalse(D.inspect(name)['running'])
        finally:
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True)


if __name__ == '__main__':
    unittest.main()
