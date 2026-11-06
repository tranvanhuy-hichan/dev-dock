"""Docker qua CLI (`docker` + `docker compose`): không cần SDK, dùng đúng socket/context của người dùng."""
import json
import os
import re
import subprocess


class DockerError(RuntimeError):
    pass


def _run(args, timeout=60, check=True):
    try:
        r = subprocess.run(['docker', *args], capture_output=True, text=True, errors='replace', timeout=timeout)
    except FileNotFoundError:
        raise DockerError('Không tìm thấy lệnh docker') from None
    except subprocess.TimeoutExpired:
        raise DockerError('docker không phản hồi (quá thời gian chờ)') from None
    if check and r.returncode:
        raise DockerError((r.stderr or r.stdout).strip() or f'docker lỗi (mã {r.returncode})')
    return r


def _jsonl(text):
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('{'):
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def status():
    """{'available': bool, 'error': str|None, 'info': dict|None}"""
    try:
        r = _run(['info', '--format', '{{json .}}'], timeout=10)
        v = json.loads(r.stdout)
        return {'available': True, 'error': None, 'info': {
            'version': v.get('ServerVersion', ''), 'os': v.get('OperatingSystem', ''), 'cpus': v.get('NCPU', 0), 'mem_total': v.get('MemTotal', 0),
            'containers': v.get('Containers', 0), 'running': v.get('ContainersRunning', 0), 'images': v.get('Images', 0)}}
    except (DockerError, ValueError) as e:
        return {'available': False, 'error': str(e), 'info': None}


def project_of(labels):
    m = re.search(r'(?:^|,)com\.docker\.compose\.project=([^,]*)', labels or '')
    return m.group(1) if m else None


def containers(all_=True):
    r = _run(['ps', *(['-a'] if all_ else []), '--no-trunc', '--format', '{{json .}}'])
    out = [{'id': c['ID'], 'name': c.get('Names', ''), 'image': c.get('Image', ''), 'state': c.get('State', ''), 'status': c.get('Status', ''),
            'ports': [p.strip() for p in (c.get('Ports') or '').split(',') if p.strip()], 'project': project_of(c.get('Labels'))} for c in _jsonl(r.stdout)]
    out.sort(key=lambda c: (c['state'] != 'running', c['name']))
    return out


ACTIONS = {'start', 'stop', 'restart', 'pause', 'unpause', 'kill', 'remove'}


def action(cid, act):
    if act not in ACTIONS:
        raise DockerError(f'Action không hỗ trợ: {act}')
    _run(['rm', '-f', cid] if act == 'remove' else [act, cid], timeout=120)


def logs(cid, tail=300):
    r = _run(['logs', '--tail', str(int(tail)), '-t', cid], timeout=30)
    return r.stdout + r.stderr  # docker ghi stdout/stderr của container ra hai luồng riêng


def images():
    r = _run(['images', '--no-trunc', '--format', '{{json .}}'])
    used = {c['image'] for c in containers(True)}
    out = []
    for i in _jsonl(r.stdout):
        tag = f"{i.get('Repository')}:{i.get('Tag')}" if i.get('Repository') not in (None, '<none>') else '<none>'
        out.append({'id': i['ID'], 'tag': tag, 'size': i.get('Size', ''), 'created': i.get('CreatedSince', ''), 'used': tag in used or i['ID'] in used})
    return out


def remove_image(iid, force=False):
    _run(['rmi', *(['-f'] if force else []), iid])


def pull(image):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/@-]*', image or ''):
        raise DockerError('Tên image không hợp lệ')
    r = _run(['pull', image], timeout=1800)
    return r.stdout.strip().splitlines()[-1] if r.stdout.strip() else 'Đã pull'


def _pct(s):
    try:
        return float(str(s).rstrip('%'))
    except ValueError:
        return 0.0


def stats():
    r = _run(['stats', '--no-stream', '--format', '{{json .}}'], timeout=30)
    out = [{'id': s.get('ID', ''), 'name': s.get('Name', ''), 'cpu': _pct(s.get('CPUPerc')), 'mem': s.get('MemUsage', ''), 'mem_pct': _pct(s.get('MemPerc')),
            'net': s.get('NetIO', ''), 'block': s.get('BlockIO', '')} for s in _jsonl(r.stdout)]
    out.sort(key=lambda s: -s['cpu'])
    return out


COMPOSE = {'up': ['up', '-d'], 'down': ['down'], 'stop': ['stop'], 'restart': ['restart'], 'pull': ['pull'], 'ps': ['ps', '-a'],
           'logs': ['logs', '--no-color', '--tail', '200']}


def compose(file, act):
    if act not in COMPOSE:
        raise DockerError(f'Action không hỗ trợ: {act}')
    if not os.path.isfile(file):
        raise DockerError(f'Không tìm thấy file: {file}')
    r = _run(['compose', '-f', file, *COMPOSE[act]], timeout=900, check=False)
    text = r.stdout + r.stderr
    if r.returncode:
        raise DockerError(text.strip() or 'docker compose lỗi')
    return text


def exec_argv(cid):
    """Lệnh mở shell trong container (cho terminal)."""
    return ['docker', 'exec', '-it', cid, 'sh', '-c', 'command -v bash >/dev/null 2>&1 && exec bash || exec sh']


def find_compose(roots, max_depth=3):
    """Tìm file compose trong các thư mục gốc (không đi vào node_modules, .git...)."""
    names = {'docker-compose.yml', 'docker-compose.yaml', 'compose.yml', 'compose.yaml'}
    skip = {'node_modules', '.git', '.venv', 'venv', 'target', 'dist', 'build', '__pycache__', 'snap'}
    out = []

    def walk(d, depth):
        try:
            entries = sorted(os.listdir(d))
        except OSError:
            return
        for n in entries:
            p = os.path.join(d, n)
            if n in names and os.path.isfile(p):
                out.append(p)
            elif depth < max_depth and n not in skip and not n.startswith('.') and os.path.isdir(p) and not os.path.islink(p):
                walk(p, depth + 1)
    for r in roots:
        r = os.path.realpath(os.path.expanduser(r))
        if os.path.isdir(r):
            walk(r, 0)
    return out
