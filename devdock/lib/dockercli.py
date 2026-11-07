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


def _config_files(labels):
    m = re.search(r'(?:^|,)com\.docker\.compose\.project\.config_files=([^,]*(?:,/[^,=]*)*)', labels or '')
    return [f for f in (m.group(1).split(',') if m else []) if f.startswith('/')]


def containers(all_=True):
    r = _run(['ps', *(['-a'] if all_ else []), '--no-trunc', '--format', '{{json .}}'])
    out = [{'id': c['ID'], 'name': c.get('Names', ''), 'image': c.get('Image', ''), 'state': c.get('State', ''), 'status': c.get('Status', ''),
            'ports': [p.strip() for p in (c.get('Ports') or '').split(',') if p.strip()], 'project': project_of(c.get('Labels')),
            'compose_files': _config_files(c.get('Labels'))} for c in _jsonl(r.stdout)]
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


# ======================================================================================
# Mở rộng: inspect, dung lượng, volume, network, chạy container, thao tác theo project compose
# ======================================================================================
_UNIT = {'B': 1, 'KB': 1e3, 'MB': 1e6, 'GB': 1e9, 'TB': 1e12, 'KIB': 1024, 'MIB': 1024 ** 2, 'GIB': 1024 ** 3, 'TIB': 1024 ** 4}


def parse_size(s):
    """'137MB' / '1.5GiB' / '12kB' -> bytes (0 nếu không đọc được)."""
    m = re.match(r'^\s*([\d.]+)\s*([A-Za-z]*)', str(s or ''))
    if not m:
        return 0
    try:
        return int(float(m.group(1)) * _UNIT.get(m.group(2).upper() or 'B', 1))
    except ValueError:
        return 0


def inspect(cid):
    """Thông tin chi tiết một container (đã chuẩn hoá)."""
    r = _run(['inspect', cid], timeout=20)
    try:
        v = json.loads(r.stdout)[0]
    except (ValueError, IndexError):
        raise DockerError('Không đọc được thông tin container') from None
    st, cfg, host, net = v.get('State', {}), v.get('Config', {}), v.get('HostConfig', {}), v.get('NetworkSettings', {})
    labels = cfg.get('Labels') or {}
    ports = []
    for cport, binds in sorted((net.get('Ports') or {}).items(), key=lambda kv: (int(kv[0].split('/')[0]) if kv[0].split('/')[0].isdigit() else 0, kv[0])):
        if binds:
            for b in binds:
                ports.append({'container': cport, 'host_ip': b.get('HostIp', ''), 'host_port': b.get('HostPort', '')})
        else:
            ports.append({'container': cport, 'host_ip': '', 'host_port': ''})
    return {
        'id': v.get('Id', ''), 'name': (v.get('Name') or '').lstrip('/'), 'image': cfg.get('Image', ''), 'created': v.get('Created', ''), 'started': st.get('StartedAt', ''),
        'state': st.get('Status', ''), 'running': bool(st.get('Running')), 'exit_code': st.get('ExitCode'), 'error': st.get('Error', ''),
        'health': (st.get('Health') or {}).get('Status', ''), 'restart': (host.get('RestartPolicy') or {}).get('Name', '') or 'no', 'restarts': v.get('RestartCount', 0),
        'cmd': ' '.join(cfg.get('Cmd') or []), 'entrypoint': ' '.join(cfg.get('Entrypoint') or []), 'workdir': cfg.get('WorkingDir', ''), 'user': cfg.get('User', ''),
        'env': [tuple(e.split('=', 1)) if '=' in e else (e, '') for e in (cfg.get('Env') or [])], 'ports': ports,
        'mounts': [{'type': m.get('Type', ''), 'src': m.get('Source') or m.get('Name', ''), 'dst': m.get('Destination', ''), 'rw': m.get('RW', True)} for m in v.get('Mounts', [])],
        'networks': [{'name': n, 'ip': d.get('IPAddress', ''), 'gateway': d.get('Gateway', '')} for n, d in (net.get('Networks') or {}).items()],
        'project': labels.get('com.docker.compose.project'), 'service': labels.get('com.docker.compose.service'),
        'compose_files': [f for f in (labels.get('com.docker.compose.project.config_files') or '').split(',') if f],
    }


def system_df():
    """Dung lượng Docker: [{'type','total','active','size','reclaimable'}] (size/reclaimable tính bằng byte)."""
    r = _run(['system', 'df', '--format', '{{json .}}'], timeout=60)
    out = []
    for row in _jsonl(r.stdout):
        out.append({'type': row.get('Type', ''), 'total': int(row.get('TotalCount') or 0), 'active': int(row.get('Active') or 0),
                    'size': parse_size(row.get('Size')), 'reclaimable': parse_size(str(row.get('Reclaimable', '')).split(' ')[0])})
    return out


def volumes():
    r = _run(['volume', 'ls', '--format', '{{json .}}'])
    vols = {v['Name']: {'name': v['Name'], 'driver': v.get('Driver', ''), 'size': 0, 'links': 0, 'mountpoint': ''} for v in _jsonl(r.stdout)}
    try:  # kích thước + số container đang dùng (chậm nên để sau, lỗi thì bỏ qua)
        d = _run(['system', 'df', '-v', '--format', '{{json .}}'], timeout=90)
        data = json.loads(d.stdout) if d.stdout.strip().startswith('{') else {}
        for v in data.get('Volumes') or []:
            if v.get('Name') in vols:
                vols[v['Name']].update(size=parse_size(v.get('Size')), links=int(v.get('Links') or 0))
    except (DockerError, ValueError):
        pass
    return sorted(vols.values(), key=lambda v: (-v['size'], v['name']))


def volume_remove(name):
    _run(['volume', 'rm', name])


def volume_prune():
    r = _run(['volume', 'prune', '-f'], timeout=120)
    return r.stdout.strip().splitlines()[-1] if r.stdout.strip() else 'Đã dọn'


def networks():
    r = _run(['network', 'ls', '--no-trunc', '--format', '{{json .}}'])
    out = []
    for n in _jsonl(r.stdout):
        out.append({'id': n['ID'], 'name': n['Name'], 'driver': n.get('Driver', ''), 'scope': n.get('Scope', ''), 'subnet': '', 'containers': 0, 'builtin': n['Name'] in ('bridge', 'host', 'none')})
    if out:
        try:
            ins = json.loads(_run(['network', 'inspect', *[n['id'] for n in out]], timeout=30).stdout)
            by = {i['Id']: i for i in ins}
            for n in out:
                i = by.get(n['id'], {})
                cfgs = ((i.get('IPAM') or {}).get('Config') or [])
                n['subnet'] = ', '.join(c.get('Subnet', '') for c in cfgs if c.get('Subnet'))
                n['containers'] = len(i.get('Containers') or {})
        except (DockerError, ValueError):
            pass
    return sorted(out, key=lambda n: (not n['builtin'], n['name']))


def network_remove(name):
    if name in ('bridge', 'host', 'none'):
        raise DockerError('Không xoá được network mặc định')
    _run(['network', 'rm', name])


def network_prune():
    r = _run(['network', 'prune', '-f'], timeout=60)
    return r.stdout.strip().splitlines()[-1] if r.stdout.strip() else 'Đã dọn'


def image_prune(all_unused=False):
    r = _run(['image', 'prune', '-f'] + (['-a'] if all_unused else []), timeout=300)
    return r.stdout.strip().splitlines()[-1] if r.stdout.strip() else 'Đã dọn'


def builder_prune():
    r = _run(['builder', 'prune', '-f'], timeout=300)
    return r.stdout.strip().splitlines()[-1] if r.stdout.strip() else 'Đã dọn'


def container_prune():
    r = _run(['container', 'prune', '-f'], timeout=120)
    return r.stdout.strip().splitlines()[-1] if r.stdout.strip() else 'Đã dọn'


_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')
_ENVKEY_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
_PORT_RE = re.compile(r'^(?:(?:\d{1,3}(?:\.\d{1,3}){3}):)?\d{1,5}:\d{1,5}(?:/(?:tcp|udp))?$')


def run_argv(image, name='', ports=(), env=(), cmd='', rm=False):
    """Dựng lệnh `docker run -d` an toàn (không qua shell). Ném DockerError nếu dữ liệu không hợp lệ."""
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/@-]*', image or ''):
        raise DockerError('Tên image không hợp lệ')
    argv = ['run', '-d']
    if name:
        if not _NAME_RE.match(name):
            raise DockerError('Tên container chỉ gồm chữ, số, _ . -')
        argv += ['--name', name]
    for p in ports:
        p = p.strip()
        if p:
            if not _PORT_RE.match(p):
                raise DockerError(f'Cổng "{p}" không hợp lệ (dạng 8080:80)')
            argv += ['-p', p]
    for e in env:
        e = e.strip()
        if not e:
            continue
        k, _, v = e.partition('=')
        if not _ENVKEY_RE.match(k):
            raise DockerError(f'Tên biến môi trường "{k}" không hợp lệ')
        argv += ['-e', f'{k}={v}']
    if rm:
        argv.append('--rm')
    argv.append(image)
    if cmd.strip():
        import shlex
        try:
            argv += shlex.split(cmd)
        except ValueError as e:
            raise DockerError(f'Lệnh không hợp lệ: {e}') from None
    return argv


def run_container(image, **kw):
    r = _run(run_argv(image, **kw), timeout=300)
    return r.stdout.strip()[:12]


def stats_one(cid):
    r = _run(['stats', '--no-stream', '--format', '{{json .}}', cid], timeout=20)
    rows = _jsonl(r.stdout)
    if not rows:
        raise DockerError('Container không chạy')
    s = rows[0]
    if str(s.get('MemUsage', '')).startswith('0B /'):  # container đã dừng: docker vẫn trả một hàng toàn số 0
        raise DockerError('Container không chạy')
    return {'cpu': _pct(s.get('CPUPerc')), 'mem': s.get('MemUsage', ''), 'mem_bytes': parse_size(str(s.get('MemUsage', '')).split('/')[0]), 'mem_pct': _pct(s.get('MemPerc')),
            'net': s.get('NetIO', ''), 'block': s.get('BlockIO', ''), 'pids': s.get('PIDs', '')}


PROJECT_ACTIONS = {'start', 'stop', 'restart', 'pause', 'unpause', 'down'}


def project_action(project, action):
    """Thao tác cho cả project compose (dựa trên nhãn container, không cần biết file): start/stop/restart/pause/unpause/down."""
    if action not in PROJECT_ACTIONS or not _NAME_RE.match(project or ''):
        raise DockerError('Thao tác hoặc tên project không hợp lệ')
    r = _run(['compose', '-p', project, action], timeout=300, check=False)
    text = r.stdout + r.stderr
    if r.returncode:
        raise DockerError(text.strip() or 'docker compose lỗi')
    return text
