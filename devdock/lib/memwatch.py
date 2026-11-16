"""Bộ nhớ thực sự của từng tiến trình.

RSS đếm trùng: thư viện dùng chung và bộ nhớ chia sẻ được tính cho MỌI process dùng nó, nên tổng RSS thường lớn hơn RAM thật.
PSS (Proportional Set Size, /proc/PID/smaps_rollup) chia đều phần chia sẻ cho các process dùng nó → tổng PSS ≈ RAM thật.
Chỉ đọc được smaps_rollup của process thuộc user hiện tại; process khác dùng RSS (đánh dấu ước lượng)."""
import os
import re
import signal
import time

import psutil

# Không bao giờ cho dừng từ DevDock (làm hỏng phiên đăng nhập / màn hình / mạng).
PROTECTED = {'systemd', 'init', 'dbus-daemon', 'dbus-broker', 'gnome-shell', 'gnome-session-binary', 'gnome-session', 'Xorg', 'Xwayland', 'gdm', 'gdm3', 'gdm-wayland-session',
             'pipewire', 'pipewire-pulse', 'wireplumber', 'sshd', 'NetworkManager', 'systemd-logind', 'polkitd', 'login', 'kwin_wayland', 'plasmashell', 'sddm', 'lightdm'}
_RE_SCOPE = re.compile(r'app-(?:gnome-|flatpak-|snap\.)?(.+?)(?:-\d+)?\.scope')
_KB = re.compile(r'^(\w+):\s+(\d+) kB', re.M)


def _unescape(s):
    return re.sub(r'\\x([0-9a-f]{2})', lambda m: chr(int(m.group(1), 16)), s)


def read_rollup(pid):
    """{'pss','uss','swap','shared'} (byte) từ smaps_rollup, hoặc None nếu không đọc được (khác user/đã thoát)."""
    try:
        with open(f'/proc/{pid}/smaps_rollup', encoding='ascii', errors='replace') as f:
            text = f.read()
    except OSError:
        return None
    d = {k: int(v) * 1024 for k, v in _KB.findall(text)}
    if 'Pss' not in d:
        return None
    return {'pss': d['Pss'], 'uss': d.get('Private_Clean', 0) + d.get('Private_Dirty', 0), 'swap': d.get('SwapPss', d.get('Swap', 0)),
            'shared': d.get('Shared_Clean', 0) + d.get('Shared_Dirty', 0)}


def app_name(name, cmd='', cgroup=''):
    """Tên ứng dụng để gom nhóm: ưu tiên scope của GNOME (app-gnome-google\\x2dchrome-123.scope), rồi vài họ ứng dụng quen thuộc, cuối cùng là tên tiến trình."""
    m = _RE_SCOPE.search(cgroup or '')
    if m:
        n = _unescape(m.group(1)).replace('org.gnome.', '').replace('com.', '')
        return n.replace('-', ' ').strip().title() if n else name
    c = (cmd or '').lower()
    if '/usr/share/code/' in c or '/.vscode' in c or '/code/resources/app' in c or name == 'code':
        return 'VS Code'
    if '/opt/google/chrome' in c or name in ('chrome', 'chromium', 'brave', 'msedge'):
        return 'Chrome'
    if 'firefox' in c or name in ('firefox', 'firefox-bin', 'Isolated Web Co', 'Web Content', 'WebExtensions', 'RDD Process', 'Privileged Cont'):
        return 'Firefox'
    if name in ('llama-server', 'ollama'):
        return 'Ollama'
    if name in ('dockerd', 'containerd', 'docker-proxy', 'containerd-shim-runc-v2'):
        return 'Docker'
    return name


def _cgroup(pid):
    try:
        with open(f'/proc/{pid}/cgroup', encoding='utf-8', errors='replace') as f:
            return f.read().strip().rsplit('/', 1)[-1]
    except OSError:
        return ''


_CACHE = {}  # giữ đối tượng Process giữa các lần gọi để cpu_percent có nghĩa (lần đầu = 0)


def snapshot(own_user_only=False):
    """Danh sách process: pid, ppid, name, app, user, cmd, status, cpu, rss, pss, uss, swap, shared, exact, mine."""
    out, alive = [], set()
    me = os.getuid()
    for p in psutil.process_iter():
        try:
            alive.add(p.pid)
            p = _CACHE.setdefault(p.pid, p)
            with p.oneshot():
                mi = p.memory_info()
                try:
                    mine = p.uids().real == me
                except psutil.Error:
                    mine = False
                if own_user_only and not mine:
                    continue
                name, cmd = p.name(), ' '.join(p.cmdline())
                row = {'pid': p.pid, 'ppid': p.ppid(), 'name': name, 'user': p.username(), 'cmd': cmd, 'status': p.status(), 'cpu': p.cpu_percent(None), 'rss': mi.rss, 'mine': mine}
            if mi.rss == 0 and not cmd:
                continue  # luồng kernel
            r = read_rollup(p.pid) if mine else None
            if r:
                row.update(pss=r['pss'], uss=r['uss'], swap=r['swap'], shared=r['shared'], exact=True)
            else:
                row.update(pss=mi.rss, uss=0, swap=0, shared=0, exact=False)
            row['app'] = app_name(name, cmd, _cgroup(p.pid) if mine else '')
            out.append(row)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    for pid in set(_CACHE) - alive:
        del _CACHE[pid]
    return out


def group(procs):
    """Gom theo ứng dụng: [{'app','count','pss','rss','swap','pids','exact','mine','top'}] sắp theo PSS giảm dần."""
    g = {}
    for p in procs:
        e = g.setdefault(p['app'], {'app': p['app'], 'count': 0, 'cpu': 0.0, 'pss': 0, 'rss': 0, 'swap': 0, 'pids': [], 'exact': True, 'mine': True, 'top': p})
        e['count'] += 1
        e['cpu'] += p.get('cpu', 0.0)
        e['pss'] += p['pss']
        e['rss'] += p['rss']
        e['swap'] += p['swap']
        e['pids'].append(p['pid'])
        e['exact'] = e['exact'] and p['exact']
        e['mine'] = e['mine'] and p['mine']
        if p['pss'] > e['top']['pss']:
            e['top'] = p
    return sorted(g.values(), key=lambda x: -x['pss'])


def summary(procs, vm=None):
    """Tóm tắt: RAM dùng thật, tổng PSS, phần cache/buffers, số process chỉ có số ước lượng."""
    vm = vm or psutil.virtual_memory()
    used = vm.total - vm.available
    pss = sum(p['pss'] for p in procs)
    return {'total': vm.total, 'used': used, 'available': vm.available, 'pss': pss, 'cache': max(0, getattr(vm, 'cached', 0) + getattr(vm, 'buffers', 0)),
            'approx': sum(1 for p in procs if not p['exact']), 'rss_sum': sum(p['rss'] for p in procs)}


# ---------------- xu hướng (phát hiện rò rỉ) ----------------
class Trend:
    """Lưu lịch sử PSS theo pid để tính mức tăng trong khoảng thời gian gần đây."""

    def __init__(self, keep=900):
        self.keep, self.h = keep, {}

    def update(self, procs, now=None):
        now = time.time() if now is None else now
        alive = set()
        for p in procs:
            alive.add(p['pid'])
            lst = self.h.setdefault(p['pid'], [])
            lst.append((now, p['pss']))
            while lst and now - lst[0][0] > self.keep:
                lst.pop(0)
        for pid in set(self.h) - alive:
            del self.h[pid]

    def delta(self, pid, window=300, now=None):
        """(mức tăng byte, số giây thực tế) so với mẫu cũ nhất trong `window` giây, hoặc (0, 0) nếu chưa đủ dữ liệu."""
        lst = self.h.get(pid) or []
        if len(lst) < 2:
            return 0, 0
        now = lst[-1][0] if now is None else now
        old = next(((t, v) for t, v in lst if now - t <= window), lst[0])
        return lst[-1][1] - old[1], int(lst[-1][0] - old[0])


# ---------------- dừng tiến trình ----------------
def can_kill(p):
    """(được_không, lý_do)."""
    pid = p.get('pid', 0)
    if pid <= 1:
        return False, 'Tiến trình hệ thống (PID ≤ 1)'
    if pid == os.getpid():
        return False, 'Đây là chính DevDock'
    if not p.get('mine'):
        return False, 'Process của user khác — cần quyền root'
    if p.get('name') in PROTECTED:
        return False, f"{p['name']} là thành phần hệ thống/phiên đăng nhập"
    return True, ''


def ancestors(pid=None):
    """Tập PID tổ tiên của tiến trình hiện tại (terminal/launcher đã mở DevDock): không cho dừng nhầm khi dừng cả nhóm."""
    out, pid = set(), pid or os.getpid()
    try:
        p = psutil.Process(pid)
        while p.ppid():
            p = psutil.Process(p.ppid())
            out.add(p.pid)
    except psutil.Error:
        pass
    return out


def stop_many(pids, wait=3.0, force=False):
    """Gửi SIGTERM tới các pid (đã kiểm tra can_kill ở nơi gọi), chờ `wait` giây. Trả về {'stopped': [...], 'alive': [...], 'denied': [...]}.
    force=True: SIGKILL các process còn sống sau khi chờ."""
    procs, denied = [], []
    for pid in pids:
        try:
            p = psutil.Process(pid)
            if pid <= 1 or pid == os.getpid():
                denied.append(pid)
                continue
            p.send_signal(signal.SIGTERM)
            procs.append(p)
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            denied.append(pid)
    gone, alive = psutil.wait_procs(procs, timeout=wait)
    if force and alive:
        for p in alive:
            try:
                p.send_signal(signal.SIGKILL)
            except psutil.Error:
                pass
        more_gone, alive = psutil.wait_procs(alive, timeout=2)
        gone += more_gone
    return {'stopped': [p.pid for p in gone], 'alive': [p.pid for p in alive], 'denied': denied}
