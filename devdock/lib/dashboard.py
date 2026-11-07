"""Dashboard: gom dữ liệu từ các module thành danh sách "Cần chú ý" (thuần logic, không phụ thuộc GTK)."""
import os
import re
import time

LEVEL_ORDER = {'bad': 0, 'warn': 1, 'info': 2}


def greeting(hour):
    return 'Chào buổi sáng' if 5 <= hour < 11 else 'Chào buổi trưa' if 11 <= hour < 14 else 'Chào buổi chiều' if 14 <= hour < 18 else 'Chào buổi tối'


def _pct(a, b):
    return (a / b * 100.0) if b else 0.0


def _names(items, n=3):
    items = list(items)
    return ', '.join(items[:n]) + (f' và {len(items) - n} mục khác' if len(items) > n else '')


def attention(sys=None, projects=None, services=None, docker=None, alerts=None, now=None, secrets=None, conflicts=None):
    """Trả về danh sách {'level','title','detail','module'} đã sắp theo mức độ (bad > warn > info).

    sys: snapshot của sysmon.Monitor. projects: danh sách từ projects.scan. services: [{'name','state','rc'}].
    secrets: tóm tắt secrets.totals ({'bad','warn','repos_with'}). conflicts: ports.conflicts (trùng cổng của dev service).
    docker: {'available': bool, 'containers': [{'name','state','status'}]} hoặc None (chưa biết). alerts: lịch sử watchdog.
    """
    out = []
    now = now or time.time()

    def add(level, title, detail='', module=''):
        out.append({'level': level, 'title': title, 'detail': detail, 'module': module})

    if sys:
        ram = _pct(sys.get('mem_used', 0), sys.get('mem_total', 0))
        if ram >= 90:
            add('bad', f'RAM gần đầy ({ram:.0f}%)', 'Xem tiến trình nào đang ngốn bộ nhớ.', 'sysmon')
        elif ram >= 80:
            add('warn', f'RAM đang cao ({ram:.0f}%)', '', 'sysmon')
        swap = _pct(sys.get('swap_used', 0), sys.get('swap_total', 0))
        if sys.get('swap_total') and swap >= 80:
            add('warn', f'Swap đã dùng {swap:.0f}%', 'Máy đang thiếu RAM thật.', 'sysmon')
        home = os.path.expanduser('~')
        for d in sys.get('disks', []):
            if d['mount'] in ('/', home) and d.get('total'):
                free = _pct(d['free'], d['total'])
                if free < 10:
                    add('bad', f"Ổ {d['mount']} sắp đầy (còn {free:.0f}%)", 'Dọn dẹp bằng DiskCleaner.', 'cleaner')
                elif free < 20:
                    add('warn', f"Ổ {d['mount']} còn {free:.0f}% dung lượng", '', 'cleaner')
    for s in services or []:
        if s.get('state') == 'failed':
            add('bad', f"Dev service “{s['name']}” đã thoát lỗi", f"Mã thoát {s.get('rc')}" if s.get('rc') is not None else '', 'services')
    if projects:
        unpushed = [p['name'] for p in projects if p.get('git') and p.get('ahead')]
        if unpushed:
            add('warn', f'{len(unpushed)} repo có commit chưa push', _names(unpushed), 'git')
        dirty = [p['name'] for p in projects if p.get('git') and (p.get('dirty') or p.get('untracked'))]
        if dirty:
            add('info', f'{len(dirty)} repo có thay đổi chưa commit', _names(dirty), 'git')
        behind = [p['name'] for p in projects if p.get('git') and p.get('behind')]
        if behind:
            add('info', f'{len(behind)} repo đi sau remote (cần pull)', _names(behind), 'git')
    if docker is not None:
        if not docker.get('available'):
            add('info', 'Docker chưa chạy hoặc không kết nối được', '', 'docker')
        else:
            bad = []
            for c in docker.get('containers', []):
                st = (c.get('status') or '').lower()
                if c.get('state') == 'restarting' or 'unhealthy' in st:
                    bad.append(c['name'])
                elif c.get('state') == 'exited':
                    m = re.search(r'exited \((\d+)\)', st)
                    # thoát với mã lỗi (không phải 0/143) cách đây chỉ vài giây/phút => vừa crash
                    if m and m.group(1) not in ('0', '143') and re.search(r'\b(second|minute)s?\b', st):
                        bad.append(c['name'])
            if bad:
                add('warn', f'{len(bad)} container có vấn đề', _names(bad), 'docker')
    if secrets and secrets.get('bad'):
        add('bad', f"{secrets['bad']} bí mật nghiêm trọng trong {secrets['repos_with']} repo", 'Khoá/.env đã commit — thu hồi và tạo khoá mới.', 'secrets')
    elif secrets and secrets.get('warn'):
        add('info', f"{secrets['warn']} mục nghi chứa bí mật cần xem lại", '', 'secrets')
    busy = [c for c in (conflicts or []) if c['level'] == 'busy']
    if busy:
        add('warn', f"{len(busy)} dev service bị trùng cổng", '; '.join(c['detail'] for c in busy[:2]), 'ports')
    recent = [a for a in (alerts or []) if now - float(a.get('t', 0)) < 86400]
    if recent:
        add('warn', f'{len(recent)} cảnh báo trong 24 giờ qua', recent[0].get('title', ''), 'watch')
    out.sort(key=lambda a: LEVEL_ORDER[a['level']])
    return out


def recent_projects(projects, n=6):
    """Project hoạt động gần nhất (ưu tiên repo git)."""
    return sorted([p for p in projects or []], key=lambda p: -(p.get('last_ts') or 0))[:n]


def service_rows(services, statuses, n=6):
    """Sắp xếp dev service cho thẻ nhanh: đang chạy → lỗi → còn lại. statuses: {id: {'state','pid','rc'}}."""
    rank = {'running': 0, 'failed': 1}
    rows = [{**s, 'state': (statuses.get(s['id']) or {}).get('state', 'stopped'), 'rc': (statuses.get(s['id']) or {}).get('rc')} for s in services or []]
    rows.sort(key=lambda r: rank.get(r['state'], 2))
    return rows[:n], max(0, len(rows) - n)
