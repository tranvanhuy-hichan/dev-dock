"""Secrets scanner: tìm khoá API/token nằm trong repo và file .env chưa được .gitignore.

Nguyên tắc an toàn: chỉ ĐỌC; không bao giờ lưu hoặc hiển thị giá trị đầy đủ (chỉ bản che, vd. `AKIA…XY`);
cache trên đĩa cũng chỉ chứa bản che. Việc duy nhất ghi file là thêm dòng vào .gitignore khi người dùng bấm."""
import hashlib
import json
import math
import os
import re
import tempfile
import time

from . import envfiles as E
from . import gitops as G

MAX_FILE = 1_000_000
SKIP_DIRS = {'node_modules', '.git', 'target', 'dist', 'build', '.venv', 'venv', '__pycache__', 'vendor', '.next', '.cache', 'coverage'}
SKIP_EXT = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.pdf', '.zip', '.gz', '.tar', '.woff', '.woff2', '.ttf', '.eot', '.mp4', '.mp3',
            '.so', '.dll', '.exe', '.class', '.jar', '.pyc', '.lock', '.svg', '.map', '.sqlite', '.db'}
SKIP_NAMES = {'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', 'Cargo.lock', 'poetry.lock', 'go.sum'}

# (tên, regex, mức) — mức: bad = chắc chắn là khoá thật, warn = nghi ngờ
PATTERNS = [
    ('AWS access key', re.compile(r'\b(?:AKIA|ASIA)[0-9A-Z]{16}\b'), 'bad'),
    ('GitHub token', re.compile(r'\bgh[pousr]_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{50,}\b'), 'bad'),
    ('Slack token', re.compile(r'\bxox[abprs]-[A-Za-z0-9-]{10,}'), 'bad'),
    ('Stripe live key', re.compile(r'\b[sr]k_live_[A-Za-z0-9]{16,}'), 'bad'),
    ('Google API key', re.compile(r'\bAIza[0-9A-Za-z_-]{35}\b'), 'bad'),
    ('Anthropic API key', re.compile(r'\bsk-ant-[A-Za-z0-9_-]{20,}'), 'bad'),
    ('OpenAI API key', re.compile(r'\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}'), 'bad'),
    ('Private key', re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP |ENCRYPTED )?PRIVATE KEY'), 'bad'),
    ('Mật khẩu trong URL', re.compile(r'\b[a-z][a-z0-9+.-]*://[^/\s:@\'"]+:([^@\s\'"/]{4,})@[^\s\'"]+'), 'warn'),
    ('JWT', re.compile(r'\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}'), 'warn'),
]
GENERIC = re.compile(r'(?i)(?:secret|token|passw(?:or)?d|passwd|api[_-]?key|access[_-]?key|private[_-]?key|client[_-]?secret)\w*["\']?\s*[:=]\s*["\']?([^\s"\'#,;`]{12,})')
PLACEHOLDER = re.compile(r'(?i)example|your|xxx|changeme|change_me|placeholder|dummy|sample|fake|redacted|todo|<[^>]*>|\$\{|\{\{|%\(|^\$|process\.env|os\.environ|getenv|secrets\.|^\*+$|password|^test|^none$|^null$')


LITERAL = re.compile(r'^[A-Za-z0-9_\-+/=.!@#%^*~]{16,}$')
DOC_PATH = re.compile(r'(?i)(^|/)(tests?|__tests__|spec|specs|docs?|examples?|fixtures|mocks?|__mocks__)/|\.(md|mdx|rst|txt|html|arb)$|(_test|\.test|\.spec)\.\w+$|(^|/)test_[^/]*$')
LOCAL_HOST = re.compile(r'@(localhost|127\.0\.0\.1|0\.0\.0\.0|host\.docker\.internal)\b')
EXAMPLE_SUFFIX = ('.example', '.sample', '.template', '.dist', '.tpl')


def is_example_name(name):
    return E.is_example(name) or name.endswith(EXAMPLE_SUFFIX)


def looks_like_secret(val):
    """Giá trị trông như chuỗi bí mật thật chứ không phải biểu thức/đoạn mã: ký tự hợp lệ, có chữ + số, không phải a.b.c, entropy cao."""
    return bool(LITERAL.match(val)) and any(c.isdigit() for c in val) and any(c.isalpha() for c in val) \
        and not re.match(r'^[A-Za-z_]\w*(\.\w+)+$', val) and entropy(val) >= 3.4


def entropy(s):
    if not s:
        return 0.0
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in (s.count(ch) for ch in set(s)))


def is_placeholder(value):
    v = value.strip('\'"')
    return not v or bool(PLACEHOLDER.search(v)) or len(set(v)) <= 3


def mask(value):
    v = value.strip()
    return v[:4] + '…' + v[-2:] if len(v) >= 14 else '••••••••'


def fingerprint(repo_rel, kind, value):
    return hashlib.sha1(f'{repo_rel}\0{kind}\0{value}'.encode()).hexdigest()[:16]


def scan_text(text, fname=''):
    """[(dòng, loại, mức, giá_trị)] — giá trị đầy đủ chỉ để tạo bản che/vân tay, không lưu lại."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if len(line) > 2000 or 'devdock:ignore' in line:
            continue
        hit = False
        for kind, rx, sev in PATTERNS:
            m = rx.search(line)
            if not m:
                continue
            val = m.group(1) if m.groups() else m.group(0)
            if kind == 'Mật khẩu trong URL' and (is_placeholder(val) or val.startswith('$') or LOCAL_HOST.search(line) or DOC_PATH.search(fname)):
                continue
            if re.search(r'(?i)example|dummy|fake|xxx', m.group(0)):  # "AKIAEXAMPLE…" và các mẫu tài liệu
                continue
            out.append((n, kind, sev, val if kind != 'Private key' else m.group(0)))
            hit = True
            break
        if hit:
            continue
        m = GENERIC.search(line)
        if m and not DOC_PATH.search(fname):
            val = m.group(1)
            if not is_placeholder(val) and looks_like_secret(val):
                out.append((n, 'Khoá/mật khẩu gán cứng', 'warn', val))
    return out


def _is_text(path):
    try:
        with open(path, 'rb') as f:
            return b'\0' not in f.read(2048)
    except OSError:
        return False


def _files(repo):
    """(đã_tracked, chưa_tracked_và_không_bị_ignore) — đường dẫn tương đối."""
    tracked = [p for p in G.run(repo, 'ls-files', '-z').split('\0') if p]
    others = [p for p in G.run(repo, 'ls-files', '-z', '--others', '--exclude-standard').split('\0') if p]
    return tracked, others


def _skip(rel):
    parts = rel.split('/')
    name = parts[-1]
    if any(p in SKIP_DIRS for p in parts[:-1]) or name in SKIP_NAMES:
        return True
    return os.path.splitext(name)[1].lower() in SKIP_EXT


def env_status(repo, tracked, others):
    """Phát hiện file .env: đã commit / chưa commit nhưng chưa ignore / từng commit trong lịch sử."""
    out, tracked_set = [], set(tracked)
    for rel, is_tracked in [(p, True) for p in tracked] + [(p, False) for p in others]:
        name = os.path.basename(rel)
        if not E.is_env_name(name) or is_example_name(name):
            continue
        full = os.path.join(repo, rel)
        secret_keys = 0
        try:
            secret_keys = sum(1 for k, v in E.load(full).items() if v and not is_placeholder(v) and E.is_secret(k, v))
        except OSError:
            pass
        out.append({'file': rel, 'status': 'tracked' if is_tracked else 'unignored', 'secret_keys': secret_keys})
    try:
        names = G.run(repo, 'log', '--all', '--format=', '--name-only', '--diff-filter=A', timeout=60).splitlines()
    except G.GitError:
        names = []
    seen = {e['file'] for e in out}
    for rel in dict.fromkeys(n for n in names if n):
        if E.is_env_name(os.path.basename(rel)) and not is_example_name(os.path.basename(rel)) and rel not in tracked_set and rel not in seen:
            out.append({'file': rel, 'status': 'history', 'secret_keys': 0})
    return out


def scan_repo(repo, ignored=(), max_files=4000):
    """Quét một repo git. Trả về {'repo','name','findings','env','scanned','at','error'}."""
    res = {'repo': repo, 'name': os.path.basename(repo.rstrip('/')), 'findings': [], 'env': [], 'scanned': 0, 'at': time.time(), 'error': ''}
    try:
        tracked, others = _files(repo)
    except G.GitError as e:
        res['error'] = str(e)
        return res
    tracked_set = set(tracked)
    for rel in (tracked + others)[:max_files]:
        if _skip(rel):
            continue
        full = os.path.join(repo, rel)
        try:
            if os.path.islink(full) or os.path.getsize(full) > MAX_FILE or not _is_text(full):
                continue
            with open(full, encoding='utf-8', errors='replace') as f:
                text = f.read()
        except OSError:
            continue
        res['scanned'] += 1
        for line, kind, sev, val in scan_text(text, rel):
            fp = fingerprint(rel, kind, val)
            if fp in ignored:
                continue
            tracked_f = rel in tracked_set
            res['findings'].append({'file': rel, 'line': line, 'kind': kind, 'severity': sev if tracked_f or sev == 'bad' else 'warn',
                                    'preview': mask(val), 'tracked': tracked_f, 'fp': fp})
    for e in env_status(repo, tracked, others):
        fp = fingerprint(e['file'], 'env-' + e['status'], '')
        if fp in ignored:
            continue
        sev = 'bad' if e['status'] == 'tracked' and e['secret_keys'] else 'warn'
        what = {'tracked': 'File .env đã được commit', 'unignored': 'File .env chưa nằm trong .gitignore', 'history': 'File .env từng bị commit trong lịch sử'}[e['status']]
        extra = f" ({e['secret_keys']} biến nhạy cảm)" if e['secret_keys'] else ''
        res['env'].append({'file': e['file'], 'status': e['status'], 'severity': sev, 'kind': what + extra, 'fp': fp})
    res['findings'].sort(key=lambda f: (f['severity'] != 'bad', f['file'], f['line']))
    return res


def totals(results):
    """Tóm tắt nhiều repo: {'repos','bad','warn','repos_with'}."""
    bad = warn = n = 0
    for r in results:
        items = r.get('findings', []) + r.get('env', [])
        b = sum(1 for i in items if i['severity'] == 'bad')
        w = len(items) - b
        bad, warn = bad + b, warn + w
        n += 1 if items else 0
    return {'repos': len(results), 'bad': bad, 'warn': warn, 'repos_with': n}


def add_to_gitignore(repo, rel):
    """Thêm tên file env vào .gitignore của repo (chỉ thêm, không xoá). Trả về True nếu đã thêm."""
    name = os.path.basename(rel)
    sub = os.path.dirname(rel)
    entry = '/' + rel if sub else name
    gi = os.path.join(repo, '.gitignore')
    try:
        text = ''
        if os.path.exists(gi):
            with open(gi, encoding='utf-8') as f:
                text = f.read()
    except OSError as e:
        raise RuntimeError(f'Không đọc được .gitignore: {e}') from None
    if entry in {ln.strip() for ln in text.splitlines()}:
        return False
    body = text if text.endswith('\n') or not text else text + '\n'
    with open(gi, 'w', encoding='utf-8') as f:
        f.write(body + entry + '\n')
    return True


def untrack_cmd(rel):
    """Lệnh gợi ý để bỏ file khỏi git mà giữ file trên đĩa (không tự chạy)."""
    return f'git rm --cached -- "{rel}"'


# ---------------- cache + danh sách bỏ qua ----------------
def _dir():
    return os.path.join(os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share'), 'devdock')


def cache_path():
    return os.path.join(_dir(), 'secrets_cache.json')


def ignore_path():
    return os.path.join(_dir(), 'secrets_ignore.json')


def _load(path):
    try:
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
        return d
    except (OSError, ValueError):
        return None


def _save(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix='.sec-')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        pass


def load_cache():
    d = _load(cache_path())
    return d if isinstance(d, dict) else {}


def save_result(res):
    d = load_cache()
    d[res['repo']] = res
    _save(cache_path(), d)


def load_ignored():
    d = _load(ignore_path())
    return set(d) if isinstance(d, list) else set()


def add_ignored(fp):
    s = load_ignored()
    s.add(fp)
    _save(ignore_path(), sorted(s))


def clear_ignored():
    _save(ignore_path(), [])
