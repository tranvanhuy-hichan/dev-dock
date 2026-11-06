"""SysMonitor: CPU/RAM/GPU/mạng realtime, process, cổng mạng, ổ đĩa, container Docker."""
from collections import deque

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk  # noqa: E402

from ..lib import dockercli  # noqa: E402
from ..lib.sysmon import Monitor, list_ports  # noqa: E402
from ..ui import (DataTable, Page, Sparkline, bar, bg, button, clear, duration, esc, hbox, human, label, rate, vbox)  # noqa: E402

HIST = 60


def card(title, big, sub, spark=None, extra=None):
    """Thẻ số liệu. Trả về (widget, big_label, sub_label)."""
    box = vbox(2, css_classes=['card', 'metric'])
    box.append(label(title, ['dim-label', 'caption']))
    b = label(big, ['big-num'])
    s = label(sub, ['dim-label', 'caption'], ellipsize=3)
    box.append(b)
    box.append(s)
    for w in (spark, extra):
        if w is not None:
            box.append(w)
    return box, b, s


def confirm(win, heading, body, label_yes, on_yes, destructive=True):
    d = Adw.AlertDialog(heading=heading, body=body)
    d.add_response('no', 'Huỷ')
    d.add_response('yes', label_yes)
    d.set_response_appearance('yes', Adw.ResponseAppearance.DESTRUCTIVE if destructive else Adw.ResponseAppearance.SUGGESTED)
    d.set_default_response('no')
    d.set_close_response('no')
    d.connect('response', lambda _d, r: r == 'yes' and on_yes())
    d.present(win)


class SysMonitorPage(Page):
    def __init__(self, window):
        super().__init__(window)
        self.mon = Monitor()
        self.hist = {k: deque(maxlen=HIST) for k in ('cpu', 'mem', 'rx', 'tx', 'gpu')}
        self.snap_busy = self.proc_busy = self.port_busy = self.dock_busy = False
        self.stack = Adw.ViewStack()
        root = vbox(0)
        head = hbox(8, margin_top=8, margin_bottom=0, margin_start=16, margin_end=16, halign=Gtk.Align.CENTER)
        head.append(Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE))
        root.append(head)
        self.info = label('', ['dim-label', 'caption'], xalign=0.5, margin_top=4)
        root.append(self.info)
        self.stack.add_titled_with_icon(self._overview(), 'overview', 'Tổng quan', 'view-grid-symbolic')
        self.stack.add_titled_with_icon(self._procs(), 'procs', 'Process', 'view-list-symbolic')
        self.stack.add_titled_with_icon(self._ports(), 'ports', 'Cổng mạng', 'network-wired-symbolic')
        self.stack.add_titled_with_icon(self._disks(), 'disks', 'Ổ đĩa', 'drive-harddisk-symbolic')
        self.stack.add_titled_with_icon(self._net(), 'net', 'Network', 'network-transmit-receive-symbolic')
        self.stack.add_titled_with_icon(self._docker(), 'docker', 'Docker', 'package-x-generic-symbolic')
        self.stack.set_vexpand(True)
        root.append(self.stack)
        self.set_child(root)
        self.every(1, self.tick_snapshot).every(2, self.tick_procs).every(3, self.tick_ports).every(4, self.tick_docker)

    def show_tool(self, name):
        self.stack.set_visible_child_name(name)

    def tab(self):
        return self.stack.get_visible_child_name()

    # ---------------- Tổng quan ----------------
    def _overview(self):
        sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        box = vbox(14, margin_top=14, margin_bottom=16, margin_start=18, margin_end=18)
        grid = Gtk.Grid(column_spacing=14, row_spacing=14, column_homogeneous=True)
        self.sp = {k: Sparkline(c, 56, m) for k, c, m in (('cpu', (.21, .52, .89), 100), ('mem', (.2, .82, .6), 100), ('rx', (.2, .82, .6), None),
                                                          ('tx', (.96, .72, .1), None), ('gpu', (.96, .6, .1), 100))}
        self.c_cpu = card('CPU', '—', '', self.sp['cpu'])
        self.c_mem = card('RAM', '—', '', self.sp['mem'])
        netbox = vbox(0)
        netbox.append(self.sp['rx'])
        netbox.append(self.sp['tx'])
        self.c_net = card('Network', '—', '', None, netbox)
        self.c_gpu = card('GPU', '—', 'Không phát hiện GPU NVIDIA (cần nvidia-smi)', self.sp['gpu'])
        for i, c in enumerate((self.c_cpu, self.c_mem, self.c_net, self.c_gpu)):
            grid.attach(c[0], i % 2, i // 2, 1, 1)
        box.append(grid)
        cores = vbox(6, css_classes=['card', 'metric'])
        cores.append(label('Từng nhân CPU', ['dim-label', 'caption']))
        self.cores = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, min_children_per_line=3, max_children_per_line=8, column_spacing=14, row_spacing=8, homogeneous=True)
        cores.append(self.cores)
        box.append(cores)
        sc.set_child(box)
        return sc

    def tick_snapshot(self):
        if self.snap_busy:
            return
        self.snap_busy = True

        def done(s, err):
            self.snap_busy = False
            if s:
                self.apply_snapshot(s)
        bg(self.mon.snapshot, done)

    def apply_snapshot(self, s):
        self.info.set_text(f"{s['host']} · {s['os']} · uptime {duration(s['uptime'])}")
        h = self.hist
        h['cpu'].append(s['cpu_total'])
        h['mem'].append(s['mem_used'] / s['mem_total'] * 100 if s['mem_total'] else 0)
        h['rx'].append(s['net_rx'])
        h['tx'].append(s['net_tx'])
        _w, big, sub = self.c_cpu
        big.set_text(f"{s['cpu_total']:.1f}%")
        sub.set_text(f"{s['cpu_brand']} · {len(s['cpu_cores'])} luồng · load {' '.join(f'{x:.2f}' for x in s['load'])}")
        _w, big, sub = self.c_mem
        big.set_text(f"{h['mem'][-1]:.1f}%")
        sub.set_text(f"{human(s['mem_used'])} / {human(s['mem_total'])}" + (f" · swap {human(s['swap_used'])}/{human(s['swap_total'])}" if s['swap_total'] else ''))
        _w, big, sub = self.c_net
        big.set_text(f"↓ {rate(s['net_rx'])}")
        sub.set_text(f"↑ {rate(s['net_tx'])}")
        if s['gpus']:
            g = s['gpus'][0]
            h['gpu'].append(g['util'])
            _w, big, sub = self.c_gpu
            self.c_gpu[0].get_first_child().set_text(f"GPU · {g['name']}")
            big.set_text(f"{g['util']:.0f}%")
            sub.set_text(f"VRAM {human(g['mem_used'])} / {human(g['mem_total'])} · {g['temp']:.0f}°C")
        for k, sp in self.sp.items():
            sp.set_data(h[k.rstrip('2')])
        if self.tab() == 'overview':
            self._cores(s['cpu_cores'])
        if self.tab() == 'disks':
            self._disks_render(s['disks'])
        if self.tab() == 'net':
            self._net_render(s)

    def _cores(self, cores):
        if len(cores) != getattr(self, '_ncores', 0):
            clear(self.cores)
            self._core_bars = []
            for i in range(len(cores)):
                b = vbox(2)
                top = hbox(0)
                top.append(label(f'#{i}', ['dim-label', 'caption'], hexpand=True))
                pct = label('', ['caption', 'tnum'])
                top.append(pct)
                pb = Gtk.ProgressBar()
                pb.add_css_class('thin')
                b.append(top)
                b.append(pb)
                self.cores.append(b)
                self._core_bars.append((pct, pb))
            self._ncores = len(cores)
        for (pct, pb), v in zip(self._core_bars, cores):
            pct.set_text(f'{v:.0f}%')
            pb.set_fraction(v / 100)

    # ---------------- Process ----------------
    def _procs(self):
        box = vbox(8, margin_top=12, margin_bottom=14, margin_start=18, margin_end=18)
        bar_ = hbox(8)
        self.p_search = Gtk.SearchEntry(placeholder_text='Tìm theo tên, PID, lệnh…', hexpand=True)
        self.p_search.connect('search-changed', lambda e: self.ptable.set_query(e.get_text()))
        self.p_term = button('Dừng', css=['flat'], on=lambda: self.kill_proc(False))
        self.p_kill = button('Buộc dừng', css=['destructive-action'], on=lambda: self.kill_proc(True))
        for b in (self.p_term, self.p_kill):
            b.set_sensitive(False)
        for w in (self.p_search, self.p_term, self.p_kill):
            bar_.append(w)
        box.append(bar_)
        cols = [
            {'title': 'PID', 'get': lambda d: str(d['pid']), 'sort': lambda d: d['pid'], 'width': 80, 'css': ['tnum']},
            {'title': 'Tên', 'get': lambda d: d['name'], 'sort': lambda d: d['name'].lower(), 'expand': True, 'tip': lambda d: d['cmd'] or d['name']},
            {'title': 'User', 'get': lambda d: d['user'] or '—', 'sort': lambda d: d['user'] or '', 'width': 110},
            {'title': 'CPU', 'get': lambda d: f"{d['cpu']:.1f}%", 'sort': lambda d: d['cpu'], 'width': 80, 'align': 1.0, 'css': ['tnum']},
            {'title': 'RAM', 'get': lambda d: human(d['mem']), 'sort': lambda d: d['mem'], 'width': 100, 'align': 1.0, 'css': ['tnum']},
            {'title': 'Trạng thái', 'get': lambda d: d['status'], 'sort': lambda d: d['status'], 'width': 110},
        ]
        self.ptable = DataTable(cols, key=lambda d: d['pid'], search_text=lambda d: f"{d['name']} {d['pid']} {d['cmd']} {d['user']}",
                                on_select=lambda d: [b.set_sensitive(d is not None) for b in (self.p_term, self.p_kill)])
        self.ptable.sort_by(3, True)
        box.append(self.ptable)
        return box

    def tick_procs(self):
        if self.tab() != 'procs' or self.proc_busy:
            return
        self.proc_busy = True

        def done(rows, err):
            self.proc_busy = False
            if rows is not None:
                self.ptable.set_rows(rows)
        bg(self.mon.processes, done)

    def kill_proc(self, force):
        d = self.ptable.selected()
        if not d:
            return

        def go():
            try:
                Monitor.kill(d['pid'], force)
                self.toast(f"Đã gửi tín hiệu dừng {d['name']} ({d['pid']})")
            except RuntimeError as e:
                self.toast(str(e))
        confirm(self.window, f"{'Buộc dừng' if force else 'Dừng'} {d['name']}?", f"PID {d['pid']}. " + ('Gửi SIGKILL, process không kịp dọn dẹp.' if force else 'Gửi SIGTERM (nhẹ nhàng).'),
                'Buộc dừng' if force else 'Dừng', go)

    # ---------------- Cổng mạng ----------------
    def _ports(self):
        box = vbox(8, margin_top=12, margin_bottom=14, margin_start=18, margin_end=18)
        bar_ = hbox(8)
        s = Gtk.SearchEntry(placeholder_text='Tìm port, process…', hexpand=True)
        s.connect('search-changed', lambda e: self.port_table.set_query(e.get_text()))
        self.port_btn = button('Giải phóng cổng', css=['destructive-action'], on=self.free_port)
        self.port_btn.set_sensitive(False)
        bar_.append(s)
        bar_.append(self.port_btn)
        box.append(bar_)
        cols = [
            {'title': 'Port', 'get': lambda d: str(d['port']), 'sort': lambda d: d['port'], 'width': 90, 'css': ['tnum']},
            {'title': 'Giao thức', 'get': lambda d: d['protocol'], 'sort': lambda d: d['protocol'], 'width': 100},
            {'title': 'Địa chỉ', 'get': lambda d: d['address'], 'sort': lambda d: d['address'], 'expand': True},
            {'title': 'PID', 'get': lambda d: str(d['pid'] or '—'), 'sort': lambda d: d['pid'], 'width': 90, 'css': ['tnum']},
            {'title': 'Process', 'get': lambda d: d['process'], 'sort': lambda d: d['process'].lower(), 'width': 200},
        ]
        self.port_table = DataTable(cols, key=lambda d: (d['port'], d['protocol'], d['pid'], d['address']),
                                    search_text=lambda d: f"{d['port']} {d['process']} {d['pid']} {d['address']}",
                                    on_select=lambda d: self.port_btn.set_sensitive(bool(d and d['pid'])))
        self.port_table.sort_by(0, False)
        box.append(self.port_table)
        box.append(label('Process của user khác có thể không hiện nếu thiếu quyền.', ['dim-label', 'caption']))
        return box

    def tick_ports(self):
        if self.tab() != 'ports' or self.port_busy:
            return
        self.port_busy = True

        def done(rows, err):
            self.port_busy = False
            if rows is not None:
                self.port_table.set_rows(rows)
        bg(list_ports, done)

    def free_port(self):
        d = self.port_table.selected()
        if not d or not d['pid']:
            return

        def go():
            try:
                Monitor.kill(d['pid'], False)
                self.toast(f"Đã dừng {d['process']}")
            except RuntimeError as e:
                self.toast(str(e))
        confirm(self.window, f"Giải phóng cổng {d['port']}?", f"Sẽ dừng process {d['process']} (PID {d['pid']}).", 'Dừng', go)

    # ---------------- Ổ đĩa / Network ----------------
    def _disks(self):
        self.disk_box = vbox(10, margin_top=14, margin_bottom=16, margin_start=18, margin_end=18)
        return Gtk.ScrolledWindow(child=self.disk_box, hscrollbar_policy=Gtk.PolicyType.NEVER)

    def _disks_render(self, disks):
        clear(self.disk_box)
        for d in sorted(disks, key=lambda x: x['mount']):
            used = d['total'] - d['free']
            c = vbox(6, css_classes=['card', 'metric'])
            line = hbox(8)
            line.append(label(f"{d['mount']}", ['heading'], hexpand=True))
            line.append(label(f"{d['name']} · {d['fs']}", ['dim-label', 'caption']))
            c.append(line)
            c.append(bar(used / d['total'] if d['total'] else 0))
            c.append(label(f"{human(used)} / {human(d['total'])} · trống {human(d['free'])}", ['dim-label', 'caption']))
            self.disk_box.append(c)

    def _net(self):
        box = vbox(12, margin_top=14, margin_bottom=16, margin_start=18, margin_end=18)
        top = hbox(14, homogeneous=True)
        self.sp['rx2'], self.sp['tx2'] = Sparkline((.2, .82, .6), 56), Sparkline((.96, .72, .1), 56)
        self.n_rx = card('Download', '—', '', self.sp['rx2'])
        self.n_tx = card('Upload', '—', '', self.sp['tx2'])
        top.append(self.n_rx[0])
        top.append(self.n_tx[0])
        box.append(top)
        self.net_list = vbox(0)
        box.append(self.net_list)
        return Gtk.ScrolledWindow(child=box, hscrollbar_policy=Gtk.PolicyType.NEVER)

    def _net_render(self, s):
        self.n_rx[1].set_text(rate(s['net_rx']))
        self.n_tx[1].set_text(rate(s['net_tx']))
        clear(self.net_list)
        lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=['boxed-list'])
        for n in s['net']:
            r = Adw.ActionRow(title=esc(n['name']), subtitle=esc(f"↓ {rate(n['rx_rate'])}   ↑ {rate(n['tx_rate'])}   ·   tổng nhận {human(n['rx_total'])}   tổng gửi {human(n['tx_total'])}"))
            lb.append(r)
        self.net_list.append(lb)

    # ---------------- Docker ----------------
    def _docker(self):
        box = vbox(8, margin_top=12, margin_bottom=14, margin_start=18, margin_end=18)
        self.dock_stack = Gtk.Stack()
        self.dock_msg = Adw.StatusPage(icon_name='package-x-generic-symbolic', title='Đang đọc thống kê container…')
        self.dock_stack.add_named(self.dock_msg, 'msg')
        cols = [
            {'title': 'Container', 'get': lambda d: d['name'], 'sort': lambda d: d['name'], 'expand': True},
            {'title': 'CPU', 'get': lambda d: f"{d['cpu']:.1f}%", 'sort': lambda d: d['cpu'], 'width': 90, 'align': 1.0, 'css': ['tnum']},
            {'title': 'RAM', 'get': lambda d: d['mem'], 'sort': lambda d: d['mem_pct'], 'width': 200, 'css': ['tnum']},
            {'title': 'RAM %', 'get': lambda d: f"{d['mem_pct']:.1f}%", 'sort': lambda d: d['mem_pct'], 'width': 90, 'align': 1.0, 'css': ['tnum']},
            {'title': 'Net ↓/↑', 'get': lambda d: d['net'], 'sort': lambda d: d['net'], 'width': 170},
            {'title': 'Block I/O', 'get': lambda d: d['block'], 'sort': lambda d: d['block'], 'width': 150},
        ]
        self.dock_table = DataTable(cols, key=lambda d: d['name'])
        self.dock_table.sort_by(1, True)
        self.dock_stack.add_named(self.dock_table, 'table')
        box.append(self.dock_stack)
        return box

    def tick_docker(self):
        if self.tab() != 'docker' or self.dock_busy:
            return
        self.dock_busy = True

        def done(rows, err):
            self.dock_busy = False
            if err:
                self.dock_msg.set_title('Docker chưa sẵn sàng')
                self.dock_msg.set_description(err)
                self.dock_stack.set_visible_child_name('msg')
            elif not rows:
                self.dock_msg.set_title('Không có container nào đang chạy')
                self.dock_msg.set_description('')
                self.dock_stack.set_visible_child_name('msg')
            else:
                self.dock_table.set_rows(rows)
                self.dock_stack.set_visible_child_name('table')
        bg(dockercli.stats, done)
