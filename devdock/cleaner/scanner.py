"""Quét và dọn rác trên đĩa.

Không phụ thuộc GTK nên dùng được độc lập: python3 -m diskcleaner scan
"""
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

HOME = os.path.expanduser('~')
DAY = 86400
MB = 1024 * 1024
GB = 1024 ** 3
ACTIVE_DAYS = 14   # project có hoạt động trong ngần này ngày => "cần xem lại"
MAX_DEPTH = 7
SKIP_DIRS = {'snap', 'node_modules', '.git'}

# Thư mục sinh ra khi build / cài đặt: xoá đi vẫn tạo lại được. `need` giúp tránh xoá nhầm
# những thư mục trùng tên nhưng chứa mã nguồn thật (ví dụ build/ của Electron, bin/ của shell).
ARTIFACTS = {
    'node_modules': {'rebuild': 'npm install'},
    '.next': {'need': ['package.json'], 'rebuild': 'npm run build'},
    'target': {'need': ['pom.xml', 'build.gradle', 'build.gradle.kts', 'Cargo.toml'], 'rebuild': 'mvn package / cargo build'},
    'bin': {'need_ext': '.csproj', 'rebuild': 'dotnet build'},
    'obj': {'need_ext': '.csproj', 'rebuild': 'dotnet build'},
    '.venv': {'inside': 'pyvenv.cfg', 'rebuild': 'python -m venv .venv && pip install -r requirements.txt'},
    'venv': {'inside': 'pyvenv.cfg', 'rebuild': 'python -m venv venv && pip install -r requirements.txt'},
    '.gradle': {'need': ['build.gradle', 'build.gradle.kts', 'settings.gradle'], 'rebuild': 'gradle build'},
}


def h(*parts):
    return os.path.join(HOME, *parts)


def _caches():
    # Hàm (không phải hằng) để đường dẫn luôn tính theo HOME hiện tại.
    code = [h('.config/Code', d) for d in ('Cache', 'CachedData', 'CachedExtensionVSIXs', 'Code Cache', 'GPUCache')]
    return [
        {'paths': [h('.npm/_cacache')], 'title': 'npm cache', 'note': 'npm tự tải lại khi cần'},
        {'paths': [h('.npm/_npx')], 'title': 'npx cache', 'note': 'npx tự tải lại khi chạy'},
        {'paths': [h('.cache/pip')], 'title': 'pip cache', 'note': 'pip tự tải lại khi cần'},
        {'paths': [h('.cache/ms-playwright')], 'title': 'Trình duyệt của Playwright', 'note': 'Tải lại bằng npx playwright install'},
        {'paths': [h('.cache/typescript')], 'title': 'TypeScript cache', 'note': 'Tự tạo lại'},
        {'paths': [h('.cache/prisma')], 'title': 'Prisma engines', 'note': 'Tải lại khi chạy Prisma'},
        {'paths': [h('.cache/electron')], 'title': 'Electron binary cache', 'note': 'Tải lại khi npm install'},
        {'paths': [h('.cache/yarn')], 'title': 'Yarn cache', 'note': 'Yarn tự tải lại khi cần'},
        {'paths': [h('.local/share/pnpm/store'), h('.cache/pnpm')], 'title': 'pnpm store', 'note': 'pnpm tải lại khi cài package'},
        {'paths': [h('.gradle/caches')], 'title': 'Gradle cache', 'note': 'Gradle tải lại khi build', 'level': 'review'},
        {'paths': [h('.m2/repository')], 'title': 'Maven repository', 'note': 'Maven tải lại khi build', 'level': 'review'},
        {'paths': [h('.cargo/registry')], 'title': 'Cargo registry', 'note': 'Cargo tải lại khi build', 'level': 'review'},
        {'paths': [h('.cache/thumbnails')], 'title': 'Ảnh thu nhỏ (thumbnails)', 'note': 'Tự tạo lại khi mở thư mục ảnh'},
        {'paths': [h('.cache/google-chrome'), h('.cache/chromium')], 'title': 'Cache trình duyệt Chrome/Chromium', 'note': 'Trang web sẽ tải chậm hơn lần đầu'},
        {'paths': [h('.cache/mozilla/firefox')], 'title': 'Cache Firefox', 'note': 'Trang web sẽ tải chậm hơn lần đầu'},
        {'paths': [h('.cache/huggingface')], 'title': 'Model Hugging Face', 'note': 'Phải tải lại nếu dùng', 'level': 'review'},
        {'paths': [h('.cache/torch')], 'title': 'Model PyTorch', 'note': 'Phải tải lại nếu dùng', 'level': 'review'},
        {'paths': [h('.cache/tracker3')], 'title': 'Chỉ mục tìm kiếm GNOME', 'note': 'Tự tạo lại'},
        {'paths': [h('.cache/kagglehub')], 'title': 'Dữ liệu/model từ Kaggle', 'note': 'Phải tải lại nếu dùng', 'level': 'review'},
        {'paths': [h('.cache/rtmlib')], 'title': 'Model rtmlib', 'note': 'Phải tải lại nếu dùng', 'level': 'review'},
        {'paths': code, 'title': 'Cache của VS Code', 'note': 'VS Code tự tạo lại'},
        {'paths': [h('.local/share/Trash/files'), h('.local/share/Trash/info')], 'title': 'Thùng rác',
         'note': 'Xoá vĩnh viễn', 'kind': 'contents'},
    ]


DOCKER = {
    'Build Cache': {'args': ['builder', 'prune', '-af'], 'level': 'safe', 'note': 'Lần docker build sau sẽ chậm hơn'},
    'Images': {'args': ['image', 'prune', '-af'], 'level': 'review', 'note': 'Xoá mọi image không có container, kể cả image của app đang làm'},
    'Containers': {'args': ['container', 'prune', '-f'], 'level': 'safe', 'note': 'Chỉ xoá container đã dừng'},
    'Local Volumes': {'args': ['volume', 'prune', '-f'], 'level': 'review', 'note': 'Có thể chứa dữ liệu database'},
}

# Chạy với quyền root qua pkexec (polkit). Chỉ các lệnh cố định này, không nhận lệnh từ ngoài.
ROOT_CMDS = {'apt': ['apt-get', 'clean'], 'journal': ['journalctl', '--vacuum-size=100M']}

LARGE_SKIP = ['/snap/', '/node_modules/', '/.local/share/Trash/', '/.cache/', '/.git/', '/.config/', '/.var/', '/.local/share/docker/']
# Bản sao nằm trong thư mục sinh tự động (build, venv...) không đáng báo là trùng lặp.
DUP_SKIP = ['/dist/', '/build/', '/site-packages/', '/_internal/', '/linux-unpacked/', '/target/', '/out/']
TRASHABLE = {'large', 'dupe'}  # chỉ nhóm này được chuyển vào thùng rác (cache/build cần giải phóng dung lượng thật)


class Ctl:
    """Cờ huỷ dùng chung giữa luồng giao diện và luồng quét."""

    def __init__(self):
        self._ev = threading.Event()

    @property
    def cancelled(self):
        return self._ev.is_set()

    def cancel(self):
        self._ev.set()


class Options:
    def __init__(self, min_size=5 * MB, large_size=200 * MB, large=True, dup=True, dup_size=10 * MB):
        self.min_size = min_size
        self.large_size = large_size
        self.large = large
        self.dup = dup
        self.dup_size = dup_size


# ---------- Tiện ích ----------
def run(cmd, args, timeout=180):
    """Chạy lệnh, trả stdout (None nếu lỗi/không có lệnh). du vẫn trả kết quả khi gặp thư mục không có quyền."""
    try:
        r = subprocess.run([cmd, *args], capture_output=True, text=True, errors='replace', timeout=timeout)
        return r.stdout or None
    except (subprocess.TimeoutExpired, OSError):
        return None


def size_of(path):
    out = run('du', ['-sk', '--', path])
    try:
        return int(out.split(None, 1)[0]) * 1024 if out else 0
    except ValueError:
        return 0


def id_of(*parts):
    return hashlib.sha1('\0'.join(parts).encode()).hexdigest()[:12]


def rel(path):
    return '~' + path[len(HOME):] if path.startswith(HOME + os.sep) else path


def parse_docker_size(s):
    m = re.match(r'^([\d.]+)\s*([kMGT]?B)', s or '')
    if not m:
        return 0
    unit = {'B': 1, 'kB': 1e3, 'MB': 1e6, 'GB': 1e9, 'TB': 1e12}[m.group(2)]
    return round(float(m.group(1)) * unit)


def fmt_size(b):
    return f'{b / GB:.1f} GB' if b >= GB else f'{max(1, round(b / MB))} MB'


def pool_map(fn, tasks, limit):
    with ThreadPoolExecutor(max_workers=limit) as ex:
        return list(ex.map(fn, tasks))


# ---------- Thư mục build/cài đặt trong project ----------
def _matches(rule, full, siblings):
    if 'need' in rule and not any(f in siblings for f in rule['need']):
        return False
    if 'need_ext' in rule and not any(f.endswith(rule['need_ext']) for f in siblings):
        return False
    if 'inside' in rule and not os.path.exists(os.path.join(full, rule['inside'])):
        return False
    return True


def _walk(d, depth, found, on_dir, ctl):
    if ctl.cancelled:
        return
    try:
        entries = list(os.scandir(d))
    except OSError:
        return
    if on_dir:
        on_dir(d)
    names = [e.name for e in entries]
    for e in entries:
        try:
            if not e.is_dir(follow_symlinks=False):  # symlink không phải thư mục thật => bỏ qua
                continue
        except OSError:
            continue
        rule = ARTIFACTS.get(e.name)
        if rule and _matches(rule, e.path, names):
            found.append((e.path, d, e.name, rule))
            continue
        if depth >= MAX_DEPTH or e.name in SKIP_DIRS or e.name.startswith('.'):
            continue
        _walk(e.path, depth + 1, found, on_dir, ctl)


_git_cache = {}


def _mtime(path):
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0


def _git_activity(d):
    best = 0
    while d.startswith(HOME) and d != HOME:
        if d not in _git_cache:
            _git_cache[d] = max(_mtime(os.path.join(d, '.git/logs/HEAD')), _mtime(os.path.join(d, '.git/index')))
        best = max(best, _git_cache[d])
        d = os.path.dirname(d)
    return best


def _last_active(d):
    best = _git_activity(d)
    for f in ('package.json', 'package-lock.json', 'pom.xml', 'requirements.txt'):
        best = max(best, _mtime(os.path.join(d, f)))
    return best or _mtime(d)


def scan_projects(opts, on_progress, ctl):
    found = []
    try:
        roots = [e.path for e in os.scandir(HOME)
                 if e.is_dir(follow_symlinks=False) and not e.name.startswith('.') and e.name not in SKIP_DIRS]
    except OSError:
        roots = []
    counter = [0]

    def on_dir(d):
        counter[0] += 1
        if counter[0] % 200 == 0 and on_progress:
            on_progress(f'Đang tìm trong {rel(d)}')

    pool_map(lambda r: _walk(r, 1, found, on_dir, ctl), roots, 4)
    if ctl.cancelled:
        return []
    if on_progress:
        on_progress(f'Đo dung lượng {len(found)} thư mục...')

    def measure(f):
        full, parent, _name, rule = f
        if ctl.cancelled:
            return None
        size = size_of(full)
        if size < opts.min_size:
            return None
        days = int((time.time() - _last_active(parent)) // DAY)
        active = days < ACTIVE_DAYS
        return {
            'id': id_of('dir', full), 'group': 'project', 'kind': 'dir', 'paths': [full], 'title': rel(full),
            'size': size, 'level': 'review' if active else 'safe',
            'note': f'Project đang làm (hoạt động {days} ngày trước)' if active else f'Project lâu không đụng ({days} ngày)',
            'rebuild': rule['rebuild'],
        }

    return [i for i in pool_map(measure, found, 4) if i]


# ---------- Cache ----------
def scan_caches(opts, ctl):
    def one(c):
        if ctl.cancelled:
            return None
        present = [p for p in c['paths'] if os.path.exists(p)]
        if not present:
            return None
        size = sum(size_of(p) for p in present)
        if size < opts.min_size:
            return None
        return {'id': id_of('cache', *present), 'group': 'cache', 'kind': c.get('kind', 'dir'), 'paths': present,
                'title': c['title'], 'size': size, 'level': c.get('level', 'safe'), 'note': c['note']}

    caches = _caches()
    items = [i for i in pool_map(one, caches, 4) if i]

    # Các thư mục lớn khác trong ~/.cache chưa nằm trong danh sách trên
    known = {p for c in caches for p in c['paths']}
    try:
        subs = [e.path for e in os.scandir(h('.cache')) if e.is_dir(follow_symlinks=False)]
    except OSError:
        subs = []

    def extra(p):
        if p in known or ctl.cancelled:
            return None
        size = size_of(p)
        if size < 200 * MB:
            return None
        return {'id': id_of('cache', p), 'group': 'cache', 'kind': 'dir', 'paths': [p], 'title': rel(p), 'size': size,
                'level': 'review', 'note': 'Cache của ứng dụng khác, kiểm tra trước khi xoá'}

    items += [i for i in pool_map(extra, subs, 4) if i]
    return items


# ---------- Docker ----------
def scan_docker(opts):
    out = run('docker', ['system', 'df', '--format', '{{json .}}'], 20)
    if not out:
        return []
    items = []
    for line in out.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        rule = DOCKER.get(row.get('Type'))
        size = parse_docker_size(row.get('Reclaimable'))
        if not rule or size < opts.min_size:
            continue
        items.append({'id': id_of('docker', row['Type']), 'group': 'docker', 'kind': 'docker', 'dockerType': row['Type'],
                      'title': f"Docker: {row['Type']}", 'size': size, 'level': rule['level'], 'note': rule['note'],
                      'cmd': 'docker ' + ' '.join(rule['args'])})
    return items


# ---------- Cần quyền root ----------
def scan_system(opts):
    items = []
    apt = size_of('/var/cache/apt/archives')
    if apt >= opts.min_size:
        items.append({'id': id_of('sys', 'apt'), 'group': 'system', 'kind': 'root', 'sysType': 'apt', 'title': 'Cache gói apt',
                      'size': apt, 'level': 'safe', 'note': 'Cần quyền root: sẽ hiện hộp thoại nhập mật khẩu (polkit)',
                      'cmd': 'apt-get clean'})
    j = run('journalctl', ['--disk-usage'])
    m = re.search(r'take up ([\d.]+)([KMGT])', j or '')
    if m:
        total = float(m.group(1)) * {'K': 1024, 'M': 1024 ** 2, 'G': 1024 ** 3, 'T': 1024 ** 4}[m.group(2)]
        keep = 100 * MB
        if total - keep >= opts.min_size:
            items.append({'id': id_of('sys', 'journal'), 'group': 'system', 'kind': 'root', 'sysType': 'journal',
                          'title': 'Log hệ thống (journal)', 'size': round(total - keep), 'level': 'safe',
                          'note': 'Cần quyền root (polkit), giữ lại 100 MB gần nhất', 'cmd': 'journalctl --vacuum-size=100M'})
    return items


# ---------- Tệp lớn + tệp trùng ----------
def list_files(min_bytes):
    out = run('find', [HOME, '-xdev', '-type', 'f', '-size', f'+{min_bytes // MB}M', '-printf', '%s\t%T@\t%p\n'], 120)
    rows = []
    for line in (out or '').splitlines():
        parts = line.split('\t', 2)
        if len(parts) != 3:
            continue
        size, mtime, p = parts
        if any(k in p for k in LARGE_SKIP):
            continue
        # thư mục/tệp ẩn: thường là dữ liệu chương trình
        if any(part.startswith('.') for part in os.path.relpath(p, HOME).split(os.sep)):
            continue
        rows.append({'p': p, 'size': int(size), 'mtime': float(mtime)})
    return rows


def hash_file(path, limit=None):
    hsh = hashlib.sha1()
    try:
        with open(path, 'rb') as f:
            left = limit
            while True:
                n = 1 << 20 if left is None else min(1 << 20, left)
                if n == 0:
                    break
                chunk = f.read(n)
                if not chunk:
                    break
                hsh.update(chunk)
                if left is not None:
                    left -= len(chunk)
    except OSError:
        return None
    return hsh.hexdigest()


def find_duplicates(rows, ctl):
    """Nhóm theo kích thước -> hash 64 KB đầu -> hash toàn bộ. Chỉ tệp qua cả ba bước mới là trùng."""
    by_size = {}
    for r in rows:
        by_size.setdefault(r['size'], []).append(r)
    sets = []
    for group in by_size.values():
        if len(group) < 2:
            continue
        partial = {}
        for r, k in zip(group, pool_map(lambda r: None if ctl.cancelled else hash_file(r['p'], 65536), group, 3)):
            if k:
                partial.setdefault(k, []).append(r)
        for cand in partial.values():
            if len(cand) < 2 or ctl.cancelled:
                continue
            full = {}
            for r, k in zip(cand, pool_map(lambda r: hash_file(r['p']), cand, 2)):
                if k:
                    full.setdefault(k, []).append(r)
            sets += [same for same in full.values() if len(same) > 1]
    return sets


def scan_files(opts, skip_paths, on_progress, ctl):
    if not opts.large and not opts.dup:
        return []
    min_bytes = min(opts.large_size if opts.large else float('inf'), opts.dup_size if opts.dup else float('inf'))
    rows = [r for r in list_files(int(min_bytes)) if not any(r['p'].startswith(d + os.sep) for d in skip_paths)]
    if ctl.cancelled:
        return []
    items = []
    in_dup = set()

    if opts.dup:
        if on_progress:
            on_progress('Đang so sánh tệp trùng lặp...')
        sets = find_duplicates([r for r in rows if r['size'] >= opts.dup_size and not any(k in r['p'] for k in DUP_SKIP)], ctl)
        sets.sort(key=lambda s: -s[0]['size'] * (len(s) - 1))
        for same in sets[:30]:
            same.sort(key=lambda r: (r['mtime'], len(r['p'])))  # giữ bản cũ nhất
            keep = same[0]
            in_dup.update(r['p'] for r in same)
            for r in same[1:]:
                items.append({'id': id_of('dup', r['p']), 'group': 'dupe', 'kind': 'file', 'paths': [r['p']], 'title': rel(r['p']),
                              'size': r['size'], 'level': 'review',
                              'note': f"Trùng nội dung với {rel(keep['p'])} (bản này mới hơn, sẽ giữ lại bản kia)"})

    if opts.large:
        big = sorted((r for r in rows if r['size'] >= opts.large_size and r['p'] not in in_dup), key=lambda r: -r['size'])[:40]
        for r in big:
            days = int((time.time() - r['mtime']) // DAY)
            in_dl = r['p'].startswith(h('Downloads') + os.sep)
            items.append({'id': id_of('file', r['p']), 'group': 'large', 'kind': 'file', 'paths': [r['p']], 'title': rel(r['p']),
                          'size': r['size'], 'level': 'review',
                          'note': f"{'Trong Downloads · ' if in_dl else ''}Sửa lần cuối {days} ngày trước · không tự tạo lại được"})
    return items


# ---------- Treemap: dung lượng từng thư mục con ----------
def tree_dir(rel_path=''):
    d = os.path.normpath(os.path.join(HOME, rel_path or '.'))
    real_home = os.path.realpath(HOME)
    real = os.path.realpath(d)
    if real != real_home and not real.startswith(real_home + os.sep):
        raise PermissionError('Ngoài thư mục home')
    dirs = set()
    for e in os.scandir(d):
        try:
            if e.is_dir(follow_symlinks=False):
                dirs.add(e.name)
        except OSError:
            pass
    out = run('du', ['-ak', '-x', '--max-depth=1', '--', d], 240)
    entries, total = [], 0
    for line in (out or '').splitlines():
        m = re.match(r'^(\d+)\t(.+)$', line)
        if not m:
            continue
        if m.group(2) == d:
            total = int(m.group(1)) * 1024
            continue
        name = os.path.basename(m.group(2))
        entries.append({'name': name, 'size': int(m.group(1)) * 1024, 'dir': name in dirs})
    entries.sort(key=lambda e: -e['size'])
    r = os.path.relpath(d, HOME)
    return {'path': '' if r == '.' else r, 'total': total, 'entries': entries[:60]}


# ---------- Quét ----------
def disk_usage():
    s = os.statvfs('/')
    return {'total': s.f_blocks * s.f_frsize, 'free': s.f_bavail * s.f_frsize}


def scan(on_progress=None, opts=None, ctl=None):
    """Trả dict {items, disk, scannedAt}, hoặc None nếu bị huỷ."""
    opts = opts or Options()
    ctl = ctl or Ctl()
    _git_cache.clear()

    def say(m):
        if on_progress:
            on_progress(m)

    say('Đang quét project...')
    projects = scan_projects(opts, on_progress, ctl)
    if ctl.cancelled:
        return None
    say('Đang quét cache...')
    caches = scan_caches(opts, ctl)
    if ctl.cancelled:
        return None
    say('Đang kiểm tra Docker...')
    docker = scan_docker(opts)
    system = scan_system(opts)
    if ctl.cancelled:
        return None
    say('Đang tìm tệp lớn...')
    files = scan_files(opts, [p['paths'][0] for p in projects], on_progress, ctl)
    if ctl.cancelled:
        return None
    items = sorted(projects + caches + docker + system + files, key=lambda i: -i['size'])
    return {'items': items, 'disk': disk_usage(), 'scannedAt': time.time()}


# ---------- Dọn ----------
def assert_inside_home(p):
    """Chỉ xoá những đường dẫn nằm trong HOME (và không phải chính HOME)."""
    r = os.path.normpath(p)
    if not r.startswith(HOME + os.sep):
        raise PermissionError(f'Từ chối xoá ngoài thư mục home: {p}')
    return r


def _remove(p, trash):
    if trash:
        trash(p)
    elif os.path.islink(p) or os.path.isfile(p):
        os.unlink(p)
    else:
        shutil.rmtree(p)


def clean_item(item, trash=None):
    """trash: hàm chuyển vào thùng rác (do giao diện cung cấp); None thì xoá vĩnh viễn."""
    if item['kind'] == 'docker':
        rule = DOCKER.get(item.get('dockerType'))
        if not rule:
            raise RuntimeError('Lệnh Docker không hợp lệ')
        r = subprocess.run(['docker', *rule['args']], capture_output=True, text=True, timeout=600)
        if r.returncode:
            raise RuntimeError(r.stderr.strip() or 'docker lỗi')
        return
    if item['kind'] == 'root':
        args = ROOT_CMDS.get(item.get('sysType'))
        if not args:
            raise RuntimeError('Lệnh hệ thống không hợp lệ')
        r = subprocess.run(['pkexec', *args], capture_output=True, text=True, timeout=300)
        if r.returncode in (126, 127):
            raise RuntimeError('Đã huỷ xác thực quyền root')
        if r.returncode:
            raise RuntimeError(r.stderr.strip() or 'lệnh lỗi')
        return
    if item['group'] not in TRASHABLE:
        trash = None  # cache/build tạo lại được và cần giải phóng dung lượng thật
    for p in item['paths']:
        r = assert_inside_home(p)
        try:
            if item['kind'] == 'contents':
                for name in os.listdir(r):
                    _remove(os.path.join(r, name), None)
            else:
                _remove(r, trash)
        except FileNotFoundError:
            pass


# ---------- Xuất CSV ----------
def _csv_cell(v):
    t = str(v if v is not None else '')
    # Ô bắt đầu bằng = + - @ bị Excel/Sheets hiểu là công thức, nên thêm dấu nháy phía trước.
    return "'" + t if t[:1] in ('=', '+', '-', '@', '\t', '\r') else t


def export_csv(items, path):
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL)
        w.writerow(['Nhóm', 'Tên', 'Đường dẫn', 'Dung lượng (byte)', 'Dung lượng (MB)', 'Mức độ', 'Ghi chú'])
        for i in items:
            w.writerow([_csv_cell(x) for x in (
                i['group'], i['title'], ' | '.join(i.get('paths') or [i.get('cmd', '')]), i['size'],
                f"{i['size'] / MB:.1f}", 'An toàn' if i['level'] == 'safe' else 'Xem lại', i['note'])])


def main():
    scan_result = scan(lambda m: print(m, file=sys.stderr))
    for i in scan_result['items']:
        print(f"{i['level']:<6} {fmt_size(i['size']):>8}  {i['title']}")
    total = sum(i['size'] for i in scan_result['items'])
    d = scan_result['disk']
    print(f"\nTổng có thể dọn: {fmt_size(total)} | Ổ / còn trống: {fmt_size(d['free'])}/{fmt_size(d['total'])}")
