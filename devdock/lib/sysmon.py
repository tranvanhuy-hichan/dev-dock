"""Giám sát hệ thống bằng psutil: CPU/RAM/mạng/ổ đĩa/GPU NVIDIA, process, cổng mạng."""
import os
import platform
import socket
import subprocess
import time

import psutil

SKIP_FS = {'squashfs', 'tmpfs', 'devtmpfs', 'overlay', 'proc', 'sysfs', 'cgroup', 'cgroup2', 'efivarfs', 'fuse.portal', 'ramfs'}


def parse_gpus(text):
    out = []
    for line in text.splitlines():
        p = [x.strip() for x in line.split(',')]
        if len(p) < 5:
            continue

        def num(s):
            try:
                return float(s)
            except ValueError:
                return 0.0
        out.append({'name': p[0], 'util': num(p[1]), 'mem_used': int(num(p[2]) * 1048576), 'mem_total': int(num(p[3]) * 1048576), 'temp': num(p[4])})
    return out


class Monitor:
    def __init__(self):
        self._net = psutil.net_io_counters(pernic=True)
        self._t = time.monotonic()
        self._gpu = (0.0, [])
        self._procs = {}
        psutil.cpu_percent(percpu=True)  # khởi động bộ đếm
        self.cpu_brand = self._brand()

    @staticmethod
    def _brand():
        try:
            with open('/proc/cpuinfo', encoding='utf-8') as f:
                for line in f:
                    if line.startswith('model name'):
                        return line.split(':', 1)[1].strip()
        except OSError:
            pass
        return platform.processor() or 'CPU'

    def _gpus(self):
        if time.monotonic() - self._gpu[0] >= 2:
            try:
                r = subprocess.run(['nvidia-smi', '--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu',
                                    '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=5)
                self._gpu = (time.monotonic(), parse_gpus(r.stdout) if r.returncode == 0 else [])
            except (OSError, subprocess.TimeoutExpired):
                self._gpu = (time.monotonic(), [])
        return self._gpu[1]

    def snapshot(self):
        cores = psutil.cpu_percent(percpu=True)
        now = time.monotonic()
        dt = max(now - self._t, 0.001)
        cur = psutil.net_io_counters(pernic=True)
        net = []
        for name, c in sorted(cur.items()):
            o = self._net.get(name)
            net.append({'name': name, 'rx_total': c.bytes_recv, 'tx_total': c.bytes_sent,
                        'rx_rate': max(0, c.bytes_recv - o.bytes_recv) / dt if o else 0.0, 'tx_rate': max(0, c.bytes_sent - o.bytes_sent) / dt if o else 0.0})
        self._net, self._t = cur, now
        real = [n for n in net if n['name'] != 'lo']
        vm, sw = psutil.virtual_memory(), psutil.swap_memory()
        disks, seen = [], set()
        for p in psutil.disk_partitions(all=False):
            if p.fstype in SKIP_FS or p.mountpoint in seen or p.mountpoint.startswith(('/snap', '/var/snap', '/boot/efi')):
                continue
            try:
                u = psutil.disk_usage(p.mountpoint)
            except OSError:
                continue
            seen.add(p.mountpoint)
            disks.append({'name': p.device, 'mount': p.mountpoint, 'fs': p.fstype, 'total': u.total, 'free': u.free})
        try:
            load = list(os.getloadavg())
        except OSError:
            load = [0.0, 0.0, 0.0]
        return {
            'host': socket.gethostname(), 'os': f'{platform.system()} {platform.release()}', 'uptime': time.time() - psutil.boot_time(),
            'cpu_brand': self.cpu_brand, 'cpu_total': sum(cores) / len(cores) if cores else 0.0, 'cpu_cores': cores,
            'mem_used': vm.total - vm.available, 'mem_total': vm.total, 'swap_used': sw.used, 'swap_total': sw.total, 'load': load,
            'net': net, 'net_rx': sum(n['rx_rate'] for n in real), 'net_tx': sum(n['tx_rate'] for n in real), 'disks': disks, 'gpus': self._gpus(),
        }

    def processes(self):
        """Dùng lại đối tượng Process giữa các lần gọi để cpu_percent có nghĩa (lần đầu = 0)."""
        out, alive = [], set()
        for p in psutil.process_iter():
            try:
                pid = p.pid
                alive.add(pid)
                q = self._procs.setdefault(pid, p)
                with q.oneshot():
                    out.append({'pid': pid, 'name': q.name(), 'cpu': q.cpu_percent(None), 'mem': q.memory_info().rss, 'status': q.status(),
                                'user': q.username(), 'cmd': ' '.join(q.cmdline())})
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, KeyError):
                continue
        for pid in set(self._procs) - alive:
            del self._procs[pid]
        return out

    @staticmethod
    def kill(pid, force=False):
        try:
            p = psutil.Process(pid)
            p.kill() if force else p.terminate()
        except psutil.NoSuchProcess:
            raise RuntimeError('Process không tồn tại') from None
        except psutil.AccessDenied:
            raise RuntimeError('Không đủ quyền dừng process này') from None


def list_ports():
    """Cổng đang lắng nghe (TCP LISTEN + UDP). Process của user khác có thể không có tên nếu thiếu quyền."""
    try:
        conns = psutil.net_connections(kind='inet')
    except psutil.AccessDenied:
        conns = []
    names, out, seen = {}, [], set()
    for c in conns:
        if not c.laddr:
            continue
        tcp = c.type == socket.SOCK_STREAM
        if tcp and c.status != psutil.CONN_LISTEN:
            continue
        if not tcp and c.raddr:
            continue
        pid = c.pid or 0
        key = (c.laddr.port, tcp, pid, c.laddr.ip)
        if key in seen:
            continue
        seen.add(key)
        if pid and pid not in names:
            try:
                names[pid] = psutil.Process(pid).name()
            except psutil.Error:
                names[pid] = '?'
        out.append({'port': c.laddr.port, 'protocol': 'TCP' if tcp else 'UDP', 'address': c.laddr.ip, 'pid': pid, 'process': names.get(pid, '—')})
    out.sort(key=lambda p: (p['port'], p['pid']))
    return out
