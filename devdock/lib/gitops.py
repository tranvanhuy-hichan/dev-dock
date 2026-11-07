"""Thao tác Git an toàn cho module Git: trạng thái, diff, stage, commit, push/pull, log, nhánh, stash.
Mọi lệnh chạy bằng argv (không qua shell). Lệnh đọc dùng cấu hình cứng (tắt fsmonitor/hook). Không bao giờ force-push."""
import os
import re
import subprocess

from . import projects as P

READ = list(P.GIT)  # git -c core.fsmonitor=false -c core.hooksPath=/dev/null -c protocol.ext.allow=never
WRITE = ['git', '-c', 'core.fsmonitor=false', '-c', 'protocol.ext.allow=never']  # commit/pull/push chạy hook của chính repo (quy trình bình thường)
MAX_OUT = 400_000
STATUS_TXT = {'M': 'sửa', 'A': 'thêm', 'D': 'xoá', 'R': 'đổi tên', 'C': 'sao chép', 'T': 'đổi kiểu', 'U': 'xung đột', '?': 'mới'}


class GitError(RuntimeError):
    pass


# ---------------- kiểm tra đầu vào ----------------
_REF_RE = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9._/\-]*$')
_SHA_RE = re.compile(r'^[0-9a-fA-F]{4,40}$')
_STASH_RE = re.compile(r'^stash@\{\d{1,4}\}$')


def valid_ref(name):
    """Tên nhánh/remote hợp lệ: không bắt đầu bằng '-', không có '..', '//', '@{', kết thúc '/' hoặc '.lock'."""
    return bool(name) and bool(_REF_RE.match(name)) and '..' not in name and '//' not in name and '@{' not in name and not name.endswith(('/', '.', '.lock'))


def _friendly(err, default):
    e = err or ''
    low = e.lower()
    if 'please tell me who you are' in low or 'empty ident' in low or 'unable to auto-detect email' in low or 'user.useconfigonly' in low:
        return 'Chưa cấu hình danh tính git. Chạy: git config --global user.name "Tên" && git config --global user.email "mail@..."'
    if 'nothing to commit' in low or 'no changes added to commit' in low:
        return 'Chưa có gì để commit — hãy stage file trước.'
    if 'authentication failed' in low or 'could not read username' in low or 'permission denied (publickey' in low or 'terminal prompts disabled' in low:
        return 'Không xác thực được với remote (thiếu credential/SSH key). Hãy đăng nhập bằng git credential helper hoặc ssh-agent.'
    if 'non-fast-forward' in low or '[rejected]' in low or 'fetch first' in low:
        return 'Remote có commit mới hơn — hãy Pull trước (DevDock không bao giờ force-push).'
    if 'has no upstream' in low or 'no upstream branch' in low:
        return 'Nhánh chưa có upstream. Dùng nút Push để đẩy lần đầu (-u origin).'
    if 'not possible to fast-forward' in low or 'diverging branches' in low:
        return 'Không thể fast-forward — nhánh đã phân kỳ với remote. Hãy merge/rebase thủ công.'
    if 'would be overwritten' in low or 'local changes' in low:
        return 'Có thay đổi chưa commit sẽ bị ghi đè. Hãy commit hoặc stash trước.'
    if 'not a git repository' in low:
        return 'Không phải repo git.'
    if 'could not resolve host' in low or 'unable to access' in low or 'connection' in low and 'refused' in low:
        return 'Không kết nối được remote (mạng/URL): ' + e.strip().splitlines()[-1][:160]
    e = e.strip()
    return (e.splitlines()[-1] if e else default)[:300]


def _env():
    env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'LC_ALL': 'C', 'GIT_PAGER': 'cat', 'GIT_EDITOR': 'true'}
    env.setdefault('GIT_SSH_COMMAND', 'ssh -o BatchMode=yes -o ConnectTimeout=15')  # không treo chờ passphrase
    return env


def run(path, *args, write=False, timeout=30, input_=None, ok=(0,)):
    """Chạy git trong `path`. Trả về stdout (str). Ném GitError với thông báo thân thiện."""
    if not os.path.isdir(path):
        raise GitError(f'Thư mục không tồn tại: {path}')
    argv = [*(WRITE if write else READ), '-C', path, *args]
    if any(a in ('--force', '-f', '--force-with-lease', '--mirror', '--delete') or a.startswith('--force') for a in args if args and args[0] == 'push'):
        raise GitError('DevDock không cho force-push / xoá nhánh remote.')
    try:
        r = subprocess.run(argv, capture_output=True, text=True, errors='replace', timeout=timeout, env=_env(), input=input_)
    except FileNotFoundError:
        raise GitError('Không tìm thấy lệnh git') from None
    except subprocess.TimeoutExpired:
        raise GitError(f'git {args[0] if args else ""} quá thời gian chờ ({timeout}s)') from None
    if r.returncode not in ok:
        raise GitError(_friendly(r.stderr or r.stdout, f'git lỗi (mã {r.returncode})'))
    out = r.stdout
    return out if len(out) <= MAX_OUT else out[:MAX_OUT] + '\n… (đã cắt bớt, diff quá lớn)'


# ---------------- trạng thái ----------------
def parse_status_v2(raw):
    """Phân tích `git status --porcelain=v2 -b -z`. Trả {branch, upstream, ahead, behind, files:[{path, orig, area, code}]}."""
    out = {'branch': '', 'upstream': None, 'ahead': 0, 'behind': 0, 'oid': '', 'files': []}
    recs = raw.split('\0')
    i = 0
    while i < len(recs):
        r = recs[i]
        i += 1
        if not r:
            continue
        if r.startswith('# branch.head '):
            out['branch'] = r[len('# branch.head '):]
        elif r.startswith('# branch.upstream '):
            out['upstream'] = r[len('# branch.upstream '):]
        elif r.startswith('# branch.oid '):
            out['oid'] = r[len('# branch.oid '):]
        elif r.startswith('# branch.ab '):
            m = re.match(r'# branch\.ab \+(\d+) -(\d+)', r)
            if m:
                out['ahead'], out['behind'] = int(m.group(1)), int(m.group(2))
        elif r.startswith('1 ') or r.startswith('2 '):
            two = r[0] == '2'
            parts = r.split(' ', 9 if two else 8)
            xy, path = parts[1], parts[9 if two else 8]
            orig = ''
            if two and i < len(recs):
                orig, i = recs[i], i + 1
            x, y = xy[0], xy[1]
            if x != '.':
                out['files'].append({'path': path, 'orig': orig, 'area': 'staged', 'code': x})
            if y != '.':
                out['files'].append({'path': path, 'orig': orig, 'area': 'unstaged', 'code': y})
        elif r.startswith('u '):
            parts = r.split(' ', 10)
            out['files'].append({'path': parts[10], 'orig': '', 'area': 'conflict', 'code': 'U'})
        elif r.startswith('? '):
            out['files'].append({'path': r[2:], 'orig': '', 'area': 'untracked', 'code': '?'})
    return out


def parse_numstat(text):
    """Đầu ra `git diff --numstat -z` → {đường_dẫn: (thêm, xoá)}; file nhị phân → (None, None)."""
    out, toks, i = {}, text.split('\0'), 0
    while i < len(toks):
        m = re.match(r'^(\d+|-)\t(\d+|-)\t(.*)$', toks[i])
        i += 1
        if not m:
            continue
        a, d, p = m.groups()
        if p == '' and i + 1 < len(toks):  # đổi tên: hai đường dẫn (cũ, mới) theo sau
            p, i = toks[i + 1], i + 2
        out[p] = (None, None) if a == '-' else (int(a), int(d))
    return out


def numstat(path):
    """{(khu_vực, đường_dẫn): (thêm, xoá)} cho file đã theo dõi (staged/unstaged). Lỗi thì trả rỗng (chỉ là thông tin phụ)."""
    out = {}
    for area, args in (('unstaged', ('diff', '--numstat', '-z')), ('staged', ('diff', '--cached', '--numstat', '-z'))):
        try:
            for p, v in parse_numstat(run(path, *args)).items():
                out[(area, p)] = v
        except GitError:
            pass
    return out


def status(path):
    st = parse_status_v2(run(path, 'status', '--porcelain=v2', '-b', '-z', '--untracked-files=all'))
    st['stats'] = numstat(path)
    return st


def has_commits(path):
    try:
        run(path, 'rev-parse', '--verify', '-q', 'HEAD')
        return True
    except GitError:
        return False


def remotes(path):
    return [r for r in run(path, 'remote').split() if r]


def identity_ok(path):
    try:
        run(path, 'var', 'GIT_AUTHOR_IDENT')
        return True
    except GitError:
        return False


# ---------------- diff ----------------
def file_diff(path, f):
    """Diff cho một dòng trong danh sách thay đổi (f = {'path','area'})."""
    p, area = f['path'], f['area']
    if '\0' in p:
        raise GitError('Đường dẫn không hợp lệ')
    base = ['diff', '--no-color', '--no-ext-diff', '--no-textconv']
    if area == 'staged':
        return run(path, *base, '--cached', '--', p)
    if area == 'untracked':
        return run(path, *base, '--no-index', '--', '/dev/null', p, ok=(0, 1))
    return run(path, *base, '--', p)


# ---------------- stage / commit ----------------
def _paths(files):
    files = list(files)
    if not files or any(('\0' in f) or not f for f in files):
        raise GitError('Danh sách file không hợp lệ')
    return files


def stage(path, files):
    run(path, 'add', '--', *_paths(files), write=True)


def unstage(path, files):
    files = _paths(files)
    if has_commits(path):
        run(path, 'restore', '--staged', '--', *files, write=True)
    else:
        run(path, 'rm', '--cached', '-r', '-q', '--', *files, write=True)


def discard(path, f):
    """Hoàn tác thay đổi chưa stage của một file (xoá hẳn nếu là file mới chưa theo dõi). KHÔNG hoàn tác được."""
    p = _paths([f['path']])[0]
    if f['area'] == 'untracked':
        run(path, 'clean', '-f', '-q', '--', p, write=True)
    elif f['area'] == 'unstaged':
        run(path, 'restore', '--', p, write=True)
    else:
        raise GitError('Chỉ hoàn tác được thay đổi chưa stage (hãy Unstage trước)')


def commit(path, message):
    msg = (message or '').strip()
    if not msg:
        raise GitError('Hãy nhập nội dung commit.')
    if not identity_ok(path):
        raise GitError(_friendly('Please tell me who you are', ''))
    out = run(path, 'commit', '-F', '-', write=True, input_=msg + '\n', timeout=120)
    return (out.strip().splitlines() or ['Đã commit'])[0]


# ---------------- remote ----------------
def fetch(path):
    run(path, 'fetch', '--prune', write=True, timeout=90)


def pull(path):
    out = run(path, 'pull', '--ff-only', write=True, timeout=120)
    return 'Đã cập nhật' if 'Already up to date' not in out else 'Đã là mới nhất'


def push(path):
    """Push nhánh hiện tại. Chưa có upstream thì `-u origin <nhánh>`. Không bao giờ force."""
    st = status(path)
    if not st['branch'] or st['branch'].startswith('('):
        raise GitError('Đang ở trạng thái detached HEAD, không push được.')
    if st['upstream']:
        run(path, 'push', write=True, timeout=120)
        return f"Đã push {st['branch']}"
    rs = remotes(path)
    if not rs:
        raise GitError('Repo chưa có remote để push.')
    remote = 'origin' if 'origin' in rs else rs[0]
    if not valid_ref(st['branch']):
        raise GitError('Tên nhánh không hợp lệ')
    run(path, 'push', '-u', remote, st['branch'], write=True, timeout=120)
    return f"Đã push {st['branch']} lên {remote} (đặt upstream)"


def push_all(paths, on_result=None):
    """Push tuần tự nhiều repo. Trả về [(path, ok, thông_báo)]."""
    res = []
    for p in paths:
        try:
            r = (p, True, push(p))
        except GitError as e:
            r = (p, False, str(e))
        res.append(r)
        if on_result:
            on_result(*r)
    return res


# ---------------- lịch sử ----------------
def log(path, n=300, query='', all_refs=False):
    """Lịch sử commit. Có `query`: lọc (không phân biệt hoa/thường, hỗ trợ tiếng Việt có dấu) theo nội dung/tác giả/mã trong 2000 commit gần nhất."""
    q = (query or '').strip().casefold()
    args = ['log', '--no-color', f'-n{2000 if q else int(n)}', '--pretty=format:%H%x1f%h%x1f%an%x1f%at%x1f%s%x1f%D%x1e']
    if all_refs:
        args.append('--all')
    try:
        raw = run(path, *args)
    except GitError as e:
        if 'does not have any commits' in str(e) or 'bad default revision' in str(e):
            return []
        raise
    out = []
    for rec in raw.split('\x1e'):
        rec = rec.strip('\n')
        if not rec:
            continue
        f = rec.split('\x1f')
        if len(f) < 6:
            continue
        c = {'sha': f[0], 'short': f[1], 'author': f[2], 'ts': int(f[3] or 0), 'subject': f[4], 'refs': f[5]}
        if q and q not in f'{c["subject"]} {c["author"]} {c["sha"]}'.casefold():
            continue
        out.append(c)
        if len(out) >= n:
            break
    return out


def show(path, sha):
    if not _SHA_RE.match(sha or ''):
        raise GitError('Mã commit không hợp lệ')
    return run(path, 'show', '--no-color', '--no-ext-diff', '--no-textconv', '--stat', '--patch', '--format=fuller', sha)


# ---------------- nhánh ----------------
def branches(path):
    fmt = '%(refname)%1f%(refname:short)%1f%(upstream:short)%1f%(upstream:track,nobracket)%1f%(HEAD)%1f%(committerdate:unix)%1f%(subject)'
    raw = run(path, 'for-each-ref', f'--format={fmt}', 'refs/heads', 'refs/remotes')
    try:
        merged = {b.strip().lstrip('* ') for b in run(path, 'branch', '--merged').splitlines()}
    except GitError:
        merged = set()
    out = []
    for line in raw.splitlines():
        f = line.split('\x1f')
        if len(f) < 7 or f[0].endswith('/HEAD'):
            continue
        remote = f[0].startswith('refs/remotes/')
        track = f[3]
        a = re.search(r'ahead (\d+)', track)
        b = re.search(r'behind (\d+)', track)
        out.append({'name': f[1], 'ref': f[0], 'remote': remote, 'upstream': f[2], 'ahead': int(a.group(1)) if a else 0, 'behind': int(b.group(1)) if b else 0,
                    'gone': 'gone' in track, 'current': f[4] == '*', 'ts': int(f[5] or 0), 'subject': f[6], 'merged': (not remote) and f[1] in merged})
    out.sort(key=lambda x: (x['remote'], not x['current'], x['name'].lower()))
    return out


def checkout(path, name, create=False, remote=False):
    if not valid_ref(name):
        raise GitError(f'Tên nhánh không hợp lệ: {name!r}')
    if create:
        run(path, 'switch', '-c', name, write=True)
    elif remote:
        run(path, 'switch', '--track', name, write=True)
    else:
        run(path, 'switch', name, write=True)


def delete_branch(path, name):
    """Chỉ xoá nhánh local đã merge (git branch -d). Không xoá nhánh đang đứng, không xoá remote."""
    if not valid_ref(name):
        raise GitError(f'Tên nhánh không hợp lệ: {name!r}')
    run(path, 'branch', '-d', name, write=True)


# ---------------- stash ----------------
def stash_list(path):
    raw = run(path, 'stash', 'list', '--format=%gd%x1f%s%x1f%at')
    out = []
    for line in raw.splitlines():
        f = line.split('\x1f')
        if len(f) >= 3:
            out.append({'ref': f[0], 'message': f[1], 'ts': int(f[2] or 0)})
    return out


def stash_push(path, message='', include_untracked=False):
    args = ['stash', 'push']
    if include_untracked:
        args.append('-u')
    if (message or '').strip():
        args += ['-m', message.strip()]
    out = run(path, *args, write=True)
    if 'No local changes' in out:
        raise GitError('Không có thay đổi nào để stash.')


def _stash_ref(ref):
    if not _STASH_RE.match(ref or ''):
        raise GitError('Tham chiếu stash không hợp lệ')
    return ref


def stash_apply(path, ref):
    run(path, 'stash', 'apply', _stash_ref(ref), write=True)


def stash_pop(path, ref):
    run(path, 'stash', 'pop', _stash_ref(ref), write=True)


def stash_drop(path, ref):
    run(path, 'stash', 'drop', _stash_ref(ref), write=True)


def stash_show(path, ref):
    return run(path, 'stash', 'show', '-p', '--no-color', '--include-untracked', _stash_ref(ref), ok=(0, 129)) or \
        run(path, 'stash', 'show', '-p', '--no-color', _stash_ref(ref))
