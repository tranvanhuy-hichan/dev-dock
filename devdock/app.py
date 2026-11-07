"""Cửa sổ chính (libadwaita): thanh bên chọn module + nội dung, palette lệnh Ctrl+K, đổi giao diện sáng/tối."""
import importlib
import os
import sys
import traceback

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, __version__  # noqa: E402
from .store import Store  # noqa: E402
from .ui import CSS, label  # noqa: E402

ICON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'icons')

# (id, tên, icon, lớp trang)
MODULES = [
    # (id, tên, icon, lớp trang, nhóm trong sidebar)
    ('dashboard', 'Tổng quan', 'go-home-symbolic', 'devdock.pages.dashboard:DashboardPage', 'Trang chủ'),
    ('projects', 'Projects', 'folder-symbolic', 'devdock.pages.projects:ProjectsPage', 'Project'),
    ('git', 'Git', 'media-playlist-repeat-symbolic', 'devdock.pages.gitpage:GitPage', 'Project'),
    ('services', 'Dev Services', 'media-playback-start-symbolic', 'devdock.pages.services:ServicesPage', 'Project'),
    ('env', 'Env Manager', 'dialog-password-symbolic', 'devdock.pages.envs:EnvPage', 'Project'),
    ('deps', 'Dependency', 'software-update-available-symbolic', 'devdock.pages.deps:DepsPage', 'Project'),
    ('docker', 'DockerDesk', 'package-x-generic-symbolic', 'devdock.pages.docker:DockerPage', 'Chạy & dữ liệu'),
    ('database', 'Database', 'drive-multidisk-symbolic', 'devdock.pages.database:DatabasePage', 'Chạy & dữ liệu'),
    ('api', 'API Tester', 'send-to-symbolic', 'devdock.pages.apitester:ApiPage', 'Chạy & dữ liệu'),
    ('logs', 'Log Viewer', 'utilities-terminal-symbolic', 'devdock.pages.logs:LogsPage', 'Chạy & dữ liệu'),
    ('sysmon', 'SysMonitor', 'utilities-system-monitor-symbolic', 'devdock.pages.sysmon:SysMonitorPage', 'Hệ thống'),
    ('watch', 'Giám sát', 'dialog-warning-symbolic', 'devdock.pages.watchdog:WatchPage', 'Hệ thống'),
    ('cleaner', 'DiskCleaner', 'edit-clear-all-symbolic', 'devdock.pages.cleaner:CleanerPage', 'Hệ thống'),
    ('ollama', 'Ollama AI', 'chat-bubble-text-symbolic', 'devdock.pages.ollama:OllamaPage', 'AI'),
]


# màu dịu cho từng module (tên lớp CSS c-<màu>)
MODULE_COLOR = {
    'dashboard': 'blue', 'projects': 'amber', 'git': 'orange', 'services': 'green', 'env': 'rose', 'deps': 'teal',
    'docker': 'sky', 'database': 'indigo', 'api': 'purple', 'logs': 'slate', 'sysmon': 'cyan', 'watch': 'red',
    'cleaner': 'lime', 'ollama': 'pink',
}


class Palette(Adw.Dialog):
    """Hộp lệnh nhanh: gõ để lọc, Enter để chạy."""

    def __init__(self, items):
        super().__init__(title='Lệnh nhanh', content_width=480, content_height=420)
        self.items, self.rows = items, []
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.entry = Gtk.SearchEntry(placeholder_text='Gõ lệnh hoặc tên công cụ…', margin_top=8, margin_bottom=8, margin_start=8, margin_end=8)
        self.entry.connect('search-changed', lambda *_: self.list.invalidate_filter())
        self.entry.connect('activate', self._activate_first)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.BROWSE, css_classes=['navigation-sidebar'])
        self.list.set_filter_func(self._filter)
        self.list.connect('row-activated', lambda _l, r: self._run(r.item))
        for it in items:
            row = Gtk.ListBoxRow()
            row.item = it
            line = Gtk.Box(spacing=8, margin_top=6, margin_bottom=6, margin_start=8, margin_end=8)
            line.append(label(it['label'], hexpand=True))
            if it.get('hint'):
                line.append(label(it['hint'], ['dim-label', 'caption']))
            row.set_child(line)
            self.list.append(row)
        sw = Gtk.ScrolledWindow(child=self.list, vexpand=True)
        box.append(self.entry)
        box.append(sw)
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        tv.set_content(box)
        self.set_child(tv)
        self.set_focus(self.entry)

    def _filter(self, row):
        q = self.entry.get_text().strip().lower()
        return not q or q in row.item['label'].lower()

    def _activate_first(self, *_):
        for it in self.list_rows():
            if self._filter(it):
                self._run(it.item)
                return

    def list_rows(self):
        r = self.list.get_first_child()
        while r:
            yield r
            r = r.get_next_sibling()

    def _run(self, item):
        self.close()
        GLib.idle_add(lambda: item['run']() and False)


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app, store):
        super().__init__(application=app, title='DevDock', default_width=1280, default_height=820)
        self.set_size_request(760, 520)
        self.store = store
        self.pages = {}
        from .lib.services import ServiceManager
        self.services = ServiceManager()  # dùng chung cho Dev Services và Log Viewer
        self.current = None
        self.toasts = Adw.ToastOverlay()

        split = Adw.NavigationSplitView(min_sidebar_width=200, max_sidebar_width=240, sidebar_width_fraction=0.2)
        # --- sidebar
        self.nav = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, css_classes=['navigation-sidebar'])
        self.nav.set_header_func(lambda row, before: row.set_header(label(row.group, ['dim-label', 'caption'], margin_top=10, margin_bottom=2, margin_start=12)
                                                                      if before is None or before.group != row.group else None))
        for idx, (mid, name, icon, _cls, group) in enumerate(MODULES, 1):
            row = Gtk.ListBoxRow()
            row.mid, row.group = mid, group
            line = Gtk.Box(spacing=12, margin_top=7, margin_bottom=7, margin_start=6, margin_end=6)
            chip = Gtk.Box(css_classes=['nav-ico', 'c-' + MODULE_COLOR.get(mid, 'blue')], valign=Gtk.Align.CENTER)
            chip.append(Gtk.Image(icon_name=icon))
            line.append(chip)
            line.append(label(name, hexpand=True))
            if idx <= 9:
                line.append(label(f'Ctrl+{idx}', ['dim-label', 'caption']))
            row.set_child(line)
            self.nav.append(row)
        self.nav.connect('row-selected', lambda _l, r: r and self.show_module(r.mid))
        side_tv = Adw.ToolbarView()
        side_head = Adw.HeaderBar(show_end_title_buttons=False)
        side_head.set_title_widget(Adw.WindowTitle(title='DevDock', subtitle='Bộ công cụ cho developer'))
        side_tv.add_top_bar(side_head)
        side_tv.set_content(Gtk.ScrolledWindow(child=self.nav, vexpand=True))
        pal = Gtk.Button(label='Lệnh nhanh  (Ctrl+K)', css_classes=['flat'], margin_start=8, margin_end=8, margin_bottom=8, margin_top=4)
        pal.connect('clicked', lambda *_: self.open_palette())
        side_tv.add_bottom_bar(pal)
        split.set_sidebar(Adw.NavigationPage(title='DevDock', child=side_tv))
        # --- content
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=120)
        self.content_title = Adw.WindowTitle(title='')
        head = Adw.HeaderBar()
        self.head, self._hdr = head, []
        head.set_title_widget(self.content_title)
        menu = Gio.Menu()
        sec = Gio.Menu()
        sec.append('Giao diện: theo hệ thống', 'win.theme::system')
        sec.append('Giao diện: sáng', 'win.theme::light')
        sec.append('Giao diện: tối', 'win.theme::dark')
        menu.append_section(None, sec)
        menu.append('Lệnh nhanh', 'win.palette')
        menu.append('Giới thiệu DevDock', 'win.about')
        menu.append('Thoát hẳn DevDock (Ctrl+Q)', 'app.quit')
        head.pack_end(Gtk.MenuButton(icon_name='open-menu-symbolic', menu_model=menu, primary=True, tooltip_text='Menu'))
        tv = Adw.ToolbarView()
        tv.add_top_bar(head)
        tv.set_content(self.stack)
        split.set_content(Adw.NavigationPage(title='Nội dung', child=tv))
        self.toasts.set_child(split)
        self.set_content(self.toasts)

        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse('max-width: 900sp'))
        bp.add_setter(split, 'collapsed', False)
        for name, cb, accel in (('palette', lambda *_: self.open_palette(), '<primary>k'), ('about', lambda *_: self.about(), None)):
            a = Gio.SimpleAction.new(name, None)
            a.connect('activate', cb)
            self.add_action(a)
            if accel:
                app.set_accels_for_action(f'win.{name}', [accel])
        theme = Gio.SimpleAction.new_stateful('theme', GLib.VariantType.new('s'), GLib.Variant('s', store['theme']))
        theme.connect('activate', lambda act, v: (act.set_state(v), self.set_theme(v.get_string(), save=True)))
        self.add_action(theme)
        for i, (mid, *_r) in enumerate(MODULES, 1):
            a = Gio.SimpleAction.new(f'go-{mid}', None)
            a.connect('activate', lambda *_a, m=mid: self.select_module(m))
            self.add_action(a)
            if i <= 9:
                app.set_accels_for_action(f'win.go-{mid}', [f'<primary>{i}'])
        self.set_theme(store['theme'])
        self.connect('close-request', self._on_close)
        # Giám sát tài nguyên chạy ở cấp ứng dụng (không cần mở trang Giám sát). Phải có trước khi dựng trang đầu tiên.
        from .lib.watchdog import WatchService
        self.watch = WatchService(self)
        self.select_module('dashboard')  # luôn mở bằng trang tổng quan
        self.watch.start()
        if self.watch.config['background'] and hasattr(app, 'set_hold'):
            app.set_hold(True)  # chế độ chạy nền: giữ ứng dụng sống khi cửa sổ bị ẩn

    def close_decision(self):
        """'hide' nếu bật "tiếp tục giám sát khi đóng cửa sổ" (ẩn cửa sổ, giữ ứng dụng sống), ngược lại 'quit'."""
        return 'hide' if self.watch.config.get('background') else 'quit'

    def shutdown_all(self):
        """Dọn dẹp trước khi thoát hẳn: dừng luồng của các trang, giám sát và các dev service (tránh tiến trình mồ côi)."""
        self.watch.stop()
        for p in self.pages.values():
            if hasattr(p, 'shutdown'):
                p.shutdown()
        self.services.shutdown()

    def _on_close(self, *_):
        if self.close_decision() == 'hide':
            self.set_visible(False)  # dev service và giám sát vẫn chạy; mở lại bằng `devdock` hoặc bấm thông báo
            return True
        self.shutdown_all()
        return False

    def nav_rows(self):
        out, r = [], self.nav.get_first_child()
        while r:
            if isinstance(r, Gtk.ListBoxRow):  # header của ListBox cũng là con trực tiếp
                out.append(r)
            r = r.get_next_sibling()
        return out

    # ---- điều hướng
    def select_module(self, mid):
        for r in self.nav_rows():
            if r.mid == mid:
                self.nav.select_row(r)
                return

    def show_module(self, mid):
        if mid not in self.pages:
            spec = next(m for m in MODULES if m[0] == mid)
            mod, cls = spec[3].split(':')
            try:
                page = getattr(importlib.import_module(mod), cls)(self)
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                page = Adw.StatusPage(icon_name='dialog-error-symbolic', title=f'Không mở được {spec[1]}', description=str(e))
            self.pages[mid] = page
            self.stack.add_named(page, mid)
        self.stack.set_visible_child_name(mid)
        self.current = mid
        self.content_title.set_title(next(m[1] for m in MODULES if m[0] == mid))
        self.apply_header(self.pages[mid])
        if self.store['module'] != mid:
            self.store['module'] = mid
            self.store.save()

    def apply_header(self, page):
        """Đặt tiêu đề/nút của trang lên thanh tiêu đề cửa sổ (chuẩn libadwaita: điều khiển theo ngữ cảnh nằm ở header)."""
        for w in self._hdr:
            self.head.remove(w)
        self._hdr = []
        self.content_title.set_subtitle(getattr(page, 'hdr_subtitle', '') or '')
        self.head.set_title_widget(getattr(page, 'hdr_title', None) or self.content_title)
        for w in getattr(page, 'hdr_start', ()):
            self.head.pack_start(w)
            self._hdr.append(w)
        for w in reversed(list(getattr(page, 'hdr_end', ()))):  # pack_end: widget đầu tiên nằm ngoài cùng bên phải
            self.head.pack_end(w)
            self._hdr.append(w)

    def open_logs(self, source):
        """Mở Log Viewer với một nguồn log, vd. ('docker', id, name)."""
        self.select_module('logs')
        self.pages['logs'].open_source(source)

    def notify(self, title, body='', nid=None, urgent=False):
        """Thông báo desktop (Gio.Notification). Bấm vào thông báo sẽ mở lại cửa sổ DevDock."""
        n = Gio.Notification.new(title)
        n.set_body(body)
        n.set_priority(Gio.NotificationPriority.URGENT if urgent else Gio.NotificationPriority.NORMAL)
        n.set_default_action('app.show')
        self.get_application().send_notification(nid, n)

    def toast(self, msg):
        self.toasts.add_toast(Adw.Toast(title=msg, timeout=4))

    def set_theme(self, name, save=False):
        Adw.StyleManager.get_default().set_color_scheme({'light': Adw.ColorScheme.FORCE_LIGHT, 'dark': Adw.ColorScheme.FORCE_DARK}.get(name, Adw.ColorScheme.DEFAULT))
        if save:
            self.store['theme'] = name
            self.store.save()

    def open_palette(self):
        items = [{'label': f'Mở {m[1]}', 'hint': f'Ctrl+{i}' if i <= 9 else '', 'run': lambda mid=m[0]: self.select_module(mid)} for i, m in enumerate(MODULES, 1)]
        items += [{'label': 'Giao diện sáng', 'run': lambda: self.set_theme('light', True)}, {'label': 'Giao diện tối', 'run': lambda: self.set_theme('dark', True)},
                  {'label': 'Giao diện theo hệ thống', 'run': lambda: self.set_theme('system', True)}]
        Palette(items).present(self)

    def about(self):
        Adw.AboutDialog(application_name='DevDock', application_icon=APP_ID, version=__version__, developer_name='DevDock',
                        comments='Trung tâm cho developer: quản lý project, dev services, file .env, log, giám sát hệ thống, Docker và dọn đĩa. GTK4/libadwaita.',
                        license_type=Gtk.License.MIT_X11, website='https://github.com/tranvanhuy-hichan/dev-dock').present(self)


class App(Adw.Application):
    def __init__(self):
        # Chế độ kiểm thử (chụp ảnh/test giao diện) chạy tách biệt, không đánh thức cửa sổ DevDock đang mở của người dùng
        isolated = bool(os.environ.get('DEVDOCK_SHOTS') or os.environ.get('DEVDOCK_TEST'))
        flags = Gio.ApplicationFlags.HANDLES_COMMAND_LINE | (Gio.ApplicationFlags.NON_UNIQUE if isolated else Gio.ApplicationFlags.DEFAULT_FLAGS)
        super().__init__(application_id=APP_ID, flags=flags)
        self.win = None
        self._held = False

    def set_hold(self, on):
        """Giữ ứng dụng sống kể cả khi không còn cửa sổ nào hiện (chế độ giám sát nền)."""
        if on and not self._held:
            self.hold()
            self._held = True
        elif not on and self._held:
            self.release()
            self._held = False

    def do_command_line(self, cl):
        """`devdock --hidden` (autostart): khởi động không hiện cửa sổ, nhưng CHỈ khi đã bật chạy nền — nếu không thì hiện cửa sổ như thường."""
        from .lib.watchdog import load_config
        hidden = '--hidden' in (cl.get_arguments() or [])
        if hidden and load_config()['background'] and self.win is None:
            self.win = MainWindow(self, self.store)  # tạo nhưng không present
        else:
            self.activate()
        return 0

    def do_startup(self):
        Adw.Application.do_startup(self)
        self.store = Store()
        prov = Gtk.CssProvider()
        prov.load_from_string(CSS)
        display = Gdk.Display.get_default()
        Gtk.StyleContext.add_provider_for_display(display, prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        Gtk.IconTheme.get_for_display(display).add_search_path(ICON_DIR)
        Gtk.Window.set_default_icon_name(APP_ID)
        self.set_accels_for_action('app.quit', ['<primary>q'])
        sh = Gio.SimpleAction.new('show', None)
        sh.connect('activate', lambda *_: self.do_activate())
        self.add_action(sh)
        q = Gio.SimpleAction.new('quit', None)
        q.connect('activate', lambda *_: ((self.win.shutdown_all() if self.win else None), self.quit()))
        self.add_action(q)

    def do_activate(self):
        if not self.win:
            self.win = MainWindow(self, self.store)
        self.win.present()
        shots = os.environ.get('DEVDOCK_SHOTS')  # chế độ kiểm thử giao diện: chụp từng trang rồi thoát
        if shots:
            from .shots import run
            run(self, self.win, shots)


def main():
    return App().run(sys.argv)
