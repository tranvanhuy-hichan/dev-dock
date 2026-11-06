import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from devdock.lib import dockercli, sysmon  # noqa: E402


class DockerTest(unittest.TestCase):
    def test_project_and_actions(self):
        self.assertEqual(dockercli.project_of('a=b,com.docker.compose.project=myproj,c=d'), 'myproj')
        self.assertIsNone(dockercli.project_of('a=b'))
        with self.assertRaises(dockercli.DockerError):
            dockercli.action('abc', 'rm -rf /')
        with self.assertRaises(dockercli.DockerError):
            dockercli.pull('nginx; rm -rf /')
        with self.assertRaises(dockercli.DockerError):
            dockercli.compose('/nonexistent.yml', 'up')
        with self.assertRaises(dockercli.DockerError):
            dockercli.compose(__file__, 'evil')

    def test_parsing(self):
        out = '{"ID":"abc","Names":"web","Image":"nginx","State":"running","Status":"Up","Ports":"0.0.0.0:80->80/tcp, :::80->80/tcp","Labels":"com.docker.compose.project=p"}\nnoise\n'
        with mock.patch.object(dockercli, '_run', return_value=mock.Mock(stdout=out, stderr='', returncode=0)):
            c = dockercli.containers()
        self.assertEqual(c[0]['project'], 'p')
        self.assertEqual(len(c[0]['ports']), 2)
        s = '{"ID":"i","Name":"web","CPUPerc":"12.5%","MemUsage":"1MiB / 2GiB","MemPerc":"0.05%","NetIO":"1B / 2B","BlockIO":"0B / 0B"}\n'
        with mock.patch.object(dockercli, '_run', return_value=mock.Mock(stdout=s, stderr='', returncode=0)):
            self.assertEqual(dockercli.stats()[0]['cpu'], 12.5)

    def test_status_never_raises(self):
        self.assertIn('available', dockercli.status())


class SysTest(unittest.TestCase):
    def test_gpu_parse(self):
        g = sysmon.parse_gpus('NVIDIA RTX 4090, 35, 2048, 24564, 51\nbad line\n')
        self.assertEqual(len(g), 1)
        self.assertEqual(g[0]['mem_total'], 24564 * 1048576)

    def test_snapshot_processes_ports(self):
        m = sysmon.Monitor()
        s = m.snapshot()
        self.assertGreater(s['mem_total'], 0)
        self.assertTrue(s['cpu_cores'])
        self.assertTrue(m.processes())
        self.assertIsInstance(sysmon.list_ports(), list)
        with self.assertRaises(RuntimeError):
            sysmon.Monitor.kill(2 ** 22 + 12345)


if __name__ == '__main__':
    unittest.main()
