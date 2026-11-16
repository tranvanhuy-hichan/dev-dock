"""Bộ nhớ: RAM thực sự (PSS) của từng tiến trình / ứng dụng, xu hướng tăng, dừng an toàn có xác nhận."""
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk  # noqa: E402

from ..lib import memwatch as M  # noqa: E402
from ..ui import DataTable, InfoStrip, Page, SelectionBar, bar, bg, button, clear, content_box, esc, hbox, human, icon_btn, label, toolbar, vbox  # noqa: E402
from .sysmon import card, confirm  # noqa: E402

WARN, BAD, DIM, OK = '#e5a50a', '#e01b24', '#9a9996', '#26a269'
MB = 1024 ** 2


def span(color, text):
    return f'<span foreground="{color}">{esc(text)}</span>'


def trend_markup(delta):
    """Mức thay đổi RAM trong ~5 phút: tăng nhiều thì tô vàng/đỏ."""
    if abs(delta) < MB:
        return span(DIM, '—')
    sign = '+' if delta > 0 else '−'
    txt = f'{sign}{human(abs(delta))}'
    return span(BAD if delta > 200 * MB else WARN if delta > 50 * MB else OK if delta < -50 * MB else DIM, txt)


class MemoryPage(Page):
    def __init__(self, window):
        super().__init__(window)
        self.procs, self.trend, self.mode, self.busy, self.sm = [], M.Trend(), 'app', False, None
        self.protected_pids = M.ancestors()
        self.hdr_end = [icon_btn('view-refresh-symbolic', 'Làm mới', lambda: self.refresh())]

        root = content_box()
        root.set_margin_bottom(0)
        stats = hbox(12, homogeneous=True)
        self.c_used, self.c_pss, self.c_cache, self.c_swap = (card('RAM đang dùng thật', '—', ''), card('Tổng RAM các tiến trình (PSS)', '—', ''),
                                                              card('Cache & buffers', '—', 'tự giải phóng khi cần'), card('Swap', '—', ''))
        for c in (self.c_used, self.c_pss, self.c_cache, self.c_swap):
            stats.append(c[0])
        root.append(stats)

        top = vbox(6, css_classes=['card', 'metric'])
        top.append(label('Ứng dụng dùng nhiều RAM nhất', ['dim-label', 'caption']))
        self.top_box = vbox(5)
        top.append(self.top_box)
        root.append(top)

        self.strip = InfoStrip('info')
        root.append(self.strip)

        self.search = Gtk.SearchEntry(placeholder_text='Tìm theo tên, PID, lệnh…', hexpand=True)
        self.search.connect('search-changed', lambda e: self.table.set_query(e.get_text()))
        self.tg = Adw.ToggleGroup()
        self.tg.add(Adw.Toggle(name='app', label='Theo ứng dụng'))
        self.tg.add(Adw.Toggle(name='proc', label='Theo tiến trình'))
        self.tg.set_active_name('app')
        self.tg.connect('notify::active-name', lambda *_: self.set_mode(self.tg.get_active_name()))
        root.append(toolbar(self.search, self.tg))

        self.table = DataTable([
            {'title': 'Tên', 'get': lambda d: d['name'], 'sort': lambda d: d['name'].lower(), 'expand': True, 'width': 170, 'tip': lambda d: d['cmd'] or d['name']},
            {'title': 'Số tiến trình / PID', 'get': lambda d: str(d['count']) if d['kind'] == 'app' else str(d['pid']), 'sort': lambda d: d['count'] if d['kind'] == 'app' else d['pid'], 'width': 120, 'css': ['tnum']},
            {'title': 'RAM thực (PSS)', 'get': lambda d: ('' if d['exact'] else '≈ ') + human(d['pss']), 'sort': lambda d: d['pss'], 'width': 120, 'css': ['tnum', 'size'],
             'tip': lambda d: 'PSS: phần RAM thật của riêng nó, chia đều phần dùng chung' if d['exact'] else 'Process của user khác: chỉ ước lượng bằng RSS (cần quyền root để đo chính xác)'},
            {'title': '% RAM', 'get': lambda d: f"{d['pct']:.1f}%", 'sort': lambda d: d['pss'], 'width': 70, 'css': ['tnum']},
            {'title': 'CPU', 'get': lambda d: f"{d['cpu']:.0f}%" if d['cpu'] >= 1 else '—', 'sort': lambda d: d['cpu'], 'width': 70, 'css': ['tnum'], 'tip': lambda d: '100% = một nhân CPU'},
            {'title': 'RSS', 'get': lambda d: human(d['rss']), 'sort': lambda d: d['rss'], 'width': 90, 'css': ['tnum', 'dim-label'], 'tip': lambda d: 'RSS đếm trùng bộ nhớ dùng chung nên thường cao hơn RAM thật'},
            {'title': 'Swap', 'get': lambda d: human(d['swap']) if d['swap'] else '—', 'sort': lambda d: d['swap'], 'width': 80, 'css': ['tnum']},
            {'title': 'Tăng sau 5 phút', 'get': lambda d: trend_markup(d['delta']), 'markup': True, 'sort': lambda d: d['delta'], 'width': 120},
            {'title': 'Người dùng', 'get': lambda d: d['user'] or '—', 'width': 100, 'sort': lambda d: d['user']},
        ], key=lambda d: d['key'], search_text=lambda d: f"{d['name']} {d['pid']} {d['cmd']} {d['user']}".lower(), on_select=self.on_select)
        self.table.sort_by(2, True)
        root.append(self.table)

        self.bar = SelectionBar()
        self.b_ign = button('Bỏ qua cảnh báo', icon='notifications-disabled-symbolic', css=['flat'], on=self.ignore)
        self.b_stop = button('Dừng', icon='process-stop-symbolic', css=['destructive-action'], on=self.stop)
        self.bar.add(self.b_ign, self.b_stop)
        outer = vbox(0)
        outer.append(root)
        outer.append(self.bar)
        self.set_child(outer)

        self.every(3, self.refresh)

    # ---- dữ liệu
    def refresh(self):
        if self.busy:
            return
        self.busy = True

        def done(res, err):
            self.busy = False
            if err or res is None:
                self.toast(err or 'Không đọc được danh sách tiến trình')
                return
            self.procs = res
            self.trend.update(res)
            self.render()
        bg(M.snapshot, done)

    def set_mode(self, mode):
        self.mode = mode or 'app'
        self.render()

    def rows(self, total):
        delta = {p['pid']: (lambda r: r[0] if r[1] >= 60 else 0)(self.trend.delta(p['pid'])) for p in self.procs}  # chỉ hiện khi đã đo ≥ 1 phút
        if self.mode == 'app':
            out = []
            for g in M.group(self.procs):
                t = g['top']
                out.append({'key': ('app', g['app']), 'kind': 'app', 'name': g['app'], 'count': g['count'], 'pid': 0, 'pss': g['pss'], 'rss': g['rss'], 'swap': g['swap'],
                            'cpu': g['cpu'], 'pct': g['pss'] / total * 100 if total else 0, 'delta': sum(delta.get(p, 0) for p in g['pids']), 'user': t['user'] if g['mine'] else 'nhiều user',
                            'cmd': t['cmd'][:300], 'pids': g['pids'], 'exact': g['exact'], 'mine': g['mine']})
            return out
        return [{'key': ('proc', p['pid']), 'kind': 'proc', 'name': p['name'], 'count': 1, 'pid': p['pid'], 'pss': p['pss'], 'rss': p['rss'], 'swap': p['swap'],
                 'cpu': p.get('cpu', 0.0), 'pct': p['pss'] / total * 100 if total else 0, 'delta': delta.get(p['pid'], 0), 'user': p['user'], 'cmd': p['cmd'][:300], 'pids': [p['pid']], 'exact': p['exact'],
                 'mine': p['mine'], 'proc': p} for p in self.procs]

    def render(self):
        s = M.summary(self.procs)
        total = s['total']
        self.c_used[1].set_text(human(s['used']))
        self.c_used[2].set_text(f"{s['used'] / total * 100:.0f}% của {human(total)} · còn {human(s['available'])}")
        self.c_pss[1].set_text(human(s['pss']))
        self.c_pss[2].set_text(f"RSS cộng lại: {human(s['rss_sum'])} (đếm trùng {human(max(0, s['rss_sum'] - s['pss']))})")
        self.c_cache[1].set_text(human(s['cache']))
        import psutil
        sw = psutil.swap_memory()
        self.c_swap[1].set_text(human(sw.used))
        self.c_swap[2].set_text(f'của {human(sw.total)}' if sw.total else 'không bật swap')
        rows = self.rows(total)
        self.table.set_rows(rows)
        self.set_subtitle(f'{len(self.procs)} tiến trình')
        # top ứng dụng
        clear(self.top_box)
        for g in M.group(self.procs)[:6]:
            line = hbox(10)
            line.append(label(g['app'], ['caption'], width_chars=18, max_width_chars=18, ellipsize=3))
            b = bar(g['pss'] / total if total else 0)
            b.set_hexpand(True)
            b.set_valign(2)
            line.append(b)
            line.append(label(f"{human(g['pss'])} · {g['count']} tiến trình", ['caption', 'tnum'], xalign=1, width_chars=22))
            self.top_box.append(line)
        if s['approx']:
            self.strip.show(f"{s['approx']} tiến trình của user khác/hệ thống chỉ có số ước lượng (≈) vì cần quyền root để đo PSS.")
        else:
            self.strip.set_visible(False)

    # ---- thao tác
    def on_select(self, d):
        if not d:
            self.bar.select(None)
            return
        targets, skipped = self.targets(d)
        self.bar.select(f"{d['name']}" + (f" · {d['count']} tiến trình" if d['kind'] == 'app' else f" · PID {d['pid']}"),
                        f"{human(d['pss'])} RAM thực" + (f' · {skipped} mục được bảo vệ' if skipped and targets else ''))
        ok = bool(targets)
        self.b_stop.set_sensitive(ok)
        why = '' if ok else (M.can_kill(d['proc'])[1] if d.get('proc') else 'Không có tiến trình nào được phép dừng (hệ thống / user khác)')
        self.b_stop.set_tooltip_text(why or None)
        self.b_stop.get_child().get_last_child().set_text('Dừng cả nhóm' if d['kind'] == 'app' and len(targets) > 1 else 'Dừng')

    def targets(self, d):
        """(các pid được phép dừng, số pid bị bảo vệ)."""
        by_pid = {p['pid']: p for p in self.procs}
        ok, skipped = [], 0
        for pid in d['pids']:
            p = by_pid.get(pid)
            if p is None:
                continue
            if M.can_kill(p)[0] and pid not in self.protected_pids:
                ok.append(pid)
            else:
                skipped += 1
        return ok, skipped

    def stop(self):
        d = self.table.selected()
        if not d:
            return
        targets, skipped = self.targets(d)
        if not targets:
            self.toast('Không có tiến trình nào được phép dừng')
            return
        by_pid = {p['pid']: p for p in self.procs}
        freed = sum(by_pid[p]['pss'] for p in targets if p in by_pid)
        what = f"{d['name']} ({len(targets)} tiến trình)" if d['kind'] == 'app' else f"{d['name']} (PID {d['pid']})"
        body = f'Giải phóng khoảng {human(freed)} RAM. Việc chưa lưu trong ứng dụng sẽ mất.' + (f'\n{skipped} tiến trình hệ thống/được bảo vệ sẽ được bỏ qua.' if skipped else '')
        if d['kind'] == 'proc':
            body += f"\n{d['cmd'][:200]}"

        def go(force=False, pids=None):
            pids = pids or targets

            def done(res, err):
                if err:
                    self.toast(err)
                elif res['alive'] and not force:
                    confirm(self.window, f"{len(res['alive'])} tiến trình chưa thoát", 'Chúng không phản hồi tín hiệu dừng. Buộc dừng (SIGKILL) có thể mất dữ liệu chưa lưu.', 'Buộc dừng',
                            lambda: go(True, res['alive']))
                    return
                else:
                    self.toast(f"Đã dừng {len(res['stopped'])} tiến trình" + (f", {len(res['denied'])} bị từ chối quyền" if res['denied'] else ''))
                self.refresh()
            bg(lambda: M.stop_many(pids, wait=3.0, force=force), done)
        confirm(self.window, f'Dừng {what}?', body, 'Dừng', go)

    def ignore(self):
        d = self.table.selected()
        if not d:
            return
        names = {d['name']} if d['kind'] == 'proc' else {n for n in (self.procs_name(p) for p in d['pids']) if n}
        ig = self.window.watch.config['ignore']
        added = [n for n in sorted(names) if n.lower() not in {x.lower() for x in ig}]
        ig.extend(added)
        self.window.watch.save()
        self.toast(f"Sẽ không cảnh báo RAM/CPU về {', '.join(added) or d['name']} nữa (khôi phục ở Giám sát → Luật)")

    def procs_name(self, pid):
        return next((p['name'] for p in self.procs if p['pid'] == pid), '')
