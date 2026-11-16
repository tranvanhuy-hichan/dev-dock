"""Giám sát tài nguyên: engine cảnh báo thuần logic (test được bằng mẫu giả + đồng hồ giả), bộ lấy mẫu psutil nhẹ,
cấu hình/lịch sử lưu bền, autostart, và WatchService chạy ở cấp ứng dụng (không phụ thuộc việc trang Giám sát đã mở)."""
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

import psutil

HISTORY_MAX = 200
RULE_TITLES = {
    'ram_low': 'RAM khả dụng thấp', 'swap_high': 'Swap gần đầy', 'disk_low': 'Ổ đĩa sắp đầy', 'hog_mem': 'Tiến trình ngốn RAM', 'hog_cpu': 'Tiến trình ngốn CPU',
    'service_died': 'Dev service dừng bất thường', 'docker_died': 'Container Docker lỗi', 'port_conflict': 'Cổng của dev service bị chiếm',
    'mem_leak': 'Có thể rò rỉ bộ nhớ', 'oom_kill': 'Hệ thống buộc dừng tiến trình vì hết RAM', 'load_high': 'Tải CPU của máy quá cao', 'temp_high': 'Nhiệt độ quá cao',
    'battery_low': 'Pin yếu', 'zombies': 'Nhiều tiến trình zombie',
}
DEFAULTS = {
    'rules': {
        'ram_low': {'enabled': True, 'pct': 10, 'minutes': 1},
        'swap_high': {'enabled': True, 'pct': 80, 'minutes': 1},
        'disk_low': {'enabled': True, 'pct': 10, 'gb': 5},
        'hog_mem': {'enabled': True, 'mb': 1024, 'minutes': 5},
        'hog_cpu': {'enabled': True, 'pct': 80, 'minutes': 3},
        'service_died': {'enabled': True},
        'docker_died': {'enabled': True},
        'port_conflict': {'enabled': True},
        'mem_leak': {'enabled': True, 'mb': 300, 'minutes': 10},
        'oom_kill': {'enabled': True},
        'load_high': {'enabled': True, 'x': 1.5, 'minutes': 3},
        'temp_high': {'enabled': True, 'celsius': 90, 'minutes': 1},
        'battery_low': {'enabled': True, 'pct': 15},
        'zombies': {'enabled': True, 'count': 5, 'minutes': 5},
    },
    'cooldown_min': 30, 'ignore': [], 'background': False, 'interval': 10,
}


def human(n):
    n = float(n or 0)
    for u in ('B', 'KB', 'MB', 'GB', 'TB'):
        if abs(n) < 1024 or u == 'TB':
            return f'{n:.0f} {u}' if u == 'B' else f'{n:.1f} {u}'
        n /= 1024


# ======================================================================================
# Đường dẫn + lưu bền
# ======================================================================================
def config_path():
    return os.path.join(os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config'), 'devdock', 'watch.json')


def history_path():
    return os.path.join(os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share'), 'devdock', 'watch_history.json')


def atomic_write(path, text, mode=0o600):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix='.watch-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _num(v, default, lo=0, hi=10 ** 9):
    try:
        return max(lo, min(hi, float(v))) if not isinstance(v, bool) else default
    except (TypeError, ValueError):
        return default


def load_config(path=None):
    """Đọc cấu hình, trộn với mặc định, ép kiểu/khoảng giá trị (file hỏng thì dùng mặc định)."""
    cfg = copy.deepcopy(DEFAULTS)
    try:
        with open(path or config_path(), encoding='utf-8') as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return cfg
    if not isinstance(raw, dict):
        return cfg
    for name, rule in cfg['rules'].items():
        r = (raw.get('rules') or {}).get(name)
        if isinstance(r, dict):
            for k, dv in rule.items():
                if k in r:
                    rule[k] = bool(r[k]) if k == 'enabled' else _num(r[k], dv, 0, 10 ** 7)
    cfg['cooldown_min'] = _num(raw.get('cooldown_min'), 30, 0, 24 * 60)
    cfg['interval'] = _num(raw.get('interval'), 10, 2, 3600)
    cfg['background'] = bool(raw.get('background', False))
    cfg['ignore'] = [str(x) for x in raw.get('ignore', []) if isinstance(x, str) and x.strip()][:200]
    return cfg


def save_config(cfg, path=None):
    atomic_write(path or config_path(), json.dumps(cfg, ensure_ascii=False, indent=1))


def load_history(path=None):
    try:
        with open(path or history_path(), encoding='utf-8') as f:
            data = json.load(f)
        return [a for a in data if isinstance(a, dict) and 'title' in a and 't' in a][:HISTORY_MAX] if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def save_history(hist, path=None):
    atomic_write(path or history_path(), json.dumps(hist[:HISTORY_MAX], ensure_ascii=False))


# ======================================================================================
# Autostart
# ======================================================================================
AUTOSTART_NAME = 'vn.makeai.DevDock.desktop'


def autostart_path():
    return os.path.join(os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config'), 'autostart', AUTOSTART_NAME)


def devdock_exec():
    """Đường dẫn tuyệt đối của lệnh devdock (autostart không chắc có ~/.local/bin trong PATH)."""
    for cand in (shutil.which('devdock'), os.path.expanduser('~/.local/bin/devdock')):
        if cand and os.path.exists(cand):
            return cand
    return 'devdock'


def autostart_enabled():
    return os.path.exists(autostart_path())


def set_autostart(enabled):
    p = autostart_path()
    if not enabled:
        try:
            os.unlink(p)
        except FileNotFoundError:
            pass
        return
    atomic_write(p, '[Desktop Entry]\nType=Application\nName=DevDock\nComment=Trung tâm cho developer: project, dev service, Docker, giám sát\n'
                    f'Exec={devdock_exec()} --hidden\nIcon=vn.makeai.DevDock\nTerminal=false\nX-GNOME-Autostart-enabled=true\n', 0o644)


# ======================================================================================
# Bộ lấy mẫu (nhẹ: psutil, giữ đối tượng Process để cpu_percent có nghĩa)
# ======================================================================================
# Runner suy luận AI: nạp model nặng RAM và kéo CPU khi đang trả lời là bình thường (Ollama tự nạp/gỡ model theo yêu cầu), không báo "ngốn tài nguyên".
INFERENCE_RUNNERS = {'llama-server', 'ollama', 'ollama_llama_server'}


class SystemSampler:
    def __init__(self):
        self._procs = {}
        psutil.cpu_percent(None)

    @staticmethod
    def _mount_of(path):
        path = os.path.realpath(path)
        while not os.path.ismount(path) and path != os.path.dirname(path):
            path = os.path.dirname(path)
        return path

    def _disks(self):
        out, seen = [], set()
        for label, path in (('/', '/'), ('~', os.path.expanduser('~'))):
            try:
                dev = os.stat(path).st_dev
                if dev in seen:
                    continue
                seen.add(dev)
                u = psutil.disk_usage(path)
                out.append({'mount': '/' if label == '/' else self._mount_of(path), 'free': u.free, 'total': u.total})
            except OSError:
                continue
        return out

    def _processes(self):
        out, alive = [], set()
        self.zombies = 0
        for p in psutil.process_iter():
            try:
                pid = p.pid
                alive.add(pid)
                q = self._procs.setdefault(pid, p)
                with q.oneshot():
                    rss, cpu = q.memory_info().rss, q.cpu_percent(None)
                    if rss < 32 * 1024 * 1024 and cpu < 5:
                        continue  # không đáng theo dõi: giữ danh sách nhỏ
                    out.append({'pid': pid, 'name': q.name(), 'rss': rss, 'cpu': cpu, 'created': q.create_time()})
            except psutil.ZombieProcess:
                self.zombies += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        for pid in set(self._procs) - alive:
            self._procs.pop(pid, None)
        return out

    @staticmethod
    def _oom_kills():
        try:
            with open('/proc/vmstat', encoding='ascii') as f:
                for line in f:
                    if line.startswith('oom_kill '):
                        return int(line.split()[1])
        except (OSError, ValueError):
            pass
        return None

    def _temperature(self):
        """(°C cao nhất, tên cảm biến) của CPU/GPU, hoặc (None, '')."""
        best = (None, '')
        try:
            for name, entries in (psutil.sensors_temperatures() or {}).items():
                if name in ('coretemp', 'k10temp', 'zenpower', 'cpu_thermal', 'acpitz'):
                    for e in entries:
                        if e.current and (best[0] is None or e.current > best[0]):
                            best = (e.current, 'CPU')
        except (AttributeError, OSError):
            pass
        gpu = self._gpu_temp()
        if gpu is not None and (best[0] is None or gpu > best[0]):
            best = (gpu, 'GPU')
        return best

    def _gpu_temp(self):
        now = time.time()
        if now - getattr(self, '_gpu_t', 0) < 30:
            return getattr(self, '_gpu_v', None)
        self._gpu_t, self._gpu_v = now, None
        if shutil.which('nvidia-smi'):
            try:
                r = subprocess.run(['nvidia-smi', '--query-gpu=temperature.gpu', '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=4)
                if r.returncode == 0:
                    self._gpu_v = max(float(x) for x in r.stdout.split() if x.replace('.', '', 1).isdigit())
            except (OSError, subprocess.TimeoutExpired, ValueError):
                pass
        return self._gpu_v

    def sample(self):
        vm, sw = psutil.virtual_memory(), psutil.swap_memory()
        procs = self._processes()
        try:
            bat = psutil.sensors_battery()
        except (AttributeError, OSError):
            bat = None
        temp, temp_name = self._temperature()
        try:
            load1 = os.getloadavg()[0]
        except OSError:
            load1 = 0.0
        return {'oom_kills': self._oom_kills(), 'load1': load1, 'cores': psutil.cpu_count() or 1, 'zombies': self.zombies, 'temp': temp, 'temp_name': temp_name,
                'battery': {'pct': bat.percent, 'plugged': bool(bat.power_plugged)} if bat else None, 't': time.time(), 'mem_total': vm.total, 'mem_avail': vm.available, 'mem_avail_pct': vm.available / vm.total * 100 if vm.total else 100.0,
                'swap_total': sw.total, 'swap_used': sw.used, 'swap_pct': sw.percent if sw.total else 0.0, 'disks': self._disks(), 'procs': procs}


def service_samples(manager, services):
    """Trạng thái các dev service đã cấu hình (đọc từ ServiceManager), kèm chủ sở hữu cổng nếu service đang chạy."""
    out, need_ports = [], False
    for s in services:
        run = manager.runs.get(s['id'])
        running = bool(run and run.proc and run.proc.poll() is None)
        out.append({'id': s['id'], 'name': s.get('name', s['id']), 'port': int(s.get('port') or 0), 'running': running, 'pid': run.proc.pid if running else None,
                    'run_id': run.started if run else None, 'rc': run.rc if run else None, 'user_stopped': bool(run and getattr(run, 'user_stopped', False))})
        need_ports = need_ports or (running and bool(s.get('port')))
    owners = {}
    if need_ports:
        try:
            from .sysmon import list_ports
            ports = {p['port']: p for p in list_ports() if p['protocol'] == 'TCP'}
        except Exception:  # noqa: BLE001
            ports = {}
        for sv in out:
            if sv['running'] and sv['port'] and sv['port'] in ports:
                o = ports[sv['port']]
                tree = {sv['pid']}
                try:
                    tree |= {c.pid for c in psutil.Process(sv['pid']).children(recursive=True)}
                except psutil.Error:
                    pass
                owners[sv['port']] = {'pid': o['pid'], 'name': o['process'], 'ours': (o['pid'] in tree) or not o['pid']}
    return out, owners


def track_user_stops(manager):
    """Đánh dấu các lần chạy bị NGƯỜI DÙNG dừng (qua manager.stop: nút Dừng, Chạy lại, Xoá, thoát app) để không báo nhầm là sự cố."""
    if getattr(manager, '_watch_tracked', False):
        return
    orig = manager.stop

    def stop(sid, *a, **kw):
        run = manager.runs.get(sid)
        if run is not None:
            run.user_stopped = True
        return orig(sid, *a, **kw)
    manager.stop = stop
    manager._watch_tracked = True


# ======================================================================================
# Engine
# ======================================================================================
class Engine:
    """Nhận các mẫu qua tick(sample, now) và quyết định cảnh báo. Không phụ thuộc GTK/thời gian thật."""

    def __init__(self, config, history=None, notify=None, clock=time.time, on_history=None):
        self.config, self.history, self.notify, self.clock, self.on_history = config, history if history is not None else [], notify, clock, on_history
        self.since, self.last, self.hog_info = {}, {}, {}
        self.prev_containers, self.reported_runs, self.cur_containers = {}, set(), None
        self.last_sample = None
        self.rss_hist, self.prev_oom = {}, None
        self.unread = 0

    # ---- tiện ích
    def rule(self, name):
        return self.config['rules'][name]

    def on(self, name):
        return bool(self.rule(name).get('enabled'))

    def _ignored(self, name):
        n = name.lower()
        return n in INFERENCE_RUNNERS or n in {x.lower() for x in self.config.get('ignore', [])}

    def _fire(self, key, title, body, level, now):
        """Bắn một cảnh báo nếu hết cooldown của khoá. Trả về alert hoặc None."""
        cd = float(self.config.get('cooldown_min', 30)) * 60
        if now - self.last.get(key, float('-inf')) < cd:
            return None
        self.last[key] = now
        alert = {'id': uuid.uuid4().hex[:10], 'key': key, 'title': title, 'body': body, 'level': level, 't': now}
        self.history.insert(0, alert)
        del self.history[HISTORY_MAX:]
        self.unread += 1
        if self.on_history:
            try:
                self.on_history(self.history)
            except OSError:
                pass
        if self.notify:
            try:
                self.notify(alert)
            except Exception:  # noqa: BLE001  (lỗi thông báo không được làm hỏng engine)
                pass
        return alert

    @staticmethod
    def _top(procs, n=3, key='rss'):
        top = sorted(procs, key=lambda p: -p[key])[:n]
        return ', '.join(f"{p['name']} {human(p['rss']) if key == 'rss' else str(round(p['cpu'])) + '%'}" for p in top)

    # ---- một lượt kiểm tra
    def tick(self, s, now=None):
        now = self.clock() if now is None else now
        self.last_sample = s
        conds = []  # (key, tiêu_đề, nội_dung, mức, giữ_tối_thiểu_giây)
        hogs = {}
        procs = s.get('procs') or []
        if self.on('ram_low'):
            r = self.rule('ram_low')
            if s.get('mem_avail_pct', 100) < r['pct']:
                conds.append(('ram_low', 'RAM khả dụng thấp', f"Chỉ còn {s['mem_avail_pct']:.0f}% RAM khả dụng ({human(s.get('mem_avail', 0))}). Dùng nhiều nhất: {self._top(procs)}", 'warn', r['minutes'] * 60))
        if self.on('swap_high'):
            r = self.rule('swap_high')
            if s.get('swap_pct', 0) > r['pct']:
                conds.append(('swap_high', 'Swap gần đầy', f"Swap đã dùng {s['swap_pct']:.0f}% ({human(s.get('swap_used', 0))}). Máy có thể rất chậm.", 'warn', r['minutes'] * 60))
        if self.on('disk_low'):
            r = self.rule('disk_low')
            for d in s.get('disks') or []:
                if d['total'] > 0 and (d['free'] / d['total'] * 100 < r['pct'] or d['free'] / 1e9 < r['gb']):
                    conds.append((f"disk:{d['mount']}", f"Ổ đĩa {d['mount']} sắp đầy", f"Còn trống {human(d['free'])} / {human(d['total'])} ({d['free'] / d['total'] * 100:.0f}%).", 'error', 0))
        for kind, rname in (('ram', 'hog_mem'), ('cpu', 'hog_cpu')):
            if not self.on(rname):
                continue
            r = self.rule(rname)
            for p in procs:
                if self._ignored(p['name']):
                    continue
                over = p['rss'] > r['mb'] * 1024 * 1024 if kind == 'ram' else p['cpu'] > r['pct']
                if not over:
                    continue
                key = f"{rname}:{p['pid']}:{p['name']}"
                need = r['minutes'] * 60
                text = (f"{p['name']} (PID {p['pid']}) đang dùng {human(p['rss'])} RAM" if kind == 'ram' else f"{p['name']} (PID {p['pid']}) đang chiếm {p['cpu']:.0f}% CPU")
                conds.append((key, 'Tiến trình ngốn RAM' if kind == 'ram' else 'Tiến trình ngốn CPU', text + f" liên tục từ {int(need // 60)} phút trước. Có thể là ứng dụng chạy nền bị lỗi.", 'warn', need))
                hogs[key] = {'key': key, 'kind': kind, 'pid': p['pid'], 'name': p['name'], 'rss': p['rss'], 'cpu': p['cpu'], 'need': need}
        self._system_conds(s, procs, conds, now)
        # dev service: cổng bị process khác chiếm
        svcs = s.get('services') or []
        owners = s.get('port_owners') or {}
        if self.on('port_conflict'):
            for sv in svcs:
                o = owners.get(sv['port']) if sv.get('running') and sv.get('port') else None
                if o and not o.get('ours'):
                    conds.append((f"port:{sv['id']}", f"Cổng {sv['port']} của {sv['name']} bị chiếm", f"{sv['name']} đang chạy nhưng cổng {sv['port']} do {o['name']} (PID {o['pid']}) giữ. Truy cập cổng này sẽ vào nhầm process.", 'warn', 10))
        # container unhealthy / restart liên tục
        conts = s.get('containers')
        if conts is not None:
            self.cur_containers = conts  # Docker chỉ được hỏi ~30s một lần: giữ danh sách gần nhất để điều kiện tích luỹ thời gian
        if self.cur_containers is not None and self.on('docker_died'):
            for c in self.cur_containers:
                st = c.get('status', '')
                if '(unhealthy)' in st:
                    conds.append((f"unhealthy:{c['id']}", f"Container {c['name']} không khoẻ", f"Docker báo unhealthy: {st}", 'warn', 0))
                if c.get('state') == 'restarting':
                    conds.append((f"restart:{c['id']}", f"Container {c['name']} restart liên tục", f"Container đang ở trạng thái restarting: {st}", 'error', 60))

        fired = []
        active = set()
        for key, title, body, level, need in conds:
            active.add(key)
            self.since.setdefault(key, now)
            if key in hogs:
                hogs[key]['since'] = self.since[key]
            if now - self.since[key] >= need:
                a = self._fire(key, title, body, level, now)
                if a:
                    fired.append(a)
                    if key in hogs:
                        hogs[key]['alerted'] = True
        for k in list(self.since):  # điều kiện đã hết => đặt lại bộ đếm thời gian
            if k not in active:
                del self.since[k]
        self.hog_info = hogs
        fired += self._events(svcs, conts, now)
        fired += self._oom(s, procs, now)
        return fired

    # ---- các điều kiện mới: rò rỉ, tải, nhiệt độ, pin, zombie
    def _system_conds(self, s, procs, conds, now):
        if self.on('mem_leak'):
            r = self.rule('mem_leak')
            win, need = r['minutes'] * 60, r['mb'] * 1024 * 1024
            seen = set()
            for p in procs:
                if self._ignored(p['name']):
                    continue
                seen.add(p['pid'])
                h = self.rss_hist.setdefault(p['pid'], [])
                h.append((now, p['rss']))
                while h and now - h[0][0] > win * 1.5:
                    h.pop(0)
                base = next(((t, v) for t, v in reversed(h) if now - t >= win), None)
                if base is None or now - p.get('created', now) < win or p['rss'] - base[1] < need:
                    continue
                span = [v for t, v in h if t >= base[0]]
                steps = list(zip(span, span[1:]))
                if not steps or sum(1 for a, b in steps if b >= a * 0.97) / len(steps) < 0.8:
                    continue  # lên xuống thất thường: không phải rò rỉ đều
                conds.append((f"leak:{p['pid']}:{p['name']}", 'Có thể rò rỉ bộ nhớ',
                              f"{p['name']} (PID {p['pid']}) tăng từ {human(base[1])} lên {human(p['rss'])} trong {int((now - base[0]) // 60)} phút mà không giảm lại. Nếu nó cứ tăng, hãy khởi động lại ứng dụng đó.", 'warn', 0))
            for pid in set(self.rss_hist) - seen:
                del self.rss_hist[pid]
        if self.on('load_high') and s.get('load1') is not None and s.get('cores'):
            r = self.rule('load_high')
            if s['load1'] > s['cores'] * r['x']:
                conds.append(('load_high', 'Tải CPU của máy quá cao', f"Tải trung bình {s['load1']:.1f} trên {s['cores']} nhân (gấp {s['load1'] / s['cores']:.1f} lần). Nhiều CPU nhất: {self._top(procs, key='cpu')}", 'warn', r['minutes'] * 60))
        if self.on('temp_high') and s.get('temp') is not None:
            r = self.rule('temp_high')
            if s['temp'] >= r['celsius']:
                conds.append(('temp_high', 'Nhiệt độ quá cao', f"{s.get('temp_name') or 'Máy'} đang {s['temp']:.0f}°C (ngưỡng {r['celsius']:.0f}°C). Kiểm tra quạt/tản nhiệt, giảm tải hoặc đặt máy ở nơi thoáng.",
                              'error' if s['temp'] >= r['celsius'] + 5 else 'warn', r['minutes'] * 60))
        bat = s.get('battery')
        if self.on('battery_low') and bat and not bat['plugged'] and bat['pct'] <= self.rule('battery_low')['pct']:
            conds.append(('battery_low', 'Pin yếu', f"Pin còn {bat['pct']:.0f}% và đang không cắm sạc. Lưu việc đang làm và cắm sạc.", 'error' if bat['pct'] <= 5 else 'warn', 0))
        if self.on('zombies') and (s.get('zombies') or 0) >= self.rule('zombies')['count']:
            r = self.rule('zombies')
            conds.append(('zombies', 'Nhiều tiến trình zombie', f"Có {s['zombies']} tiến trình zombie (đã thoát nhưng tiến trình cha chưa dọn). Thường do ứng dụng cha lỗi; khởi động lại ứng dụng đó sẽ dọn sạch.", 'warn', r['minutes'] * 60))

    def _oom(self, s, procs, now):
        """Kernel tăng bộ đếm oom_kill = vừa buộc dừng một tiến trình vì hết RAM (đáng báo mỗi lần, không dùng cooldown theo khoá chung)."""
        n = s.get('oom_kills')
        if n is None:
            return []
        prev, self.prev_oom = self.prev_oom, n
        if prev is None or n <= prev or not self.on('oom_kill'):
            return []
        a = self._fire(f'oom:{n}', 'Hệ thống buộc dừng tiến trình vì hết RAM', f"Kernel vừa dừng {n - prev} tiến trình do thiếu bộ nhớ (OOM). Đang dùng nhiều RAM nhất: {self._top(procs)}. Cân nhắc đóng bớt ứng dụng hoặc tăng swap.", 'error', now)
        return [a] if a else []

    # ---- sự kiện (chuyển trạng thái)
    def _events(self, svcs, conts, now):
        out = []
        if self.on('service_died'):
            for sv in svcs:
                rid = sv.get('run_id')
                if sv.get('running') or rid is None or (sv['id'], rid) in self.reported_runs:
                    continue
                self.reported_runs.add((sv['id'], rid))
                if sv.get('user_stopped'):
                    continue
                rc = sv.get('rc')
                title = f"{sv['name']} bị lỗi (mã {rc})" if rc not in (0, None) else f"{sv['name']} đã dừng"
                body = ('Tiến trình thoát với mã lỗi — xem log ở Log Viewer.' if rc not in (0, None) else 'Tiến trình đã tự thoát mà bạn không bấm dừng.') + (' (bị tín hiệu kill từ bên ngoài)' if rc in (-9, -15) else '')
                a = self._fire(f"svc:{sv['id']}", title, body, 'error' if rc not in (0, None) else 'warn', now)
                if a:
                    out.append(a)
        if conts is not None:
            if self.on('docker_died'):
                for c in conts:
                    prev = self.prev_containers.get(c['id'])
                    if prev == 'running' and c.get('state') in ('exited', 'dead'):
                        m = re.search(r'Exited \((\d+)\)', c.get('status', ''))
                        code = int(m.group(1)) if m else None
                        if c['state'] == 'dead' or (code not in (0, 143)):
                            a = self._fire(f"docker:{c['name']}", f"Container {c['name']} thoát bất thường", f"Trạng thái: {c.get('status', '')}. Mã 137 thường là bị kill hoặc hết RAM (OOM).", 'error', now)
                            if a:
                                out.append(a)
            self.prev_containers = {c['id']: c.get('state') for c in conts}
        return out

    # ---- cho giao diện
    def hogs(self):
        """Tiến trình đang vượt ngưỡng (kể cả chưa đủ thời gian để cảnh báo), kèm thời gian đã vượt."""
        now = self.clock()
        out = []
        for h in self.hog_info.values():
            since = h.get('since', now)
            out.append({**h, 'age': max(0.0, now - since), 'alerted': bool(h.get('alerted'))})
        return sorted(out, key=lambda h: -h['age'])

    def clear_history(self):
        self.history.clear()
        self.unread = 0
        if self.on_history:
            self.on_history(self.history)


# ======================================================================================
# WatchService: chạy ở cấp ứng dụng
# ======================================================================================
class WatchService:
    def __init__(self, window, sampler=None, clock=time.time):
        self.window, self.clock = window, clock
        self.config = load_config()
        self.engine = Engine(self.config, load_history(), notify=self._notify, clock=clock, on_history=lambda h: save_history(h))
        self.sampler = sampler or SystemSampler()
        self.busy, self.ticks, self.timer = False, 0, 0
        self.has_docker = bool(shutil.which('docker'))
        track_user_stops(window.services)
        self.listeners = []

    # ---- vòng đời
    def start(self):
        """Chế độ kiểm thử/chụp ảnh không tự chạy (tránh thông báo thật); đặt DEVDOCK_WATCH=1 để bật."""
        if (os.environ.get('DEVDOCK_TEST') or os.environ.get('DEVDOCK_SHOTS')) and not os.environ.get('DEVDOCK_WATCH'):
            return
        from gi.repository import GLib
        GLib.timeout_add_seconds(4, lambda: (self.tick_now(), False)[1])
        self.timer = GLib.timeout_add_seconds(int(self.config['interval']), lambda: (self.tick_now(), True)[1])

    def stop(self):
        if self.timer:
            from gi.repository import GLib
            GLib.source_remove(self.timer)
            self.timer = 0

    def _notify(self, alert):
        self.window.notify(alert['title'], alert['body'], alert['key'], urgent=alert['level'] == 'error')

    def _collect(self):
        s = self.sampler.sample()
        svcs, owners = service_samples(self.window.services, self.window.store['services'])
        s['services'], s['port_owners'] = svcs, owners
        if self.has_docker and self.engine.on('docker_died') and self.ticks % 3 == 0:  # Docker: ~30 giây một lần
            try:
                from . import dockercli
                s['containers'] = dockercli.containers(True)
            except Exception:  # noqa: BLE001
                s['containers'] = None
        else:
            s['containers'] = None  # giữa hai lần hỏi Docker: engine giữ nguyên trạng thái cũ
        self.ticks += 1
        return s

    def tick_now(self, sync=False):
        if self.busy:
            return
        self.busy = True
        if sync:
            try:
                self.apply(self._collect())
            finally:
                self.busy = False
            return
        from ..ui import bg

        def done(s, err):
            self.busy = False
            if s is not None:
                self.apply(s)
        bg(self._collect, done)

    def apply(self, sample, now=None):
        fired = self.engine.tick(sample, now)
        for cb in list(self.listeners):
            cb(fired)
        return fired

    # ---- cấu hình
    def save(self):
        try:
            save_config(self.config)
        except OSError:
            pass

    def set_background(self, on):
        self.config['background'] = bool(on)
        self.save()
        app = self.window.get_application()
        if hasattr(app, 'set_hold'):
            app.set_hold(bool(on))
