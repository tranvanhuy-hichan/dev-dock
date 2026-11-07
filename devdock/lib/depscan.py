"""Dependency health: phát hiện thư viện của từng project (Node/Python), kiểm tra lạc hậu + lỗ hổng, đọc JSON thực tế của npm/pnpm/yarn/pip.
Không bao giờ tự cài hoặc cập nhật gói — chỉ đọc kết quả và gợi ý lệnh."""
import json
import os
import re
import signal
import subprocess
import tempfile
import threading
import time
import tomllib

from . import projects as P
from .discover import _walk

NOISE = ('cannot set terminal process group', 'no job control in this shell', 'Inappropriate ioctl for device')
NET_RE = re.compile(r'ENOTFOUND|EAI_AGAIN|ETIMEDOUT|ECONNREFUSED|ECONNRESET|ENETUNREACH|EHOSTUNREACH|getaddrinfo|network (?:error|request)|Temporary failure in name resolution|'
                    r'Could not resolve host|Failed to establish a new connection|Network is unreachable|Connection (?:timed out|refused)|offline', re.I)
SEVERITIES = ('critical', 'high', 'moderate', 'low', 'info')
LOCKS = (('pnpm-lock.yaml', 'pnpm'), ('yarn.lock', 'yarn'), ('bun.lockb', 'bun'), ('bun.lock', 'bun'), ('package-lock.json', 'npm'), ('npm-shrinkwrap.json', 'npm'))


class CheckError(RuntimeError):
    pass


class NetError(CheckError):
    """Không có mạng / không tới được registry."""


# ======================================================================================
# Phát hiện
# ======================================================================================
def data_dir():
    return os.path.join(os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share'), 'devdock')


def cache_path():
    return os.path.join(data_dir(), 'deps_cache.json')


def _up_to(path, root):
    """path rồi các thư mục cha cho tới root (gồm cả root)."""
    d = path
    while True:
        yield d
        if d == root or len(d) <= len(root) or os.path.dirname(d) == d:
            return
        d = os.path.dirname(d)


def detect_pm(path, root):
    """(package manager, có lockfile) theo lockfile ở thư mục này hoặc cha (workspace)."""
    for d in _up_to(path, root):
        for fn, pm in LOCKS:
            if os.path.exists(os.path.join(d, fn)):
                return pm, True
    return 'npm', False


def node_deps(pkg):
    """({tên: 'prod'|'dev'|'optional'|'peer'}, tổng số) từ package.json đã parse."""
    out = {}
    for key, kind in (('dependencies', 'prod'), ('devDependencies', 'dev'), ('optionalDependencies', 'optional'), ('peerDependencies', 'peer')):
        v = pkg.get(key)
        if isinstance(v, dict):
            for n in v:
                out.setdefault(n, kind)
    return out


def _req_names(text):
    names = []
    for ln in text.splitlines():
        ln = ln.split('#', 1)[0].strip()
        if not ln or ln.startswith('-') or ln.startswith(('http://', 'https://', 'git+')):
            continue
        m = re.match(r'^([A-Za-z0-9][A-Za-z0-9._-]*)', ln)
        if m:
            names.append(m.group(1).lower().replace('_', '-'))
    return names


def python_deps(path):
    """{tên: 'prod'|'dev'} từ requirements*.txt và pyproject.toml (project.dependencies + optional-dependencies)."""
    out = {}
    try:
        names = sorted(os.listdir(path))
    except OSError:
        return out
    for fn in names:
        if re.match(r'^requirements.*\.txt$', fn):
            try:
                with open(os.path.join(path, fn), encoding='utf-8', errors='replace') as f:
                    for n in _req_names(f.read()):
                        out.setdefault(n, 'dev' if re.search(r'dev|test', fn) else 'prod')
            except OSError:
                pass
    pp = os.path.join(path, 'pyproject.toml')
    if os.path.isfile(pp):
        try:
            with open(pp, 'rb') as f:
                data = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError):
            data = {}
        proj = data.get('project') or {}
        for spec in proj.get('dependencies') or []:
            for n in _req_names(str(spec)):
                out.setdefault(n, 'prod')
        for group in (proj.get('optional-dependencies') or {}).values():
            for spec in group or []:
                for n in _req_names(str(spec)):
                    out.setdefault(n, 'dev')
        poetry = (data.get('tool') or {}).get('poetry') or {}
        for n in (poetry.get('dependencies') or {}):
            if n.lower() != 'python':
                out.setdefault(n.lower().replace('_', '-'), 'prod')
    return out


def find_venv(path, root):
    for d in _up_to(path, root):
        for v in ('.venv', 'venv'):
            py = os.path.join(d, v, 'bin', 'python')
            if os.path.exists(py):
                return os.path.join(d, v)
    return None


def find_targets(roots, max_depth=3):
    """Quét project → danh sách mục tiêu kiểm tra. Mỗi thư mục có package.json (có dependency) hoặc requirements/pyproject là một mục tiêu."""
    out = []
    for root in P.find_projects(roots):
        project = os.path.basename(root.rstrip('/'))
        for d, names in _walk(root, max_depth):
            rel = os.path.relpath(d, root)
            sub = '' if rel == '.' else rel
            if 'package.json' in names:
                try:
                    with open(os.path.join(d, 'package.json'), encoding='utf-8') as f:
                        pkg = json.load(f)
                except (OSError, ValueError):
                    pkg = {}
                deps = node_deps(pkg) if isinstance(pkg, dict) else {}
                if deps:
                    pm, lock = detect_pm(d, root)
                    installed = any(os.path.isdir(os.path.join(x, 'node_modules')) for x in _up_to(d, root))
                    out.append({'key': d + '::node', 'path': d, 'root': root, 'project': project, 'sub': sub, 'eco': 'node', 'pm': pm, 'lock': lock, 'installed': installed,
                                'deps': len(deps), 'declared': deps})
            if any(re.match(r'^requirements.*\.txt$', n) for n in names) or 'pyproject.toml' in names:
                deps = python_deps(d)
                if deps:
                    venv = find_venv(d, root)
                    lock = any(os.path.exists(os.path.join(d, f)) for f in ('poetry.lock', 'Pipfile.lock', 'uv.lock', 'pdm.lock'))
                    out.append({'key': d + '::python', 'path': d, 'root': root, 'project': project, 'sub': sub, 'eco': 'python', 'pm': 'pip', 'lock': lock, 'installed': bool(venv),
                                'deps': len(deps), 'declared': deps, 'venv': venv})
    return out


def label(t):
    return t['project'] + ('/' + t['sub'] if t['sub'] else '')


def install_cmd(t):
    """Lệnh cài thư viện (chỉ để hiển thị/mở trong terminal, không tự chạy)."""
    if t['eco'] == 'node':
        return {'npm': 'npm install', 'pnpm': 'pnpm install', 'yarn': 'yarn install', 'bun': 'bun install'}.get(t['pm'], 'npm install')
    has_req = os.path.exists(os.path.join(t['path'], 'requirements.txt'))
    return 'python3 -m venv .venv && .venv/bin/pip install ' + ('-r requirements.txt' if has_req else '-e .')


# ======================================================================================
# Phân tích JSON
# ======================================================================================
def extract_json(text):
    """Lấy giá trị JSON đầu tiên (object/array) trong text, bỏ qua rác phía trước (cảnh báo của bash/npm). None nếu không có."""
    dec = json.JSONDecoder()
    i = 0
    while i < len(text):
        j = min([k for k in (text.find('{', i), text.find('[', i)) if k >= 0], default=-1)
        if j < 0:
            return None
        try:
            val, _end = dec.raw_decode(text, j)
            if isinstance(val, (dict, list)):
                return val
        except ValueError:
            pass
        i = j + 1
    return None


def _ver(v):
    m = re.match(r'^\D*(\d+)(?:\.(\d+))?(?:\.(\d+))?', str(v or ''))
    return tuple(int(x or 0) for x in m.groups()) if m else None


def bump(current, latest):
    """'major' | 'minor' | 'patch' | '' khi so sánh current → latest."""
    a, b = _ver(current), _ver(latest)
    if not a or not b or b <= a:
        return ''
    return 'major' if b[0] > a[0] else 'minor' if b[1] > a[1] else 'patch'


def _entry(name, current, wanted, latest, kind, dependent=''):
    base = current or wanted
    return {'name': name, 'current': current or '', 'wanted': wanted or '', 'latest': latest or '', 'type': kind, 'dependent': dependent, 'bump': bump(base, latest)}


def parse_npm_outdated(text, declared=None):
    """`npm outdated --json` (npm 10/11): {pkg: {current, wanted, latest, dependent, location}} (giá trị có thể là list khi nhiều vị trí)."""
    declared = declared or {}
    data = extract_json(text)
    if data is None:
        if NET_RE.search(text or ''):
            raise NetError('Không có mạng / không tới được registry')
        return []
    if isinstance(data, dict) and 'error' in data:
        err = data['error']
        msg = (err.get('summary') or err.get('code') or str(err)) if isinstance(err, dict) else str(err)
        if NET_RE.search(json.dumps(data)):
            raise NetError('Không có mạng / không tới được registry')
        raise CheckError(msg)
    out = []
    if isinstance(data, dict):
        for name, v in data.items():
            for item in (v if isinstance(v, list) else [v]):
                if isinstance(item, dict):
                    out.append(_entry(name, item.get('current'), item.get('wanted'), item.get('latest'), declared.get(name, 'prod'), str(item.get('dependent', ''))))
    return _sort(out)


def parse_pnpm_outdated(text, declared=None):
    """`pnpm outdated --format json`: {pkg: {current, latest, wanted, dependencyType}}."""
    declared = declared or {}
    data = extract_json(text)
    if data is None:
        if NET_RE.search(text or ''):
            raise NetError('Không có mạng / không tới được registry')
        return []
    out = []
    if isinstance(data, dict):
        for name, v in data.items():
            if isinstance(v, dict):
                kind = {'dependencies': 'prod', 'devDependencies': 'dev', 'optionalDependencies': 'optional'}.get(v.get('dependencyType'), declared.get(name, 'prod'))
                out.append(_entry(name, v.get('current'), v.get('wanted'), v.get('latest'), kind))
    return _sort(out)


def parse_yarn_outdated(text, declared=None):
    """`yarn outdated --json` (classic): NDJSON, dòng type=table có data.head + data.body [name,current,wanted,latest,type,url]."""
    declared = declared or {}
    out, seen = [], False
    for ln in (text or '').splitlines():
        ln = ln.strip()
        if not ln.startswith('{'):
            continue
        try:
            ev = json.loads(ln)
        except ValueError:
            continue
        if ev.get('type') == 'table':
            seen = True
            for row in (ev.get('data') or {}).get('body') or []:
                if len(row) >= 4:
                    out.append(_entry(row[0], row[1], row[2], row[3], {'dependencies': 'prod', 'devDependencies': 'dev'}.get(row[4] if len(row) > 4 else '', declared.get(row[0], 'prod'))))
        elif ev.get('type') == 'error' and NET_RE.search(json.dumps(ev)):
            raise NetError('Không có mạng / không tới được registry')
    if not seen and NET_RE.search(text or ''):
        raise NetError('Không có mạng / không tới được registry')
    return _sort(out)


def parse_pip_outdated(text, declared=None):
    """`pip list --outdated --format json`: [{name, version, latest_version, latest_filetype}]."""
    declared = declared or {}
    data = extract_json(text)
    if data is None:
        if NET_RE.search(text or ''):
            raise NetError('Không có mạng / không tới được PyPI')
        return []
    out = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict) and item.get('name'):
            n = item['name']
            out.append(_entry(n, item.get('version'), item.get('version'), item.get('latest_version'), declared.get(n.lower().replace('_', '-'), 'transitive')))
    return _sort(out)


def _sort(items):
    order = {'major': 0, 'minor': 1, 'patch': 2, '': 3}
    return sorted(items, key=lambda e: (order[e['bump']], e['name']))


def _counts(vulns, meta=None):
    c = {s: 0 for s in SEVERITIES}
    if isinstance(meta, dict) and any(s in meta for s in SEVERITIES):
        for s in SEVERITIES:
            c[s] = int(meta.get(s) or 0)
    else:
        for v in vulns:
            if v['severity'] in c:
                c[v['severity']] += 1
    c['total'] = sum(c[s] for s in SEVERITIES)
    return c


def _norm_sev(s):
    s = str(s or '').lower()
    return 'moderate' if s == 'medium' else s if s in SEVERITIES else 'info'


def parse_npm_audit(text):
    """`npm audit --json` (npm 7+): {vulnerabilities: {pkg: {severity, via: [dict|str], fixAvailable, range, nodes}}, metadata: {vulnerabilities: {...}}}.
    Cũng nhận định dạng pnpm (advisories). Trả {'counts', 'vulns'}."""
    data = extract_json(text)
    if data is None:
        if NET_RE.search(text or ''):
            raise NetError('Không có mạng / không tới được registry')
        return {'counts': _counts([]), 'vulns': []}
    if not isinstance(data, dict):
        return {'counts': _counts([]), 'vulns': []}
    err = data.get('error')
    if err:
        blob = json.dumps(err)
        if NET_RE.search(blob):
            raise NetError('Không có mạng / không tới được registry')
        code = err.get('code', '') if isinstance(err, dict) else ''
        if code == 'ENOLOCK' or 'lockfile' in blob.lower():
            raise CheckError('Cần lockfile (package-lock.json) để chạy audit')
        raise CheckError((err.get('summary') if isinstance(err, dict) else str(err)) or 'audit lỗi')
    vulns = []
    for name, v in (data.get('vulnerabilities') or {}).items():
        if not isinstance(v, dict):
            continue
        title, url = '', ''
        for via in v.get('via') or []:
            if isinstance(via, dict) and via.get('title'):
                title, url = via['title'], via.get('url', '')
                break
        if not title:
            via_names = [x for x in (v.get('via') or []) if isinstance(x, str)]
            title = ('Phụ thuộc vào gói có lỗ hổng: ' + ', '.join(via_names[:3])) if via_names else 'Lỗ hổng'
        fix = v.get('fixAvailable')
        fix_txt = (f"{fix.get('name')}@{fix.get('version')}" + (' (major)' if fix.get('isSemVerMajor') else '')) if isinstance(fix, dict) else ('có' if fix else '')
        vulns.append({'name': name, 'severity': _norm_sev(v.get('severity')), 'title': title, 'url': url, 'fix': fix_txt, 'range': v.get('range', '')})
    for adv in (data.get('advisories') or {}).values():  # pnpm / npm cũ
        if isinstance(adv, dict):
            vulns.append({'name': adv.get('module_name', '?'), 'severity': _norm_sev(adv.get('severity')), 'title': adv.get('title', ''), 'url': adv.get('url', ''),
                          'fix': adv.get('patched_versions', '') if adv.get('patched_versions') not in ('<0.0.0', None) else '', 'range': adv.get('vulnerable_versions', '')})
    order = {s: i for i, s in enumerate(SEVERITIES)}
    vulns.sort(key=lambda v: (order[v['severity']], v['name']))
    meta = ((data.get('metadata') or {}).get('vulnerabilities'))
    return {'counts': _counts(vulns, meta), 'vulns': vulns}


def parse_yarn_audit(text):
    """`yarn audit --json` (classic): NDJSON auditAdvisory + auditSummary."""
    vulns, meta = [], None
    for ln in (text or '').splitlines():
        ln = ln.strip()
        if not ln.startswith('{'):
            continue
        try:
            ev = json.loads(ln)
        except ValueError:
            continue
        if ev.get('type') == 'auditAdvisory':
            adv = (ev.get('data') or {}).get('advisory') or {}
            vulns.append({'name': adv.get('module_name', '?'), 'severity': _norm_sev(adv.get('severity')), 'title': adv.get('title', ''), 'url': adv.get('url', ''),
                          'fix': adv.get('patched_versions', '') if adv.get('patched_versions') != '<0.0.0' else '', 'range': adv.get('vulnerable_versions', '')})
        elif ev.get('type') == 'auditSummary':
            meta = (ev.get('data') or {}).get('vulnerabilities')
        elif ev.get('type') == 'error' and NET_RE.search(json.dumps(ev)):
            raise NetError('Không có mạng / không tới được registry')
    if not vulns and meta is None and NET_RE.search(text or ''):
        raise NetError('Không có mạng / không tới được registry')
    order = {s: i for i, s in enumerate(SEVERITIES)}
    vulns.sort(key=lambda v: (order[v['severity']], v['name']))
    return {'counts': _counts(vulns, meta), 'vulns': vulns}


def parse_pip_audit(text):
    """`pip-audit --format json`: {dependencies: [{name, version, vulns: [{id, fix_versions, description}]}]} (hoặc list)."""
    data = extract_json(text)
    if data is None:
        if NET_RE.search(text or ''):
            raise NetError('Không có mạng / không tới được PyPI')
        return {'counts': _counts([]), 'vulns': []}
    deps = data.get('dependencies') if isinstance(data, dict) else data
    vulns = []
    for d in deps or []:
        for v in (d.get('vulns') or []):
            vulns.append({'name': d.get('name', '?'), 'severity': 'high', 'title': f"{v.get('id', '')} {str(v.get('description', ''))[:140]}".strip(), 'url': '',
                          'fix': ', '.join(v.get('fix_versions') or []), 'range': d.get('version', '')})
    return {'counts': _counts(vulns), 'vulns': vulns}  # pip-audit không có mức nghiêm trọng: coi là "high"


# ======================================================================================
# Chạy lệnh (bash -ic để có PATH của nvm/pyenv, như Dev Services)
# ======================================================================================
def run_shell(cmd, cwd, timeout=120, cancel=None):
    """Chạy `bash -ic cmd`. Trả (stdout, stderr đã lọc nhiễu, mã thoát). Ném CheckError khi quá thời gian / bị huỷ."""
    prefix = os.environ.get('DEVDOCK_DEPS_PATH_PREFIX')  # chỉ dùng cho test: đặt thư mục có lệnh giả lên đầu PATH (bashrc có thể ghi đè PATH)
    if prefix:
        cmd = f'export PATH="{prefix}:$PATH"; {cmd}'
    env = {**os.environ, 'NO_COLOR': '1', 'FORCE_COLOR': '0', 'CI': '1', 'npm_config_update_notifier': 'false', 'npm_config_fund': 'false', 'NO_UPDATE_NOTIFIER': '1',
           'PIP_DISABLE_PIP_VERSION_CHECK': '1'}
    try:
        proc = subprocess.Popen(['bash', '-ic', cmd], cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors='replace',
                                start_new_session=True, env=env)
    except OSError as e:
        raise CheckError(f'Không chạy được bash: {e}') from None
    end = time.monotonic() + timeout
    out = err = ''
    while True:
        try:
            out, err = proc.communicate(timeout=0.2)
            break
        except subprocess.TimeoutExpired:
            reason = 'Đã huỷ' if (cancel is not None and cancel.is_set()) else ('Quá thời gian chờ (%ds)' % timeout if time.monotonic() > end else '')
            if reason:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    proc.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.communicate()
                raise CheckError(reason) from None
    err = '\n'.join(ln for ln in err.splitlines() if not any(n in ln for n in NOISE))
    return out, err, proc.returncode


def _cmds(t):
    pm = t['pm']
    if pm == 'pnpm':
        return 'pnpm outdated --format json', 'pnpm audit --json'
    if pm == 'yarn':
        return 'yarn outdated --json', 'yarn audit --json'
    return 'npm outdated --json', 'npm audit --json'


def check_target(t, cancel=None, timeout=180):
    """Kiểm tra một mục tiêu. Trả dict kết quả (không ném lỗi): status ok|offline|error|unsupported."""
    res = {'checked_at': time.time(), 'status': 'ok', 'outdated': [], 'vulns': [], 'counts': _counts([]), 'errors': [], 'notes': []}
    declared = t.get('declared') or {}
    try:
        if t['eco'] == 'node':
            if t['pm'] == 'bun':
                res['status'] = 'unsupported'
                res['errors'].append('bun chưa hỗ trợ kiểm tra lạc hậu/lỗ hổng')
                return res
            c_out, c_aud = _cmds(t)
            parse_out = {'pnpm': parse_pnpm_outdated, 'yarn': parse_yarn_outdated}.get(t['pm'], parse_npm_outdated)
            parse_aud = parse_yarn_audit if t['pm'] == 'yarn' else parse_npm_audit
            if not t['installed']:
                res['notes'].append('Chưa cài thư viện nên cột "hiện tại" có thể trống')
            if not t['lock']:
                res['notes'].append('Không có lockfile: bỏ qua audit lỗ hổng' if t['pm'] == 'npm' else 'Không có lockfile')
            o, e, _rc = run_shell(c_out, t['path'], timeout, cancel)  # mã thoát 1 là bình thường khi có gói lạc hậu
            res['outdated'] = parse_out(o + ('\n' + e if not o.strip() else ''), declared)
            if t['lock'] or t['pm'] != 'npm':
                o, e, _rc = run_shell(c_aud, t['path'], timeout, cancel)
                try:
                    a = parse_aud(o + ('\n' + e if not o.strip() else ''))
                    res['vulns'], res['counts'] = a['vulns'], a['counts']
                except NetError:
                    raise
                except CheckError as ex:
                    res['errors'].append(f'Audit: {ex}')
        else:
            venv = t.get('venv') or find_venv(t['path'], t['root'])
            if not venv:
                res['status'] = 'error'
                res['errors'].append('Chưa có môi trường ảo (.venv) — hãy cài thư viện trước')
                return res
            py = os.path.join(venv, 'bin', 'python')
            o, e, rc = run_shell(f'"{py}" -m pip list --outdated --format json', t['path'], timeout, cancel)
            if not o.strip() and re.search(r'No module named pip', e):
                raise CheckError('Môi trường ảo không có pip')
            res['outdated'] = parse_pip_outdated(o if o.strip() else o + '\n' + e, declared)
            if os.path.exists(os.path.join(venv, 'bin', 'pip-audit')) or run_shell(f'"{py}" -c "import pip_audit"', t['path'], 30, cancel)[2] == 0:
                o, e, rc = run_shell(f'"{py}" -m pip_audit --format json -l', t['path'], timeout, cancel)
                try:
                    a = parse_pip_audit(o if o.strip() else o + '\n' + e)
                    res['vulns'], res['counts'] = a['vulns'], a['counts']
                except NetError:
                    raise
            else:
                res['notes'].append('Chưa có pip-audit trong .venv: bỏ qua kiểm tra lỗ hổng Python')
    except NetError as ex:
        res['status'], res['errors'] = 'offline', [str(ex)]
    except CheckError as ex:
        res['status'], res['errors'] = 'error', [str(ex)]
    res['checked_at'] = time.time()
    return res


def summarize(res):
    """{'major','minor','patch','outdated','vulns','serious'} từ một kết quả."""
    b = {'major': 0, 'minor': 0, 'patch': 0}
    for e in res.get('outdated', []):
        if e['bump'] in b:
            b[e['bump']] += 1
    c = res.get('counts') or {}
    return {**b, 'outdated': len(res.get('outdated', [])), 'vulns': c.get('total', 0), 'serious': c.get('critical', 0) + c.get('high', 0)}


def to_markdown(t, res):
    s = summarize(res)
    lines = [f"# Dependency — {label(t)} ({t['eco']}/{t['pm']})", '']
    if res.get('status') != 'ok':
        lines += [f"> Kết quả: {res.get('status')} — " + '; '.join(res.get('errors', [])), '']
    lines.append(f"- Lạc hậu: {s['outdated']} (major {s['major']}, minor {s['minor']}, patch {s['patch']})")
    lines.append(f"- Lỗ hổng: {s['vulns']} (nghiêm trọng/cao: {s['serious']})")
    if res.get('outdated'):
        lines += ['', '| Gói | Hiện tại | Wanted | Latest | Loại | Nhảy |', '|---|---|---|---|---|---|']
        lines += [f"| {e['name']} | {e['current'] or '—'} | {e['wanted'] or '—'} | {e['latest'] or '—'} | {e['type']} | {e['bump'] or '—'} |" for e in res['outdated']]
    if res.get('vulns'):
        lines += ['', '| Mức | Gói | Tiêu đề | Bản sửa |', '|---|---|---|---|']
        lines += [f"| {v['severity']} | {v['name']} | {v['title'].replace('|', '/')} | {v['fix'] or '—'} |" for v in res['vulns']]
    return '\n'.join(lines) + '\n'


# ======================================================================================
# Cache (ghi nguyên tử)
# ======================================================================================
def load_cache(path=None):
    try:
        with open(path or cache_path(), encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


_lock = threading.Lock()


def save_result(key, res, path=None):
    path = path or cache_path()
    with _lock:
        data = load_cache(path)
        data[key] = res
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix='.deps-')
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError:
            pass
