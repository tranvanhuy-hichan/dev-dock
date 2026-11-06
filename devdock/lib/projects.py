"""Projects Hub: tìm repo/project trong các thư mục gốc, đọc trạng thái git, mở trong VS Code/terminal, dọn thư mục build."""
import os
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

from ..cleaner import scanner

MARKERS = {'package.json': 'Node', 'Cargo.toml': 'Rust', 'pyproject.toml': 'Python', 'requirements.txt': 'Python', 'setup.py': 'Python', 'go.mod': 'Go',
           'pom.xml': 'Java', 'build.gradle': 'Java', 'build.gradle.kts': 'Java', 'composer.json': 'PHP', 'Gemfile': 'Ruby', 'pubspec.yaml': 'Flutter'}
SKIP = {'node_modules', '.git', '.venv', 'venv', 'target', 'dist', 'build', '__pycache__', '.cache', 'snap'}
# Chạy git an toàn với repo không tin cậy: tắt fsmonitor/hook (cấu hình trong .git/config có thể chạy lệnh tuỳ ý)
GIT = ['git', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null', '-c', 'protocol.ext.allow=never']


def default_roots():
    p = os.path.expanduser('~/Projects')
    return [p] if os.path.isdir(p) else [os.path.expanduser('~')]


def find_projects(roots, max_depth=3):
    """Danh sách đường dẫn project: thư mục có .git hoặc file đánh dấu (package.json, Cargo.toml...). Không đi sâu vào bên trong project."""
    found, seen = [], set()

    def walk(d, depth):
        try:
            names = os.listdir(d)
        except OSError:
            return
        has_git = '.git' in names
        marker = any(m in names for m in MARKERS)
        if (has_git or marker) and d not in seen and depth > 0:
            seen.add(d)
            found.append(d)
            return
        if depth >= max_depth:
            return
        for n in sorted(names):
            if n in SKIP or n.startswith('.'):
                continue
            p = os.path.join(d, n)
            if os.path.isdir(p) and not os.path.islink(p):
                walk(p, depth + 1)
    for r in roots:
        r = os.path.realpath(os.path.expanduser(r))
        if os.path.isdir(r):
            walk(r, 0)
            # chính thư mục gốc cũng có thể là project
            if os.path.isdir(os.path.join(r, '.git')) and r not in seen:
                found.append(r)
    return found


def _git(path, *args, timeout=20):
    try:
        r = subprocess.run([*GIT, '-C', path, *args], capture_output=True, text=True, errors='replace', timeout=timeout,
                           env={**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'LC_ALL': 'C'})
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def parse_status(text):
    """Phân tích `git status --porcelain=v1 -b`: trả {branch, ahead, behind, dirty, untracked, upstream}."""
    out = {'branch': '', 'ahead': 0, 'behind': 0, 'dirty': 0, 'untracked': 0, 'upstream': None, 'detached': False}
    for i, line in enumerate((text or '').splitlines()):
        if i == 0 and line.startswith('## '):
            head = line[3:]
            m = re.search(r'\[(.*?)\]\s*$', head)
            if m:
                head = head[:m.start()].strip()
                a, b = re.search(r'ahead (\d+)', m.group(1)), re.search(r'behind (\d+)', m.group(1))
                out['ahead'], out['behind'] = int(a.group(1)) if a else 0, int(b.group(1)) if b else 0
            if head.startswith('No commits yet on '):
                out['branch'] = head[len('No commits yet on '):]
            elif head.startswith('HEAD (no branch)'):
                out['branch'], out['detached'] = 'HEAD (detached)', True
            elif '...' in head:
                out['branch'], out['upstream'] = head.split('...', 1)
            else:
                out['branch'] = head
        elif line.startswith('??'):
            out['untracked'] += 1
        elif line.strip():
            out['dirty'] += 1
    return out


def lang_of(path):
    try:
        names = set(os.listdir(path))
    except OSError:
        return ''
    for m, lang in MARKERS.items():
        if m in names:
            return lang
    return ''


def inspect(path):
    """Thông tin một project (nhanh: không đo dung lượng)."""
    p = {'path': path, 'name': os.path.basename(path), 'lang': lang_of(path), 'git': os.path.exists(os.path.join(path, '.git')),
         'branch': '', 'ahead': 0, 'behind': 0, 'dirty': 0, 'untracked': 0, 'upstream': None, 'last_ts': 0, 'last_msg': '', 'remote': '', 'build': None}
    if p['git']:
        st = _git(path, 'status', '--porcelain=v1', '-b')
        p.update(parse_status(st))
        log = _git(path, 'log', '-1', '--format=%ct%x1f%s')
        if log and '\x1f' in log:
            ts, msg = log.strip().split('\x1f', 1)
            p['last_ts'], p['last_msg'] = int(ts or 0), msg
        p['remote'] = (_git(path, 'remote', 'get-url', 'origin') or '').strip()
    if not p['last_ts']:
        try:
            p['last_ts'] = int(max(os.stat(os.path.join(path, f)).st_mtime for f in os.listdir(path) if f in MARKERS) if lang_of(path) else os.stat(path).st_mtime)
        except (OSError, ValueError):
            p['last_ts'] = 0
    return p


def scan(roots, progress=None):
    paths = find_projects(roots)
    with ThreadPoolExecutor(max_workers=8) as ex:
        out = list(ex.map(inspect, paths))
    out.sort(key=lambda p: -p['last_ts'])
    return out


def build_dirs(path):
    """Thư mục build/cài đặt có thể dọn trong project (dùng luật của DiskCleaner): [(đường dẫn, rebuild)]."""
    try:
        names = os.listdir(path)
    except OSError:
        return []
    out = []
    for n in names:
        rule = scanner.ARTIFACTS.get(n)
        full = os.path.join(path, n)
        if rule and os.path.isdir(full) and not os.path.islink(full) and scanner._matches(rule, full, names):
            out.append((full, rule['rebuild']))
    return out


def build_size(path):
    return sum(scanner.size_of(d) for d, _ in build_dirs(path))


def clean_build(path):
    """Xoá thư mục build trong project (chỉ trong HOME). Trả số byte giải phóng ước tính."""
    freed = 0
    for d, _ in build_dirs(path):
        scanner.assert_inside_home(d)
        freed += scanner.size_of(d)
        shutil.rmtree(d)
    return freed


def git_fetch(path):
    r = subprocess.run([*GIT, '-C', path, 'fetch', '--quiet', '--prune'], capture_output=True, text=True, timeout=60, env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or 'git fetch lỗi')


def ago(ts):
    if not ts:
        return '—'
    s = max(0, time.time() - ts)
    if s < 3600:
        return f'{int(s // 60)} phút trước' if s >= 60 else 'vừa xong'
    if s < 86400:
        return f'{int(s // 3600)} giờ trước'
    if s < 86400 * 60:
        return f'{int(s // 86400)} ngày trước'
    return f'{int(s // 86400 // 30)} tháng trước'


# ---- mở bằng công cụ ngoài (không đi qua shell) ----
def _spawn(argv, cwd=None):
    subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def open_vscode(path):
    exe = shutil.which('code') or shutil.which('codium')
    if not exe:
        raise RuntimeError('Không tìm thấy lệnh `code` (VS Code)')
    _spawn([exe, path])


def terminal_argv(cmd=None, cwd=None):
    """Lệnh mở terminal hệ thống (chạy `cmd` nếu có). Ưu tiên gnome-terminal, rồi ptyxis, x-terminal-emulator."""
    if shutil.which('gnome-terminal'):
        a = ['gnome-terminal'] + ([f'--working-directory={cwd}'] if cwd else [])
        return a + (['--', *cmd] if cmd else [])
    if shutil.which('ptyxis'):
        a = ['ptyxis', '--new-window'] + ([f'--working-directory={cwd}'] if cwd else [])
        return a + (['-x', ' '.join(shlex_quote(c) for c in cmd)] if cmd else [])
    if shutil.which('x-terminal-emulator'):
        return ['x-terminal-emulator'] + (['-e', *cmd] if cmd else [])
    raise RuntimeError('Không tìm thấy terminal (gnome-terminal/ptyxis)')


def shlex_quote(s):
    import shlex
    return shlex.quote(s)


def open_terminal(path=None, cmd=None):
    _spawn(terminal_argv(cmd, path), cwd=path)


def open_folder(path):
    _spawn(['xdg-open', path])
