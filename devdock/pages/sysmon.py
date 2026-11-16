"""Tài nguyên: CPU/RAM/GPU/mạng realtime, ổ đĩa, network. (Process → trang Bộ nhớ, cổng → Cổng mạng, container → DockerDesk.)"""
from collections import deque

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk  # noqa: E402

from ..lib.sysmon import Monitor  # noqa: E402
from ..ui import Page, Sparkline, bar, bg, clear, content_box, duration, esc, hbox, human, label, rate, vbox  # noqa: E402

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
        self.snap_busy = False
        self.stack = Adw.ViewStack(vexpand=True)
        self.hdr_title = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)  # tab chuyển trang nằm trên thanh tiêu đề
        self.stack.add_titled_with_icon(self._overview(), 'overview', 'Tổng quan', 'view-grid-symbolic')
        self.stack.add_titled_with_icon(self._disks(), 'disks', 'Ổ đĩa', 'drive-harddisk-symbolic')
        self.stack.add_titled_with_icon(self._net(), 'net', 'Network', 'network-transmit-receive-symbolic')
        self.set_child(self.stack)
        self.every(1, self.tick_snapshot)

    def show_tool(self, name):
        self.stack.set_visible_child_name(name)

    def tab(self):
        return self.stack.get_visible_child_name()

    # ---------------- Tổng quan ----------------
    def _overview(self):
        sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        box = content_box(12)
        box.set_margin_bottom(12)
        self.info = label('', ['dim-label'], xalign=0, ellipsize=3)
        box.append(self.info)
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
    def _disks(self):
        self.disk_box = content_box(10)
        self.disk_box.set_margin_bottom(12)
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
        box = content_box(12)
        box.set_margin_bottom(12)
        top = hbox(12, homogeneous=True)
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
