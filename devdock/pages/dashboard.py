"""Dashboard: trang chủ — tình trạng máy, mục cần chú ý, project gần đây, dev service, Docker, Ollama, cảnh báo, tự khởi động."""
import datetime
import time
from collections import deque

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Adw, GLib, Gtk, Pango, PangoCairo  # noqa: E402

from ..lib import dashboard as DB  # noqa: E402
from ..lib import dockercli as DK  # noqa: E402
from ..lib import ollama as O  # noqa: E402
from ..lib import projects as P  # noqa: E402
from ..lib import watchdog as W  # noqa: E402
from ..lib.sysmon import Monitor  # noqa: E402
from ..ui import InfoStrip, Page, Sparkline, bar, bg, button, clear, content_box, duration, esc, hbox, human, icon_btn, label, rate, vbox  # noqa: E402
from .projects import status_markup  # noqa: E402


LEVEL_ICON = {'bad': 'dialog-error-symbolic', 'warn': 'dialog-warning-symbolic', 'info': 'dialog-information-symbolic'}
LEVEL_CSS = {'bad': 'error', 'warn': 'warning', 'info': 'accent'}
BLUE, AMBER, RED = (0.21, 0.52, 0.89), (0.90, 0.65, 0.04), (0.88, 0.10, 0.14)


class Gauge(Gtk.DrawingArea):
    """Vòng đo tròn có hoạt ảnh mượt; đổi màu theo mức tải (xanh → vàng → đỏ)."""

    def __init__(self, size=96, invert=False):
        super().__init__(content_width=size, content_height=size, valign=Gtk.Align.CENTER, halign=Gtk.Align.START)
        self.frac, self.shown, self.text, self.invert, self._anim = 0.0, 0.0, '—', invert, None
        self.set_draw_func(self._draw)

    def set(self, frac, text):
        frac = max(0.0, min(1.0, frac))
        self.text = text
        if abs(frac - self.frac) < 0.002:
            self.queue_draw()
            return
        start = self.shown
        self.frac = frac
        if self._anim:
            self._anim.skip()
        self._anim = Adw.TimedAnimation.new(self, start, frac, 450, Adw.CallbackAnimationTarget.new(self._step))
        self._anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        self._anim.play()

    def _step(self, v):
        self.shown = v
        self.queue_draw()

    def _draw(self, _a, cr, w, h):
        import math
        cx, cy, r, lw = w / 2, h / 2, min(w, h) / 2 - 8, 9
        dark = Adw.StyleManager.get_default().get_dark()
        cr.set_line_width(lw)
        cr.set_source_rgba(*((1, 1, 1, .10) if dark else (0, 0, 0, .10)))
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.stroke()
        v = self.shown
        level = (1 - v) if self.invert else v  # invert: ít càng nguy hiểm (không dùng cho ổ đĩa vì ta vẽ phần ĐÃ DÙNG)
        color = BLUE if level < .6 else AMBER if level < .85 else RED
        if v > 0.003:
            cr.set_line_cap(1)
            cr.set_source_rgb(*color)
            cr.arc(cx, cy, r, -math.pi / 2, -math.pi / 2 + 2 * math.pi * v)
            cr.stroke()
        layout = self.create_pango_layout(self.text)
        layout.set_font_description(Pango.FontDescription('Bold 15'))
        tw, th = layout.get_pixel_size()
        cr.set_source_rgba(*((1, 1, 1, .95) if dark else (0, 0, 0, .85)))
        cr.move_to(cx - tw / 2, cy - th / 2)
        PangoCairo.show_layout(cr, layout)


def gauge_card(title, gauge, spark=None):
    """Thẻ số liệu có vòng đo: trả (widget, tiêu_đề, dòng_lớn, dòng_phụ)."""
    box = vbox(6, css_classes=['card', 'gauge-card'])
    top = hbox(14)
    top.append(gauge)
    col = vbox(1, valign=Gtk.Align.CENTER, hexpand=True)
    t = label(title, ['dim-label', 'caption'], ellipsize=3)
    big = label('—', ['title-3'], ellipsize=3)
    sub = label('', ['dim-label', 'caption'], ellipsize=3, max_width_chars=26)
    for w in (t, big, sub):
        col.append(w)
    top.append(col)
    box.append(top)
    if spark is not None:
        box.append(spark)
    return box, t, big, sub


def metric_card(title, big, sub):
    """Thẻ số liệu nhỏ (Docker/Ollama/Cảnh báo): (widget, tiêu_đề, dòng_lớn, dòng_phụ)."""
    box = vbox(2, css_classes=['card', 'gauge-card'])
    t = label(title, ['dim-label', 'caption'])
    b = label(big, ['big-num'])
    s = label(sub, ['dim-label', 'caption'], ellipsize=3, max_width_chars=34, xalign=0)
    for w in (t, b, s):
        box.append(w)
    return box, t, b, s


class DashboardPage(Page):
    def __init__(self, window):
        super().__init__(window)
        self.mon = Monitor()
        self.hist = {'cpu': deque(maxlen=60), 'mem': deque(maxlen=60)}
        self.snap = self.projects = self.docker = self.ollama = None
        self.alerts, self.last_scan, self.busy, self.svc_sig = [], 0.0, set(), None
        self.hdr_end = [icon_btn('view-refresh-symbolic', 'Làm mới tất cả', self.refresh_all)]

        sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        root = content_box(14)
        root.set_margin_bottom(18)
        # --- banner chào: gradient + đồng hồ + các chip tóm tắt (bấm để mở module)
        hero = vbox(10, css_classes=['dash-hero'])
        top = hbox(12)
        left = vbox(2, hexpand=True, valign=Gtk.Align.CENTER)
        self.title = label('', ['hero-title'], xalign=0)
        self.sub = label('', ['dim-label'], xalign=0, ellipsize=3)
        left.append(self.title)
        left.append(self.sub)
        right = vbox(0, halign=Gtk.Align.END, valign=Gtk.Align.CENTER)
        self.clock = label('', ['hero-clock'], xalign=1)
        self.date = label('', ['dim-label'], xalign=1)
        right.append(self.clock)
        right.append(self.date)
        top.append(left)
        top.append(right)
        hero.append(top)
        self.chips = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=8, row_spacing=8, max_children_per_line=8, homogeneous=False)
        hero.append(self.chips)
        root.append(hero)
        # --- vòng đo
        row = hbox(12, homogeneous=True)
        self.g_cpu, self.g_mem, self.g_disk, self.g_gpu = Gauge(), Gauge(), Gauge(), Gauge()
        self.sp_cpu, self.sp_mem = Sparkline((.21, .52, .89), 30, 100), Sparkline((.2, .82, .6), 30, 100)
        self.c_cpu = gauge_card('CPU', self.g_cpu, self.sp_cpu)
        self.c_mem = gauge_card('RAM', self.g_mem, self.sp_mem)
        self.c_disk = gauge_card('Ổ đĩa', self.g_disk)
        self.c_net = gauge_card('Mạng', self.g_gpu)
        for c in (self.c_cpu, self.c_mem, self.c_disk, self.c_net):
            row.append(c[0])
        root.append(row)
        # --- cần chú ý
        root.append(label('Cần chú ý', ['section-title'], xalign=0))
        self.attn_box = vbox(8)
        root.append(self.attn_box)
        # --- project gần đây | dev service
        cols = hbox(12, homogeneous=True)
        self.proj_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=['boxed-list'])
        self.svc_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=['boxed-list'])
        self.svc_more = Gtk.Button(label='', css_classes=['flat'], visible=False, halign=Gtk.Align.START)
        self.svc_more.connect('clicked', lambda *_: self.window.select_module('services'))
        cols.append(self._section('Project gần đây', 'projects', self.proj_list))
        cols.append(self._section('Dev Services', 'services', self.svc_list, self.svc_more))
        root.append(cols)
        # --- Docker | Ollama | Cảnh báo
        row3 = hbox(12, homogeneous=True)
        self.c_dock = metric_card('Docker', '—', 'đang kiểm tra…')
        self.c_oll = metric_card('Ollama AI', '—', 'đang kiểm tra…')
        self.c_alert = metric_card('Cảnh báo (24 giờ)', '—', '')
        for c, mod in ((self.c_dock, 'docker'), (self.c_oll, 'ollama'), (self.c_alert, 'watch')):
            c[0].append(button('Mở →', css=['flat'], on=lambda m=mod: self.window.select_module(m)))
            c[0].get_last_child().set_halign(Gtk.Align.START)
            row3.append(c[0])
        root.append(row3)
        # --- khởi động cùng máy
        grp = Adw.PreferencesGroup(title='Khởi động')
        self.auto_row = Adw.SwitchRow(title='Tự khởi động DevDock khi đăng nhập', active=W.autostart_enabled())
        self.auto_row.connect('notify::active', lambda w, _p: self.set_autostart(w.get_active()))
        grp.add(self.auto_row)
        root.append(grp)
        self.auto_note = label('', ['dim-label', 'caption'], xalign=0, wrap=True, max_width_chars=90)
        root.append(self.auto_note)
        sc.set_child(root)
        self.set_child(sc)
        self.update_auto_note()
        self.every(2, self.tick_fast).every(5, self.tick_projects).every(10, self.tick_docker).every(15, self.tick_ollama)

    # ---------------- dựng khối ----------------
    def _section(self, title, module, lst, extra=None):
        box = vbox(6)
        head = hbox(6)
        head.append(label(title, ['section-title'], hexpand=True, xalign=0))
        head.append(button('Xem tất cả →', css=['flat'], on=lambda: self.window.select_module(module)))
        box.append(head)
        box.append(lst)
        if extra:
            box.append(extra)
        return box

    def update_auto_note(self):
        bg_on = bool(getattr(getattr(self.window, 'watch', None), 'config', {}).get('background'))
        if self.auto_row.get_active():
            self.auto_note.set_text('DevDock sẽ mở cửa sổ này mỗi lần bạn đăng nhập máy.' + ('' if bg_on else ' Muốn chỉ chạy ngầm (giám sát) mà không hiện cửa sổ, bật “Tiếp tục giám sát khi đóng cửa sổ” ở trang Giám sát.'))
        else:
            self.auto_note.set_text('Đang tắt: DevDock chỉ chạy khi bạn tự mở.')

    def set_autostart(self, on):
        try:
            W.set_autostart(on)
        except OSError as e:
            self.toast(f'Không ghi được file tự khởi động: {e}')
            self.auto_row.set_active(W.autostart_enabled())
        self.update_auto_note()

    # ---------------- làm mới dữ liệu ----------------
    def once(self, key, fn, done):
        if key in self.busy:
            return
        self.busy.add(key)
        bg(fn, lambda r, e: (self.busy.discard(key), done(r, e)))

    def refresh_all(self):
        self.last_scan = 0
        for f in (self.tick_fast, self.tick_projects, self.tick_docker, self.tick_ollama):
            f()

    def tick_fast(self):
        now = datetime.datetime.now()
        self.title.set_text(f"{DB.greeting(now.hour)}!")
        self.clock.set_text(now.strftime('%H:%M'))
        self.date.set_text(now.strftime('%A, %d/%m/%Y'))
        self.render_chips()

        def done(s, _e):
            if s:
                self.snap = s
                self.render_system()
        self.once('sys', self.mon.snapshot, done)
        self.render_services()
        self.render_attention()

    def tick_projects(self):
        if time.time() - self.last_scan < 45:
            return
        self.last_scan = time.time()
        roots = self.window.store['project_roots'] or P.default_roots()

        def done(res, _e):
            if res is not None:
                self.projects = res
                self.render_projects()
                self.render_attention()
        self.once('proj', lambda: P.scan(roots), done)

    def tick_docker(self):
        def work():
            st = DK.status()
            return {'available': st['available'], 'containers': DK.containers(True) if st['available'] else []}

        def done(res, err):
            self.docker = res if res else {'available': False, 'containers': []}
            self.alerts = W.load_history()
            self.render_docker()
            self.render_alerts()
            self.render_attention()
        self.once('docker', work, done)

    def tick_ollama(self):
        host = self.window.store['ollama_host'] or None

        def work():
            c = O.Client(host)
            return {'up': True, 'version': c.version(), 'tags': c.tags(), 'ps': c.ps()}

        def done(res, err):
            self.ollama = res or {'up': False}
            self.render_ollama()
        self.once('ollama', work, done)

    # ---------------- hiển thị ----------------
    def render_system(self):
        s = self.snap
        self.sub.set_text(f"{s['host']} · {s['os']} · đã bật máy {duration(s['uptime'])}")
        self.hist['cpu'].append(s['cpu_total'])
        mem = s['mem_used'] / s['mem_total'] * 100 if s['mem_total'] else 0
        self.hist['mem'].append(mem)
        self.g_cpu.set(s['cpu_total'] / 100, f"{s['cpu_total']:.0f}%")
        self.c_cpu[2].set_text(f"{len(s['cpu_cores'])} luồng")
        self.c_cpu[3].set_text(f"load {s['load'][0]:.2f} · {s['cpu_brand'].split('@')[0].replace('(R)', '').replace('(TM)', '').strip()[:28]}")
        self.sp_cpu.set_data(self.hist['cpu'])
        self.g_mem.set(mem / 100, f'{mem:.0f}%')
        self.c_mem[2].set_text(f"{human(s['mem_used'])} / {human(s['mem_total'])}")
        self.c_mem[3].set_text(f"swap {human(s['swap_used'])}" if s['swap_total'] else 'không có swap')
        self.sp_mem.set_data(self.hist['mem'])
        root = next((d for d in s['disks'] if d['mount'] == '/'), s['disks'][0] if s['disks'] else None)
        if root:
            used = 1 - root['free'] / root['total']
            self.g_disk.set(used, f'{used * 100:.0f}%')
            self.c_disk[2].set_text(f"{human(root['free'])} trống")
            self.c_disk[3].set_text(f"{root['mount']} · tổng {human(root['total'])}")
        if s['gpus']:
            g = s['gpus'][0]
            self.c_net[1].set_text('GPU · ' + g['name'].replace('NVIDIA GeForce ', '').replace(' Laptop GPU', ''))
            self.g_gpu.set(g['util'] / 100, f"{g['util']:.0f}%")
            self.c_net[2].set_text(f"VRAM {human(g['mem_used'])}")
            self.c_net[3].set_text(f"của {human(g['mem_total'])} · {g['temp']:.0f}°C")
        else:
            self.c_net[1].set_text('Mạng')
            self.g_gpu.set(0, '↕')
            self.c_net[2].set_text(f"↓ {rate(s['net_rx'])}")
            self.c_net[3].set_text(f"↑ {rate(s['net_tx'])}")
        self.render_attention()

    def render_chips(self):
        """Các chip tóm tắt ở banner; bấm để mở module tương ứng."""
        stat = self.statuses()
        run = sum(1 for v in stat.values() if v['state'] == 'running')
        items = [(f'{run}/{len(stat)} dev service chạy', 'services', '#26a269' if run else '#9a9996')]
        if self.projects is not None:
            ahead = sum(1 for p in self.projects if p.get('git') and p.get('ahead'))
            dirty = sum(1 for p in self.projects if p.get('git') and (p.get('dirty') or p.get('untracked')))
            items += [(f'{len(self.projects)} project', 'projects', '#3584e4'), (f'{ahead} repo chưa push', 'git', '#e01b24' if ahead else '#9a9996'), (f'{dirty} repo chưa commit', 'git', '#e5a50a' if dirty else '#9a9996')]
        if self.docker is not None:
            n = sum(1 for c in self.docker['containers'] if c['state'] == 'running')
            items.append((f'{n} container chạy' if self.docker['available'] else 'Docker tắt', 'docker', '#26a269' if n else '#9a9996'))
        if self.ollama is not None:
            items.append((f"{len(self.ollama['ps'])} model đang nạp" if self.ollama.get('up') else 'Ollama tắt', 'ollama', '#26a269' if self.ollama.get('up') and self.ollama['ps'] else '#9a9996'))
        sig = [(t, m) for t, m, _c in items]
        if sig == getattr(self, '_chip_sig', None):
            return
        self._chip_sig = sig
        self.chips.remove_all()
        for text, mod, color in items:
            b = Gtk.Button(css_classes=['chip', 'flat'])
            b.set_child(Gtk.Label(use_markup=True, label=f'<span foreground="{color}">●</span>  {esc(text)}'))
            b.connect('clicked', lambda _b, m=mod: self.window.select_module(m))
            self.chips.append(b)

    def statuses(self):
        mgr = self.window.services
        return {s['id']: mgr.status(s['id']) for s in self.window.store['services']}

    def render_services(self):
        svcs = self.window.store['services']
        stat = self.statuses()
        rows, more = DB.service_rows(svcs, stat, 6)
        sig = (tuple((r['id'], r['state']) for r in rows), more)
        if sig == self.svc_sig:
            return
        self.svc_sig = sig
        self.svc_list.remove_all()
        if not rows:
            self.svc_list.append(Adw.ActionRow(title='Chưa có dev service nào', subtitle='Vào Dev Services → “Tìm trong project” để thêm.', activatable=False))
        for r in rows:
            running = r['state'] == 'running'
            dot = {'running': '#26a269', 'failed': '#e01b24'}.get(r['state'], '#9a9996')
            ar = Adw.ActionRow(title=esc(r['name']), subtitle=esc(r['cmd'] + (f"  ·  :{r['port']}" if r['port'] else '')), activatable=False)
            av = Adw.Avatar(size=34, text=r['name'], show_initials=True)
            ar.add_prefix(av)
            ar.add_prefix(Gtk.Label(use_markup=True, label=f'<span foreground="{dot}" size="large">●</span>'))
            b = icon_btn('media-playback-stop-symbolic' if running else 'media-playback-start-symbolic', 'Dừng' if running else 'Chạy', lambda s=r, run=running: self.toggle_service(s, run), ['flat', 'circular'])
            b.set_valign(Gtk.Align.CENTER)
            ar.add_suffix(b)
            self.svc_list.append(ar)
        self.svc_more.set_visible(more > 0)
        self.svc_more.set_label(f'… và {more} dev service khác')

    def toggle_service(self, row, running):
        svc = next((s for s in self.window.store['services'] if s['id'] == row['id']), None)
        if not svc:
            return
        try:
            self.window.services.stop(svc['id']) if running else self.window.services.start(svc)
        except RuntimeError as e:
            self.toast(str(e))
        GLib.timeout_add(500, lambda: (self.render_services(), self.render_attention(), False)[2])

    def render_projects(self):
        self.proj_list.remove_all()
        rows = DB.recent_projects(self.projects, 6)
        if not rows:
            self.proj_list.append(Adw.ActionRow(title='Chưa thấy project nào', subtitle='Đổi thư mục gốc ở trang Projects.', activatable=False))
        for p in rows:
            ar = Adw.ActionRow(title=esc(p['name']), subtitle=f"{esc(p['branch'] or '—')}  ·  {status_markup(p)}  ·  {esc(P.ago(p['last_ts']))}", activatable=False)
            ar.add_prefix(Adw.Avatar(size=34, text=p['name'], show_initials=True))
            for ic, tip, fn in (('text-editor-symbolic', 'Mở bằng VS Code', P.open_vscode), ('utilities-terminal-symbolic', 'Mở terminal', P.open_terminal)):
                b = icon_btn(ic, tip, lambda f=fn, path=p['path']: self.guard(lambda: f(path)), ['flat', 'circular'])
                b.set_valign(Gtk.Align.CENTER)
                ar.add_suffix(b)
            self.proj_list.append(ar)

    def guard(self, fn):
        try:
            fn()
        except RuntimeError as e:
            self.toast(str(e))

    def render_docker(self):
        d = self.docker
        if not d['available']:
            self.c_dock[2].set_text('—')
            self.c_dock[3].set_text('Docker chưa chạy')
            return
        run = [c for c in d['containers'] if c['state'] == 'running']
        self.c_dock[2].set_text(f"{len(run)} / {len(d['containers'])}")
        self.c_dock[3].set_text(('đang chạy: ' + DB._names([c['name'] for c in run])) if run else 'không có container nào đang chạy')
        self.render_chips()

    def render_ollama(self):
        o = self.ollama
        if not o or not o.get('up'):
            self.c_oll[2].set_text('—')
            self.c_oll[3].set_text('Ollama chưa chạy')
            return
        self.c_oll[2].set_text(f"{len(o['tags'])} model")
        self.c_oll[3].set_text(('đang nạp: ' + ', '.join(f"{m['name']} ({human(m['vram'])} VRAM)" for m in o['ps'])) if o['ps'] else f"v{o['version']} · chưa nạp model nào")
        self.render_chips()

    def render_alerts(self):
        recent = [a for a in self.alerts if time.time() - float(a.get('t', 0)) < 86400]
        self.c_alert[2].set_text(str(len(recent)))
        self.c_alert[3].set_text(recent[0]['title'] if recent else 'không có sự cố')

    def render_attention(self):
        if self.snap is None and self.projects is None and self.docker is None:
            return
        svc_state = [{'name': s['name'], **{k: v for k, v in (self.window.services.status(s['id']) or {}).items() if k in ('state', 'rc')}} for s in self.window.store['services']]
        items = DB.attention(self.snap, self.projects, svc_state, self.docker, self.alerts)
        sig = [(i['level'], i['title'], i['detail']) for i in items]
        if sig == getattr(self, '_attn_sig', None):
            return
        self._attn_sig = sig
        clear(self.attn_box)
        if not items:
            ok = InfoStrip('info')
            ok.show('Mọi thứ đều ổn — không có gì cần xử lý.')
            self.attn_box.append(ok)
            return
        lst = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=['boxed-list'])
        for it in items[:8]:
            ar = Adw.ActionRow(title=esc(it['title']), subtitle=esc(it['detail']), subtitle_lines=1, activatable=bool(it['module']))
            img = Gtk.Image(icon_name=LEVEL_ICON[it['level']], css_classes=[LEVEL_CSS[it['level']]])
            ar.add_prefix(img)
            ar.add_css_class('attn-' + it['level'])
            if it['module']:
                b = button('Mở', css=['flat'], on=lambda m=it['module']: self.window.select_module(m))
                b.set_valign(Gtk.Align.CENTER)
                ar.add_suffix(b)
                ar.connect('activated', lambda _r, m=it['module']: self.window.select_module(m))
            lst.append(ar)
        self.attn_box.append(lst)
        if len(items) > 8:
            self.attn_box.append(label(f'… và {len(items) - 8} mục khác', ['dim-label', 'caption'], xalign=0))
