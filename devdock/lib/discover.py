"""Phát hiện dev server trong các project: package.json (dev/start), docker compose, Makefile, FastAPI/Django, Spring Boot, script khởi chạy.
Mỗi ứng viên: {project, name, cwd, cmd, port, kind, ready, note, default}."""
import json
import os
import re

from . import projects as P

try:  # PyYAML có sẵn trên Ubuntu; thiếu thì chỉ bỏ qua việc đọc cổng trong compose
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

SKIP = {'node_modules', '.git', '.venv', 'venv', '.venv-build', 'target', 'dist', 'build', '.next', 'out', '__pycache__', 'packages', 'docs', 'tests', 'test', 'vendor'}
COMPOSE_NAMES = ('docker-compose.yml', 'docker-compose.yaml', 'compose.yml', 'compose.yaml')
INFRA = ('db', 'postgres', 'postgresql', 'mysql', 'mariadb', 'redis', 'mongo', 'mongodb', 'rabbitmq', 'elasticsearch', 'minio', 'keycloak', 'kafka', 'zookeeper')
WEB_PORTS = (80, 443, 3000, 3001, 4000, 5000, 5173, 8000, 8080, 8081, 8787, 8765, 9000)


def _walk(root, max_depth=3):
    """Duyệt thư mục con (bỏ thư mục sinh tự động). Trả về [(dir, tên các mục)]."""
    out = []

    def go(d, depth):
        try:
            names = sorted(os.listdir(d))
        except OSError:
            return
        out.append((d, names))
        if depth >= max_depth:
            return
        for n in names:
            p = os.path.join(d, n)
            if n in SKIP or n.startswith('.') or os.path.islink(p) or not os.path.isdir(p):
                continue
            go(p, depth + 1)
    go(root, 0)
    return out


def _has_deps(path, project_root, marker='node_modules'):
    """node_modules có ở thư mục này hoặc thư mục cha (workspace hoist) trong phạm vi project."""
    d = path
    while True:
        if os.path.isdir(os.path.join(d, marker)):
            return True
        if d == project_root or len(d) <= len(project_root):
            return False
        d = os.path.dirname(d)


def _pm(path, project_root):
    d = path
    while True:
        for lock, pm in (('pnpm-lock.yaml', 'pnpm'), ('yarn.lock', 'yarn'), ('bun.lockb', 'bun'), ('package-lock.json', 'npm')):
            if os.path.exists(os.path.join(d, lock)):
                return pm
        if d == project_root or len(d) <= len(project_root):
            return 'npm'
        d = os.path.dirname(d)


def script_port(script):
    """Cổng suy ra từ nội dung script npm."""
    m = re.search(r'(?:--port[= ]|-p[= ]|PORT=)(\d{2,5})', script or '')
    if m:
        return int(m.group(1))
    if re.search(r'\bnext\b', script or ''):
        return 3000
    if re.search(r'\bvite\b', script or ''):
        return 5173
    if re.search(r'react-scripts|\bnuxt\b', script or ''):
        return 3000
    if re.search(r'\bastro\b', script or ''):
        return 4321
    return 0


def _node(project, root, d, names, out):
    if 'package.json' not in names:
        return
    try:
        with open(os.path.join(d, 'package.json'), encoding='utf-8') as f:
            j = json.load(f)
    except (OSError, ValueError):
        return
    scripts = j.get('scripts') or {}
    if not isinstance(scripts, dict):
        return
    key = next((k for k in ('dev', 'start:dev', 'serve', 'start') if k in scripts), None)
    if not key:
        return
    script = str(scripts[key])
    desktop = bool(re.search(r'\belectron\b', script))
    pm = _pm(d, root)
    rel = os.path.relpath(d, root)
    ready = _has_deps(d, root)
    runner = f'{pm} run {key}' if pm != 'npm' or key not in ('start',) else 'npm start'
    note = '' if ready else f'Chưa cài thư viện: chạy `{pm} install` trong thư mục này'
    missing = [m for m in re.findall(r'(?<![\w.])(/(?:home|opt|srv)/[\w./-]+)', script) if not os.path.exists(m)]
    if missing:
        ready = False
        note = (note + ' ' if note else '') + f'Script tham chiếu đường dẫn không có trên máy này: {missing[0]}'
    if desktop:
        note = ('Ứng dụng desktop (Electron). ' + note).strip()
    out.append({'project': project, 'name': j.get('name') or os.path.basename(d), 'sub': '' if rel == '.' else rel, 'cwd': d, 'cmd': runner, 'port': 0 if desktop else script_port(script),
                'kind': 'desktop' if desktop else 'node', 'ready': ready, 'note': note, 'default': not desktop})


def compose_ports(path):
    """Các cổng host được publish trong file compose: [(service, host_port)]."""
    if yaml is None:
        return []
    try:
        with open(path, encoding='utf-8') as f:
            y = yaml.safe_load(f) or {}
    except (OSError, ValueError, yaml.YAMLError):
        return []
    out = []
    for svc, spec in (y.get('services') or {}).items():
        for p in (spec or {}).get('ports') or []:
            s = p if isinstance(p, str) else str(p.get('published', '')) if isinstance(p, dict) else str(p)
            s = re.sub(r'\$\{[^}:]+:-([^}]*)\}', r'\1', s)  # ${PORT:-5433} -> 5433
            s = re.sub(r'\$\{[^}]*\}', '', s)             # biến bắt buộc không có mặc định -> bỏ
            parts = s.split('/')[0].split(':')
            host = parts[-2] if len(parts) >= 2 else ''
            if host.isdigit():
                out.append((svc, int(host)))
    return out


def _compose(project, root, d, names, out):
    for fn in COMPOSE_NAMES:
        if fn not in names:
            continue
        path = os.path.join(d, fn)
        try:
            with open(path, encoding='utf-8') as f:
                y = yaml.safe_load(f) if yaml else {}
        except (OSError, ValueError, Exception):  # noqa: BLE001
            y = {}
        services = list((y or {}).get('services') or {})
        ports = compose_ports(path)
        webs = [p for _s, p in ports if p in WEB_PORTS]
        web = min(webs, key=WEB_PORTS.index) if webs else (ports[0][1] if ports else 0)  # ưu tiên cổng web phổ biến (3000 trước 8787...)
        rel = os.path.relpath(d, root)
        flag = '' if fn == 'docker-compose.yml' else f' -f {fn}'
        env_missing = os.path.exists(os.path.join(d, '.env.example')) and not os.path.exists(os.path.join(d, '.env'))
        note = ('Chưa có file .env (có .env.example)' if env_missing else '')
        out.append({'project': project, 'name': 'docker compose', 'sub': '' if rel == '.' else rel, 'cwd': d, 'cmd': f'docker compose{flag} up', 'port': web, 'kind': 'compose',
                    'ready': not env_missing, 'note': ('Toàn bộ stack: ' + ', '.join(services[:8]) + ('…' if len(services) > 8 else '') + ('. ' + note if note else '')) if services else note,
                    'default': True})
        infra = [s for s in services if s in INFRA]
        if infra and len(infra) < len(services):
            out.append({'project': project, 'name': 'hạ tầng (' + ', '.join(infra) + ')', 'sub': '' if rel == '.' else rel, 'cwd': d, 'cmd': f'docker compose{flag} up ' + ' '.join(infra),
                        'port': next((p for s, p in ports if s in infra), 0), 'kind': 'compose', 'ready': not env_missing, 'note': 'Chỉ dựng DB/cache để chạy app ngoài Docker' + (('. ' + note) if note else ''), 'default': True})
        return  # một compose chính mỗi thư mục


def _makefile(project, root, d, names, out):
    if 'Makefile' not in names:
        return
    try:
        with open(os.path.join(d, 'Makefile'), encoding='utf-8', errors='replace') as f:
            lines = f.read().splitlines()
    except OSError:
        return
    rel = os.path.relpath(d, root)
    for target in ('dev', 'run', 'serve'):
        for i, ln in enumerate(lines):
            if re.match(rf'^{target}\s*:(?!=)', ln):
                body = []
                for nxt in lines[i + 1:]:
                    if nxt.startswith(('\t', ' ')) or not nxt.strip():
                        body.append(nxt)
                    else:
                        break
                text = '\n'.join(body)
                if re.search(r'\bup\b.*(\s-d\b|--detach)|--wait\b', text):
                    break  # chạy nền rồi thoát: không phải tiến trình dài hạn
                help_ = ln.split('##', 1)[1].strip() if '##' in ln else ''
                out.append({'project': project, 'name': f'make {target}', 'sub': '' if rel == '.' else rel, 'cwd': d, 'cmd': f'make {target}', 'port': 0, 'kind': 'make', 'ready': True, 'note': help_, 'default': True})
                break


def _python(project, root, d, names, out):
    rel = os.path.relpath(d, root)
    sub = '' if rel == '.' else rel
    venv = next((v for v in ('.venv', 'venv') if os.path.isdir(os.path.join(d, v)) or os.path.isdir(os.path.join(root, v))), None)
    if 'manage.py' in names and not os.path.exists(os.path.join(d, 'Dockerfile')):
        out.append({'project': project, 'name': 'Django runserver', 'sub': sub, 'cwd': d, 'cmd': 'python manage.py runserver', 'port': 8000, 'kind': 'python', 'ready': bool(venv),
                    'note': '' if venv else 'Chưa thấy .venv', 'default': True})
    # FastAPI: tìm file khai báo `x = FastAPI(`
    for fn in ('main.py', os.path.join('app', 'main.py'), os.path.join('src', 'main.py')):
        path = os.path.join(d, fn)
        if os.path.isfile(path):
            _fastapi(project, root, d, path, venv, out)
    # package con kiểu <pkg>/main.py (vd. argus/main.py) khi d là thư mục có pyproject.toml
    if 'pyproject.toml' in names:
        for n in names:
            p = os.path.join(d, n, 'main.py')
            if os.path.isfile(p) and os.path.isfile(os.path.join(d, n, '__init__.py')) and n not in ('tests',):
                _fastapi(project, root, d, p, venv, out)


def _fastapi(project, root, cwd, path, venv, out):
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            src = f.read()
    except OSError:
        return
    if 'FastAPI' not in src or any(o.get('file') == path for o in out):  # file đã được nhận diện từ thư mục cha
        return
    mod = os.path.relpath(path, cwd)[:-3].replace(os.sep, '.')
    m = re.search(r'^(\w+)\s*=\s*FastAPI\(', src, re.M)          # app = FastAPI(...)
    m2 = re.search(r'^(\w+)\s*=\s*create_app\(', src, re.M)       # app = create_app()
    if m:
        target, extra = f'{mod}:{m.group(1)}', ''
    elif m2:
        target, extra = f'{mod}:{m2.group(1)}', ''
    elif re.search(r'^def create_app\(', src, re.M):              # application factory
        target, extra = f'{mod}:create_app', ' --factory'
    else:
        return
    rel = os.path.relpath(cwd, root)
    uv = 'uvicorn'
    for cand, prefix in ((os.path.join(cwd, '.venv', 'bin', 'uvicorn'), '.venv/bin/uvicorn'), (os.path.join(root, '.venv', 'bin', 'uvicorn'), '../.venv/bin/uvicorn' if rel != '.' else '.venv/bin/uvicorn')):
        if os.path.exists(cand):
            uv = prefix
            break
    out.append({'project': project, 'name': 'FastAPI', 'sub': '' if rel == '.' else rel, 'cwd': cwd, 'cmd': f'{uv} {target}{extra} --reload --port 8000', 'port': 8000, 'kind': 'python',
                'file': path, 'ready': uv != 'uvicorn' or bool(venv), 'note': '' if (uv != 'uvicorn' or venv) else 'Chưa thấy .venv — cần cài uvicorn', 'default': True})


def _spring(project, root, d, names, out):
    if 'pom.xml' not in names:
        return
    try:
        with open(os.path.join(d, 'pom.xml'), encoding='utf-8', errors='replace') as f:
            if 'spring-boot' not in f.read():
                return
    except OSError:
        return
    rel = os.path.relpath(d, root)
    wrapper = 'mvnw' in names
    out.append({'project': project, 'name': 'Spring Boot', 'sub': '' if rel == '.' else rel, 'cwd': d, 'cmd': './mvnw spring-boot:run' if wrapper else 'mvn spring-boot:run', 'port': 8080, 'kind': 'java',
                'ready': True, 'note': '', 'default': True})


def _script(project, root, d, names, out):
    for fn in ('dev.sh', 'start.sh', 'run.sh'):
        if fn in names:
            rel = os.path.relpath(d, root)
            out.append({'project': project, 'name': f'./{fn}', 'sub': '' if rel == '.' else rel, 'cwd': d, 'cmd': f'bash ./{fn}', 'port': 0, 'kind': 'script', 'ready': True, 'note': 'Script khởi chạy của project', 'default': True})
            return


def discover(project_paths):
    """Quét danh sách đường dẫn project, trả về danh sách ứng viên dev server."""
    out = []
    for root in project_paths:
        project = os.path.basename(root.rstrip('/'))
        for d, names in _walk(root):
            _node(project, root, d, names, out)
            _compose(project, root, d, names, out)
            _makefile(project, root, d, names, out)
            _python(project, root, d, names, out)
            _spring(project, root, d, names, out)
            _script(project, root, d, names, out)
    # tên hiển thị gọn: "<project>/<thư mục con>" + loại (nếu không phải dev server Node)
    suffix = {'compose': ' (compose)', 'make': '', 'python': ' (FastAPI)', 'java': ' (Spring Boot)', 'script': '', 'desktop': ' (desktop)', 'node': ''}
    for c in out:
        base = c['project'] + ('/' + c['sub'] if c['sub'] else '')
        extra = ''
        if c['kind'] == 'compose':
            extra = ' (hạ tầng: ' + c['name'][len('hạ tầng ('):-1] + ')' if c['name'].startswith('hạ tầng') else ' (compose)'
        elif c['kind'] in ('make', 'script'):
            extra = f" ({c['cmd'].replace('bash ', '')})"
        elif c['kind'] == 'python' and c['name'] != 'FastAPI':
            extra = f" ({c['name']})"
        else:
            extra = suffix.get(c['kind'], '')
        c['title'] = base + extra
    return out


def discover_roots(roots):
    return discover(P.find_projects(roots))


def to_service(c):
    """Ứng viên → dict service lưu trong cài đặt."""
    import uuid
    return {'id': uuid.uuid4().hex[:8], 'name': c['title'], 'project': c['project'], 'cwd': c['cwd'], 'cmd': c['cmd'], 'port': int(c.get('port') or 0), 'kind': c['kind']}


def already_added(c, services):
    return any(s['cwd'] == c['cwd'] and s['cmd'] == c['cmd'] for s in services)


def setup_note(svc):
    """Gợi ý còn thiếu để service chạy được (chuỗi rỗng nếu ổn)."""
    cwd = os.path.expanduser(svc.get('cwd') or '')
    cmd = svc.get('cmd') or ''
    if re.match(r'^(npm|pnpm|yarn|bun)\b', cmd):
        d = cwd
        for _ in range(4):
            if os.path.isdir(os.path.join(d, 'node_modules')):
                return ''
            if os.path.dirname(d) == d:
                break
            d = os.path.dirname(d)
        pm = cmd.split()[0]
        return f'Chưa cài thư viện — chạy `{pm} install` trong thư mục này trước'
    if cmd.startswith('docker compose') and os.path.exists(os.path.join(cwd, '.env.example')) and not os.path.exists(os.path.join(cwd, '.env')):
        return 'Chưa có file .env (có .env.example) — tạo .env trước'
    first = cmd.split()[0] if cmd.split() else ''
    if first.startswith('.venv/') or first.startswith('../.venv/'):
        if not os.path.exists(os.path.normpath(os.path.join(cwd, first))):
            return 'Chưa thấy môi trường ảo (.venv) — cài thư viện Python trước'
    return ''
