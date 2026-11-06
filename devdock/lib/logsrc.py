"""Log Viewer: nguồn log (Docker, journal, file, dev service), nhận diện mức độ, lọc."""
import json
import os
import re
import signal
import subprocess
import threading
from collections import deque

LEVELS = ('error', 'warn', 'info', 'debug')
_PATTERNS = [
    ('error', re.compile(r'\b(ERROR|ERR|FATAL|CRITICAL|PANIC|SEVERE|EXCEPTION|Traceback)\b|\b[Ff]ailed\b')),
    ('warn', re.compile(r'\b(WARN|WARNING)\b')),
    ('debug', re.compile(r'\b(DEBUG|TRACE|VERBOSE)\b')),
    ('info', re.compile(r'\b(INFO|NOTICE)\b')),
]
_JSON_LEVELS = {'error': 'error', 'err': 'error', 'fatal': 'error', 'critical': 'error', 'panic': 'error', 'warn': 'warn', 'warning': 'warn',
                'info': 'info', 'notice': 'info', 'debug': 'debug', 'trace': 'debug', '50': 'error', '40': 'warn', '30': 'info', '20': 'debug', '10': 'debug'}


def level_of(line):
    """'error' | 'warn' | 'info' | 'debug' | None (không rõ)."""
    s = line.strip()
    if s.startswith('{') and s.endswith('}'):
        try:
            v = json.loads(s)
            lv = str(v.get('level') or v.get('severity') or v.get('lvl') or '').lower()
            if lv in _JSON_LEVELS:
                return _JSON_LEVELS[lv]
        except (ValueError, AttributeError):
            pass
    for name, rx in _PATTERNS:
        if rx.search(line):
            return name
    return None


def compile_query(query, regex=False):
    """Trả hàm khớp(line)->bool; ném ValueError nếu regex sai."""
    q = (query or '').strip()
    if not q:
        return lambda line: True
    if regex:
        try:
            rx = re.compile(q, re.I)
        except re.error as e:
            raise ValueError(str(e)) from None
        return lambda line: bool(rx.search(line))
    ql = q.lower()
    return lambda line: ql in line.lower()


# ---------------- Nguồn log ----------------
def docker_argv(cid, tail=300):
    return ['docker', 'logs', '-f', '--tail', str(int(tail)), '-t', cid]


def journal_argv(user=False, unit=None, tail=300):
    a = ['journalctl', '-f', '-n', str(int(tail)), '-o', 'short-iso', '--no-pager']
    if user:
        a.append('--user')
    if unit:
        a += ['-u', unit]
    return a


def file_argv(path, tail=300):
    return ['tail', '-n', str(int(tail)), '-F', '--', path]


class ProcStream:
    """Chạy một lệnh dài hạn (docker logs -f, journalctl -f, tail -F) và gom dòng vào hàng đợi."""

    def __init__(self, argv):
        self.argv, self.q, self.lock, self.proc, self.closed, self.error = argv, deque(maxlen=50000), threading.Lock(), None, False, None
        try:
            self.proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace',
                                         bufsize=1, start_new_session=True)
        except OSError as e:
            self.error = f'Không chạy được {argv[0]}: {e}'
            self.closed = True
            return
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        try:
            for line in self.proc.stdout:
                with self.lock:
                    self.q.append(line.rstrip('\n'))
        finally:
            self.closed = True

    def drain(self, limit=2000):
        with self.lock:
            n = min(limit, len(self.q))
            return [self.q.popleft() for _ in range(n)]

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        if self.proc and self.proc.stdout:
            try:
                self.proc.stdout.close()
            except OSError:
                pass


class ServiceStream:
    """Đọc log của một dev service từ ServiceManager (đọc tăng dần theo số thứ tự)."""

    def __init__(self, manager, sid):
        self.manager, self.sid, self.seq, self.closed, self.error = manager, sid, 0, False, None

    def drain(self, limit=2000):
        run = self.manager.runs.get(self.sid)
        if not run:
            return []
        new, self.seq = run.since(self.seq)
        return [ln for _, ln in new[:limit]] if len(new) <= limit else [ln for _, ln in new]

    def stop(self):
        pass


def open_stream(source, manager=None):
    kind = source[0]
    if kind == 'docker':
        return ProcStream(docker_argv(source[1]))
    if kind == 'journal':
        return ProcStream(journal_argv(user=False))
    if kind == 'journal-user':
        return ProcStream(journal_argv(user=True))
    if kind == 'file':
        if not os.path.isfile(source[1]):
            s = ProcStream(['true'])
            s.error = f'Không phải file: {source[1]}'
            return s
        return ProcStream(file_argv(source[1]))
    if kind == 'service':
        return ServiceStream(manager, source[1])
    raise ValueError(f'Nguồn log không hỗ trợ: {kind}')
