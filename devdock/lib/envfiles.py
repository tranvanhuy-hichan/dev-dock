"""Env Manager: tìm, đọc, so sánh và sửa các file .env (giữ nguyên comment/thứ tự), che giá trị bí mật."""
import os
import re
import tempfile

SKIP = {'node_modules', '.git', '.venv', 'venv', 'target', 'dist', 'build', '__pycache__', '.cache', 'snap', '.next'}
EXAMPLE_NAMES = ('.env.example', '.env.sample', '.env.template', '.env.dist', 'example.env', 'sample.env')
KEY_RE = re.compile(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_.\-]*)\s*=(.*)$')
SECRET_RE = re.compile(r'(SECRET|TOKEN|PASS(WORD|WD)?|PWD|PRIVATE|CREDENTIAL|AUTH|API[_-]?KEY|ACCESS[_-]?KEY|SIGNING|ENCRYPTION|SALT|DSN|COOKIE|SESSION)', re.I)


def is_env_name(name):
    return name == '.env' or name.startswith('.env.') or name.endswith('.env')


def find_env_files(roots, max_depth=4):
    out = []

    def walk(d, depth):
        try:
            entries = os.listdir(d)
        except OSError:
            return
        for n in sorted(entries):
            p = os.path.join(d, n)
            if os.path.islink(p):
                continue
            if os.path.isfile(p) and is_env_name(n):
                out.append(p)
            elif os.path.isdir(p) and depth < max_depth and n not in SKIP and (not n.startswith('.') or n in ('.config',)):
                walk(p, depth + 1)
    for r in roots:
        r = os.path.realpath(os.path.expanduser(r))
        if os.path.isdir(r):
            walk(r, 0)
    return out


def is_example(path):
    return os.path.basename(path) in EXAMPLE_NAMES


def _unescape(raw):
    return re.sub(r'\\(["\\n])', lambda m: '\n' if m.group(1) == 'n' else m.group(1), raw)


def _parse_value(rest, lines, i):
    """Giá trị sau dấu '='. Trả (value, dòng_cuối)."""
    s = rest.lstrip()
    if s[:1] in ('"', "'"):
        q = s[0]
        raw, j, cur = [], i, s[1:]
        while True:
            k, esc = 0, False
            while k < len(cur):
                c = cur[k]
                if q == '"' and c == '\\' and not esc:
                    esc = True
                elif c == q and not esc:
                    val = '\n'.join(raw + [cur[:k]])
                    return (_unescape(val) if q == '"' else val), j
                else:
                    esc = False
                k += 1
            raw.append(cur)
            j += 1
            if j >= len(lines):
                val = '\n'.join(raw)  # thiếu dấu đóng: lấy hết
                return (_unescape(val) if q == '"' else val), j - 1
            cur = lines[j]
    m = re.search(r'\s#', s)
    return (s[:m.start()] if m else s).strip(), i


def parse(text):
    """Danh sách mục: {'key','value','line'(chỉ số dòng bắt đầu),'end'(dòng cuối)}. Bỏ qua comment/dòng trống."""
    lines = text.splitlines()
    out, i = [], 0
    while i < len(lines):
        m = KEY_RE.match(lines[i]) if not lines[i].lstrip().startswith('#') else None
        if m:
            val, end = _parse_value(m.group(2), lines, i)
            out.append({'key': m.group(1), 'value': val, 'line': i, 'end': end})
            i = end + 1
        else:
            i += 1
    return out


def read(path):
    with open(path, encoding='utf-8', errors='replace') as f:
        return f.read()


def load(path):
    """dict key → value (khoá lặp: giá trị cuối thắng), giữ thứ tự xuất hiện đầu tiên."""
    d = {}
    for e in parse(read(path)):
        d[e['key']] = e['value']
    return d


def quote(value):
    if value == '' or not re.search(r'[\s#"\'\\$`]', value):
        return value
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') + '"'


def _write(path, text):
    mode = os.stat(path).st_mode & 0o777 if os.path.exists(path) else 0o600
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, prefix='.env-edit-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)  # ghi nguyên tử, giữ quyền file
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _check_key(key):
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.\-]*', key or ''):
        raise ValueError('Tên biến chỉ gồm chữ, số, _ . - và không bắt đầu bằng số')


def set_var(path, key, value):
    _check_key(key)
    text = read(path)
    lines = text.splitlines()
    ents = [e for e in parse(text) if e['key'] == key]
    new = f'{key}={quote(value)}'
    if ents:
        e = ents[-1]
        prefix = 'export ' if lines[e['line']].lstrip().startswith('export ') else ''
        lines[e['line']:e['end'] + 1] = [prefix + new]
    else:
        lines.append(new)
    _write(path, '\n'.join(lines) + '\n')


def delete_var(path, key):
    text = read(path)
    lines = text.splitlines()
    for e in reversed([e for e in parse(text) if e['key'] == key]):
        del lines[e['line']:e['end'] + 1]
    _write(path, ('\n'.join(lines) + '\n') if lines else '')


def add_missing(path, items):
    """Thêm các (key, value) chưa có vào cuối file."""
    text = read(path)
    have = {e['key'] for e in parse(text)}
    add = [(k, v) for k, v in items if k not in have]
    for k, _ in add:
        _check_key(k)
    if not add:
        return 0
    body = text if text.endswith('\n') or not text else text + '\n'
    _write(path, body + ''.join(f'{k}={quote(v)}\n' for k, v in add))
    return len(add)


def is_secret(key, value=''):
    return bool(SECRET_RE.search(key)) or bool(re.search(r'://[^/\s:@]+:[^@\s]+@', value or ''))


def mask(value):
    return '' if value == '' else '••••••••'


def diff(a, b):
    """So sánh hai dict: [(key, trạng_thái, giá_trị_a, giá_trị_b)] với trạng thái same|different|only_a|only_b."""
    rows = []
    for k in list(a) + [k for k in b if k not in a]:
        if k in a and k in b:
            rows.append((k, 'same' if a[k] == b[k] else 'different', a[k], b[k]))
        elif k in a:
            rows.append((k, 'only_a', a[k], None))
        else:
            rows.append((k, 'only_b', None, b[k]))
    order = {'different': 0, 'only_a': 1, 'only_b': 2, 'same': 3}
    return sorted(rows, key=lambda r: order[r[1]])


def example_for(path, siblings):
    """File example cùng thư mục với `path` (nếu có)."""
    d = os.path.dirname(path)
    for s in siblings:
        if os.path.dirname(s) == d and is_example(s) and s != path:
            return s
    return None


def missing_keys(path, example):
    return [k for k in load(example) if k not in load(path)]
