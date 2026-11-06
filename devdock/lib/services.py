"""Dev Services: chạy/dừng các server dev (npm run dev, cargo run, docker compose up…), gom log, phát hiện xung đột cổng."""
import json
import os
import re
import signal
import socket
import subprocess
import threading
import time
import uuid
from collections import deque

from .sysmon import list_ports

MAX_LINES = 5000
NOISE = ('cannot set terminal process group', 'no job control in this shell', 'Inappropriate ioctl for device')


class Run:
    """Một lần chạy của service: tiến trình, log (có số thứ tự để đọc tăng dần)."""

    def __init__(self, svc):
        self.svc, self.proc, self.started, self.ended, self.rc = svc, None, time.time(), None, None
        self.lines = deque(maxlen=MAX_LINES)
        self.seq = 0
        self.lock = threading.Lock()

    def add(self, line):
        with self.lock:
            self.seq += 1
            self.lines.append((self.seq, line))

    def since(self, seq):
        with self.lock:
            return [(s, ln) for s, ln in self.lines if s > seq], self.seq


class ServiceManager:
    def __init__(self):
        self.runs = {}
        self.lock = threading.Lock()

    # ---- vòng đời
    def start(self, svc):
        sid = svc['id']
        if self.is_running(sid):
            raise RuntimeError('Service đang chạy')
        cwd = os.path.expanduser(svc.get('cwd') or '~')
        if not os.path.isdir(cwd):
            raise RuntimeError(f'Thư mục không tồn tại: {cwd}')
        cmd = (svc.get('cmd') or '').strip()
        if not cmd:
            raise RuntimeError('Chưa có lệnh để chạy')
        run = Run(svc)
        try:
            # bash -ic để nạp PATH của người dùng (nvm, pyenv…); session riêng để dừng được cả cây tiến trình con
            run.proc = subprocess.Popen(['bash', '-ic', cmd], cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, errors='replace', bufsize=1, start_new_session=True, env={**os.environ, 'FORCE_COLOR': '0', 'NO_COLOR': '1'})
        except OSError as e:
            raise RuntimeError(f'Không chạy được: {e}') from None
        with self.lock:
            self.runs[sid] = run
        run.add(f'$ {cmd}   (thư mục: {cwd})')
        threading.Thread(target=self._pump, args=(run,), daemon=True).start()
        return run

    @staticmethod
    def _pump(run):
        for line in run.proc.stdout:
            line = line.rstrip('\n')
            if not any(n in line for n in NOISE):
                run.add(line)
        run.proc.stdout.close()
        run.rc = run.proc.wait()
        run.ended = time.time()
        run.add(f'[đã thoát, mã {run.rc}]')

    def stop(self, sid, wait=5.0):
        run = self.runs.get(sid)
        if not run or run.proc is None or run.proc.poll() is not None:
            return
        try:
            os.killpg(run.proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return

        def reaper():
            try:
                run.proc.wait(timeout=wait)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(run.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        threading.Thread(target=reaper, daemon=True).start()

    def restart(self, svc):
        self.stop(svc['id'])
        run = self.runs.get(svc['id'])
        if run and run.proc:
            try:
                run.proc.wait(timeout=6)
            except subprocess.TimeoutExpired:
                pass
        return self.start(svc)

    def shutdown(self):
        for sid in list(self.runs):
            self.stop(sid, wait=2.0)
        for run in self.runs.values():
            if run.proc:
                try:
                    run.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass

    # ---- trạng thái
    def is_running(self, sid):
        r = self.runs.get(sid)
        return bool(r and r.proc and r.proc.poll() is None)

    def status(self, sid):
        r = self.runs.get(sid)
        if not r:
            return {'state': 'stopped', 'pid': None, 'since': None, 'rc': None}
        running = r.proc.poll() is None
        return {'state': 'running' if running else ('failed' if (r.rc or 0) not in (0, -15, -9) else 'stopped'), 'pid': r.proc.pid if running else None,
                'since': r.started if running else r.ended, 'rc': r.rc}

    def forget(self, sid):
        self.stop(sid, wait=1.0)
        self.runs.pop(sid, None)


# ---------------- Cổng ----------------
def port_open(port, host='127.0.0.1', timeout=0.3):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def port_owner(port):
    """(pid, tên process) đang lắng nghe `port`, hoặc None."""
    for p in list_ports():
        if p['port'] == port and p['protocol'] == 'TCP':
            return p['pid'], p['process']
    return None


# ---------------- Gợi ý lệnh ----------------
def detect_commands(path):
    """Gợi ý lệnh chạy dev cho một thư mục project: [(nhãn, lệnh)]."""
    out = []
    path = os.path.expanduser(path or '')
    try:
        names = set(os.listdir(path))
    except OSError:
        return out
    if 'package.json' in names:
        try:
            with open(os.path.join(path, 'package.json'), encoding='utf-8') as f:
                scripts = (json.load(f) or {}).get('scripts') or {}
        except (OSError, ValueError):
            scripts = {}
        pm = 'pnpm' if 'pnpm-lock.yaml' in names else 'yarn' if 'yarn.lock' in names else 'npm'
        for key in ('dev', 'start', 'serve', 'watch', 'preview'):
            if key in scripts:
                out.append((f'{pm} run {key}', f'{pm} run {key}'))
    if 'manage.py' in names:
        out.append(('Django runserver', 'python manage.py runserver'))
    if 'Cargo.toml' in names:
        out.append(('cargo run', 'cargo run'))
    if 'go.mod' in names:
        out.append(('go run .', 'go run .'))
    for f in ('docker-compose.yml', 'docker-compose.yaml', 'compose.yml', 'compose.yaml'):
        if f in names:
            out.append(('docker compose up', 'docker compose up'))
            break
    if 'pom.xml' in names:
        out.append(('Maven spring-boot:run', 'mvn spring-boot:run'))
    return out


def guess_port(cmd, path):
    """Đoán cổng từ lệnh (--port 3000, -p 8080, PORT=3000)."""
    m = re.search(r'(?:--port[= ]|-p |PORT=)(\d{2,5})', cmd or '')
    return int(m.group(1)) if m else 0


def new_service(name, cwd, cmd, port=0):
    return {'id': uuid.uuid4().hex[:8], 'name': name.strip() or os.path.basename(cwd.rstrip('/')) or 'service', 'cwd': cwd, 'cmd': cmd.strip(), 'port': int(port or 0)}
