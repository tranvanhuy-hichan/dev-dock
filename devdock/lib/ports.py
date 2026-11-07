"""Port Manager: cổng đang lắng nghe + ai đang giữ (process / dev service / container / project), dừng an toàn, phát hiện trùng cổng."""
import os
import re
import signal
import time

import psutil

from .sysmon import list_ports

# Process hệ thống không bao giờ cho dừng từ DevDock.
PROTECTED = {'systemd', 'init', 'dbus-daemon', 'gnome-shell', 'Xorg', 'Xwayland', 'pipewire', 'wireplumber', 'sshd', 'NetworkManager'}


def _proc_info(pid, cache):
    if pid in cache:
        return cache[pid]
    info = {'cmd': '', 'cwd': '', 'user': '', 'mine': False, 'started': 0}
    try:
        p = psutil.Process(pid)
        info['cmd'] = ' '.join(p.cmdline())[:300]
        info['started'] = p.create_time()
        try:
            info['user'] = p.username()
            info['mine'] = p.uids().real == os.getuid()
        except psutil.Error:
            pass
        try:
            info['cwd'] = p.cwd()
        except psutil.Error:
            pass
    except psutil.Error:
        pass
    cache[pid] = info
    return info


def parse_container_ports(specs):
    """['0.0.0.0:5433->5432/tcp', '[::]:5433->5432/tcp', '80/tcp'] -> {cổng_host: (cổng_container, giao_thức)}"""
    out = {}
    for s in specs or []:
        m = re.search(r':(\d+)(?:-(\d+))?->(\d+)(?:-\d+)?/(\w+)', s)
        if not m:
            continue
        lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
        for hp in range(lo, min(hi, lo + 200) + 1):
            out[hp] = (int(m.group(3)) + (hp - lo), m.group(4).upper())
    return out


def service_pids(services, mgr_status):
    """{pid: tên service} cho cả cây tiến trình của dev service DevDock đang chạy. mgr_status: {sid: {'pid': ...}}."""
    out = {}
    for s in services:
        st = mgr_status.get(s['id']) or {}
        pid = st.get('pid')
        if not pid:
            continue
        out[pid] = s['name']
        try:
            for c in psutil.Process(pid).children(recursive=True):
                out[c.pid] = s['name']
        except psutil.Error:
            pass
    return out


def project_of(path, project_paths):
    """Project (thư mục sâu nhất) chứa `path`, hoặc ''."""
    best = ''
    for p in project_paths:
        if path and (path == p or path.startswith(p.rstrip('/') + '/')) and len(p) > len(best):
            best = p
    return best


def snapshot(services=(), mgr_status=None, containers=(), project_paths=(), raw=None):
    """Danh sách cổng đã gắn chủ sở hữu.

    Mỗi dòng: port, protocol, address, pid, process, cmd, user, mine, started, kind ('service'|'container'|'project'|'process'),
    owner (tên hiển thị), project (đường dẫn), container (dict|None), local (chỉ nghe localhost)."""
    raw = list_ports() if raw is None else raw
    svc = service_pids(services, mgr_status or {})
    cports = {}
    for c in containers or []:
        if c.get('state') != 'running':
            continue
        for hp, (cp, proto) in parse_container_ports(c.get('ports')).items():
            cports[(hp, proto)] = (c, cp)
    cache, rows = {}, []
    for r in raw:
        info = _proc_info(r['pid'], cache) if r['pid'] else {'cmd': '', 'cwd': '', 'user': '', 'mine': False, 'started': 0}
        row = {**r, **info, 'kind': 'process', 'owner': r['process'], 'project': '', 'container': None,
               'local': r['address'] in ('127.0.0.1', '::1')}
        cont = cports.get((r['port'], r['protocol']))
        if r['pid'] in svc:
            row.update(kind='service', owner=f"Dev service · {svc[r['pid']]}")
        elif cont:
            row.update(kind='container', owner=f"Container · {cont[0]['name']}", container=cont[0], mine=True)
            row['cmd'] = f"{cont[0]['image']}  (cổng container {cont[1]})"
        proj = project_of(info['cwd'], project_paths)
        if proj:
            row['project'] = proj
            if row['kind'] == 'process':
                row['owner'] = f"{r['process']} · {os.path.basename(proj)}"
        elif cont and cont[0].get('project'):
            row['project'] = cont[0]['project']
        if not r['pid'] and row['owner'] in ('—', '?', ''):
            row['owner'] = 'Không rõ (cần quyền root)'
        rows.append(row)
    return merge(rows)


def merge(rows):
    """Gộp các dòng cùng (cổng, giao thức, PID) khác địa chỉ (IPv4/IPv6/nhiều interface): coi là 'mạng' nếu có địa chỉ không phải localhost."""
    out, idx = [], {}
    for r in rows:
        k = (r['port'], r['protocol'], r['pid'])
        if k in idx:
            first = out[idx[k]]
            first['addresses'].append(r['address'])
            first['local'] = first['local'] and r['local']
            if not r['local']:
                first['address'] = r['address']
            continue
        idx[k] = len(out)
        r['addresses'] = [r['address']]
        out.append(r)
    return out


def can_stop(row):
    """(được_không, lý_do). Quy tắc an toàn: không dừng process của user khác, process hệ thống, chính DevDock."""
    if row.get('container'):
        return True, ''
    pid = row.get('pid') or 0
    if pid <= 1:
        return False, 'Không xác định được process (thiếu quyền hoặc thuộc hệ thống)'
    if pid == os.getpid():
        return False, 'Đây là chính DevDock'
    if not row.get('mine'):
        return False, 'Process của user khác — cần quyền root'
    if row.get('process') in PROTECTED:
        return False, f"{row['process']} là thành phần hệ thống"
    return True, ''


def stop_pid(pid, wait=3.0, force=False):
    """SIGTERM tới process, chờ thoát; nếu `force` thì SIGKILL khi quá hạn. Trả về 'stopped' | 'killed' | 'running'."""
    try:
        p = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return 'stopped'
    if pid <= 1 or pid == os.getpid() or p.name() in PROTECTED:
        raise RuntimeError('Không được dừng process này')
    try:
        p.send_signal(signal.SIGTERM)
        p.wait(wait)
        return 'stopped'
    except psutil.NoSuchProcess:
        return 'stopped'
    except psutil.AccessDenied:
        raise RuntimeError('Không đủ quyền dừng process này') from None
    except psutil.TimeoutExpired:
        if not force:
            return 'running'
    try:
        p.send_signal(signal.SIGKILL)
        p.wait(2)
        return 'killed'
    except psutil.NoSuchProcess:
        return 'killed'
    except (psutil.AccessDenied, psutil.TimeoutExpired):
        raise RuntimeError('Không dừng được process') from None


def conflicts(services, rows, running_ids=()):
    """Trùng cổng liên quan tới dev service. Trả về danh sách {'level','port','service','detail','pid'}:
    - 'busy': cổng của service đang bị process khác giữ (service chưa chạy)
    - 'dup': hai service cấu hình cùng một cổng."""
    by_port = {}
    for r in rows:
        if r['protocol'] == 'TCP':
            by_port.setdefault(r['port'], r)
    out, seen = [], {}
    for s in services:
        port = int(s.get('port') or 0)
        if not port:
            continue
        seen.setdefault(port, []).append(s)
        occ = by_port.get(port)
        if occ and s['id'] not in running_ids:
            out.append({'level': 'busy', 'port': port, 'service': s['name'], 'pid': occ['pid'],
                        'detail': f"Cổng {port} đang bị {occ['owner']}" + (f" (PID {occ['pid']})" if occ['pid'] else '') + ' chiếm'})
    for port, lst in seen.items():
        if len(lst) > 1:
            names = ', '.join(x['name'] for x in lst)
            out.append({'level': 'dup', 'port': port, 'service': names, 'pid': 0, 'detail': f'Các service cùng cấu hình cổng {port}: {names}'})
    out.sort(key=lambda c: (c['level'] != 'busy', c['port']))
    return out


def check_start(svc, services, rows, running_ids=()):
    """Cảnh báo trước khi khởi động `svc`: trả về danh sách chuỗi (rỗng = ổn)."""
    port = int(svc.get('port') or 0)
    if not port:
        return []
    msgs = []
    for c in conflicts(services, rows, running_ids):
        if c['port'] != port:
            continue
        if c['level'] == 'busy' and c['service'] == svc['name']:
            msgs.append(c['detail'])
        elif c['level'] == 'dup' and svc['name'] in c['service']:
            others = [s['name'] for s in services if int(s.get('port') or 0) == port and s['id'] != svc['id']]
            if any(s['id'] in running_ids for s in services if s['id'] != svc['id'] and int(s.get('port') or 0) == port):
                msgs.append(f"Cổng {port} cũng được cấu hình cho: {', '.join(others)} (đang chạy)")
    return msgs


def free_ports(rows, start=3000, end=3100, n=5):
    """Vài cổng trống trong khoảng, để gợi ý khi bị trùng."""
    used = {r['port'] for r in rows}
    return [p for p in range(start, end) if p not in used][:n]


def uptime(started):
    return '' if not started else _fmt(time.time() - started)


def _fmt(sec):
    sec = int(sec)
    if sec < 90:
        return f'{sec}s'
    if sec < 5400:
        return f'{sec // 60}m'
    if sec < 172800:
        return f'{sec // 3600}h'
    return f'{sec // 86400}d'
